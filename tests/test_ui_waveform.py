"""波形ウィジェットとドラッグ選択の GUI テスト。"""

from __future__ import annotations

import array

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from voggify.waveform import WaveformData
from voggify.ui.waveform_view import (
    DRAG_THRESHOLD_PX,
    HANDLE_GRAB_PX,
    RULER_HEIGHT,
    WaveformView,
    _tick_step,
)
from tests.qt_helpers import item_named, load_files, pump, wait_until

pytestmark = pytest.mark.ffmpeg


def make_data(duration: float = 30.0, buckets: int = 600) -> WaveformData:
    peaks = array.array("h")
    for i in range(buckets):
        amplitude = int(20000 * abs((i % 100) - 50) / 50)
        peaks.append(-amplitude)
        peaks.append(amplitude)
    return WaveformData(duration=duration, peaks=peaks, sample_rate=8000)


@pytest.fixture
def view(qapp):
    widget = WaveformView()
    widget.resize(600, 120)
    widget.show()
    pump()
    yield widget
    widget.hide()
    widget.deleteLater()
    qapp.processEvents()


def send(widget, kind, x, y=None, button=Qt.MouseButton.LeftButton):
    """マウスイベントを 1 つ送る。

    localPos だけの版は非推奨なので globalPos も渡す。
    """
    y = widget.height() // 2 if y is None else y
    buttons = button if kind == QMouseEvent.Type.MouseButtonPress else (
        Qt.MouseButton.LeftButton if kind == QMouseEvent.Type.MouseMove
        else Qt.MouseButton.NoButton
    )
    local = QPointF(x, y)
    event = QMouseEvent(
        kind,
        local,
        widget.mapToGlobal(local),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.instance().sendEvent(widget, event)


def drag(widget, x0, x1, steps=6, y=None):
    send(widget, QMouseEvent.Type.MouseButtonPress, x0, y)
    for i in range(1, steps + 1):
        send(widget, QMouseEvent.Type.MouseMove, x0 + (x1 - x0) * i / steps, y)
    send(widget, QMouseEvent.Type.MouseButtonRelease, x1, y)
    pump()


def ruler_y(widget) -> int:
    """目盛り帯（範囲選択の掴み代）の中ほどの y 座標。"""
    return widget.height() - RULER_HEIGHT // 2


def drag_range(widget, x0, x1, steps=6):
    """範囲選択のドラッグ。目盛り帯を掴む。"""
    drag(widget, x0, x1, steps, y=ruler_y(widget))


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------
def test_placeholder_when_empty(view):
    assert not view.has_waveform
    view.set_placeholder("波形を読み込み中…")
    pump()
    assert not view.has_waveform  # 落ちずに描けていればよい


def test_setting_data_selects_the_whole_range(view):
    view.set_waveform(make_data(30.0), 30.0)
    pump()
    assert view.has_waveform
    assert view.selection() == pytest.approx((0.0, 30.0))


def test_set_range_does_not_emit(view):
    """数値側から入れたときにシグナルを出すと往復してしまう。"""
    view.set_waveform(make_data(30.0), 30.0)
    seen = []
    view.range_changed.connect(lambda s, e: seen.append((s, e)))
    view.set_range(5.0, 10.0)
    pump()
    assert view.selection() == pytest.approx((5.0, 10.0))
    assert seen == []


def test_volume_change_redraws_without_error(view):
    view.set_waveform(make_data(30.0), 30.0)
    pump()
    view.set_volume_db(-12.0)
    pump()
    view.set_volume_db(12.0)
    pump()
    assert view.has_waveform


# ---------------------------------------------------------------------------
# ドラッグ
# ---------------------------------------------------------------------------
def test_drag_selects_a_range(view):
    view.set_waveform(make_data(30.0), 30.0)
    committed = []
    view.range_committed.connect(lambda s, e: committed.append((s, e)))

    drag_range(view, view.width() * 0.25, view.width() * 0.75)

    start, end = view.selection()
    assert start == pytest.approx(7.5, abs=0.5)
    assert end == pytest.approx(22.5, abs=0.5)
    assert committed, "確定のシグナルが出ていない"


def test_drag_backwards_still_orders_the_range(view):
    view.set_waveform(make_data(30.0), 30.0)
    drag_range(view, view.width() * 0.8, view.width() * 0.2)
    start, end = view.selection()
    assert start < end
    assert start == pytest.approx(6.0, abs=0.5)
    assert end == pytest.approx(24.0, abs=0.5)


def test_a_plain_click_does_not_change_the_range(view):
    """クリックしただけで範囲が消えると使いづらい。"""
    view.set_waveform(make_data(30.0), 30.0)
    view.set_range(5.0, 25.0)
    committed = []
    view.range_committed.connect(lambda s, e: committed.append((s, e)))

    x = view.width() * 0.5
    y = ruler_y(view)
    send(view, QMouseEvent.Type.MouseButtonPress, x, y)
    send(view, QMouseEvent.Type.MouseButtonRelease, x + DRAG_THRESHOLD_PX - 1, y)
    pump()

    assert committed == []
    assert view.selection() == pytest.approx((5.0, 25.0))


def test_dragging_the_start_handle(view):
    view.set_waveform(make_data(30.0), 30.0)
    view.set_range(6.0, 24.0)          # 端は 20% と 80%
    handle_x = view.width() * 0.2
    drag(view, handle_x, view.width() * 0.4)

    start, end = view.selection()
    assert start == pytest.approx(12.0, abs=0.5)
    assert end == pytest.approx(24.0, abs=0.3), "終端は動かない"


def test_dragging_the_end_handle(view):
    view.set_waveform(make_data(30.0), 30.0)
    view.set_range(6.0, 24.0)
    drag(view, view.width() * 0.8, view.width() * 0.5)

    start, end = view.selection()
    assert start == pytest.approx(6.0, abs=0.3), "開始は動かない"
    assert end == pytest.approx(15.0, abs=0.5)


def test_handles_cannot_cross(view):
    view.set_waveform(make_data(30.0), 30.0)
    view.set_range(10.0, 20.0)
    # 終端を開始より左へ引っ張る
    drag(view, view.width() * (20.0 / 30.0), 0)
    start, end = view.selection()
    assert end > start, f"順序が壊れた: {start} 〜 {end}"


def test_selection_stays_within_the_file(view):
    view.set_waveform(make_data(30.0), 30.0)
    drag_range(view, -200, view.width() + 400)
    start, end = view.selection()
    assert start >= 0.0
    assert end <= 30.0


def test_handle_grab_zone(view):
    view.set_waveform(make_data(30.0), 30.0)
    view.set_range(15.0, 30.0)   # 開始は中央
    middle = view.width() * 0.5
    assert view._hit_handle(int(middle)) == "start"
    assert view._hit_handle(int(middle + HANDLE_GRAB_PX + 5)) is None


# ---------------------------------------------------------------------------
# 目盛り
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "duration,width",
    [(5, 600), (30, 600), (300, 600), (3600, 600), (10800, 1200)],
)
def test_tick_step_is_sensible(duration, width):
    step = _tick_step(duration, width)
    assert step > 0
    ticks = duration / step
    assert 1 <= ticks <= width / 40, f"目盛りが多すぎ/少なすぎ: {ticks}"


