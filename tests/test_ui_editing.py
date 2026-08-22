"""編集パネルの GUI テスト（トリミング・音量）。"""

from __future__ import annotations

import subprocess

import pytest

from voggify.config import AppConfig, load_config
from voggify.editing import EditSettings, format_timecode
from voggify.ffmpeg_locator import subprocess_flags
from voggify.models import FileStatus
from voggify.ui.file_list_model import COL_EDIT, COL_SIZE
from tests.qt_helpers import (
    item_named,
    load_files,
    pump,
    wait_for_conversion,
    wait_for_probes,
)
from PySide6.QtCore import Qt

pytestmark = pytest.mark.ffmpeg


def cell(window, row, column) -> str:
    return window.model.data(window.model.index(row, column), Qt.ItemDataRole.DisplayRole)


def duration_of(ffmpeg_tools, path) -> float:
    result = subprocess.run(
        [ffmpeg_tools.ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, **subprocess_flags(),
    )
    return float(result.stdout.strip())


def select_only(window, row: int) -> None:
    window.view.clearSelection()
    window.view.selectRow(row)
    pump()


# ---------------------------------------------------------------------------
# パネルの開閉と有効/無効
# ---------------------------------------------------------------------------
def test_panel_is_hidden_by_default(window):
    assert not window.edit_panel.isVisible()
    assert not window.edit_button.isChecked()


def test_toggle_opens_the_panel(window):
    window.edit_button.setChecked(True)
    pump()
    assert window.edit_panel.isVisible()

    window.set_edit_panel_visible(False)
    pump()
    assert not window.edit_panel.isVisible()
    assert not window.edit_button.isChecked()


def test_panel_is_disabled_without_a_selection(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    window.view.clearSelection()
    pump()
    assert not window.edit_panel.isEnabled()


def test_panel_is_disabled_with_multiple_selection(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac", "sample.wav"))

    selection = window.view.selectionModel()
    flags = selection.SelectionFlag.Select | selection.SelectionFlag.Rows
    selection.select(window.model.index(0, 0), flags)
    selection.select(window.model.index(1, 0), flags)
    pump()

    assert len(window.view.selected_rows()) == 2
    assert not window.edit_panel.isEnabled()
    assert "1 つだけ" in window.edit_panel.target_label.text()


def test_panel_enables_for_a_single_selection(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    assert window.edit_panel.isEnabled()
    assert "sample.flac" in window.edit_panel.target_label.text()
    item = item_named(window, "sample.flac")
    assert format_timecode(item.source_duration) in window.edit_panel.target_label.text()


def test_error_rows_cannot_be_edited(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("notsupported.opus"))
    select_only(window, 0)
    assert not window.edit_panel.isEnabled()


# ---------------------------------------------------------------------------
# トリミングの入力
# ---------------------------------------------------------------------------
def test_setting_a_trim_updates_the_item(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    window.edit_panel.start_edit.setText("0:01.000")
    window.edit_panel.end_edit.setText("0:03.000")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()

    item = item_named(window, "sample.flac")
    assert item.edit.trim_start == pytest.approx(1.0)
    assert item.edit.trim_end == pytest.approx(3.0)
    assert item.output_duration == pytest.approx(2.0)


def test_invalid_range_is_rejected_and_reverted(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    # 開始 > 終了
    window.edit_panel.start_edit.setText("0:04.000")
    window.edit_panel.end_edit.setText("0:02.000")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()

    assert window.edit_panel.error_label.isVisible()
    assert "開始位置より後" in window.edit_panel.error_message()
    # 値は据え置き（不正な状態を持たない）
    assert item_named(window, "sample.flac").edit.is_default


def test_malformed_timecode_is_rejected(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    window.edit_panel.start_edit.setText("あいうえお")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()

    assert window.edit_panel.error_label.isVisible()
    assert item_named(window, "sample.flac").edit.is_default


def test_trim_beyond_the_file_is_rejected(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    window.edit_panel.end_edit.setText("9:99.000")  # 不正な書式
    window.edit_panel.end_edit.editingFinished.emit()
    pump()
    assert window.edit_panel.error_label.isVisible()

    window.edit_panel.end_edit.setText("5:00.000")  # ファイルより長い
    window.edit_panel.end_edit.editingFinished.emit()
    pump()
    assert window.edit_panel.error_label.isVisible()
    assert item_named(window, "sample.flac").edit.is_default


def test_reset_trim_button(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    window.edit_panel.start_edit.setText("0:01.000")
    window.edit_panel.end_edit.setText("0:03.000")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()
    assert item_named(window, "sample.flac").edit.has_trim

    window.edit_panel.reset_trim_button.click()
    pump()
    item = item_named(window, "sample.flac")
    assert not item.edit.has_trim
    assert item.output_duration == pytest.approx(item.source_duration)


# ---------------------------------------------------------------------------
# 音量
# ---------------------------------------------------------------------------
def test_volume_slider_updates_the_item(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    window.edit_panel.volume_slider.setValue(-60)  # -6.0 dB
    pump()

    assert item_named(window, "sample.flac").edit.volume_db == pytest.approx(-6.0)
    assert "-6.0 dB" in window.edit_panel.volume_label.text()


def test_volume_range(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    slider = window.edit_panel.volume_slider
    slider.setValue(slider.minimum())
    pump()
    assert item_named(window, "sample.flac").edit.volume_db == pytest.approx(-30.0)

    slider.setValue(slider.maximum())
    pump()
    assert item_named(window, "sample.flac").edit.volume_db == pytest.approx(30.0)


def test_reset_volume_button(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    window.edit_panel.volume_slider.setValue(-100)
    pump()
    assert item_named(window, "sample.flac").edit.has_volume

    window.edit_panel.reset_volume_button.click()
    pump()
    assert not item_named(window, "sample.flac").edit.has_volume


# ---------------------------------------------------------------------------
# 一覧への反映
# ---------------------------------------------------------------------------
def test_edit_badge_appears_in_the_list(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    assert cell(window, 0, COL_EDIT) == ""

    window.edit_panel.volume_slider.setValue(-60)
    pump()
    assert "-6.0dB" in cell(window, 0, COL_EDIT)

    window.edit_panel.start_edit.setText("0:01.000")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()
    assert "✂" in cell(window, 0, COL_EDIT)


def test_trim_shrinks_the_size_estimate(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    item = item_named(window, "sample.flac")
    full = item.estimated_size(window.model.quality(), window.model.output_format())

    window.edit_panel.start_edit.setText("0:00.000")
    window.edit_panel.end_edit.setText("0:02.500")  # 5 秒の半分
    window.edit_panel.end_edit.editingFinished.emit()
    pump()

    trimmed = item.estimated_size(window.model.quality(), window.model.output_format())
    assert trimmed == pytest.approx(full / 2, rel=0.05)
    assert cell(window, 0, COL_SIZE).startswith("約 ")


def test_summary_counts_edited_files(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac", "sample.wav"))
    select_only(window, 0)
    window.edit_panel.volume_slider.setValue(-30)
    pump()
    assert "編集 1" in window.summary_label.text()


def test_edits_are_per_file(window, workspace):
    """編集はファイルごと。選択を切り替えても混ざらない。"""
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac", "sample.wav"))

    select_only(window, 0)
    window.edit_panel.volume_slider.setValue(-60)
    pump()

    select_only(window, 1)
    assert window.edit_panel.volume_slider.value() == 0, "別ファイルには波及しない"
    assert window.model.item_at(1).edit.is_default

    select_only(window, 0)
    assert window.edit_panel.volume_slider.value() == -60, "戻すと元の値が出る"


# ---------------------------------------------------------------------------
# 実際の変換
# ---------------------------------------------------------------------------
def test_trim_is_applied_to_the_output(window, workspace, ffmpeg_tools):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    window.edit_panel.start_edit.setText("0:01.000")
    window.edit_panel.end_edit.setText("0:03.000")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()

    window.start_conversion()
    wait_for_conversion(window)

    item = item_named(window, "sample.flac")
    assert item.status is FileStatus.DONE
    assert duration_of(ffmpeg_tools, item.output_path) == pytest.approx(2.0, abs=0.1)


def test_progress_reaches_one_with_trim(window, workspace):
    """out_time は切り出し後の相対時間なので、進捗の分母もそれに合わせる。"""
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)

    window.edit_panel.start_edit.setText("1:00.000")
    window.edit_panel.end_edit.setText("2:00.000")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()

    seen: list[float] = []
    window.conversion.file_progress.connect(lambda _p, r: seen.append(r))
    window.start_conversion()
    wait_for_conversion(window)

    assert seen, "進捗が届いていない"
    assert all(0.0 <= r <= 1.0 for r in seen), f"範囲外の進捗: {seen}"
    assert seen[-1] == 1.0
    assert window.overall_progress.value() == 100


def test_unedited_files_are_unaffected(window, workspace, ffmpeg_tools):
    """同じキューに編集ありと編集なしが混ざっても取り違えない。"""
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac", "sample.wav"))

    select_only(window, 0)
    window.edit_panel.start_edit.setText("0:00.000")
    window.edit_panel.end_edit.setText("0:02.000")
    window.edit_panel.end_edit.editingFinished.emit()
    pump()

    window.start_conversion()
    wait_for_conversion(window)

    edited = item_named(window, "sample.flac")
    untouched = item_named(window, "sample.wav")
    assert duration_of(ffmpeg_tools, edited.output_path) == pytest.approx(2.0, abs=0.1)
    assert duration_of(ffmpeg_tools, untouched.output_path) == pytest.approx(5.0, abs=0.1)


# ---------------------------------------------------------------------------
# ロックと設定
# ---------------------------------------------------------------------------
def test_panel_is_locked_during_conversion(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    assert window.edit_panel.isEnabled()

    window.start_conversion()
    assert not window.edit_panel.isEnabled()
    assert not window.edit_button.isEnabled()

    window.cancel_conversion()
    wait_for_conversion(window, timeout=20)
    assert window.edit_button.isEnabled()


def test_panel_visibility_is_saved(window, isolated_config):
    window.set_edit_panel_visible(True)
    pump()
    assert window.current_config().edit_panel_visible is True

    window.close()
    pump()
    assert load_config().config.edit_panel_visible is True


def test_edits_are_not_saved_to_config(window, workspace, isolated_config):
    """編集はファイル固有なので config.json には残さない。"""
    import json

    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    window.edit_panel.volume_slider.setValue(-60)
    pump()

    window.close()
    pump()

    data = json.loads((isolated_config / "config.json").read_text(encoding="utf-8"))
    assert "volume" not in json.dumps(data)
    assert "trim" not in json.dumps(data)
    assert data["edit_panel_visible"] is True


def test_restoring_panel_visibility(qapp, ffmpeg_tools):
    from voggify.ui.main_window import MainWindow

    window = MainWindow(ffmpeg_tools, AppConfig(edit_panel_visible=True))
    window.show()
    try:
        pump()
        assert window.edit_panel.isVisible()
        assert window.edit_button.isChecked()
    finally:
        window.probe_service.discard_pending()
        window.hide()
        window.deleteLater()
        qapp.processEvents()
