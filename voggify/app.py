"""GUI アプリケーションの起動処理。"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import TracebackType

from . import __app_name__
from .config import load_config
from .ffmpeg_locator import find_ffmpeg_tools


def _fallback_report(
    previous,  # noqa: ANN001
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    """最後の手段。windowed ビルドでは stderr が無いので黙って諦める。"""
    if sys.stderr is None:
        return
    try:
        previous(exc_type, exc, tb)
    except Exception:  # noqa: BLE001
        pass


def install_excepthook(window) -> None:  # noqa: ANN001
    """UI スレッドで拾い損ねた例外でアプリごと落ちないようにする。

    ワーカー側（ProbeService / ConversionWorker）は個別に例外を捕まえて
    該当ファイルをエラー扱いにするので、ここは最後の受け皿。
    """
    previous = sys.excepthook

    def hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            window.report_unexpected_error(text)
        except Exception:  # noqa: BLE001 - 報告側で落ちても元の例外を優先する
            _fallback_report(previous, exc_type, exc, tb)

    sys.excepthook = hook


def run(argv: list[str] | None = None) -> int:
    """QApplication を立ち上げてメインウィンドウを表示する。"""
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    args = argv if argv is not None else sys.argv
    app = QApplication(args)
    app.setApplicationName(__app_name__)
    app.setOrganizationName(__app_name__)

    # 起動時に一度だけ探索する。見つからなくても起動は続行し、
    # ウィンドウ上部の警告バーから再確認できるようにする。
    tools = find_ffmpeg_tools()

    # 設定は壊れていても例外を投げない。既定値へのフォールバックと
    # その理由が LoadResult に入って返る。
    loaded = load_config()

    window = MainWindow(tools, loaded.config, loaded.warnings)
    install_excepthook(window)
    window.show()

    # コマンドライン引数でファイルが渡されていれば最初から積んでおく
    startup_files = [Path(a) for a in args[1:] if Path(a).exists()]
    if startup_files:
        window.add_paths(startup_files)

    return app.exec()
