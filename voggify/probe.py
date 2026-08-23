"""ffprobe を使って入力ファイルの実体を調べる。

拡張子はあくまでヒントで、対応可否の最終判定は音声ストリームの
codec_name で行う（中身が動画や未対応コーデックのものを弾くため）。

音声トラック
------------
MP4 / MKV は音声トラックを複数持てる（吹き替え・コメンタリーなど）。
ffprobe が返す音声ストリームを AudioInfo.tracks に全部並べ、どれを使うかは
EditSettings.audio_track が持つ。AudioInfo 自身の codec_name などは
従来どおり先頭トラックの値で、1 本しか無いファイルでは今までと変わらない。
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
    SUPPORTED_CODECS,
    SUPPORTED_EXTENSIONS,
    display_codec_name,
    display_language_name,
    is_supported_extension,
    is_video_extension,
)
from .output_formats import DEFAULT_OUTPUT_FORMAT, OutputFormat


@dataclass(frozen=True)
class AudioTrack:
    """音声トラック 1 本ぶんの情報。

    index は音声だけを数えた 0 始まりの通し番号で、そのまま
    ffmpeg の `-map 0:a:{index}` に渡せる。ffprobe の絶対ストリーム番号
    （映像を含めて数えたもの）は stream_index に別に持つ。
    """

    #: 音声のみで数えた番号（0 始まり）。`-map 0:a:N` の N。
    index: int
    #: ffprobe の絶対ストリーム番号（映像込み）。診断用。
    stream_index: int
    codec_name: str
    channels: int
    sample_rate: int | None
    duration_sec: float | None
    bit_rate_bps: int | None
    #: language タグ（"jpn" / "eng" など）。無ければ None。
    language: str | None = None
    #: title 相当のトラック名。無ければ None。
    title: str | None = None

    @property
    def display_format(self) -> str:
        return display_codec_name(self.codec_name)

    @property
    def language_name(self) -> str | None:
        """language タグの日本語表記。不明なら None。"""
        return display_language_name(self.language)

    @property
    def number(self) -> int:
        """画面に出す 1 始まりの番号。"""
        return self.index + 1

    @property
    def label(self) -> str:
        """トラック選択に出す表示名。

        言語タグを主に使い、トラック名があれば併記する。どちらも無ければ
        「トラック1」のような番号表記へ落とす。
        """
        language = self.language_name
        title = (self.title or "").strip() or None
        if language and title:
            return f"{language} / {title}"
        if language:
            return language
        if title:
            return title
        return f"トラック{self.number}"

    @property
    def detail(self) -> str:
        """label に添える技術情報（"AAC 2ch 48.0kHz"）。"""
        parts = [self.display_format, f"{self.channels}ch"]
        if self.sample_rate:
            parts.append(f"{self.sample_rate / 1000:.1f}kHz")
        return " ".join(parts)

    def describe(self) -> str:
        """ドロップダウンに出す 1 行。"""
        return f"{self.label}（{self.detail}）"


@dataclass(frozen=True)
class AudioInfo:
    """ffprobe から得た入力ファイルの情報。

    codec_name などの単数形のフィールドは先頭トラックの値。複数トラックの
    ファイルで「選択中のトラック」を見たいときは track() を使う。
    """

    path: Path
    codec_name: str
    format_name: str
    duration_sec: float | None
    bit_rate_bps: int | None
    sample_rate: int | None
    channels: int
    file_size: int | None
    #: 含まれる音声トラック全部（必ず 1 本以上）
    tracks: tuple[AudioTrack, ...] = ()

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def has_multiple_tracks(self) -> bool:
        """トラック選択 UI を出すかどうかの判定に使う。"""
        return len(self.tracks) > 1

    def track(self, index: int) -> AudioTrack | None:
        """音声トラック番号（0 始まり）からトラックを引く。"""
        for track in self.tracks:
            if track.index == index:
                return track
        return None

    def track_or_first(self, index: int) -> AudioTrack | None:
        """指定トラック。無ければ先頭（範囲外の選択への保険）。"""
        return self.track(index) or (self.tracks[0] if self.tracks else None)

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


def _tag(stream: dict, name: str) -> str | None:
    """ストリームのタグを大文字小文字を無視して引く。

    Matroska のタグは大文字（TITLE / LANGUAGE）で入っていることがあり、
    ffprobe はそれをそのまま返す。
    """
    tags = stream.get("tags")
    if not isinstance(tags, dict):
        return None
    wanted = name.lower()
    for key, value in tags.items():
        if str(key).lower() == wanted:
            text = str(value).strip()
            return text or None
    return None


def _build_track(index: int, stream: dict) -> AudioTrack:
    """ffprobe の音声ストリーム 1 本を AudioTrack にする。"""
    return AudioTrack(
        index=index,
        stream_index=_to_int(stream.get("index")) or index,
        codec_name=str(stream.get("codec_name") or "unknown"),
        channels=int(stream.get("channels") or 2),
        sample_rate=_to_int(stream.get("sample_rate")),
        duration_sec=_to_float(stream.get("duration")),
        bit_rate_bps=_to_int(stream.get("bit_rate")),
        language=_tag(stream, "language"),
        title=_tag(stream, "title"),
    )


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
        # 動画コンテナで映像しか入っていない場合はここに来る。
        # 拡張子は対応していても取り出せる音声が無いので「対応外」扱い。
        has_video = any(s.get("codec_type") == "video" for s in streams)
        detail = (
            "  映像トラックしか含まれていないため、取り出せる音声がありません。"
            if has_video
            else "  音声データが見つかりませんでした。"
        )
        raise UnsupportedFormatError(
            f"音声トラックが含まれていません: {resolved.name}\n{detail}"
        )

    tracks = tuple(
        _build_track(index, stream) for index, stream in enumerate(audio_streams)
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
        tracks=tracks,
    )


def check_supported(
    info: AudioInfo,
    *,
    strict_extension: bool = True,
    output_format: OutputFormat | None = None,
    track: int = 0,
) -> None:
    """対応フォーマットかを検査し、駄目なら UnsupportedFormatError。

    「既に出力形式と同じ」かどうかは変換先によって変わるので、
    output_format を見て判定する（MP3 出力なら MP3 入力を弾く）。

    複数トラックのファイルでは選んだトラックのコーデックで判定する。
    未指定なら先頭トラック（＝解析直後の既定）を見る。
    """
    target = output_format or DEFAULT_OUTPUT_FORMAT
    selected = info.track_or_first(track)
    codec = selected.codec_name if selected is not None else info.codec_name

    if strict_extension and not is_supported_extension(info.path.name):
        allowed = "、".join(sorted(e.lstrip(".").upper() for e in SUPPORTED_EXTENSIONS))
        raise UnsupportedFormatError(
            f"対応していない拡張子です: {info.path.name}\n  対応: {allowed}"
        )
    # 動画コンテナは「取り出す」のが目的なので、中の音声が出力と同じ形式でも通す。
    # 音声ファイルと違い、.mkv のまま使うという選択肢が無いため。
    if codec in target.same_as_output_codecs and not is_video_extension(info.path.name):
        # 出力と同じ形式。再エンコードしても音質が落ちるだけなので止める。
        raise UnsupportedFormatError(
            f"既に {target.label} です: {info.path.name}\n"
            "  変換する必要はありません（再エンコードすると音質が落ちます）。"
        )
    if codec not in SUPPORTED_CODECS:
        raise UnsupportedFormatError(
            f"対応していないコーデックです: {info.path.name}"
            f"（検出: {display_codec_name(codec)}）"
        )


def inspect(
    path: str | os.PathLike[str],
    tools: FFmpegTools | None = None,
    *,
    strict_extension: bool = True,
    output_format: OutputFormat | None = None,
    track: int = 0,
) -> AudioInfo:
    """解析と対応判定をまとめて行う。GUI からはこれを呼ぶ。"""
    info = probe_audio(path, tools)
    check_supported(
        info,
        strict_extension=strict_extension,
        output_format=output_format,
        track=track,
    )
    return info
