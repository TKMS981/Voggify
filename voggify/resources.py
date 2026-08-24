"""同梱リソース（アイコンなど）の場所を解決する。

ソースから動かす場合はリポジトリの assets/ を、PyInstaller で固めた場合は
展開先（sys._MEIPASS）を見る。ここだけが frozen かどうかを気にする。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: どの OS でも読める既定のアイコン（Qt は .ico も .icns も扱える）
DEFAULT_ICON_RELATIVE = "assets/icon.ico"

#: OS ごとの native なアイコン。ここで返すのはウィンドウ用
#: （setWindowIcon）で、macOS の Dock / Finder のアイコンは
#: .app の CFBundleIconFile＝assets/icon.icns が受け持つ。
ICON_BY_PLATFORM = {
    "darwin": "assets/icon.icns",
    "win32": "assets/icon.ico",
}

#: 実行中の OS で使うアイコン
ICON_RELATIVE = ICON_BY_PLATFORM.get(sys.platform, DEFAULT_ICON_RELATIVE)


def resource_root() -> Path:
    """同梱リソースの起点。

    PyInstaller の onefile は実行時に一時フォルダへ展開し、その場所を
    sys._MEIPASS に入れる。ソース実行時はリポジトリのルート。
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """同梱リソースの絶対パスを返す（存在するとは限らない）。"""
    return resource_root() / relative


def icon_path() -> Path | None:
    """アプリアイコンのパス。見つからなければ None。

    OS 向けのものが無ければ既定（.ico）へ落とす。片方しか同梱していない
    ビルドでもアイコン無しにはならないようにするため。
    """
    for relative in (ICON_RELATIVE, DEFAULT_ICON_RELATIVE):
        candidate = resource_path(relative)
        if candidate.is_file():
            return candidate
    return None
