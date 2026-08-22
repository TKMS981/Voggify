"""プレビュー再生のテスト。"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from voggify.ui.preview_player import (
    MULTIMEDIA_AVAILABLE,
    PreviewPlayer,
    boost_is_capped,
    volume_from_db,
)
from tests.qt_helpers import load_files, pump, wait_until, wait_for_conversion

pytestmark = pytest.mark.ffmpeg

#: Voggify が受け付ける形式すべて
ALL_FORMATS = [
    ("MP3", "sample.mp3"),
    ("WAV", "sample.wav"),
    ("FLAC", "sample.flac"),
    ("M4A (AAC)", "sample.m4a"),
    ("OGG (Opus)", "opus.ogg"),
    ("OGG (Vorbis)", "vorbis.ogg"),
    ("OGA (FLAC)", "flac.oga"),
]


def wait_playing(player: PreviewPlayer, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.instance().processEvents()
        if player.is_playing:
            return True
        time.sleep(0.01)
    return False


def wait_position_advances(player: PreviewPlayer, timeout: float = 5.0) -> bool:
    start = player.position()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.instance().processEvents()
        if player.position() > start:
            return True
        time.sleep(0.02)
    return False


def select_only(window, row: int) -> None:
    window.view.clearSelection()
    window.view.selectRow(row)
    pump()


def click_waveform(widget, x: float) -> None:
    """波形を動かさずにクリックする（シーク扱いになる）。"""
    y = widget.height() // 2
    local = QPointF(x, y)
    for kind, button, buttons in (
        (QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
         Qt.MouseButton.LeftButton),
        (QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton,
         Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(kind, local, widget.mapToGlobal(local),
                            button, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.instance().sendEvent(widget, event)
    pump()


# ---------------------------------------------------------------------------
# 音量の換算（Qt 不要な純粋計算）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "db,expected",
    [(-60, 0.001), (-30, 0.03162), (-12, 0.25119), (-6, 0.50119), (0, 1.0)],
)
def test_volume_matches_the_ffmpeg_filter(db, expected):
    """ffmpeg の volume=XdB と同じ 10^(dB/20) であること。"""
    assert volume_from_db(db) == pytest.approx(expected, rel=1e-3)


def test_volume_matches_qt_own_conversion():
    """Qt 自身の換算とも一致すること。"""
    from PySide6.QtMultimedia import QAudio

    for db in (-30, -20, -12, -6, -3, 0):
        qt_value = QAudio.convertVolume(
            db,
            QAudio.VolumeScale.DecibelVolumeScale,
            QAudio.VolumeScale.LinearVolumeScale,
        )
        assert volume_from_db(db) == pytest.approx(qt_value, rel=1e-5)


@pytest.mark.parametrize("db", [0.1, 3, 6, 30])
def test_positive_db_is_capped(db):
    """QAudioOutput は 1.0 までなので増幅はできない。"""
    assert volume_from_db(db) == 1.0
    assert boost_is_capped(db)


def test_zero_and_negative_are_not_flagged_as_capped():
    assert not boost_is_capped(0.0)
    assert not boost_is_capped(-6.0)


# ---------------------------------------------------------------------------
# 全フォーマットの再生
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not MULTIMEDIA_AVAILABLE, reason="Qt Multimedia が無い")
@pytest.mark.parametrize("label,name", ALL_FORMATS, ids=[f[0] for f in ALL_FORMATS])
def test_every_supported_format_plays(qapp, workspace, label, name):
    (source,) = workspace.copy(name)
    player = PreviewPlayer()
    player.set_volume_db(-120)  # 実際には鳴らさない
    failures: list[str] = []
    player.failed.connect(failures.append)
    try:
        player.set_source(source)
        player.play()
        assert wait_playing(player), f"{label} が再生状態にならない"
        assert wait_position_advances(player), f"{label} の再生位置が進まない"
        assert not failures, f"{label} でエラー: {failures}"
    finally:
        player.stop()
        player.deleteLater()
        qapp.processEvents()


# ---------------------------------------------------------------------------
# プレイヤー単体
# ---------------------------------------------------------------------------
@pytest.fixture
def player(qapp, workspace):
    instance = PreviewPlayer()
    instance.set_volume_db(-120)
    yield instance
    instance.stop()
    instance.deleteLater()
    qapp.processEvents()


def test_play_pause_resume_stop(player, workspace):
    (source,) = workspace.copy("long.mp3")
    player.set_source(source)

    player.play()
    assert wait_playing(player)
    assert wait_position_advances(player)

    player.pause()
    pump(0.2)
    assert not player.is_playing
    paused_at = player.position()
    assert paused_at > 0

    player.play()
    assert wait_playing(player)
    assert wait_position_advances(player)

    player.stop()
    pump(0.2)
    assert not player.is_playing


def test_toggle_switches_state(player, workspace):
    (source,) = workspace.copy("sample.flac")
    player.set_source(source)

    player.toggle()
    assert wait_playing(player)
    player.toggle()
    pump(0.2)
    assert not player.is_playing


def test_play_from_seeks(player, workspace):
    (source,) = workspace.copy("long.mp3")
    player.set_source(source)
    player.play_from(120.0)
    assert wait_playing(player)
    wait_until(lambda: player.position() > 119.0, 10, "シーク位置に来ない")
    assert player.position() == pytest.approx(120.0, abs=2.0)


def test_changing_source_stops_playback(player, workspace):
    first, second = workspace.copy("long.mp3", "sample.flac")
    player.set_source(first)
    player.play()
    assert wait_playing(player)

    player.set_source(second)
    pump(0.3)
    assert not player.is_playing
    assert player.source == second


def test_disabling_stops_and_blocks(player, workspace):
    (source,) = workspace.copy("long.mp3")
    player.set_source(source)
    player.play()
    assert wait_playing(player)

    player.set_enabled(False)
    pump(0.3)
    assert not player.is_playing

    player.play()
    pump(0.3)
    assert not player.is_playing, "無効中に再生が始まった"

    player.set_enabled(True)
    player.play()
    assert wait_playing(player)


def test_volume_can_change_while_playing(player, workspace):
    (source,) = workspace.copy("long.mp3")
    player.set_source(source)
    player.play()
    assert wait_playing(player)
    for db in (-30, -12, 0, -6):
        player.set_volume_db(db)
        pump(0.05)
    assert player.is_playing, "音量変更で再生が止まった"


# ---------------------------------------------------------------------------
# 編集パネルとの連携
# ---------------------------------------------------------------------------
def test_play_button_starts_playback(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    assert panel.play_button.isEnabled()
    panel.play_button.click()
    assert wait_playing(panel.player)
    assert "一時停止" in panel.play_button.text()

    panel.play_button.click()
    pump(0.3)
    assert not panel.player.is_playing
    assert "再生" in panel.play_button.text()


def test_clicking_the_waveform_seeks_and_plays(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    wave = panel.waveform
    click_waveform(wave, wave.width() * 0.5)   # 300 秒の中央 = 150 秒

    assert wait_playing(panel.player)
    wait_until(lambda: panel.player.position() > 140, 10, "シークしていない")
    assert panel.player.position() == pytest.approx(150.0, abs=5.0)


def test_clicking_does_not_change_the_selection(window, workspace):
    """クリックはシーク。範囲選択は変えない。"""
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    before = panel.waveform.selection()
    click_waveform(panel.waveform, panel.waveform.width() * 0.3)
    assert panel.waveform.selection() == pytest.approx(before)
    assert window.model.item_at(0).edit.is_default


def test_playhead_follows_playback(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    wait_until(lambda: window.edit_panel.waveform.has_waveform, 60)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    panel.player.play_from(30.0)
    assert wait_playing(panel.player)
    wait_until(
        lambda: (panel.waveform.playhead() or 0) > 30.0, 15, "カーソルが動かない"
    )
    assert panel.waveform.playhead() == pytest.approx(
        panel.player.position(), abs=1.0
    )


def test_volume_slider_reaches_the_player(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    panel = window.edit_panel

    panel.volume_slider.setValue(-120)   # -12.0 dB
    pump()
    assert window.model.item_at(0).edit.volume_db == pytest.approx(-12.0)
    assert panel.player._output.volume() == pytest.approx(
        volume_from_db(-12.0), rel=1e-3
    )


def test_boost_shows_a_note(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("sample.flac"))
    select_only(window, 0)
    panel = window.edit_panel

    panel.volume_slider.setValue(60)  # +6 dB
    pump()
    assert "増幅できない" in panel.play_status_label.text()

    panel.volume_slider.setValue(-60)
    pump()
    assert panel.play_status_label.text() == ""


def test_changing_the_selected_row_stops_playback(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3", "sample.flac"))
    select_only(window, 0)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    panel.play_button.click()
    assert wait_playing(panel.player)

    select_only(window, 1)
    pump(0.3)
    assert not panel.player.is_playing, "行を変えても再生が続いている"


def test_deselecting_stops_playback(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    panel.play_button.click()
    assert wait_playing(panel.player)

    window.view.clearSelection()
    pump(0.3)
    assert not panel.player.is_playing


# ---------------------------------------------------------------------------
# 変換中
# ---------------------------------------------------------------------------
def test_conversion_disables_preview(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    panel.play_button.click()
    assert wait_playing(panel.player)

    window.start_conversion()
    pump(0.3)
    assert not panel.player.is_playing, "変換が始まっても鳴っている"
    assert not panel.play_button.isEnabled()
    assert "変換中" in panel.play_status_label.text()

    window.cancel_conversion()
    wait_for_conversion(window, timeout=20)
    assert panel.play_button.isEnabled(), "変換後に戻っていない"


def test_preview_cannot_start_during_conversion(window, workspace):
    window.set_edit_panel_visible(True)
    load_files(window, workspace.copy("long.mp3"))
    select_only(window, 0)
    panel = window.edit_panel
    panel.player.set_volume_db(-120)

    window.start_conversion()
    pump(0.2)
    panel.player.play()
    pump(0.3)
    assert not panel.player.is_playing

    window.cancel_conversion()
    wait_for_conversion(window, timeout=20)
