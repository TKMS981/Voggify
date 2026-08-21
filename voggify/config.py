"""設定の永続化。

保存先は OS ごとのユーザー設定フォルダで、形式は人が読める JSON。

  Windows : %APPDATA%\\Voggify\\config.json
  macOS   : ~/Library/Application Support/Voggify/config.json
  Linux   : $XDG_CONFIG_HOME/Voggify/config.json（既定は ~/.config）

QSettings ではなく自前の JSON にした理由:

* QSettings は Windows では既定でレジストリに書く。IniFormat にすればファイルに
  なるが、いずれにせよ JSON にはならない。「%APPDATA% に JSON で置いて人が読める
  状態にする」という要件をそのまま満たせるのは自前の実装。
* 設定の形式が変わったときのマイグレーションを自分で書きたい。QSettings には
  スキーマのバージョン管理という考え方がない。
* Qt に依存しないので、GUI を起動せずにテストできる。

このモジュールは Qt を import しない。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .formats import DEFAULT_QUALITY, MAX_QUALITY, MIN_QUALITY

#: 設定ファイルの形式バージョン。
#: 互換性の無い変更をしたら +1 して _migrate() に変換処理を足す。
CONFIG_VERSION = 1

#: 保存先を差し替えるための環境変数（テストやポータブル運用で使う）
ENV_CONFIG_DIR = "VOGGIFY_CONFIG_DIR"

APP_DIR_NAME = "Voggify"
CONFIG_FILE_NAME = "config.json"


# ---------------------------------------------------------------------------
# 保存場所
# ---------------------------------------------------------------------------
def config_dir() -> Path:
    """設定を置くフォルダ。環境変数があればそちらを優先する。"""
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
    return root / APP_DIR_NAME


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


# ---------------------------------------------------------------------------
# 設定の中身
# ---------------------------------------------------------------------------
@dataclass
class WindowGeometry:
    """ウィンドウの位置とサイズ。"""

    x: int
    y: int
    width: int
    height: int
    maximized: bool = False


@dataclass
class AppConfig:
    """保存する設定一式。未設定の項目はここが既定値になる。"""

    quality: int = DEFAULT_QUALITY
    #: True なら output_dir を使う。False なら入力ファイルと同じフォルダ
    use_custom_output_dir: bool = False
    #: 最後に選んだ出力先。use_custom_output_dir が False でも覚えておく
    output_dir: str | None = None
    log_visible: bool = False
    window: WindowGeometry | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "config_version": CONFIG_VERSION,
            "quality": self.quality,
            "use_custom_output_dir": self.use_custom_output_dir,
            "output_dir": self.output_dir,
            "log_visible": self.log_visible,
        }
        data["window"] = asdict(self.window) if self.window else None
        return data


@dataclass
class LoadResult:
    """読み込み結果。壊れていた項目は warnings に理由が入る。"""

    config: AppConfig
    warnings: list[str] = field(default_factory=list)
    #: 設定ファイルが実在したか（初回起動の判定に使う）
    existed: bool = False
    path: Path | None = None


# ---------------------------------------------------------------------------
# 値の検証
# ---------------------------------------------------------------------------
def _read_int(data: dict, key: str, default: int, warnings: list[str]) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        warnings.append(f"{key} の値が不正です（{value!r}）。既定値 {default} を使います。")
        return default
    return value


def _read_bool(data: dict, key: str, default: bool, warnings: list[str]) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        warnings.append(f"{key} の値が不正です（{value!r}）。既定値 {default} を使います。")
        return default
    return value


def _read_optional_str(data: dict, key: str, warnings: list[str]) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        warnings.append(f"{key} の値が不正です（{value!r}）。未設定として扱います。")
        return None
    return value


def _read_geometry(data: dict, warnings: list[str]) -> WindowGeometry | None:
    raw = data.get("window")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        warnings.append("window の値が不正です。ウィンドウ位置は復元しません。")
        return None

    values: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            warnings.append("window の値が不正です。ウィンドウ位置は復元しません。")
            return None
        values[key] = value

    if values["width"] < 200 or values["height"] < 150:
        warnings.append("window のサイズが小さすぎます。既定サイズを使います。")
        return None

    maximized = raw.get("maximized", False)
    if not isinstance(maximized, bool):
        maximized = False
    return WindowGeometry(maximized=maximized, **values)


def _migrate(data: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """古い形式の設定を現在の形式に寄せる。

    今は v1 しか存在しないので変換は無い。形式を変えるときは
    CONFIG_VERSION を上げて、ここに `if version < 2:` を足していく。
    """
    version = data.get("config_version")
    if version is None:
        warnings.append("config_version がありません。既定値で補完します。")
        version = CONFIG_VERSION
    elif isinstance(version, bool) or not isinstance(version, int):
        warnings.append(f"config_version が不正です（{version!r}）。")
        version = CONFIG_VERSION

    if version > CONFIG_VERSION:
        warnings.append(
            f"設定ファイルが新しい形式です（version {version}）。"
            "読める項目だけ使います。"
        )
    return data


def config_from_dict(data: dict[str, Any]) -> tuple[AppConfig, list[str]]:
    """dict から AppConfig を作る。壊れている項目は既定値に落とす。"""
    warnings: list[str] = []
    data = _migrate(data, warnings)

    quality = _read_int(data, "quality", DEFAULT_QUALITY, warnings)
    if not MIN_QUALITY <= quality <= MAX_QUALITY:
        warnings.append(
            f"quality が範囲外です（{quality}）。既定値 {DEFAULT_QUALITY} を使います。"
        )
        quality = DEFAULT_QUALITY

    config = AppConfig(
        quality=quality,
        use_custom_output_dir=_read_bool(data, "use_custom_output_dir", False, warnings),
        output_dir=_read_optional_str(data, "output_dir", warnings),
        log_visible=_read_bool(data, "log_visible", False, warnings),
        window=_read_geometry(data, warnings),
    )

    if config.use_custom_output_dir and not config.output_dir:
        warnings.append("出力先フォルダが記録されていません。入力と同じフォルダを使います。")
        config.use_custom_output_dir = False
    return config, warnings


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------
def load_config(path: Path | None = None) -> LoadResult:
    """設定を読み込む。無い・壊れている場合は既定値にフォールバックする。

    例外は投げない。問題は LoadResult.warnings に入れて返す。
    """
    target = path or config_path()

    if not target.is_file():
        return LoadResult(config=AppConfig(), existed=False, path=target)

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        from .errors import describe_os_error

        return LoadResult(
            config=AppConfig(),
            warnings=[f"設定ファイルを読めませんでした。{describe_os_error(exc)}"],
            existed=True,
            path=target,
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return LoadResult(
            config=AppConfig(),
            warnings=[
                f"設定ファイルの内容が壊れています（{exc.lineno} 行目）。既定値で起動します。"
            ],
            existed=True,
            path=target,
        )

    if not isinstance(data, dict):
        return LoadResult(
            config=AppConfig(),
            warnings=["設定ファイルの形式が不正です。既定値で起動します。"],
            existed=True,
            path=target,
        )

    config, warnings = config_from_dict(data)
    return LoadResult(config=config, warnings=warnings, existed=True, path=target)


def save_config(config: AppConfig, path: Path | None = None) -> tuple[bool, str]:
    """設定を保存する。成功したかと、失敗した場合の理由を返す。

    書き込み途中で電源が落ちても壊れたファイルが残らないよう、
    一時ファイルへ書いてから置き換える（変換の .part と同じ考え方）。
    """
    from .errors import describe_os_error

    target = path or config_path()
    temporary = target.with_name(target.name + ".tmp")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False, f"設定を保存できませんでした。{describe_os_error(exc)}"
    return True, ""
