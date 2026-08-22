"""同梱リソース（アイコンなど）の場所を解決する。

ソースから動かす場合はリポジトリの assets/ を、PyInstaller で固めた場合は
展開先（sys._MEIPASS）を見る。ここだけが frozen かどうかを気にする。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: アプリのアイコン（リポジトリからの相対パス）
ICON_RELATIVE = "assets/icon.ico"


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
    """アプリアイコンのパス。見つからなければ None。"""
    candidate = resource_path(ICON_RELATIVE)
    return candidate if candidate.is_file() else None
