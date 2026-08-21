"""進捗列を進捗バーとして描く delegate。

行ごとに QProgressBar ウィジェットを置くと行数分のウィジェットができてしまうので、
QStyle で直接描画する。
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, Qt
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QStyleOptionViewItem,
)

from ..models import FileStatus
from .file_list_model import ROLE_PROGRESS, ROLE_STATUS

#: 進捗バーを描く状態
_BAR_STATUSES = (FileStatus.CONVERTING, FileStatus.DONE, FileStatus.CANCELLED)


class ProgressDelegate(QStyledItemDelegate):
    """変換中は進捗バー、それ以外は通常のテキストを描く。"""

    def paint(
        self,
        painter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        status = index.data(ROLE_STATUS)
        if status not in _BAR_STATUSES:
            super().paint(painter, option, index)
            return

        # 選択時の背景などは既定の描画に任せる
        self.initStyleOption(option, index)
        option.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)

        ratio = float(index.data(ROLE_PROGRESS) or 0.0)
        if status is FileStatus.DONE:
            ratio = 1.0

        bar = QStyleOptionProgressBar()
        bar.rect = QRect(option.rect).adjusted(4, 5, -4, -5)
        bar.minimum = 0
        bar.maximum = 100
        bar.progress = int(round(max(0.0, min(1.0, ratio)) * 100))
        bar.textVisible = True
        bar.textAlignment = Qt.AlignmentFlag.AlignCenter
        if status is FileStatus.CANCELLED:
            bar.text = "中断"
        else:
            bar.text = f"{bar.progress}%"
        # State_Horizontal を落とすと縦向きの進捗バーとして描かれてしまう
        bar.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Horizontal
        bar.palette = option.palette
        bar.fontMetrics = option.fontMetrics
        bar.direction = option.direction

        style.drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter, option.widget)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex):  # noqa: N802
        size = super().sizeHint(option, index)
        size.setWidth(max(size.width(), 110))
        return size
