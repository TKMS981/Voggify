"""同梱リソース（アイコン）のテスト。"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from voggify.resources import ICON_RELATIVE, icon_path, resource_path, resource_root

#: .ico に入っていてほしいサイズ（要求は 16/32/48/256）
REQUIRED_SIZES = {16, 32, 48, 256}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def read_ico_entries(path: Path) -> list[tuple[int, int, int]]:
    """(幅, 高さ, ビット深度) の一覧を返す。"""
    with open(path, "rb") as f:
        reserved, kind, count = struct.unpack("<HHH", f.read(6))
        assert reserved == 0 and kind == 1, "ICO のヘッダが不正"
        entries = []
        for _ in range(count):
            w, h, _colors, _res, _planes, bits, _size, _offset = struct.unpack(
                "<BBBBHHII", f.read(16)
            )
            entries.append((w or 256, h or 256, bits))
    return entries


def test_icon_file_exists():
    path = icon_path()
    assert path is not None, f"{ICON_RELATIVE} が見つかりません"
    assert path.is_file()


def test_icon_contains_the_required_sizes():
    entries = read_ico_entries(icon_path())
    sizes = {w for w, _h, _bits in entries}
    missing = REQUIRED_SIZES - sizes
    assert not missing, f"不足しているサイズ: {sorted(missing)}"


def test_icon_entries_are_square_and_32bit():
    for width, height, bits in read_ico_entries(icon_path()):
        assert width == height, f"正方形でない: {width}x{height}"
        assert bits == 32, f"{width}px が {bits}bit（32bit を期待）"


def test_icon_has_no_duplicate_sizes():
    sizes = [w for w, _h, _b in read_ico_entries(icon_path())]
    assert len(sizes) == len(set(sizes)), f"サイズが重複: {sizes}"


def test_source_png_is_large_enough():
    """256px 未満だと大アイコン表示で粗くなる。"""
    png = resource_path("assets/icon.png")
    assert png.is_file(), "元の PNG がありません"
    with open(png, "rb") as f:
        header = f.read(24)
    assert header[:8] == PNG_MAGIC
    width, height = struct.unpack(">II", header[16:24])
    assert width == height, f"正方形でない: {width}x{height}"
    assert min(width, height) >= 256, (
        f"元画像が {width}x{height} しかありません。"
        "256px 以上の原本を用意してください。"
    )


def test_resource_root_follows_frozen_state(monkeypatch):
    """PyInstaller で固めたときは展開先を見る。"""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    from_source = resource_root()
    assert (from_source / "voggify").is_dir()

    monkeypatch.setattr(sys, "_MEIPASS", r"C:\fake\_MEI123", raising=False)
    assert resource_root() == Path(r"C:\fake\_MEI123")


def test_icon_path_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert icon_path() is None


@pytest.mark.ffmpeg
def test_window_uses_the_icon(window):
    """QMainWindow にアイコンが設定されていること。"""
    assert not window.windowIcon().isNull(), "ウィンドウアイコンが空"
    sizes = window.windowIcon().availableSizes()
    assert sizes, "アイコンにサイズが無い"
    assert max(s.width() for s in sizes) >= 256
