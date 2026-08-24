# Voggify

音楽ファイルと動画ファイルの音声を **OGG Vorbis** または **MP3** に変換する
デスクトップアプリ。ドラッグ&ドロップでファイルを積み、形式・品質・出力先を
決めて一括変換する。

- 対応入力: MP3 / WAV / FLAC / AAC / M4A / OGG・OGA / MP4 / MKV（音声を抽出）
- 出力: OGG Vorbis (`.ogg`) / MP3 (`.mp3`)
- GUI: PySide6
- 変換エンジン: ffmpeg（ラッパーライブラリは使わず、コマンドを組み立てて subprocess 実行）

## 必要なもの

- Python 3.10 以上（3.12 で動作確認）
- PySide6 6.6 以上
- ffmpeg / ffprobe（**libvorbis を含むビルド**）

## インストール

```sh
git clone <このリポジトリ>
cd Voggify
python -m venv .venv
.venv\Scripts\activate        # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # テストを走らせる場合
```

ffmpeg は Python パッケージではないので別途インストールする。

```sh
# Windows
winget install Gyan.FFmpeg
# macOS（libvorbis 入り。素の ffmpeg は libvorbis 抜きで OGG が作れない）
brew install ffmpeg-full
# Debian / Ubuntu
sudo apt install ffmpeg
```

Homebrew の `ffmpeg` は libvorbis を含まずにビルドされているため、MP3 は作れても
OGG Vorbis へは変換できない。`ffmpeg-full` を入れること。keg-only で PATH には
入らないが、Voggify は下に挙げた場所を自動で探すのでそのままで認識される。

インストールできたか確認する。

```sh
python main.py check
```

### ffmpeg が見つからないとき

PATH に無くても、以下の場所は自動で探索する。

- `C:\ffmpeg\bin`, `C:\Program Files\ffmpeg\bin`, chocolatey / scoop のパス
- winget のポータブル配置（`%LOCALAPPDATA%\Microsoft\WinGet\Packages\*ffmpeg*\*\bin`）
- `/usr/bin`, `/usr/local/bin`, `/opt/homebrew/bin`, `/snap/bin`
- Homebrew の keg-only 配置（`/opt/homebrew/opt/ffmpeg*/bin`, `/usr/local/opt/ffmpeg*/bin`）

探索は 環境変数 → PATH → 上の既知の場所 の順。**PATH の ffmpeg に libvorbis が
無い場合は、既知の場所も見て libvorbis 入りのビルドがあればそちらを優先する。**
macOS で PATH に素の `ffmpeg`、keg-only に `ffmpeg-full` がある構成でも、
何も設定せずに OGG へ変換できるのはこのため。
環境変数で明示指定した場合は、その指定が常に最優先される。

それでも見つからない場合は環境変数で直接指定できる。実行ファイル・フォルダのどちらでもよい。

```sh
set VOGGIFY_FFMPEG=C:\path\to\ffmpeg.exe
set VOGGIFY_FFPROBE=C:\path\to\ffprobe.exe
```

ffmpeg が無くてもアプリは起動する。ウィンドウ上部に警告バーが出るので、
インストール後に「再確認」を押せば再起動なしで認識される。

## 使い方

```sh
python main.py                          # GUI を起動
python main.py <file> [<file> ...]      # GUI を起動してファイルを積む
```

### 操作

| 操作 | 方法 |
| --- | --- |
| ファイル追加 | ドラッグ&ドロップ / 「ファイルを選択…」/ `Ctrl+O` |
| フォルダ追加 | フォルダをドロップすると対応拡張子を再帰的に拾う |
| 個別削除 | 行を選んで `Delete`、または右クリック →「削除」 |
| 全削除 | 「全てクリア」 |
| 再解析 | 右クリック →「再解析」 |
| 出力形式 | 「変換設定」の OGG Vorbis / MP3 ラジオボタン（既定 OGG Vorbis） |
| 品質変更 | 「変換設定」の品質スライダー（0〜10、既定 6） |
| 出力先変更 | 「変換設定」→「フォルダを指定」→「参照…」 |
| 変換実行 | 「変換開始」（待機中の項目を上から順に処理） |
| 中断 | 変換中は同じボタンが「キャンセル」に変わる |
| 編集パネル | 「編集」ボタン / `Ctrl+E` |
| ログ表示 | 「ログ」ボタン / `Ctrl+L` |
| 詳細確認 | 行にマウスを乗せるとツールチップに再生時間・出力先などを表示 |

### 対応入力フォーマット

| 拡張子 | 受け付ける中身 |
| --- | --- |
| `.mp3` | MP3 |
| `.wav` | PCM / ADPCM |
| `.flac` | FLAC |
| `.aac` | AAC |
| `.m4a` | AAC / ALAC |
| `.ogg` `.oga` | Vorbis / Opus / FLAC / Speex |
| `.mp4` `.mkv` | 中の音声トラック（AAC / MP3 / AC-3 / DTS / Vorbis / Opus など） |

動画ファイルは**音声トラックだけを取り出す**（`-map 0:a:N`）。映像・字幕・
カバーアートは出力に入らない。音声トラックが 1 本も無い（映像だけの）ファイルは
「音声トラックが含まれていません」として対応外のエラーになる。

#### 複数音声トラック

MP4 / MKV は音声トラックを複数持てる（吹き替え・コメンタリーなど）。

