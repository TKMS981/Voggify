"""対応フォーマットの定義と、出力サイズ予測などの補助関数。"""

from __future__ import annotations

from typing import Final

#: 受け付ける拡張子（小文字・ドット付き）。
#: .ogg / .oga は Ogg コンテナなので中身が Vorbis 以外（Opus / FLAC / Speex）の
#: ときだけ変換対象になる。中身が Vorbis のものは出力と同じ形式なので弾く。
SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".oga"}
)

#: ffprobe が返す codec_name のうち、入力として許可するもの。
#: M4A / AAC コンテナには AAC のほか ALAC が入りうるため両方許可する。
SUPPORTED_CODECS: Final[frozenset[str]] = frozenset(
    {
        "mp3",
        "mp3float",
        "flac",
        "aac",
        "aac_latm",
        "alac",
        # WAV に入りうる非圧縮 / 単純圧縮 PCM 系
        "pcm_s16le",
        "pcm_s24le",
        "pcm_s32le",
        "pcm_u8",
        "pcm_f32le",
        "pcm_f64le",
        "pcm_s16be",
        "pcm_s24be",
        "pcm_s32be",
        "adpcm_ms",
        "adpcm_ima_wav",
        # Ogg コンテナに入りうる Vorbis 以外のコーデック
        "opus",
        "speex",
    }
)

#: 出力と同じ形式。入力として受け取っても変換する意味が無いので弾く。
OUTPUT_CODECS: Final[frozenset[str]] = frozenset({"vorbis"})

#: 拡張子から素直に期待されるコーデック。
#: ここに載っていない組み合わせは「拡張子と実体の食い違い」として注記する。
EXPECTED_CODECS_BY_EXTENSION: Final[dict[str, frozenset[str]]] = {
    ".mp3": frozenset({"mp3", "mp3float"}),
    ".wav": frozenset(
        {
            "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8",
            "pcm_f32le", "pcm_f64le", "pcm_s16be", "pcm_s24be", "pcm_s32be",
            "adpcm_ms", "adpcm_ima_wav",
        }
    ),
    ".flac": frozenset({"flac"}),
    ".aac": frozenset({"aac", "aac_latm"}),
    ".m4a": frozenset({"aac", "aac_latm", "alac"}),
    # Ogg コンテナに入りうる音声コーデック。vorbis も「食い違い」ではないので
    # 載せておく（受け入れ可否は SUPPORTED_CODECS / OUTPUT_CODECS 側で決める）。
    ".ogg": frozenset({"vorbis", "opus", "flac", "speex"}),
    ".oga": frozenset({"vorbis", "opus", "flac", "speex"}),
}

#: codec_name → 画面表示用の名前
CODEC_DISPLAY_NAMES: Final[dict[str, str]] = {
    "mp3": "MP3",
    "mp3float": "MP3",
    "flac": "FLAC",
    "aac": "AAC",
    "aac_latm": "AAC",
    "alac": "ALAC",
    "vorbis": "Vorbis",
    "opus": "Opus",
    "speex": "Speex",
    "pcm_u8": "PCM 8bit",
    "pcm_s16le": "PCM 16bit",
    "pcm_s16be": "PCM 16bit",
    "pcm_s24le": "PCM 24bit",
    "pcm_s24be": "PCM 24bit",
    "pcm_s32le": "PCM 32bit",
    "pcm_s32be": "PCM 32bit",
    "pcm_f32le": "PCM float32",
    "pcm_f64le": "PCM float64",
    "adpcm_ms": "ADPCM",
    "adpcm_ima_wav": "ADPCM",
}

#: libvorbis の -q:a に対する公称ビットレート（kbps / 44.1kHz ステレオ基準）
VORBIS_NOMINAL_KBPS: Final[dict[int, int]] = {
    0: 64,
    1: 80,
    2: 96,
    3: 112,
    4: 128,
    5: 160,
    6: 192,
    7: 224,
    8: 256,
    9: 320,
    10: 500,
}

MIN_QUALITY: Final[int] = 0
MAX_QUALITY: Final[int] = 10
DEFAULT_QUALITY: Final[int] = 6

#: OGG Vorbis の出力拡張子
OUTPUT_EXTENSION: Final[str] = ".ogg"


def is_supported_extension(filename: str) -> bool:
    """拡張子だけで大まかに判定する（D&D 時の一次フィルタ用）。"""
    lowered = filename.lower()
    return any(lowered.endswith(ext) for ext in SUPPORTED_EXTENSIONS)


def display_codec_name(codec_name: str) -> str:
    """codec_name を画面表示用の文字列にする。"""
    known = CODEC_DISPLAY_NAMES.get(codec_name)
    if known:
        return known
    # 表に無い PCM 系（pcm_s8 など）もそれらしく見せる
    if codec_name.startswith("adpcm_"):
        return "ADPCM"
    if codec_name.startswith("pcm_"):
        return "PCM"
    return codec_name.upper()


def clamp_quality(quality: int) -> int:
    """品質値を 0〜10 に丸める。"""
    return max(MIN_QUALITY, min(MAX_QUALITY, int(quality)))


def nominal_bitrate_bps(quality: int, channels: int = 2) -> int:
    """指定品質での想定ビットレート（bps）を返す。

    公称値は 44.1kHz ステレオ基準なので、モノラルはおおよそ 0.6 倍、
    3ch 以上はチャンネル数に比例するものとして補正する。
    """
    kbps = VORBIS_NOMINAL_KBPS[clamp_quality(quality)]
    if channels <= 1:
        factor = 0.6
    else:
        factor = channels / 2.0
    return int(kbps * 1000 * factor)


def estimate_output_size(
    duration_sec: float | None,
    quality: int,
    channels: int = 2,
) -> int | None:
    """変換後のファイルサイズ（バイト）を概算する。

    再生時間が不明な場合は None を返す。VBR なので誤差は数割ある前提。
    """
    if duration_sec is None or duration_sec <= 0:
        return None
    return int(nominal_bitrate_bps(quality, channels) * duration_sec / 8)


def format_estimated_size(size: int | None) -> str:
    """予測サイズを「約 4.1 MB」形式にする。VBR なのであくまで概算。"""
    if size is None:
        return "-"
    return f"約 {format_bytes(size)}"


def format_bytes(size: int | None) -> str:
    """バイト数を人間が読める文字列にする。"""
    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_duration(duration_sec: float | None) -> str:
    """秒数を mm:ss / h:mm:ss 形式にする。"""
    if duration_sec is None or duration_sec < 0:
        return "-"
    total = int(round(duration_sec))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
