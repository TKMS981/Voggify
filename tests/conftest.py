"""pytest の共通設定とフィクスチャ。

GUI テストはヘッドレス（offscreen）で動かす。PySide6 を読み込む前に
プラットフォームを決める必要があるので、import より先に環境変数を設定する。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# --- PySide6 を import する前に設定する必要がある ------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen ではフォントが無いという警告が出るが、描画結果は検証しないので黙らせる
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts.warning=false")

#: リポジトリのルート（voggify パッケージの親）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: テスト用の音声アセット
ASSETS = Path(__file__).resolve().parent / "assets"


# ---------------------------------------------------------------------------
# 基本のフィクスチャ
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch) -> Path:
    """設定の保存先をテストごとの一時フォルダに逃がす。

    実ユーザーの %APPDATA%\\Voggify\\config.json を絶対に触らないよう
    全テストへ自動適用する。
    """
    from voggify.config import ENV_CONFIG_DIR

    directory = tmp_path / "config"
    monkeypatch.setenv(ENV_CONFIG_DIR, str(directory))
    return directory


@pytest.fixture(scope="session")
def assets() -> Path:
    """テスト用アセットのフォルダ。"""
    if not ASSETS.is_dir():
        pytest.fail(f"テストアセットが見つかりません: {ASSETS}")
    return ASSETS


@pytest.fixture(scope="session")
def ffmpeg_tools():
    """検出済みの ffmpeg / ffprobe。無ければそのテストはスキップする。"""
    from voggify.ffmpeg_locator import find_ffmpeg_tools

    tools = find_ffmpeg_tools()
    if tools is None:
        pytest.skip("ffmpeg / ffprobe が見つかりません（`python main.py check` で確認）")
    if not tools.has_libvorbis:
        pytest.skip("この ffmpeg は libvorbis を含んでいません")
    return tools


class Workspace:
    """テストごとの作業フォルダ。アセットをコピーして使う。"""

    def __init__(self, path: Path, assets_dir: Path) -> None:
        self.path = path
        self._assets = assets_dir

    def copy(self, *names: str) -> list[Path]:
        """アセットを作業フォルダへコピーしてパスを返す。"""
        import shutil

        copied = []
        for name in names:
            destination = self.path / name
            shutil.copyfile(self._assets / name, destination)
            copied.append(destination)
        return copied

    def outputs(self, pattern: str = "*.ogg") -> list[Path]:
        return sorted(self.path.glob(pattern))

    def subdir(self, name: str) -> Path:
        directory = self.path / name
        directory.mkdir(parents=True, exist_ok=True)
        return directory


@pytest.fixture
def workspace(tmp_path: Path, assets: Path) -> Workspace:
    """入力ファイルを置いて変換結果を確認するための一時フォルダ。"""
    work = tmp_path / "work"
    work.mkdir()
    return Workspace(work, assets)


# ---------------------------------------------------------------------------
# Qt
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def qapp():
    """QApplication はプロセスに 1 つだけ。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def window(qapp, ffmpeg_tools):
    """毎テスト新しい MainWindow を用意する。"""
    from voggify.ui.main_window import MainWindow

    main_window = MainWindow(ffmpeg_tools)
    main_window.resize(960, 640)
    main_window.show()

    # 失敗サマリーはモーダルなのでヘッドレスでは開けない。
    # 呼ばれたことだけ記録して先へ進める。
    main_window.failure_dialog_calls = []
    main_window._show_failure_summary = main_window.failure_dialog_calls.append

    yield main_window

    if main_window.conversion.running:
        main_window.conversion.cancel()
        main_window.conversion.wait(10_000)
    main_window.probe_service.discard_pending()
    main_window.hide()
    main_window.deleteLater()
    qapp.processEvents()
