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
from collections.abc import Iterator
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
    # Homebrew の ffmpeg-full / ffmpeg@N は keg-only で PATH に入らない。
    # 素の ffmpeg には libvorbis が無いことがあるので、こちらも候補に入れる
    # （新しい版を優先したいので glob 展開に任せる）。
    "/opt/homebrew/opt/ffmpeg*/bin",
    "/usr/local/opt/ffmpeg*/bin",
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
    #: 使えるエンコーダー名の集合（libvorbis / libmp3lame など）
    encoders: frozenset[str] = frozenset()

    @property
    def has_libvorbis(self) -> bool:
        return "libvorbis" in self.encoders

    @property
    def has_libmp3lame(self) -> bool:
        return "libmp3lame" in self.encoders

    def supports(self, output_format) -> bool:  # noqa: ANN001 - 循環 import を避ける
        """その出力形式でエンコードできるか。"""
        return output_format.encoder in self.encoders

    def describe(self) -> str:
        found = "、".join(sorted(self.encoders)) if self.encoders else "エンコーダー見つからず"
        return (
            f"{self.version} ({found})\n"
            f"  ffmpeg : {self.ffmpeg}\n  ffprobe: {self.ffprobe}"
        )


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


def _iter_executables(name: str, env_var: str) -> Iterator[str]:
    """実行ファイルの候補を優先順に返す（環境変数 → PATH → 既知の場所）。

    遅延評価にしてあるので、先頭の候補で用が足りるうちは
    _candidate_dirs() の glob 展開もディスクアクセスも走らない。

    環境変数で明示指定された場合は「それを使え」という意思表示なので、
    そこで打ち切って他の候補は出さない。
    """
    override = os.environ.get(env_var)
    if override:
        candidate = Path(override)
        # ディレクトリを指定された場合はその中を見る
        explicit = (
            shutil.which(name, path=str(candidate))
            if candidate.is_dir()
            else (str(candidate) if candidate.is_file() else None)
        )
        if explicit:
            yield explicit
            return

    # 実体で重複を除く。Homebrew の /opt/homebrew/bin と
    # /opt/homebrew/opt/ffmpeg/bin のように、同じ実行ファイルへの
    # 別名を二度叩かないため。
    seen: set[str] = set()

    def fresh(path: str | None) -> str | None:
        """まだ出していない実体ならそのパスを返す。既出・未検出なら None。"""
        if not path:
            return None
        try:
            key = os.path.realpath(path)
        except OSError:
            key = path
        if key in seen:
            return None
        seen.add(key)
        return path

    found = fresh(shutil.which(name))
    if found:
        yield found

    for directory in _candidate_dirs():
        if not directory or not os.path.isdir(directory):
            continue
        found = fresh(shutil.which(name, path=directory))
        if found:
            yield found


def _find_executable(name: str, env_var: str) -> str | None:
    """環境変数 → PATH → 既知のインストール先 の順に探し、最初の 1 本を返す。"""
    return next(_iter_executables(name, env_var), None)


def _paired_ffprobe(ffmpeg_path: str) -> str | None:
    """ffmpeg と対になる ffprobe を返す。

    環境変数の明示指定が最優先。無ければ同じフォルダのものを使う
    （ffmpeg だけ別ビルドを選んだときに版が食い違わないようにする）。
    それも無ければ通常の探索へ落とす。
    """
    if os.environ.get(ENV_FFPROBE):
        explicit = _find_executable("ffprobe", ENV_FFPROBE)
        if explicit:
            return explicit
    same_dir = shutil.which("ffprobe", path=str(Path(ffmpeg_path).parent))
    if same_dir:
        return same_dir
    return _find_executable("ffprobe", ENV_FFPROBE)


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


def _probe_version(ffmpeg_path: str) -> tuple[str, frozenset[str]]:
    """`ffmpeg -version` を叩いてバージョン文字列と使えるエンコーダーを得る。"""
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

    found = {name for name in WANTED_ENCODERS if f"--enable-{name}" in output}
    missing = WANTED_ENCODERS - found
    if missing:
        # ビルド情報に出ない場合があるので -encoders でも確かめる
        found |= _list_encoders(ffmpeg_path, missing)
    return version, frozenset(found)


#: 出力形式が必要とするエンコーダー。output_formats と揃えること。
WANTED_ENCODERS: frozenset[str] = frozenset({"libvorbis", "libmp3lame"})


def _list_encoders(ffmpeg_path: str, wanted: set[str]) -> set[str]:
    """`ffmpeg -encoders` の一覧から、探しているものを拾う。"""
    try:
        result = _run_capture([ffmpeg_path, "-hide_banner", "-encoders"])
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    listing = result.stdout or ""
    return {name for name in wanted if name in listing}


_cached_tools: FFmpegTools | None = None