- **既定は先頭のトラック**（トラック番号が最も若いもの）。今までどおり何も
  操作しなくても変換できる。
- 2 本以上あるファイルを選ぶと、**編集パネルにトラック選択のドロップダウン**が出る。
  1 本しか無いファイルではこの欄自体を出さないので、音声ファイルの見た目は変わらない。
- 一覧の「現在の形式」列に `♪3` のように本数が付く。ツールチップで全トラックを確認できる。
- 表示名は ffprobe が読めたタグから組み立てる。

  | タグの有無 | 表示 |
  | --- | --- |
  | `language` + `title` | `日本語 / コメンタリー` |
  | `language` のみ | `日本語` |
  | `title` のみ | `Director` |
  | どちらも無い（`und` を含む） | `トラック3` |

  末尾にコーデックとチャンネル数を添える（`日本語（AAC 2ch 48.0kHz）`）。
  同じ表示名が並んだ場合はトラック番号を足して区別する。
- 選んだトラックは**そのファイルの編集パラメータ**として持つので、ファイルごとに
  別々のトラックを選べる。波形・プレビュー再生も選択中のトラックに切り替わる
  （波形キャッシュはトラックごとに別データとして持つ）。
- CLI では `info` でトラック一覧を確認し、`convert --track N` で選ぶ。

動画コンテナは「中の音声を取り出す」のが目的なので、中身が出力と同じ形式でも
弾かない（`.mkv` のままでは使えないため）。音声ファイルの場合は従来どおり弾く。

**入力が出力と同じ形式の場合は弾く。** 再エンコードしても音質が落ちるだけなので、
「既に OGG Vorbis です」「既に MP3 です」と表示して変換対象から外す。
この判定は選んだ出力形式によって変わる。

| 入力 | → OGG Vorbis | → MP3 |
| --- | --- | --- |
| MP3 | 変換する | **弾く**（既に MP3） |
| OGG (Vorbis) | **弾く**（既に OGG Vorbis） | 変換する |
| WAV / FLAC / AAC / M4A / Opus | 変換する | 変換する |
| MP4 / MKV（動画） | 変換する | 変換する |

入力と出力の拡張子が同じになる場合（`.ogg` → OGG、`.mp3` → MP3 は弾かれるので
実際には `.ogg` → OGG のみ）、出力先が入力と同じフォルダだと
`podcast.ogg` → `podcast (1).ogg` のように別名になる。
元の名前のままにしたいときは出力先フォルダを指定する。
入力そのものを壊すことはなく、`--overwrite` を付けても
入力と出力が同じファイルになる場合はエラーで止まる。

### 品質スライダー

スライダーは**どの出力形式でも「0〜10、大きいほど高音質」**で共通。
エンコーダーごとの尺度の違いは内部で吸収している。

| 共通品質 | libvorbis `-q:a` | 目安 | libmp3lame `-q:a` | 目安 |
| --- | --- | --- | --- | --- |
| 0 | 0 | 64 kbps | 9 | 65 kbps |
| 6（既定） | 6 | 192 kbps | 3 | 175 kbps |
| 10 | 10 | 500 kbps | 0 | 245 kbps |

LAME は `-q:a` が**小さいほど高音質**という逆向きの尺度なので、そのまま渡すと
形式を切り替えた瞬間に音質が反転してしまう。これを避けるため
`output_formats.py` の対応表で変換している。実際に渡している値は
スライダー横のヒントに出るので確認できる（例: `libmp3lame -q:a 3`）。

MP3 の VBR は 245kbps 付近が上限なので、共通品質の 9 と 10 は同じ設定になる。

### 一覧の見かた

| 状態 | 意味 |
| --- | --- |
| 解析中 | ffprobe でファイルを調べている |
| 待機中 | 変換できる |
| 変換待ち / 変換中 / 完了 | 変換キューの進行状況 |
| 失敗 / 中断 | 変換が失敗、またはキャンセルされた |
| エラー | 対応外・破損などで変換対象にできない（グレーアウト表示） |

- 対応外のファイルや壊れたファイルもリストには残り、グレーアウト＋警告アイコンで区別する
- 拡張子と実体のコーデックが食い違う場合は**変換対象として通したうえで**、形式欄に `⚠` を付け、
  ツールチップ・ステータスバー・ログに理由を出す
- 「変換後のサイズ」列は変換前が予測値（`約 4.1 MB`）、変換後は実サイズに切り替わる
- 変換中はファイルの追加・削除・クリアと変換設定を無効化する（設定は開始時点の値で確定）

### 編集（トリミング・音量）

`Ctrl+E` で開閉する。一覧で **1 行だけ**選んでいるときに有効になる
（未選択・複数選択のときは無効）。編集内容はファイルごとに持つ。

波形の上では、**掴む場所によってドラッグの意味が変わる**。同じ場所に
2 つの意味があると「切り出しを直したいだけなのに再生位置が飛ぶ」といった
取り違えが起きるため、帯で分けている。

| 掴む場所 | 動作 |
| --- | --- |
| 波形の上 | 再生カーソルを動かす（スクラブ。掴んでいる間も音が追従する） |
| 下の目盛り帯 | 切り出す範囲を新しく引く |
| 橙の縦線（ハンドル） | 範囲の端を微調整。どちらの帯からでも掴める |

