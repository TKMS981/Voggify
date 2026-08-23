"""対応フォーマットの定義と、出力サイズ予測などの補助関数。"""

from __future__ import annotations

from typing import Final

from . import output_formats as _out

#: 動画コンテナの拡張子。中の音声トラックだけを取り出して変換する。
#: 音声ファイルと違い「拡張子から期待されるコーデック」が定まらないので、
#: 食い違いの注記は出さない（EXPECTED_CODECS_BY_EXTENSION に載せない）。
VIDEO_EXTENSIONS: Final[frozenset[str]] = frozenset({".mp4", ".mkv"})

#: 受け付ける拡張子（小文字・ドット付き）。
#: .ogg / .oga は Ogg コンテナなので中身が Vorbis 以外（Opus / FLAC / Speex）の
#: ときだけ変換対象になる。中身が Vorbis のものは出力と同じ形式なので弾く。
SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".oga"} | VIDEO_EXTENSIONS
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
        # Ogg コンテナに入りうるコーデック。
        # vorbis も入力としては対応する（MP3 へ変換できる）。
        # 出力と同じ形式かどうかは OutputFormat.same_as_output_codecs で見る。
        "vorbis",
        "opus",
        "speex",
        # MP4 / MKV に入りうる音声コーデック。ffmpeg はいずれもデコードできる
        # （エンコーダーは不要。Voggify は常に OGG / MP3 へ再エンコードする）。
        "ac3",
        "eac3",
        "dts",
        "truehd",
        "mp2",
        "wmav2",
    }
)

#: 出力できる形式のコーデック全部。どれかに当たる入力は
#: 「その形式へは変換不要」の判定対象になる（実際の可否は出力形式ごとに決まる）。
ALL_OUTPUT_CODECS: Final[frozenset[str]] = frozenset(
    codec for fmt in _out.OUTPUT_FORMATS for codec in fmt.same_as_output_codecs
)

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
    "ac3": "AC-3",
    "eac3": "E-AC-3",
    "dts": "DTS",
    "truehd": "TrueHD",
    "mp2": "MP2",
    "wmav2": "WMA",
}

#: 音声トラックの language タグ → 画面表示用の名前。
#: ffprobe は ISO 639-2/B の 3 文字（jpn / eng）で返すことが多いが、
#: MP4 では 2 文字（ja / en）のこともあるので両方を引けるようにしている。
#: 表に無いタグはそのまま大文字で出す（"tha" → "THA"）。
LANGUAGE_NAMES: Final[dict[str, str]] = {
    "jpn": "日本語", "ja": "日本語",
    "eng": "英語", "en": "英語",
    "chi": "中国語", "zho": "中国語", "zh": "中国語",
    "kor": "韓国語", "ko": "韓国語",
    "fre": "フランス語", "fra": "フランス語", "fr": "フランス語",
    "ger": "ドイツ語", "deu": "ドイツ語", "de": "ドイツ語",
    "spa": "スペイン語", "es": "スペイン語",
    "ita": "イタリア語", "it": "イタリア語",
    "por": "ポルトガル語", "pt": "ポルトガル語",
    "rus": "ロシア語", "ru": "ロシア語",
    "tha": "タイ語", "th": "タイ語",
    "vie": "ベトナム語", "vi": "ベトナム語",
    "ind": "インドネシア語", "id": "インドネシア語",
    "ara": "アラビア語", "ar": "アラビア語",
    "hin": "ヒンディー語", "hi": "ヒンディー語",
}

#: 「言語不明」を表すタグ。これらは言語名として扱わない。
UNKNOWN_LANGUAGE_TAGS: Final[frozenset[str]] = frozenset({"und", "unk", "mis", "zxx"})

#: 品質スケールと既定の出力形式は output_formats.py が持つ。
#: ここでは従来どおりの名前で使えるように再エクスポートしている。
MIN_QUALITY = _out.MIN_QUALITY
MAX_QUALITY = _out.MAX_QUALITY
DEFAULT_QUALITY = _out.DEFAULT_QUALITY
clamp_quality = _out.clamp_quality

#: 既定の出力拡張子（形式を指定しない場合に使う）
OUTPUT_EXTENSION: Final[str] = _out.DEFAULT_OUTPUT_FORMAT.extension


def is_supported_extension(filename: str) -> bool:
    """拡張子だけで大まかに判定する（D&D 時の一次フィルタ用）。"""
    lowered = filename.lower()
    return any(lowered.endswith(ext) for ext in SUPPORTED_EXTENSIONS)


def is_video_extension(filename: str) -> bool:
    """動画コンテナの拡張子か（音声トラックを取り出す対象か）。"""
    lowered = filename.lower()
    return any(lowered.endswith(ext) for ext in VIDEO_EXTENSIONS)


def display_language_name(language: str | None) -> str | None:
    """language タグを画面表示用の名前にする。

    不明・未設定なら None（呼び出し側でトラック番号にフォールバックする）。
    """
    if not language:
        return None
    tag = language.strip().lower()
    if not tag or tag in UNKNOWN_LANGUAGE_TAGS:
        return None
    return LANGUAGE_NAMES.get(tag, tag.upper())


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


def nominal_bitrate_bps(
    quality: int, channels: int = 2, output_format: "_out.OutputFormat | None" = None
) -> int:
    """指定品質での想定ビットレート（bps）を返す。"""
    fmt = output_format or _out.DEFAULT_OUTPUT_FORMAT
    return fmt.nominal_bitrate_bps(quality, channels)


def estimate_output_size(
    duration_sec: float | None,
    quality: int,
    channels: int = 2,
    output_format: "_out.OutputFormat | None" = None,
) -> int | None:
    """変換後のファイルサイズ（バイト）を概算する。

    再生時間が不明な場合は None を返す。VBR なので誤差は数割ある前提。
    """
    fmt = output_format or _out.DEFAULT_OUTPUT_FORMAT
    return fmt.estimate_size(duration_sec, quality, channels)


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
