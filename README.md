# Voggify

音楽ファイルを **OGG Vorbis** に変換するデスクトップアプリ。
ドラッグ&ドロップでファイルを積み、品質と出力先を決めて一括変換する。

- 対応入力: MP3 / WAV / FLAC / AAC / M4A / OGG・OGA（Vorbis 以外の中身）
- 出力: OGG Vorbis (`.ogg`)
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
# macOS
brew install ffmpeg
# Debian / Ubuntu
sudo apt install ffmpeg
```

インストールできたか確認する。

```sh
python main.py check
```

### ffmpeg が見つからないとき

PATH に無くても、以下の場所は自動で探索する。

- `C:\ffmpeg\bin`, `C:\Program Files\ffmpeg\bin`, chocolatey / scoop のパス
- winget のポータブル配置（`%LOCALAPPDATA%\Microsoft\WinGet\Packages\*ffmpeg*\*\bin`）
- `/usr/bin`, `/usr/local/bin`, `/opt/homebrew/bin`, `/snap/bin`

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
| 品質変更 | 「変換設定」の品質スライダー（0〜10、既定 6） |
| 出力先変更 | 「変換設定」→「フォルダを指定」→「参照…」 |
| 変換実行 | 「変換開始」（待機中の項目を上から順に処理） |
| 中断 | 変換中は同じボタンが「キャンセル」に変わる |
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
| `.ogg` `.oga` | **Opus / FLAC / Speex**（Vorbis は下記のとおり対象外） |

`.ogg` と `.oga` は Ogg コンテナなので、中身のコーデックによって扱いが変わる。
Opus や FLAC が入っているものは変換できるが、**中身が既に Vorbis のものは
「既に OGG Vorbis です」として弾く**（出力と同じ形式であり、再エンコードすると
音質が落ちるだけのため）。

`.ogg` を `.ogg` に変換するので、出力先が入力と同じフォルダの場合は
`podcast.ogg` → `podcast (1).ogg` のように別名になる。
元の名前のままにしたいときは出力先フォルダを指定する。
入力そのものを壊すことはなく、`--overwrite` を付けても
入力と出力が同じファイルになる場合はエラーで止まる。

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
python main.py convert <file> [options] # OGG Vorbis に変換

  -q, --quality 0-10   品質 (-q:a、既定 6)
  -o, --output-dir DIR 出力先 (既定: 入力と同じフォルダ)
      --overwrite      同名ファイルを上書き (既定: 「名前 (1).ogg」に退避)
      --verbose        ffmpeg のログを表示
```

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

`ffmpeg` マーカーの付いたテストは ffmpeg を起動する。未インストールなら自動でスキップする。
書き込み権限のテストは、拒否が効かない環境（管理者権限での実行など）ではスキップする。

### テストアセット

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
| `video.mp4` | 対応外（フォルダドロップで拾わないことの確認） |
| `fake.mp3` | `video.mp4` のコピー。拡張子は `.mp3` だが中身は AAC |
| `broken.mp3` | 音声ではないデータ。解析エラーの確認 |

## exe のビルド

```sh
pip install -r requirements-dev.txt
pyinstaller voggify.spec           # dist/Voggify.exe ができる
pyinstaller --clean voggify.spec   # キャッシュを捨ててビルドし直す
```

- 1 ファイル形式（`--onefile` 相当）。約 44MB、起動はおよそ 1 秒
- コンソールウィンドウは出ない（`console=False`）
- **ffmpeg は同梱しない。** ユーザー環境のものを実行時に探す

ビルド設定は `voggify.spec` に置いてある。主に触るのは次の 2 つ。

| 変数 | 用途 |
| --- | --- |
| `ICON_PATH` | `.ico` のパス。`None` なら PyInstaller の既定アイコン |
| `EXCLUDES` | 取り込まない Qt モジュール。減らすと exe が小さくなる |

アイコンを用意したら `ICON_PATH = "assets/voggify.ico"` のように書き換えるだけでよい。
`EXCLUDES` に足すときは、外したあと必ず起動確認すること
（`shiboken6` は PySide6 の中核なので絶対に外さない）。

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

`voggify/__init__.py` の `__version__` を書き換えるだけでよい。

```python
__version__ = "0.2.0"
```

この値が次の順で伝わる。

```
voggify/__init__.py
  ├→ アプリのタイトルバー（"Voggify 0.2.0"）
  ├→ voggify.spec が exe のバージョンリソースに埋め込む
  └→ voggify.iss が exe から読み取る
        ├→ インストーラーの表示名とアンインストール情報
        └→ 出力ファイル名（Voggify-Setup-0.2.0.exe）
```

`.spec` と `.iss` にバージョンを直接書いている箇所は無いので、
書き換え漏れが起きない。

## 構成

```
main.py                    エントリポイント（引数なしで GUI、サブコマンドで CLI）
voggify/
  __init__.py
  config.py                設定の永続化（JSON の読み書きと検証）
  errors.py                例外定義と OSError の日本語化
  formats.py               対応フォーマット定義、サイズ予測、表示整形
  ffmpeg_locator.py        ffmpeg / ffprobe の探索・検証
  ffmpeg_errors.py         ffmpeg のエラー出力を日本語の説明に翻訳
  probe.py                 ffprobe による解析と対応可否の判定
  converter.py             変換コア（進捗・ログ・キャンセル）
  models.py                リスト項目のデータ構造（Qt 非依存）
  cli.py                   CLI
  console.py               windowed ビルドでの標準出力の確保
  app.py                   GUI の起動処理と excepthook
  ui/
    main_window.py         メインウィンドウ
    file_list_model.py     ファイル一覧のモデル（追加・削除・集計・進捗）
    file_list_view.py      ファイル一覧のビュー（D&D・キー操作・右クリック）
    settings_panel.py      品質・出力先の設定 UI
    log_panel.py           変換ログの表示・クリア・保存
    probe_service.py       解析のバックグラウンド実行
    conversion_service.py  変換キューのワーカースレッド実行
    progress_delegate.py   進捗列の描画
tests/
  conftest.py              共通フィクスチャ（offscreen 設定、作業フォルダ、MainWindow）
  qt_helpers.py            イベントループ操作、D&D の合成、権限操作
  assets/                  テスト用の音声（generate_assets.py で再生成できる）
  test_*.py                テスト本体
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
- **`.opus` 拡張子は対象外。** Ogg コンテナに入った Opus（`.ogg`）は変換できるが、
  `.opus` という拡張子のファイルは受け付けない
- **入力の音声ストリームは先頭 1 本のみ**を変換する（`-map 0:a:0`）。
  複数音声トラックを持つファイルの 2 本目以降は無視される
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
