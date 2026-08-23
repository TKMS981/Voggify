"""波形の生成をバックグラウンドで行うサービス。

生成は数秒かかることがある（1 時間のファイルで約 3 秒）ので、
UI スレッドから追い出して結果をシグナルで返す。同じファイルを選び直した
ときに作り直さないよう、結果はキャッシュに載せる。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..errors import VoggifyError
from ..ffmpeg_locator import FFmpegTools
from ..waveform import (
    WaveformCache,
    WaveformCancelled,
    WaveformData,
    extract_waveform,
)

#: 波形はまとめて要求されないので 1 本ずつで足りる
MAX_CONCURRENT = 1


class _WaveformSignals(QObject):
    """QRunnable はシグナルを持てないので別オブジェクトに預ける。"""

    #: (generation, path, track, WaveformData または None, エラーメッセージ)
    done = Signal(int, str, int, object, str)


class _WaveformTask(QRunnable):
    """1 ファイルぶんの波形を作るタスク。"""

    def __init__(
        self,
        generation: int,
        path: Path,
        duration: float,
        source_rate: int | None,
        tools: FFmpegTools,
        signals: _WaveformSignals,
        cancel: threading.Event,
        track: int = 0,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._path = path
        self._duration = duration
        self._source_rate = source_rate
        self._tools = tools
        self._signals = signals
        self._cancel = cancel
        self._track = track

    def run(self) -> None:  # noqa: D102 - QRunnable の規定メソッド
        data: WaveformData | None = None
        message = ""
        try:
            data = extract_waveform(
                self._path,
                self._tools,
                self._duration,
                source_rate=self._source_rate,
                track=self._track,
                cancel=self._cancel,
            )
        except WaveformCancelled:
            return  # 取り消されたので何も通知しない
        except VoggifyError as exc:
            message = exc.user_message
        except Exception as exc:  # noqa: BLE001 - ワーカーから例外を漏らさない
            message = f"波形の生成に失敗しました: {exc}"
        self._signals.done.emit(
            self._generation, str(self._path), self._track, data, message
        )


class WaveformService(QObject):
    """波形生成の投入・キャッシュ・結果配送。"""

    #: (path, track, WaveformData または None, エラーメッセージ)
    ready = Signal(str, int, object, str)
    #: 生成を始めた（プレースホルダ表示用）
    started = Signal(str)

    def __init__(
        self,
        tools: FFmpegTools | None = None,
        parent: QObject | None = None,
        cache: WaveformCache | None = None,
    ) -> None:
        super().__init__(parent)
        self._tools = tools
        self._cache = cache or WaveformCache()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_CONCURRENT)
        self._signals = _WaveformSignals(self)
        self._signals.done.connect(self._on_done)
        #: 選択が切り替わったら古い結果を捨てるための世代番号
        self._generation = 0
        self._cancel = threading.Event()
        #: 生成中の対象 (path, track)。トラックだけ変えた場合も別物として扱う。
        self._pending: tuple[str, int] | None = None

    # ------------------------------------------------------------------
    @property
    def cache(self) -> WaveformCache:
        return self._cache

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def set_tools(self, tools: FFmpegTools | None) -> None:
        self._tools = tools

    def request(
        self,
        path: str | Path,
        duration: float,
        source_rate: int | None = None,
        track: int = 0,
    ) -> WaveformData | None:
        """波形を要求する。

        キャッシュにあればその場で返す（シグナルは出さない）。
        無ければ生成を始めて None を返し、あとで ready が飛ぶ。

        同じファイルでもトラックが違えば別の波形なので、キャッシュも
        生成もトラックごとに分かれる。
        """
        target = Path(path)
        track = max(0, int(track))
        cached = self._cache.get(target, track)
        if cached is not None:
            return cached

        if self._tools is None or duration <= 0:
            return None

        # 走っている生成は捨てる（選択が変わったら前の結果は要らない）
        self.cancel_pending()
        self._pending = (str(target), track)
        self._cancel = threading.Event()
        self._pool.start(
            _WaveformTask(
                self._generation,
                target,
                duration,
                source_rate,
                self._tools,
                self._signals,
                self._cancel,
                track,
            )
        )
        self.started.emit(str(target))
        return None

    def cancel_pending(self) -> None:
        """走っている生成を打ち切る。"""
        self._generation += 1
        self._cancel.set()
        self._pending = None
        self._pool.clear()

    def discard(self, path: str | Path) -> None:
        """そのファイルの波形をキャッシュから捨てる（一覧から削除されたとき）。

        複数トラックぶん載っていることがあるので、まとめて落とす。
        """
        self._cache.discard(path)

    def clear(self) -> None:
        self.cancel_pending()
        self._cache.clear()

    def wait_for_done(self, timeout_ms: int = 60_000) -> bool:
        """生成の終了を待つ（テスト用）。"""
        return self._pool.waitForDone(timeout_ms)

    # ------------------------------------------------------------------
    def _on_done(
        self, generation: int, path: str, track: int, data: object, message: str
    ) -> None:
        if generation != self._generation:
            return  # 捨てた世代の結果
        self._pending = None
        if isinstance(data, WaveformData):
            self._cache.put(path, data, track)
        self.ready.emit(path, track, data, message)
