"""編集パラメータ（トリミング・音量）のテスト。ffmpeg 不要な部分。"""

from __future__ import annotations

import pytest

from voggify.converter import build_command
from voggify.editing import (
    DEFAULT_VOLUME_DB,
    MAX_VOLUME_DB,
    MIN_VOLUME_DB,
    EditSettings,
    EditValueError,
    clamp_volume,
    format_timecode,
    parse_timecode,
    validate_trim,
)
from voggify.ffmpeg_locator import FFmpegTools
from voggify.models import FileItem
from voggify.output_formats import MP3, OGG_VORBIS
from pathlib import Path

FAKE_TOOLS = FFmpegTools(
    ffmpeg="ffmpeg.exe",
    ffprobe="ffprobe.exe",
    version="test",
    encoders=frozenset({"libvorbis", "libmp3lame"}),
)


# ---------------------------------------------------------------------------
# 既定値
# ---------------------------------------------------------------------------
def test_defaults_are_no_op():
    edit = EditSettings()
    assert edit.trim_start == 0.0
    assert edit.trim_end is None
    assert edit.volume_db == DEFAULT_VOLUME_DB
    assert edit.is_default
    assert not edit.has_trim
    assert not edit.has_volume
    assert edit.input_args(100.0) == []
    assert edit.filter_args() == []
    assert edit.badge() == ""
    assert edit.describe(100.0) == ""


def test_default_edit_does_not_change_the_command():
    """編集なしなら既存のコマンドと 1 文字も変わらないこと。"""
    without = build_command(FAKE_TOOLS, Path("in.mp3"), Path("o.part"), 6)
    with_default = build_command(
        FAKE_TOOLS, Path("in.mp3"), Path("o.part"), 6,
        edit=EditSettings(), source_duration=100.0,
    )
    assert without == with_default
    assert "-ss" not in with_default
    assert "-t" not in with_default
    assert "-af" not in with_default


# ---------------------------------------------------------------------------
# 長さ
# ---------------------------------------------------------------------------
def test_effective_duration_without_trim():
    assert EditSettings().effective_duration(120.0) == 120.0
    assert EditSettings().effective_duration(None) is None


def test_effective_duration_with_trim():
    edit = EditSettings(trim_start=10.0, trim_end=70.0)
    assert edit.effective_duration(120.0) == 60.0


def test_effective_duration_start_only():
    edit = EditSettings(trim_start=90.0)
    assert edit.effective_duration(120.0) == 30.0


def test_effective_end_is_clamped_to_the_source():
    edit = EditSettings(trim_end=500.0)
    assert edit.effective_end(120.0) == 120.0
    assert edit.effective_duration(120.0) == 120.0


# ---------------------------------------------------------------------------
# ffmpeg 引数
# ---------------------------------------------------------------------------
def test_trim_uses_input_side_seek():
    """-ss は -i の前に置く（精度は同等で、離れた位置ほど速いため）。"""
    edit = EditSettings(trim_start=10.5, trim_end=70.25)
    cmd = build_command(
        FAKE_TOOLS, Path("in.mp3"), Path("o.part"), 6,
        edit=edit, source_duration=100.0,
    )
    assert cmd.index("-ss") < cmd.index("-i"), "-ss が入力側にない"
    assert cmd[cmd.index("-ss") + 1] == "10.500"
    assert cmd[cmd.index("-t") + 1] == "59.750"
    assert cmd.index("-t") < cmd.index("-i")


def test_start_only_omits_duration():
    edit = EditSettings(trim_start=30.0)
    assert edit.input_args(120.0) == ["-ss", "30.000"]


def test_end_equal_to_source_is_not_a_trim():
    """終了位置が全長と同じなら切り出しではない。"""
    edit = EditSettings().with_trim(0.0, 120.0, 120.0)
    assert edit.trim_end is None
    assert not edit.has_trim
    assert edit.input_args(120.0) == []


def test_volume_filter():
    assert EditSettings(volume_db=-6.0).filter_args() == ["-af", "volume=-6.00dB"]
    assert EditSettings(volume_db=3.5).filter_args() == ["-af", "volume=3.50dB"]


def test_tiny_volume_change_is_ignored():
    """浮動小数の誤差でフィルタが付かないようにする。"""
    assert not EditSettings(volume_db=0.01).has_volume
    assert EditSettings(volume_db=0.01).filter_args() == []


@pytest.mark.parametrize("fmt", [OGG_VORBIS, MP3])
def test_edit_args_come_before_the_encoder(fmt):
    """-af はエンコーダー指定より前（フィルタ→エンコードの順）。"""
    cmd = build_command(
        FAKE_TOOLS, Path("in.wav"), Path("o.part"), 6, fmt,
        EditSettings(trim_start=1.0, trim_end=5.0, volume_db=-3.0), 10.0,
    )
    assert cmd.index("-af") < cmd.index("-c:a")
    assert cmd[cmd.index("-c:a") + 1] == fmt.encoder


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------
def test_start_after_end_is_rejected():
    with pytest.raises(EditValueError, match="開始位置より後"):
        validate_trim(10.0, 5.0, 100.0)


