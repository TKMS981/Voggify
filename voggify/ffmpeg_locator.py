"""ffmpeg / ffprobe の探索と検証。

ラッパーライブラリは使わず、実行ファイルのパスを自前で解決する。
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import FFmpegNotFoundError

#: 明示的に ffmpeg / ffprobe の場所を指定するための環境変数
ENV_FFMPEG = "VOGGIFY_FFMPEG"
ENV_FFPROBE = "VOGGIFY_FFPROBE"

#: PATH に無い場合に探索する典型的なインストール先。
#: `*` を含む要素は glob として展開する（winget のポータブル配置対策）。
_WINDOWS_HINTS = (
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
    # winget は実体を Packages\<パッケージ>\<展開先>\bin に置き、
    # PATH への追加は次回シェル起動まで反映されない
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*[Ff][Ff]mpeg*\*\bin"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*[Ff][Ff]mpeg*\bin"),
    os.path.expandvars(r"%USERPROFILE%\scoop\shims"),
    os.path.expandvars(r"%USERPROFILE%\scoop\apps\ffmpeg\current\bin"),
)
_POSIX_HINTS = (
    "/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/snap/bin",
)

#: 変換中にコンソールウィンドウを出さないための Windows 用フラグ
CREATE_NO_WINDOW = 0x08000000


def subprocess_flags() -> dict[str, object]:
    """subprocess 呼び出しに共通で渡すプラットフォーム依存オプション。"""
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


@dataclass(frozen=True)
class FFmpegTools:
    """検出済みの ffmpeg / ffprobe 一式。"""

    ffmpeg: str
    ffprobe: str
    version: str
    has_libvorbis: bool

    def describe(self) -> str:
        vorbis = "libvorbis 利用可" if self.has_libvorbis else "libvorbis 見つからず"
        return f"{self.version} ({vorbis})\n  ffmpeg : {self.ffmpeg}\n  ffprobe: {self.ffprobe}"


def _candidate_dirs() -> list[str]:
    """探索対象フォルダを列挙する（glob パターンはここで展開する）。"""
    hints = _WINDOWS_HINTS if sys.platform == "win32" else _POSIX_HINTS
    directories: list[str] = []
    for hint in hints:
        if not hint:
            continue
        if "*" in hint or "?" in hint:
            directories.extend(sorted(glob.glob(hint), reverse=True))  # 新しい版を優先
        else:
            directories.append(hint)
    return directories


def _find_executable(name: str, env_var: str) -> str | None:
    """PATH → 環境変数 → 既知のインストール先 の順に実行ファイルを探す。"""
    override = os.environ.get(env_var)
    if override:
        candidate = Path(override)
        # ディレクトリを指定された場合はその中を見る
        if candidate.is_dir():
            found = shutil.which(name, path=str(candidate))
            if found:
                return found
        elif candidate.is_file():
            return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    for directory in _candidate_dirs():
        if not directory or not os.path.isdir(directory):
            continue
        found = shutil.which(name, path=directory)
        if found:
            return found
    return None


def _run_capture(argv: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    """テキストモードでプロセスを実行し、結果をそのまま返す。"""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **subprocess_flags(),  # type: ignore[arg-type]
    )


def _probe_version(ffmpeg_path: str) -> tuple[str, bool]:
    """`ffmpeg -version` を叩いてバージョン文字列と libvorbis の有無を得る。"""
    try:
        result = _run_capture([ffmpeg_path, "-hide_banner", "-version"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise FFmpegNotFoundError(
            f"ffmpeg を実行できませんでした: {ffmpeg_path}\n  {exc}"
        ) from exc

    if result.returncode != 0:
        raise FFmpegNotFoundError(
            f"ffmpeg の実行に失敗しました（終了コード {result.returncode}）: {ffmpeg_path}"
        )

    output = result.stdout or ""
    match = re.search(r"ffmpeg version (\S+)", output)
    version = f"ffmpeg {match.group(1)}" if match else "ffmpeg (バージョン不明)"
    has_libvorbis = "--enable-libvorbis" in output
    if not has_libvorbis:
        has_libvorbis = _has_libvorbis_encoder(ffmpeg_path)
    return version, has_libvorbis


def _has_libvorbis_encoder(ffmpeg_path: str) -> bool:
    """`ffmpeg -encoders` に libvorbis があるか確認する（ビルド情報が無い場合の保険）。"""
    try:
        result = _run_capture([ffmpeg_path, "-hide_banner", "-encoders"])
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "libvorbis" in (result.stdout or "")


_cached_tools: FFmpegTools | None = None


def find_ffmpeg_tools(force_refresh: bool = False) -> FFmpegTools | None:
    """ffmpeg / ffprobe を探して返す。見つからなければ None。

    結果はプロセス内でキャッシュする（起動のたびに探索しないため）。
    """
    global _cached_tools
    if _cached_tools is not None and not force_refresh:
        return _cached_tools

    ffmpeg_path = _find_executable("ffmpeg", ENV_FFMPEG)
    ffprobe_path = _find_executable("ffprobe", ENV_FFPROBE)
    if not ffmpeg_path or not ffprobe_path:
        return None

    version, has_libvorbis = _probe_version(ffmpeg_path)
    _cached_tools = FFmpegTools(
        ffmpeg=ffmpeg_path,
        ffprobe=ffprobe_path,
        version=version,
        has_libvorbis=has_libvorbis,
    )
    return _cached_tools


def ensure_ffmpeg_tools(force_refresh: bool = False) -> FFmpegTools:
    """ffmpeg / ffprobe を取得する。無ければ案内付きの例外を送出。"""
    tools = find_ffmpeg_tools(force_refresh=force_refresh)
    if tools is None:
        raise FFmpegNotFoundError(missing_ffmpeg_message())
    if not tools.has_libvorbis:
        raise FFmpegNotFoundError(
            "この ffmpeg は libvorbis エンコーダーを含んでいないため、"
            "OGG Vorbis へ変換できません。\n"
            "libvorbis 付きのビルド（公式配布版など）を利用してください。\n"
            f"  検出したffmpeg: {tools.ffmpeg}"
        )
    return tools


def missing_ffmpeg_message() -> str:
    """ffmpeg 未検出時にユーザーへ表示する案内文。"""
    lines = [
        "ffmpeg / ffprobe が見つかりませんでした。Voggify の変換には ffmpeg が必要です。",
        "",
        "インストール方法:",
    ]
    if sys.platform == "win32":
        lines += [
            "  winget install Gyan.FFmpeg",
            "  （または https://www.gyan.dev/ffmpeg/builds/ から入手して bin フォルダを PATH に追加）",
        ]
    elif sys.platform == "darwin":
        lines += ["  brew install ffmpeg"]
    else:
        lines += ["  sudo apt install ffmpeg   （Debian/Ubuntu 系）"]
    lines += [
        "",
        "インストール済みなのに検出されない場合は、環境変数で実行ファイルを直接指定できます:",
        f"  {ENV_FFMPEG}=<ffmpeg の実行ファイル or bin フォルダ>",
        f"  {ENV_FFPROBE}=<ffprobe の実行ファイル or bin フォルダ>",
    ]
    return "\n".join(lines)
