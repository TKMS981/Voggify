"""assets/icon.png から assets/icon.ico（Windows）と assets/icon.icns（macOS）を作る。

    python assets/generate_icon.py

生成物はリポジトリに含めてあるので、通常は実行しなくてよい。
元の PNG を差し替えたときだけ実行する。

Pillow を使う理由
-----------------
.ico は複数サイズを 1 つにまとめる形式で、Pillow の `save(format="ICO")`
はそれを 1 行で扱える。PySide6（QImage）でも 1 枚だけの .ico なら書けるが、
マルチサイズをまとめる口が無い。Pillow はこのスクリプトを走らせるときだけ
必要で、アプリの実行時には使わない（requirements-dev.txt に入れている）。

.icns の作り方
--------------
macOS では .iconset フォルダに規定の名前で PNG を並べ、Apple 純正の
`iconutil -c icns` でまとめるのが正攻法。Retina 用の @2x まで含めた
組み合わせが決まっているので、それに従う。iconutil が無い環境
（macOS 以外）では Pillow の ICNS 保存へ落とすが、収録されるサイズは
iconutil 版と一致しない場合がある。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Pillow が必要です:  pip install -r requirements-dev.txt", file=sys.stderr)
    raise SystemExit(1)

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "icon.png"
TARGET = HERE / "icon.ico"
ICNS_TARGET = HERE / "icon.icns"

#: .ico に入れるサイズ。Windows はエクスプローラーの表示倍率に応じて
#: この中から近いものを選ぶ。256 が無いと大アイコン表示で粗くなる。
SIZES = (16, 24, 32, 48, 64, 128, 256)

#: これを下回る元画像は拡大が必要になり、輪郭が荒れる
MIN_SOURCE_PX = 256

#: .icns に入れる (論理サイズ, 倍率)。macOS の .iconset の規定どおり。
#: 実ピクセルは 論理サイズ x 倍率 で、16x16 から 1024x1024 までを覆う。
ICNS_SIZES = (
    (16, 1), (16, 2),
    (32, 1), (32, 2),
    (128, 1), (128, 2),
    (256, 1), (256, 2),
    (512, 1), (512, 2),
)

#: .icns は 1024x1024（512@2x）まで入るので、元画像もそれ以上が望ましい
MIN_ICNS_SOURCE_PX = 1024


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

    return _write_icns(image, shortest)


def _iconset_name(logical: int, scale: int) -> str:
    """.iconset の中でのファイル名。iconutil はこの命名しか受け付けない。"""
    suffix = "" if scale == 1 else f"@{scale}x"
    return f"icon_{logical}x{logical}{suffix}.png"


def _write_icns(image: "Image.Image", shortest: int) -> int:
    """.iconset を組み立てて .icns にまとめる。"""
    print(f"\n--- {ICNS_TARGET.name} ---")
    if shortest < MIN_ICNS_SOURCE_PX:
        print(
            f"  警告: 元画像が {shortest}px しかありません。"
            f".icns には 1024x1024 が入るため {MIN_ICNS_SOURCE_PX}px 以上を推奨します。",
            file=sys.stderr,
        )

    with tempfile.TemporaryDirectory() as work:
        iconset = Path(work) / "icon.iconset"
        iconset.mkdir()
        for logical, scale in ICNS_SIZES:
            pixels = logical * scale
            frame = image.resize((pixels, pixels), Image.Resampling.LANCZOS)
            frame.save(iconset / _iconset_name(logical, scale), format="PNG")

        iconutil = shutil.which("iconutil")
        if iconutil:
            result = subprocess.run(
                [iconutil, "-c", "icns", str(iconset), "-o", str(ICNS_TARGET)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(
                    "  iconutil に失敗しました:\n  "
                    + (result.stderr or "").strip(),
                    file=sys.stderr,
                )
                return 3
            method = "iconutil（Apple 純正）"
        else:
            # macOS 以外。収録サイズは Pillow の実装依存になる
            print("  補足: iconutil が無いため Pillow で書き出します（macOS 以外）。")
            image.resize((1024, 1024), Image.Resampling.LANCZOS).save(
                ICNS_TARGET, format="ICNS"
            )
            method = "Pillow（ICNS 保存）"

    print(f"生成: {ICNS_TARGET.name}  {ICNS_TARGET.stat().st_size:,} bytes")
    print(f"  方法: {method}")
    print(
        "  収録サイズ: "
        + ", ".join(
            f"{logical}x{logical}"
            + ("" if scale == 1 else f"@{scale}x")
            + f"({logical * scale}px)"
            for logical, scale in ICNS_SIZES
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
