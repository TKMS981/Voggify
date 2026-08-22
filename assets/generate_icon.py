"""assets/icon.png から Windows 用の assets/icon.ico を作る。

    python assets/generate_icon.py

生成した .ico はリポジトリに含めてあるので、通常は実行しなくてよい。
元の PNG を差し替えたときだけ実行する。

Pillow を使う理由
-----------------
.ico は複数サイズを 1 つにまとめる形式で、Pillow の `save(format="ICO")`
はそれを 1 行で扱える。PySide6（QImage）でも 1 枚だけの .ico なら書けるが、
マルチサイズをまとめる口が無い。Pillow はこのスクリプトを走らせるときだけ
必要で、アプリの実行時には使わない（requirements-dev.txt に入れている）。
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Pillow が必要です:  pip install -r requirements-dev.txt", file=sys.stderr)
    raise SystemExit(1)

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "icon.png"
TARGET = HERE / "icon.ico"

#: .ico に入れるサイズ。Windows はエクスプローラーの表示倍率に応じて
#: この中から近いものを選ぶ。256 が無いと大アイコン表示で粗くなる。
SIZES = (16, 24, 32, 48, 64, 128, 256)

#: これを下回る元画像は拡大が必要になり、輪郭が荒れる
MIN_SOURCE_PX = 256


def main() -> int:
    if not SOURCE.is_file():
        print(f"元画像がありません: {SOURCE}", file=sys.stderr)
        return 1

    image = Image.open(SOURCE)
    width, height = image.size
    print(f"元画像: {SOURCE.name}  {width}x{height}  mode={image.mode}")

    if width != height:
        print(
            f"  警告: 正方形ではありません（{width}x{height}）。"
            "アイコンが歪む可能性があります。",
            file=sys.stderr,
        )

    shortest = min(width, height)
    if shortest < MIN_SOURCE_PX:
        print(
            f"  警告: 元画像が {shortest}px しかありません。"
            f"{MIN_SOURCE_PX}px 以上の原本を用意してください。\n"
            "  拡大して埋めることもできますが、輪郭がぼやけるため行いません。",
            file=sys.stderr,
        )
        return 2

    if image.mode != "RGBA":
        print(
            f"  補足: アルファチャンネルがありません（{image.mode}）。"
            "背景は不透明のまま .ico にします。"
        )
    image = image.convert("RGBA")

    # 各サイズを LANCZOS で作る。Pillow の ICO 保存に任せると縮小方法が
    # 版によって変わりうるので、こちらで明示的に作って渡す。
    frames = [image.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES]
    largest = frames[-1]
    largest.save(
        TARGET,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=frames[:-1],
    )

    print(f"\n生成: {TARGET.name}  {TARGET.stat().st_size:,} bytes")
    print(f"  収録サイズ: {', '.join(f'{s}x{s}' for s in SIZES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
