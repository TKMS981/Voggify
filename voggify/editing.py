"""簡易編集（トリミングと音量調整）のパラメータ。

ファイルごとに持つ値なので、変換設定（ConversionOptions）とは分けている。
Qt に依存しないので GUI なしでテストできる。

ffmpeg への渡し方
-----------------
トリミングは **入力側**（`-i` の前）に `-ss` と `-t` を置く。実測した結果:

* 精度は入力側・出力側とも同じだった。1 秒ごとに周波数が変わる音源を
  切り出して中身を確認したところ、どちらも狙った位置から始まり、
  長さの誤差も 0ms だった（WAV / MP3 とも）。
* 速度は入力側が有利。30 分の音源の 1700 秒地点から 60 秒を切り出す場合、
  入力側 0.64 秒に対して出力側は 1.14 秒かかった。出力側 `-ss` は先頭から
  デコードして捨てるのに対し、入力側はそこまでシークするため。

音量は `-af volume=XdB`。Voggify は常に再エンコードする（`-c copy` は
使わない）ので、フィルタを足しても変換の流れは変わらない。

音声トラックの選択
------------------
MP4 / MKV は音声トラックを複数持てるので、どれを使うかもファイルごとの
値としてここに持つ。`-map 0:a:{audio_track}` として渡す。既定の 0 は
「先頭の音声トラック」で、音声ファイルは常にこれになる。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

#: 音量調整の範囲（dB）
MIN_VOLUME_DB = -30.0
MAX_VOLUME_DB = 30.0
DEFAULT_VOLUME_DB = 0.0

#: これ未満の差は「変更なし」とみなす（浮動小数の誤差対策）
VOLUME_EPSILON = 0.05
TIME_EPSILON = 0.001

#: トリミングで残す最短の長さ（秒）。これ以下にはできない。
MIN_TRIM_DURATION = 0.1

#: 既定の音声トラック（先頭）。音声ファイルは常にこれ。
DEFAULT_AUDIO_TRACK = 0


class EditValueError(ValueError):
    """編集パラメータが不正なときに投げる。UI 側で警告に使う。"""


@dataclass(frozen=True)
class EditSettings:
    """1 ファイル分の編集内容。"""

    #: 切り出しの開始位置（秒）
    trim_start: float = 0.0
    #: 切り出しの終了位置（秒）。None なら最後まで。
    trim_end: float | None = None
    #: 音量の増減（dB）。0.0 なら変更なし。
    volume_db: float = DEFAULT_VOLUME_DB
    #: 使う音声トラック（0 始まり）。`-map 0:a:N` の N。
    audio_track: int = DEFAULT_AUDIO_TRACK

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    @property
    def has_trim(self) -> bool:
        return self.trim_start > TIME_EPSILON or self.trim_end is not None

    @property
    def has_volume(self) -> bool:
        return abs(self.volume_db) > VOLUME_EPSILON

    @property
    def has_track_selection(self) -> bool:
        """先頭以外の音声トラックを選んでいるか。"""
        return self.audio_track != DEFAULT_AUDIO_TRACK

    @property
    def is_default(self) -> bool:
        """何も編集していない状態か。真なら ffmpeg に余計な引数を足さない。

        トラック選択も「既定と違う設定」なので含める。-map 自体は常に
        付けるが、一覧の編集マークとツールチップに出したいため。
        """
        return (
            not self.has_trim
            and not self.has_volume
            and not self.has_track_selection
        )

    # ------------------------------------------------------------------
    # 長さ
    # ------------------------------------------------------------------
    def effective_end(self, source_duration: float | None) -> float | None:
        """実際の終了位置。trim_end が未設定なら元の全長。"""
        if self.trim_end is not None:
            if source_duration is not None:
                return min(self.trim_end, source_duration)
            return self.trim_end
        return source_duration

    def effective_duration(self, source_duration: float | None) -> float | None:
        """トリミング後の長さ（秒）。不明なら None。

        進捗計算とサイズ予測はこの値を基準にする。
        """
        end = self.effective_end(source_duration)
        if end is None:
            return None
        return max(0.0, end - self.trim_start)

    # ------------------------------------------------------------------
    # ffmpeg 引数
    # ------------------------------------------------------------------
    def input_args(self, source_duration: float | None) -> list[str]:
        """`-i` の前に置く引数（トリミング）。"""
        if not self.has_trim:
            return []
        args: list[str] = []
        if self.trim_start > TIME_EPSILON:
            args += ["-ss", _format_seconds(self.trim_start)]
        duration = self.effective_duration(source_duration)
        # 元の全長まで使う場合は -t を付けない（余計な指定をしない）
        if (
            self.trim_end is not None
            and duration is not None
            and (source_duration is None or duration < source_duration - TIME_EPSILON)
        ):
            args += ["-t", _format_seconds(duration)]
        return args

    def map_args(self) -> list[str]:
        """使う音声トラックを指定する `-map`。

        映像やカバーアートを落とすために元から常に付けている引数なので、
        トラックを選んでいない場合も 0:a:0 を返す（従来と同じ）。
        """
        return ["-map", f"0:a:{max(0, self.audio_track)}"]

    def filter_args(self) -> list[str]:
        """`-af` に渡すフィルタ（音量）。"""
        if not self.has_volume:
            return []
        return ["-af", f"volume={self.volume_db:.2f}dB"]

    # ------------------------------------------------------------------
    # 変更（frozen なので新しいインスタンスを返す）
    # ------------------------------------------------------------------
    def with_trim(
        self, start: float, end: float | None, source_duration: float | None
    ) -> "EditSettings":
        """トリミングを設定する。不正なら EditValueError。"""
        validate_trim(start, end, source_duration)
        # 全体を使う指定なら None に寄せて「編集なし」に戻す
        if end is not None and source_duration is not None:
            if end >= source_duration - TIME_EPSILON:
                end = None
        return replace(self, trim_start=max(0.0, start), trim_end=end)

    def with_volume(self, volume_db: float) -> "EditSettings":
        return replace(self, volume_db=clamp_volume(volume_db))

    def without_trim(self) -> "EditSettings":
        return replace(self, trim_start=0.0, trim_end=None)

    def without_volume(self) -> "EditSettings":
        return replace(self, volume_db=DEFAULT_VOLUME_DB)

    def with_track(self, index: int) -> "EditSettings":
        """使う音声トラックを差し替える。

        トリミングと音量はトラックが変わっても意味が変わらないので
        そのまま持ち越す（同じ長さの別音声、という想定）。
        """
        return replace(self, audio_track=max(0, int(index)))

    # ------------------------------------------------------------------
    # 表示
    # ------------------------------------------------------------------
    def badge(self) -> str:
        """一覧に出す短い印。編集なしなら空文字。"""
        marks: list[str] = []
        if self.has_track_selection:
            # 表示は 1 始まり（内部は 0 始まり）
            marks.append(f"♪{self.audio_track + 1}")
        if self.has_trim:
            marks.append("✂")
        if self.has_volume:
            marks.append(f"{self.volume_db:+.1f}dB")
        return " ".join(marks)

    def describe(self, source_duration: float | None, track_label: str = "") -> str:
        """ツールチップ用の説明。編集なしなら空文字。

        track_label にトラックの表示名を渡すと、選択中のトラックも並べる。
        """
        if self.is_default:
            return ""
        lines: list[str] = []
        if self.has_track_selection:
            suffix = f"（{track_label}）" if track_label else ""
            lines.append(f"音声トラック: {self.audio_track + 1}{suffix}")
        if self.has_trim:
            end = self.effective_end(source_duration)
            lines.append(
                f"切り出し: {format_timecode(self.trim_start)}"
                f" 〜 {format_timecode(end) if end is not None else '最後'}"
                f"（{format_timecode(self.effective_duration(source_duration))}）"
            )
        if self.has_volume:
            lines.append(f"音量: {self.volume_db:+.1f} dB")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------
def clamp_volume(volume_db: float) -> float:
    """音量を範囲内に丸める。"""
    if math.isnan(volume_db):
        return DEFAULT_VOLUME_DB
    return max(MIN_VOLUME_DB, min(MAX_VOLUME_DB, round(float(volume_db), 2)))


def validate_trim(
    start: float, end: float | None, source_duration: float | None
) -> None:
    """トリミングの値を検査する。駄目なら EditValueError。"""
    if start < 0:
        raise EditValueError("開始位置が負の値です。")
    if end is not None:
        if end <= start:
            raise EditValueError("終了位置は開始位置より後にしてください。")
        if end - start < MIN_TRIM_DURATION:
            raise EditValueError(
                f"切り出す長さが短すぎます（{MIN_TRIM_DURATION} 秒以上にしてください）。"
            )
    if source_duration is not None:
        if start >= source_duration - TIME_EPSILON:
            raise EditValueError(
                f"開始位置がファイルの長さ（{format_timecode(source_duration)}）を超えています。"
            )
        if end is not None and end > source_duration + TIME_EPSILON:
            raise EditValueError(
                f"終了位置がファイルの長さ（{format_timecode(source_duration)}）を超えています。"
            )


# ---------------------------------------------------------------------------
# 時間の表記
# ---------------------------------------------------------------------------
#: mm:ss.ms / m:ss / ss.ms などを受け付ける
_TIMECODE_RE = re.compile(
    r"^\s*(?:(?P<h>\d+):)?(?:(?P<m>\d+):)?(?P<s>\d+(?:\.\d+)?)\s*$"
)


def format_timecode(seconds: float | None) -> str:
    """秒を mm:ss.ms 形式にする。1 時間以上なら h:mm:ss.ms。"""
    if seconds is None or seconds < 0:
        return "0:00.000"
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    hours, remainder = divmod(total_s, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{minutes}:{secs:02d}.{ms:03d}"


def parse_timecode(text: str) -> float:
    """mm:ss.ms などの文字列を秒に直す。解釈できなければ EditValueError。"""
    match = _TIMECODE_RE.match(text or "")
    if not match:
        raise EditValueError(
            f"時間の書き方が不正です: {text!r}（例: 1:23.500 / 83.5）"
        )
    hours = match.group("h")
    minutes = match.group("m")
    secs = float(match.group("s"))

    # "1:23" は 1 分 23 秒。"1:2:3" は 1 時間 2 分 3 秒。
    if hours is not None and minutes is not None:
        total = int(hours) * 3600 + int(minutes) * 60 + secs
    elif minutes is not None:
        total = int(minutes) * 60 + secs
    elif hours is not None:
        total = int(hours) * 60 + secs
    else:
        total = secs

    if secs >= 60 and (minutes is not None or hours is not None):
        raise EditValueError(f"秒は 60 未満で指定してください: {text!r}")
    return total


def _format_seconds(seconds: float) -> str:
    """ffmpeg に渡す秒数（小数 3 桁）。"""
    return f"{seconds:.3f}"


__all__ = [
    "DEFAULT_AUDIO_TRACK",
    "DEFAULT_VOLUME_DB",
    "EditSettings",
    "EditValueError",
    "MAX_VOLUME_DB",
    "MIN_TRIM_DURATION",
    "MIN_VOLUME_DB",
    "clamp_volume",
    "format_timecode",
    "parse_timecode",
    "validate_trim",
]
