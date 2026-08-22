"""ffmpeg を必要としないコアロジックのテスト。

フォーマット定義、サイズ予測、コマンド組み立て、出力パスの解決、
エラーメッセージの日本語化まで。
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

from voggify.converter import (
    ConversionOptions,
    build_command,
    ensure_writable_dir,
    resolve_output_path,
)
from voggify.errors import OutputPathError, UnsupportedFormatError, describe_os_error
from voggify.ffmpeg_errors import describe_failure, explain, find_error_line
from voggify.ffmpeg_locator import FFmpegTools
from voggify.formats import (
    clamp_quality,
    display_codec_name,
    estimate_output_size,
    format_bytes,
    format_duration,
    format_estimated_size,
    is_supported_extension,
)
from voggify.output_formats import (
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_QUALITY,
    MAX_QUALITY,
    MIN_QUALITY,
    MP3,
    OGG_VORBIS,
    OUTPUT_FORMATS,
    output_format_by_key,
)
from voggify.probe import AudioInfo, check_supported

FAKE_TOOLS = FFmpegTools(
    ffmpeg="ffmpeg.exe",
    ffprobe="ffprobe.exe",
    version="test",
    encoders=frozenset({"libvorbis", "libmp3lame"}),
)


def audio_info(name: str, codec: str, **overrides) -> AudioInfo:
    defaults = dict(
        path=Path(name),
        codec_name=codec,
        format_name="test",
        duration_sec=10.0,
        bit_rate_bps=128_000,
        sample_rate=44_100,
        channels=2,
        file_size=100_000,
    )
    defaults.update(overrides)
    return AudioInfo(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# formats
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "given,expected", [(-3, 0), (0, 0), (6, 6), (10, 10), (99, 10)]
)
def test_clamp_quality(given, expected):
    assert clamp_quality(given) == expected


@pytest.mark.parametrize(
    "name,supported",
    [
        ("A.FLAC", True),
        ("song.mp3", True),
        ("song.m4a", True),
        # Ogg は拡張子では通し、中身のコーデックで受け入れ可否を決める
        ("song.ogg", True),
        ("song.oga", True),
        ("song.opus", False),
        ("song.txt", False),
    ],
)
def test_is_supported_extension(name, supported):
    assert is_supported_extension(name) is supported


@pytest.mark.parametrize(
    "codec,shown",
    [
        ("mp3float", "MP3"),
        ("pcm_s16le", "PCM 16bit"),
        ("pcm_s8", "PCM"),          # 表に無い PCM 系
        ("adpcm_yamaha", "ADPCM"),  # 表に無い ADPCM 系
        ("alac", "ALAC"),
        ("ac3", "AC3"),             # 未知はそのまま大文字化
    ],
)
def test_display_codec_name(codec, shown):
    assert display_codec_name(codec) == shown


def test_estimate_output_size_matches_nominal_bitrate():
    # 3 分 / q6 / ステレオ -> 192kbps 相当
    estimated = estimate_output_size(180.0, 6, 2)
    assert 4_200_000 < estimated < 4_400_000


def test_estimate_output_size_grows_with_quality():
    sizes = [estimate_output_size(60.0, q, 2) for q in range(11)]
    assert sizes == sorted(sizes)
    assert len(set(sizes)) == len(sizes)


def test_estimate_output_size_mono_is_smaller():
    assert estimate_output_size(60.0, 6, 1) < estimate_output_size(60.0, 6, 2)


def test_estimate_output_size_without_duration():
    assert estimate_output_size(None, 6, 2) is None
    assert estimate_output_size(0, 6, 2) is None


def test_format_helpers():
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(None) == "-"
    assert format_estimated_size(1536) == "約 1.5 KB"
    assert format_estimated_size(None) == "-"
    assert format_duration(65) == "1:05"
    assert format_duration(3725) == "1:02:05"
    assert format_duration(None) == "-"


# ---------------------------------------------------------------------------
# コマンド組み立て
# ---------------------------------------------------------------------------
def test_build_command_uses_libvorbis_and_forces_ogg():
    cmd = build_command(FAKE_TOOLS, Path("in.mp3"), Path("out.ogg.part"), 6)
    assert "libvorbis" in cmd
    assert cmd[cmd.index("-q:a") + 1] == "6"
    # .part は拡張子からコンテナを推測できないので明示が要る
    assert cmd[cmd.index("-f") + 1] == "ogg"
    assert cmd[cmd.index("-progress") + 1] == "pipe:1"
    assert cmd[cmd.index("-map") + 1] == "0:a:0"


def test_build_command_clamps_quality():
    cmd = build_command(FAKE_TOOLS, Path("in.mp3"), Path("out"), 42)
    assert cmd[cmd.index("-q:a") + 1] == "10"


# ---------------------------------------------------------------------------
# 出力パス
# ---------------------------------------------------------------------------
def test_output_defaults_to_input_folder(tmp_path):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"x")
    assert resolve_output_path(source, ConversionOptions()) == tmp_path / "song.ogg"


def test_output_avoids_collision(tmp_path):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"x")
    (tmp_path / "song.ogg").write_bytes(b"x")
    assert resolve_output_path(source, ConversionOptions()) == tmp_path / "song (1).ogg"

    (tmp_path / "song (1).ogg").write_bytes(b"x")
    assert resolve_output_path(source, ConversionOptions()) == tmp_path / "song (2).ogg"


def test_output_overwrite_keeps_name(tmp_path):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"x")
    (tmp_path / "song.ogg").write_bytes(b"x")
    options = ConversionOptions(overwrite=True)
    assert resolve_output_path(source, options) == tmp_path / "song.ogg"


def test_output_honours_custom_dir(tmp_path):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"x")
    target = tmp_path / "sub" / "deep"
    options = ConversionOptions(output_dir=target)
    assert resolve_output_path(source, options) == target / "song.ogg"


def test_ensure_writable_dir_creates_and_cleans_up(tmp_path):
    target = tmp_path / "a" / "b"
    ensure_writable_dir(target)
    assert target.is_dir()
    # 書き込み確認用の一時ファイルを残さない
    assert not list(target.iterdir())


def test_ensure_writable_dir_rejects_file(tmp_path):
    path = tmp_path / "not_a_dir.txt"
    path.write_bytes(b"x")
    with pytest.raises(OutputPathError) as excinfo:
        ensure_writable_dir(path)
    assert "出力先フォルダを作成できません" in excinfo.value.user_message


# ---------------------------------------------------------------------------
# 対応判定
# ---------------------------------------------------------------------------
def test_check_supported_accepts_known_codec():
    check_supported(audio_info("a.mp3", "mp3"))  # 例外が出なければよい


def test_check_supported_rejects_unknown_codec():
    with pytest.raises(UnsupportedFormatError) as excinfo:
        check_supported(audio_info("a.m4a", "ac3"))
    assert "対応していないコーデック" in excinfo.value.user_message
    assert "AC3" in excinfo.value.user_message


def test_check_supported_rejects_unknown_extension():
    with pytest.raises(UnsupportedFormatError):
        check_supported(audio_info("a.opus", "opus"))


def test_mismatch_note_only_when_codec_differs():
    assert audio_info("a.mp3", "mp3").mismatch_note is None
    note = audio_info("a.mp3", "aac").mismatch_note
    assert note is not None
    assert ".mp3" in note and "AAC" in note


def test_m4a_accepts_both_aac_and_alac():
    assert audio_info("a.m4a", "aac").mismatch_note is None
    assert audio_info("a.m4a", "alac").mismatch_note is None


# ---------------------------------------------------------------------------
# Ogg コンテナの入力
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("extension", [".ogg", ".oga"])
@pytest.mark.parametrize("codec", ["opus", "flac", "speex"])
def test_ogg_with_non_vorbis_codec_is_accepted(extension, codec):
    """Vorbis 以外が入った Ogg は変換対象にする。"""
    check_supported(audio_info(f"a{extension}", codec))


@pytest.mark.parametrize("extension", [".ogg", ".oga"])
def test_ogg_with_vorbis_is_rejected_as_already_converted(extension):
    """中身が既に Vorbis なら再エンコードしても劣化するだけなので弾く。"""
    with pytest.raises(UnsupportedFormatError) as excinfo:
        check_supported(audio_info(f"a{extension}", "vorbis"))
    message = excinfo.value.user_message
    assert "既に OGG Vorbis です" in message
    assert "音質" in message


def test_ogg_with_unsupported_codec_is_rejected():
    """Ogg でも中身が対応外なら弾く（Theora 映像など）。"""
    with pytest.raises(UnsupportedFormatError) as excinfo:
        check_supported(audio_info("a.ogg", "theora"))
    assert "対応していないコーデック" in excinfo.value.user_message


@pytest.mark.parametrize("codec", ["opus", "vorbis", "flac", "speex"])
def test_ogg_container_codecs_are_not_flagged_as_mismatch(codec):
    """Ogg コンテナにこれらが入っているのは普通なので注記しない。"""
    assert audio_info("a.ogg", codec).mismatch_note is None


def test_ogg_extension_is_supported():
    assert is_supported_extension("song.ogg")
    assert is_supported_extension("song.OGG")
    assert is_supported_extension("song.oga")


def test_opus_extension_is_still_rejected():
    """.opus は今回の対象外（要望は「ogg ファイル」だったため）。"""
    assert not is_supported_extension("song.opus")


def test_display_names_for_ogg_codecs():
    assert display_codec_name("opus") == "Opus"
    assert display_codec_name("vorbis") == "Vorbis"
    assert display_codec_name("speex") == "Speex"


def test_ogg_input_avoids_overwriting_itself(tmp_path):
    """入力も出力も .ogg なので、同じフォルダでは名前がぶつかる。"""
    source = tmp_path / "podcast.ogg"
    source.write_bytes(b"x")
    destination = resolve_output_path(source, ConversionOptions())
    assert destination == tmp_path / "podcast (1).ogg"
    assert destination != source


def test_ogg_input_keeps_its_name_in_another_folder(tmp_path):
    source = tmp_path / "podcast.ogg"
    source.write_bytes(b"x")
    target = tmp_path / "out"
    destination = resolve_output_path(source, ConversionOptions(output_dir=target))
    assert destination == target / "podcast.ogg"


# ---------------------------------------------------------------------------
# エラーメッセージの日本語化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "log_lines,fragment",
    [
        (["av_interleaved_write_frame(): No space left on device"], "ディスク容量"),
        (["[in#0] Error opening input: Permission denied"], "アクセスが拒否"),
        (["[in#0/mp3] Invalid data found when processing input"], "壊れている"),
        (["Unknown encoder 'libvorbis'"], "libvorbis"),
        (["No such file or directory"], "見つかりません"),
        (["[in#0/mov] moov atom not found"], "構造が壊れて"),
        (["Output file #0 does not contain any stream"], "取り出せません"),
        (["Error: Read-only file system"], "読み取り専用"),
    ],
)
def test_explain_translates_common_ffmpeg_errors(log_lines, fragment):
    assert fragment in explain(log_lines)


def test_explain_returns_empty_for_unknown():
    assert explain(["something totally unexpected"]) == ""


def test_find_error_line_skips_boilerplate():
    lines = [
        "av_interleaved_write_frame(): No space left on device",
        "Conversion failed!",
    ]
    assert find_error_line(lines) == lines[0]


def test_describe_failure_has_name_reason_and_raw_line():
    message = describe_failure("song.mp3", ["No space left on device"], 1)
    assert "song.mp3" in message
    assert "ディスク容量" in message
    assert "ffmpeg: No space left on device" in message


def test_describe_failure_without_log():
    message = describe_failure("song.mp3", [], 69)
    assert "song.mp3" in message
    assert "69" in message


@pytest.mark.parametrize(
    "code,fragment",
    [
        (errno.ENOSPC, "空き容量"),
        (errno.EACCES, "アクセスが拒否"),
        (errno.EROFS, "読み取り専用"),
        (errno.ENOENT, "見つかりません"),
    ],
)
def test_describe_os_error(code, fragment):
    assert fragment in describe_os_error(OSError(code, os.strerror(code)))


def test_describe_os_error_hides_internal_paths(tmp_path):
    """内部の一時ファイル名をユーザー向けメッセージに出さない。"""
    exc = OSError(errno.EACCES, "Access is denied", str(tmp_path / ".voggify_write_test_1"))
    assert ".voggify_write_test" not in describe_os_error(exc)


# ---------------------------------------------------------------------------
# windowed ビルドでの標準出力
# ---------------------------------------------------------------------------
def test_ensure_streams_leaves_working_streams_alone():
    from voggify import console

    before_out, before_err = sys.stdout, sys.stderr
    assert console.ensure_streams() is True
    assert sys.stdout is before_out
    assert sys.stderr is before_err


def test_ensure_streams_installs_a_sink_when_there_is_no_console(monkeypatch):
    """--noconsole でエクスプローラーから起動された状況を再現する。"""
    from voggify import console

    monkeypatch.setattr(console, "_attach_parent_console", lambda: None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    attached = console.ensure_streams()

    assert attached is False
    assert sys.stdout is not None and sys.stderr is not None
    # print() が例外にならないことが目的
    print("この出力は捨てられる")
    sys.stderr.write("これも捨てられる\n")
    sys.stdout.close()


def test_ensure_streams_falls_back_when_console_cannot_be_opened(monkeypatch):
    """コンソールに接続できても CONOUT$ を開けない場合。"""
    from voggify import console

    monkeypatch.setattr(console, "_attach_parent_console", lambda: 932)
    monkeypatch.setattr(console, "_open_console_stream", lambda codepage: None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    assert console.ensure_streams() is False
    print("落ちない")
    sys.stdout.close()


# ---------------------------------------------------------------------------
# 出力形式（OGG Vorbis / MP3）
# ---------------------------------------------------------------------------
def test_output_formats_are_registered():
    assert [f.key for f in OUTPUT_FORMATS] == ["ogg", "mp3"]
    assert DEFAULT_OUTPUT_FORMAT is OGG_VORBIS


@pytest.mark.parametrize("fmt", OUTPUT_FORMATS)
def test_quality_is_monotonic_for_every_format(fmt):
    """共通スライダーは全形式で「大きいほど高音質」でなければならない。"""
    sizes = [fmt.nominal_bitrate_bps(q) for q in range(MIN_QUALITY, MAX_QUALITY + 1)]
    assert sizes == sorted(sizes), f"{fmt.label} でビットレートが逆転している: {sizes}"


def test_vorbis_quality_maps_straight_through():
    for q in range(11):
        assert OGG_VORBIS.encoder_quality(q) == q


def test_mp3_quality_is_inverted():
    """LAME は小さいほど高音質なので、共通尺度とは逆向きに写す。"""
    assert MP3.encoder_quality(MIN_QUALITY) == 9   # 最低音質 -> V9
    assert MP3.encoder_quality(MAX_QUALITY) == 0   # 最高音質 -> V0
    mapped = [MP3.encoder_quality(q) for q in range(11)]
    assert mapped == sorted(mapped, reverse=True), f"単調減少でない: {mapped}"


def test_mp3_tops_out_at_v0():
    """MP3 の VBR は上限があるので、最上位の 2 段は同じ設定になる。"""
    assert MP3.encoder_quality(9) == MP3.encoder_quality(10) == 0


@pytest.mark.parametrize("fmt", OUTPUT_FORMATS)
def test_encoder_args_use_the_right_encoder(fmt):
    args = fmt.encoder_args(DEFAULT_QUALITY)
    assert args[:2] == ["-c:a", fmt.encoder]
    assert args[2] == "-q:a"
    assert args[3] == str(fmt.encoder_quality(DEFAULT_QUALITY))


@pytest.mark.parametrize("fmt", OUTPUT_FORMATS)
def test_build_command_follows_the_format(fmt):
    cmd = build_command(FAKE_TOOLS, Path("in.wav"), Path("out.part"), 6, fmt)
    assert fmt.encoder in cmd
    assert cmd[cmd.index("-f") + 1] == fmt.container
    assert cmd[cmd.index("-q:a") + 1] == str(fmt.encoder_quality(6))


@pytest.mark.parametrize("fmt", OUTPUT_FORMATS)
def test_output_extension_follows_the_format(tmp_path, fmt):
    source = tmp_path / "song.wav"
    source.write_bytes(b"x")
    destination = resolve_output_path(source, ConversionOptions(output_format=fmt))
    assert destination == tmp_path / f"song{fmt.extension}"


def test_already_in_target_format_is_rejected_per_format():
    """「変換不要」の判定は出力形式ごとに変わる。"""
    mp3_input = audio_info("a.mp3", "mp3")
    vorbis_input = audio_info("a.ogg", "vorbis")

    # MP3 出力なら MP3 入力を弾き、Vorbis 入力は通す
    with pytest.raises(UnsupportedFormatError, match="既に MP3 です"):
        check_supported(mp3_input, output_format=MP3)
    check_supported(vorbis_input, output_format=MP3)

    # OGG 出力ならその逆
    with pytest.raises(UnsupportedFormatError, match="既に OGG Vorbis です"):
        check_supported(vorbis_input, output_format=OGG_VORBIS)
    check_supported(mp3_input, output_format=OGG_VORBIS)


def test_size_estimate_differs_by_format():
    ogg = OGG_VORBIS.estimate_size(60.0, 10, 2)
    mp3 = MP3.estimate_size(60.0, 10, 2)
    assert ogg > mp3, "MP3 の VBR は Vorbis の最高品質より上限が低い"


def test_quality_hint_shows_the_encoder_value():
    assert "libvorbis -q:a 6" in OGG_VORBIS.quality_hint(6)
    assert "libmp3lame -q:a 3" in MP3.quality_hint(6)


def test_output_format_by_key():
    assert output_format_by_key("ogg") is OGG_VORBIS
    assert output_format_by_key("mp3") is MP3
    assert output_format_by_key("flac") is None
    assert output_format_by_key(None) is None
