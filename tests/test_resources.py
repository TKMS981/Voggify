"""同梱リソース（アイコン）のテスト。"""

from __future__ import annotations

import io
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from voggify.resources import (
    DEFAULT_ICON_RELATIVE,
    ICON_BY_PLATFORM,
    ICON_RELATIVE,
    icon_path,
    resource_path,
    resource_root,
)

#: .ico に入っていてほしいサイズ（要求は 16/32/48/256）
REQUIRED_SIZES = {16, 32, 48, 256}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: .icns に入っていてほしい OSType。16x16 から 512x512@2x（1024px）まで。
#: 小さい 2 つは iconutil が PNG ではなく ARGB（ic04 / ic05）で書く。
REQUIRED_ICNS_TYPES = {
    "ic04": "16x16",
    "ic11": "16x16@2x",
    "ic05": "32x32",
    "ic12": "32x32@2x",
    "ic07": "128x128",
    "ic13": "128x128@2x",
    "ic08": "256x256",
    "ic14": "256x256@2x",
    "ic09": "512x512",
    "ic10": "512x512@2x",
}


def ico_file() -> Path:
    """OS に関係なく .ico そのものを見る（成果物の検査なので）。"""
    return resource_path(DEFAULT_ICON_RELATIVE)


def icns_file() -> Path:
    return resource_path("assets/icon.icns")


def read_icns_entries(path: Path) -> dict[str, int]:
    """.icns に入っている OSType -> データ長 を返す。"""
    data = path.read_bytes()
    magic, declared = struct.unpack(">4sI", data[:8])
    assert magic == b"icns", "icns のヘッダが不正"
    assert declared == len(data), f"宣言長 {declared} と実長 {len(data)} が不一致"

    entries: dict[str, int] = {}
    offset = 8
    while offset < len(data):
        ostype, size = struct.unpack(">4sI", data[offset : offset + 8])
        assert size >= 8, f"不正なチャンク長: {size}"
        entries[ostype.decode("ascii", "replace")] = size
        offset += size
    return entries


def read_icns_pngs(path: Path) -> dict[str, "Image.Image"]:
    """.icns の中の PNG エントリを OSType -> 画像 で返す。

    一番小さい 2 つ（ic04 / ic05）は iconutil が ARGB で書くので PNG では
    取り出せない。そちらは macOS 限定のテストで iconutil に戻して見る。
    """
    from PIL import Image

    data = path.read_bytes()
    images: dict[str, Image.Image] = {}
    offset = 8
    while offset < len(data):
        ostype, size = struct.unpack(">4sI", data[offset : offset + 8])
        blob = data[offset + 8 : offset + size]
        if blob[:8] == PNG_MAGIC:
            name = ostype.decode("ascii", "replace")
            images[name] = Image.open(io.BytesIO(blob)).convert("RGBA")
        offset += size
    return images


