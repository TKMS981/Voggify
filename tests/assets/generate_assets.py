"""テスト用の音声アセットを生成する。

生成済みのファイルはリポジトリにコミットしてあるので、通常は実行不要。
アセットを作り直したいときや、内容を確認したいときに使う。

    python tests/assets/generate_assets.py

いずれも ffmpeg の lavfi で作った 440Hz のサイン波で、著作物は含まない。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from voggify.ffmpeg_locator import ensure_ffmpeg_tools, subprocess_flags  # noqa: E402

#: 短いサンプルの長さ（秒）。フォーマット判定の確認用なので短くてよい。
SAMPLE_SECONDS = 5

#: 長いサンプル。変換の進捗とキャンセルを試すため、
#: 変換に 2 秒程度かかる長さが要る。低ビットレートの MP3 にして
#: リポジトリに置ける大きさ（約 1.2MB）に収めている。
LONG_SECONDS = 300
LONG_BITRATE = "32k"

#: multi_audio.mkv の各音声トラックの周波数（Hz）。
#: どのトラックが取り出されたかを、変換結果の周波数で判定できるようにする。
MULTI_FREQUENCIES = (440, 880, 1320)

#: (ファイル名, ffmpeg の出力オプション)
SHORT_SAMPLES: tuple[tuple[str, list[str]], ...] = (
    ("sample.wav", ["-c:a", "pcm_s16le"]),
    ("sample.mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
    ("sample.flac", ["-c:a", "flac"]),
    ("sample.m4a", ["-c:a", "aac", "-b:a", "192k"]),
    # --- Ogg コンテナ ---
    # 中身が Vorbis 以外なら変換対象になる
    ("opus.ogg", ["-c:a", "libopus", "-b:a", "96k", "-f", "ogg"]),
    ("flac.oga", ["-c:a", "flac", "-f", "ogg"]),
    # 中身が Vorbis のものは「既に OGG Vorbis です」で弾かれる
    ("vorbis.ogg", ["-c:a", "libvorbis", "-q:a", "5", "-f", "ogg"]),
    # 拡張子で弾かれる想定のフォーマット
    ("notsupported.opus", ["-c:a", "libopus", "-b:a", "64k"]),
)


def run(argv: list[str]) -> None:
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **subprocess_flags(),  # type: ignore[arg-type]
    )
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg 失敗 ({result.returncode}):\n{result.stderr}")


def main() -> int:
    tools = ensure_ffmpeg_tools()
    base = [tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    tone = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SAMPLE_SECONDS}"]
    tags = ["-metadata", "title=TestTone", "-metadata", "artist=Voggify"]

    for name, codec_options in SHORT_SAMPLES:
        run(base + tone + tags + codec_options + [str(HERE / name)])
        print(f"  {name}")

    # 進捗・キャンセル確認用の長いファイル
    run(
        base
        + ["-f", "lavfi", "-i", f"sine=frequency=440:duration={LONG_SECONDS}"]
        + ["-ac", "1", "-c:a", "libmp3lame", "-b:a", LONG_BITRATE]
        + [str(HERE / "long.mp3")]
    )
    print("  long.mp3")

    # 映像入り。音声 1 本の MP4（拡張子を偽装した fake.mp3 の元にもなる）
    run(
        base
        + ["-f", "lavfi", "-i", f"testsrc=duration={SAMPLE_SECONDS}:size=320x240:rate=10"]
        + ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SAMPLE_SECONDS}"]
        + ["-c:v", "libx264", "-c:a", "aac", str(HERE / "video.mp4")]
    )
    print("  video.mp4")

    # 音声トラックが 3 本の MKV。言語タグとトラック名を付けて、
    # 選択 UI の表示名（言語 / トラック名 / 番号フォールバック）を全部試せるようにする。
    # トラックごとに周波数を変えてあるので、狙ったトラックが出たか音で判定できる。
    run(
        base
        + ["-f", "lavfi", "-i", f"testsrc=duration={SAMPLE_SECONDS}:size=320x240:rate=10"]
        + ["-f", "lavfi", "-i", f"sine=frequency={MULTI_FREQUENCIES[0]}:duration={SAMPLE_SECONDS}"]
        + ["-f", "lavfi", "-i", f"sine=frequency={MULTI_FREQUENCIES[1]}:duration={SAMPLE_SECONDS}"]
        + ["-f", "lavfi", "-i", f"sine=frequency={MULTI_FREQUENCIES[2]}:duration={SAMPLE_SECONDS}"]
        + ["-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a"]
        + ["-c:v", "libx264", "-c:a:0", "aac", "-c:a:1", "aac", "-c:a:2", "libmp3lame"]
        # a:0 = 言語 + トラック名 / a:1 = 言語のみ / a:2 = タグ無し（番号へ落ちる）
        + ["-metadata:s:a:0", "language=jpn", "-metadata:s:a:0", "title=Main"]
        + ["-metadata:s:a:1", "language=eng"]
        + ["-ac:a:2", "2"]
        + [str(HERE / "multi_audio.mkv")]
    )
    print("  multi_audio.mkv (音声 3 本 / jpn+title, eng, タグ無し)")

    # 音声トラックが 2 本の MP4。MKV 以外でも同じように扱えることの確認用。
    run(
        base
        + ["-f", "lavfi", "-i", f"testsrc=duration={SAMPLE_SECONDS}:size=320x240:rate=10"]
        + ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SAMPLE_SECONDS}"]
        + ["-f", "lavfi", "-i", f"sine=frequency=880:duration={SAMPLE_SECONDS}"]
        + ["-map", "0:v", "-map", "1:a", "-map", "2:a"]
        + ["-c:v", "libx264", "-c:a", "aac"]
        + ["-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "language=eng"]
        + [str(HERE / "multi_audio.mp4")]
    )
    print("  multi_audio.mp4 (音声 2 本 / jpn, eng)")

    # 映像だけで音声が無い MP4。「対応外」としてエラーになることの確認用。
    run(
        base
        + ["-f", "lavfi", "-i", f"testsrc=duration={SAMPLE_SECONDS}:size=320x240:rate=10"]
        + ["-an", "-c:v", "libx264", str(HERE / "silent_video.mp4")]
    )
    print("  silent_video.mp4 (音声トラック無し)")
    # 中身は AAC(mp4) だが拡張子は .mp3 —「拡張子と実体の食い違い」の確認用
    shutil.copyfile(HERE / "video.mp4", HERE / "fake.mp3")
    print("  fake.mp3   (video.mp4 のコピー / 拡張子を偽装)")

    # 解析に失敗するファイル
    (HERE / "broken.mp3").write_bytes(b"this is definitely not audio data" * 100)
    print("  broken.mp3 (音声ではないデータ)")

    print(f"\n生成先: {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
