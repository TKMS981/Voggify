"""dmg の背景画像 assets/dmg_background.tiff を作る。

    python assets/generate_dmg_background.py

生成した .tiff はリポジトリに含めてあるので、通常は実行しなくてよい。
配色や文言を変えたときだけ実行する。

Retina 対応
-----------
Finder は背景画像を「論理サイズ」で表示するので、Retina では 2 倍の絵が要る。
macOS の作法は、等倍と 2 倍の画像を 1 つの TIFF にまとめて
`tiffutil -cathidpicheck` で「HiDPI 付き」と印を付けること。これを使うと
Finder が表示倍率に応じて中の適切な方を選ぶ。

配色は assets/icon.png から採ったもので揃えている。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    print("Pillow が必要です:  pip install -r requirements-dev.txt", file=sys.stderr)
    raise SystemExit(1)

HERE = Path(__file__).resolve().parent
TARGET = HERE / "dmg_background.tiff"

#: dmg のウィンドウの内寸。dmg_settings.py の window_rect と必ず揃えること
WIDTH = 600
HEIGHT = 400

#: assets/icon.png から採った配色
CREAM = (245, 233, 211)
BROWN = (43, 34, 28)
ORANGE = (215, 104, 65)

#: アイコンを置く位置（中心）。dmg_settings.py の icon_locations と揃える
APP_CENTER = (150, 185)
LINK_CENTER = (450, 185)

#: 日本語が出るフォント。無ければ英字だけのフォントへ落とす
FONT_CANDIDATES = (
    ("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", 0),
    ("/System/Library/Fonts/ヒラギノ角ゴシック W5.ttc", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """使えるフォントを順に試す。どれも無ければ既定のビットマップフォント。"""
    for path, index in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    print("  警告: 日本語フォントが見つかりません。既定フォントで描きます。", file=sys.stderr)
    return ImageFont.load_default()


def _centered(draw: ImageDraw.ImageDraw, text: str, font, y: int, fill, width: int) -> None:
    """y を上端として、横方向に中央揃えで文字を描く。"""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (right - left)) / 2 - left, y - top), text, font=font, fill=fill)


def _draw_arrow(draw: ImageDraw.ImageDraw, scale: int) -> None:
    """2 つのアイコンの間に「→」を描く。"""
    y = APP_CENTER[1] * scale
    x0 = (APP_CENTER[0] + 95) * scale
    x1 = (LINK_CENTER[0] - 95) * scale
    shaft = 5 * scale
    head = 17 * scale

    # 軸
    draw.rounded_rectangle(
        [x0, y - shaft // 2, x1 - head, y + shaft // 2],
        radius=shaft // 2,
        fill=ORANGE,
    )
    # 矢じり
    draw.polygon(
        [(x1, y), (x1 - head, y - head * 0.62), (x1 - head, y + head * 0.62)],
        fill=ORANGE,
    )


def _render(scale: int) -> Image.Image:
    """等倍（scale=1）または 2 倍（scale=2）の背景を描く。"""
    width, height = WIDTH * scale, HEIGHT * scale
    image = Image.new("RGB", (width, height), CREAM)
    draw = ImageDraw.Draw(image)

    # 上下に細い帯を入れて、アイコンの帯と視覚的に揃える
    draw.rectangle([0, 0, width, 6 * scale], fill=BROWN)
    draw.rectangle([0, height - 6 * scale, width, height], fill=BROWN)

    title = _load_font(30 * scale)
    subtitle = _load_font(14 * scale)
    caption = _load_font(15 * scale)

    _centered(draw, "Voggify", title, 46 * scale, BROWN, width)
    _centered(
        draw,
        "音楽・動画の音声を OGG Vorbis / MP3 に変換",
        subtitle,
        88 * scale,
        BROWN,
        width,
    )

    _draw_arrow(draw, scale)

    _centered(
        draw,
        "Voggify を Applications フォルダにドラッグしてください",
        caption,
        300 * scale,
        BROWN,
        width,
    )
    _centered(
        draw,
        "初回起動は右クリック →「開く」（未署名のため）",
        subtitle,
        330 * scale,
        ORANGE,
        width,
    )
    return image


def main() -> int:
    print(f"背景を描きます: {WIDTH}x{HEIGHT}（等倍）と {WIDTH * 2}x{HEIGHT * 2}（2 倍）")

    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        singles = []
        for scale in (1, 2):
            path = work_dir / f"background@{scale}x.png"
            _render(scale).save(path, format="PNG")
            singles.append(path)

        tiffutil = shutil.which("tiffutil")
        if not tiffutil:
            # macOS 以外。等倍だけの TIFF にしておく（Retina では粗くなる）
            print("  補足: tiffutil が無いため等倍だけの TIFF にします（macOS 以外）。")
            _render(1).save(TARGET, format="TIFF")
        else:
            result = subprocess.run(
                [tiffutil, "-cathidpicheck", *map(str, singles), "-out", str(TARGET)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(
                    "  tiffutil に失敗しました:\n  " + (result.stderr or "").strip(),
                    file=sys.stderr,
                )
                return 1

    print(f"\n生成: {TARGET.name}  {TARGET.stat().st_size:,} bytes")
    print(f"  ウィンドウ内寸 {WIDTH}x{HEIGHT} / アイコン中心 {APP_CENTER} と {LINK_CENTER}")
    print("  （dmg_settings.py の window_rect・icon_locations と揃えること）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
