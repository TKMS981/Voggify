"""ファイルリストの Qt モデル。

表示だけでなく「どのファイルを抱えているか」の管理もここが担当する。
変換の進捗（ステップ3）もこのモデル経由で反映する想定。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import QApplication, QStyle

from ..formats import DEFAULT_QUALITY, SUPPORTED_EXTENSIONS
from ..models import FileItem, FileStatus
from ..probe import AudioInfo

COL_STATUS = 0
COL_NAME = 1
COL_FORMAT = 2
COL_SIZE = 3
COL_PROGRESS = 4
COLUMN_COUNT = 5

_HEADERS = ("状態", "ファイル名", "現在の形式", "変換後のサイズ", "進捗")

#: delegate から進捗と状態を読むためのカスタムロール
ROLE_PROGRESS = Qt.ItemDataRole.UserRole + 1
ROLE_STATUS = Qt.ItemDataRole.UserRole + 2

#: エラー行の文字色（グレーアウト）
ERROR_FOREGROUND = QColor(150, 150, 150)
#: 注記付き行のアクセント色
NOTE_FOREGROUND = QColor(196, 120, 0)


class FileListModel(QAbstractTableModel):
    """FileItem の一覧を保持する QAbstractTableModel。"""

    #: 件数や状態が変わった（ボタンの有効/無効やステータスバーの更新用）
    contents_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[FileItem] = []
        self._quality: int = DEFAULT_QUALITY
        self._icons: dict[FileStatus, QIcon] = {}

    # ------------------------------------------------------------------
    # QAbstractTableModel の実装
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else COLUMN_COUNT

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < COLUMN_COUNT:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            # 見出しの寄せを中身に合わせる
            if section == COL_SIZE:
                alignment = Qt.AlignmentFlag.AlignRight
            elif section == COL_STATUS:
                alignment = Qt.AlignmentFlag.AlignCenter
            else:
                alignment = Qt.AlignmentFlag.AlignLeft
            return int(alignment | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        column = index.column()

        if role == ROLE_PROGRESS:
            return item.progress
        if role == ROLE_STATUS:
            return item.status

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_text(item, column)

        if role == Qt.ItemDataRole.DecorationRole and column == COL_STATUS:
            return self._status_icon(item.status)

        if role == Qt.ItemDataRole.ToolTipRole:
            return item.tooltip(self._quality)

        if role == Qt.ItemDataRole.ForegroundRole:
            if item.status.is_error:
                return QBrush(ERROR_FOREGROUND)
            if item.note and column == COL_FORMAT:
                return QBrush(NOTE_FOREGROUND)
            return None

        if role == Qt.ItemDataRole.FontRole and item.status.is_error:
            font = QFont()
            font.setItalic(True)
            return font

        if role == Qt.ItemDataRole.TextAlignmentRole and column == COL_SIZE:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def _display_text(self, item: FileItem, column: int) -> str:
        if column == COL_STATUS:
            return item.status.value
        if column == COL_NAME:
            return item.name
        if column == COL_FORMAT:
            return item.display_format()
        if column == COL_SIZE:
            return item.display_size(self._quality)
        if column == COL_PROGRESS:
            # 進捗バーを描かない状態のときだけテキストを出す
            if item.status is FileStatus.QUEUED:
                return "変換待ち"
            if item.status is FileStatus.FAILED:
                return "失敗"
            return ""
        return ""

    def _status_icon(self, status: FileStatus) -> QIcon | None:
        """標準アイコンを使う（画像リソースを持たずに済ませる）。"""
        if status in self._icons:
            return self._icons[status]
        app = QApplication.instance()
        if app is None:
            return None
        style = app.style()
        mapping = {
            FileStatus.ANALYZING: QStyle.StandardPixmap.SP_BrowserReload,
            FileStatus.READY: QStyle.StandardPixmap.SP_FileIcon,
            FileStatus.ERROR: QStyle.StandardPixmap.SP_MessageBoxWarning,
            FileStatus.QUEUED: QStyle.StandardPixmap.SP_MediaPause,
            FileStatus.CONVERTING: QStyle.StandardPixmap.SP_MediaPlay,
            FileStatus.DONE: QStyle.StandardPixmap.SP_DialogApplyButton,
            FileStatus.FAILED: QStyle.StandardPixmap.SP_MessageBoxCritical,
            FileStatus.CANCELLED: QStyle.StandardPixmap.SP_BrowserStop,
        }
        icon = style.standardIcon(mapping[status])
        self._icons[status] = icon
        return icon

    # ------------------------------------------------------------------
    # ファイル管理
    # ------------------------------------------------------------------
    @property
    def items(self) -> list[FileItem]:
        return list(self._items)

    def item_at(self, row: int) -> FileItem | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def quality(self) -> int:
        return self._quality

    def set_quality(self, quality: int) -> None:
        """品質が変わったら予測サイズを引き直す（ステップ4のスライダー用）。"""
        if quality == self._quality:
            return
        self._quality = quality
        if self._items:
            self.dataChanged.emit(
                self.index(0, COL_SIZE),
                self.index(len(self._items) - 1, COL_SIZE),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole],
            )
            # サマリーの合計予測サイズも引き直す
            self.contents_changed.emit()

    def add_paths(self, paths: Iterable[Path]) -> tuple[list[Path], int]:
        """ファイルを追加する。

        戻り値は (実際に追加したパス, 重複でスキップした件数)。
        追加時点では解析前なので、対応外かどうかはまだ判定しない。
        """
        existing = {item.path for item in self._items}
        new_paths: list[Path] = []
        duplicates = 0
        for path in paths:
            resolved = _normalize(path)
            if resolved in existing:
                duplicates += 1
                continue
            existing.add(resolved)
            new_paths.append(resolved)

        if new_paths:
            start = len(self._items)
            self.beginInsertRows(QModelIndex(), start, start + len(new_paths) - 1)
            self._items.extend(FileItem(path=p) for p in new_paths)
            self.endInsertRows()
            self.contents_changed.emit()
        return new_paths, duplicates

    def remove_rows(self, rows: Iterable[int]) -> int:
        """指定行を削除する。処理中の項目は削除しない。"""
        targets = sorted(
            {r for r in rows if 0 <= r < len(self._items)},
            reverse=True,
        )
        removed = 0
        for row in targets:
            if self._items[row].status.is_busy:
                continue
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._items[row]
            self.endRemoveRows()
            removed += 1
        if removed:
            self.contents_changed.emit()
        return removed

    def remove_items(self, items: Iterable[FileItem]) -> int:
        wanted = {id(i) for i in items}
        rows = [r for r, item in enumerate(self._items) if id(item) in wanted]
        return self.remove_rows(rows)

    def clear(self) -> None:
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()
        self.contents_changed.emit()

    def rows_of_status(self, *statuses: FileStatus) -> list[int]:
        return [r for r, item in enumerate(self._items) if item.status in statuses]

    # ------------------------------------------------------------------
    # 解析結果の反映
    # ------------------------------------------------------------------
    def apply_probe_result(
        self,
        path: str | Path,
        info: AudioInfo | None,
        message: str,
    ) -> FileItem | None:
        """ProbeService からの結果を該当行に反映する。"""
        target = _normalize(Path(path))
        for row, item in enumerate(self._items):
            if item.path != target:
                continue
            if info is not None:
                item.info = info
                item.status = FileStatus.READY
                item.note = info.mismatch_note
                item.message = ""
            else:
                item.info = None
                item.status = FileStatus.ERROR
                item.note = None
                item.message = message or "解析に失敗しました。"
            self._emit_row_changed(row)
            self.contents_changed.emit()
            return item
        return None

    def reset_to_analyzing(self, rows: Iterable[int]) -> list[Path]:
        """再解析のために状態を戻す。対象のパスを返す。"""
        paths: list[Path] = []
        for row in rows:
            item = self.item_at(row)
            if item is None or item.status.is_busy:
                continue
            item.status = FileStatus.ANALYZING
            item.info = None
            item.note = None
            item.message = ""
            paths.append(item.path)
            self._emit_row_changed(row)
        if paths:
            self.contents_changed.emit()
        return paths

    # ------------------------------------------------------------------
    # 変換の反映
    # ------------------------------------------------------------------
    def find_row(self, path: str | Path) -> int:
        """パスから行番号を引く。見つからなければ -1。"""
        target = _normalize(Path(path))
        for row, item in enumerate(self._items):
            if item.path == target:
                return row
        return -1

    def mark_queued(self, items: Iterable[FileItem]) -> list[FileItem]:
        """変換キューに積む。前回の結果はここでクリアする。"""
        queued: list[FileItem] = []
        for item in items:
            row = self._items.index(item)
            item.reset_for_conversion()
            item.status = FileStatus.QUEUED
            self._emit_row_changed(row)
            queued.append(item)
        if queued:
            self.contents_changed.emit()
        return queued

    def set_converting(self, path: str | Path) -> FileItem | None:
        row = self.find_row(path)
        if row < 0:
            return None
        item = self._items[row]
        item.status = FileStatus.CONVERTING
        item.progress = 0.0
        self._emit_row_changed(row)
        self.contents_changed.emit()
        return item

    def set_progress(self, path: str | Path, ratio: float) -> None:
        """進捗だけを更新する（進捗列のみ再描画）。"""
        row = self.find_row(path)
        if row < 0:
            return
        item = self._items[row]
        item.progress = max(0.0, min(1.0, ratio))
        index = self.index(row, COL_PROGRESS)
        self.dataChanged.emit(index, index, [ROLE_PROGRESS, Qt.ItemDataRole.DisplayRole])

    def append_log(self, path: str | Path, line: str) -> None:
        row = self.find_row(path)
        if row >= 0:
            self._items[row].append_log(line)

    def apply_conversion_result(
        self,
        path: str | Path,
        status: FileStatus,
        message: str = "",
        output_path: Path | None = None,
        output_size: int | None = None,
        elapsed_sec: float | None = None,
    ) -> FileItem | None:
        """1 件分の変換結果を反映する。"""
        row = self.find_row(path)
        if row < 0:
            return None
        item = self._items[row]
        item.status = status
        item.message = message
        item.output_path = output_path
        item.output_size = output_size
        item.elapsed_sec = elapsed_sec
        item.progress = 1.0 if status is FileStatus.DONE else item.progress
        self._emit_row_changed(row)
        self.contents_changed.emit()
        return item

    def cancel_remaining(self) -> int:
        """キューに残っている項目を中断扱いにする。"""
        changed = 0
        for row, item in enumerate(self._items):
            if item.status is FileStatus.QUEUED:
                item.status = FileStatus.CANCELLED
                item.message = "変換を中断しました。"
                self._emit_row_changed(row)
                changed += 1
        if changed:
            self.contents_changed.emit()
        return changed

    def _emit_row_changed(self, row: int) -> None:
        self.dataChanged.emit(
            self.index(row, 0),
            self.index(row, COLUMN_COUNT - 1),
        )

    # ------------------------------------------------------------------
    # 集計
    # ------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        """サマリー表示用の集計。"""
        result = {
            "total": len(self._items),
            "ready": 0,
            "error": 0,
            "analyzing": 0,
            "queued": 0,
            "converting": 0,
            "done": 0,
            "failed": 0,
            "cancelled": 0,
        }
        by_status = {
            FileStatus.READY: "ready",
            FileStatus.ERROR: "error",
            FileStatus.ANALYZING: "analyzing",
            FileStatus.QUEUED: "queued",
            FileStatus.CONVERTING: "converting",
            FileStatus.DONE: "done",
            FileStatus.FAILED: "failed",
            FileStatus.CANCELLED: "cancelled",
        }
        for item in self._items:
            result[by_status[item.status]] += 1
        return result

    def total_estimated_size(self) -> int:
        return sum(
            item.estimated_size(self._quality) or 0
            for item in self._items
            if item.status is FileStatus.READY
        )

    def convertible_items(self) -> list[FileItem]:
        """変換対象にできる項目（ステップ3で使う）。"""
        return [item for item in self._items if item.status.is_convertible]


def _normalize(path: Path) -> Path:
    """比較用にパスを正規化する（大文字小文字違いの重複を防ぐ）。"""
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser().absolute()


def collect_audio_files(paths: Iterable[Path], max_files: int = 2000) -> list[Path]:
    """ドロップされたパスから追加候補のファイルを取り出す。

    フォルダが混ざっていた場合は再帰的に対応拡張子を拾う。
    拡張子で拾うのはあくまで候補集めで、対応可否の判定は解析後に行う。
    """
    collected: list[Path] = []
    for path in paths:
        if len(collected) >= max_files:
            break
        if path.is_dir():
            for ext in sorted(SUPPORTED_EXTENSIONS):
                for found in sorted(path.rglob(f"*{ext}")):
                    if found.is_file():
                        collected.append(found)
                    if len(collected) >= max_files:
                        break
        elif path.is_file():
            collected.append(path)
    return collected
