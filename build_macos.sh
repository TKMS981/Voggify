#!/bin/bash
#
# macOS 向けのビルド。.app を作って dmg に包むまでを一気にやる。
#
#   ./build_macos.sh              # dist/Voggify.app と dist/Voggify-<version>.dmg
#   ./build_macos.sh --clean      # PyInstaller のキャッシュを捨ててから
#   ./build_macos.sh --app-only   # .app まで（dmg を作らない）
#
# 事前に必要なもの:
#   pip install -r requirements.txt -r requirements-dev.txt
#
# バージョンは voggify/__init__.py の __version__ が唯一の出どころ。
# ここでは読み取るだけで、どこにも書かない。

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if [ -x "venv/bin/python" ]; then
        PYTHON="venv/bin/python"
    elif [ -x ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    else
        PYTHON="python3"
    fi
fi

CLEAN=""
APP_ONLY=""
for arg in "$@"; do
    case "$arg" in
        --clean)    CLEAN="--clean" ;;
        --app-only) APP_ONLY="1" ;;
        -h|--help)  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "不明な引数: $arg" >&2; exit 2 ;;
    esac
done

if [ "$(uname -s)" != "Darwin" ]; then
    echo "エラー: このスクリプトは macOS 用です（PyInstaller はクロスビルドできません）。" >&2
    exit 1
fi

VERSION="$($PYTHON -c 'from voggify import __version__; print(__version__)')"
APP="dist/Voggify.app"
DMG="dist/Voggify-${VERSION}.dmg"

echo "==> Voggify ${VERSION} を ${PYTHON} でビルドします"

# --- 1. アイコン ------------------------------------------------------------
# 生成物はコミットしてあるので、無いときだけ作る
if [ ! -f assets/icon.icns ] || [ ! -f assets/dmg_background.tiff ]; then
    echo "==> アイコン／背景を生成します"
    [ -f assets/icon.icns ]           || $PYTHON assets/generate_icon.py
    [ -f assets/dmg_background.tiff ] || $PYTHON assets/generate_dmg_background.py
fi

# --- 2. .app ----------------------------------------------------------------
echo "==> PyInstaller で .app を作ります"
$PYTHON -m PyInstaller $CLEAN --noconfirm voggify.spec

if [ ! -d "$APP" ]; then
    echo "エラー: $APP ができていません。" >&2
    exit 1
fi
echo "    $APP  ($(du -sh "$APP" | cut -f1))"

if [ -n "$APP_ONLY" ]; then
    echo "==> --app-only なのでここまで"
    exit 0
fi

# --- 3. dmg -----------------------------------------------------------------
echo "==> dmg に包みます"

# 前回の残りがあると dmgbuild が止まるので先に消す
rm -f "$DMG"

# 同名のボリュームがマウントされたままだと失敗する
if [ -d "/Volumes/Voggify ${VERSION}" ]; then
    echo "    前回のボリュームがマウントされたままなので外します"
    hdiutil detach "/Volumes/Voggify ${VERSION}" -quiet || true
fi

$PYTHON -m dmgbuild \
    -s dmg_settings.py \
    -D root="$PWD" \
    -D app="$PWD/$APP" \
    "Voggify ${VERSION}" \
    "$DMG"

echo ""
echo "==> 完成"
echo "    $DMG  ($(du -sh "$DMG" | cut -f1))"
echo ""
echo "    配布前に、マウントして中身と見た目を確認すること:"
echo "      open \"$DMG\""
