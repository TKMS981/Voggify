"""動画ファイル（MP4 / MKV）からの音声抽出と、音声トラックの選択。

トラックごとに違う周波数のサイン波を入れたアセットを使うので、
「狙ったトラックが出力されたか」を変換結果の音そのもので確認できる。
"""

from __future__ import annotations

import array
import subprocess

import pytest
from PySide6.QtCore import Qt

from voggify.converter import ConversionOptions, Converter, build_command
from voggify.editing import EditSettings
from voggify.errors import UnsupportedFormatError
from voggify.ffmpeg_locator import subprocess_flags
from voggify.formats import (
    SUPPORTED_EXTENSIONS,
    display_language_name,
    is_supported_extension,
    is_video_extension,
)
from voggify.models import FileStatus
from voggify.probe import AudioTrack, check_supported, probe_audio
from voggify.ui.file_list_model import COL_FORMAT
from voggify.waveform import WaveformCache, WaveformData, cache_key, extract_waveform
from tests.qt_helpers import (
    item_named,
    load_files,
    pump,
    wait_for_conversion,
)

#: generate_assets.py が multi_audio.mkv の各トラックに入れた周波数
MULTI_FREQUENCIES = (440, 880, 1320)


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------
def dominant_frequency(ffmpeg_tools, path) -> float:
    """ゼロ交差から主要周波数を出す。サイン波なので十分な精度が出る。"""
    rate = 8000
    raw = subprocess.run(
        [ffmpeg_tools.ffmpeg, "-v", "error", "-i", str(path),
         "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        capture_output=True, **subprocess_flags(),
    ).stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    # 端はフェードやエンコーダーの立ち上がりが混じるので中央だけ見る
    middle = samples[rate : rate * 3]
    assert len(middle) > rate, "解析できるだけの長さがありません"
    crossings = sum(
        1 for i in range(1, len(middle)) if (middle[i - 1] < 0) != (middle[i] < 0)
    )
    return crossings / (len(middle) / rate) / 2


# ---------------------------------------------------------------------------
# 拡張子（ffmpeg 不要）
# ---------------------------------------------------------------------------
def test_video_extensions_are_supported():
    assert {".mp4", ".mkv"} <= SUPPORTED_EXTENSIONS
    assert is_supported_extension("movie.mp4")
    assert is_supported_extension("MOVIE.MKV")
    assert is_video_extension("movie.mkv")
    assert not is_video_extension("song.mp3")


# ---------------------------------------------------------------------------
# 言語タグとトラックの表示名（ffmpeg 不要）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tag,shown",
    [
        ("jpn", "日本語"),
        ("ja", "日本語"),
        ("eng", "英語"),
        ("kor", "韓国語"),
        ("tha", "タイ語"),
        ("xyz", "XYZ"),   # 表に無いタグはそのまま大文字で出す
        ("und", None),    # 「不明」は言語として扱わない
        ("", None),
        (None, None),
    ],
)
def test_display_language_name(tag, shown):
    assert display_language_name(tag) == shown


def make_track(index=2, language=None, title=None, codec="aac", channels=2):
    return AudioTrack(
        index=index, stream_index=index + 1, codec_name=codec, channels=channels,
        sample_rate=48000, duration_sec=None, bit_rate_bps=None,
        language=language, title=title,
    )


@pytest.mark.parametrize(
    "language,title,expected",
    [
        ("jpn", "コメンタリー", "日本語 / コメンタリー"),  # 言語 + トラック名
        ("jpn", None, "日本語"),                            # 言語のみ
        (None, "Director", "Director"),                     # トラック名のみ
        (None, None, "トラック3"),                          # どちらも無ければ番号
        ("und", None, "トラック3"),                         # und も番号へ落ちる
    ],
)
def test_track_label_priority(language, title, expected):
    assert make_track(language=language, title=title).label == expected


def test_track_detail_and_describe():
    track = make_track(index=0, language="jpn")
    assert track.detail == "AAC 2ch 48.0kHz"
    assert track.describe() == "日本語（AAC 2ch 48.0kHz）"
    assert track.number == 1  # 表示は 1 始まり


# ---------------------------------------------------------------------------
# EditSettings（ffmpeg 不要）
# ---------------------------------------------------------------------------
def test_map_args_point_at_the_selected_track():
    assert EditSettings().map_args() == ["-map", "0:a:0"]
    assert EditSettings(audio_track=2).map_args() == ["-map", "0:a:2"]


def test_track_selection_counts_as_an_edit():
    assert EditSettings().is_default
    picked = EditSettings().with_track(1)
    assert not picked.is_default
    assert picked.has_track_selection
    assert picked.badge() == "♪2"          # 表示は 1 始まり
    assert "音声トラック: 2" in picked.describe(10.0)
    # トリミングと音量はトラックを変えても持ち越す
    combined = EditSettings(trim_start=1.0, volume_db=-6.0).with_track(2)
    assert combined.trim_start == 1.0 and combined.volume_db == -6.0