- 数値欄（`mm:ss.ms`）と波形は双方向に同期する
- 音量は -30dB 〜 +30dB。波形の振幅にも反映される
- 「全体を使う」「0 dB に戻す」で解除
- **▶ 再生**で元のファイルをそのまま再生できる。離した位置から鳴る
- **🔁 区間をリピート**を入れると、切り出す範囲の終わりまで来たら開始位置へ
  戻って繰り返す。範囲を調整しながら聴き比べるときに使う。範囲の外で
  再生ボタンを押した場合は範囲の先頭から始まる
- 編集したファイルは一覧の「編集」列に `✂ -6.0dB` のような印が付く
- トリミングすると一覧の長さと予測サイズが追従する

編集内容は `config.json` に保存しない（ファイル固有の値のため）。
パネルの開閉状態だけは保存する。

#### プレビュー再生

元のファイル（変換前）をそのまま鳴らす。OGG 変換後の音を確かめるものではない。

- 再生位置は波形上の白い縦線で示す（更新はおよそ 50ms 間隔）
- 波形を**クリック**するとその位置から再生（ドラッグは範囲選択なので競合しない）
- 音量スライダーの値が再生にも即座に効く
- 選択行を変える・変換を始めると再生は止まる

`QMediaPlayer` + `QAudioOutput` を使っている。PySide6 の Qt Multimedia は
**FFmpeg をバックエンドに持つ**（同梱の avcodec 等を使う）ので、Voggify が
受け付ける形式はすべて再生できる。ユーザーがインストールした ffmpeg とは
別物なので、ffmpeg 未インストールでもプレビューだけは動く。

| 形式 | 再生 |
| --- | --- |
| MP3 / WAV / FLAC / M4A (AAC) | OK |
| OGG (Opus / Vorbis) / OGA (FLAC) | OK |
| MP4 / MKV | OK（音声のみ。映像は出力先を繋いでいないので鳴るだけ） |

音量は `QAudioOutput.setVolume()` に `10^(dB/20)` を渡している。ffmpeg の
`volume=XdB` フィルタも振幅に同じ係数を掛けるので、**減衰側（0dB 以下）は
変換結果と一致する**。Qt 自身の `QAudio.convertVolume` とも小数 5 桁まで
一致することを確認済み。

ただし `setVolume` は 1.0 で頭打ちになるため、**正の dB はプレビューで
増幅できない**（0dB で鳴る）。変換結果には正しく反映されるので、
その場合はパネルに注記を出している。

#### 波形の作り方

ffmpeg で PCM を取り出し、表示に要る解像度（3000 点）まで畳んだ
ピーク列にしている。1 時間の MP3 で 3 方式を比べた結果を踏まえた選択:

| 方式 | 所要時間 | パイプ量 | 備考 |
| --- | --- | --- | --- |
| 44.1kHz のままデコード | 8.2 秒 | 303 MB | 遅い |
| **ダウンサンプルしてピーク** | **2.6 秒** | 55 MB | 採用 |
| ffmpeg の `showwavespic` | 2.2 秒 | 4 KB | 画像しか返らない |

`showwavespic` は速いが画像しか得られないため、ドラッグ選択やウィンドウ幅の
変更に追従できない。ピークの数値を持っておけば一度の生成で任意の幅に
描き直せるので、ダウンサンプル方式にした。

長いファイルほどサンプルレートを落として、デコード量が増えすぎないように
している（1 時間なら 8.3kHz）。パイプは逐次読みしてバケットに畳むので、
ファイルの長さによらずメモリは 4MB 程度で収まる。

| ファイル長 | レート | 生成時間 | ピークメモリ |
| --- | --- | --- | --- |
| 5 秒 | 44.1kHz | 0.06 秒 | 2.0 MB |
| 5 分 | 44.1kHz | 0.61 秒 | 4.2 MB |
| 1 時間 | 8.3kHz | 3.27 秒 | 4.2 MB |
| 3 時間 | 4kHz | 7.14 秒 | 4.3 MB |

生成はバックグラウンドで行い、待っている間は「波形を読み込み中…」を出す。
できた波形は 1 件 12KB でメモリに持っておく（最大 64 件 / 16MB の LRU）。
一覧から削除するとそのキャッシュも捨てる。

### ログパネル

`Ctrl+L` で開閉する。ふだんは畳んで一覧を広く使う想定。

- ffmpeg の実行コマンドと標準エラー出力をリアルタイムに表示
- ファイルごとに `───── sample.mp3 (1/3) ─────` の見出しで区切る
- 種別ごとに色分け（コマンド / ffmpeg 出力 / 警告 / エラー / 成功）
- 「クリア」「ファイルに保存…」（UTF-8 のテキストファイル）
- 閉じている間にエラーが起きるとボタンが「ログ ●」に変わる

### 設定の保存

品質・出力先・ログパネルの開閉・ウィンドウの位置とサイズは、アプリを閉じたときに
まとめて保存され、次回の起動で復元される。

| OS | 保存先 |
| --- | --- |
| Windows | `%APPDATA%\Voggify\config.json` |
| macOS | `~/Library/Application Support/Voggify/config.json` |
| Linux | `$XDG_CONFIG_HOME/Voggify/config.json`（既定は `~/.config`） |

中身は人が読める JSON で、直接編集してもよい。

