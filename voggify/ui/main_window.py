"""Voggify のメインウィンドウ。

ファイルの収集・解析（ProbeService）と変換の実行（ConversionService）を束ね、
結果を FileListModel に流し込む。重い処理はいずれも別スレッドで動く。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import __app_name__, __version__
from ..config import AppConfig, WindowGeometry, save_config
from ..converter import ConversionOptions
from ..ffmpeg_locator import FFmpegTools, find_ffmpeg_tools, missing_ffmpeg_message
from ..formats import SUPPORTED_EXTENSIONS, format_bytes
from ..models import FileStatus
from ..resources import icon_path
from .conversion_service import (
    OUTCOME_CANCELLED,
    OUTCOME_DONE,
    ConversionJob,
    ConversionService,
)
from .edit_panel import EditPanel
from .file_list_model import FileListModel, collect_audio_files
from .file_list_view import FileListView
from .log_panel import LogPanel
from .probe_service import ProbeService
from .settings_panel import SettingsPanel
from .waveform_service import WaveformService


def _file_dialog_filter() -> str:
    patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
    return f"音楽ファイル ({patterns});;すべてのファイル (*)"


def _install_hint() -> str:
    """警告バーに出す 1 行のインストール案内。"""
    if sys.platform == "win32":
        return "コマンドプロンプトで  winget install Gyan.FFmpeg  を実行するとインストールできます。"
    if sys.platform == "darwin":
        return "ターミナルで  brew install ffmpeg  を実行するとインストールできます。"
    return "端末で  sudo apt install ffmpeg  などを実行するとインストールできます。"


INSTALL_HINT = _install_hint()


class FFmpegBanner(QFrame):
    """ffmpeg が見つからないときに出す警告バー。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #5a3a10; border: 1px solid #8a5a20; border-radius: 4px; }"
            "QLabel { color: #ffd9a0; border: none; background: transparent; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)

        self.label = QLabel("ffmpeg が見つかりません。変換を実行できません。")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        self.details_button = QPushButton("インストール方法")
        layout.addWidget(self.details_button)
        self.recheck_button = QPushButton("再確認")
        layout.addWidget(self.recheck_button)


