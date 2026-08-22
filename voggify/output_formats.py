"""出力形式の定義。

出力できる形式（OGG Vorbis / MP3）ごとに、拡張子・エンコーダー・
品質の扱い・サイズ予測をまとめて持たせる。形式を増やすときは
OUTPUT_FORMATS に 1 つ足せば、変換コアも UI も追従する。

品質スライダーは全形式で共通の「0〜10、大きいほど高音質」にしている。
libvorbis はそのままの向きだが、LAME は 0〜9 で小さいほど高音質と
逆向きなので、encoder_args() の中で変換する。UI にこの差を漏らさない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: 共通の品質スケール（大きいほど高音質）
MIN_QUALITY: Final[int] = 0
MAX_QUALITY: Final[int] = 10
DEFAULT_QUALITY: Final[int] = 6


def clamp_quality(quality: int) -> int:
    """品質値を 0〜10 に丸める。"""
    return max(MIN_QUALITY, min(MAX_QUALITY, int(quality)))


@dataclass(frozen=True)
class OutputFormat:
    """変換先の形式ひとつぶん。"""

    #: 設定ファイルに保存する識別子。変えると保存済み設定が読めなくなる
    key: str
    #: 画面表示用の名前
    label: str
    extension: str
    #: ffmpeg の -c:a に渡すエンコーダー名
    encoder: str
    #: ffmpeg の -f に渡すコンテナ名
    container: str
    #: このエンコーダーが無いときの案内に使う
    encoder_label: str
    #: 共通品質 0〜10 → エンコーダー固有の -q:a 値。添字が共通品質。
    #: 尺度の向きも本数の違いもここで吸収する（計算より表の方が読める）。
    quality_map: tuple[int, ...]
    #: エンコーダー固有の -q:a 値 → 公称ビットレート（kbps / 44.1kHz ステレオ）
    encoder_kbps: dict[int, int]
    #: 出力と同じ形式とみなす codec_name。入力に来たら弾く
    same_as_output_codecs: frozenset[str]

    # ------------------------------------------------------------------
    def encoder_quality(self, quality: int) -> int:
        """共通の品質値をエンコーダーの尺度に直す。"""
        return self.quality_map[clamp_quality(quality)]

    def encoder_args(self, quality: int) -> list[str]:
        """ffmpeg に渡すエンコード指定。"""
        return ["-c:a", self.encoder, "-q:a", str(self.encoder_quality(quality))]

    def nominal_bitrate_bps(self, quality: int, channels: int = 2) -> int:
        """指定品質での想定ビットレート（bps）。

        公称値は 44.1kHz ステレオ基準なので、モノラルはおおよそ 0.6 倍、
        3ch 以上はチャンネル数に比例するものとして補正する。
        """
        kbps = self.encoder_kbps[self.encoder_quality(quality)]
        factor = 0.6 if channels <= 1 else channels / 2.0
        return int(kbps * 1000 * factor)

    def estimate_size(
        self, duration_sec: float | None, quality: int, channels: int = 2
    ) -> int | None:
        """変換後のファイルサイズ（バイト）を概算する。"""
        if duration_sec is None or duration_sec <= 0:
            return None
        return int(self.nominal_bitrate_bps(quality, channels) * duration_sec / 8)

    def quality_hint(self, quality: int) -> str:
        """スライダーの横に出す目安。"""
        kbps = self.nominal_bitrate_bps(quality, 2) // 1000
        return (
            f"ステレオでおおよそ {kbps} kbps 相当"
            f"（{self.encoder} -q:a {self.encoder_quality(quality)}）"
        )


#: OGG Vorbis。libvorbis の -q:a は 0〜10 で大きいほど高音質。共通尺度と同じ向き。
OGG_VORBIS: Final[OutputFormat] = OutputFormat(
    key="ogg",
    label="OGG Vorbis",
    extension=".ogg",
    encoder="libvorbis",
    container="ogg",
    encoder_label="libvorbis",
    quality_map=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
    encoder_kbps={
        0: 64, 1: 80, 2: 96, 3: 112, 4: 128, 5: 160,
        6: 192, 7: 224, 8: 256, 9: 320, 10: 500,
    },
    same_as_output_codecs=frozenset({"vorbis"}),
)

#: MP3。LAME の -q:a（-V 相当）は 0〜9 で小さいほど高音質なので逆に並べる。
#: 共通品質は 11 段、LAME は 10 段なので最上位だけ V0 に重なる
#: （MP3 の VBR は 245kbps 付近が上限で、それ以上は伸びない）。
MP3: Final[OutputFormat] = OutputFormat(
    key="mp3",
    label="MP3",
    extension=".mp3",
    encoder="libmp3lame",
    container="mp3",
    encoder_label="libmp3lame",
    quality_map=(9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0),
    encoder_kbps={
        0: 245, 1: 225, 2: 190, 3: 175, 4: 165,
        5: 130, 6: 115, 7: 100, 8: 85, 9: 65,
    },
    same_as_output_codecs=frozenset({"mp3", "mp3float"}),
)

#: 選択できる出力形式（UI に並ぶ順）
OUTPUT_FORMATS: Final[tuple[OutputFormat, ...]] = (OGG_VORBIS, MP3)

#: 既定の出力形式
DEFAULT_OUTPUT_FORMAT: Final[OutputFormat] = OGG_VORBIS


def output_format_by_key(key: str | None) -> OutputFormat | None:
    """設定ファイルの識別子から出力形式を引く。不明なら None。"""
    if not key:
        return None
    for fmt in OUTPUT_FORMATS:
        if fmt.key == key:
            return fmt
    return None


__all__ = [
    "DEFAULT_OUTPUT_FORMAT",
    "DEFAULT_QUALITY",
    "MAX_QUALITY",
    "MIN_QUALITY",
    "MP3",
    "OGG_VORBIS",
    "OUTPUT_FORMATS",
    "OutputFormat",
    "clamp_quality",
    "output_format_by_key",
]
