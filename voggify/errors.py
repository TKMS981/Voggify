"""Voggify 全体で使う例外定義。

GUI 層はこれらを捕捉してユーザー向けメッセージに変換する。
"""

from __future__ import annotations

import errno


#: errno → ユーザー向けの日本語。ここに無いものは strerror をそのまま使う。
_ERRNO_MESSAGES = {
    errno.EACCES: "アクセスが拒否されました。フォルダの権限を確認してください。",
    errno.EPERM: "操作が許可されていません。フォルダの権限を確認してください。",
    errno.ENOSPC: "ディスクの空き容量が足りません。",
    errno.EROFS: "読み取り専用のため書き込めません。",
    errno.ENOENT: "パスが見つかりません。",
    errno.ENOTDIR: "フォルダではありません。",
    errno.EEXIST: "同名のファイルが既に存在します。",
    errno.ENAMETOOLONG: "パスが長すぎます。",
    errno.EBUSY: "他のプログラムが使用中です。",
    errno.EDQUOT: "ディスクの割り当て容量を超えました。",
}


def describe_os_error(exc: OSError) -> str:
    """OSError をユーザー向けの 1 文にする。

    そのまま str(exc) を見せると内部の一時ファイル名などが混ざるので、
    errno から意味のある説明を組み立てる。
    """
    message = _ERRNO_MESSAGES.get(exc.errno)
    if message:
        return message
    reason = (exc.strerror or "").strip()
    return reason if reason else str(exc)


class VoggifyError(Exception):
    """Voggify が送出する例外の基底クラス。"""

    #: ユーザーにそのまま提示できる日本語メッセージ
    user_message: str = "予期しないエラーが発生しました。"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.user_message)
        self.user_message = message or self.user_message


class FFmpegNotFoundError(VoggifyError):
    """ffmpeg / ffprobe が見つからない、または実行できない。"""

    user_message = "ffmpeg が見つかりませんでした。"


class ProbeError(VoggifyError):
    """ffprobe による解析に失敗した。"""

    user_message = "ファイルの解析に失敗しました。"


class UnsupportedFormatError(VoggifyError):
    """対応していない入力フォーマット。"""

    user_message = "対応していないフォーマットです。"


class OutputPathError(VoggifyError):
    """出力先が存在しない / 書き込み権限がない など。"""

    user_message = "出力先に書き込めません。"


class ConversionCancelled(VoggifyError):
    """ユーザー操作により変換が中断された。"""

    user_message = "変換をキャンセルしました。"


class ConversionError(VoggifyError):
    """ffmpeg の実行が失敗した。"""

    user_message = "変換に失敗しました。"

    def __init__(
        self,
        message: str | None = None,
        *,
        returncode: int | None = None,
        log: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        #: ffmpeg の stderr 全文（GUI のログパネルに流す想定）
        self.log = log