def test_ruler_reserves_space(view):
    view.set_waveform(make_data(30.0), 30.0)
    pump()
    assert view._wave_rect().height() == view.height() - RULER_HEIGHT


# ---------------------------------------------------------------------------
# 編集パネルとの連携
# ---------------------------------------------------------------------------
def select_only(window, row: int) -> None:
    window.view.clearSelection()
    window.view.selectRow(row)
    pump()


def test_waveform_appears_for_the_selected_file(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)

    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60, "波形が出ない")
    assert window.edit_panel.waveform.has_waveform


def test_placeholder_shows_while_generating(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    window.view.clearSelection()
    window.view.selectRow(0)   # pump しないので生成前の状態を見られる
    assert not window.edit_panel.waveform.has_waveform
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)


def test_dragging_updates_the_numeric_fields(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)

    wave = window.edit_panel.waveform
    drag_range(wave, wave.width() * 0.2, wave.width() * 0.6)

    item = item_named(window, "sample.flac")
    assert item.edit.has_trim
    assert item.edit.trim_start == pytest.approx(1.0, abs=0.2)
    assert item.edit.trim_end == pytest.approx(3.0, abs=0.2)
    assert "0:01" in window.edit_panel.start_edit.text()


def test_numeric_input_updates_the_waveform(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)

    window.edit_panel.start_edit.setText("0:01.000")
    window.edit_panel.end_edit.setText("0:04.000")
    window.edit_panel.start_edit.editingFinished.emit()
    pump()

    start, end = window.edit_panel.waveform.selection()
    assert start == pytest.approx(1.0, abs=0.05)
    assert end == pytest.approx(4.0, abs=0.05)


