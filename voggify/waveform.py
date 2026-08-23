"""波形データの生成とキャッシュ。

ffmpeg で PCM を取り出し、表示に要る解像度まで畳んだピーク列にする。
Qt に依存しないので GUI なしでテストできる。

取り出し方の選択
----------------
1 時間の MP3 で 3 方式を比べた結果:

===========================  ========  =========  ==========
方式                          所要時間   パイプ量    振幅の誤差
===========================  ========  =========  ==========
44.1kHz のままデコード          8.17 秒   303 MB     基準
8000Hz にダウンサンプル         2.61 秒    55 MB     11.9%
ffmpeg の showwavespic        2.23 秒     4 KB     —
===========================  ========  =========  ==========

showwavespic は速いが画像しか返らないため、ドラッグ選択やウィンドウ幅の
変更に追従できない。ピークの数値があれば一度の生成で任意の幅に描き直せる
ので、ダウンサンプルしてピークを取る方式にした。誤差 11.9% は波形の
見た目には現れない（ダウンサンプル時のローパスで尖りが少し丸まる程度）。

パイプは逐次読みしてバケットに畳むので、長いファイルでも一度に
数十 MB を抱え込まない。
"""

from __future__ import annotations

import array
import os
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .errors import ProbeError
from .ffmpeg_locator import FFmpegTools, subprocess_flags

#: 1 ファイルあたりのピーク数。画面幅より多めに取り、描画時に間引く。
DEFAULT_BUCKETS = 3000

#: デコードする総サンプル数の目安。長いファイルほどレートを落として
#: この程度に収める（1 時間なら約 8000Hz になる）。
TARGET_TOTAL_SAMPLES = 30_000_000

#: ダウンサンプルの下限・上限
MIN_SAMPLE_RATE = 4000
MAX_SAMPLE_RATE = 48000

#: パイプから 1 回に読むバイト数
_CHUNK_BYTES = 1 << 20

#: int16 の最大値（振幅の正規化に使う）
_INT16_MAX = 32767


class WaveformCancelled(Exception):
    """生成中に取り消された。"""