def test_start_equal_to_end_is_rejected():
    with pytest.raises(EditValueError, match="開始位置より後"):
        validate_trim(10.0, 10.0, 100.0)


def test_too_short_range_is_rejected():
    with pytest.raises(EditValueError, match="短すぎます"):
        validate_trim(10.0, 10.05, 100.0)


def test_negative_start_is_rejected():
    with pytest.raises(EditValueError, match="負の値"):
        validate_trim(-1.0, 10.0, 100.0)


def test_start_beyond_the_file_is_rejected():
    with pytest.raises(EditValueError, match="長さ"):
        validate_trim(150.0, None, 100.0)


def test_end_beyond_the_file_is_rejected():
    with pytest.raises(EditValueError, match="長さ"):
        validate_trim(0.0, 150.0, 100.0)


def test_unknown_duration_skips_the_range_check():
    validate_trim(10.0, 20.0, None)  # 例外が出なければよい


@pytest.mark.parametrize(
    "given,expected",
    [(-99, MIN_VOLUME_DB), (MIN_VOLUME_DB, MIN_VOLUME_DB), (0, 0.0),
     (6.25, 6.25), (MAX_VOLUME_DB, MAX_VOLUME_DB), (99, MAX_VOLUME_DB)],
)
def test_volume_is_clamped(given, expected):
    assert clamp_volume(given) == expected


# ---------------------------------------------------------------------------
# 時間の表記
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "seconds,text",
    [
        (0, "0:00.000"),
        (5.5, "0:05.500"),
        (65.25, "1:05.250"),
        (3725.125, "1:02:05.125"),
    ],
)
def test_format_timecode(seconds, text):
    assert format_timecode(seconds) == text


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("0:00.000", 0.0),
        ("1:05.250", 65.25),
        ("1:02:05.125", 3725.125),
        ("83.5", 83.5),
        ("  2:30  ", 150.0),
    ],
)
def test_parse_timecode(text, seconds):
    assert parse_timecode(text) == pytest.approx(seconds)


def test_timecode_round_trip():
    for value in (0.0, 1.234, 59.999, 60.0, 599.5, 3661.001):
        assert parse_timecode(format_timecode(value)) == pytest.approx(value, abs=0.001)


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "--", "1:75.0"])
def test_bad_timecode_is_rejected(bad):
    with pytest.raises(EditValueError):
        parse_timecode(bad)


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------
def test_badge_shows_what_is_set():
    assert EditSettings().badge() == ""
    assert EditSettings(trim_start=1.0).badge() == "✂"
    assert EditSettings(volume_db=-6.0).badge() == "-6.0dB"
    both = EditSettings(trim_start=1.0, volume_db=3.0).badge()
    assert "✂" in both and "+3.0dB" in both


def test_describe_mentions_both():
    text = EditSettings(trim_start=10.0, trim_end=70.0, volume_db=-6.0).describe(120.0)
    assert "切り出し" in text and "0:10.000" in text and "1:10.000" in text
    assert "音量" in text and "-6.0" in text


# ---------------------------------------------------------------------------
# FileItem との連携
# ---------------------------------------------------------------------------
def make_item(duration: float, **edit_kwargs) -> FileItem:
    from voggify.models import FileStatus
    from voggify.probe import AudioInfo

    return FileItem(
        path=Path("a.wav"),
        status=FileStatus.READY,
        info=AudioInfo(
            path=Path("a.wav"), codec_name="pcm_s16le", format_name="wav",
            duration_sec=duration, bit_rate_bps=None, sample_rate=44100,
            channels=2, file_size=1000,
        ),
        edit=EditSettings(**edit_kwargs),
    )


def test_item_output_duration_reflects_trim():
    assert make_item(120.0).output_duration == 120.0
    assert make_item(120.0, trim_start=10.0, trim_end=40.0).output_duration == 30.0


def test_item_size_estimate_shrinks_with_trim():
    full = make_item(120.0).estimated_size(6, OGG_VORBIS)
    trimmed = make_item(120.0, trim_start=0.0, trim_end=60.0).estimated_size(6, OGG_VORBIS)
    assert trimmed == pytest.approx(full / 2, rel=0.02)


def test_item_tooltip_shows_the_edit():
    tooltip = make_item(120.0, trim_start=10.0, trim_end=40.0, volume_db=-6.0).tooltip()
    assert "【編集】" in tooltip
    assert "切り出し" in tooltip
    assert "音量" in tooltip
    assert "切り出し後" in tooltip


def test_item_without_edit_has_no_edit_section():
    assert "【編集】" not in make_item(120.0).tooltip()