def test_reset_button_restores_the_whole_waveform(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)

    drag_range(window.edit_panel.waveform, 100, 300)
    assert item_named(window, "sample.flac").edit.has_trim

    window.edit_panel.reset_trim_button.click()
    pump()

    item = item_named(window, "sample.flac")
    start, end = window.edit_panel.waveform.selection()
    assert not item.edit.has_trim
    assert start == pytest.approx(0.0)
    assert end == pytest.approx(item.source_duration, abs=0.05)


def test_volume_is_reflected_in_the_waveform(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)

    window.edit_panel.volume_slider.setValue(-120)
    pump()
    assert window.edit_panel.waveform._volume_db == pytest.approx(-12.0)


# ---------------------------------------------------------------------------
# キャッシュとの連携
# ---------------------------------------------------------------------------
def test_reselecting_uses_the_cache(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac", "sample.wav"))

    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)
    select_only(window, 1)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)
    assert window.waveform_service.cache.count == 2

    # 戻したときは生成を待たずに出る
    select_only(window, 0)
    assert window.edit_panel.waveform.has_waveform, "キャッシュから即座に出ていない"


def test_removing_a_file_drops_its_cache(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)
    assert window.waveform_service.cache.count == 1

    window._remove_rows([0])
    pump()
    assert window.waveform_service.cache.count == 0


def test_clearing_the_list_drops_every_cache(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac", "sample.wav"))
    for row in (0, 1):
        select_only(window, row)
        wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)
    assert window.waveform_service.cache.count == 2

    window.clear_list()
    pump()
    assert window.waveform_service.cache.count == 0


def test_cache_stays_small(window, workspace):
    """波形 1 件は数 KB。多くても知れている。"""
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac", "sample.wav", "sample.m4a"))
    for row in range(3):
        select_only(window, row)
        wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)

    assert window.waveform_service.cache.count == 3
    assert window.waveform_service.cache.nbytes < 100_000


# ---------------------------------------------------------------------------
# 再生カーソルの追従
# ---------------------------------------------------------------------------
#: 再生位置の通知が来る間隔（実測でおよそ 50ms）
POSITION_INTERVAL = 0.05


def _count_repaints(widget, duration: float, seconds: float = 2.0) -> int:
    """位置通知を実測どおりの間隔で流し、再描画された回数を返す。"""
    widget.set_waveform(make_data(duration, buckets=3000), duration)
    widget.set_playhead(0.0)  # 1 回目は必ず描かれるので、数える前に済ませておく

    calls: list[int] = []
    original = widget.update
    widget.update = lambda *a, **k: calls.append(1)
    try:
        elapsed = 0.0
        while elapsed < seconds:
            elapsed += POSITION_INTERVAL
            widget.set_playhead(elapsed)
    finally:
        widget.update = original
    return len(calls)


def _expected_repaints(widget, duration: float, seconds: float = 2.0) -> float:
    """その長さなら何 px 動くか＝何回描き直すのが正しいか。"""
    seconds_per_pixel = duration / widget.width()
    moved_px = seconds / seconds_per_pixel
    # 通知の回数より多くは描けない
    return min(moved_px, seconds / POSITION_INTERVAL)


def test_playhead_repaints_while_playing_a_long_file(view):
    """長いファイルでもカーソルが動く（＝再描画が起きる）こと。

    1px 未満の移動は間引くが、その判定の基準は「最後に描いた位置」で
    なければならない。基準を毎回いまの位置に進めてしまうと差分が 1px を
    超えず、カーソルが止まって見える。4 分の動画は通知 1 回あたり
    0.5px 程度しか動かないので、5 秒のテスト音源では取りこぼす。

    なお playhead() の値は不具合があっても正しく進むため、
    「描き直されたか」を見ないとこの退行は捕まえられない。
    """
    duration = 240.0
    repaints = _count_repaints(view, duration)
    expected = _expected_repaints(view, duration)
    assert expected >= 4, "テストの前提（4px 以上動く長さ）が崩れています"
    assert repaints >= expected * 0.5, (
        f"カーソルがほとんど描き直されていない（{repaints} 回 / 期待 {expected:.0f} 回）"
    )


