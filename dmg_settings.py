# -*- coding: utf-8 -*-
"""dmgbuild の設定。macOS の配布用 dmg を組み立てる。

    dmgbuild -s dmg_settings.py "Voggify 0.5.0" dist/Voggify-0.5.0.dmg

ふつうは build_macos.sh から呼ぶ。単体で使う場合は先に
`pyinstaller voggify.spec` で dist/Voggify.app を作っておくこと。

dmgbuild を選んだ理由
---------------------
create-dmg は AppleScript で Finder を操作してアイコンを並べる。つまり
GUI セッションと自動化の許可が要り、CI や権限を絞った環境では失敗する。
dmgbuild は .DS_Store を直接書くので Finder を触らない。設定もこのファイル
1 枚に収まり、バージョン管理と相性が良い。

ウィンドウの寸法とアイコンの位置は assets/generate_dmg_background.py と
対になっている。片方だけ変えると背景と中身がずれるので注意。
"""

import os
from pathlib import Path

# dmgbuild はこのファイルを exec するだけなので __file__ が無い。
# リポジトリの場所は build_macos.sh が -D root=... で渡す。
_ROOT = Path(defines.get("root", os.getcwd())).resolve()  # noqa: F821

# --- 中身 -------------------------------------------------------------------
#: 同梱する .app。build_macos.sh から -D app=... で差し替えられる
application = defines.get("app", str(_ROOT / "dist" / "Voggify.app"))  # noqa: F821
_app_name = Path(application).name

files = [application]

#: ドラッグ先。実体は作らず /Applications への symlink を置く
symlinks = {"Applications": "/Applications"}

#: マウントしたボリュームのアイコン。アプリと同じものを使う
badge_icon = str(_ROOT / "assets" / "icon.icns")

# --- 見た目 -----------------------------------------------------------------
background = str(_ROOT / "assets" / "dmg_background.tiff")

#: ((左, 上), (幅, 高さ))。幅と高さは背景画像と同じでなければならない
window_rect = ((200, 200), (600, 400))

default_view = "icon-view"
icon_size = 128
text_size = 13
label_pos = "bottom"
arrange_by = None

#: アイコンの中心座標。generate_dmg_background.py の APP_CENTER / LINK_CENTER と揃える
icon_locations = {
    _app_name: (150, 185),
    "Applications": (450, 185),
}

# 配布物なので、余計な UI は畳んでおく
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
sidebar_width = 180
show_icon_preview = False

include_icon_view_settings = True
include_list_view_settings = False

# --- ディスクイメージ --------------------------------------------------------
#: UDZO = zlib 圧縮。読み取り専用で、配布用の標準的な形式
format = "UDZO"
compression_level = 9

#: 中身に合わせて自動で決めさせる（小さすぎるとビルドが落ちる）
size = None
