"""ファイル解析（ffprobe 実行）をバックグラウンドで行うサービス。

ffprobe はファイル 1 本につき 1 プロセス立ち上がるため、大量にドロップされた
ときに UI スレッドで回すと固まる。QThreadPool に逃がして結果をシグナルで返す。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..errors import FFmpegNotFoundError, VoggifyError
from ..ffmpeg_locator import FFmpegTools
from ..output_formats import DEFAULT_OUTPUT_FORMAT, OutputFormat
from ..probe import AudioInfo, inspect

#: 同時に走らせる ffprobe の本数。多すぎるとプロセス生成でかえって遅くなる。
MAX_CONCURRENT_PROBES = 4


class _ProbeSignals(QObject):
    """QRunnable は QObject ではないのでシグナルは別オブジェクトに持たせる。"""

    #: (generation, path, info または None, エラーメッセージ)
    done = Signal(int, str, object, str)


class _ProbeTask(QRunnable):
    """1 ファイルを解析するタスク。"""

    def __init__(
        self,
        generation: int,
        path: Path,
        tools: FFmpegTools | None,
        signals: _ProbeSignals,
        output_format: OutputFormat,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._path = path
        self._tools = tools
        self._signals = signals
        self._output_format = output_format

    def run(self) -> None:  # noqa: D102 - QRunnable の規定メソッド
        info: AudioInfo | None = None
        message = ""
        try:
            if self._tools is None:
                raise FFmpegNotFoundError(
                    "ffmpeg が見つからないため解析できません。"
                )
            info = inspect(
                self._path, self._tools, output_format=self._output_format
            )
        except VoggifyError as exc:
            message = exc.user_message
        except Exception as exc:  # noqa: BLE001 - ワーカーから例外を漏らさない
            message = f"解析中に予期しないエラーが発生しました: {exc}"
        self._signals.done.emit(self._generation, str(self._path), info, message)


class ProbeService(QObject):
    """解析タスクの投入と結果の受け取りをまとめたサービス。"""

    #: (path, info または None, エラーメッセージ)
    probed = Signal(str, object, str)
    #: 実行待ち / 実行中のタスクが 0 になった
    idle = Signal()

    def __init__(self, tools: FFmpegTools | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tools = tools
        self._output_format: OutputFormat = DEFAULT_OUTPUT_FORMAT
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(
            min(MAX_CONCURRENT_PROBES, max(1, QThreadPool.globalInstance().maxThreadCount()))
        )
        self._signals = _ProbeSignals(self)
        self._signals.done.connect(self._on_done)
        #: 「全てクリア」などで古い結果を捨てるための世代番号
        self._generation = 0
        self._pending = 0

    def set_tools(self, tools: FFmpegTools | None) -> None:
        """ffmpeg を後から検出し直したときに差し替える。"""
        self._tools = tools

    def set_output_format(self, output_format: OutputFormat) -> None:
        """出力形式が変わると「既に変換済み」の判定も変わる。"""
        self._output_format = output_format

    @property
    def busy(self) -> bool:
        return self._pending > 0

    def submit(self, paths: list[Path]) -> None:
        """解析タスクを投入する。"""
        for path in paths:
            self._pending += 1
            self._pool.start(
                _ProbeTask(
                    self._generation, path, self._tools, self._signals,
                    self._output_format,
                )
            )

    def discard_pending(self) -> None:
        """未処理の結果を無視する（リストをクリアしたときなど）。

        既に走っている ffprobe は止められないが、結果は捨てられる。
        """
        self._generation += 1
        self._pending = 0
        self._pool.clear()

    def wait_for_done(self, timeout_ms: int = 30_000) -> bool:
        """全タスクの終了を待つ（テスト用）。"""
        return self._pool.waitForDone(timeout_ms)

    def _on_done(self, generation: int, path: str, info: object, message: str) -> None:
        if generation != self._generation:
            return  # 破棄された世代の結果
        self._pending = max(0, self._pending - 1)
        self.probed.emit(path, info, message)
        if self._pending == 0:
            self.idle.emit()
