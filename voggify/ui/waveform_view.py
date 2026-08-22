"""波形の表示とドラッグでの範囲選択。

描画コスト対策
--------------
波形そのものは QPixmap に一度描いてから貼る。選択範囲やハンドルは
その上に重ねるだけなので、ドラッグ中は波形を描き直さない。
ピクセルマップはウィジェットの大きさ・波形データ・音量が変わったときだけ
作り直す（リサイズ中に何度も呼ばれても、実際の再生成は 1 回で済む）。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..editing import format_timecode
from ..waveform import WaveformData

#: 配色
BACKGROUND = QColor(30, 32, 36)
WAVE_COLOR = QColor(110, 170, 220)
WAVE_MUTED = QColor(70, 84, 96)
CENTER_LINE = QColor(60, 66, 74)
SELECTION_FILL = QColor(110, 170, 220, 46)
OUTSIDE_FILL = QColor(18, 19, 22, 165)
HANDLE_COLOR = QColor(235, 150, 70)
RULER_TEXT = QColor(150, 156, 164)
RULER_LINE = QColor(70, 76, 84)
PLACEHOLDER_TEXT = QColor(140, 146, 154)
#: 再生位置のカーソル
PLAYHEAD_COLOR = QColor(250, 250, 250)

#: 目盛りの高さ（下端に確保する）
RULER_HEIGHT = 18
#: ハンドルの掴みやすさ（この距離内なら掴んだ扱い）
HANDLE_GRAB_PX = 7
#: ハンドルの描画幅
HANDLE_WIDTH = 3
#: ドラッグと判定する最小の移動量（クリックで全体解除にしないため）
DRAG_THRESHOLD_PX = 3


class WaveformView(QWidget):
    """波形を出し、ドラッグで範囲を選ばせるウィジェット。"""

    #: 範囲が変わった（開始秒, 終了秒）。全体に戻したときは (0.0, duration)
    range_changed = Signal(float, float)
    #: ドラッグが終わった（確定のタイミング）
    range_committed = Signal(float, float)
    #: 動かさずにクリックされた。その位置から再生してほしい
    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)

        self._data: WaveformData | None = None
        self._duration: float = 0.0
        self._start: float = 0.0
        self._end: float = 0.0
        self._volume_db: float = 0.0
        self._placeholder: str = "ファイルを選ぶと波形を表示します。"
        #: 再生位置（秒）。None なら出さない。
        self._playhead: float | None = None

        #: 波形だけを描いたもの。選択の描画では作り直さない。
        self._pixmap: QPixmap | None = None
        self._pixmap_key: tuple | None = None

        #: ドラッグの状態
        self._dragging: str | None = None  # "new" / "start" / "end"
        self._press_x: int | None = None

    # ------------------------------------------------------------------
    # 外から設定するもの
    # ------------------------------------------------------------------
    def set_placeholder(self, text: str) -> None:
        """波形が無いときに出す文言。"""
        self._data = None
        self._playhead = None
        self._placeholder = text
        self._invalidate()

    def set_waveform(self, data: WaveformData | None, duration: float) -> None:
        self._playhead = None
        self._data = data
        self._duration = duration if duration > 0 else (data.duration if data else 0.0)
        if self._end <= 0.0:
            self._start, self._end = 0.0, self._duration
        self._invalidate()

    def set_range(self, start: float, end: float) -> None:
        """数値入力側からの反映。シグナルは出さない（往復を防ぐ）。"""
        self._start = max(0.0, start)
        self._end = end if end > 0 else self._duration
        self.update()

    def set_volume_db(self, volume_db: float) -> None:
        """音量を波形の振幅に反映する。"""
        if abs(volume_db - self._volume_db) < 0.01:
            return
        self._volume_db = volume_db
        self._invalidate()

    def set_playhead(self, seconds: float | None) -> None:
        """再生位置のカーソルを動かす。None で消える。"""
        if seconds is None:
            if self._playhead is None:
                return
            self._playhead = None
            self.update()
            return
        clamped = max(0.0, min(self._duration, seconds))
        # 1px 未満の移動では描き直さない
        if self._playhead is not None and self._duration > 0:
            if abs(self._x_of(clamped) - self._x_of(self._playhead)) < 1.0:
                self._playhead = clamped
                return
        self._playhead = clamped
        self.update()

    def playhead(self) -> float | None:
        return self._playhead

    def selection(self) -> tuple[float, float]:
        return self._start, self._end

    @property
    def has_waveform(self) -> bool:
        return self._data is not None

    # ------------------------------------------------------------------
    # 座標の変換
    # ------------------------------------------------------------------
    def _wave_rect(self) -> QRect:
        return QRect(0, 0, self.width(), max(1, self.height() - RULER_HEIGHT))

    def _x_of(self, seconds: float) -> float:
        if self._duration <= 0:
            return 0.0
        return seconds / self._duration * self.width()

    def _seconds_at(self, x: float) -> float:
        if self._duration <= 0 or self.width() <= 0:
            return 0.0
        return max(0.0, min(self._duration, x / self.width() * self._duration))

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    def _invalidate(self) -> None:
        self._pixmap = None
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        self._pixmap = None  # 次の paint で作り直す

    def _ensure_pixmap(self) -> QPixmap | None:
        """波形のピクセルマップを必要なときだけ作る。"""
        if self._data is None:
            return None
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), id(self._data), round(self._volume_db, 2), ratio)
        if self._pixmap is not None and self._pixmap_key == key:
            return self._pixmap

        rect = self._wave_rect()
        pixmap = QPixmap(int(self.width() * ratio), int(rect.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._draw_wave(painter, QRect(0, 0, self.width(), rect.height()))
        painter.end()

        self._pixmap = pixmap
        self._pixmap_key = key
        return pixmap

    def _draw_wave(self, painter: QPainter, rect: QRect) -> None:
        assert self._data is not None
        width = max(1, rect.width())
        envelope = self._data.envelope(width)
        if not envelope:
            return

        gain = 10 ** (self._volume_db / 20.0) if self._volume_db else 1.0
        middle = rect.height() / 2.0
        scale = middle - 2

        painter.setPen(QPen(WAVE_COLOR, 1))
        columns = len(envelope)
        for index, (low, high) in enumerate(envelope):
            # envelope が width より少ないことがあるので位置を按分する
            x = rect.left() + (index + 0.5) * width / columns
            top = middle - max(-1.0, min(1.0, high * gain)) * scale
            bottom = middle - max(-1.0, min(1.0, low * gain)) * scale
            if bottom - top < 1:
                bottom = top + 1
            painter.drawLine(int(x), int(top), int(x), int(bottom))

        painter.setPen(QPen(CENTER_LINE, 1))
        painter.drawLine(rect.left(), int(middle), rect.right(), int(middle))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)

        if self._data is None:
            painter.setPen(PLACEHOLDER_TEXT)
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder
            )
            painter.end()
            return

        wave_rect = self._wave_rect()
        pixmap = self._ensure_pixmap()
        if pixmap is not None:
            painter.drawPixmap(wave_rect.topLeft(), pixmap)

        self._draw_selection(painter, wave_rect)
        self._draw_playhead(painter, wave_rect)
        self._draw_ruler(painter)
        painter.end()

    def _draw_selection(self, painter: QPainter, rect: QRect) -> None:
        if self._duration <= 0:
            return
        left = self._x_of(self._start)
        right = self._x_of(self._end)

        # 範囲外を暗くする
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(OUTSIDE_FILL))
        if left > 0:
            painter.drawRect(QRectF(0, rect.top(), left, rect.height()))
        if right < rect.width():
            painter.drawRect(
                QRectF(right, rect.top(), rect.width() - right, rect.height())
            )

        # 範囲内をうっすら塗る
        painter.setBrush(QBrush(SELECTION_FILL))
        painter.drawRect(QRectF(left, rect.top(), max(1.0, right - left), rect.height()))

        # 両端のハンドル
        painter.setBrush(QBrush(HANDLE_COLOR))
        for x in (left, right):
            painter.drawRect(
                QRectF(x - HANDLE_WIDTH / 2, rect.top(), HANDLE_WIDTH, rect.height())
            )

    def _draw_playhead(self, painter: QPainter, rect: QRect) -> None:
        if self._playhead is None or self._duration <= 0:
            return
        x = self._x_of(self._playhead)
        painter.setPen(QPen(PLAYHEAD_COLOR, 1))
        painter.drawLine(int(x), rect.top(), int(x), rect.bottom())

    def _draw_ruler(self, painter: QPainter) -> None:
        if self._duration <= 0:
            return
        top = self.height() - RULER_HEIGHT
        painter.setPen(QPen(RULER_LINE, 1))
        painter.drawLine(0, top, self.width(), top)

        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)

        step = _tick_step(self._duration, self.width())
        painter.setPen(RULER_TEXT)
        tick = 0.0
        while tick <= self._duration + 1e-6:
            x = self._x_of(tick)
            painter.setPen(QPen(RULER_LINE, 1))
            painter.drawLine(int(x), top, int(x), top + 4)
            painter.setPen(RULER_TEXT)
            label = _tick_label(tick, step)
            # 右端からはみ出さないよう寄せ方を変える
            if x > self.width() - 30:
                painter.drawText(int(x) - 42, top + 5, 40, RULER_HEIGHT - 5,
                                 Qt.AlignmentFlag.AlignRight, label)
            else:
                painter.drawText(int(x) + 2, top + 5, 60, RULER_HEIGHT - 5,
                                 Qt.AlignmentFlag.AlignLeft, label)
            tick += step

    # ------------------------------------------------------------------
    # マウス操作
    # ------------------------------------------------------------------
    def _hit_handle(self, x: int) -> str | None:
        if self._duration <= 0:
            return None
        if abs(x - self._x_of(self._start)) <= HANDLE_GRAB_PX:
            return "start"
        if abs(x - self._x_of(self._end)) <= HANDLE_GRAB_PX:
            return "end"
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        x = int(event.position().x())
        if self._dragging is None:
            if self._data is not None and self._hit_handle(x):
                self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
            return

        if self._data is None:
            return
        seconds = self._seconds_at(x)
        if self._dragging == "start":
            self._start = min(seconds, self._end - _min_span(self._duration))
            self._start = max(0.0, self._start)
        elif self._dragging == "end":
            self._end = max(seconds, self._start + _min_span(self._duration))
            self._end = min(self._duration, self._end)
        else:  # 新しく引いている最中
            anchor = self._seconds_at(self._press_x or 0)
            self._start = max(0.0, min(anchor, seconds))
            self._end = min(self._duration, max(anchor, seconds))
        self.update()
        self.range_changed.emit(self._start, self._end)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._data is None:
            return
        x = int(event.position().x())
        self._press_x = x
        self._dragging = self._hit_handle(x) or "new"
        if self._dragging == "new":
            # 動かさずに離したら「全体」に戻さないよう、まだ変更しない
            pass
        self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging is None:
            return
        moved = (
            self._press_x is not None
            and abs(int(event.position().x()) - self._press_x) >= DRAG_THRESHOLD_PX
        )
        was_new = self._dragging == "new"
        self._dragging = None
        self._press_x = None
        self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))

        if was_new and not moved:
            # 範囲は変えず、その位置から再生してもらう
            self.seek_requested.emit(self._seconds_at(int(event.position().x())))
            return
        self.range_committed.emit(self._start, self._end)

    def leaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
        super().leaveEvent(event)


# ---------------------------------------------------------------------------
def _min_span(duration: float) -> float:
    """ハンドルを重ねられないようにする最小幅。"""
    return max(0.05, duration / 500.0)


def _tick_step(duration: float, width: int) -> float:
    """目盛りの間隔を、ラベルが重ならない範囲で切りのいい値にする。"""
    if duration <= 0 or width <= 0:
        return 1.0
    # 1 目盛りに最低 70px は使う
    rough = duration / max(1, width // 70)
    for candidate in (
        0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30,
        60, 120, 300, 600, 900, 1800, 3600, 7200,
    ):
        if candidate >= rough:
            return float(candidate)
    return math.ceil(rough / 3600) * 3600.0


def _tick_label(seconds: float, step: float) -> str:
    """目盛りの文字。1 秒未満の刻みのときだけ小数を出す。"""
    text = format_timecode(seconds)
    if step >= 1.0:
        return text.rsplit(".", 1)[0]
    return text[:-2] if text.endswith("0") else text
