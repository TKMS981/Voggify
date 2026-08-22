"""波形データの生成とキャッシュのテスト（Qt 不要）。"""

from __future__ import annotations

import array
import shutil
import subprocess
import threading
import time

import pytest

from voggify.errors import ProbeError
from voggify.ffmpeg_locator import subprocess_flags
from voggify.waveform import (
    MAX_SAMPLE_RATE,
    MIN_SAMPLE_RATE,
    WaveformCache,
    WaveformCancelled,
    WaveformData,
    choose_sample_rate,
    extract_waveform,
)


# ---------------------------------------------------------------------------
# サンプルレートの決め方（ffmpeg 不要）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "duration,expected",
    [
        (5, 44100),        # 短ければ元のレートのまま
        (300, 44100),
        (3600, 8333),      # 1 時間なら落とす
        (10800, MIN_SAMPLE_RATE),
        (100000, MIN_SAMPLE_RATE),
    ],
)
def test_sample_rate_drops_for_long_files(duration, expected):
    assert choose_sample_rate(duration, 44100) == expected


def test_sample_rate_never_exceeds_the_source():
    assert choose_sample_rate(10, 22050) == 22050
    assert choose_sample_rate(10, None) <= MAX_SAMPLE_RATE


def test_sample_rate_has_a_floor():
    assert choose_sample_rate(1e9, 44100) == MIN_SAMPLE_RATE
    assert choose_sample_rate(0, 44100) == MIN_SAMPLE_RATE


# ---------------------------------------------------------------------------
# envelope（ffmpeg 不要）
# ---------------------------------------------------------------------------
def make_data(pairs: list[tuple[int, int]], duration: float = 10.0) -> WaveformData:
    peaks = array.array("h", [v for pair in pairs for v in pair])
    return WaveformData(duration=duration, peaks=peaks, sample_rate=8000)


def test_envelope_normalises_to_unit_range():
    data = make_data([(-32767, 32767), (0, 0)])
    env = data.envelope(2)
    assert env[0] == pytest.approx((-1.0, 1.0))
    assert env[1] == pytest.approx((0.0, 0.0))


def test_envelope_folds_down_keeping_the_extremes():
    """畳んだときにピークが消えないこと。"""
    data = make_data([(-100, 100), (-30000, 30000), (-50, 50), (-10, 10)])
    env = data.envelope(2)
    assert env[0][1] == pytest.approx(30000 / 32767), "大きい方が残る"
    assert env[0][0] == pytest.approx(-30000 / 32767)


def test_envelope_caps_at_the_bucket_count():
    data = make_data([(-1, 1)] * 50)
    assert len(data.envelope(500)) == 50
    assert len(data.envelope(10)) == 10


def test_envelope_handles_empty():
    assert make_data([]).envelope(10) == []
    assert make_data([(-1, 1)]).envelope(0) == []


def test_nbytes_matches_the_buffer():
    data = make_data([(-1, 1)] * 100)
    assert data.buckets == 100
    assert data.nbytes == 400  # int16 × 2 × 100


# ---------------------------------------------------------------------------
# キャッシュ（ffmpeg 不要）
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_files(tmp_path):
    paths = []
    for i in range(6):
        p = tmp_path / f"f{i}.wav"
        p.write_bytes(b"x" * (10 + i))
        paths.append(p)
    return paths


def test_cache_returns_what_was_put(sample_files):
    cache = WaveformCache()
    data = make_data([(-1, 1)] * 10)
    cache.put(sample_files[0], data)
    assert cache.get(sample_files[0]) is data
    assert cache.get(sample_files[1]) is None


def test_cache_evicts_the_oldest(sample_files):
    cache = WaveformCache(max_entries=3)
    data = make_data([(-1, 1)] * 10)
    for path in sample_files[:5]:
        cache.put(path, data)

    assert cache.count == 3
    assert cache.get(sample_files[0]) is None, "古いものが残っている"
    assert cache.get(sample_files[4]) is not None


def test_cache_keeps_recently_used(sample_files):
    """使ったものは新しい扱いになる（LRU）。"""
    cache = WaveformCache(max_entries=2)
    data = make_data([(-1, 1)] * 10)
    cache.put(sample_files[0], data)
    cache.put(sample_files[1], data)
    cache.get(sample_files[0])          # 0 を触る
    cache.put(sample_files[2], data)    # 押し出されるのは 1

    assert cache.get(sample_files[0]) is not None
    assert cache.get(sample_files[1]) is None


def test_cache_respects_the_byte_cap(sample_files):
    big = make_data([(-1, 1)] * 1000)   # 4000 bytes
    cache = WaveformCache(max_entries=100, max_bytes=9000)
    for path in sample_files[:5]:
        cache.put(path, big)
    assert cache.count == 2
    assert cache.nbytes <= 9000


def test_cache_discard(sample_files):
    cache = WaveformCache()
    cache.put(sample_files[0], make_data([(-1, 1)]))
    assert cache.discard(sample_files[0]) is True
    assert cache.get(sample_files[0]) is None
    assert cache.count == 0
    assert cache.nbytes == 0
    assert cache.discard(sample_files[0]) is False


