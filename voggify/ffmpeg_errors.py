"""ffmpeg のエラー出力を、ユーザーに意味が伝わる日本語へ翻訳する。

ffmpeg のメッセージは英語かつ実装寄りなので、そのまま出しても原因が
分からない。よくある失敗を拾って言い換え、元の行は補足として残す。
"""

from __future__ import annotations

from typing import Final, Iterable, Sequence

#: (ffmpeg の出力に含まれる語, 日本語の説明) の対応表。上から順に判定する。
_HINTS: Final[Sequence[tuple[tuple[str, ...], str]]] = (
    (
        ("no space left on device", "enospc"),
        "保存先のディスク容量が足りません。空き容量を確保してください。",
    ),
    (
        ("disk quota exceeded",),
        "ディスクの割り当て容量を超えました。",
    ),
    (
        ("read-only file system",),
        "保存先が読み取り専用のため書き込めません。",
    ),
    (
        ("permission denied", "access is denied"),
        "ファイルまたはフォルダへのアクセスが拒否されました。",
    ),
    (
        ("being used by another process", "device or resource busy", "text file busy"),
        "ファイルが他のプログラムで使用中です。閉じてから再試行してください。",
    ),
    (
        ("unknown encoder",),
        "この ffmpeg は libvorbis エンコーダーを含んでいません。"
        "libvorbis 付きのビルドを使ってください。",
    ),
    (
        ("moov atom not found",),
        "ファイルの構造が壊れています（MP4 / M4A のインデックスが見つかりません）。",
    ),
    (
        ("invalid data found when processing input", "error while decoding"),
        "ファイルが壊れているか、対応していない形式です。",
    ),
    (
        ("no such file or directory", "could not open file"),
        "入力ファイルが見つかりません。移動または削除された可能性があります。",
    ),
    (
        ("file name too long", "filename too long"),
        "ファイル名またはパスが長すぎます。",
    ),
    (
        ("does not contain any stream", "output file is empty"),
        "音声データを取り出せませんでした。",
    ),
    (
        ("immediate exit requested",),
        "変換が中断されました。",
    ),
)

#: 原因の手がかりになりにくい行（拾っても意味がないもの）
_NOISE: Final[tuple[str, ...]] = (
    "conversion failed!",
    "error opening output file",
    "error opening output files",
)


def _matches(line: str, needles: Iterable[str]) -> bool:
    lowered = line.lower()
    return any(needle in lowered for needle in needles)


def find_error_line(log_lines: Sequence[str]) -> str:
    """ログから原因らしき行を 1 行選ぶ。

    末尾の定型句（Conversion failed! など）は避け、その手前の実質的な
    エラー行を優先する。
    """
    fallback = ""
    for line in reversed(log_lines):
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(noise in lowered for noise in _NOISE):
            fallback = fallback or stripped
            continue
        if any(_matches(stripped, needles) for needles, _ in _HINTS):
            return stripped
        if "error" in lowered or "invalid" in lowered or "failed" in lowered:
            fallback = fallback or stripped
    if fallback:
        return fallback
    return log_lines[-1].strip() if log_lines else ""


def explain(log_lines: Sequence[str], returncode: int | None = None) -> str:
    """失敗の理由を日本語 1 文で返す。分からなければ空文字。"""
    joined = "\n".join(log_lines[-40:]).lower()
    for needles, explanation in _HINTS:
        if any(needle in joined for needle in needles):
            return explanation
    if returncode is not None and returncode < 0:
        return f"ffmpeg が異常終了しました（シグナル {-returncode}）。"
    return ""


def describe_failure(
    source_name: str,
    log_lines: Sequence[str],
    returncode: int | None = None,
) -> str:
    """ConversionError に載せる本文を組み立てる。

    1 行目に日本語の説明、2 行目に ffmpeg の該当行を添える。
    """
    explanation = explain(log_lines, returncode)
    detail = find_error_line(log_lines)

    if explanation:
        headline = f"{source_name}: {explanation}"
    elif returncode is not None:
        headline = f"{source_name}: 変換に失敗しました（ffmpeg 終了コード {returncode}）。"
    else:
        headline = f"{source_name}: 変換に失敗しました。"

    if detail:
        return f"{headline}\n  ffmpeg: {detail}"
    return headline
