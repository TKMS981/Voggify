"""変換の実行・スレッド処理・進捗表示のテスト。"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt

from voggify.converter import ConversionOptions
from voggify.models import FileStatus
from voggify.ui.file_list_model import (
    COL_PROGRESS,
    COL_SIZE,
    COL_STATUS,
    ROLE_PROGRESS,
    ROLE_STATUS,
)
from tests.qt_helpers import (
    item_named,
    load_files,
    pump,
    wait_for_conversion,
    wait_until,
    write_denied,
)

pytestmark = pytest.mark.ffmpeg


def cell(window, row: int, column: int) -> str:
    return window.model.data(window.model.index(row, column), Qt.ItemDataRole.DisplayRole)


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------
def test_converts_every_queued_file(window, workspace):
    load_files(window, workspace.copy("sample.mp3", "sample.flac", "sample.m4a"))
    assert all(i.status is FileStatus.READY for i in window.model.items)

    assert window.start_conversion() is True
    wait_for_conversion(window)

    assert all(i.status is FileStatus.DONE for i in window.model.items)
    assert len(workspace.outputs()) == 3
    assert not list(workspace.path.rglob("*.part"))


def test_status_passes_through_queued_and_converting(window, workspace):
    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    seen: dict[str, set[FileStatus]] = {}

    def snapshot(*_args) -> None:
        for entry in window.model.items:
            seen.setdefault(entry.name, set()).add(entry.status)

    window.model.dataChanged.connect(snapshot)

    window.start_conversion()
    assert all(i.status is FileStatus.QUEUED for i in window.model.items)
    wait_for_conversion(window)

    for name in ("sample.mp3", "sample.flac"):
        assert FileStatus.CONVERTING in seen[name], f"{name} が CONVERTING を経ていない"
        assert FileStatus.DONE in seen[name]


def test_ui_stays_responsive_during_conversion(window, workspace):
    from PySide6.QtWidgets import QApplication

    load_files(window, workspace.copy("long.mp3"))
    window.start_conversion()

    ticks = 0
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and window.conversion.running:
        QApplication.instance().processEvents()
        ticks += 1
        time.sleep(0.002)

    assert ticks > 50, f"変換中にイベントが回っていない（{ticks} 回）"
    wait_for_conversion(window)


def test_per_file_progress_is_reported(window, workspace):
    load_files(window, workspace.copy("long.mp3"))
    seen: list[float] = []
    window.conversion.file_progress.connect(lambda _path, ratio: seen.append(ratio))

    window.start_conversion()
    wait_for_conversion(window)

    assert seen, "進捗が届いていない"
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    assert any(0.0 < ratio < 1.0 for ratio in seen), f"途中経過が無い: {seen}"


def test_overall_progress_reaches_100(window, workspace):
    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    window.start_conversion()
    wait_for_conversion(window)
    assert window.overall_progress.value() == 100


def test_result_is_recorded_on_the_item(window, workspace):
    load_files(window, workspace.copy("sample.mp3"))
    window.start_conversion()
    wait_for_conversion(window)

    item = item_named(window, "sample.mp3")
    assert item.status is FileStatus.DONE
    assert item.output_path is not None and item.output_path.exists()
    assert item.output_size == item.output_path.stat().st_size
    assert item.elapsed_sec > 0
    assert len(item.log_lines) > 3


def test_size_column_switches_to_actual_size(window, workspace):
    load_files(window, workspace.copy("sample.mp3"))
    assert cell(window, 0, COL_SIZE).startswith("約 ")

    window.start_conversion()
    wait_for_conversion(window)

    assert not cell(window, 0, COL_SIZE).startswith("約 ")
    assert cell(window, 0, COL_STATUS) == "完了"
    model = window.model
    assert model.data(model.index(0, COL_PROGRESS), ROLE_PROGRESS) == 1.0
    assert model.data(model.index(0, COL_PROGRESS), ROLE_STATUS) is FileStatus.DONE

    tooltip = model.data(model.index(0, COL_SIZE), Qt.ItemDataRole.ToolTipRole)
    assert "出力先:" in tooltip and "所要時間:" in tooltip


def test_success_summary_is_reported(window, workspace):
    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    window.start_conversion()
    wait_for_conversion(window)

    assert "成功 2 件" in window.statusBar().currentMessage()
    assert "完了 2" in window.summary_label.text()
    assert window.run_button.text() == "変換開始"
    assert not window.run_button.isEnabled(), "全て完了したら押せない"


# ---------------------------------------------------------------------------
# UI ロック
# ---------------------------------------------------------------------------
def test_ui_is_locked_during_conversion(window, workspace):
    load_files(window, workspace.copy("long.mp3"))
    window.start_conversion()

    assert window.run_button.text() == "キャンセル"
    assert not window.add_button.isEnabled()
    assert not window.clear_button.isEnabled()
    assert not window.view.acceptDrops()
    assert not window.settings.isEnabled()

    window.cancel_conversion()
    wait_for_conversion(window)

    assert window.run_button.text() == "変換開始"
    assert window.add_button.isEnabled()
    assert window.view.acceptDrops()
    assert window.settings.isEnabled()


def test_add_and_clear_are_refused_while_converting(window, workspace):
    sources = workspace.copy("long.mp3", "sample.mp3")
    load_files(window, [sources[0]])
    window.start_conversion()

    window.add_paths([sources[1]])
    assert window.model.rowCount() == 1
    assert "変換中" in window.statusBar().currentMessage()

    window.clear_list()
    assert window.model.rowCount() == 1

    window.cancel_conversion()
    wait_for_conversion(window)


# ---------------------------------------------------------------------------
# キャンセル
# ---------------------------------------------------------------------------
def test_cancel_stops_current_and_skips_the_rest(window, workspace):
    load_files(window, workspace.copy("long.mp3", "sample.mp3", "sample.flac"))
    window.start_conversion()

    wait_until(
        lambda: item_named(window, "long.mp3").status is FileStatus.CONVERTING,
        timeout=20,
        message="変換が始まらない",
    )
    wait_until(
        lambda: item_named(window, "long.mp3").progress > 0.02,
        timeout=20,
        message="進捗が動き出さない",
    )

    started = time.monotonic()
    window.cancel_conversion()
    assert not window.run_button.isEnabled(), "中断処理中はボタンを押せない"
    wait_for_conversion(window, timeout=20)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"停止に {elapsed:.1f} 秒かかった"
    assert all(i.status is FileStatus.CANCELLED for i in window.model.items)
    assert not workspace.outputs()
    assert not list(workspace.path.rglob("*.part"))
    assert "中断" in window.statusBar().currentMessage()
    assert window.add_button.isEnabled(), "ロックが解除される"


def test_cancelled_items_can_be_retried(window, workspace):
    load_files(window, workspace.copy("long.mp3", "sample.mp3"))
    window.start_conversion()
    wait_until(
        lambda: item_named(window, "long.mp3").status is FileStatus.CONVERTING,
        timeout=20,
    )
    window.cancel_conversion()
    wait_for_conversion(window, timeout=20)

    assert len(window.model.convertible_items()) == 2
    assert window.run_button.isEnabled()

    window.start_conversion()
    wait_for_conversion(window)
    assert all(i.status is FileStatus.DONE for i in window.model.items)
    assert len(workspace.outputs()) == 2


# ---------------------------------------------------------------------------
# 失敗
# ---------------------------------------------------------------------------
def test_failure_marks_item_and_keeps_going(window, workspace):
    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    with write_denied(workspace.path / "locked") as denied:
        window.current_options = lambda: ConversionOptions(quality=6, output_dir=denied)
        window.start_conversion()
        wait_for_conversion(window)

    assert all(i.status is FileStatus.FAILED for i in window.model.items)
    item = item_named(window, "sample.mp3")
    assert "書き込" in item.message
    assert cell(window, 0, COL_PROGRESS) == "失敗"

    foreground = window.model.data(
        window.model.index(0, COL_STATUS), Qt.ItemDataRole.ForegroundRole
    )
    assert foreground is not None and foreground.color().red() == 150
    assert "失敗 2 件" in window.statusBar().currentMessage()
    assert window.failure_dialog_calls == [2]
    assert window.add_button.isEnabled()


def test_unexpected_worker_exception_does_not_stop_the_queue(window, workspace, monkeypatch):
    """想定外の例外でも該当ファイルだけ失敗にして続行する。"""
    import voggify.ui.conversion_service as conversion_service

    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    original = conversion_service.Converter.convert
    calls = {"n": 0}

    def exploding(self, source, options=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("シミュレートした想定外の例外")
        return original(self, source, options, **kwargs)

    monkeypatch.setattr(conversion_service.Converter, "convert", exploding)

    window.start_conversion()
    wait_for_conversion(window)

    statuses = [i.status for i in window.model.items]
    assert FileStatus.FAILED in statuses
    assert FileStatus.DONE in statuses, "残りの処理が続行される"
    assert not window.conversion.running


# ---------------------------------------------------------------------------
# 開始できない場合
# ---------------------------------------------------------------------------
def test_cannot_start_with_empty_list(window):
    assert window.start_conversion() is False
    assert "変換できるファイルがありません" in window.statusBar().currentMessage()


def test_cannot_start_without_ffmpeg(window, workspace):
    load_files(window, workspace.copy("sample.mp3"))
    window._tools = None
    assert window.start_conversion() is False
    assert "ffmpeg" in window.statusBar().currentMessage()
