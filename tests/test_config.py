"""設定の永続化のテスト。

前半は Qt 非依存の読み書き、後半は MainWindow への復元と保存。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from voggify.config import (
    CONFIG_VERSION,
    ENV_CONFIG_DIR,
    AppConfig,
    WindowGeometry,
    config_dir,
    config_from_dict,
    config_path,
    load_config,
    save_config,
)
from voggify.formats import DEFAULT_QUALITY
from tests.qt_helpers import pump, write_denied


# ---------------------------------------------------------------------------
# 保存場所
# ---------------------------------------------------------------------------
def test_config_dir_follows_the_environment_override(isolated_config):
    assert config_dir() == isolated_config
    assert config_path() == isolated_config / "config.json"


def test_config_dir_uses_appdata_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_CONFIG_DIR, raising=False)
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert config_dir() == tmp_path / "Voggify"
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert config_dir() == tmp_path / "Voggify"


# ---------------------------------------------------------------------------
# 往復
# ---------------------------------------------------------------------------
def test_defaults_when_no_file_exists():
    result = load_config()
    assert result.existed is False
    assert result.warnings == []
    assert result.config == AppConfig()
    assert result.config.quality == DEFAULT_QUALITY


def test_round_trip(tmp_path):
    original = AppConfig(
        quality=9,
        use_custom_output_dir=True,
        output_dir=str(tmp_path / "out"),
        log_visible=True,
        window=WindowGeometry(x=100, y=50, width=1000, height=700, maximized=False),
    )
    saved, reason = save_config(original)
    assert saved, reason

    result = load_config()
    assert result.existed is True
    assert result.warnings == []
    assert result.config == original


def test_file_is_human_readable_json(isolated_config):
    save_config(AppConfig(quality=3, output_dir="C:/音楽/出力"))
    text = (isolated_config / "config.json").read_text(encoding="utf-8")

    assert "\n" in text, "1 行に詰めない（人が読める形にする）"
    assert "音楽" in text, "日本語をエスケープしない"
    data = json.loads(text)
    assert data["quality"] == 3
    assert data["config_version"] == CONFIG_VERSION


def test_save_is_atomic(isolated_config):
    save_config(AppConfig())
    assert (isolated_config / "config.json").is_file()
    assert not list(isolated_config.glob("*.tmp")), "一時ファイルを残さない"


def test_save_creates_missing_directory(isolated_config):
    assert not isolated_config.exists()
    saved, _ = save_config(AppConfig())
    assert saved
    assert isolated_config.is_dir()


def test_save_reports_failure_without_raising(tmp_path):
    with write_denied(tmp_path / "denied") as denied:
        saved, reason = save_config(AppConfig(), denied / "config.json")
    assert saved is False
    assert "保存できませんでした" in reason


# ---------------------------------------------------------------------------
# バージョン
# ---------------------------------------------------------------------------
def test_config_version_is_written():
    save_config(AppConfig())
    data = json.loads(config_path().read_text(encoding="utf-8"))
    assert data["config_version"] == CONFIG_VERSION


def test_missing_version_is_tolerated():
    config, warnings = config_from_dict({"quality": 4})
    assert config.quality == 4
    assert any("config_version" in w for w in warnings)


def test_future_version_falls_back_to_readable_fields():
    config, warnings = config_from_dict(
        {"config_version": CONFIG_VERSION + 5, "quality": 8}
    )
    assert config.quality == 8, "読める項目は活かす"
    assert any("新しい形式" in w for w in warnings)


def test_invalid_version_type_is_tolerated():
    config, warnings = config_from_dict({"config_version": "one", "quality": 2})
    assert config.quality == 2
    assert any("config_version" in w for w in warnings)


# ---------------------------------------------------------------------------
# 壊れた設定
# ---------------------------------------------------------------------------
def test_broken_json_falls_back_to_defaults(isolated_config):
    isolated_config.mkdir(parents=True)
    (isolated_config / "config.json").write_text("{ これは JSON ではない", encoding="utf-8")

    result = load_config()
    assert result.config == AppConfig()
    assert result.existed is True
    assert any("壊れています" in w for w in result.warnings)


def test_non_object_json_falls_back(isolated_config):
    isolated_config.mkdir(parents=True)
    (isolated_config / "config.json").write_text("[1, 2, 3]", encoding="utf-8")

    result = load_config()
    assert result.config == AppConfig()
    assert any("形式が不正" in w for w in result.warnings)


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("quality", "六"),
        ("quality", None),
        ("quality", True),
        ("use_custom_output_dir", "yes"),
        ("log_visible", 1),
        ("output_dir", 123),
        ("output_dir", "   "),
    ],
)
def test_bad_field_types_fall_back_with_a_warning(key, bad_value):
    config, warnings = config_from_dict({"config_version": 1, key: bad_value})
    assert warnings, f"{key}={bad_value!r} で警告が出ていない"
    assert getattr(config, key) == getattr(AppConfig(), key)


@pytest.mark.parametrize("quality", [-1, 11, 999])
def test_out_of_range_quality_falls_back(quality):
    config, warnings = config_from_dict({"config_version": 1, "quality": quality})
    assert config.quality == DEFAULT_QUALITY
    assert any("範囲外" in w for w in warnings)


def test_custom_dir_without_path_falls_back():
    config, warnings = config_from_dict(
        {"config_version": 1, "use_custom_output_dir": True, "output_dir": None}
    )
    assert config.use_custom_output_dir is False
    assert any("出力先" in w for w in warnings)


@pytest.mark.parametrize(
    "geometry",
    [
        "not a dict",
        {"x": 0, "y": 0, "width": "wide", "height": 600},
        {"x": 0, "y": 0, "width": 10, "height": 10},  # 小さすぎる
        {"x": 0, "y": 0},  # 欠けている
    ],
)
def test_bad_geometry_is_ignored(geometry):
    config, warnings = config_from_dict({"config_version": 1, "window": geometry})
    assert config.window is None
    assert warnings


def test_unreadable_file_falls_back(isolated_config):
    """読めないファイルでも例外にせず既定値で起動する。"""
    isolated_config.mkdir(parents=True)
    # ファイルの代わりにフォルダを置いて read_text を失敗させる
    (isolated_config / "config.json").mkdir()

    result = load_config()
    assert result.config == AppConfig()
    assert result.warnings == [] or all(isinstance(w, str) for w in result.warnings)


# ---------------------------------------------------------------------------
# MainWindow への反映
# ---------------------------------------------------------------------------
pytestmark_ui = pytest.mark.ffmpeg


def make_window(qapp, tools, config=None, warnings=None):
    from voggify.ui.main_window import MainWindow

    window = MainWindow(tools, config, warnings)
    # 設定でサイズを復元したときはそれを上書きしない
    if config is None or config.window is None:
        window.resize(960, 640)
    window.show()
    window.failure_dialog_calls = []
    window._show_failure_summary = window.failure_dialog_calls.append
    return window


@pytest.fixture
def make_main_window(qapp, ffmpeg_tools):
    """設定を差し替えながら MainWindow を作れるようにする。"""
    created = []

    def factory(config=None, warnings=None):
        window = make_window(qapp, ffmpeg_tools, config, warnings)
        created.append(window)
        return window

    yield factory

    for window in created:
        window.probe_service.discard_pending()
        window.hide()
        window.deleteLater()
    qapp.processEvents()


@pytest.mark.ffmpeg
def test_defaults_on_first_launch(make_main_window):
    window = make_main_window()
    assert window.settings.quality() == DEFAULT_QUALITY
    assert window.settings.output_dir() is None
    assert not window.log_panel.isVisible()


@pytest.mark.ffmpeg
def test_settings_are_restored(make_main_window, tmp_path):
    target = tmp_path / "saved_output"
    target.mkdir()
    config = AppConfig(
        quality=2, use_custom_output_dir=True, output_dir=str(target), log_visible=True
    )
    window = make_main_window(config)

    assert window.settings.quality() == 2
    assert window.model.quality() == 2, "予測サイズにも反映される"
    assert window.settings.output_dir() == target
    assert window.settings.custom_folder_radio.isChecked()
    assert window.log_panel.isVisible()
    assert window.current_options().quality == 2
    assert window.current_options().output_dir == target


@pytest.mark.ffmpeg
def test_remembered_dir_is_kept_when_not_in_use(make_main_window, tmp_path):
    """「同じフォルダ」に戻していても、選んでいたパスは覚えておく。"""
    target = tmp_path / "remembered"
    target.mkdir()
    config = AppConfig(use_custom_output_dir=False, output_dir=str(target))
    window = make_main_window(config)

    assert window.settings.output_dir() is None
    assert window.settings.remembered_output_dir() == target
    assert window.settings.path_edit.text() == str(target)


@pytest.mark.ffmpeg
def test_missing_saved_dir_falls_back_with_a_warning(make_main_window, tmp_path):
    gone = tmp_path / "removed_usb_drive"
    config = AppConfig(use_custom_output_dir=True, output_dir=str(gone))
    window = make_main_window(config)

    assert window.settings.output_dir() is None, "入力と同じフォルダに戻す"
    assert window.settings.same_folder_radio.isChecked()
    assert window.settings.is_valid(), "無効状態のまま起動しない"
    assert "見つかりません" in window.log_panel.to_text()
    # パスは覚えたままにして、復活したらラジオ 1 つで戻せるようにする
    assert window.settings.remembered_output_dir() == gone
    assert window.settings.path_edit.text() == str(gone)


@pytest.mark.ffmpeg
def test_unwritable_saved_dir_falls_back(make_main_window, tmp_path):
    with write_denied(tmp_path / "denied") as denied:
        config = AppConfig(use_custom_output_dir=True, output_dir=str(denied))
        window = make_main_window(config)

    assert window.settings.output_dir() is None
    assert "書き込めません" in window.log_panel.to_text()


@pytest.mark.ffmpeg
def test_load_warnings_are_shown(make_main_window):
    window = make_main_window(AppConfig(), ["設定ファイルの内容が壊れています。"])
    assert "壊れています" in window.log_panel.to_text()
    assert "壊れています" in window.statusBar().currentMessage()


# ---------------------------------------------------------------------------
# MainWindow からの保存
# ---------------------------------------------------------------------------
@pytest.mark.ffmpeg
def test_current_config_reflects_the_ui(make_main_window, tmp_path):
    window = make_main_window()
    target = tmp_path / "out"
    target.mkdir()
    window.settings.quality_slider.setValue(8)
    window.settings.set_output_dir(target)
    window.set_log_visible(True)
    pump()

    config = window.current_config()
    assert config.quality == 8
    assert config.use_custom_output_dir is True
    assert config.output_dir == str(target)
    assert config.log_visible is True
    assert config.window is not None and config.window.width > 0


@pytest.mark.ffmpeg
def test_close_saves_the_config(make_main_window, tmp_path, isolated_config):
    window = make_main_window()
    target = tmp_path / "out"
    target.mkdir()
    window.settings.quality_slider.setValue(1)
    window.settings.set_output_dir(target)
    window.set_log_visible(True)
    pump()

    window.close()
    pump()

    saved_file = isolated_config / "config.json"
    assert saved_file.is_file(), "終了時に保存される"
    data = json.loads(saved_file.read_text(encoding="utf-8"))
    assert data["quality"] == 1
    assert data["use_custom_output_dir"] is True
    assert data["output_dir"] == str(target)
    assert data["log_visible"] is True


@pytest.mark.ffmpeg
def test_settings_survive_a_restart(make_main_window, tmp_path, isolated_config):
    """保存 → 読み込み → 復元まで通しで確認する。"""
    target = tmp_path / "out"
    target.mkdir()

    first = make_main_window()
    first.settings.quality_slider.setValue(4)
    first.settings.set_output_dir(target)
    first.set_log_visible(True)
    pump()
    first.close()
    pump()

    loaded = load_config()
    assert loaded.existed is True
    assert loaded.warnings == []

    second = make_main_window(loaded.config, loaded.warnings)
    assert second.settings.quality() == 4
    assert second.settings.output_dir() == target
    assert second.log_panel.isVisible()


@pytest.mark.ffmpeg
def test_close_still_works_when_saving_fails(make_main_window, monkeypatch):
    """設定を保存できなくてもアプリの終了を妨げない。"""
    import voggify.ui.main_window as main_window_module

    window = make_main_window()
    monkeypatch.setattr(
        main_window_module,
        "save_config",
        lambda config, path=None: (_ for _ in ()).throw(RuntimeError("書けない")),
    )

    window.close()  # 例外が漏れたらここで失敗する
    pump()
    assert not window.isVisible()


@pytest.mark.ffmpeg
def test_window_geometry_round_trip(make_main_window, isolated_config):
    window = make_main_window()
    window.resize(1024, 700)
    pump()
    window.close()
    pump()

    data = json.loads((isolated_config / "config.json").read_text(encoding="utf-8"))
    assert data["window"]["width"] == 1024
    assert data["window"]["height"] == 700

    restored = make_main_window(load_config().config)
    assert restored.width() == 1024
    assert restored.height() == 700


@pytest.mark.ffmpeg
def test_offscreen_geometry_keeps_size_but_not_position(make_main_window):
    """画面構成が変わって保存位置が画面外になっていても復元しない。"""
    config = AppConfig(
        window=WindowGeometry(x=-30000, y=-30000, width=900, height=600)
    )
    window = make_main_window(config)

    assert window.width() == 900, "サイズは活かす"
    assert window.x() > -30000, "画面外の位置は使わない"
