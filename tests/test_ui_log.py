"""ログパネルとエラー通知のテスト。"""

from __future__ import annotations

import sys

import pytest

from voggify.app import install_excepthook
from voggify.models import FileStatus
from voggify.ui.log_panel import MAX_LOG_BLOCKS
from tests.qt_helpers import (
    item_named,
    load_files,
    pump,
    wait_for_conversion,
    write_denied,
)

pytestmark = pytest.mark.ffmpeg


# ---------------------------------------------------------------------------
# 開閉
# ---------------------------------------------------------------------------
def test_log_is_hidden_by_default(window):
    assert not window.log_panel.isVisible()
    assert not window.log_button.isChecked()


def test_toggle_opens_and_closes(window):
    window.log_button.setChecked(True)
    pump()
    assert window.log_panel.isVisible()

    sizes = window.splitter.sizes()
    assert sizes[0] > sizes[1] > 0, f"一覧が潰れている: {sizes}"

    window.set_log_visible(False)
    pump()
    assert not window.log_panel.isVisible()
    assert not window.log_button.isChecked()


def test_close_button_hides_the_panel(window):
    window.set_log_visible(True)
    pump()
    window.log_panel.close_button.click()
    pump()
    assert not window.log_panel.isVisible()


# ---------------------------------------------------------------------------
# 変換ログ
# ---------------------------------------------------------------------------
def test_captures_ffmpeg_output_with_headers(window, workspace):
    window.set_log_visible(True)
    load_files(window, workspace.copy("sample.mp3", "sample.flac"))
    window.start_conversion()
    wait_for_conversion(window)

    text = window.log_panel.to_text()
    assert "───── sample.mp3" in text
    assert "───── sample.flac" in text
    assert "$ " in text and "libvorbis" in text, "実行コマンドが載る"
    assert "Output #0" in text or "Stream mapping" in text, "ffmpeg の出力が載る"
    assert "完了:" in text
    # キューの順序どおりに並ぶ
    assert text.index("───── sample.mp3") < text.index("───── sample.flac")


def test_logs_the_settings_at_start(window, workspace):
    window.set_log_visible(True)
    load_files(window, workspace.copy("sample.mp3"))
    window.settings.quality_slider.setValue(3)
    pump()
    window.start_conversion()
    wait_for_conversion(window)

    text = window.log_panel.to_text()
    assert "-q:a 3" in text
    assert "入力ファイルと同じフォルダ" in text


def test_mismatch_note_reaches_the_log(window, workspace):
    window.set_log_visible(True)
    load_files(window, workspace.copy("fake.mp3"))

    text = window.log_panel.to_text()
    assert "───── fake.mp3" in text
    assert "拡張子は .mp3" in text
    assert "AAC" in text


def test_probe_errors_reach_the_log(window, workspace):
    window.set_log_visible(True)
    load_files(window, workspace.copy("notsupported.opus", "broken.mp3"))

    text = window.log_panel.to_text()
    assert "対応していない拡張子" in text
    assert "解析に失敗" in text


def test_failure_reaches_the_log_and_flags_the_button(window, workspace):
    window.set_log_visible(False)
    load_files(window, workspace.copy("sample.mp3"))

    with write_denied(workspace.path / "locked") as denied:
        window.settings.set_output_dir(denied)
        pump()
        # 出力先が無効なので開始できない。エラーはログに出る。
        assert not window.settings.is_valid()

    assert "書き込" in window.log_panel.to_text()
    assert window.log_button.text() == "ログ ●", "閉じたログにエラーの印が出る"

    window.set_log_visible(True)
    pump()
    assert window.log_button.text() == "ログ", "開くと印が消える"


def test_conversion_failure_is_logged(window, workspace):
    window.set_log_visible(True)
    load_files(window, workspace.copy("sample.mp3"))

    from voggify.converter import ConversionOptions

    with write_denied(workspace.path / "locked") as denied:
        window.current_options = lambda: ConversionOptions(quality=6, output_dir=denied)
        window.start_conversion()
        wait_for_conversion(window)

    assert item_named(window, "sample.mp3").status is FileStatus.FAILED
    assert "書き込" in window.log_panel.to_text()
    assert "Ctrl+L" in window.statusBar().currentMessage()