def test_playhead_repaints_every_notification_when_short(view):
    """短いファイルでは通知のたびに動く（間引きに掛からない）。"""
    repaints = _count_repaints(view, 5.0)
    assert repaints == pytest.approx(2.0 / POSITION_INTERVAL, abs=1)


def test_playhead_repaint_is_throttled(view):
    """間引き自体は効いていること（1px あたり 1 回に抑える）。"""
    duration = 240.0
    repaints = _count_repaints(view, duration)
    notifications = 2.0 / POSITION_INTERVAL
    assert repaints < notifications, "間引きが効いていない"
    assert repaints <= _expected_repaints(view, duration) + 1


def test_playhead_matches_what_is_drawn(view):
    """playhead() は実際に描いた位置を返す（ずれは 1px 相当まで）。"""
    duration = 240.0
    view.set_waveform(make_data(duration, buckets=3000), duration)
    elapsed = 0.0
    while elapsed < 2.0:
        view.set_playhead(elapsed)
        elapsed += POSITION_INTERVAL
    drawn = view.playhead()
    assert drawn is not None
    assert abs(drawn - elapsed) <= duration / view.width() + POSITION_INTERVAL


# ---------------------------------------------------------------------------
# 再生カーソルのドラッグ（スクラブ）と範囲選択の分離
# ---------------------------------------------------------------------------
def test_dragging_the_wave_moves_the_playhead_only(view):
    """波形の上のドラッグは再生カーソルだけを動かし、範囲には触らない。"""
    view.set_waveform(make_data(30.0), 30.0)
    view.set_range(5.0, 25.0)
    committed = []
    scrubbed = []
    view.range_committed.connect(lambda s, e: committed.append((s, e)))
    view.playhead_moved.connect(scrubbed.append)

    drag(view, view.width() * 0.2, view.width() * 0.6)   # y は波形の中ほど

    assert view.selection() == pytest.approx((5.0, 25.0)), "範囲が動いてしまった"
    assert committed == [], "範囲の確定シグナルが出ている"
    assert scrubbed, "スクラブのシグナルが出ていない"
    assert view.playhead() == pytest.approx(18.0, abs=0.5)


def test_dragging_the_ruler_selects_a_range_only(view):
    """目盛り帯のドラッグは範囲だけを変え、再生カーソルは動かさない。"""
    view.set_waveform(make_data(30.0), 30.0)
    view.set_playhead(3.0)
    scrubbed = []
    view.playhead_moved.connect(scrubbed.append)

    drag_range(view, view.width() * 0.25, view.width() * 0.75)

    assert view.selection()[0] == pytest.approx(7.5, abs=0.5)
    assert scrubbed == [], "範囲選択でスクラブが起きている"
    assert view.playhead() == pytest.approx(3.0, abs=0.01), "カーソルが動いた"


def test_handles_are_grabbable_from_the_ruler(view):
    """端のハンドルは目盛り帯からも掴める（微調整しやすいように）。"""
    view.set_waveform(make_data(30.0), 30.0)
    view.set_range(6.0, 24.0)
    drag(view, view.width() * 0.2, view.width() * 0.4, y=ruler_y(view))
    start, end = view.selection()
    assert start == pytest.approx(12.0, abs=0.5)
    assert end == pytest.approx(24.0, abs=0.3)


def test_scrubbing_wins_over_playback_updates(view):
    """掴んでいる間は再生側の位置通知でカーソルを引き戻さない。"""
    view.set_waveform(make_data(30.0), 30.0)
    send(view, QMouseEvent.Type.MouseButtonPress, view.width() * 0.5)
    send(view, QMouseEvent.Type.MouseMove, view.width() * 0.5)

    view.set_playhead(1.0)   # 再生中の通知が割り込んできた想定
    assert view.playhead() == pytest.approx(15.0, abs=0.5), "指の位置が奪われた"

    send(view, QMouseEvent.Type.MouseButtonRelease, view.width() * 0.5)
    pump()
    view.set_playhead(1.0)   # 離した後は従来どおり追従する
    assert view.playhead() == pytest.approx(1.0, abs=0.01)