def alpha_report(image: "Image.Image") -> tuple[int, int]:
    """(四隅のアルファの最大値, 中央のアルファ) を返す。"""
    width, height = image.size
    pixels = image.load()
    corners = [
        pixels[0, 0][3],
        pixels[width - 1, 0][3],
        pixels[0, height - 1][3],
        pixels[width - 1, height - 1][3],
    ]
    return max(corners), pixels[width // 2, height // 2][3]


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


def test_icon_path_prefers_the_native_format():
    """OS ごとに native なアイコンを返す（macOS は .icns）。"""
    expected = ICON_BY_PLATFORM.get(sys.platform, DEFAULT_ICON_RELATIVE)
    assert icon_path() == resource_path(expected)


def test_icon_contains_the_required_sizes():
    entries = read_ico_entries(ico_file())
    sizes = {w for w, _h, _bits in entries}
    missing = REQUIRED_SIZES - sizes
    assert not missing, f"不足しているサイズ: {sorted(missing)}"


def test_icon_entries_are_square_and_32bit():
    for width, height, bits in read_ico_entries(ico_file()):
        assert width == height, f"正方形でない: {width}x{height}"
        assert bits == 32, f"{width}px が {bits}bit（32bit を期待）"


def test_icon_has_no_duplicate_sizes():
    sizes = [w for w, _h, _b in read_ico_entries(ico_file())]
    assert len(sizes) == len(set(sizes)), f"サイズが重複: {sizes}"


# ---------------------------------------------------------------------------
# .icns（macOS）
# ---------------------------------------------------------------------------
def test_icns_file_exists():
    assert icns_file().is_file(), (
        "assets/icon.icns がありません。"
        "python assets/generate_icon.py で生成してください。"
    )


def test_icns_contains_every_size_from_16_to_1024():
    """16x16 〜 512x512@2x(1024px) の @1x/@2x が揃っていること。"""
    entries = read_icns_entries(icns_file())
    missing = {
        label for ostype, label in REQUIRED_ICNS_TYPES.items() if ostype not in entries
    }
    assert not missing, f"不足している解像度: {sorted(missing)}"


def test_icns_large_entries_are_png():
    """大きい方は PNG で入っていて、実解像度が名前と一致すること。"""
    data = icns_file().read_bytes()
    expected_px = {
        "ic07": 128, "ic13": 256, "ic08": 256,
        "ic14": 512, "ic09": 512, "ic10": 1024,
    }
    offset, checked = 8, 0
    while offset < len(data):
        ostype, size = struct.unpack(">4sI", data[offset : offset + 8])
        name = ostype.decode("ascii", "replace")
        blob = data[offset + 8 : offset + size]
        if name in expected_px:
            assert blob[:8] == PNG_MAGIC, f"{name} が PNG でない"
            width, height = struct.unpack(">II", blob[16:24])
            assert (width, height) == (expected_px[name], expected_px[name]), (
                f"{name} が {width}x{height}（{expected_px[name]}px を期待）"
            )
            checked += 1
        offset += size
    assert checked == len(expected_px), "PNG の解像度を確認できなかった"


def test_icns_corners_are_transparent():
    """macOS は自動でマスクをかけないので、角丸と透明は画像側に要る。

    iOS と違って OS 側のマスクが無い。四隅が不透明だと Dock で
    四角いタイルとして表示されてしまう。
    """
    images = read_icns_pngs(icns_file())
    assert images, "PNG エントリが 1 つも取り出せない"
    for name, image in sorted(images.items()):
        corner, center = alpha_report(image)
        assert corner == 0, f"{name}（{image.size[0]}px）の四隅が透明でない: α={corner}"
        assert center == 255, f"{name}（{image.size[0]}px）の中央が不透明でない: α={center}"


def test_icns_follows_the_macos_content_ratio():
    """実体がキャンバスに占める割合が macOS の慣例どおりであること。

    Apple 純正アイコンの実測値は 79.5%（1024 中 814）。小さい解像度は
    アンチエイリアスの分だけ下振れするので幅を持たせる。
    """
    images = read_icns_pngs(icns_file())
    for name, image in sorted(images.items()):
        width = image.size[0]
        if width < 128:
            continue  # 小さすぎて縁の判定が粗くなる
        solid = image.getchannel("A").point(lambda v: 255 if v > 250 else 0)
        box = solid.getbbox()
        assert box is not None, f"{name} に不透明部分が無い"
        ratio = (box[2] - box[0]) / width
        assert 0.75 <= ratio <= 0.83, (
            f"{name}（{width}px）の占有率が {ratio:.1%}（75〜83% を期待）"
        )


def test_ico_stays_a_fully_opaque_square():
    """Windows 側には角丸マスクをかけない。

    .icns の加工が .ico に波及していないことの歯止め。Windows の
    タスクバーはマスクをかけないので、正方形のままでよい。
    """
    from PIL import Image

    for size in (16, 256):
        with Image.open(ico_file()) as source:
            source.size = (size, size)
            image = source.convert("RGBA")
        corner, center = alpha_report(image)
        assert corner == 255, f".ico の {size}px が透明になっている: α={corner}"
        assert center == 255


@pytest.mark.skipif(sys.platform != "darwin", reason="iconutil は macOS にしか無い")
def test_icns_round_trips_to_ten_transparent_sizes(tmp_path):
    """iconutil で戻して、10 解像度すべてで透明が効いていることを確かめる。

    ic04 / ic05（16x16 と 32x32 の @1x）は ARGB で入っていて PNG として
    読めないので、macOS でだけこの経路で確認する。
    """
    from PIL import Image

    iconset = tmp_path / "check.iconset"
    result = subprocess.run(
        ["iconutil", "-c", "iconset", str(icns_file()), "-o", str(iconset)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"iconutil に失敗: {result.stderr}"

    frames = sorted(iconset.glob("*.png"))
    assert len(frames) == 10, f"取り出せたのが {len(frames)} 枚（10 枚を期待）"
    for frame in frames:
        with Image.open(frame) as source:
            corner, center = alpha_report(source.convert("RGBA"))
        assert corner == 0, f"{frame.name} の四隅が透明でない: α={corner}"
        assert center == 255, f"{frame.name} の中央が不透明でない: α={center}"


def test_source_png_is_large_enough_for_icns():
    """.icns には 1024px が入るので、元画像もそれ以上必要。"""
    png = resource_path("assets/icon.png")
    with open(png, "rb") as f:
        header = f.read(24)
    width, height = struct.unpack(">II", header[16:24])
    assert min(width, height) >= 1024, (
        f"元画像が {width}x{height} しかありません。1024px 以上が必要です。"
    )


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