def test_waveform_cache_keys_differ_per_track(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"x")
    assert cache_key(source, 0) != cache_key(source, 1)

    cache = WaveformCache()
    first = WaveformData(
        duration=5.0, peaks=array.array("h", [0, 1]), sample_rate=8000, track=0
    )
    second = WaveformData(
        duration=5.0, peaks=array.array("h", [0, 2]), sample_rate=8000, track=1
    )
    cache.put(source, first)
    cache.put(source, second)

    assert cache.count == 2, "トラック違いは別データとして持つ"
    assert cache.get(source, 0) is first
    assert cache.get(source, 1) is second
    # 削除はそのファイルのぶんをまとめて落とす
    assert cache.discard(source) is True
    assert cache.count == 0


# ---------------------------------------------------------------------------
# ここから先は ffmpeg が要る
# ---------------------------------------------------------------------------
@pytest.mark.ffmpeg
def test_probe_lists_every_audio_track(workspace, ffmpeg_tools):
    (source,) = workspace.copy("multi_audio.mkv")
    info = probe_audio(source, ffmpeg_tools)

    assert info.track_count == 3
    assert info.has_multiple_tracks
    # 音声のみで 0 から連番（映像は含まれない）
    assert [t.index for t in info.tracks] == [0, 1, 2]
    # 絶対ストリーム番号は映像を挟むので 1 から
    assert [t.stream_index for t in info.tracks] == [1, 2, 3]
    assert [t.label for t in info.tracks] == ["日本語 / Main", "英語", "トラック3"]


@pytest.mark.ffmpeg
def test_probe_reads_tags_from_mp4(workspace, ffmpeg_tools):
    (source,) = workspace.copy("multi_audio.mp4")
    info = probe_audio(source, ffmpeg_tools)
    assert info.track_count == 2
    assert [t.language_name for t in info.tracks] == ["日本語", "英語"]


@pytest.mark.ffmpeg
def test_single_track_audio_file_is_unchanged(workspace, ffmpeg_tools):
    """音声ファイルは 1 本だけ。UI もこれで従来どおりになる。"""
    (source,) = workspace.copy("sample.mp3")
    info = probe_audio(source, ffmpeg_tools)
    assert info.track_count == 1
    assert not info.has_multiple_tracks
    assert info.tracks[0].index == 0


@pytest.mark.ffmpeg
def test_video_without_audio_is_rejected(workspace, ffmpeg_tools):
    (source,) = workspace.copy("silent_video.mp4")
    with pytest.raises(UnsupportedFormatError) as excinfo:
        probe_audio(source, ffmpeg_tools)
    message = excinfo.value.user_message
    assert "音声トラックが含まれていません" in message
    assert "映像トラック" in message


@pytest.mark.ffmpeg
def test_build_command_uses_the_selected_track(ffmpeg_tools, tmp_path):
    cmd = build_command(
        ffmpeg_tools, tmp_path / "in.mkv", tmp_path / "out.ogg", 5,
        edit=EditSettings(audio_track=1),
    )
    assert cmd[cmd.index("-map") + 1] == "0:a:1"
    # 音声 1 本だけを取るので映像は出力に入らない
    assert cmd.count("-map") == 1


# ---------------------------------------------------------------------------
# 変換
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("track", [0, 1, 2])
@pytest.mark.ffmpeg
def test_converts_the_selected_track(workspace, ffmpeg_tools, track):
    """選んだトラックの音が実際に出力されることを周波数で確認する。"""
    (source,) = workspace.copy("multi_audio.mkv")
    destination = workspace.subdir(f"track{track}")
    converter = Converter(ffmpeg_tools)

    result = converter.convert(
        source,
        ConversionOptions(output_dir=destination),
        edit=EditSettings(audio_track=track),
    )

    assert result.output.exists()
    measured = dominant_frequency(ffmpeg_tools, result.output)
    expected = MULTI_FREQUENCIES[track]
    assert abs(measured - expected) < expected * 0.05, (
        f"トラック {track} は {expected}Hz のはずが {measured:.0f}Hz でした"
    )


@pytest.mark.ffmpeg
def test_default_track_is_the_first_one(workspace, ffmpeg_tools):
    """トラックを指定しなければ先頭（最も若い番号）が使われる。"""
    (source,) = workspace.copy("multi_audio.mkv")
    converter = Converter(ffmpeg_tools)
    result = converter.convert(
        source, ConversionOptions(output_dir=workspace.subdir("out"))
    )
    measured = dominant_frequency(ffmpeg_tools, result.output)
    assert abs(measured - MULTI_FREQUENCIES[0]) < MULTI_FREQUENCIES[0] * 0.05


@pytest.mark.ffmpeg
def test_conversion_output_has_no_video(workspace, ffmpeg_tools):
    (source,) = workspace.copy("multi_audio.mkv")
    converter = Converter(ffmpeg_tools)
    result = converter.convert(
        source, ConversionOptions(output_dir=workspace.subdir("out"))
    )

    streams = subprocess.run(
        [ffmpeg_tools.ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(result.output)],
        capture_output=True, text=True, **subprocess_flags(),
    ).stdout.split()
    assert streams == ["audio"], "映像トラックが混ざっています"


