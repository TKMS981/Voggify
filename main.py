"""Voggify のエントリポイント。

  python main.py            -> GUI を起動
  python main.py <command>  -> CLI（check / info / convert）

exe 化した場合も同じで、`Voggify.exe` で GUI、`Voggify.exe check` で CLI。
"""

from __future__ import annotations

import sys

#: CLI として扱うサブコマンド。これ以外の引数はファイルとみなして GUI に渡す。
CLI_COMMANDS = {"check", "info", "convert", "-h", "--help"}


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in CLI_COMMANDS:
        # windowed ビルドでは標準出力が無いので、先に用意しておく
        from voggify.console import ensure_streams

        ensure_streams()

        from voggify.cli import main as cli_main

        return cli_main(args)

    from voggify.app import run

    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
