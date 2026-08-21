"""変換キューをワーカースレッドで順次処理するサービス。

`Converter.convert()` はブロッキングなので、QThread に載せた QObject の中で
回し、進捗・ログ・結果をシグナルで UI スレッドへ返す。
並列化する場合はこのサービスを複数持つか、内部を QThreadPool に差し替える。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..converter import ConversionOptions, Converter
from ..errors import ConversionCancelled, VoggifyError
from ..ffmpeg_locator import FFmpegTools
from ..probe import AudioInfo

#: 変換結果の種別（シグナルで文字列として飛ばす）
OUTCOME_DONE = "done"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"


@dataclass(frozen=True)
class ConversionJob:
    """キューに積む 1 件分。

    解析済みの info を持ち回すことで、変換時の ffprobe 再実行を省く。
    """

    path: Path
    info: AudioInfo | None = None


class ConversionWorker(QObject):
    """ワーカースレッド側で動く本体。"""

    #: (path)
    file_started = Signal(str)
    #: (path, 0.0〜1.0)
    file_progress = Signal(str, float)
    #: (path, ffmpeg のログ 1 行)
    file_log = Signal(str, str)
    #: (path, outcome, メッセージ, 出力パス, 出力サイズ, 所要秒)
    file_finished = Signal(str, str, str, str, int, float)
    #: (成功, 失敗, 中断)
    all_finished = Signal(int, int, int)

    def __init__(
        self,
        tools: FFmpegTools,
        jobs: list[ConversionJob],
        options: ConversionOptions,
    ) -> None:
        super().__init__()
        self._jobs = jobs
        self._options = options
        self._converter = Converter(tools)
        self._stop = threading.Event()

    @Slot()
    def run(self) -> None:
        """キューを先頭から順に処理する。"""
        done = failed = cancelled = 0

        for job in self._jobs:
            path_text = str(job.path)

            if self._stop.is_set():
                # キャンセル後に残ったキューは実行せず中断扱いにする
                cancelled += 1
                self.file_finished.emit(
                    path_text, OUTCOME_CANCELLED, "変換を中断しました。", "", 0, 0.0
                )
                continue

            self.file_started.emit(path_text)
            try:
                result = self._converter.convert(
                    job.path,
                    self._options,
                    info=job.info,
                    on_progress=lambda ratio, p=path_text: self.file_progress.emit(p, ratio),
                    on_log=lambda line, p=path_text: self.file_log.emit(p, line),
                )
            except ConversionCancelled as exc:
                cancelled += 1
                self.file_finished.emit(
                    path_text, OUTCOME_CANCELLED, exc.user_message, "", 0, 0.0
                )
            except VoggifyError as exc:
                failed += 1
                self.file_finished.emit(
                    path_text, OUTCOME_FAILED, exc.user_message, "", 0, 0.0
                )
            except Exception as exc:  # noqa: BLE001 - ワーカーから例外を漏らさない
                failed += 1
                self.file_finished.emit(
                    path_text,
                    OUTCOME_FAILED,
                    f"予期しないエラーが発生しました: {exc}",
                    "",
                    0,
                    0.0,
                )
            else:
                done += 1
                self.file_finished.emit(
                    path_text,
                    OUTCOME_DONE,
                    "",
                    str(result.output),
                    result.output_size or 0,
                    result.elapsed_sec,
                )

        self.all_finished.emit(done, failed, cancelled)

    def cancel(self) -> None:
        """UI スレッドから呼ばれる。実行中の ffmpeg を止め、以降は処理しない。"""
        self._stop.set()
        self._converter.cancel()


class ConversionService(QObject):
    """QThread のライフサイクルを管理し、ワーカーのシグナルを中継する。"""

    file_started = Signal(str)
    file_progress = Signal(str, float)
    file_log = Signal(str, str)
    file_finished = Signal(str, str, str, str, int, float)
    #: (成功, 失敗, 中断)
    finished = Signal(int, int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: ConversionWorker | None = None
        self._cancelling = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def cancelling(self) -> bool:
        return self._cancelling

    def start(
        self,
        tools: FFmpegTools,
        jobs: list[ConversionJob],
        options: ConversionOptions,
    ) -> bool:
        """変換を開始する。既に実行中なら False。"""
        if self.running or not jobs:
            return False

        self._cancelling = False
        thread = QThread(self)
        worker = ConversionWorker(tools, jobs, options)
        worker.moveToThread(thread)

        worker.file_started.connect(self.file_started)
        worker.file_progress.connect(self.file_progress)
        worker.file_log.connect(self.file_log)
        worker.file_finished.connect(self.file_finished)
        worker.all_finished.connect(self._on_all_finished)

        thread.started.connect(worker.run)

        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def cancel(self) -> None:
        """実行中の変換を中断する。"""
        if self._worker is not None and self.running:
            self._cancelling = True
            self._worker.cancel()

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """スレッドの終了を待つ（終了処理・テスト用）。"""
        if self._thread is None:
            return True
        return self._thread.wait(timeout_ms)

    @Slot(int, int, int)
    def _on_all_finished(self, done: int, failed: int, cancelled: int) -> None:
        thread = self._thread
        if thread is not None:
            thread.quit()
            # wait() を挟んでからワーカーの参照を落とす（スレッド実行中の破棄を避ける）
            thread.wait(5000)
            thread.deleteLater()
        self._thread = None
        self._worker = None
        self._cancelling = False
        self.finished.emit(done, failed, cancelled)