def test_cache_invalidates_when_the_file_changes(tmp_path):
    """中身が変わったら作り直す（mtime とサイズを見ている）。"""
    path = tmp_path / "a.wav"
    path.write_bytes(b"x" * 100)
    cache = WaveformCache()
    cache.put(path, make_data([(-1, 1)]))
    assert cache.get(path) is not None

    time.sleep(0.01)
    path.write_bytes(b"y" * 200)
    assert cache.get(path) is None


def test_cache_clear(sample_files):
    cache = WaveformCache()
    for path in sample_files[:3]:
        cache.put(path, make_data([(-1, 1)]))
    cache.clear()
    assert cache.count == 0
    assert cache.nbytes == 0


# ---------------------------------------------------------------------------
# 実際の生成（ffmpeg が要る）
# ---------------------------------------------------------------------------
pytestmark_ffmpeg = pytest.mark.ffmpeg


@pytest.mark.ffmpeg
def test_extract_produces_the_expected_shape(workspace, ffmpeg_tools):
    (source,) = workspace.copy("sample.flac")
    data = extract_waveform(source, ffmpeg_tools, 5.0)

    assert data.duration == 5.0
    assert data.buckets > 1000
    assert data.nbytes == data.buckets * 4
    env = data.envelope(200)
    assert len(env) == 200
    assert all(-1.0 <= low <= high <= 1.0 for low, high in env)
    assert max(high for _, high in env) > 0.01, "音があるのに真っ平ら"


@pytest.mark.ffmpeg
def test_extract_matches_the_real_peak(workspace, ffmpeg_tools):
    """ffmpeg が報告するピークと一致すること。"""
    (source,) = workspace.copy("sample.flac")
    result = subprocess.run(
        [ffmpeg_tools.ffmpeg, "-hide_banner", "-v", "info", "-i", str(source),
         "-af", "astats=metadata=1", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        **subprocess_flags(),
    )
    peak_db = None
    for line in result.stderr.splitlines():
        if "Peak level dB" in line:
            peak_db = float(line.split(":")[-1].strip())
    assert peak_db is not None, "astats からピークが取れない"
    expected = 10 ** (peak_db / 20)

    data = extract_waveform(source, ffmpeg_tools, 5.0)
    got = max(high for _, high in data.envelope(1000))
    assert got == pytest.approx(expected, rel=0.05)


@pytest.mark.ffmpeg
def test_extract_reflects_a_quiet_file(workspace, ffmpeg_tools, tmp_path):
    """音量が半分のファイルは振幅も半分になる。"""
    (source,) = workspace.copy("sample.flac")
    quiet = tmp_path / "quiet.flac"
    subprocess.run(
        [ffmpeg_tools.ffmpeg, "-hide_banner", "-v", "error", "-y", "-i", str(source),
         "-af", "volume=-6dB", "-c:a", "flac", str(quiet)],
        capture_output=True, **subprocess_flags(),
    )
    loud_peak = max(h for _, h in extract_waveform(source, ffmpeg_tools, 5.0).envelope(500))
    quiet_peak = max(h for _, h in extract_waveform(quiet, ffmpeg_tools, 5.0).envelope(500))
    assert quiet_peak == pytest.approx(loud_peak * 0.5, rel=0.08)


@pytest.mark.ffmpeg
def test_extract_streams_without_hoarding_memory(workspace, ffmpeg_tools):
    """長いファイルでも保持するのはピーク列だけ。"""
    (source,) = workspace.copy("long.mp3")
    data = extract_waveform(source, ffmpeg_tools, 300.0)
    assert data.nbytes < 20_000, f"データが大きすぎる: {data.nbytes}"


@pytest.mark.ffmpeg
def test_extract_rejects_unknown_duration(workspace, ffmpeg_tools):
    (source,) = workspace.copy("sample.flac")
    with pytest.raises(ProbeError, match="長さが不明"):
        extract_waveform(source, ffmpeg_tools, 0.0)


@pytest.mark.ffmpeg
def test_extract_can_be_cancelled(workspace, ffmpeg_tools):
    (source,) = workspace.copy("long.mp3")
    cancel = threading.Event()
    outcome: dict[str, object] = {}

    def worker():
        try:
            extract_waveform(source, ffmpeg_tools, 300.0, cancel=cancel)
            outcome["done"] = True
        except WaveformCancelled:
            outcome["cancelled"] = True
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = repr(exc)

    thread = threading.Thread(target=worker)
    started = time.monotonic()
    thread.start()
    time.sleep(0.05)
    cancel.set()
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert "cancelled" in outcome or "done" in outcome, outcome
    assert time.monotonic() - started < 10


@pytest.mark.ffmpeg
def test_long_file_stays_within_a_reasonable_time(workspace, ffmpeg_tools):
    """5 分のファイルが数秒で終わること（1 時間でも実測 3.3 秒）。"""
    (source,) = workspace.copy("long.mp3")
    started = time.monotonic()
    extract_waveform(source, ffmpeg_tools, 300.0)
    elapsed = time.monotonic() - started
    assert elapsed < 10.0, f"5 分のファイルに {elapsed:.1f} 秒かかった"