class MainWindow(QMainWindow):
    """ファイルリストを中心に据えたメインウィンドウ。"""

    def __init__(
        self,
        tools: FFmpegTools | None = None,
        config: AppConfig | None = None,
        config_warnings: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._tools = tools
        self._last_dir = str(Path.home())
        self._config = config or AppConfig()
        self._config_warnings = list(config_warnings or [])

        self.setWindowTitle(f"{__app_name__} {__version__}")
        icon = icon_path()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(940, 640)
        self.setMinimumSize(QSize(720, 480))

        self.model = FileListModel(self)
        self.probe_service = ProbeService(tools, self)
        self.waveform_service = WaveformService(tools, self)
        self.conversion = ConversionService(self)

        #: 変換中は UI をロックする
        self._locked = False
        #: 閉じたログにエラーが溜まっているか
        self._log_attention = False
        #: 全体進捗の分母・分子
        self._total_jobs = 0
        self._completed_jobs = 0
        self._current_progress = 0.0

        self._build_ui()
        self._connect_signals()
        self._apply_config()
        self._update_ffmpeg_banner()
        self._update_status()

    # ------------------------------------------------------------------
    # 組み立て
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 6)
        layout.setSpacing(8)

        self.banner = FFmpegBanner(central)
        self.banner.hide()
        layout.addWidget(self.banner)

        # --- 操作ボタン列 ---
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.add_button = QPushButton("ファイルを選択…")
        self.add_button.setToolTip("変換したい音楽ファイルを選びます（複数選択可）")
        buttons.addWidget(self.add_button)

        self.remove_button = QPushButton("選択項目を削除")
        self.remove_button.setToolTip("選択した項目をリストから外します（Delete キー）")
        buttons.addWidget(self.remove_button)

        self.clear_button = QPushButton("全てクリア")
        buttons.addWidget(self.clear_button)

        self.edit_button = QPushButton("編集")
        self.edit_button.setCheckable(True)
        self.edit_button.setToolTip("トリミングと音量の編集パネルを開閉します (Ctrl+E)")
        buttons.addWidget(self.edit_button)

        self.log_button = QPushButton("ログ")
        self.log_button.setCheckable(True)
        self.log_button.setToolTip("変換ログの表示を切り替えます (Ctrl+L)")
        buttons.addWidget(self.log_button)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        buttons.addWidget(spacer)

        self.summary_label = QLabel("")
        self.summary_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        buttons.addWidget(self.summary_label)
        layout.addLayout(buttons)

        # --- 変換設定（品質・出力先）---
        self.settings = SettingsPanel(central)
        layout.addWidget(self.settings)

        # --- 編集（トリミング・音量）。既定は畳んでおく ---
        self.edit_panel = EditPanel(central)
        self.edit_panel.hide()
        layout.addWidget(self.edit_panel)

        # --- ファイル一覧 + ログパネル（縦分割・ログは既定で非表示）---
        self.splitter = QSplitter(Qt.Orientation.Vertical, central)
        self.splitter.setChildrenCollapsible(False)

        self.view = FileListView(self.splitter)
        self.view.setModel(self.model)
        self.splitter.addWidget(self.view)

        self.log_panel = LogPanel(self.splitter)
        self.log_panel.hide()
        self.splitter.addWidget(self.log_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)

        # --- 変換の実行と全体進捗 ---
        run_row = QHBoxLayout()
        run_row.setSpacing(8)

        self.run_button = QPushButton("変換開始")
        self.run_button.setFixedWidth(140)
        self.run_button.setDefault(True)
        run_row.addWidget(self.run_button)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(False)
        self.overall_progress.setFixedHeight(16)
        run_row.addWidget(self.overall_progress, 1)

        self.overall_label = QLabel("")
        self.overall_label.setMinimumWidth(120)
        self.overall_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        run_row.addWidget(self.overall_label)
        layout.addLayout(run_row)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))

        # --- ショートカット ---
        add_action = QAction("ファイルを追加", self)
        add_action.setShortcut(QKeySequence.StandardKey.Open)
        add_action.triggered.connect(self.open_file_dialog)
        self.addAction(add_action)

        edit_action = QAction("編集パネルの表示を切り替え", self)
        edit_action.setShortcut(QKeySequence("Ctrl+E"))
        edit_action.triggered.connect(
            lambda: self.set_edit_panel_visible(not self.edit_panel.isVisible())
        )
        self.addAction(edit_action)

        log_action = QAction("ログの表示を切り替え", self)
        log_action.setShortcut(QKeySequence("Ctrl+L"))
        log_action.triggered.connect(lambda: self.set_log_visible(not self.log_panel.isVisible()))
        self.addAction(log_action)

    def _connect_signals(self) -> None:
        self.add_button.clicked.connect(self.open_file_dialog)
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button.clicked.connect(self.clear_list)

        self.view.files_dropped.connect(self._on_files_dropped)
        self.view.remove_requested.connect(self._remove_rows)
        self.view.reanalyze_requested.connect(self._reanalyze_rows)
        self.view.selectionModel().selectionChanged.connect(
            lambda *_: self._update_buttons()
        )

        self.model.contents_changed.connect(self._update_status)
        self.probe_service.probed.connect(self._on_probed)

        self.banner.recheck_button.clicked.connect(self._recheck_ffmpeg)
        self.banner.details_button.clicked.connect(self._show_ffmpeg_help)

        self.edit_button.toggled.connect(self.set_edit_panel_visible)
        self.edit_panel.edit_changed.connect(self._on_edit_changed)
        self.waveform_service.ready.connect(self._on_waveform_ready)
        self.view.selectionModel().selectionChanged.connect(
            lambda *_: self._sync_edit_panel()
        )

        self.log_button.toggled.connect(self.set_log_visible)
        self.log_panel.close_requested.connect(lambda: self.set_log_visible(False))

        self.settings.output_format_changed.connect(self._on_output_format_changed)
        self.settings.quality_changed.connect(self._on_quality_changed)
        self.settings.output_dir_changed.connect(self._on_output_dir_changed)
        self.settings.validity_changed.connect(lambda *_: self._update_buttons())

        self.run_button.clicked.connect(self._on_run_clicked)
        self.conversion.file_started.connect(self._on_file_started)
        self.conversion.file_progress.connect(self._on_file_progress)
        self.conversion.file_log.connect(self._on_file_log)
        self.conversion.file_finished.connect(self._on_file_finished)
        self.conversion.finished.connect(self._on_conversion_finished)

    # ------------------------------------------------------------------
    # ファイル追加
    # ------------------------------------------------------------------
    def open_file_dialog(self) -> None:
        """「ファイルを選択」ボタン。複数選択可。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "変換するファイルを選択",
            self._last_dir,
            _file_dialog_filter(),
        )
        if not paths:
            return
        self._last_dir = str(Path(paths[0]).parent)
        self.add_paths([Path(p) for p in paths])

    def _on_files_dropped(self, paths: list[Path]) -> None:
        """ドロップされたパス（フォルダを含みうる）を展開して追加する。"""
        collected = collect_audio_files(paths)
        if not collected:
            self.statusBar().showMessage(
                "追加できるファイルがありませんでした。", 5000
            )
            return
        self.add_paths(collected)

    def add_paths(self, paths: list[Path]) -> None:
        """リストに追加し、バックグラウンド解析を投入する。"""
        if self._locked:
            self.statusBar().showMessage(
                "変換中はファイルを追加できません。", 5000
            )
            return
        added, duplicates = self.model.add_paths(paths)
        if added:
            self.probe_service.submit(added)

        messages = []
        if added:
            messages.append(f"{len(added)} 件を追加しました。")
        if duplicates:
            messages.append(f"{duplicates} 件は追加済みのためスキップしました。")
        if not added and not duplicates:
            messages.append("追加できるファイルがありませんでした。")
        self.statusBar().showMessage(" ".join(messages), 6000)
        if added:
            self.log(f"{len(added)} 件をリストに追加しました。")
        self._update_status()

    # ------------------------------------------------------------------
    # 解析結果
    # ------------------------------------------------------------------
    def _on_probed(self, path: str, info: object, message: str) -> None:
        item = self.model.apply_probe_result(path, info, message)  # type: ignore[arg-type]
        if item is None:
            return
        if item.status is FileStatus.ERROR:
            self.log(item.message or "解析に失敗しました。", "error", file_name=item.name)
            return
        if item.note:
            # 拡張子と実体が食い違う場合はその場で知らせる（詳細は行のツールチップ）
            self.statusBar().showMessage(f"{item.name}: {item.note}", 8000)
            self.log(f"⚠ {item.note}", "warn", file_name=item.name)

    def _reanalyze_rows(self, rows: list[int]) -> None:
        paths = self.model.reset_to_analyzing(rows)
        if paths:
            self.probe_service.submit(paths)
            self.statusBar().showMessage(f"{len(paths)} 件を再解析します…", 4000)

    # ------------------------------------------------------------------
    # 削除
    # ------------------------------------------------------------------
    def _remove_selected(self) -> None:
        self._remove_rows(self.view.selected_rows())

    def _remove_rows(self, rows: list[int]) -> None:
        if not rows:
            return
        if self._locked:
            self.statusBar().showMessage("変換中は削除できません。", 5000)
            return
        # 消す行の波形キャッシュも捨てる
        for row in rows:
            item = self.model.item_at(row)
            if item is not None and not item.status.is_busy:
                self.waveform_service.discard(item.path)
        removed = self.model.remove_rows(rows)
        skipped = len(rows) - removed
        message = f"{removed} 件を削除しました。"
        if skipped:
            message += f" {skipped} 件は処理中のため削除できません。"
        self.statusBar().showMessage(message, 5000)

    def clear_list(self) -> None:
        if not self.model.rowCount():
            return
        if self._locked:
            self.statusBar().showMessage("変換中はクリアできません。", 5000)
            return
        self.probe_service.discard_pending()
        self.waveform_service.clear()
        self.model.clear()
        self.statusBar().showMessage("リストをクリアしました。", 4000)

    # ------------------------------------------------------------------
    # 設定の復元と保存
    # ------------------------------------------------------------------
    def _apply_config(self) -> None:
        """読み込んだ設定を UI に反映する。

        壊れていた項目や使えなくなった出力先は既定値に落として、
        理由をログとステータスバーに出す（起動は止めない）。
        """
        config = self._config
        warnings = list(self._config_warnings)

        restore_warning = self.settings.restore(
            config.quality,
            config.use_custom_output_dir,
            config.output_dir,
            config.output_format,
        )
        if restore_warning:
            warnings.append(restore_warning)

        self.model.set_output_format(self.settings.output_format())
        self.probe_service.set_output_format(self.settings.output_format())
        self.model.set_quality(self.settings.quality())
        self.set_edit_panel_visible(config.edit_panel_visible)
        self.set_log_visible(config.log_visible)
        self._apply_window_geometry(config.window)

        for message in warnings:
            for line in message.splitlines():
                self.log(line, "warn")
        if warnings:
            self.statusBar().showMessage(
                warnings[0].splitlines()[0] + "（詳細はログ / Ctrl+L）", 10000
            )

    def _apply_window_geometry(self, geometry: WindowGeometry | None) -> None:
        """前回のウィンドウ位置を復元する。

        画面構成が変わっていると保存位置が画面外になりうるので、
        どこかの画面に載っているときだけ位置を復元する。
        """
        if geometry is None:
            return
        self.resize(max(geometry.width, self.minimumWidth()),
                    max(geometry.height, self.minimumHeight()))

        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication

        saved = QRect(geometry.x, geometry.y, geometry.width, geometry.height)
        on_screen = any(
            screen.availableGeometry().intersects(saved)
            for screen in QGuiApplication.screens()
        )
        if on_screen:
            self.move(geometry.x, geometry.y)
        if geometry.maximized:
            self.showMaximized()

    def current_config(self) -> AppConfig:
        """今の UI の状態から保存用の設定を組み立てる。"""
        remembered = self.settings.remembered_output_dir()
        geometry = self.geometry()
        return AppConfig(
            quality=self.settings.quality(),
            output_format=self.settings.output_format().key,
            use_custom_output_dir=self.settings.uses_custom_output_dir(),
            output_dir=str(remembered) if remembered else None,
            log_visible=self.log_panel.isVisible(),
            edit_panel_visible=self.edit_panel.isVisible(),
            window=WindowGeometry(
                x=geometry.x(),
                y=geometry.y(),
                width=geometry.width(),
                height=geometry.height(),
                maximized=self.isMaximized(),
            ),
        )

    def save_config(self) -> bool:
        """設定をディスクへ書き出す。終了時にまとめて呼ぶ。"""
        saved, reason = save_config(self.current_config())
        if not saved:
            # 保存できなくても終了は妨げない
            self.log(reason, "error")
            self.statusBar().showMessage(reason, 8000)
        return saved

    # ------------------------------------------------------------------
    # 編集パネル
    # ------------------------------------------------------------------
    def set_edit_panel_visible(self, visible: bool) -> None:
        """編集パネルの開閉。"""
        self.edit_panel.setVisible(visible)
        if self.edit_button.isChecked() != visible:
            self.edit_button.blockSignals(True)
            self.edit_button.setChecked(visible)
            self.edit_button.blockSignals(False)
        if visible:
            self._sync_edit_panel()

    def _sync_edit_panel(self) -> None:
        """選択に合わせて編集パネルの対象を切り替える。

        1 行だけ選ばれているときに有効。未選択・複数選択では無効化する。
        """
        self._update_buttons()
        if not self.edit_panel.isVisible():
            return

        rows = self.view.selected_rows()
        if len(rows) != 1:
            self.edit_panel.set_target(None, None, None)  # 再生も止まる
            if len(rows) > 1:
                self.edit_panel.target_label.setText(
                    f"{len(rows)} 件選択中です。編集するファイルを 1 つだけ選んでください。"
                )
            return

        item = self.model.item_at(rows[0])
        if item is None or item.status.is_error:
            self.edit_panel.set_target(None, None, None)
            if item is not None:
                self.edit_panel.target_label.setText(
                    f"{item.name} は変換できないため編集できません。"
                )
            return

        self.edit_panel.set_target(item.name, item.edit, item.source_duration)
        self.edit_panel.set_source_path(item.path)
        self._request_waveform(item)

    def _request_waveform(self, item) -> None:  # noqa: ANN001
        """選択中のファイルの波形を用意する。キャッシュがあれば即座に出る。"""
        if item.info is None or not item.source_duration:
            self.edit_panel.set_waveform(None, "長さが不明なため波形を表示できません。")
            return
        cached = self.waveform_service.request(
            item.path, item.source_duration, item.info.sample_rate
        )
        if cached is not None:
            self.edit_panel.set_waveform(cached)
        else:
            self.edit_panel.set_waveform(None, "波形を読み込み中…")

    def _on_waveform_ready(self, path: str, data: object, message: str) -> None:
        """生成が終わった。まだ同じ行が選ばれていれば反映する。"""
        rows = self.view.selected_rows()
        if len(rows) != 1:
            return
        item = self.model.item_at(rows[0])
        if item is None or str(item.path) != path:
            return  # 選択が変わっていたので捨てる
        if data is None:
            self.edit_panel.set_waveform(None, message or "波形を生成できませんでした。")
            if message:
                self.log(f"{item.name}: {message}", "warn")
        else:
            self.edit_panel.set_waveform(data)  # type: ignore[arg-type]

    def _on_edit_changed(self, edit: object) -> None:
        """パネルの変更を選択中の行に書き戻す。"""
        rows = self.view.selected_rows()
        if len(rows) != 1:
            return
        item = self.model.set_edit(rows[0], edit)
        if item is None:
            return
        description = item.edit.describe(item.source_duration)
        if description:
            self.statusBar().showMessage(
                f"{item.name}: " + description.replace("\n", " / "), 6000
            )
        else:
            self.statusBar().showMessage(f"{item.name}: 編集を解除しました。", 4000)

    # ------------------------------------------------------------------
    # ログパネル
    # ------------------------------------------------------------------
    def set_log_visible(self, visible: bool) -> None:
        """ログパネルの開閉。閉じているときは一覧が広く使える。"""
        self.log_panel.setVisible(visible)
        if self.log_button.isChecked() != visible:
            self.log_button.blockSignals(True)
            self.log_button.setChecked(visible)
            self.log_button.blockSignals(False)
        if visible:
            self._log_attention = False
            self.log_button.setText("ログ")
            sizes = self.splitter.sizes()
            if len(sizes) == 2 and sizes[1] <= 0:
                total = self.splitter.height() or sum(sizes) or 400
                self.splitter.setSizes([int(total * 0.7), int(total * 0.3)])

    def log(self, text: str, level: str = "info", *, file_name: str | None = None) -> None:
        """ログパネルへ 1 行流す。file_name を渡すと見出しを差し込む。"""
        if file_name:
            self.log_panel.append_header(file_name)
        self.log_panel.append(text, level)
        if level == "error":
            self._flag_log_attention()

    def _flag_log_attention(self) -> None:
        """閉じているログにエラーが溜まったことを示す。"""
        if self.log_panel.isVisible() or self._log_attention:
            return
        self._log_attention = True
        self.log_button.setText("ログ ●")
        self.log_button.setToolTip(
            "エラーが記録されています。クリックまたは Ctrl+L で表示します。"
        )

    def report_unexpected_error(self, text: str) -> None:
        """想定外の例外を握りつぶさずに残す（app.py の excepthook から呼ぶ）。"""
        first_line = text.strip().splitlines()[-1] if text.strip() else "不明なエラー"
        self.log_panel.append_header("予期しないエラー")
        for line in text.rstrip().splitlines():
            self.log_panel.append(line, "error", timestamp=False)
        self._flag_log_attention()
        self.statusBar().showMessage(
            f"予期しないエラーが発生しました（詳細はログ / Ctrl+L）: {first_line}", 0
        )

    # ------------------------------------------------------------------
    # 変換設定
    # ------------------------------------------------------------------
    def _on_output_format_changed(self, output_format: object) -> None:
        """出力形式が変わったら、予測サイズと対応判定をやり直す。

        「既に MP3 です」のような判定は変換先によって変わるため、
        解析済みの項目も含めて再評価する必要がある。
        """
        self.model.set_output_format(output_format)  # type: ignore[arg-type]
        self.probe_service.set_output_format(output_format)  # type: ignore[arg-type]
        self.statusBar().showMessage(
            f"出力形式: {output_format.label}（{output_format.extension}）", 5000  # type: ignore[attr-defined]
        )
        self.log(f"出力形式を {output_format.label} に変更しました。")  # type: ignore[attr-defined]
        self._update_ffmpeg_banner()
        self._revalidate_items()

    def _revalidate_items(self) -> None:
        """出力形式が変わったときに、解析済みの項目を判定し直す。"""
        rows = self.model.rows_of_status(FileStatus.READY, FileStatus.ERROR)
        paths = self.model.reset_to_analyzing(rows)
        if paths:
            self.probe_service.submit(paths)

    def _on_quality_changed(self, quality: int) -> None:
        """スライダーの値をリストの予測サイズに即座に反映する。"""
        self.model.set_quality(quality)

    def _on_output_dir_changed(self, output_dir: object) -> None:
        if not self.settings.is_valid():
            self.statusBar().showMessage(self.settings.error_message(), 0)
            self.log(self.settings.error_message(), "error")
        elif output_dir is None:
            self.statusBar().showMessage(
                "出力先: 入力ファイルと同じフォルダ", 5000
            )
        else:
            self.statusBar().showMessage(f"出力先: {output_dir}", 5000)
        self._update_buttons()

    # ------------------------------------------------------------------
    # 変換
    # ------------------------------------------------------------------
    def _on_run_clicked(self) -> None:
        """1 つのボタンで「変換開始」と「キャンセル」を切り替える。"""
        if self.conversion.running:
            self.cancel_conversion()
        else:
            self.start_conversion()

    def current_options(self) -> ConversionOptions:
        """設定パネルの現在値から変換オプションを組み立てる。"""
        return ConversionOptions(
            quality=self.settings.quality(),
            output_dir=self.settings.output_dir(),
            overwrite=False,
            output_format=self.settings.output_format(),
        )

    def start_conversion(self) -> bool:
        """待機中の項目をキューに積んで変換を開始する。"""
        if self.conversion.running:
            return False
        target = self.settings.output_format()
        if self._tools is None or not self._tools.supports(target):
            self.statusBar().showMessage(
                f"ffmpeg が {target.label} に対応していないため変換を開始できません。", 8000
            )
            self._update_ffmpeg_banner()
            return False

        # 選択時に確認済みでも、その後に権限が変わっている場合がある
        self.settings.revalidate()
        if not self.settings.is_valid():
            self.statusBar().showMessage(
                f"出力先を確認してください: {self.settings.error_message()}", 8000
            )
            return False

        targets = self.model.convertible_items()
        if not targets:
            counts = self.model.counts()
            if counts["analyzing"]:
                message = "解析が終わるまでお待ちください。"
            elif counts["done"] and counts["total"] == counts["done"]:
                message = "すべて変換済みです。"
            else:
                message = "変換できるファイルがありません。"
            self.statusBar().showMessage(message, 6000)
            return False

        queued = self.model.mark_queued(targets)
        jobs = [
            ConversionJob(path=item.path, info=item.info, edit=item.edit)
            for item in queued
        ]

        self._total_jobs = len(jobs)
        self._completed_jobs = 0
        self._current_progress = 0.0

        if not self.conversion.start(self._tools, jobs, self.current_options()):
            self.model.cancel_remaining()
            return False

        self._set_ui_locked(True)
        self._update_overall_progress()

        skipped = self.model.counts()["analyzing"]
        message = f"{len(jobs)} 件の変換を開始しました。"
        if skipped:
            message += f" 解析中の {skipped} 件は今回の対象外です。"
        self.statusBar().showMessage(message, 8000)

        options = self.current_options()
        destination = options.output_dir or "入力ファイルと同じフォルダ"
        edited = sum(1 for job in jobs if not job.edit.is_default)
        if edited:
            self.log(f"うち {edited} 件は編集（切り出し・音量）が入っています。")
        self.log_panel.reset_header()
        self.log(
            f"{message} ({options.output_format.label} / 品質 {options.quality}"
            f" -> {options.output_format.encoder} -q:a "
            f"{options.output_format.encoder_quality(options.quality)}"
            f" / 出力先: {destination})",
            "info",
        )
        return True

    def cancel_conversion(self) -> None:
        """実行中のファイルを中断し、以降のキューを処理しない。"""
        if not self.conversion.running:
            return
        self.run_button.setEnabled(False)
        self.run_button.setText("中断中…")
        self.statusBar().showMessage("変換を中断しています…", 0)
        self.conversion.cancel()

    def _on_file_started(self, path: str) -> None:
        item = self.model.set_converting(path)
        if item is not None:
            self.log_panel.append_header(
                f"{item.name}  ({self._completed_jobs + 1}/{self._total_jobs})"
            )
        self._current_progress = 0.0
        self._update_overall_progress()
        if item is not None:
            self.statusBar().showMessage(
                f"変換中 ({self._completed_jobs + 1}/{self._total_jobs}): {item.name}", 0
            )

    def _on_file_progress(self, path: str, ratio: float) -> None:
        self.model.set_progress(path, ratio)
        self._current_progress = ratio
        self._update_overall_progress()

    def _on_file_log(self, path: str, line: str) -> None:
        self.model.append_log(path, line)
        # 実行コマンドは目立たせ、ffmpeg の出力は控えめな色にする
        self.log_panel.append(line, "command" if line.startswith("$ ") else "ffmpeg")

    def _on_file_finished(
        self,
        path: str,
        outcome: str,
        message: str,
        output_path: str,
        output_size: int,
        elapsed_sec: float,
    ) -> None:
        status = {
            OUTCOME_DONE: FileStatus.DONE,
            OUTCOME_CANCELLED: FileStatus.CANCELLED,
        }.get(outcome, FileStatus.FAILED)

        self.model.apply_conversion_result(
            path,
            status,
            message=message,
            output_path=Path(output_path) if output_path else None,
            output_size=output_size or None,
            elapsed_sec=elapsed_sec or None,
        )
        item = self.model.item_at(self.model.find_row(path))
        name = item.name if item is not None else Path(path).name
        if status is FileStatus.DONE:
            size = format_bytes(output_size or None)
            self.log(
                f"完了: {output_path}（{size} / {elapsed_sec:.1f} 秒）",
                "success",
                file_name=name,
            )
        elif status is FileStatus.CANCELLED:
            self.log(message or "中断しました。", "warn", file_name=name)
        else:
            for line in (message or "変換に失敗しました。").splitlines():
                self.log(line, "error", file_name=name)

        self._completed_jobs += 1
        self._current_progress = 0.0
        self._update_overall_progress()

    def _on_conversion_finished(self, done: int, failed: int, cancelled: int) -> None:
        self._set_ui_locked(False)
        self._current_progress = 0.0
        self._update_overall_progress()

        parts = [f"成功 {done} 件"]
        if failed:
            parts.append(f"失敗 {failed} 件")
        if cancelled:
            parts.append(f"中断 {cancelled} 件")
        headline = "変換を中断しました" if cancelled and not done else "変換が完了しました"
        self.statusBar().showMessage(f"{headline}: " + " / ".join(parts), 0)
        self.overall_label.setText(" / ".join(parts))
        self.log_panel.reset_header()
        self.log(
            f"{headline}: " + " / ".join(parts),
            "error" if failed else ("warn" if cancelled else "success"),
        )
        if failed:
            self.statusBar().showMessage(
                f"{headline}: " + " / ".join(parts) + "  （詳細はログ / Ctrl+L）", 0
            )

        if failed:
            self._show_failure_summary(failed)

    def _show_failure_summary(self, failed: int) -> None:
        """失敗したファイルの理由をまとめて出す。"""
        reasons = [
            f"・{item.name}\n    {item.message.splitlines()[0] if item.message else '原因不明'}"
            for item in self.model.items
            if item.status is FileStatus.FAILED
        ]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("変換に失敗したファイルがあります")
        box.setText(f"{failed} 件の変換に失敗しました。")
        box.setInformativeText("\n".join(reasons[:10]))
        if len(reasons) > 10:
            box.setDetailedText("\n".join(reasons))
        box.exec()

    def _set_ui_locked(self, locked: bool) -> None:
        """変換中はリストの編集を止める（誤操作防止）。"""
        self._locked = locked
        self.add_button.setEnabled(not locked)
        # 変換中は開始時点の設定で確定させる
        self.settings.setEnabled(not locked)
        self.edit_button.setEnabled(not locked)
        # 変換中は ffmpeg と取り合わないようプレビューを止める
        self.edit_panel.set_playback_enabled(not locked)
        if locked:
            self.edit_panel.setEnabled(False)
        else:
            self._sync_edit_panel()
        self.view.set_drop_enabled(not locked)
        self.run_button.setEnabled(True)
        self.run_button.setText("キャンセル" if locked else "変換開始")
        self.run_button.setToolTip(
            "実行中のファイルを中断し、残りのキューを取り消します"
            if locked
            else "待機中のファイルを上から順に OGG Vorbis へ変換します"
        )
        self._update_buttons()

    def _update_overall_progress(self) -> None:
        """全体進捗＝完了件数＋実行中ファイルの進捗。"""
        if self._total_jobs <= 0:
            self.overall_progress.setValue(0)
            self.overall_label.setText("")
            return
        completed = min(self._completed_jobs, self._total_jobs)
        value = (completed + self._current_progress) / self._total_jobs
        self.overall_progress.setValue(int(round(value * 100)))
        if self.conversion.running:
            self.overall_label.setText(f"{completed} / {self._total_jobs} 完了")

    # ------------------------------------------------------------------
    # 表示の更新
    # ------------------------------------------------------------------
    def _update_status(self) -> None:
        counts = self.model.counts()
        if counts["total"] == 0:
            self.summary_label.setText("")
        else:
            parts = [f"{counts['total']} 件"]
            if counts["analyzing"]:
                parts.append(f"解析中 {counts['analyzing']}")
            if counts["ready"]:
                total = self.model.total_estimated_size()
                parts.append(f"変換可能 {counts['ready']}（約 {format_bytes(total)}）")
            if counts["done"]:
                parts.append(f"完了 {counts['done']}")
            if counts["failed"]:
                parts.append(f"失敗 {counts['failed']}")
            if counts["cancelled"]:
                parts.append(f"中断 {counts['cancelled']}")
            if counts["error"]:
                parts.append(f"エラー {counts['error']}")
            edited = self.model.edited_count()
            if edited:
                parts.append(f"編集 {edited}")
            self.summary_label.setText(" / ".join(parts))
        self._update_buttons()

    def _update_buttons(self) -> None:
        locked = self._locked
        has_items = self.model.rowCount() > 0
        self.clear_button.setEnabled(has_items and not locked)

        selected = self.view.selected_rows()
        removable = not locked and any(
            (item := self.model.item_at(r)) is not None and not item.status.is_busy
            for r in selected
        )
        self.remove_button.setEnabled(removable)

        if not locked:
            self.run_button.setEnabled(
                bool(self.model.convertible_items()) and self.settings.is_valid()
            )

    # ------------------------------------------------------------------
    # ffmpeg
    # ------------------------------------------------------------------
    def _update_ffmpeg_banner(self) -> None:
        if self._tools is None:
            # インストール方法をその場に出す（詳細は「インストール方法」ボタン）
            self.banner.label.setText(
                "ffmpeg が見つかりません。ファイルの解析と変換ができません。\n"
                + INSTALL_HINT
            )
            self.banner.show()
        elif not self._tools.supports(self.settings.output_format()):
            target = self.settings.output_format()
            self.banner.label.setText(
                f"この ffmpeg は {target.encoder_label} を含んでいないため、"
                f"{target.label} へ変換できません。"
            )
            self.banner.show()
        else:
            self.banner.hide()
        if self.banner.isVisibleTo(self):
            self.log(self.banner.label.text(), "error")

    def _recheck_ffmpeg(self) -> None:
        tools = find_ffmpeg_tools(force_refresh=True)
        self._tools = tools
        self.probe_service.set_tools(tools)
        self.waveform_service.set_tools(tools)
        self._update_ffmpeg_banner()
        if tools is None:
            self.statusBar().showMessage("ffmpeg はまだ見つかりません。", 6000)
            self.log("ffmpeg はまだ見つかりません。", "error")
            return
        self.statusBar().showMessage(f"{tools.version} を検出しました。", 6000)
        self.log(f"{tools.version} を検出しました: {tools.ffmpeg}", "success")
        # 解析できずエラーになっていた項目を再解析する
        rows = self.model.rows_of_status(FileStatus.ERROR)
        if rows:
            self._reanalyze_rows(rows)

    def _show_ffmpeg_help(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("ffmpeg のインストール")
        box.setText("Voggify の変換には ffmpeg が必要です。")
        box.setDetailedText(missing_ffmpeg_message())
        box.exec()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        if self.conversion.running:
            answer = QMessageBox.question(
                self,
                "変換中です",
                "変換を中断して終了しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.conversion.cancel()
            self.conversion.wait(10_000)
        self.edit_panel.player.stop()
        self.probe_service.discard_pending()
        self.waveform_service.cancel_pending()
        # 設定は変更のたびではなく終了時にまとめて書く
        try:
            self.save_config()
        except Exception:  # noqa: BLE001 - 保存の失敗で終了を妨げない
            pass
        super().closeEvent(event)
