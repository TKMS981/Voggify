"""windowed ビルドでの標準出力の取り扱い。

PyInstaller の `--noconsole`（= pythonw 相当）で動かすと sys.stdout と
sys.stderr が None になる。この状態で print() すると AttributeError で
落ちるため、CLI サブコマンドを使う前にここで整えておく。

コマンドプロンプトから `Voggify.exe check` のように呼ばれた場合は、
呼び出し元のコンソールに接続して出力を見せる。エクスプローラーから
起動された場合は接続先が無いので、出力は捨てる（落とさないことが目的）。
"""

from __future__ import annotations

import os
import sys

#: AttachConsole に渡す「親プロセスのコンソール」
_ATTACH_PARENT_PROCESS = -1


def _attach_parent_console() -> int | None:
    """親プロセスのコンソールに接続する。成功したら出力コードページを返す。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if not kernel32.AttachConsole(_ATTACH_PARENT_PROCESS):
            return None
        # コンソール側の設定を変えずに済ませたいので、今のコードページに合わせる
        return int(kernel32.GetConsoleOutputCP()) or None
    except Exception:  # noqa: BLE001 - 接続できないだけなので握りつぶす
        return None


def _open_console_stream(codepage: int):
    encoding = f"cp{codepage}"
    try:
        return open("CONOUT$", "w", encoding=encoding, errors="replace", buffering=1)
    except (OSError, LookupError):
        return None


def ensure_streams() -> bool:
    """sys.stdout / sys.stderr を使える状態にする。

    コンソールに出力できるようになったら True、捨てることにしたら False。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return True

    codepage = _attach_parent_console()
    if codepage is not None:
        stream = _open_console_stream(codepage)
        if stream is not None:
            if sys.stdout is None:
                sys.stdout = stream
            if sys.stderr is None:
                sys.stderr = _open_console_stream(codepage) or stream
            return True

    sink = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink
    return False
