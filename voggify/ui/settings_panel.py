"""変換設定（品質・出力先）のパネル。

ファイル一覧の視認性を落とさないよう、横一列にまとめて一覧の上に置く。
（右サイドパネルにすると一覧のファイル名列を圧迫するため）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QWidget,
)

from ..converter import ensure_writable_dir
from ..errors import OutputPathError
from ..output_formats import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_QUALITY,
    MAX_QUALITY,
    MIN_QUALITY,
    OUTPUT_FORMATS,
    OutputFormat,
    clamp_quality,
    output_format_by_key,
)

#: エラー表示の色
ERROR_COLOR = "#ff8080"
#: 補足テキストの色（palette(mid) は暗すぎて読めない環境がある）
HINT_COLOR = "#9a9a9a"


class SettingsPanel(QGroupBox):
    """品質スライダーと出力先の指定をまとめたパネル。"""

    #: 出力形式が変わった
    output_format_changed = Signal(object)
    #: 品質が変わった（0〜10）
    quality_changed = Signal(int)
    #: 出力先が変わった（None なら入力ファイルと同じフォルダ）
    output_dir_changed = Signal(object)
    #: 設定が有効か変わった（出力先に書き込めないと False）
    validity_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("変換設定", parent)
        self._output_dir: Path | None = None
        self._valid = True
        self._last_browse_dir = str(Path.home())
        self._output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT

        self._build()
        self._connect()
        self._update_quality_labels(DEFAULT_QUALITY)

    # ------------------------------------------------------------------
    # 組み立て
    # ------------------------------------------------------------------
    def _build(self) -> None:
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(3, 1)  # パス欄に余白を割り当てる

        # --- 出力形式 ---
        grid.addWidget(QLabel("出力形式"), 0, 0)

        format_row = QHBoxLayout()
        format_row.setSpacing(12)
        self.format_buttons: dict[str, QRadioButton] = {}
        self._format_group = QButtonGroup(self)
        for index, fmt in enumerate(OUTPUT_FORMATS):
            button = QRadioButton(f"{fmt.label} ({fmt.extension})")
            button.setChecked(fmt is DEFAULT_OUTPUT_FORMAT)
            self._format_group.addButton(button, index)
            self.format_buttons[fmt.key] = button
            format_row.addWidget(button)
        format_row.addStretch(1)
        format_holder = QWidget()
        format_holder.setLayout(format_row)
        grid.addWidget(format_holder, 0, 1, 1, 4)

        # --- 品質 ---
        grid.addWidget(QLabel("品質 (-q:a)"), 1, 0)

        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(MIN_QUALITY, MAX_QUALITY)
        self.quality_slider.setValue(DEFAULT_QUALITY)
        self.quality_slider.setSingleStep(1)
        self.quality_slider.setPageStep(1)
        self.quality_slider.setTickInterval(1)
        self.quality_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.quality_slider.setFixedWidth(240)
        self.quality_slider.setToolTip(
            "大きいほど高音質・大容量になります。\n"
            "出力形式を切り替えても同じ向き（大きい方が高音質）で扱えます。"
        )
        grid.addWidget(self.quality_slider, 1, 1)

        self.quality_value_label = QLabel()
        self.quality_value_label.setMinimumWidth(56)
        grid.addWidget(self.quality_value_label, 1, 2)

        self.quality_hint_label = QLabel()
        self.quality_hint_label.setStyleSheet(f"color: {HINT_COLOR};")
        grid.addWidget(self.quality_hint_label, 1, 3, 1, 2)

        # --- 出力先 ---
        grid.addWidget(QLabel("出力先"), 2, 0)

        choice = QHBoxLayout()
        choice.setSpacing(12)
        self.same_folder_radio = QRadioButton("入力ファイルと同じフォルダ")
        self.same_folder_radio.setChecked(True)
        choice.addWidget(self.same_folder_radio)
        self.custom_folder_radio = QRadioButton("フォルダを指定")
        choice.addWidget(self.custom_folder_radio)
        holder = QWidget()
        holder.setLayout(choice)
        grid.addWidget(holder, 2, 1, 1, 2)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("（未指定）")
        self.path_edit.setEnabled(False)
        grid.addWidget(self.path_edit, 2, 3)

        self.browse_button = QPushButton("参照…")
        self.browse_button.setEnabled(False)
        grid.addWidget(self.browse_button, 2, 4, Qt.AlignmentFlag.AlignLeft)

        # --- エラー表示 ---
        self.error_label = QLabel()
        self.error_label.setStyleSheet(f"color: {ERROR_COLOR};")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        grid.addWidget(self.error_label, 3, 1, 1, 4)

    def _connect(self) -> None:
        self._format_group.idToggled.connect(self._on_format_toggled)
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        self.same_folder_radio.toggled.connect(self._on_mode_toggled)
        self.browse_button.clicked.connect(self.browse_output_dir)

    # ------------------------------------------------------------------
    # 値の取得
    # ------------------------------------------------------------------
    def quality(self) -> int:
        return self.quality_slider.value()

    def output_format(self) -> OutputFormat:
        return self._output_format

    def set_output_format(self, fmt: OutputFormat) -> None:
        """出力形式を切り替える（設定の復元とテストから使う）。"""
        button = self.format_buttons.get(fmt.key)
        if button is not None and not button.isChecked():
            button.setChecked(True)  # ここから _on_format_toggled 経由
        elif fmt is not self._output_format:
            self._apply_output_format(fmt)

    def output_dir(self) -> Path | None:
        """出力先。None なら入力ファイルと同じフォルダ。"""
        if self.same_folder_radio.isChecked():
            return None
        return self._output_dir

    def uses_custom_output_dir(self) -> bool:
        return self.custom_folder_radio.isChecked()

    def remembered_output_dir(self) -> Path | None:
        """最後に選んだ出力先。「同じフォルダ」に戻していても覚えている。"""
        return self._output_dir

    def restore(
        self,
        quality: int,
        use_custom_output_dir: bool,
        output_dir: str | Path | None,
        output_format_key: str | None = None,
    ) -> str | None:
        """保存しておいた設定を復元する。

        前回の出力先が使えなくなっていることがある（USB メモリを抜いた、
        フォルダを消した等）ので、その場合は「入力と同じフォルダ」に戻し、
        理由を文字列で返す。問題なければ None。
        """
        restored_format = output_format_by_key(output_format_key)
        if restored_format is not None:
            self.set_output_format(restored_format)
        self.quality_slider.setValue(clamp_quality(quality))

        if not output_dir:
            self.same_folder_radio.setChecked(True)
            return None

        path = Path(output_dir).expanduser()
        # 使わない場合でもパスは覚えておく。USB メモリを挿し直したときなどに
        # ラジオを切り替えるだけで元の出力先に戻せる。
        self._remember(path)

        if not use_custom_output_dir:
            self.same_folder_radio.setChecked(True)
            return None

        if not path.is_dir():
            self.same_folder_radio.setChecked(True)
            return (
                f"前回の出力先が見つかりません: {path}"
                "\n「入力ファイルと同じフォルダ」に戻しました。"
            )
        try:
            ensure_writable_dir(path)
        except OutputPathError:
            self.same_folder_radio.setChecked(True)
            return (
                f"前回の出力先に書き込めません: {path}"
                "\n「入力ファイルと同じフォルダ」に戻しました。"
            )

        self.set_output_dir(path)
        return None

    def _remember(self, path: Path) -> None:
        """パスを覚えて表示だけ更新する（ラジオの状態は変えない）。"""
        self._output_dir = path
        self._last_browse_dir = str(path)
        self.path_edit.setText(str(path))
        self.path_edit.setToolTip(str(path))

    def is_valid(self) -> bool:
        """このまま変換を開始してよいか。"""
        return self._valid

    def error_message(self) -> str:
        return self.error_label.text()

    # ------------------------------------------------------------------
    # 品質
    # ------------------------------------------------------------------
    def _on_format_toggled(self, index: int, checked: bool) -> None:
        if not checked or not 0 <= index < len(OUTPUT_FORMATS):
            return
        self._apply_output_format(OUTPUT_FORMATS[index])

    def _apply_output_format(self, fmt: OutputFormat) -> None:
        self._output_format = fmt
        # 品質の数値は据え置き。意味（何 kbps 相当か）だけ出し直す
        self._update_quality_labels(self.quality_slider.value())
        self.output_format_changed.emit(fmt)

    def _on_quality_changed(self, value: int) -> None:
        self._update_quality_labels(value)
        self.quality_changed.emit(value)

    def _update_quality_labels(self, value: int) -> None:
        self.quality_value_label.setText(f"品質: {value}")
        self.quality_hint_label.setText(self._output_format.quality_hint(value))

    # ------------------------------------------------------------------
    # 出力先
    # ------------------------------------------------------------------
    def _on_mode_toggled(self, same_folder: bool) -> None:
        self.path_edit.setEnabled(not same_folder)
        self.browse_button.setEnabled(not same_folder)

        if same_folder:
            self._set_error("")
            self.output_dir_changed.emit(None)
            return

        if self._output_dir is None:
            # 「フォルダを指定」を選んだ直後はそのまま選択ダイアログを開く
            if not self.browse_output_dir():
                self.same_folder_radio.setChecked(True)
            return

        self._validate_and_notify()

    def browse_output_dir(self) -> bool:
        """フォルダ選択ダイアログを開く。選択されたら True。"""
        start = str(self._output_dir) if self._output_dir else self._last_browse_dir
        selected = QFileDialog.getExistingDirectory(
            self, "出力先フォルダを選択", start
        )
        if not selected:
            return False
        self.set_output_dir(Path(selected))
        return True

    def set_output_dir(self, directory: Path) -> None:
        """出力先を設定する（テストからも使う）。"""
        self._remember(Path(directory))
        if not self.custom_folder_radio.isChecked():
            self.custom_folder_radio.setChecked(True)  # ここから _on_mode_toggled 経由
            return
        self._validate_and_notify()

    def _validate_and_notify(self) -> None:
        """選択した時点で書き込み可否を確かめる（変換開始まで待たない）。"""
        directory = self._output_dir
        if directory is None:
            self._set_error("出力先フォルダが選ばれていません。")
        else:
            try:
                ensure_writable_dir(directory)
            except OutputPathError as exc:
                self._set_error(exc.user_message.splitlines()[0])
            else:
                self._set_error("")
        self.output_dir_changed.emit(self.output_dir())

    def revalidate(self) -> None:
        """変換開始の直前など、状況が変わりうる場面で再確認する。"""
        if self.same_folder_radio.isChecked():
            self._set_error("")
        else:
            self._validate_and_notify()

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        valid = not message
        if valid != self._valid:
            self._valid = valid
            self.validity_changed.emit(valid)
