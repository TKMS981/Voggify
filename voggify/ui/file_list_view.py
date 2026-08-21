"""ファイルリストのビュー。

ドラッグ&ドロップ、Delete キー削除、右クリックメニューを担当する。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QPainter,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTableView,
    QWidget,
)

from .file_list_model import (
    COL_FORMAT,
    COL_NAME,
    COL_PROGRESS,
    COL_SIZE,
    COL_STATUS,
    FileListModel,
)
from .progress_delegate import ProgressDelegate

#: 空のときに中央へ出す案内
EMPTY_HINT = "ここに音楽ファイルをドラッグ&ドロップ\n（MP3 / WAV / FLAC / AAC / M4A）"


class FileListView(QTableView):
    """ドロップを受け付けるファイル一覧。"""

    #: 外部からファイル/フォルダがドロップされた
    files_dropped = Signal(list)
    #: 選択行の削除が要求された
    remove_requested = Signal(list)
    #: 選択行の再解析が要求された
    reanalyze_requested = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setStretchLastSection(False)

        # 外部からのドロップのみ受け付ける（行の並べ替えはしない）
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(False)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

        self._drag_active = False
        self._drop_enabled = True

    def setModel(self, model) -> None:  # noqa: N802, ANN001
        """列幅の設定はセクションが生える（=モデル設定後）まで効かない。"""
        super().setModel(model)
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_FORMAT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_PROGRESS, 150)
        self.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self))

    def set_drop_enabled(self, enabled: bool) -> None:
        """変換中は誤ってファイルを落とせないようにする。"""
        self._drop_enabled = enabled
        self.setAcceptDrops(enabled)
        self.viewport().setAcceptDrops(enabled)

    # ------------------------------------------------------------------
    # ドラッグ&ドロップ
    # ------------------------------------------------------------------
    @staticmethod
    def _local_paths(event: QDropEvent | QDragEnterEvent | QDragMoveEvent) -> list[Path]:
        """ドラッグされた中身からローカルのパスだけ取り出す。"""
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        paths: list[Path] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            local = url.toLocalFile()
            if local:
                paths.append(Path(local))
        return paths

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._drop_enabled and self._local_paths(event):
            self._drag_active = True
            self.viewport().update()
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._drop_enabled and self._local_paths(event):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self._drag_active = False
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = self._local_paths(event) if self._drop_enabled else []
        self._drag_active = False
        self.viewport().update()
        if not paths:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.acceptProposedAction()
        self.files_dropped.emit(paths)

    # ------------------------------------------------------------------
    # キーボード / コンテキストメニュー
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            rows = self.selected_rows()
            if rows:
                self.remove_requested.emit(rows)
                event.accept()
                return
        super().keyPressEvent(event)

    def selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.selectionModel().selectedRows()})

    def _show_context_menu(self, position: QPoint) -> None:
        index: QModelIndex = self.indexAt(position)
        rows = self.selected_rows()
        if index.isValid() and index.row() not in rows:
            self.selectRow(index.row())
            rows = [index.row()]

        menu = QMenu(self)
        if rows:
            remove = QAction(f"選択した {len(rows)} 件を削除", self)
            remove.setShortcut(Qt.Key.Key_Delete)
            remove.triggered.connect(lambda: self.remove_requested.emit(rows))
            menu.addAction(remove)

            reanalyze = QAction("再解析", self)
            reanalyze.triggered.connect(lambda: self.reanalyze_requested.emit(rows))
            menu.addAction(reanalyze)

            menu.addSeparator()
            open_folder = QAction("保存フォルダを開く", self)
            open_folder.triggered.connect(lambda: self._reveal(rows[0]))
            menu.addAction(open_folder)
        else:
            placeholder = QAction("（項目を選択してください）", self)
            placeholder.setEnabled(False)
            menu.addAction(placeholder)

        menu.exec(self.viewport().mapToGlobal(position))

    def _reveal(self, row: int) -> None:
        """エクスプローラー等でファイルの場所を開く。"""
        model = self.model()
        if not isinstance(model, FileListModel):
            return
        item = model.item_at(row)
        if item is None:
            return
        path = item.path
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802, ANN001
        super().paintEvent(event)
        model = self.model()
        if model is not None and model.rowCount() > 0 and not self._drag_active:
            return

        painter = QPainter(self.viewport())
        rect = self.viewport().rect()
        if self._drag_active:
            painter.fillRect(rect, QColor(80, 140, 220, 40))
            painter.setPen(QColor(80, 140, 220))
            painter.drawRect(rect.adjusted(2, 2, -3, -3))
        if model is None or model.rowCount() == 0:
            painter.setPen(QColor(140, 140, 140))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, EMPTY_HINT)
        painter.end()
