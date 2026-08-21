"""Voggify の CLI。

GUI 実装前の動作確認用だが、完成後もデバッグ用途で残す想定。

  python main.py check
  python main.py info  <file>
  python main.py convert <file> [-q 0-10] [-o DIR] [--overwrite] [--verbose]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .converter import ConversionOptions, Converter
from .errors import VoggifyError
from .ffmpeg_locator import ensure_ffmpeg_tools, find_ffmpeg_tools, missing_ffmpeg_message
from .formats import (
    DEFAULT_QUALITY,
    MAX_QUALITY,
    MIN_QUALITY,
    estimate_output_size,
    format_bytes,
    format_duration,
)
from .probe import inspect


def _print_progress(ratio: float) -> None:
    """1 行の簡易プログレスバーを描画する。"""
    width = 30
    filled = int(ratio * width)
    bar = "#" * filled + "-" * (width - filled)
    sys.stdout.write(f"\r  [{bar}] {ratio * 100:5.1f}%")
    sys.stdout.flush()


def cmd_check(_args: argparse.Namespace) -> int:
    """ffmpeg / ffprobe の検出結果を表示する。"""
    tools = find_ffmpeg_tools(force_refresh=True)
    if tools is None:
        print(missing_ffmpeg_message(), file=sys.stderr)
        return 1
    print("ffmpeg を検出しました:")
    print("  " + tools.describe())
    if not tools.has_libvorbis:
        print("\n警告: libvorbis が見つかりません。OGG Vorbis へ変換できない可能性があります.")
        return 1
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """ファイルの解析結果と変換後サイズ予測を表示する。"""
    tools = ensure_ffmpeg_tools()
    info = inspect(args.input, tools)
    estimated = estimate_output_size(info.duration_sec, args.quality, info.channels)

    print(f"ファイル      : {info.path.name}")
    print(f"パス          : {info.path}")
    print(f"形式          : {info.display_format} ({info.format_name})")
    print(f"再生時間      : {format_duration(info.duration_sec)}")
    print(f"サンプルレート: {info.sample_rate or '-'} Hz / {info.channels} ch")
    print(f"ビットレート  : {int(info.bit_rate_bps / 1000) if info.bit_rate_bps else '-'} kbps")
    print(f"現在のサイズ  : {format_bytes(info.file_size)}")
    print(f"変換後の予測  : {format_bytes(estimated)}  (-q:a {args.quality})")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    """1 ファイルを OGG Vorbis に変換する。"""
    tools = ensure_ffmpeg_tools()
    info = inspect(args.input, tools)
    options = ConversionOptions(
        quality=args.quality,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        overwrite=args.overwrite,
    )

    print(f"変換: {info.path.name}  [{info.display_format} -> OGG Vorbis q{args.quality}]")
    converter = Converter(tools)
    on_log = (lambda line: print(f"  | {line}")) if args.verbose else None

    try:
        result = converter.convert(
            info.path,
            options,
            info=info,
            on_progress=None if args.verbose else _print_progress,
            on_log=on_log,
        )
    except KeyboardInterrupt:
        converter.cancel()
        print("\nキャンセルしました。", file=sys.stderr)
        return 130

    if not args.verbose:
        print()

    ratio = ""
    if info.file_size and result.output_size:
        ratio = f"  ({result.output_size / info.file_size * 100:.0f}% of original)"
    print(f"完了: {result.output}")
    print(f"  サイズ: {format_bytes(result.output_size)}{ratio}")
    print(f"  所要時間: {result.elapsed_sec:.1f} 秒")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voggify",
        description="音楽ファイルを OGG Vorbis に変換します。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="ffmpeg / ffprobe の検出状況を表示")
    check.set_defaults(func=cmd_check)

    quality_kwargs = {
        "type": int,
        "default": DEFAULT_QUALITY,
        "metavar": f"{MIN_QUALITY}-{MAX_QUALITY}",
        "help": f"OGG Vorbis の品質 -q:a (既定: {DEFAULT_QUALITY})",
    }

    info_cmd = subparsers.add_parser("info", help="ファイルを解析して情報を表示")
    info_cmd.add_argument("input", help="入力ファイル")
    info_cmd.add_argument("-q", "--quality", **quality_kwargs)  # type: ignore[arg-type]
    info_cmd.set_defaults(func=cmd_info)

    convert = subparsers.add_parser("convert", help="OGG Vorbis に変換")
    convert.add_argument("input", help="入力ファイル")
    convert.add_argument("-q", "--quality", **quality_kwargs)  # type: ignore[arg-type]
    convert.add_argument(
        "-o", "--output-dir",
        default=None,
        help="出力先フォルダ (既定: 入力ファイルと同じ場所)",
    )
    convert.add_argument(
        "--overwrite",
        action="store_true",
        help="同名ファイルがあれば上書きする",
    )
    convert.add_argument(
        "--verbose",
        action="store_true",
        help="ffmpeg のログを表示する",
    )
    convert.set_defaults(func=cmd_convert)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "quality", None) is not None:
        if not MIN_QUALITY <= args.quality <= MAX_QUALITY:
            parser.error(f"--quality は {MIN_QUALITY}〜{MAX_QUALITY} で指定してください")

    try:
        return args.func(args)
    except VoggifyError as exc:
        print(f"\nエラー: {exc.user_message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました。", file=sys.stderr)
        return 130
