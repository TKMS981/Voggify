"""ファイルリストが持つデータ構造。

GUI（Qt）に依存させないことで、ロジック単体でテストできるようにしている。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .editing import EditSettings
from .formats import estimate_output_size, format_estimated_size
from .output_formats import DEFAULT_OUTPUT_FORMAT, DEFAULT_QUALITY, OutputFormat
from .probe import AudioInfo

#: 1 項目あたりに保持する ffmpeg ログの上限行数
MAX_LOG_LINES_PER_ITEM = 500


class FileStatus(Enum):
    """リスト項目の状態。

    ANALYZING / READY / ERROR はステップ2（解析）で、
    それ以降はステップ3（変換）で使う。
    """

    ANALYZING = "解析中"
    READY = "待機中"
    ERROR = "エラー"
    QUEUED = "変換待ち"
    CONVERTING = "変換中"
    DONE = "完了"
    FAILED = "失敗"
    CANCELLED = "中断"

    @property
    def is_error(self) -> bool:
        return self in (FileStatus.ERROR, FileStatus.FAILED)

    @property
    def is_convertible(self) -> bool:
        """変換キューに入れられる状態か。"""
        return self in (FileStatus.READY, FileStatus.CANCELLED, FileStatus.FAILED)

    @property
    def is_busy(self) -> bool:
        """処理中で、リストから外すべきでない状態か。"""
        return self in (FileStatus.ANALYZING, FileStatus.QUEUED, FileStatus.CONVERTING)


@dataclass
class FileItem:
    """リストの 1 行。解析前は path 以外ほぼ空。"""

    path: Path
    status: FileStatus = FileStatus.ANALYZING
    info: AudioInfo | None = None
    #: エラー時のユーザー向けメッセージ
    message: str = ""
    #: 拡張子と実体が食い違う場合の注記
    note: str | None = None
    #: 簡易編集（トリミング・音量）。既定値なら ffmpeg に何も足さない。
    edit: EditSettings = field(default_factory=EditSettings)
    #: 変換の進捗 0.0〜1.0
    progress: float = 0.0
    #: 変換後の出力先
    output_path: Path | None = None
    #: 変換後の実サイズ（完了後に予測を置き換える）
    output_size: int | None = None
    #: 変換にかかった秒数
    elapsed_sec: float | None = None
    #: この項目に関するログ行（ステップ5のログパネルで使う）
    log_lines: list[str] = field(default_factory=list)

    def append_log(self, line: str) -> None:
        """ffmpeg のログを溜める。際限なく増えないよう上限を設ける。"""
        self.log_lines.append(line)
        if len(self.log_lines) > MAX_LOG_LINES_PER_ITEM:
            del self.log_lines[: len(self.log_lines) - MAX_LOG_LINES_PER_ITEM]

    def reset_for_conversion(self) -> None:
        """変換キューに積む前に前回の結果を消す。"""
        self.progress = 0.0
        self.output_path = None
        self.output_size = None
        self.elapsed_sec = None
        self.message = ""
        self.log_lines.clear()

    # ------------------------------------------------------------------
    # 表示用のプロパティ
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.path.name

    @property
    def source_duration(self) -> float | None:
        """元のファイルの長さ（秒）。解析前は None。"""
        return self.info.duration_sec if self.info is not None else None

    @property
    def output_duration(self) -> float | None:
        """変換後の長さ（秒）。トリミングを反映する。"""
        return self.edit.effective_duration(self.source_duration)

    def display_format(self) -> str:
        """「現在の形式」列の文字列。食い違いがあれば併記する。"""
        if self.status is FileStatus.ANALYZING:
            return "解析中…"
        if self.info is None:
            return "-"
        if self.note:
            # 例: MP3 (.mp3) -> 実体 AAC のとき
            return f"{self.info.display_format} ⚠"
        return self.info.display_format

    def estimated_size(
        self,
        quality: int = DEFAULT_QUALITY,
        output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    ) -> int | None:
        """指定品質・形式での変換後サイズ（バイト）。解析前・エラー時は None。"""
        if self.info is None or self.status is FileStatus.ERROR:
            return None
        return estimate_output_size(
            self.output_duration, quality, self.info.channels, output_format
        )

    def display_size(
        self,
        quality: int = DEFAULT_QUALITY,
        output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    ) -> str:
        """「変換後のサイズ」列の文字列。

        変換前は「約 4.1 MB」の予測、変換後は実サイズに差し替える。
        """
        if self.status is FileStatus.ANALYZING:
            return "…"
        if self.status is FileStatus.DONE and self.output_size is not None:
            from .formats import format_bytes

            return format_bytes(self.output_size)
        return format_estimated_size(self.estimated_size(quality, output_format))

    def tooltip(
        self,
        quality: int = DEFAULT_QUALITY,
        output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    ) -> str:
        """行のツールチップ。詳細情報はここにまとめる。"""
        from .formats import format_bytes, format_duration

        lines = [str(self.path)]
        if self.status is FileStatus.ERROR and self.message:
            lines.append("")
            lines.append(self.message)
            return "\n".join(lines)

        if self.info is not None:
            lines.append("")
            lines.append(f"形式: {self.info.display_format} ({self.info.format_name})")
            if self.edit.has_trim:
                lines.append(
                    f"再生時間: {format_duration(self.info.duration_sec)}"
                    f" → {format_duration(self.output_duration)}（切り出し後）"
                )
            else:
                lines.append(f"再生時間: {format_duration(self.info.duration_sec)}")
            lines.append(
                f"サンプルレート: {self.info.sample_rate or '-'} Hz / "
                f"{self.info.channels} ch"
            )
            lines.append(f"現在のサイズ: {format_bytes(self.info.file_size)}")
            if self.status is FileStatus.DONE:
                lines.append(f"変換後のサイズ: {format_bytes(self.output_size)}")
            else:
                lines.append(
                    "変換後の予測: "
                    f"{format_estimated_size(self.estimated_size(quality, output_format))}"
                    f"（{output_format.label} / 品質 {quality}）"
                )
        description = self.edit.describe(self.source_duration)
        if description:
            lines.append("")
            lines.append("【編集】")
            lines.extend(description.splitlines())
        if self.note:
            lines.append("")
            lines.append(f"⚠ {self.note}")
        if self.status is FileStatus.DONE and self.output_path is not None:
            lines.append("")
            lines.append(f"出力先: {self.output_path}")
            if self.elapsed_sec is not None:
                lines.append(f"所要時間: {self.elapsed_sec:.1f} 秒")
            if self.info is not None and self.info.file_size and self.output_size:
                ratio = self.output_size / self.info.file_size * 100
                lines.append(f"元のサイズ比: {ratio:.0f}%")
        if self.message and self.status is not FileStatus.ERROR:
            lines.append("")
            lines.append(self.message)
        return "\n".join(lines)


def make_error_item(path: Path, message: str) -> FileItem:
    """解析に失敗した項目を作る。リストからは消さず、エラー表示で残す。"""
    return FileItem(path=path, status=FileStatus.ERROR, message=message)