# ---------------------------------------------------------------------------
# クリアと保存
# ---------------------------------------------------------------------------
def test_clear_empties_the_log(window, workspace):
    window.set_log_visible(True)
    load_files(window, workspace.copy("sample.mp3"))
    assert not window.log_panel.is_empty()

    window.log_panel.clear()
    assert window.log_panel.is_empty()


def test_entries_have_timestamps_and_a_line_cap(window):
    window.log("テスト行 1")
    window.log("テスト行 2", "error")
    pump()

    lines = window.log_panel.to_text().splitlines()
    assert len(lines) == 2
    assert lines[0].count(":") >= 2, f"時刻が付いていない: {lines[0]}"
    assert "テスト行 1" in lines[0]
    assert window.log_panel.output.maximumBlockCount() == MAX_LOG_BLOCKS


def test_save_writes_utf8(window, workspace, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    window.log("日本語を含むログ行")
    pump()
    target = workspace.path / "voggify-log.txt"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )

    saved = window.log_panel.save_to_file()

    assert saved == target
    assert target.read_text(encoding="utf-8") == window.log_panel.to_text()
    assert "日本語を含むログ行" in target.read_text(encoding="utf-8")


def test_save_is_skipped_when_cancelled(window, workspace, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    window.log("なにか")
    pump()
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    assert window.log_panel.save_to_file() is None


# ---------------------------------------------------------------------------
# 想定外の例外
# ---------------------------------------------------------------------------
def test_excepthook_routes_to_the_log(window):
    saved_hook = sys.excepthook
    install_excepthook(window)
    try:
        try:
            raise ValueError("UI スレッドの想定外エラー")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        pump()
    finally:
        sys.excepthook = saved_hook

    text = window.log_panel.to_text()
    assert "予期しないエラー" in text
    assert "ValueError" in text
    assert "UI スレッドの想定外エラー" in text
    assert "予期しないエラー" in window.statusBar().currentMessage()
    assert window.isVisible(), "アプリは落ちない"


# ---------------------------------------------------------------------------
# ffmpeg 未検出との整合
# ---------------------------------------------------------------------------
def test_banner_shows_the_install_command(window):
    """ffmpeg が無いときは、その場に入れ方を出す。"""
    import sys as _sys

    window._tools = None
    window._update_ffmpeg_banner()
    pump()

    text = window.banner.label.text()
    assert "ffmpeg が見つかりません" in text
    if _sys.platform == "win32":
        assert "winget install Gyan.FFmpeg" in text
    assert window.banner.isVisible()
    # ログにも同じ案内が残る
    assert "winget install Gyan.FFmpeg" in window.log_panel.to_text() or _sys.platform != "win32"


def test_missing_ffmpeg_is_consistent_across_banner_and_log(window, workspace, ffmpeg_tools):
    window.set_log_visible(True)
    window.log_panel.clear()

    window._tools = None
    window.probe_service.set_tools(None)
    window._update_ffmpeg_banner()
    pump()

    assert window.banner.isVisible()
    assert "ffmpeg が見つかりません" in window.log_panel.to_text()

    load_files(window, workspace.copy("sample.mp3"))
    assert item_named(window, "sample.mp3").status is FileStatus.ERROR
    assert "解析できません" in window.log_panel.to_text()
    assert window.start_conversion() is False

    window._tools = ffmpeg_tools
    window.probe_service.set_tools(ffmpeg_tools)
    window._recheck_ffmpeg()
    from tests.qt_helpers import wait_for_probes

    wait_for_probes(window)

    assert not window.banner.isVisible()
    assert "を検出しました" in window.log_panel.to_text()
    assert item_named(window, "sample.mp3").status is FileStatus.READY