```json
{
  "config_version": 1,
  "quality": 9,
  "use_custom_output_dir": true,
  "output_dir": "D:\\音楽\\変換済み",
  "log_visible": true,
  "window": { "x": 120, "y": 80, "width": 1000, "height": 680, "maximized": false }
}
```

- 設定ファイルが無ければ既定値で起動する（初回起動）
- 内容が壊れている項目は既定値に戻し、理由をログとステータスバーに出す。起動は止めない
- 前回の出力先が消えている／書き込めない場合は「入力ファイルと同じフォルダ」に戻す。
  パスは覚えたままなので、復活したらラジオを切り替えるだけで元に戻せる
- `config_version` は形式が変わったときのマイグレーション用。現在は 1

保存先は環境変数 `VOGGIFY_CONFIG_DIR` で変更できる。USB メモリに入れて持ち歩くような
ポータブル運用のときに使う。

```sh
set VOGGIFY_CONFIG_DIR=D:\Voggify\settings
```

### CLI

GUI を使わず、動作確認やスクリプトから呼ぶこともできる。

```sh
python main.py check                    # ffmpeg の検出状況を表示
python main.py info  <file>             # 解析結果と変換後サイズ予測
python main.py convert <file> [options] # OGG Vorbis / MP3 に変換

  -f, --format ogg|mp3 出力形式 (既定: ogg)
  -q, --quality 0-10   品質 / 大きいほど高音質 (既定 6)
  -o, --output-dir DIR 出力先 (既定: 入力と同じフォルダ)
      --track N        使う音声トラック (0 始まり / 既定 0)
      --overwrite      同名ファイルを上書き (既定: 「名前 (1).ogg」に退避)
      --verbose        ffmpeg のログを表示
```

複数音声を持つファイルは `info` がトラック一覧を出す。

```
$ python main.py info movie.mkv
...
音声トラック  : 3 本
  * --track 0  日本語 / Main（AAC 1ch 44.1kHz）
    --track 1  英語（AAC 1ch 44.1kHz）
    --track 2  トラック3（MP3 2ch 44.1kHz）
  * 既定（--track を省略したときに使うトラック）
```

## アイコン

`assets/icon.png`（1254x1254）が原本で、そこから Windows 用の `assets/icon.ico` と
macOS 用の `assets/icon.icns` を作って、exe・.app・インストーラー・
ショートカット・ウィンドウに使っている。

```sh
python assets/generate_icon.py
```

生成した `.ico` / `.icns` はコミットしてあるので、通常このコマンドは要らない。
原本を差し替えたときだけ実行する。

- `.ico` の収録サイズは 16 / 24 / 32 / 48 / 64 / 128 / 256（すべて 32bit）
- `.icns` は 16x16 / 32x32 / 128x128 / 256x256 / 512x512 の各 @1x・@2x を収録し、
  実ピクセルで 16 から 1024 までを覆う
- 元画像が 256px 未満だと拡大が要るため、スクリプトが警告して止まる
  （拡大で埋めると輪郭が荒れるので、原本を用意し直す方針）。
  `.icns` には 1024px が入るので、原本は 1024px 以上が望ましい
- Pillow はこのスクリプトを走らせるときだけ必要で、アプリの実行時には使わない

`.icns` は macOS 純正の `iconutil` でまとめている。`.iconset` フォルダに規定の
名前で PNG を並べて `iconutil -c icns` に渡す、という Apple の手順そのまま。
`iconutil` が無い環境（macOS 以外）では Pillow の ICNS 保存へ落とすが、
収録サイズは一致しないことがある。

反映先。

| 場所 | 設定 |
| --- | --- |
| exe のアイコン（Windows） | `voggify.spec` の `ICON_PATH` |
| .app のアイコン（macOS） | `voggify.spec` の `ICON_PATH` → `CFBundleIconFile` |
| インストーラー（Setup.exe） | `voggify.iss` の `SetupIconFile` |
| ショートカット / アンインストール一覧 | `voggify.iss` の `IconFilename` / `UninstallDisplayIcon` |
| ウィンドウとタスクバー / Dock | `app.py` / `main_window.py` の `setWindowIcon` |

実行時にもアイコンを読むため、その OS 用のものを成果物に同梱している
（`voggify/resources.py` が `sys._MEIPASS` から探し、macOS では `.icns`、
それ以外は `.ico` を選ぶ）。

### macOS のアイコンの形

macOS は .app のアイコンに自動でマスクをかけない（iOS はかけるが、ここが違う）。
角丸と余白は画像自身に持たせる必要があり、実際システムアプリの `.icns` を開くと
四隅のアルファは 0 になっている。透明な正方形のままだと、Dock では角の立った
四角いタイルとして表示されてしまう。

そのため `.icns` の生成時にだけ、原本の角丸矩形を切り出して Apple の版面に
合わせ直し、squircle でくり抜いている。寸法は macOS 15 の Music.app /
Podcasts.app の `AppIcon.icns` を実測した値。

| | Apple 純正（実測） | Voggify |
| --- | --- | --- |
| 実体の一辺 | 1024 中 814（79.5%） | 812（79.3%） |
| 余白 | 四辺 105px | 四辺 106px |
| 角の形 | 指数 5 の superellipse | 指数 5 の superellipse |

原本の `assets/icon.png` は正方形・アルファ無しのまま**変更していない**。
Windows の `.ico` は角丸にしないので（タスクバーはマスクをかけない）、
加工を `.icns` 側だけに閉じている。