@dataclass(frozen=True)
class WaveformData:
    """描画に使うピーク列。

    peaks は [min0, max0, min1, max1, ...] の int16 配列。
    バケット i は音声の (i / buckets * duration) 秒付近にあたる。
    """

    duration: float
    peaks: array.array
    sample_rate: int
    #: どの音声トラックから作ったか（0 始まり）
    track: int = 0

    @property
    def buckets(self) -> int:
        return len(self.peaks) // 2

    @property
    def nbytes(self) -> int:
        return self.peaks.buffer_info()[1] * self.peaks.itemsize

    def envelope(self, count: int) -> list[tuple[float, float]]:
        """指定した本数に畳んだ (min, max) を -1.0〜1.0 で返す。

        描画側はウィジェットの幅ぶんだけ呼べばよい。バケット数より多く
        要求された場合は持っているぶんだけ返す。
        """
        total = self.buckets
        if total == 0 or count <= 0:
            return []
        count = min(count, total)
        result: list[tuple[float, float]] = []
        for i in range(count):
            begin = i * total // count
            end = max(begin + 1, (i + 1) * total // count)
            low = min(self.peaks[b * 2] for b in range(begin, end))
            high = max(self.peaks[b * 2 + 1] for b in range(begin, end))
            result.append((low / _INT16_MAX, high / _INT16_MAX))
        return result


def choose_sample_rate(duration: float, source_rate: int | None) -> int:
    """ダウンサンプル先のレートを決める。

    長いファイルほど落として、デコード量が増えすぎないようにする。
    """
    if duration <= 0:
        return MIN_SAMPLE_RATE
    wanted = int(TARGET_TOTAL_SAMPLES / duration)
    wanted = max(MIN_SAMPLE_RATE, min(MAX_SAMPLE_RATE, wanted))
    if source_rate:
        wanted = min(wanted, source_rate)
    return max(MIN_SAMPLE_RATE, wanted)


def extract_waveform(
    path: str | os.PathLike[str],
    tools: FFmpegTools,
    duration: float,
    *,
    buckets: int = DEFAULT_BUCKETS,
    source_rate: int | None = None,
    track: int = 0,
    cancel: threading.Event | None = None,
) -> WaveformData:
    """ffmpeg で PCM を読み出し、バケットごとの min/max に畳む。

    duration が分かっている前提（ffprobe 済みの値を渡す）。
    track は音声トラック番号（0 始まり）で、複数音声を持つ動画から
    選択中のトラックだけを読むために使う。
    cancel がセットされたら WaveformCancelled を投げる。
    """
    source = Path(path)
    if duration <= 0:
        raise ProbeError(f"長さが不明なため波形を作れません: {source.name}")

    rate = choose_sample_rate(duration, source_rate)
    total_samples = max(buckets, int(duration * rate))
    per_bucket = max(1, total_samples // buckets)

    argv = [
        tools.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v", "error",
        "-i", str(source),
        "-map", f"0:a:{max(0, track)}",
        "-ac", "1",
        "-ar", str(rate),
        "-f", "s16le",
        "-",
    ]

    peaks = array.array("h")
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **subprocess_flags(),  # type: ignore[arg-type]
        )
    except OSError as exc:
        raise ProbeError(f"波形の生成を開始できませんでした: {exc}") from exc

    # バケットに満たなかった端数だけを次のチャンクへ持ち越す。
    # 先頭から削るのではなく memoryview で窓を切る（削ると詰め直しで O(n^2) になる）。
    remainder = array.array("h")
    leftover = b""
    try:
        assert process.stdout is not None
        while True:
            if cancel is not None and cancel.is_set():
                process.kill()
                raise WaveformCancelled()

            chunk = process.stdout.read(_CHUNK_BYTES)
            if not chunk:
                break

            data = leftover + chunk
            usable = len(data) // 2 * 2
            leftover = data[usable:]

            block = array.array("h")
            block.frombytes(data[:usable])
            if remainder:
                remainder.extend(block)
                block = remainder
                remainder = array.array("h")

            view = memoryview(block)
            full = len(block) // per_bucket
            for i in range(full):
                window = view[i * per_bucket : (i + 1) * per_bucket]
                peaks.append(min(window))
                peaks.append(max(window))
            rest = len(block) - full * per_bucket
            if rest:
                remainder = array.array("h", view[full * per_bucket :])
            view.release()

        if remainder:
            peaks.append(min(remainder))
            peaks.append(max(remainder))

        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    if process.returncode not in (0, None) and not peaks:
        raise ProbeError(f"波形を生成できませんでした: {source.name}")
    if not peaks:
        raise ProbeError(f"波形を生成できませんでした（音声が空）: {source.name}")

    return WaveformData(
        duration=duration, peaks=peaks, sample_rate=rate, track=max(0, track)
    )


# ---------------------------------------------------------------------------
# キャッシュ
# ---------------------------------------------------------------------------
#: 保持する最大件数
CACHE_MAX_ENTRIES = 64
#: 保持する最大バイト数（1 件はおよそ 12KB なので通常は件数で先に頭打ちになる）
CACHE_MAX_BYTES = 16 * 1024 * 1024


def cache_key(
    path: str | os.PathLike[str], track: int = 0
) -> tuple[str, int, int, int]:
    """波形の同一性を見るキー。ファイルが更新されたら作り直す。

    同じファイルでもトラックが違えば別の波形なので、track もキーに含める。
    """
    resolved = Path(path)
    track = max(0, int(track))
    try:
        stat = resolved.stat()
        return (str(resolved), int(stat.st_mtime_ns), int(stat.st_size), track)
    except OSError:
        return (str(resolved), 0, 0, track)


class WaveformCache:
    """波形データを持っておく LRU。

    1 件が数 KB と小さいので、件数と合計バイト数の両方で上限を設けている。
    スレッドから触られるのでロックする。
    """

    def __init__(
        self,
        max_entries: int = CACHE_MAX_ENTRIES,
        max_bytes: int = CACHE_MAX_BYTES,
    ) -> None:
        self._entries: OrderedDict[
            tuple[str, int, int, int], WaveformData
        ] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = threading.Lock()

    def get(
        self, path: str | os.PathLike[str], track: int = 0
    ) -> WaveformData | None:
        key = cache_key(path, track)
        with self._lock:
            data = self._entries.get(key)
            if data is not None:
                self._entries.move_to_end(key)  # 使ったので新しい側へ
            return data

    def put(
        self, path: str | os.PathLike[str], data: WaveformData, track: int | None = None
    ) -> None:
        key = cache_key(path, data.track if track is None else track)
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._bytes -= existing.nbytes
            self._entries[key] = data
            self._bytes += data.nbytes
            self._evict()

    def discard(self, path: str | os.PathLike[str]) -> bool:
        """そのファイルの波形を捨てる。一覧から削除されたときに呼ぶ。

        複数トラックのファイルはトラックごとに載っているので全部落とす。
        """
        target = str(Path(path))
        dropped = False
        with self._lock:
            for key in [k for k in self._entries if k[0] == target]:
                self._bytes -= self._entries.pop(key).nbytes
                dropped = True
        return dropped

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def _evict(self) -> None:
        while self._entries and (
            len(self._entries) > self._max_entries or self._bytes > self._max_bytes
        ):
            _, dropped = self._entries.popitem(last=False)  # 一番古いもの
            self._bytes -= dropped.nbytes

    # --- 状態の確認（テストと診断用）---
    @property
    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def nbytes(self) -> int:
        with self._lock:
            return self._bytes


__all__ = [
    "CACHE_MAX_BYTES",
    "CACHE_MAX_ENTRIES",
    "DEFAULT_BUCKETS",
    "WaveformCache",
    "WaveformCancelled",
    "WaveformData",
    "choose_sample_rate",
    "extract_waveform",
]
