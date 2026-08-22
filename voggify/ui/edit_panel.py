"""簡易編集（トリミング・音量）のパネル。

一覧で 1 行だけ選ばれているときに有効になり、その行の編集内容を出す。
未選択・複数選択のときは中身を空にして無効化する。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QWidget,
)

from ..editing import (
    MAX_VOLUME_DB,
    MIN_VOLUME_DB,
    EditSettings,
    EditValueError,
    format_timecode,
    parse_timecode,
)
from ..waveform import WaveformData
from .preview_player import PreviewPlayer, boost_is_capped
from .waveform_view import WaveformView

#: エラー表示の色（settings_panel と揃える）
ERROR_COLOR = "#ff8080"
HINT_COLOR = "#9a9a9a"

#: スライダーは整数しか扱えないので 0.1dB 単位の整数に写す
VOLUME_STEPS_PER_DB = 10


class EditPanel(QGroupBox):
    """選択中のファイルの編集内容を出すパネル。"""

    #: 編集内容が変わった（EditSettings）
    edit_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("編集（選択したファイル）", parent)
        self._edit = EditSettings()
        self._source_duration: float | None = None
        self._file_name: str = ""
        #: 反映中のシグナルで再入しないためのフラグ
        self._loading = False

        self.player = PreviewPlayer(self)

        self._build()
        self._connect()
        self.set_target(None, None, None)

    # ------------------------------------------------------------------
    # 組み立て
    # ------------------------------------------------------------------
    def _build(self) -> None:
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(5, 1)

        # --- 対象 ---
        self.target_label = QLabel()
        self.target_label.setStyleSheet(f"color: {HINT_COLOR};")
        grid.addWidget(self.target_label, 0, 0, 1, 6)

        # --- 波形（ドラッグで範囲を選ぶ）---
        self.waveform = WaveformView()
        grid.addWidget(self.waveform, 1, 0, 1, 6)

        # --- 再生（波形のすぐ下）---
        self.play_button = QPushButton("▶ 再生")
        self.play_button.setFixedWidth(96)
        self.play_button.setToolTip(
            "元のファイルをそのまま再生します。\n"
            "波形をクリックするとその位置から再生します。"
        )
        grid.addWidget(self.play_button, 2, 0, 1, 2)

        self.play_status_label = QLabel()
        self.play_status_label.setStyleSheet(f"color: {HINT_COLOR};")
        grid.addWidget(self.play_status_label, 2, 2, 1, 4)

        # --- トリミング ---
        grid.addWidget(QLabel("切り出し"), 3, 0)

        self.start_edit = QLineEdit()
        self.start_edit.setFixedWidth(110)
        self.start_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.start_edit.setToolTip("開始位置（mm:ss.ms）")
        grid.addWidget(self.start_edit, 3, 1)

        grid.addWidget(QLabel("〜"), 3, 2)

        self.end_edit = QLineEdit()
        self.end_edit.setFixedWidth(110)
        self.end_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.end_edit.setToolTip("終了位置（mm:ss.ms）")
        grid.addWidget(self.end_edit, 3, 3)

        self.reset_trim_button = QPushButton("全体を使う")
        grid.addWidget(self.reset_trim_button, 3, 4)

        self.length_label = QLabel()
        self.length_label.setStyleSheet(f"color: {HINT_COLOR};")
        grid.addWidget(self.length_label, 3, 5)

        # --- 音量 ---
        grid.addWidget(QLabel("音量"), 4, 0)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(
            int(MIN_VOLUME_DB * VOLUME_STEPS_PER_DB),
            int(MAX_VOLUME_DB * VOLUME_STEPS_PER_DB),
        )
        self.volume_slider.setValue(0)
        self.volume_slider.setSingleStep(VOLUME_STEPS_PER_DB // 2)
        self.volume_slider.setPageStep(VOLUME_STEPS_PER_DB * 3)
        self.volume_slider.setTickInterval(VOLUME_STEPS_PER_DB * 10)
        self.volume_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.volume_slider.setFixedWidth(240)
        self.volume_slider.setToolTip("-30dB 〜 +30dB")
        grid.addWidget(self.volume_slider, 4, 1, 1, 3)

        self.reset_volume_button = QPushButton("0 dB に戻す")
        grid.addWidget(self.reset_volume_button, 4, 4)

        self.volume_label = QLabel()
        monospace = QFont("Consolas")
        monospace.setStyleHint(QFont.StyleHint.Monospace)
        self.volume_label.setFont(monospace)
        self.volume_label.setMinimumWidth(80)
        grid.addWidget(self.volume_label, 4, 5)

        # --- エラー ---
        self.error_label = QLabel()
        self.error_label.setStyleSheet(f"color: {ERROR_COLOR};")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        grid.addWidget(self.error_label, 5, 1, 1, 5)

    def _connect(self) -> None:
        self.waveform.range_changed.connect(self._on_waveform_dragging)
        self.waveform.range_committed.connect(self._on_waveform_committed)
        self.waveform.seek_requested.connect(self._on_seek_requested)
        self.play_button.clicked.connect(self.player.toggle)
        self.player.position_changed.connect(self.waveform.set_playhead)
        self.player.playing_changed.connect(self._on_playing_changed)
        self.player.failed.connect(self._on_playback_failed)
        # 入力欄は「確定したとき」に検証する（打っている途中で弾かない）
        self.start_edit.editingFinished.connect(self._apply_trim_from_fields)
        self.end_edit.editingFinished.connect(self._apply_trim_from_fields)
        self.reset_trim_button.clicked.connect(self.reset_trim)
        self.volume_slider.valueChanged.connect(self._on_volume_slider)
        self.reset_volume_button.clicked.connect(self.reset_volume)

    # ------------------------------------------------------------------
    # 対象の切り替え
    # ------------------------------------------------------------------
    def set_waveform(self, data: WaveformData | None, message: str = "") -> None:
        """波形データを差し込む。None ならプレースホルダを出す。"""
        if data is None:
            self.waveform.set_placeholder(message or "波形を読み込み中…")
        else:
            self.waveform.set_waveform(data, self._source_duration or data.duration)
            self._sync_waveform_range()

    def _sync_waveform_range(self) -> None:
        """数値入力の値を波形の選択範囲へ反映する。"""
        end = self._edit.effective_end(self._source_duration)
        self.waveform.set_range(
            self._edit.trim_start,
            end if end is not None else (self._source_duration or 0.0),
        )
        self.waveform.set_volume_db(self._edit.volume_db)

    def set_source_path(self, path) -> None:  # noqa: ANN001
        """再生するファイルを差し替える（再生中なら止まる）。"""
        self.player.set_source(path)

    def set_playback_enabled(self, enabled: bool) -> None:
        """変換中はプレビューを止めておく。"""
        self.player.set_enabled(enabled)
        self.play_button.setEnabled(enabled and self.player.available)
        if not enabled:
            self.waveform.set_playhead(None)
            self.play_status_label.setText("変換中はプレビューを使えません。")
        else:
            self._update_play_status()

    def set_target(
        self,
        file_name: str | None,
        edit: EditSettings | None,
        source_duration: float | None,
    ) -> None:
        """パネルが編集する対象を差し替える。

        file_name が None なら「対象なし」として無効化する。
        """
        self.player.stop()
        self._loading = True
        try:
            if file_name is None or edit is None:
                self._file_name = ""
                self._edit = EditSettings()
                self._source_duration = None
                self.setEnabled(False)
                self.target_label.setText(
                    "ファイルを 1 つ選ぶと、その範囲と音量を編集できます。"
                )
                self.start_edit.clear()
                self.end_edit.clear()
                self.volume_slider.setValue(0)
                self.volume_label.setText("")
                self.length_label.setText("")
                self.waveform.set_placeholder(
                    "ファイルを 1 つ選ぶと波形を表示します。"
                )
                self.player.set_source(None)
                self.play_button.setEnabled(False)
                self.play_status_label.setText("")
                self._set_error("")
                return

            self._file_name = file_name
            self._edit = edit
            self._source_duration = source_duration
            self.setEnabled(True)
            self.target_label.setText(
                f"{file_name}（全体 {format_timecode(source_duration)}）"
                if source_duration is not None
                else f"{file_name}（長さ不明）"
            )
            self.play_button.setEnabled(self.player.available)
            self._load_into_widgets()
        finally:
            self._loading = False

    def _load_into_widgets(self) -> None:
        self.start_edit.setText(format_timecode(self._edit.trim_start))
        end = self._edit.effective_end(self._source_duration)
        self.end_edit.setText(format_timecode(end) if end is not None else "")
        self.volume_slider.setValue(
            int(round(self._edit.volume_db * VOLUME_STEPS_PER_DB))
        )
        self._update_labels()
        self._sync_waveform_range()
        self.player.set_volume_db(self._edit.volume_db)
        self._update_play_status()
        self._set_error("")

    # ------------------------------------------------------------------
    # 値の取得
    # ------------------------------------------------------------------
    def edit(self) -> EditSettings:
        return self._edit

    def error_message(self) -> str:
        return self.error_label.text()

    # ------------------------------------------------------------------
    # トリミング
    # ------------------------------------------------------------------
    def _apply_trim_from_fields(self) -> None:
        if self._loading or not self.isEnabled():
            return
        try:
            start = parse_timecode(self.start_edit.text())
            end_text = self.end_edit.text().strip()
            end = parse_timecode(end_text) if end_text else None
            updated = self._edit.with_trim(start, end, self._source_duration)
        except EditValueError as exc:
            # 直前の正しい値に戻して、理由を出す
            self._set_error(str(exc))
            self._loading = True
            try:
                self._load_into_widgets()
            finally:
                self._loading = False
            self._set_error(str(exc))
            return

        self._set_error("")
        self._commit(updated)

    # ------------------------------------------------------------------
    # 再生
    # ------------------------------------------------------------------
    def _on_seek_requested(self, seconds: float) -> None:
        """波形をクリックされた。その位置から再生する。"""
        if not self.isEnabled():
            return
        self.waveform.set_playhead(seconds)
        self.player.play_from(seconds)

    def _on_playing_changed(self, playing: bool) -> None:
        self.play_button.setText("⏸ 一時停止" if playing else "▶ 再生")
        if not playing and not self.player.is_playing:
            # 停止したらカーソルは残さない（一時停止では残す）
            if self.player.position() <= 0.0:
                self.waveform.set_playhead(None)
        self._update_play_status()

    def _on_playback_failed(self, message: str) -> None:
        self.play_status_label.setText(message)

    def _update_play_status(self) -> None:
        """増幅がプレビューに乗らないことを伝える。"""
        if not self.player.available:
            self.play_status_label.setText("この環境では再生できません。")
            return
        if boost_is_capped(self._edit.volume_db):
            self.play_status_label.setText(
                "プレビューは増幅できないため 0dB で鳴ります（変換結果には反映されます）"
            )
        else:
            self.play_status_label.setText("")

    def _on_waveform_dragging(self, start: float, end: float) -> None:
        """ドラッグ中。数値欄だけ追随させ、確定は離したときに行う。"""
        if self._loading or not self.isEnabled():
            return
        self._loading = True
        try:
            self.start_edit.setText(format_timecode(start))
            self.end_edit.setText(format_timecode(end))
            self.length_label.setText(f"→ {format_timecode(max(0.0, end - start))}")
        finally:
            self._loading = False

    def _on_waveform_committed(self, start: float, end: float) -> None:
        """ドラッグが終わったので編集内容として確定する。"""
        if not self.isEnabled():
            return
        try:
            updated = self._edit.with_trim(start, end, self._source_duration)
        except EditValueError as exc:
            self._set_error(str(exc))
            self._loading = True
            try:
                self._load_into_widgets()
            finally:
                self._loading = False
            return
        self._set_error("")
        self._commit(updated)

    def reset_trim(self) -> None:
        """「全体を使う」。切り出しを解除する。"""
        if not self.isEnabled():
            return
        self._set_error("")
        self._commit(self._edit.without_trim())

    # ------------------------------------------------------------------
    # 音量
    # ------------------------------------------------------------------
    def _on_volume_slider(self, value: int) -> None:
        if self._loading or not self.isEnabled():
            return
        self._commit(self._edit.with_volume(value / VOLUME_STEPS_PER_DB))

    def reset_volume(self) -> None:
        if not self.isEnabled():
            return
        self._commit(self._edit.without_volume())

    # ------------------------------------------------------------------
    def _commit(self, updated: EditSettings) -> None:
        """新しい編集内容を確定して通知する。"""
        self._edit = updated
        self._loading = True
        try:
            self.play_button.setEnabled(self.player.available)
            self._load_into_widgets()
        finally:
            self._loading = False
        self.edit_changed.emit(updated)

    def _update_labels(self) -> None:
        length = self._edit.effective_duration(self._source_duration)
        if length is None:
            self.length_label.setText("")
        elif self._edit.has_trim:
            self.length_label.setText(f"→ {format_timecode(length)}")
        else:
            self.length_label.setText("（全体）")

        volume = self._edit.volume_db
        self.volume_label.setText(
            f"{volume:+.1f} dB" if self._edit.has_volume else " 0.0 dB"
        )

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
