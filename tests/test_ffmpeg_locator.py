"""ffmpeg / ffprobe の探索と、足りないときの案内文のテスト。

実際の ffmpeg は使わない。空の実行ファイルを temp に並べて shutil.which に
本物の探索をさせ、`ffmpeg -version` の結果だけ _probe_version を差し替えて
偽装する。こうすると「どの候補を、どの順で叩いたか」まで検証できる。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import voggify.ffmpeg_locator as L
from voggify.errors import FFmpegNotFoundError
from voggify.output_formats import MP3, OGG_VORBIS

#: Windows では shutil.which が PATHEXT を見るので拡張子が要る
EXE = ".exe" if sys.platform == "win32" else ""

BOTH = frozenset({"libvorbis", "libmp3lame"})
LAME_ONLY = frozenset({"libmp3lame"})


# ---------------------------------------------------------------------------
# 下ごしらえ
# ---------------------------------------------------------------------------
def _make_build(directory: Path, *, ffprobe: bool = True) -> Path:
    """ffmpeg（と ffprobe）が入ったフォルダを作る。中身は空でよい。

    .exe 付きも一緒に置く。shutil.which は sys.platform を見て挙動を変えるので、
    Windows を騙って動かすテストでも同じフォルダが使えるようにしておく。
    """
    directory.mkdir(parents=True, exist_ok=True)
    names = ["ffmpeg", "ffprobe"] if ffprobe else ["ffmpeg"]
    for name in names:
        for exe in (directory / name, directory / f"{name}.exe"):
            exe.write_text("", encoding="utf-8")
            exe.chmod(0o755)
    return directory


def _pretend_windows(monkeypatch) -> None:
    """sys.platform を win32 にする。shutil.which もその流儀に切り替わる。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATHEXT", ".EXE")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """キャッシュと環境変数を毎回まっさらにする。"""
    monkeypatch.setattr(L, "_cached_tools", None)
    monkeypatch.delenv(L.ENV_FFMPEG, raising=False)
    monkeypatch.delenv(L.ENV_FFPROBE, raising=False)


class Fake:
    """_probe_version の差し替え。フォルダ名 → エンコーダー集合で引く。

    encoders に None を入れるとその候補は「実行できない」ものとして扱う。
    probed には叩いた順にフォルダ名が積まれる。
    """

    def __init__(self, monkeypatch, table: dict[str, frozenset[str] | None]) -> None:
        self.table = table
        self.probed: list[str] = []
        self.listed_dirs = 0
        monkeypatch.setattr(L, "_probe_version", self._probe)

    def _probe(self, ffmpeg_path: str) -> tuple[str, frozenset[str]]:
        key = Path(ffmpeg_path).parent.name
        self.probed.append(key)
        encoders = self.table[key]
        if encoders is None:
            raise FFmpegNotFoundError(f"ffmpeg を実行できませんでした: {ffmpeg_path}")
        return f"ffmpeg 9.0.1 ({key})", encoders

    def watch_hints(self, monkeypatch, dirs: list[Path]) -> None:
        """_candidate_dirs を差し替えつつ、呼ばれた回数を数える。"""

        def fake_dirs() -> list[str]:
            self.listed_dirs += 1
            return [str(d) for d in dirs]

        monkeypatch.setattr(L, "_candidate_dirs", fake_dirs)


def _use_path(monkeypatch, directory: Path) -> None:
    """PATH をそのフォルダだけにする。"""
    monkeypatch.setenv("PATH", str(directory))


# ---------------------------------------------------------------------------
# 探索の優先順位
# ---------------------------------------------------------------------------
def test_complete_path_build_wins_without_touching_hints(tmp_path, monkeypatch):
    """PATH の ffmpeg で用が足りるなら、既知の場所は見に行かない。

    従来の優先順位と探索コストを変えないことの確認。
    """
    on_path = _make_build(tmp_path / "brew")
    hint = _make_build(tmp_path / "ffmpeg-full")
    _use_path(monkeypatch, on_path)
    fake = Fake(monkeypatch, {"brew": BOTH, "ffmpeg-full": BOTH})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == on_path
    assert fake.probed == ["brew"], "PATH の 1 本目で決まるはず"
    assert fake.listed_dirs == 0, "glob 展開まで遅延しているはず"


def test_prefers_ffmpeg_full_when_path_build_lacks_libvorbis(tmp_path, monkeypatch):
    """PATH の ffmpeg に libvorbis が無ければ、既知の場所の対応ビルドを採る。

    Homebrew の既定 ffmpeg（libvorbis 抜き）＋ keg-only の ffmpeg-full という
    macOS の実構成の再現。
    """
    on_path = _make_build(tmp_path / "brew")
    hint = _make_build(tmp_path / "ffmpeg-full")
    _use_path(monkeypatch, on_path)
    fake = Fake(monkeypatch, {"brew": LAME_ONLY, "ffmpeg-full": BOTH})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == hint
    assert tools.has_libvorbis
    assert tools.supports(OGG_VORBIS)
    assert fake.probed == ["brew", "ffmpeg-full"], "PATH を見てから既知の場所へ"
    # ffprobe も同じビルドから採る（版の食い違いを避ける）
    assert Path(tools.ffprobe).parent == hint


def test_keeps_first_build_when_nothing_has_libvorbis(tmp_path, monkeypatch):
    """どれにも libvorbis が無ければ、従来どおり最初の候補を使う。

    ここで初めて「libvorbis を含んでいない」の警告が出る状態になる。
    """
    on_path = _make_build(tmp_path / "brew")
    hint = _make_build(tmp_path / "other")
    _use_path(monkeypatch, on_path)
    fake = Fake(monkeypatch, {"brew": LAME_ONLY, "other": LAME_ONLY})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == on_path, "優先順位は元のまま"
    assert not tools.has_libvorbis
    assert tools.supports(MP3), "MP3 は従来どおり使える"


def test_broken_candidate_is_skipped(tmp_path, monkeypatch):
    """実行できない候補は飛ばして次を見る。"""
    on_path = _make_build(tmp_path / "broken")
    hint = _make_build(tmp_path / "ffmpeg-full")
    _use_path(monkeypatch, on_path)
    fake = Fake(monkeypatch, {"broken": None, "ffmpeg-full": BOTH})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == hint


def test_all_candidates_broken_reports_the_first_failure(tmp_path, monkeypatch):
    """全滅したときは従来どおり実行失敗を報告する（黙って None にしない）。"""
    on_path = _make_build(tmp_path / "broken")
    _use_path(monkeypatch, on_path)
    fake = Fake(monkeypatch, {"broken": None})
    fake.watch_hints(monkeypatch, [])

    with pytest.raises(FFmpegNotFoundError, match="実行できませんでした"):
        L.find_ffmpeg_tools(force_refresh=True)


def test_missing_ffprobe_gives_up(tmp_path, monkeypatch):
    """ffprobe が無ければ検出扱いにしない。"""
    on_path = _make_build(tmp_path / "brew", ffprobe=False)
    _use_path(monkeypatch, on_path)
    fake = Fake(monkeypatch, {"brew": BOTH})
    fake.watch_hints(monkeypatch, [])

    assert L.find_ffmpeg_tools(force_refresh=True) is None


# ---------------------------------------------------------------------------
# 環境変数による明示指定は最優先のまま
# ---------------------------------------------------------------------------
def test_env_override_wins_even_without_libvorbis(tmp_path, monkeypatch):
    """明示指定は「これを使え」の意思表示。対応ビルドがあっても乗り換えない。"""
    override = _make_build(tmp_path / "override")
    hint = _make_build(tmp_path / "ffmpeg-full")
    _use_path(monkeypatch, tmp_path / "empty")
    monkeypatch.setenv(L.ENV_FFMPEG, str(override))
    fake = Fake(monkeypatch, {"override": LAME_ONLY, "ffmpeg-full": BOTH})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == override
    assert not tools.has_libvorbis
    assert fake.probed == ["override"], "他の候補は見に行かない"


def test_env_override_accepts_a_file_path(tmp_path, monkeypatch):
    """フォルダではなく実行ファイルを直接指してもよい。"""
    override = _make_build(tmp_path / "override")
    _use_path(monkeypatch, tmp_path / "empty")
    monkeypatch.setenv(L.ENV_FFMPEG, str(override / f"ffmpeg{EXE}"))
    monkeypatch.setenv(L.ENV_FFPROBE, str(override / f"ffprobe{EXE}"))
    fake = Fake(monkeypatch, {"override": BOTH})
    fake.watch_hints(monkeypatch, [])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == override
    assert Path(tools.ffprobe).parent == override


def test_env_ffprobe_beats_same_folder_pairing(tmp_path, monkeypatch):
    """ffprobe を明示指定したら、ffmpeg と同じフォルダより優先する。"""
    on_path = _make_build(tmp_path / "brew")
    elsewhere = _make_build(tmp_path / "custom")
    _use_path(monkeypatch, on_path)
    monkeypatch.setenv(L.ENV_FFPROBE, str(elsewhere))
    fake = Fake(monkeypatch, {"brew": BOTH})
    fake.watch_hints(monkeypatch, [])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == on_path
    assert Path(tools.ffprobe).parent == elsewhere


# ---------------------------------------------------------------------------
# Windows の探索が変わっていないこと
#
# _find_executable が _iter_executables に置き換わったので、Windows 側の
# 探索順（環境変数 → PATH → 既知の場所）と winget の glob 展開が
# 従来どおりであることを一通り押さえる。
# ---------------------------------------------------------------------------
def _winget_layout(root: Path, *versions: str) -> dict[str, Path]:
    """winget のポータブル配置を真似たフォルダを作る。

    実物は Packages\\<パッケージ>\\<展開先>\\bin という二段構造で、
    ここが glob（*[Ff][Ff]mpeg*\\*\\bin）で拾えるかどうかが肝。
    """
    packages = root / "WinGet" / "Packages"
    made: dict[str, Path] = {}
    for version in versions:
        package = packages / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        made[version] = _make_build(package / f"ffmpeg-{version}-full_build" / "bin")
    return made


def _winget_pattern(root: Path) -> str:
    """_WINDOWS_HINTS に入っているのと同じ形の glob。"""
    return str(root / "WinGet" / "Packages" / "*[Ff][Ff]mpeg*" / "*" / "bin")


def test_windows_hints_still_declare_the_winget_locations():
    """既知の場所から winget の 2 パターンが消えていないことの歯止め。"""
    joined = "\n".join(L._WINDOWS_HINTS)

    assert "WinGet" in joined and "Packages" in joined
    assert "Links" in joined, "winget のショートカット置き場"
    # 二段構造（<パッケージ>\<展開先>\bin）と一段の両方を見ている
    assert sum("Packages" in hint for hint in L._WINDOWS_HINTS) >= 2
    assert any("scoop" in hint for hint in L._WINDOWS_HINTS)
    assert any("chocolatey" in hint for hint in L._WINDOWS_HINTS)


def test_windows_winget_glob_is_still_expanded(tmp_path, monkeypatch):
    """winget のポータブル配置（glob）を今までどおり展開する。"""
    builds = _winget_layout(tmp_path, "7.1")
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(L, "_WINDOWS_HINTS", (_winget_pattern(tmp_path),))

    dirs = L._candidate_dirs()

    assert str(builds["7.1"]) in dirs


def test_windows_winget_prefers_the_newer_package(tmp_path, monkeypatch):
    """同じ glob に複数版が当たったら新しい方を先に見る（reverse sort）。"""
    builds = _winget_layout(tmp_path, "7.1", "8.0")
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(L, "_WINDOWS_HINTS", (_winget_pattern(tmp_path),))

    dirs = L._candidate_dirs()

    assert dirs.index(str(builds["8.0"])) < dirs.index(str(builds["7.1"]))


def test_windows_candidate_dirs_keep_the_declared_order(tmp_path, monkeypatch):
    """固定パスは宣言順のまま。glob だけがその場で展開される。"""
    builds = _winget_layout(tmp_path, "7.1")
    fixed = _make_build(tmp_path / "choco")
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(L, "_WINDOWS_HINTS", (str(fixed), _winget_pattern(tmp_path)))

    dirs = L._candidate_dirs()

    assert dirs == [str(fixed), str(builds["7.1"])]


def test_windows_winget_is_used_when_path_is_empty(tmp_path, monkeypatch):
    """PATH に無くても winget の場所から拾える（従来の fallback）。"""
    builds = _winget_layout(tmp_path, "7.1")
    _use_path(monkeypatch, tmp_path / "empty")
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(L, "_WINDOWS_HINTS", (_winget_pattern(tmp_path),))
    fake = Fake(monkeypatch, {"bin": BOTH})

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == builds["7.1"]
    assert Path(tools.ffprobe).parent == builds["7.1"]


def test_windows_path_still_wins_over_winget(tmp_path, monkeypatch):
    """Windows でも PATH 優先の順序は変わらない。"""
    on_path = _make_build(tmp_path / "onpath")
    hint = _make_build(tmp_path / "winget")
    _use_path(monkeypatch, on_path)
    _pretend_windows(monkeypatch)
    fake = Fake(monkeypatch, {"onpath": BOTH, "winget": BOTH})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == on_path
    assert fake.probed == ["onpath"]
    assert fake.listed_dirs == 0, "PATH で足りるなら glob 展開もしない"


def test_windows_env_override_beats_path_and_winget(tmp_path, monkeypatch):
    """明示指定は Windows でも最優先。"""
    override = _make_build(tmp_path / "override")
    on_path = _make_build(tmp_path / "onpath")
    hint = _make_build(tmp_path / "winget")
    _use_path(monkeypatch, on_path)
    _pretend_windows(monkeypatch)
    monkeypatch.setenv(L.ENV_FFMPEG, str(override))
    fake = Fake(monkeypatch, {"override": BOTH, "onpath": BOTH, "winget": BOTH})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == override
    assert fake.probed == ["override"]


def test_windows_search_order_is_env_then_path_then_hints(tmp_path, monkeypatch):
    """探索順そのものを固定する（環境変数 → PATH → 既知の場所）。

    _find_executable から _iter_executables への差し替えで
    順序が変わっていないことの確認。
    """
    override = _make_build(tmp_path / "override")
    on_path = _make_build(tmp_path / "onpath")
    builds = _winget_layout(tmp_path, "7.1", "8.0")
    _use_path(monkeypatch, on_path)
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(L, "_WINDOWS_HINTS", (_winget_pattern(tmp_path),))

    # 環境変数が無いとき: PATH → 既知の場所（winget は新しい版が先）
    found = [str(Path(p).parent) for p in L._iter_executables("ffmpeg", L.ENV_FFMPEG)]
    assert found == [
        str(on_path),
        str(builds["8.0"]),
        str(builds["7.1"]),
    ]

    # 環境変数があるとき: そこで打ち切り、PATH も既知の場所も出さない
    monkeypatch.setenv(L.ENV_FFMPEG, str(override))
    found = [str(Path(p).parent) for p in L._iter_executables("ffmpeg", L.ENV_FFMPEG)]
    assert found == [str(override)]


def test_windows_find_executable_priority_is_unchanged(tmp_path, monkeypatch):
    """_find_executable が返す 1 本が従来と同じであることを段階的に確かめる。

    _iter_executables を経由せず、置き換え前からある入口だけを使うので、
    変更前のコードに対しても同じように通る＝順序が変わっていない証拠になる。
    """
    override = _make_build(tmp_path / "override")
    on_path = _make_build(tmp_path / "onpath")
    builds = _winget_layout(tmp_path, "7.1", "8.0")
    _pretend_windows(monkeypatch)
    monkeypatch.setattr(L, "_WINDOWS_HINTS", (_winget_pattern(tmp_path),))

    # 1) 全部ある → 環境変数
    _use_path(monkeypatch, on_path)
    monkeypatch.setenv(L.ENV_FFMPEG, str(override))
    assert Path(L._find_executable("ffmpeg", L.ENV_FFMPEG)).parent == override

    # 2) 環境変数を外す → PATH
    monkeypatch.delenv(L.ENV_FFMPEG)
    assert Path(L._find_executable("ffmpeg", L.ENV_FFMPEG)).parent == on_path

    # 3) PATH も空 → 既知の場所（winget の新しい版）
    _use_path(monkeypatch, tmp_path / "empty")
    assert Path(L._find_executable("ffmpeg", L.ENV_FFMPEG)).parent == builds["8.0"]

    # 4) 既知の場所も空 → 見つからない
    monkeypatch.setattr(L, "_WINDOWS_HINTS", ())
    assert L._find_executable("ffmpeg", L.ENV_FFMPEG) is None


def test_windows_prefers_a_build_with_libvorbis(tmp_path, monkeypatch):
    """今回の変更は Windows でも同じに効く。

    PATH の ffmpeg に libvorbis が無ければ winget 側の対応ビルドを採る。
    """
    on_path = _make_build(tmp_path / "onpath")
    hint = _make_build(tmp_path / "winget")
    _use_path(monkeypatch, on_path)
    _pretend_windows(monkeypatch)
    fake = Fake(monkeypatch, {"onpath": LAME_ONLY, "winget": BOTH})
    fake.watch_hints(monkeypatch, [hint])

    tools = L.find_ffmpeg_tools(force_refresh=True)

    assert Path(tools.ffmpeg).parent == hint
    assert tools.has_libvorbis
    assert fake.probed == ["onpath", "winget"]


# ---------------------------------------------------------------------------
# 案内文の OS 分岐
# ---------------------------------------------------------------------------
def test_encoder_message_on_macos_points_at_ffmpeg_full(monkeypatch):
    """macOS では brew の具体的な手順と、keg-only の注意を出す。"""
    monkeypatch.setattr(sys, "platform", "darwin")

    message = L.missing_encoder_message(OGG_VORBIS, "/opt/homebrew/bin/ffmpeg")

    assert "brew install ffmpeg-full" in message
    assert "keg-only" in message
    assert L.ENV_FFMPEG in message and L.ENV_FFPROBE in message
    assert "/opt/homebrew/bin/ffmpeg" in message
    assert "公式配布版" not in message, "Windows 向けの文言は出さない"


def test_encoder_message_on_windows_is_unchanged(monkeypatch):
    """Windows の文言は従来のまま。"""
    monkeypatch.setattr(sys, "platform", "win32")

    message = L.missing_encoder_message(OGG_VORBIS, r"C:\ffmpeg\bin\ffmpeg.exe")

    assert message == (
        "この ffmpeg は libvorbis を含んでいないため、OGG Vorbis へ変換できません。\n"
        "libvorbis 付きのビルド（公式配布版など）を利用してください。\n"
        r"  検出したffmpeg: C:\ffmpeg\bin\ffmpeg.exe"
    )


def test_install_message_on_macos_prefers_ffmpeg_full(monkeypatch):
    """「インストール方法」の案内も libvorbis 入りを先に出す。"""
    monkeypatch.setattr(sys, "platform", "darwin")

    message = L.missing_ffmpeg_message()

    assert "brew install ffmpeg-full" in message
    assert message.index("ffmpeg-full") < message.index("MP3 だけで良い場合")
    assert "keg-only" in message


def test_install_message_on_windows_is_unchanged(monkeypatch):
    """Windows の案内は従来のまま winget。"""
    monkeypatch.setattr(sys, "platform", "win32")

    message = L.missing_ffmpeg_message()

    assert "winget install Gyan.FFmpeg" in message
    assert "brew" not in message


def test_banner_hint_is_one_line(monkeypatch):
    """警告バーに出す案内は 1 行に収める。"""
    for platform in ("darwin", "win32", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        assert "\n" not in L.encoder_install_hint(OGG_VORBIS)