def find_ffmpeg_tools(force_refresh: bool = False) -> FFmpegTools | None:
    """ffmpeg / ffprobe を探して返す。見つからなければ None。

    結果はプロセス内でキャッシュする（起動のたびに探索しないため）。
    """
    global _cached_tools
    if _cached_tools is not None and not force_refresh:
        return _cached_tools

    # 候補を順に見て、必要なエンコーダーが揃ったものを採る。
    # PATH 上の ffmpeg が libvorbis 抜き（Homebrew の既定ビルドなど）でも、
    # keg-only の ffmpeg-full などが入っていればそちらを拾えるようにする。
    fallback: FFmpegTools | None = None
    first_error: FFmpegNotFoundError | None = None

    for ffmpeg_path in _iter_executables("ffmpeg", ENV_FFMPEG):
        ffprobe_path = _paired_ffprobe(ffmpeg_path)
        if not ffprobe_path:
            continue
        try:
            version, encoders = _probe_version(ffmpeg_path)
        except FFmpegNotFoundError as exc:
            # 壊れた候補は飛ばす。全滅したときだけ最初の失敗を報告する
            first_error = first_error or exc
            continue

        tools = FFmpegTools(
            ffmpeg=ffmpeg_path,
            ffprobe=ffprobe_path,
            version=version,
            encoders=encoders,
        )
        if WANTED_ENCODERS <= encoders:
            _cached_tools = tools
            return tools
        # 足りないものがあっても、より良いのが無ければこれを使う。
        # 最初に見つかったもの＝従来の優先順位をそのまま温存する
        fallback = fallback or tools

    if fallback is None and first_error is not None:
        raise first_error
    _cached_tools = fallback
    return fallback


def ensure_ffmpeg_tools(force_refresh: bool = False) -> FFmpegTools:
    """ffmpeg / ffprobe を取得する。無ければ案内付きの例外を送出。"""
    tools = find_ffmpeg_tools(force_refresh=force_refresh)
    if tools is None:
        raise FFmpegNotFoundError(missing_ffmpeg_message())
    if not tools.encoders:
        lines = [
            "この ffmpeg は libvorbis も libmp3lame も含んでいないため、変換できません。",
            _encoder_build_hint("エンコーダー"),
        ]
        lines += _keg_only_note()
        lines.append(f"  検出したffmpeg: {tools.ffmpeg}")
        raise FFmpegNotFoundError("\n".join(lines))
    return tools


def _keg_only_note() -> list[str]:
    """macOS 向けに、環境変数での指定が要る場合の補足を返す。"""
    if sys.platform != "darwin":
        return []
    return [
        "",
        "ffmpeg-full は keg-only のため PATH には入りません。自動で検出されない",
        "場合は、環境変数で場所を指定してください:",
        f"  {ENV_FFMPEG}=<ffmpeg-full の bin フォルダ>",
        f"  {ENV_FFPROBE}=<ffmpeg-full の bin フォルダ>",
        "  （場所は  brew --prefix ffmpeg-full  で確認できます）",
        "",
    ]


def _encoder_build_hint(encoder_label: str) -> str:
    """エンコーダーが足りないときの対処を 1 行で返す（OS ごと）。"""
    if sys.platform == "darwin":
        # Homebrew の既定の ffmpeg は libvorbis 抜きでビルドされている
        return (
            f"ターミナルで  brew install ffmpeg-full  を実行すると、"
            f"{encoder_label} 入りのビルドが入ります。"
        )
    return f"{encoder_label} 付きのビルド（公式配布版など）を利用してください。"


def encoder_install_hint(output_format) -> str:  # noqa: ANN001 - 循環 import を避ける
    """警告バーに出す 1 行の対処案内。"""
    return _encoder_build_hint(output_format.encoder_label)


def missing_encoder_message(output_format, ffmpeg_path: str = "") -> str:  # noqa: ANN001
    """特定の出力形式が使えないときの案内。"""
    lines = [
        f"この ffmpeg は {output_format.encoder_label} を含んでいないため、"
        f"{output_format.label} へ変換できません。",
        encoder_install_hint(output_format),
    ]
    lines += _keg_only_note()
    if ffmpeg_path:
        lines.append(f"  検出したffmpeg: {ffmpeg_path}")
    return "\n".join(lines)


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
        # 素の ffmpeg は libvorbis 抜きなので、OGG まで使うなら ffmpeg-full が要る
        lines += [
            "  brew install ffmpeg-full   （OGG Vorbis に必要な libvorbis 入り）",
            "  brew install ffmpeg        （MP3 だけで良い場合はこちらでも可）",
            "",
            "  ffmpeg-full は keg-only のため PATH には入りません。場所は",
            "  brew --prefix ffmpeg-full  で確認し、下の環境変数で指定してください。",
        ]
    else:
        lines += ["  sudo apt install ffmpeg   （Debian/Ubuntu 系）"]
    lines += [
        "",
        "インストール済みなのに検出されない場合は、環境変数で実行ファイルを直接指定できます:",
        f"  {ENV_FFMPEG}=<ffmpeg の実行ファイル or bin フォルダ>",
        f"  {ENV_FFPROBE}=<ffprobe の実行ファイル or bin フォルダ>",
    ]
    return "\n".join(lines)
