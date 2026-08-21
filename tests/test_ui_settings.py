"""品質スライダーと出力先設定のテスト。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from voggify.formats import DEFAULT_QUALITY, MAX_QUALITY, MIN_QUALITY
from voggify.models import FileStatus
from voggify.ui.file_list_model import COL_SIZE
from tests.qt_helpers import item_named, load_files, pump, wait_for_conversion, write_denied

pytestmark = pytest.mark.ffmpeg


def size_cell(window, row: int = 0) -> str:
    return window.model.data(window.model.index(row, COL_SIZE), Qt.ItemDataRole.DisplayRole)


# ---------------------------------------------------------------------------
# 品質スライダー
# ---------------------------------------------------------------------------
def test_slider_defaults(window):
    panel = window.settings
    assert panel.quality() == DEFAULT_QUALITY
    assert panel.quality_slider.minimum() == MIN_QUALITY
    assert panel.quality_slider.maximum() == MAX_QUALITY
    assert window.model.quality() == panel.quality()
    assert panel.quality_value_label.text() == f"品質: {DEFAULT_QUALITY}"
    assert "192 kbps" in panel.quality_hint_label.text()


@pytest.mark.parametrize("quality", [0, 2, 6, 9, 10])
def test_slider_updates_label_and_model(window, quality):
    window.settings.quality_slider.setValue(quality)
    pump()
    assert window.model.quality() == quality
    assert window.settings.quality_value_label.text() == f"品質: {quality}"


def test_slider_updates_estimates_live(window, workspace):
    load_files(window, workspace.copy("long.mp3"))
    item = item_named(window, "long.mp3")

    shown, estimated = [], []
    for quality in (0, 2, 6, 9, 10):
        window.settings.quality_slider.setValue(quality)
        pump()
        shown.append(size_cell(window))
        estimated.append(item.estimated_size(quality))

    assert all(text.startswith("約 ") for text in shown)
    assert len(set(shown)) == 5, f"品質ごとに変わっていない: {shown}"
    assert estimated == sorted(estimated)


def test_slider_updates_summary_total(window, workspace):
    load_files(window, workspace.copy("long.mp3"))
    window.settings.quality_slider.setValue(2)
    pump()
    low = window.summary_label.text()

    window.settings.quality_slider.setValue(10)
    pump()
    high = window.summary_label.text()

    assert "約" in low and "約" in high
    assert low != high


# ---------------------------------------------------------------------------
# 出力先
# ---------------------------------------------------------------------------
def test_output_defaults_to_input_folder(window):
    panel = window.settings
    assert panel.same_folder_radio.isChecked()
    assert panel.output_dir() is None
    assert not panel.path_edit.isEnabled()
    assert not panel.browse_button.isEnabled()
    assert window.current_options().output_dir is None


def test_choosing_a_folder_enables_the_path_field(window, workspace):
    target = workspace.subdir("out")
    window.settings.set_output_dir(target)
    pump()

    panel = window.settings
    assert panel.custom_folder_radio.isChecked()
    assert panel.output_dir() == target
    assert panel.path_edit.text() == str(target)
    assert panel.path_edit.isEnabled() and panel.browse_button.isEnabled()
    assert panel.is_valid()
    assert not panel.error_label.isVisible()
    assert window.current_options().output_dir == target


def test_unwritable_folder_is_rejected_on_selection(window, workspace):
    """変換開始まで待たず、選んだ時点でエラーにする。"""
    with write_denied(workspace.path / "denied") as denied:
        window.settings.set_output_dir(denied)
        pump()

        assert not window.settings.is_valid()
        assert window.settings.error_label.isVisible()
        assert "書き込" in window.settings.error_message()
        assert "書き込" in window.statusBar().currentMessage()
        assert not window.run_button.isEnabled()
        assert window.start_conversion() is False


def test_switching_back_to_same_folder_clears_the_error(window, workspace):
    with write_denied(workspace.path / "denied") as denied:
        window.settings.set_output_dir(denied)
        pump()
        assert not window.settings.is_valid()

        window.settings.same_folder_radio.setChecked(True)
        pump()

    assert window.settings.is_valid()
    assert not window.settings.error_label.isVisible()
    assert window.settings.output_dir() is None


def test_error_message_hides_internal_probe_file(window, workspace):
    with write_denied(workspace.path / "denied") as denied:
        window.settings.set_output_dir(denied)
        pump()
        assert ".voggify_write_test" not in window.settings.error_message()


# ---------------------------------------------------------------------------
# 実際の変換への反映
# ---------------------------------------------------------------------------
def test_settings_are_applied_to_the_conversion(window, workspace):
    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    target = workspace.subdir("out")
    window.settings.set_output_dir(target)
    window.settings.quality_slider.setValue(2)
    pump()

    options = window.current_options()
    assert options.quality == 2
    assert options.output_dir == target

    window.start_conversion()
    wait_for_conversion(window)

    assert all(i.status is FileStatus.DONE for i in window.model.items)
    assert len(sorted(target.glob("*.ogg"))) == 2
    assert not workspace.outputs(), "入力フォルダには出力しない"


def test_quality_changes_the_produced_file_size(window, workspace):
    (source,) = workspace.copy("sample.flac")

    sizes = {}
    for quality in (2, 9):
        load_files(window, [source])
        window.settings.set_output_dir(workspace.subdir(f"q{quality}"))
        window.settings.quality_slider.setValue(quality)
        pump()
        window.start_conversion()
        wait_for_conversion(window)
        sizes[quality] = item_named(window, "sample.flac").output_size

    assert sizes[9] > sizes[2], f"品質を上げてもサイズが増えない: {sizes}"


def test_missing_output_dir_is_created(window, workspace):
    load_files(window, workspace.copy("sample.mp3"))
    target = workspace.path / "does" / "not" / "exist"
    window.settings.set_output_dir(target)
    pump()

    window.start_conversion()
    wait_for_conversion(window)

    assert target.is_dir()
    assert len(sorted(target.glob("*.ogg"))) == 1


# ---------------------------------------------------------------------------
# 変換中のロック
# ---------------------------------------------------------------------------
def test_settings_are_locked_during_conversion(window, workspace):
    load_files(window, workspace.copy("long.mp3"))
    window.settings.quality_slider.setValue(6)
    pump()
    assert window.settings.isEnabled()

    window.start_conversion()
    assert not window.settings.isEnabled()
    assert not window.settings.quality_slider.isEnabled()
    assert not window.settings.browse_button.isEnabled()
    assert window.current_options().quality == 6, "開始時点の値で確定する"

    window.cancel_conversion()
    wait_for_conversion(window, timeout=20)

    assert window.settings.isEnabled()
    assert window.settings.quality_slider.isEnabled()
