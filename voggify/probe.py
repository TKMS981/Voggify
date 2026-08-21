"""ffprobe を使って入力ファイルの実体を調べる。

拡張子はあくまでヒントで、対応可否の最終判定は音声ストリームの
codec_name で行う（中身が動画や未対応コーデックのものを弾くため）。
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import ProbeError, UnsupportedFormatError, describe_os_error
from .ffmpeg_locator import FFmpegTools, ensure_ffmpeg_tools, subprocess_flags
from .formats import (
    EXPECTED_CODECS_BY_EXTENSION,
    OUTPUT_CODECS,
    SUPPORTED_CODECS,
    SUPPORTED_EXTENSIONS,
    display_codec_name,
    is_supported_extension,
)


@dataclass(frozen=True)
class AudioInfo:
    """ffprobe から得た入力ファイルの情報。"""

    path: Path
    codec_name: str
    format_name: str
    duration_sec: float | None
    bit_rate_bps: int | None
    sample_rate: int | None
    channels: int
    file_size: int | None

    @property
    def display_format(self) -> str:
        """リスト表示用の「現在の形式」文字列。"""
        return display_codec_name(self.codec_name)

    @property
    def extension_mismatch(self) -> bool:
        """拡張子から期待されるコーデックと実体が食い違っているか。"""
        expected = EXPECTED_CODECS_BY_EXTENSION.get(self.path.suffix.lower())
        if expected is None:
            return False
        return self.codec_name not in expected

    @property
    def mismatch_note(self) -> str | None:
        """食い違っている場合の注記。問題なければ None。

        実体のコーデックが対応済みなら変換はできるので、弾かずに注記だけ出す。
        """
        if not self.extension_mismatch:
            return None
        return (
            f"拡張子は {self.path.suffix.lower()} ですが、"
            f"中身は {self.display_format} 音声として扱います"
        )


def _probe_raw(path: Path, tools: FFmpegTools) -> dict:
    """ffprobe を JSON 出力で実行して結果を dict で返す。"""
    argv = [
        tools.ffprobe,
        "-hide_banner",
        "-loglevel", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **subprocess_flags(),  # type: ignore[arg-type]
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"解析がタイムアウトしました: {path.name}") from exc
    except OSError as exc:
        raise ProbeError(
            "ffprobe を実行できませんでした。\n  " + describe_os_error(exc)
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else f"終了コード {result.returncode}"
        raise ProbeError(f"解析に失敗しました: {path.name}\n  {reason}")

    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe の出力を解釈できませんでした: {path.name}") from exc


def _to_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _to_int(value: object) -> int | None:
    parsed = _to_float(value)
    return int(parsed) if parsed is not None else None


def probe_audio(path: str | os.PathLike[str], tools: FFmpegTools | None = None) -> AudioInfo:
    """ファイルを解析して AudioInfo を返す。

    音声ストリームが 1 本も無い場合は UnsupportedFormatError。
    """
    tools = tools or ensure_ffmpeg_tools()
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise ProbeError(f"ファイルが見つかりません: {resolved}")

    data = _probe_raw(resolved, tools)
    streams = data.get("streams") or []
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise UnsupportedFormatError(
            f"音声ストリームが含まれていません: {resolved.name}"
        )

    stream = audio_streams[0]
    fmt = data.get("format") or {}

    duration = _to_float(stream.get("duration")) or _to_float(fmt.get("duration"))
    bit_rate = _to_int(stream.get("bit_rate")) or _to_int(fmt.get("bit_rate"))
    try:
        file_size = resolved.stat().st_size
    except OSError:
        file_size = _to_int(fmt.get("size"))

    return AudioInfo(
        path=resolved,
        codec_name=str(stream.get("codec_name") or "unknown"),
        format_name=str(fmt.get("format_name") or "unknown"),
        duration_sec=duration,
        bit_rate_bps=bit_rate,
        sample_rate=_to_int(stream.get("sample_rate")),
        channels=int(stream.get("channels") or 2),
        file_size=file_size,
    )


def check_supported(info: AudioInfo, *, strict_extension: bool = True) -> None:
    """対応フォーマットかを検査し、駄目なら UnsupportedFormatError。"""
    if strict_extension and not is_supported_extension(info.path.name):
        allowed = "、".join(sorted(e.lstrip(".").upper() for e in SUPPORTED_EXTENSIONS))
        raise UnsupportedFormatError(
            f"対応していない拡張子です: {info.path.name}\n  対応: {allowed}"
        )
    if info.codec_name in OUTPUT_CODECS:
        # Ogg コンテナは受け付けるが、中身が既に Vorbis なら変換する意味が無い。
        # 再エンコードすると音質が落ちるだけなのでここで止める。
        raise UnsupportedFormatError(
            f"既に OGG Vorbis です: {info.path.name}\n"
            "  変換する必要はありません（再エンコードすると音質が落ちます）。"
        )
    if info.codec_name not in SUPPORTED_CODECS:
        raise UnsupportedFormatError(
            f"対応していないコーデックです: {info.path.name}"
            f"（検出: {display_codec_name(info.codec_name)}）"
        )


def inspect(
    path: str | os.PathLike[str],
    tools: FFmpegTools | None = None,
    *,
    strict_extension: bool = True,
) -> AudioInfo:
    """解析と対応判定をまとめて行う。GUI からはこれを呼ぶ。"""
    info = probe_audio(path, tools)
    check_supported(info, strict_extension=strict_extension)
    return info