影は付けていない。Apple 純正アイコンは実体の下に薄い影を持つので、Dock で
並べると少しフラットに見える。付けるなら `_macos_shaped()` に足すことになる。

## テスト

```sh
pip install -r requirements-dev.txt
pytest tests/          # または pytest（pytest.ini の testpaths で tests を見る）
```

GUI テストは `QT_QPA_PLATFORM=offscreen` で動くので、画面が出ることはない。
実行時間はおよそ 30 秒。

```sh
pytest tests/test_core_offline.py   # ffmpeg 不要な部分だけ
pytest -m "not ffmpeg"              # ffmpeg を起動しないテストだけ
pytest -v                           # 1 件ずつ表示
pytest -k cancel                    # 名前で絞り込み
```

| ファイル | 対象 |
| --- | --- |
| `test_core_offline.py` | フォーマット定義、サイズ予測、コマンド組み立て、出力パス、メッセージの日本語化 |
| `test_converter.py` | 実際に ffmpeg を動かす変換、進捗、ログ、キャンセル |
| `test_ui_file_list.py` | ファイル追加、D&D、削除、エラー表示 |
| `test_ui_conversion.py` | 変換の実行、スレッド処理、進捗、UI ロック |
| `test_ui_settings.py` | 品質スライダー、出力先設定 |
| `test_ui_log.py` | ログパネル、エラー通知、excepthook |
| `test_config.py` | 設定の読み書き、壊れた値のフォールバック、復元と保存 |
| `test_ui_output_format.py` | 出力形式の切り替え、品質の写像、形式ごとの判定 |
| `test_editing.py` | トリミング・音量のパラメータ、ffmpeg 引数、時間の表記 |
| `test_ui_editing.py` | 編集パネル、入力の検証、変換への反映 |
| `test_waveform.py` | 波形の生成、精度、キャッシュの LRU |
| `test_ui_waveform.py` | 波形の描画、ドラッグ選択、数値欄との同期 |
| `test_ui_preview.py` | プレビュー再生、全形式の再生確認、音量の換算 |
| `test_resources.py` | アイコンの収録サイズ、元画像の解像度、ウィンドウへの反映 |

`ffmpeg` マーカーの付いたテストは ffmpeg を起動する。未インストールなら自動でスキップする。
書き込み権限のテストは、拒否が効かない環境（管理者権限での実行など）ではスキップする。

## テストアセット

`tests/assets/` に置いてあるのは ffmpeg の `lavfi` で生成した 440Hz のサイン波で、
著作物は含まない（合計約 2MB）。作り直したいときは次を実行する。

```sh
python tests/assets/generate_assets.py
```

| ファイル | 用途 |
| --- | --- |
| `sample.{mp3,wav,flac,m4a}` | 対応 4 形式（各 5 秒） |
| `opus.ogg` / `flac.oga` | Ogg コンテナに Vorbis 以外が入ったもの（変換できる） |
| `vorbis.ogg` | Ogg コンテナに Vorbis が入ったもの（弾かれる） |
| `long.mp3` | 進捗とキャンセルの確認用（300 秒 / 32kbps）。変換に 2 秒ほどかかる長さが要る |
| `notsupported.opus` | 対応外の拡張子 |
| `multi_audio.mkv` | 音声 3 本（`jpn`+`title` / `eng` / タグ無し）。440 / 880 / 1320Hz |
| `multi_audio.mp4` | 音声 2 本（`jpn` / `eng`）。MKV 以外でも同じに扱えることの確認 |
| `silent_video.mp4` | 映像のみ。音声が無いファイルがエラーになることの確認 |
| `video.mp4` | 音声 1 本の動画（音声抽出の確認） |
| `fake.mp3` | `video.mp4` のコピー。拡張子は `.mp3` だが中身は AAC |
| `broken.mp3` | 音声ではないデータ。解析エラーの確認 |

## アプリのビルド

`voggify.spec` は Windows と macOS で共用する。同じコマンドで、走らせた OS 用の
成果物ができる。

```sh
pip install -r requirements-dev.txt
pyinstaller voggify.spec           # Windows: dist/Voggify.exe
                                   # macOS  : dist/Voggify.app
pyinstaller --clean voggify.spec   # キャッシュを捨ててビルドし直す
```

クロスビルドはできない。Windows の exe は Windows で、macOS の .app は macOS で
それぞれビルドする必要がある。

| | Windows | macOS |
| --- | --- | --- |
| 成果物 | `dist/Voggify.exe` | `dist/Voggify.app` |
| 形式 | 1 ファイル（`--onefile` 相当） | onedir を `.app` で包む |
| サイズ | 約 54MB | 約 113MB |
| 起動 | およそ 1 秒 | およそ 0.5 秒 |
| アイコン | `assets/icon.ico` | `assets/icon.icns` |

macOS を onedir にしているのは、`.app` が中に `Frameworks/` を並べる前提の
入れ物だから。onefile にすると起動のたびに一時フォルダへ展開することになり、
体感で遅くなる。サイズが Windows の倍あるのは、`.app` が展開済みの
Qt フレームワークをそのまま抱えているため（onefile は圧縮されている）。

どちらの OS でも共通:

- コンソールウィンドウ／ターミナルは出ない（`console=False`）
- **ffmpeg は同梱しない。** ユーザー環境のものを実行時に探す