@pytest.mark.ffmpeg
def test_vorbis_inside_video_is_still_extractable(workspace, ffmpeg_tools):
    """動画の中身が Vorbis でも「既に OGG Vorbis です」で弾かない。

    .mkv のままでは使えないので取り出す必要がある。音声ファイルの
    Vorbis は従来どおり弾く。
    """
    (movie,) = workspace.copy("multi_audio.mkv")
    check_supported(probe_audio(movie, ffmpeg_tools), track=0)  # 例外が出なければよい

    (ogg,) = workspace.copy("vorbis.ogg")
    with pytest.raises(UnsupportedFormatError):
        check_supported(probe_audio(ogg, ffmpeg_tools))


# ---------------------------------------------------------------------------
# 波形（トラックごとに別データ）
# ---------------------------------------------------------------------------
@pytest.mark.ffmpeg
def test_waveform_reads_the_selected_track(workspace, ffmpeg_tools):
    (source,) = workspace.copy("multi_audio.mkv")
    first = extract_waveform(source, ffmpeg_tools, 5.0, track=0)
    second = extract_waveform(source, ffmpeg_tools, 5.0, track=1)

    assert first.track == 0 and second.track == 1
    assert first.buckets > 0 and second.buckets > 0
    assert first.peaks != second.peaks, "トラックが違えば波形も違うはず"


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def select_row(window, row: int = 0) -> None:
    window.view.clearSelection()
    window.view.selectRow(row)
    pump()


@pytest.mark.ffmpeg
def test_multi_track_file_shows_the_selector(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("multi_audio.mkv"))
    select_row(window)

    panel = window.edit_panel
    assert panel.track_combo.isVisible(), "2 本以上あるので選択欄を出す"
    assert panel.track_combo.count() == 3
    assert panel.track_combo.itemText(0).startswith("日本語 / Main")
    assert panel.track_combo.itemText(1).startswith("英語")
    assert panel.track_combo.itemText(2).startswith("トラック3")
    assert panel.track_combo.currentIndex() == 0  # 既定は先頭


@pytest.mark.ffmpeg
def test_single_track_file_hides_the_selector(window, workspace):
    """音声ファイルでは選択欄ごと出さない（従来の見た目を保つ）。"""
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.mp3"))
    select_row(window)

    panel = window.edit_panel
    assert not panel.track_combo.isVisible()
    assert not panel.track_label.isVisible()
    assert not panel.track_hint_label.isVisible()


@pytest.mark.ffmpeg
def test_selecting_a_track_updates_the_item(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("multi_audio.mkv"))
    select_row(window)

    window.edit_panel.track_combo.setCurrentIndex(1)
    pump()

    item = item_named(window, "multi_audio.mkv")
    assert item.edit.audio_track == 1
    assert item.selected_track.label == "英語"
    # プレビューも同じトラックを向く
    assert window.edit_panel.player.audio_track == 1


@pytest.mark.ffmpeg
def test_track_change_refreshes_the_waveform(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("multi_audio.mkv"))
    select_row(window)
    window.waveform_service.wait_for_done()
    pump(0.3)

    cache = window.waveform_service.cache
    item = item_named(window, "multi_audio.mkv")
    assert cache.get(item.path, 0) is not None, "先頭トラックの波形が載っているはず"

    window.edit_panel.track_combo.setCurrentIndex(1)
    pump()
    window.waveform_service.wait_for_done()
    pump(0.3)

    # トラックごとに別データとしてキャッシュされる
    assert cache.get(item.path, 1) is not None
    assert cache.get(item.path, 0) is not cache.get(item.path, 1)


@pytest.mark.ffmpeg
def test_list_marks_files_with_several_tracks(window, workspace):
    load_files(window, workspace.copy("multi_audio.mkv", "sample.mp3"))

    multi = item_named(window, "multi_audio.mkv")
    single = item_named(window, "sample.mp3")
    assert "♪3" in multi.display_format(), "複数音声のマークを出す"
    assert "♪" not in single.display_format(), "1 本ならマークは付けない"

    row = window.model.find_row(multi.path)
    tooltip = window.model.data(
        window.model.index(row, COL_FORMAT), Qt.ItemDataRole.ToolTipRole
    )
    assert "音声トラック: 3 本" in tooltip
    assert "日本語 / Main" in tooltip


@pytest.mark.ffmpeg
def test_video_without_audio_shows_as_error(window, workspace):
    load_files(window, workspace.copy("silent_video.mp4"))
    item = item_named(window, "silent_video.mp4")
    assert item.status is FileStatus.ERROR
    assert "音声トラックが含まれていません" in item.message


@pytest.mark.ffmpeg
def test_video_converts_through_the_gui(window, workspace):
    load_files(window, workspace.copy("multi_audio.mp4"))
    window.set_edit_panel_visible(True)
    select_row(window)
    window.edit_panel.track_combo.setCurrentIndex(1)
    pump()

    window.start_conversion()
    wait_for_conversion(window)

    item = item_named(window, "multi_audio.mp4")
    assert item.status is FileStatus.DONE, item.message
    assert item.output_path is not None and item.output_path.exists()
    assert item.output_path.suffix == ".ogg"
