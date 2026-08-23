# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller のビルド設定。

    pyinstaller voggify.spec           # dist/Voggify.exe ができる
    pyinstaller --clean voggify.spec   # キャッシュを捨ててビルドし直す

ffmpeg / ffprobe は同梱しない。ユーザー環境にインストールされたものを
voggify/ffmpeg_locator.py が実行時に探す（PATH → 環境変数 → 既知の場所）。
その探索は PATH と環境変数だけを見ており、__file__ や sys._MEIPASS に
依存しないため、exe 化しても挙動は変わらない。
"""

import sys
from pathlib import Path

APP_NAME = "Voggify"

# --- バージョン -------------------------------------------------------------
# 唯一の出どころは voggify/__init__.py。ここで読み取って Windows の
# バージョンリソースに埋め込み、voggify.iss はその exe から読み取る。
# こうしておけば __init__.py の 1 行を変えるだけで全部が追従する。
_ROOT = Path(SPECPATH)


def _read_app_version() -> str:
    """voggify/__init__.py から __version__ を取り出す（import はしない）。

    Windows のエディタが BOM を付けることがあるので utf-8-sig で読む。
    """
    namespace: dict = {}
    source = (_ROOT / "voggify" / "__init__.py").read_text(encoding="utf-8-sig")
    exec(compile(source, "voggify/__init__.py", "exec"), namespace)
    return namespace["__version__"]


APP_VERSION = _read_app_version()


def _write_version_resource() -> str:
    """PyInstaller に渡すバージョンリソース定義を書き出す。"""
    numbers = [int(part) for part in APP_VERSION.split(".")]
    while len(numbers) < 4:
        numbers.append(0)
    quad = tuple(numbers[:4])

    # 言語は en-US / Unicode（040904B0）。Inno Setup の GetStringFileInfo が
    # 既定で参照する組み合わせに合わせている。
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Voggify'),
          StringStruct('FileDescription', 'Voggify - 音楽・動画の音声を OGG Vorbis / MP3 に変換'),
          StringStruct('FileVersion', '{APP_VERSION}'),
          StringStruct('InternalName', '{APP_NAME}'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('OriginalFilename', '{APP_NAME}.exe'),
          StringStruct('ProductName', 'Voggify'),
          StringStruct('ProductVersion', '{APP_VERSION}'),
        ],
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""
    build_dir = _ROOT / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    target = build_dir / "version_info.txt"
    target.write_text(content, encoding="utf-8")
    return str(target)


VERSION_FILE = _write_version_resource()
print(f"[voggify.spec] バージョン {APP_VERSION} をビルドします")

# --- アイコン ---------------------------------------------------------------
# exe に埋め込むアイコン。None にすると PyInstaller の既定アイコンになる。
# 元画像は assets/icon.png で、assets/generate_icon.py が .ico を作る。
ICON_PATH = "assets/icon.ico"

_icon = None
if ICON_PATH:
    _candidate = _ROOT / ICON_PATH
    if _candidate.is_file():
        _icon = str(_candidate.resolve())
    else:
        print(f"[voggify.spec] 警告: アイコンが見つかりません: {_candidate}")

# --- 取り込まないもの -------------------------------------------------------
# 使っていない Qt モジュールを外して exe を小さくする。
# ここに足すときは、外した後に必ず起動確認すること。
EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    # QtMultimedia はプレビュー再生に使うので外さないこと
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    # shiboken6 は PySide6 の中核なので絶対に除外しないこと
]


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # アイコンは exe に埋め込むだけでなく、実行時にも読む
    # （setWindowIcon 用。voggify/resources.py が sys._MEIPASS から探す）
    datas=[(str(_ROOT / "assets" / "icon.ico"), "assets")] if _icon else [],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX はウイルス対策ソフトの誤検知を招きやすいので使わない
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # コンソールウィンドウを出さない（--noconsole 相当）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version=VERSION_FILE,
)
