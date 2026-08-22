"""ffmpeg を subprocess で呼び出して OGG Vorbis へ変換するコア。

GUI からはワーカースレッド上で Converter.convert() を呼び、
進捗とログをコールバックで受け取る想定。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .errors import (
    ConversionCancelled,
    ConversionError,
    OutputPathError,
    describe_os_error,
)
from .editing import EditSettings
from .ffmpeg_errors import describe_failure
from .ffmpeg_locator import FFmpegTools, ensure_ffmpeg_tools, subprocess_flags
from .formats import DEFAULT_QUALITY, clamp_quality
from .output_formats import DEFAULT_OUTPUT_FORMAT, OutputFormat
from .probe import AudioInfo, check_supported, inspect

#: 進捗コールバック: 0.0〜1.0 の比率を受け取る
ProgressCallback = Callable[[float], None]
#: ログコールバック: ffmpeg の 1 行を受け取る
LogCallback = Callable[[str], None]

#: 変換途中のファイルに付ける拡張子
PARTIAL_SUFFIX = ".part"


@dataclass(frozen=True)
class ConversionOptions:
    """1 回の変換に対する設定。"""

    quality: int = DEFAULT_QUALITY
    #: None のときは入力ファイルと同じフォルダに出力する
    output_dir: Path | None = None
    #: True なら同名ファイルを上書き、False なら「名前 (1).ogg」のように退避
    overwrite: bool = False
    #: 変換先の形式
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT

    def normalized(self) -> "ConversionOptions":
        return ConversionOptions(
            quality=clamp_quality(self.quality),
            output_dir=Path(self.output_dir).expanduser() if self.output_dir else None,
            overwrite=self.overwrite,
            output_format=self.output_format,
        )


@dataclass
class ConversionResult:
    """変換 1 件の結果。"""

    source: Path
    output: Path
    quality: int
    duration_sec: float | None
    output_size: int | None
    elapsed_sec: float
    log: str = field(repr=False, default="")


def resolve_output_path(source: Path, options: ConversionOptions) -> Path:
    """出力先パスを決める（衝突回避まで含む）。"""
    options = options.normalized()
    directory = options.output_dir or source.parent
    extension = options.output_format.extension
    candidate = directory / (source.stem + extension)

    if options.overwrite:
        return candidate

    index = 1
    while candidate.exists():
        candidate = directory / f"{source.stem} ({index}){extension}"
        index += 1
    return candidate


def ensure_writable_dir(directory: Path) -> None:
    """出力先フォルダを用意し、書き込めるか確認する。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputPathError(
            f"出力先フォルダを作成できません: {directory}\n  {describe_os_error(exc)}"
        ) from exc

    if not os.access(directory, os.W_OK):
        raise OutputPathError(f"出力先フォルダに書き込み権限がありません: {directory}")

    # os.access は Windows では当てにならないので実際に書いてみる
    probe_file = directory / f".voggify_write_test_{os.getpid()}"
    try:
        probe_file.touch()
    except OSError as exc:
        raise OutputPathError(
            f"出力先フォルダに書き込めません: {directory}\n  {describe_os_error(exc)}"
        ) from exc
    finally:
        try:
            probe_file.unlink()
        except OSError:
            pass


def build_command(
    tools: FFmpegTools,
    source: Path,
    destination: Path,
    quality: int,
    output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT,
    edit: EditSettings | None = None,
    source_duration: float | None = None,
) -> list[str]:
    """ffmpeg のコマンドライン引数を組み立てる。

    destination は途中ファイル（.part）になりうるので、拡張子に依存しないよう
    `-f` でコンテナを明示する。品質はエンコーダーごとに尺度が違うため、
    OutputFormat 側で変換してもらう。

    トリミングの `-ss` / `-t` は `-i` の前（入力側）に置く。精度は出力側と
    同じで、離れた位置を切り出すときに速いため（editing.py の説明を参照）。
    編集が既定値なら余計な引数は一切足さない。
    """
    edit = edit or EditSettings()
    return [
        tools.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel", "info",
        "-y",
        *edit.input_args(source_duration),  # トリミング（入力側シーク）
        "-i", str(source),
        "-map", "0:a:0",       # 先頭の音声ストリームのみ（カバーアート等は除外）
        "-map_metadata", "0",  # タグを引き継ぐ
        *edit.filter_args(),   # 音量調整
        *output_format.encoder_args(quality),
        "-f", output_format.container,
        "-progress", "pipe:1",
        "-stats_period", "0.2",  # 既定の 0.5 秒だと進捗バーの動きが粗い
        "-nostats",
        str(destination),
    ]


