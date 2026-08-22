"""出力形式の切り替え（OGG Vorbis / MP3）の GUI テスト。"""

from __future__ import annotations

import subprocess

import pytest
from PySide6.QtCore import Qt

from voggify.config import AppConfig, config_from_dict, load_config
from voggify.ffmpeg_locator import subprocess_flags
from voggify.models import FileStatus
from voggify.output_formats import MP3, OGG_VORBIS
from voggify.ui.file_list_model import COL_SIZE
from tests.qt_helpers import (
    item_named,
    load_files,
    pump,
    wait_for_conversion,
    wait_for_probes,
)

pytestmark = pytest.mark.ffmpeg


def size_cell(window, row: int = 0) -> str:
    return window.model.data(
        window.model.index(row, COL_SIZE), Qt.ItemDataRole.DisplayRole
    )


def codec_of(ffmpeg_tools, path) -> str:
    result = subprocess.run(
        [
            ffmpeg_tools.ffprobe, "-v", "error",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        **subprocess_flags(),
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 選択 UI
# ---------------------------------------------------------------------------
def test_defaults_to_ogg(window):
    assert window.settings.output_format() is OGG_VORBIS
    assert window.settings.format_buttons["ogg"].isChecked()
    assert window.current_options().output_format is OGG_VORBIS


def test_switching_format_updates_options(window):
    window.settings.set_output_format(MP3)
    pump()

    assert window.settings.output_format() is MP3
    assert window.settings.format_buttons["mp3"].isChecked()
    assert not window.settings.format_buttons["ogg"].isChecked()
    assert window.current_options().output_format is MP3
    assert window.model.output_format() is MP3


def test_quality_value_is_kept_when_switching(window):
    """スライダーの数値は据え置き。意味だけ形式に合わせて出し直す。"""
    window.settings.quality_slider.setValue(8)
    pump()
    window.settings.set_output_format(MP3)
    pump()

    assert window.settings.quality() == 8, "数値は変えない"
    hint = window.settings.quality_hint_label.text()
    assert "libmp3lame" in hint
    assert f"-q:a {MP3.encoder_quality(8)}" in hint


def test_hint_shows_the_encoder_specific_value(window):
    window.settings.quality_slider.setValue(6)
    pump()
    assert "libvorbis -q:a 6" in window.settings.quality_hint_label.text()

    window.settings.set_output_format(MP3)
    pump()
    assert "libmp3lame -q:a 3" in window.settings.quality_hint_label.text()


# ---------------------------------------------------------------------------
# 予測サイズ
# ---------------------------------------------------------------------------
def test_size_estimate_follows_the_format(window, workspace):
    # MP3 出力でもエラーにならない入力を使う（MP3 入力は「変換不要」で弾かれる）
    load_files(window, workspace.copy("sample.wav"))
    window.settings.quality_slider.setValue(10)
    pump()
    ogg_text = size_cell(window)

    window.settings.set_output_format(MP3)
    wait_for_probes(window)
    pump()
    mp3_text = size_cell(window)

    item = item_named(window, "sample.wav")
    assert item.status is FileStatus.READY, "エラーではなく予測が出る状態であること"
    assert ogg_text.startswith("約 ") and mp3_text.startswith("約 ")
    assert ogg_text != mp3_text, "形式で予測が変わっていない"
    assert item.estimated_size(10, MP3) < item.estimated_size(10, OGG_VORBIS)


# ---------------------------------------------------------------------------
# 形式に応じた対応判定のやり直し
# ---------------------------------------------------------------------------
def test_mp3_input_becomes_an_error_when_targeting_mp3(window, workspace):
    """OGG 出力では変換できた MP3 が、MP3 出力に切り替えるとエラーになる。"""
    load_files(window, workspace.copy("sample.mp3"))
    assert item_named(window, "sample.mp3").status is FileStatus.READY

    window.settings.set_output_format(MP3)
    wait_for_probes(window)
    pump()

    item = item_named(window, "sample.mp3")
    assert item.status is FileStatus.ERROR
    assert "既に MP3 です" in item.message


def test_vorbis_input_becomes_convertible_when_targeting_mp3(window, workspace):
    """逆に、OGG 出力では弾かれた Vorbis が MP3 出力なら変換できる。"""
    load_files(window, workspace.copy("vorbis.ogg"))
    assert item_named(window, "vorbis.ogg").status is FileStatus.ERROR

    window.settings.set_output_format(MP3)
    wait_for_probes(window)
    pump()

    assert item_named(window, "vorbis.ogg").status is FileStatus.READY


# ---------------------------------------------------------------------------
# 実際の変換
# ---------------------------------------------------------------------------
def test_converts_to_mp3(window, workspace, ffmpeg_tools):
    load_files(window, workspace.copy("sample.flac", "sample.wav"))
    window.settings.set_output_format(MP3)
    wait_for_probes(window)
    pump()

    window.start_conversion()
    wait_for_conversion(window)

    assert all(i.status is FileStatus.DONE for i in window.model.items)
    assert len(workspace.outputs("*.mp3")) == 2
    assert not workspace.outputs("*.ogg"), "OGG は作られない"

    output = item_named(window, "sample.flac").output_path
    assert output.suffix == ".mp3"
    assert codec_of(ffmpeg_tools, output) == "mp3"


def test_higher_quality_makes_a_bigger_mp3(window, workspace):
    (source,) = workspace.copy("sample.flac")
    sizes = {}
    for quality in (2, 9):
        load_files(window, [source])
        window.settings.set_output_format(MP3)
        window.settings.set_output_dir(workspace.subdir(f"q{quality}"))
        window.settings.quality_slider.setValue(quality)
        wait_for_probes(window)
        pump()
        window.start_conversion()
        wait_for_conversion(window)
        sizes[quality] = item_named(window, "sample.flac").output_size

    assert sizes[9] > sizes[2], f"品質を上げてもサイズが増えない: {sizes}"


def test_conversion_log_records_the_format(window, workspace):
    window.set_log_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    window.settings.set_output_format(MP3)
    wait_for_probes(window)
    pump()
    window.start_conversion()
    wait_for_conversion(window)

    text = window.log_panel.to_text()
    assert "MP3" in text
    assert "libmp3lame" in text


# ---------------------------------------------------------------------------
# 設定の保存
# ---------------------------------------------------------------------------
def test_format_is_saved_and_restored(window, isolated_config):
    window.settings.set_output_format(MP3)
    pump()
    assert window.current_config().output_format == "mp3"

    window.close()
    pump()

    loaded = load_config()
    assert loaded.config.output_format == "mp3"
    assert loaded.warnings == []


def test_unknown_saved_format_falls_back():
    """設定に知らない形式が入っていても既定に落ちる。"""
    config, warnings = config_from_dict({"config_version": 1, "output_format": "flac"})
    assert config.output_format == "ogg"
    assert any("output_format" in w for w in warnings)


def test_missing_format_key_uses_the_default():
    """MP3 対応より前に保存された設定にはこのキーが無い。"""
    config, warnings = config_from_dict({"config_version": 1, "quality": 5})
    assert config.output_format == "ogg"
    assert not any("output_format" in w for w in warnings), "警告は出さない"


def test_restoring_mp3_config_applies_to_ui(qapp, ffmpeg_tools):
    from voggify.ui.main_window import MainWindow

    window = MainWindow(ffmpeg_tools, AppConfig(output_format="mp3", quality=3))
    try:
        assert window.settings.output_format() is MP3
        assert window.model.output_format() is MP3
        assert window.current_options().output_format is MP3
        assert window.settings.quality() == 3
    finally:
        window.probe_service.discard_pending()
        window.deleteLater()
        qapp.processEvents()
