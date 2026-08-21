"""GUI テストの補助関数。

イベントループを回す、ドラッグ&ドロップを合成する、書き込み権限を
一時的に落とす、といった「テストの都合」をここにまとめる。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Iterator

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

#: 変換の完了待ちに使う既定のタイムアウト（秒）
DEFAULT_TIMEOUT = 120.0


def app() -> QApplication:
    instance = QApplication.instance()
    assert instance is not None, "QApplication が未作成です（qapp フィクスチャを使ってください）"
    return instance


def pump(seconds: float = 0.05) -> None:
    """指定時間だけイベントを処理する。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app().processEvents()
        time.sleep(0.002)


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = DEFAULT_TIMEOUT,
    message: str = "",
) -> None:
    """条件が成立するまでイベントを回しながら待つ。駄目なら失敗させる。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app().processEvents()
        if predicate():
            app().processEvents()
            return
        time.sleep(0.005)
    pytest.fail(message or f"{timeout} 秒待っても条件が成立しませんでした")


def wait_for_probes(window, timeout: float = 60.0) -> None:
    wait_until(lambda: not window.probe_service.busy, timeout, "解析が終わりません")


def wait_for_conversion(window, timeout: float = DEFAULT_TIMEOUT) -> None:
    wait_until(lambda: not window.conversion.running, timeout, "変換が終わりません")
    pump(0.2)  # 完了シグナルの後続処理を反映させる


def load_files(window, paths: Iterable[Path]) -> None:
    """ファイルを追加して解析の完了まで待つ。"""
    window.model.clear()
    pump()
    window.add_paths(list(paths))
    wait_for_probes(window)
    pump()


def item_named(window, name: str):
    """ファイル名で FileItem を引く。無ければ None。"""
    return next((i for i in window.model.items if i.name == name), None)


def send_drop(view, paths: Iterable[Path], settle: bool = True) -> tuple[bool, bool]:
    """実際の QDragEnterEvent / QDropEvent を流して D&D を再現する。

    settle=False にすると解析結果を配送せずに戻るので、追加直後の状態を見られる。
    戻り値は (dragEnter が受理されたか, drop が受理されたか)。
    """
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    center = view.viewport().rect().center()

    enter = QDragEnterEvent(
        center,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    app().sendEvent(view.viewport(), enter)

    drop = QDropEvent(
        QPointF(center),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    app().sendEvent(view.viewport(), drop)

    if settle:
        pump()
    return enter.isAccepted(), drop.isAccepted()


def _icacls(directory: Path, *args: str) -> None:
    subprocess.run(
        ["icacls", str(directory), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def write_denied(directory: Path) -> Iterator[Path]:
    """フォルダを一時的に書き込み不可にする。

    管理者権限で動かしている場合など、拒否が効かない環境ではスキップする。
    """
    directory.mkdir(parents=True, exist_ok=True)
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    original_mode = directory.stat().st_mode

    if sys.platform == "win32":
        if not user:
            pytest.skip("ユーザー名が取得できないため権限テストを行えません")
        _icacls(directory, "/deny", f"{user}:(W)")
    else:
        directory.chmod(0o500)

    probe = directory / ".write_check"
    try:
        probe.touch()
    except OSError:
        pass  # 期待どおり書き込めない
    else:
        probe.unlink()
        if sys.platform == "win32":
            _icacls(directory, "/remove:d", user)
        else:
            directory.chmod(original_mode)
        pytest.skip("書き込み拒否が効かない環境のためスキップします（管理者権限で実行中?）")

    try:
        yield directory
    finally:
        if sys.platform == "win32":
            _icacls(directory, "/remove:d", user)
        else:
            directory.chmod(original_mode)
