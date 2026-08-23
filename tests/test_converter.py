"""実際に ffmpeg を動かす変換コアのテスト（GUI なし）。"""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

from voggify.converter import ConversionOptions, Converter
from voggify.errors import (
    ConversionCancelled,
    OutputPathError,
    ProbeError,
    UnsupportedFormatError,
)
from voggify.ffmpeg_locator import subprocess_flags
from voggify.probe import probe_audio
from tests.qt_helpers import write_denied

pytestmark = pytest.mark.ffmpeg


@pytest.fixture
def converter(ffmpeg_tools) -> Converter:
    return Converter(ffmpeg_tools)


def ffprobe_value(tools, path, entries: str) -> str:
    """ffprobe で出力ファイルの中身を確かめる。"""
    result = subprocess.run(
        [
            tools.ffprobe, "-v", "error",
            "-show_entries", entries,
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_flags(),  # type: ignore[arg-type]
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name", ["sample.mp3", "sample.wav", "sample.flac", "sample.m4a"]
)
def test_converts_each_input_format(converter, workspace, ffmpeg_tools, name):
    (source,) = workspace.copy(name)
    result = converter.convert(source)

    assert result.output.exists()
    assert result.output.suffix == ".ogg"
    assert result.output_size > 0
    assert ffprobe_value(ffmpeg_tools, result.output, "stream=codec_name") == "vorbis"


def test_output_lands_next_to_input(converter, workspace):
    (source,) = workspace.copy("sample.mp3")
    result = converter.convert(source)
    assert result.output.parent == workspace.path


def test_output_can_go_to_another_folder(converter, workspace):
    (source,) = workspace.copy("sample.mp3")
    target = workspace.path / "out" / "deep"
    result = converter.convert(source, ConversionOptions(output_dir=target))
    assert result.output.parent == target
    assert not workspace.outputs()


def test_metadata_is_carried_over(converter, workspace, ffmpeg_tools):
    (source,) = workspace.copy("sample.mp3")
    result = converter.convert(source)
    # Ogg では Vorbis comment としてストリーム側に載る
    tags = ffprobe_value(ffmpeg_tools, result.output, "stream_tags=title,artist")
    assert "TestTone" in tags
    assert "Voggify" in tags


def test_duration_is_preserved(converter, workspace, ffmpeg_tools):
    (source,) = workspace.copy("sample.flac")
    info = probe_audio(source, ffmpeg_tools)
    result = converter.convert(source)
    converted = float(ffprobe_value(ffmpeg_tools, result.output, "format=duration"))
    assert converted == pytest.approx(info.duration_sec, abs=0.2)


def test_higher_quality_produces_bigger_file(converter, workspace):
    (source,) = workspace.copy("sample.flac")
    low = converter.convert(source, ConversionOptions(quality=1, output_dir=workspace.subdir("q1")))
    high = converter.convert(source, ConversionOptions(quality=9, output_dir=workspace.subdir("q9")))
    assert high.output_size > low.output_size


def test_no_partial_files_left_behind(converter, workspace):
    (source,) = workspace.copy("sample.mp3")
    converter.convert(source)
    assert not list(workspace.path.rglob("*.part"))


def test_collision_is_avoided(converter, workspace):
    (source,) = workspace.copy("sample.mp3")
    first = converter.convert(source)
    second = converter.convert(source)
    assert first.output.name == "sample.ogg"
    assert second.output.name == "sample (1).ogg"


def test_overwrite_reuses_name(converter, workspace):
    (source,) = workspace.copy("sample.mp3")
    first = converter.convert(source)
    second = converter.convert(source, ConversionOptions(overwrite=True))
    assert first.output == second.output


@pytest.mark.parametrize("name", ["opus.ogg", "flac.oga"])
def test_converts_ogg_containers_holding_other_codecs(
    converter, workspace, ffmpeg_tools, name
):
    """Vorbis 以外が入った Ogg / Oga を OGG Vorbis にする。"""
    (source,) = workspace.copy(name)
    result = converter.convert(source)

    assert result.output.exists()
    assert ffprobe_value(ffmpeg_tools, result.output, "stream=codec_name") == "vorbis"
    assert result.output != source


def test_ogg_output_does_not_clobber_the_ogg_input(converter, workspace, ffmpeg_tools):
    """入力も .ogg なので、同じフォルダなら別名になり入力は残る。"""
    (source,) = workspace.copy("opus.ogg")
    original = source.read_bytes()

    result = converter.convert(source)

    assert result.output.name == "opus (1).ogg"
    assert source.read_bytes() == original, "入力が壊されていない"
    assert ffprobe_value(ffmpeg_tools, source, "stream=codec_name") == "opus"


def test_overwrite_refuses_to_destroy_an_ogg_input(converter, workspace):
    """--overwrite でも入力と出力が同じファイルになるなら止める。"""
    (source,) = workspace.copy("opus.ogg")
    original = source.read_bytes()

    with pytest.raises(OutputPathError) as excinfo:
        converter.convert(source, ConversionOptions(overwrite=True))

    assert "入力と出力が同じファイル" in excinfo.value.user_message
    assert source.read_bytes() == original


def test_already_vorbis_ogg_is_rejected(converter, workspace):
    (source,) = workspace.copy("vorbis.ogg")
    with pytest.raises(UnsupportedFormatError) as excinfo:
        converter.convert(source)
    assert "既に OGG Vorbis です" in excinfo.value.user_message
    assert not workspace.outputs("*(1).ogg")


def test_extension_codec_mismatch_still_converts(converter, workspace, ffmpeg_tools):
    """.mp3 だが中身は AAC(mp4)。実体で判定して通す方針。"""
    (source,) = workspace.copy("fake.mp3")
    info = probe_audio(source, ffmpeg_tools)
    assert info.codec_name == "aac"
    assert info.mismatch_note is not None

    result = converter.convert(source)
    assert result.output.exists()
    # 映像は落として音声だけにする
    assert ffprobe_value(ffmpeg_tools, result.output, "stream=codec_type") == "audio"


# ---------------------------------------------------------------------------
# 異常系
# ---------------------------------------------------------------------------
def test_rejects_unsupported_extension(converter, workspace):
    (source,) = workspace.copy("notsupported.opus")
    with pytest.raises(UnsupportedFormatError) as excinfo:
        converter.convert(source)
    assert "対応していない拡張子" in excinfo.value.user_message


def test_rejects_missing_file(converter, workspace):
    with pytest.raises(ProbeError) as excinfo:
        converter.convert(workspace.path / "no_such_file.mp3")
    assert "ファイルが見つかりません" in excinfo.value.user_message


def test_rejects_broken_file(converter, workspace):
    (source,) = workspace.copy("broken.mp3")
    with pytest.raises((ProbeError, UnsupportedFormatError)):
        converter.convert(source)


def test_rejects_unwritable_output_dir(converter, workspace):
    (source,) = workspace.copy("sample.mp3")
    with write_denied(workspace.path / "denied") as denied:
        with pytest.raises(OutputPathError) as excinfo:
            converter.convert(source, ConversionOptions(output_dir=denied))
    message = excinfo.value.user_message
    # 文言は 2 通りある。POSIX は os.access で弾いて「書き込み権限がありません」、
    # Windows は os.access が当てにならないので実際に書いてみて「書き込めません」
    assert "出力先フォルダに書き込" in message
    # 内部の書き込みテスト用ファイル名を漏らさない
    assert ".voggify_write_test" not in message


# ---------------------------------------------------------------------------
# 進捗・ログ・キャンセル
# ---------------------------------------------------------------------------
def test_reports_progress(converter, workspace):
    (source,) = workspace.copy("long.mp3")
    seen: list[float] = []
    converter.convert(source, on_progress=seen.append)

    assert seen, "進捗が 1 度も通知されなかった"
    assert seen == sorted(seen), f"進捗が単調増加していない: {seen}"
    assert seen[-1] == 1.0
    assert any(0.0 < ratio < 1.0 for ratio in seen), f"途中経過が無い: {seen}"


def test_captures_ffmpeg_log(converter, workspace):
    (source,) = workspace.copy("sample.mp3")
    lines: list[str] = []
    result = converter.convert(source, on_log=lines.append)

    assert lines[0].startswith("$ "), "1 行目に実行コマンドが載る"
    assert "libvorbis" in lines[0]
    assert len(lines) > 3
    assert "libvorbis" in result.log or "Vorbis" in result.log


def test_cancel_stops_quickly_and_leaves_nothing(converter, workspace):
    (source,) = workspace.copy("long.mp3")
    outcome: dict[str, object] = {}
    progress: list[float] = []

    def worker() -> None:
        try:
            converter.convert(source, on_progress=progress.append)
        except ConversionCancelled as exc:
            outcome["cancelled"] = exc.user_message
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        else:
            outcome["completed"] = True

    thread = threading.Thread(target=worker)
    thread.start()
    # 進捗が動き出してから止める
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not progress:
        time.sleep(0.01)

    started = time.monotonic()
    converter.cancel()
    thread.join(timeout=15)
    elapsed = time.monotonic() - started

    assert not thread.is_alive()
    assert "cancelled" in outcome, f"中断されなかった: {outcome}"
    assert elapsed < 5.0, f"停止に {elapsed:.1f} 秒かかった"
    assert not workspace.outputs()
    assert not list(workspace.path.rglob("*.part"))


def test_converter_is_reusable_after_cancel(converter, workspace):
    (long_source, short_source) = workspace.copy("long.mp3", "sample.wav")

    def worker() -> None:
        try:
            converter.convert(long_source)
        except ConversionCancelled:
            pass

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.3)
    converter.cancel()
    thread.join(timeout=15)

    converter.reset_cancel()
    result = converter.convert(short_source)
    assert result.output.exists()