class Converter:
    """ffmpeg プロセスを 1 本ずつ実行する変換器。

    インスタンスは 1 スレッドから使う前提。cancel() だけは別スレッド
    （UI スレッド）から呼ばれてよい。
    """

    def __init__(self, tools: FFmpegTools | None = None) -> None:
        self._tools = tools
        self._process: subprocess.Popen[str] | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    @property
    def tools(self) -> FFmpegTools:
        if self._tools is None:
            self._tools = ensure_ffmpeg_tools()
        return self._tools

    def cancel(self) -> None:
        """実行中の変換を中断する（UI スレッドから呼ぶ）。"""
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def reset_cancel(self) -> None:
        """キャンセル状態をクリアする（キューの再開時などに使う）。"""
        self._cancelled.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def convert(
        self,
        source: str | os.PathLike[str],
        options: ConversionOptions | None = None,
        *,
        info: AudioInfo | None = None,
        edit: EditSettings | None = None,
        on_progress: ProgressCallback | None = None,
        on_log: LogCallback | None = None,
    ) -> ConversionResult:
        """1 ファイルを OGG Vorbis に変換する。

        失敗時は ConversionError、中断時は ConversionCancelled を送出する。
        """
        options = (options or ConversionOptions()).normalized()
        edit = edit or EditSettings()
        src = Path(source).expanduser()

        if self._cancelled.is_set():
            raise ConversionCancelled()

        # 実体を確認して対応フォーマットか判定（未指定なら都度 probe する）
        if info is None:
            info = inspect(src, self.tools, output_format=options.output_format)
        else:
            # 呼び出し側で解析済みでも、出力形式との組み合わせは見直す
            check_supported(info, output_format=options.output_format)

        destination = resolve_output_path(src, options)
        ensure_writable_dir(destination.parent)
        if destination.exists() and destination.samefile(src):
            raise OutputPathError(f"入力と出力が同じファイルになります: {destination}")

        partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
        argv = build_command(
            self.tools,
            src,
            partial,
            options.quality,
            options.output_format,
            edit,
            info.duration_sec,
        )

        if on_log:
            on_log("$ " + " ".join(_quote(arg) for arg in argv))

        started = time.monotonic()
        log_lines: list[str] = []
        returncode = self._run(
            argv,
            # ffmpeg の out_time はトリミング後の相対時間なので、
            # 進捗の分母も切り出し後の長さにする（実測で確認済み）
            total_duration=edit.effective_duration(info.duration_sec),
            on_progress=on_progress,
            on_log=on_log,
            log_sink=log_lines,
        )
        elapsed = time.monotonic() - started
        log_text = "\n".join(log_lines)

        if self._cancelled.is_set():
            _remove_quietly(partial)
            raise ConversionCancelled(f"変換を中断しました: {src.name}")

        if returncode != 0:
            _remove_quietly(partial)
            raise ConversionError(
                describe_failure(src.name, log_lines, returncode),
                returncode=returncode,
                log=log_text,
            )

        if not partial.exists():
            raise ConversionError(
                f"{src.name}: 出力ファイルが生成されませんでした。"
                "入力に音声データが含まれていない可能性があります。",
                returncode=returncode,
                log=log_text,
            )

        try:
            os.replace(partial, destination)
        except OSError as exc:
            _remove_quietly(partial)
            raise OutputPathError(
                f"出力ファイルを保存できませんでした: {destination}\n  {describe_os_error(exc)}"
            ) from exc

        if on_progress:
            on_progress(1.0)

        try:
            output_size: int | None = destination.stat().st_size
        except OSError:
            output_size = None

        return ConversionResult(
            source=src,
            output=destination,
            quality=options.quality,
            duration_sec=edit.effective_duration(info.duration_sec),
            output_size=output_size,
            elapsed_sec=elapsed,
            log=log_text,
        )

    # ------------------------------------------------------------------
    # 内部処理
    # ------------------------------------------------------------------
    def _run(
        self,
        argv: list[str],
        *,
        total_duration: float | None,
        on_progress: ProgressCallback | None,
        on_log: LogCallback | None,
        log_sink: list[str],
    ) -> int:
        """ffmpeg を起動し、stdout=進捗 / stderr=ログ を別スレッドで読む。"""
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **subprocess_flags(),  # type: ignore[arg-type]
            )
        except OSError as exc:
            raise ConversionError(
                "ffmpeg を起動できませんでした。\n  " + describe_os_error(exc)
            ) from exc

        with self._lock:
            self._process = process

        # cancel() が起動処理と競合した場合の取りこぼし対策
        if self._cancelled.is_set():
            try:
                process.terminate()
            except OSError:
                pass

        readers = [
            threading.Thread(
                target=self._read_progress,
                args=(process.stdout, total_duration, on_progress),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_log,
                args=(process.stderr, on_log, log_sink),
                daemon=True,
            ),
        ]
        for thread in readers:
            thread.start()

        try:
            returncode = process.wait()
        finally:
            for thread in readers:
                thread.join(timeout=5.0)
            with self._lock:
                self._process = None
        return returncode

    def _read_progress(
        self,
        stream: Iterable[str] | None,
        total_duration: float | None,
        on_progress: ProgressCallback | None,
    ) -> None:
        """`-progress pipe:1` の key=value 出力を読んで進捗を通知する。"""
        if stream is None:
            return
        last_ratio = -1.0
        for raw in stream:
            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key in ("out_time_us", "out_time_ms"):
                # ffmpeg は out_time_ms も実際にはマイクロ秒で出力する
                if not total_duration or on_progress is None:
                    continue
                try:
                    micros = float(value)
                except ValueError:
                    continue
                ratio = max(0.0, min(0.999, micros / 1_000_000 / total_duration))
                if ratio - last_ratio >= 0.005:
                    last_ratio = ratio
                    on_progress(ratio)
            elif key == "progress" and value == "end":
                if on_progress and not self._cancelled.is_set():
                    on_progress(1.0)

    def _read_log(
        self,
        stream: Iterable[str] | None,
        on_log: LogCallback | None,
        log_sink: list[str],
    ) -> None:
        """ffmpeg の stderr を 1 行ずつ回収する。"""
        if stream is None:
            return
        for raw in stream:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            log_sink.append(line)
            if on_log:
                on_log(line)


def _quote(arg: str) -> str:
    return '"' + arg + '"' if " " in arg else arg


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


__all__ = [
    "ConversionOptions",
    "ConversionResult",
    "Converter",
    "build_command",
    "ensure_writable_dir",
    "resolve_output_path",
]
