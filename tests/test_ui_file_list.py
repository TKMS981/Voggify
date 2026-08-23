"""ファイルリスト UI のテスト（追加・D&D・削除・エラー表示）。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from voggify.models import FileStatus
from voggify.ui.file_list_model import COL_FORMAT, COL_NAME, COL_SIZE, COL_STATUS
from tests.qt_helpers import (
    item_named,
    load_files,
    pump,
    send_drop,
    wait_for_probes,
)

pytestmark = pytest.mark.ffmpeg

SHORT_SAMPLES = ["sample.mp3", "sample.wav", "sample.flac", "sample.m4a"]


def cell(window, row: int, column: int) -> str:
    return window.model.data(window.model.index(row, column), Qt.ItemDataRole.DisplayRole)


def row_of(window, name: str) -> int:
    item = item_named(window, name)
    assert item is not None, f"{name} がリストにありません"
    return window.model.items.index(item)


# ---------------------------------------------------------------------------
# 追加
# ---------------------------------------------------------------------------
def test_drop_adds_files(window, workspace):
    paths = workspace.copy(*SHORT_SAMPLES)
    enter_accepted, drop_accepted = send_drop(window.view, paths, settle=False)

    assert enter_accepted
    assert drop_accepted
    assert window.model.rowCount() == 4
    # 解析はバックグラウンドなので、追加直後は「解析中」
    assert all(i.status is FileStatus.ANALYZING for i in window.model.items)
    assert cell(window, 0, COL_FORMAT) == "解析中…"

    wait_for_probes(window)
    assert all(i.status is FileStatus.READY for i in window.model.items)


def test_format_column_shows_real_codec(window, workspace):
    load_files(window, workspace.copy(*SHORT_SAMPLES))
    shown = {cell(window, r, COL_FORMAT) for r in range(window.model.rowCount())}
    assert shown == {"MP3", "PCM 16bit", "FLAC", "AAC"}


def test_size_column_shows_estimate(window, workspace):
    load_files(window, workspace.copy("sample.mp3"))
    assert cell(window, 0, COL_SIZE).startswith("約 ")


def test_file_dialog_path_adds_files(window, workspace):
    """QFileDialog の戻り値に相当するパスを直接渡す経路。"""
    window.add_paths(workspace.copy("sample.mp3", "sample.flac"))
    wait_for_probes(window)
    assert window.model.rowCount() == 2


def test_duplicates_are_skipped(window, workspace):
    paths = workspace.copy("sample.mp3")
    load_files(window, paths)
    send_drop(window.view, paths)
    assert window.model.rowCount() == 1


def test_folder_drop_collects_supported_files(window, workspace):
    workspace.copy(*SHORT_SAMPLES, "notsupported.opus", "video.mp4")
    send_drop(window.view, [workspace.path])
    wait_for_probes(window)

    names = sorted(i.name for i in window.model.items)
    assert set(SHORT_SAMPLES) <= set(names)
    # 対応拡張子でないものは拾わない
    assert "notsupported.opus" not in names
    # 動画は音声トラックを取り出す対象なので拾う
    assert "video.mp4" in names
    assert item_named(window, "video.mp4").status is FileStatus.READY


def test_drop_without_urls_is_ignored(window):
    from PySide6.QtCore import QMimeData, QPointF
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QApplication

    mime = QMimeData()
    mime.setText("ただのテキスト")
    center = window.view.viewport().rect().center()
    event = QDropEvent(
        QPointF(center),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.instance().sendEvent(window.view.viewport(), event)
    pump()
    assert window.model.rowCount() == 0


# ---------------------------------------------------------------------------
# エラー表示
# ---------------------------------------------------------------------------
def test_unsupported_and_broken_files_stay_as_errors(window, workspace):
    load_files(window, workspace.copy("notsupported.opus", "broken.mp3"))

    assert window.model.rowCount() == 2
    opus = item_named(window, "notsupported.opus")
    broken = item_named(window, "broken.mp3")
    assert opus.status is FileStatus.ERROR
    assert "対応していない拡張子" in opus.message
    assert broken.status is FileStatus.ERROR
    assert "解析に失敗" in broken.message


def test_error_rows_are_visually_distinct(window, workspace):
    load_files(window, workspace.copy("notsupported.opus"))
    model = window.model
    index = model.index(0, COL_NAME)

    foreground = model.data(index, Qt.ItemDataRole.ForegroundRole)
    font = model.data(index, Qt.ItemDataRole.FontRole)
    icon = model.data(model.index(0, COL_STATUS), Qt.ItemDataRole.DecorationRole)

    assert foreground is not None and foreground.color().red() == 150  # グレーアウト
    assert font is not None and font.italic()
    assert icon is not None and not icon.isNull()
    assert cell(window, 0, COL_SIZE) == "-"


def test_extension_mismatch_is_flagged_but_convertible(window, workspace):
    load_files(window, workspace.copy("fake.mp3"))
    item = item_named(window, "fake.mp3")

    assert item.status is FileStatus.READY, "変換自体はできるので通す"
    assert item.note and ".mp3" in item.note and "AAC" in item.note

    row = row_of(window, "fake.mp3")
    assert "⚠" in cell(window, row, COL_FORMAT)
    warn_color = window.model.data(
        window.model.index(row, COL_FORMAT), Qt.ItemDataRole.ForegroundRole
    )
    assert warn_color is not None and warn_color.color().red() == 196

    tooltip = window.model.data(
        window.model.index(row, COL_FORMAT), Qt.ItemDataRole.ToolTipRole
    )
    assert "⚠" in tooltip and "拡張子は" in tooltip


def test_ogg_inputs_are_listed_by_their_real_codec(window, workspace):
    load_files(window, workspace.copy("opus.ogg", "flac.oga"))

    opus = item_named(window, "opus.ogg")
    flac = item_named(window, "flac.oga")
    assert opus.status is FileStatus.READY
    assert flac.status is FileStatus.READY
    assert cell(window, row_of(window, "opus.ogg"), COL_FORMAT) == "Opus"
    assert cell(window, row_of(window, "flac.oga"), COL_FORMAT) == "FLAC"
    # Ogg にこれらが入っているのは普通なので注記は出さない
    assert opus.note is None
    assert flac.note is None


def test_already_vorbis_ogg_is_shown_as_an_error(window, workspace):
    load_files(window, workspace.copy("vorbis.ogg"))

    item = item_named(window, "vorbis.ogg")
    assert item.status is FileStatus.ERROR
    assert "既に OGG Vorbis です" in item.message
    assert item not in window.model.convertible_items()


def test_folder_drop_picks_up_ogg_files(window, workspace):
    workspace.copy("opus.ogg", "flac.oga", "sample.mp3")
    send_drop(window.view, [workspace.path])
    wait_for_probes(window)

    names = sorted(i.name for i in window.model.items)
    assert "opus.ogg" in names
    assert "flac.oga" in names


def test_tooltip_shows_details(window, workspace):
    load_files(window, workspace.copy("sample.flac"))
    tooltip = window.model.data(
        window.model.index(0, COL_NAME), Qt.ItemDataRole.ToolTipRole
    )
    assert "形式:" in tooltip
    assert "再生時間:" in tooltip
    assert "サンプルレート:" in tooltip
    assert "変換後の予測:" in tooltip


# ---------------------------------------------------------------------------
# 削除
# ---------------------------------------------------------------------------
def test_delete_key_removes_selection(window, workspace):
    from PySide6.QtWidgets import QApplication

    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    window.view.selectRow(row_of(window, "sample.mp3"))

    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    QApplication.instance().sendEvent(window.view, event)
    pump()

    assert item_named(window, "sample.mp3") is None
    assert window.model.rowCount() == 1


def test_remove_button_removes_multiple_rows(window, workspace):
    load_files(window, workspace.copy(*SHORT_SAMPLES))
    selection = window.view.selectionModel()
    flags = selection.SelectionFlag.Select | selection.SelectionFlag.Rows
    selection.select(window.model.index(0, 0), flags)
    selection.select(window.model.index(1, 0), flags)

    assert len(window.view.selected_rows()) == 2
    window._remove_selected()
    pump()
    assert window.model.rowCount() == 2


def test_busy_items_are_not_removed(window, workspace):
    """解析中の項目は削除させない。"""
    paths = workspace.copy("long.mp3")
    send_drop(window.view, paths, settle=False)

    row = row_of(window, "long.mp3")
    assert window.model.item_at(row).status.is_busy
    assert window.model.remove_rows([row]) == 0
    assert item_named(window, "long.mp3") is not None

    wait_for_probes(window)


def test_clear_empties_the_list(window, workspace):
    load_files(window, workspace.copy(*SHORT_SAMPLES))
    assert window.model.rowCount() == 4

    window.clear_list()
    pump()
    assert window.model.rowCount() == 0
    assert not window.clear_button.isEnabled()


def test_reanalyze_restores_error_items(window, workspace):
    load_files(window, workspace.copy("sample.mp3"))
    window.model.apply_probe_result(
        str(workspace.path / "sample.mp3"), None, "わざと失敗させた"
    )
    assert item_named(window, "sample.mp3").status is FileStatus.ERROR

    window._reanalyze_rows([0])
    wait_for_probes(window)
    assert item_named(window, "sample.mp3").status is FileStatus.READY


# ---------------------------------------------------------------------------
# 品質とサマリー
# ---------------------------------------------------------------------------
def test_quality_change_updates_estimates(window, workspace):
    load_files(window, workspace.copy("long.mp3"))
    item = item_named(window, "long.mp3")

    sizes = []
    for quality in (0, 2, 6, 9, 10):
        window.model.set_quality(quality)
        pump()
        sizes.append(item.estimated_size(quality))
        assert cell(window, 0, COL_SIZE).startswith("約 ")

    assert sizes == sorted(sizes)
    assert len(set(sizes)) == len(sizes)


def test_summary_counts_files(window, workspace):
    load_files(window, workspace.copy("sample.mp3", "notsupported.opus"))
    summary = window.summary_label.text()
    assert "2 件" in summary
    assert "変換可能 1" in summary
    assert "エラー 1" in summary


# ---------------------------------------------------------------------------
# ffmpeg 未検出
# ---------------------------------------------------------------------------
def test_missing_ffmpeg_shows_banner_and_recovers(window, workspace, ffmpeg_tools):
    assert not window.banner.isVisible()

    window._tools = None
    window.probe_service.set_tools(None)
    window._update_ffmpeg_banner()
    assert window.banner.isVisible()

    load_files(window, workspace.copy("sample.mp3"))
    item = item_named(window, "sample.mp3")
    assert item.status is FileStatus.ERROR
    assert "ffmpeg" in item.message

    window._tools = ffmpeg_tools
    window.probe_service.set_tools(ffmpeg_tools)
    window._recheck_ffmpeg()
    wait_for_probes(window)

    assert not window.banner.isVisible()
    assert item_named(window, "sample.mp3").status is FileStatus.READY
