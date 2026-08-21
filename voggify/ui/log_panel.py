"""ffmpeg の出力とアプリのイベントを流すログパネル。

ふだんは畳んでおき、必要なときだけ開く。行数には上限を設けて、
長時間の一括変換でもメモリを圧迫しないようにする。
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

#: 保持する最大行数（超えた分は古いものから捨てる）
MAX_LOG_BLOCKS = 5000

#: ログの種別ごとの色
LEVEL_COLORS = {
    "info": "#c8c8c8",
    "command": "#7fa8d0",
    "ffmpeg": "#9a9a9a",
    "warn": "#e0a040",
    "error": "#ff8080",
    "success": "#7fc47f",
    "header": "#d0d0d0",
}


class LogPanel(QWidget):
    """変換ログの表示・クリア・保存。"""

    #: 「閉じる」が押された
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_header: str | None = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.addWidget(QLabel("変換ログ"))

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.autoscroll_check = QCheckBox("自動スクロール")
        self.autoscroll_check.setChecked(True)
        toolbar.addWidget(self.autoscroll_check)

        self.clear_button = QPushButton("クリア")
        self.clear_button.clicked.connect(self.clear)
        toolbar.addWidget(self.clear_button)

        self.save_button = QPushButton("ファイルに保存…")
        self.save_button.clicked.connect(self.save_to_file)
        toolbar.addWidget(self.save_button)

        self.close_button = QPushButton("閉じる")
        self.close_button.clicked.connect(self.close_requested)
        toolbar.addWidget(self.close_button)
        layout.addLayout(toolbar)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setPlaceholderText("変換を実行するとここに ffmpeg の出力が表示されます。")
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.output.setFont(font)
        layout.addWidget(self.output, 1)

    # ------------------------------------------------------------------
    # 書き込み
    # ------------------------------------------------------------------
    def append(self, text: str, level: str = "info", *, timestamp: bool = True) -> None:
        """1 行追加する。level は LEVEL_COLORS のキー。"""
        at_bottom = self._at_bottom()
        color = LEVEL_COLORS.get(level, LEVEL_COLORS["info"])
        prefix = ""
        if timestamp:
            prefix = (
                f'<span style="color:#6a6a6a">'
                f"{datetime.now().strftime('%H:%M:%S')}</span> "
            )
        body = html.escape(text).replace(" ", "&nbsp;")
        self.output.appendHtml(f'{prefix}<span style="color:{color}">{body}</span>')
        if self.autoscroll_check.isChecked() and at_bottom:
            self._scroll_to_bottom()

    def append_header(self, title: str) -> None:
        """ファイルの区切り。同じ見出しが続くときは重複させない。"""
        if self._current_header == title:
            return
        self._current_header = title
        at_bottom = self._at_bottom()
        color = LEVEL_COLORS["header"]
        line = html.escape(f"───── {title} ─────")
        if self.output.blockCount() > 1:
            self.output.appendHtml("")
        self.output.appendHtml(
            f'<span style="color:{color}; font-weight:bold">{line}</span>'
        )
        if self.autoscroll_check.isChecked() and at_bottom:
            self._scroll_to_bottom()

    def reset_header(self) -> None:
        """見出しの重複判定をリセットする（変換の区切りなど）。"""
        self._current_header = None

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def clear(self) -> None:
        self.output.clear()
        self._current_header = None

    def is_empty(self) -> bool:
        return not self.output.toPlainText().strip()

    def to_text(self) -> str:
        return self.output.toPlainText()

    def save_to_file(self) -> Path | None:
        """ログをテキストファイルに保存する。保存したパスを返す。"""
        if self.is_empty():
            QMessageBox.information(self, "ログの保存", "保存できるログがありません。")
            return None

        default_name = f"voggify-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "ログの保存先",
            str(Path.home() / default_name),
            "テキストファイル (*.txt);;すべてのファイル (*)",
        )
        if not selected:
            return None

        path = Path(selected)
        try:
            path.write_text(self.to_text(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self,
                "ログの保存",
                f"ログを保存できませんでした。\n{path}\n\n{exc}",
            )
            return None
        return path

    # ------------------------------------------------------------------
    def _at_bottom(self) -> bool:
        bar = self.output.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_to_bottom(self) -> None:
        """末尾へ送る。

        moveCursor(End) は横方向にも飛んでしまい、ffmpeg の長いコマンド行で
        行頭が見えなくなるので、スクロールバーを直接動かして横は左端に戻す。
        """
        vertical = self.output.verticalScrollBar()
        vertical.setValue(vertical.maximum())
        self.output.horizontalScrollBar().setValue(0)

    def sizeHint(self):  # noqa: N802
        size = super().sizeHint()
        size.setHeight(200)
        return size


__all__ = ["LogPanel", "LEVEL_COLORS", "MAX_LOG_BLOCKS"]