ビルド設定は `voggify.spec` に置いてある。主に触るのは次の 2 つ。

| 変数 | 用途 |
| --- | --- |
| `ICON_PATH` | アイコンのパス。OS で自動的に `.ico` / `.icns` を選ぶ |
| `EXCLUDES` | 取り込まない Qt モジュール。減らすと成果物が小さくなる |

`EXCLUDES` に足すときは、外したあと必ず起動確認すること
（`shiboken6` は PySide6 の中核なので絶対に外さない。`PySide6.QtMultimedia` は
プレビュー再生に使うので外さない）。

### macOS の .app について

`BUNDLE` の設定は `voggify.spec` にまとめてある。

| 項目 | 値 |
| --- | --- |
| `bundle_identifier` | `com.tkms981.voggify` |
| `CFBundleShortVersionString` | `voggify/__init__.py` の `__version__` |
| `CFBundleIconFile` | `assets/icon.icns` |
| `LSMinimumSystemVersion` | 11.0 |

バージョンは Windows のバージョンリソースと同じ `voggify/__init__.py` の
`__version__` が出どころで、`__init__.py` の 1 行を変えれば両方が追従する。

`NSRequiresAquaSystemAppearance` を `False` にしてあるので、ダークモードに追従する。

**署名していない。** 配布する場合は Gatekeeper に止められるため、受け取った側は
初回だけ右クリック →「開く」で実行するか、次を実行する必要がある。

```sh
xattr -dr com.apple.quarantine /Applications/Voggify.app
```

**Finder の D&D は受けない。** ウィンドウへのドロップには対応しているが、
`CFBundleDocumentTypes` は宣言していないので、Finder の「このアプリケーションで
開く」や Dock アイコンへのドロップでは何も起きない。対応するには
`QFileOpenEvent` の処理を足す必要がある。

### macOS 版での CLI

`.app` の中の実行ファイルを直接叩けば、Windows と同じサブコマンドが使える。

```sh
./dist/Voggify.app/Contents/MacOS/Voggify check
```

`open dist/Voggify.app` では引数を渡せない（`open --args` は GUI 起動用）ので、
CLI として使うときは上のパスを直接指定する。

### exe 版での CLI

`Voggify.exe check` のようにサブコマンドも使える。ただしコンソールを持たない
GUI アプリなので、コマンドプロンプトから呼んだ場合は `voggify/console.py` が
親のコンソールに接続し直してから出力する。

シェルは GUI アプリの終了を待たないため、終了コードが要る場合は明示的に待つ。

```powershell
Start-Process .\dist\Voggify.exe -ArgumentList check -Wait -PassThru -NoNewWindow
```

## インストーラーのビルド

[Inno Setup 6](https://jrsoftware.org/isinfo.php) が要る。

```sh
winget install JRSoftware.InnoSetup
```

exe を作ってからインストーラーをコンパイルする。

```sh
pyinstaller voggify.spec
ISCC voggify.iss        # dist\installer\Voggify-Setup-0.1.0.exe ができる
```

`ISCC.exe` に PATH が通っていない場合は絶対パスで呼ぶ。

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" voggify.iss
```

### インストーラーの仕様

| 項目 | 内容 |
| --- | --- |
| インストール先 | `%LOCALAPPDATA%\Programs\Voggify`（**管理者権限は不要**） |
| スタートメニュー | 常に作成 |
| デスクトップ | インストール時のチェックボックスで選択（既定はオフ） |
| アンインストーラー | 同梱（`unins000.exe`、コントロールパネルからも実行可） |
| 言語 | 日本語 / 英語 |

`AppId` を固定してあるので、同じ AppId のまま新しいバージョンを実行すれば
**上書き更新**になる（二重インストールにならない）。`AppId` は絶対に変えないこと。

設定ファイルは `%APPDATA%\Voggify` にあり、インストール先の外なので
**アンインストールしても消えない**。アップデートや入れ直しで設定はそのまま引き継がれる。
完全に消したい場合はアンインストール時のダイアログで「はい」を選ぶ。

ffmpeg が見つからない環境では、インストール完了画面に
`winget install Gyan.FFmpeg` の案内が出る（アプリ内の警告バーにも同じ案内がある）。

### サイレント実行

```powershell
# インストール（デスクトップアイコンも作る）
.\Voggify-Setup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS=desktopicon

# アンインストール（設定は残る）
& "$env:LOCALAPPDATA\Programs\Voggify\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES

# アンインストール（設定も消す）
& "$env:LOCALAPPDATA\Programs\Voggify\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /DELETECONFIG
```

アンインストーラーは `/DELETECONFIG` と `/KEEPCONFIG` を受け付ける。
どちらも指定しない場合はダイアログで尋ね、既定は「いいえ」（＝残す）。

## バージョンを上げるには

**機能を追加したら、コミット・ビルドの前にバージョンを上げる。**
上げ忘れると、中身の違うインストーラーが同じ名前で生成されて
既存の配布物を黙って上書きしてしまう。

| 変更の種類 | 上げ方 | 例 |
| --- | --- | --- |
| 機能追加 | マイナー | 0.4.0 → 0.5.0 |
| 修正のみ | パッチ | 0.5.0 → 0.5.1 |

`voggify/__init__.py` の `__version__` を書き換えるだけでよい。

```python
__version__ = "0.5.0"
```

この値が次の順で伝わる。

```
voggify/__init__.py
  ├→ アプリのタイトルバー（"Voggify 0.5.0"）
  ├→ voggify.spec
  │     ├→ Windows: exe のバージョンリソース
  │     └→ macOS  : Info.plist の CFBundleShortVersionString / CFBundleVersion
  └→ voggify.iss が exe から読み取る
        ├→ インストーラーの表示名とアンインストール情報
        └→ 出力ファイル名（Voggify-Setup-0.5.0.exe）
```

`.spec` と `.iss` にバージョンを直接書いている箇所は無いので、
書き換え漏れが起きない。

## 構成

```
main.py                    エントリポイント（引数なしで GUI、サブコマンドで CLI）
voggify/
  __init__.py
  config.py                設定の永続化（JSON の読み書きと検証）
  output_formats.py        出力形式の定義（拡張子・エンコーダー・品質の対応表）
  errors.py                例外定義と OSError の日本語化
  formats.py               対応フォーマット定義、サイズ予測、表示整形
  ffmpeg_locator.py        ffmpeg / ffprobe の探索・検証
  ffmpeg_errors.py         ffmpeg のエラー出力を日本語の説明に翻訳
  probe.py                 ffprobe による解析と対応可否の判定
  converter.py             変換コア（進捗・ログ・キャンセル）
  editing.py               トリミング・音量のパラメータ（Qt 非依存）
  waveform.py              波形の生成とキャッシュ（Qt 非依存）
  models.py                リスト項目のデータ構造（Qt 非依存）
  resources.py             同梱リソースのパス解決（frozen 対応）
  cli.py                   CLI
  console.py               windowed ビルドでの標準出力の確保
  app.py                   GUI の起動処理と excepthook
  ui/
    main_window.py         メインウィンドウ
    file_list_model.py     ファイル一覧のモデル（追加・削除・集計・進捗）
    file_list_view.py      ファイル一覧のビュー（D&D・キー操作・右クリック）
    settings_panel.py      品質・出力先の設定 UI
    edit_panel.py          トリミング・音量の編集 UI
    waveform_view.py       波形の描画とドラッグ選択
    waveform_service.py    波形生成のバックグラウンド実行
    preview_player.py      プレビュー再生（QMediaPlayer のラッパー）
    log_panel.py           変換ログの表示・クリア・保存
    probe_service.py       解析のバックグラウンド実行
    conversion_service.py  変換キューのワーカースレッド実行
    progress_delegate.py   進捗列の描画
tests/
  conftest.py              共通フィクスチャ（offscreen 設定、作業フォルダ、MainWindow）
  qt_helpers.py            イベントループ操作、D&D の合成、権限操作
  assets/                  テスト用の音声（generate_assets.py で再生成できる）
  test_*.py                テスト本体
assets/
  icon.png                 アイコンの原本
  icon.ico                 生成物（コミット対象）
  generate_icon.py         .ico の生成
pytest.ini                 pytest の設定
voggify.spec               PyInstaller のビルド設定
voggify.iss                Inno Setup のインストーラー定義
```

## 設計メモ

**スレッド構成**
UI スレッドは描画だけを担当する。解析（ffprobe）は `ProbeService` が
QThreadPool へ最大 4 並列で流し、変換は `ConversionService` が QThread に載せた
`ConversionWorker` で 1 件ずつ順に処理する。進捗とログはシグナル経由で
UI スレッドへ渡す。`Converter.cancel()` だけは UI スレッドから呼んでよい。

**出力形式の足し方**
`output_formats.py` の `OUTPUT_FORMATS` に `OutputFormat` を 1 つ足せば、
変換コア・設定 UI・予測サイズ・設定の保存がすべて追従する。形式ごとの差
（拡張子・エンコーダー名・コンテナ・品質の尺度）はこのオブジェクトに閉じている。
UI 側に「MP3 なら〜」のような分岐は書かない。

**対応判定**
拡張子（一次フィルタ）と ffprobe の `codec_name`（最終判定）の両方を見る。
M4A / AAC は ALAC も含みうるため両方許可している。実体が対応コーデックなら
拡張子と食い違っていても変換し、注記だけ出す。

Ogg は「コンテナは受け付けるが中身で判断する」形の典型で、拡張子だけでは
可否を決められない。`.ogg` は拡張子フィルタを通し、`codec_name` が Vorbis
だったときに `OUTPUT_CODECS` の判定で弾いている。

**出力の安全性**
`<name>.ogg.part` に書き出し、成功時のみ `os.replace()` で本来の名前にする。
失敗・中断時は `.part` を削除するので、壊れたファイルは残らない。
`.part` は拡張子からコンテナを推測できないため `-f ogg` で明示している。
既存ファイルは上書きせず `名前 (1).ogg` に退避する。

**エラーの扱い**
`errors.py` の例外はすべてユーザー向けの日本語メッセージを持つ。
ffmpeg の英語エラーは `ffmpeg_errors.py` が「ディスク容量が足りません」などに
翻訳し、原文も併記する。OSError は errno から説明を組み立てる。
ワーカーは想定外の例外も捕まえて該当ファイルを失敗扱いにし、キューを止めない。
UI スレッドで拾い損ねた例外は `app.py` の excepthook がログパネルへ回す。

**ffprobe の二重実行を避ける**
解析済みの `AudioInfo` を `ConversionJob` に載せて変換ワーカーへ渡すので、
変換時に ffprobe を再実行しない。

**exe 化しても ffmpeg の探索はそのまま動く**
`ffmpeg_locator.py` は PATH（`shutil.which`）・環境変数・既知のインストール先しか
見ておらず、`__file__` や `sys._MEIPASS` に依存しない。そのため PyInstaller で
固めても挙動は変わらず、`sys.frozen` による分岐は要らない。
実際に frozen ビルドで、PATH に無い winget 配置の ffmpeg を検出できることを確認済み。

一方 `--noconsole` では `sys.stdout` / `sys.stderr` が None になり、CLI の
`print()` が落ちる。これは `voggify/console.py` で受けている
（親のコンソールに接続、無理なら出力を捨てる）。

**トリミングの `-ss` を入力側に置いた理由**
1 秒ごとに周波数が変わる音源を切り出して中身を確かめたところ、入力側
（`-i` の前）でも出力側でも位置・長さとも誤差 0ms だった。差が出たのは
速度で、30 分の音源の 1700 秒地点から 60 秒を切り出す場合、入力側 0.64 秒に
対して出力側は 1.14 秒。出力側は先頭からデコードして捨てるため。
長さは `-to`（絶対位置）ではなく `-t`（長さ）で渡している。

**進捗はトリミング後の長さが基準**
ffmpeg の `-progress` が返す `out_time` は、トリミングしても 0 始まりの
相対時間だった（実測）。そのため進捗の分母には切り出し後の長さを使う。
元の長さを使うと進捗が途中で止まって見える。

**波形の描画コスト**
波形は QPixmap に一度描いてから貼る。選択範囲やハンドルはその上に重ねる
だけなので、ドラッグ中に波形を描き直さない。ピクセルマップを作り直すのは
ウィジェットの大きさ・波形データ・音量が変わったときだけ。

**設定の保存に QSettings を使わなかった理由**
QSettings は Windows では既定でレジストリに書く。IniFormat にすればファイルには
なるが、いずれにせよ JSON にはならない。「%APPDATA% に JSON で置いて人が読める
状態にする」という要件をそのまま満たせるのは自前の実装だった。加えて、形式が
変わったときのマイグレーションを自分で書けること（`config_version`）と、
Qt に依存しないので GUI 抜きでテストできることも決め手になった。

**設定パネルの置き場所**
一覧の上に横一列で置いている。右サイドパネルにするとファイル名列を
圧迫するため、縦を少し使う方を選んだ。

## 既知の制限

- **変換後サイズはあくまで概算**。`-q:a` の公称ビットレート表からの計算なので、
  VBR の実際の出力とはズレる。特に無音や純音が多い素材では大幅に下回る
  （実測例: 予測 8.2 MB → 実際 1.5 MB）。実際の音楽ではおおむね ±20〜30% 程度
- **変換は 1 ファイルずつの逐次処理**。並列変換は未実装
  （`Converter` を複数持てば並列化できる構造にはしてある）
- **編集はトリミングと音量のみ。** フェードやイコライザーは未対応
- **プレビューで音量を増幅できない**（`QAudioOutput` が 1.0 で頭打ちのため）。
  減衰は正確。変換結果には増幅も反映される
- **プレビューは変換後の音ではなく元ファイルの音**
- **編集内容はアプリを閉じるとリセットされる**（ファイル固有の値のため保存しない）
- **MP3 の出力は VBR のみ。** CBR（ビットレート固定）は未対応
- **`.opus` 拡張子は対象外。** Ogg コンテナに入った Opus（`.ogg`）は変換できるが、
  `.opus` という拡張子のファイルは受け付けない
- **1 回の変換で取り出す音声トラックは 1 本**（`-map 0:a:N`）。
  複数トラックを 1 度にまとめて書き出すことはできない（同じファイルを
  もう一度追加して別のトラックを選ぶ）
- **動画は音声だけを取り出す。** 映像・字幕の書き出しや、映像を保ったままの
  音声差し替えは対象外
- **対応する動画コンテナは `.mp4` と `.mkv` のみ**（`.avi` / `.mov` / `.webm`
  などは未対応）
- **トラックの表示名は ffprobe が読めたタグ次第。** タグが無いファイルは
  「トラック1」のような番号表記になる
- **カバーアートは引き継がれない**。タグ（タイトル・アーティスト等）は引き継ぐ
- **キャンセルはファイル単位**。実行中のファイルは中断され、出力は残らない
- **ログの保持は 5000 行、1 ファイルあたり 500 行まで**。超えた分は古いものから捨てる
- ドラッグ&ドロップでフォルダを渡した場合、対応拡張子のファイルを最大 2000 件まで拾う
- 設定はアプリを閉じたときに保存する。強制終了（タスクマネージャなど）では保存されない
- インストーラーにコード署名をしていないため、初回実行時に SmartScreen の警告が出る
- 保存するのは品質・出力先・ログパネルの開閉・ウィンドウの位置とサイズのみ。
  ファイルリストの中身は保存しない

## ライセンス

MIT License. [LICENSE](LICENSE) を参照。

ffmpeg 自体は本アプリに同梱しておらず、別途インストールしたものを呼び出す。
ffmpeg のライセンスはそのビルドの配布条件に従う。
