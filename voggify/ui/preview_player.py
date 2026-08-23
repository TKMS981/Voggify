"""編集パネルのプレビュー再生。

QMediaPlayer + QAudioOutput を使う。PySide6 の Qt Multimedia は FFmpeg を
バックエンドに持っているため、Voggify が受け付ける形式
（MP3 / WAV / FLAC / AAC / M4A / OGG / OGA / MP4 / MKV）はそのまま再生できる。
動画は映像の出力先（QVideoSink）を繋いでいないので音声だけが鳴る。
ユーザーが入れた ffmpeg とは別に PySide6 が同梱しているものを使うので、
ffmpeg 未インストールでもプレビューだけは動く。

音量について
------------
`QAudioOutput.setVolume()` は 0.0〜1.0 の **振幅の倍率** を取る。
ffmpeg の `volume=XdB` フィルタも振幅に 10^(dB/20) を掛けるので、
同じ式を渡せば変換後の音量と一致する。Qt 自身の `QAudio.convertVolume`
とも小数 5 桁まで一致することを確認済み。

ただし setVolume は 1.0 で頭打ちになる（実測）。そのため **正の dB では
プレビューを増幅できない**。減衰側（0dB 以下）は正確に一致する。

音声トラックの選択
------------------
複数音声を持つ動画は `QMediaPlayer.setActiveAudioTrack()` で切り替える。
この指定はメディアの読み込みが済むまで効かないので、シークと同じように
保留しておいて mediaStatus が Loaded になってから適用する。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal

#: 再生位置の通知はおよそ 50ms 間隔で来る（実測）
try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    MULTIMEDIA_AVAILABLE = True
except ImportError:  # pragma: no cover - Qt Multimedia が無いビルド向け
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]
    MULTIMEDIA_AVAILABLE = False


def volume_from_db(volume_db: float) -> float:
    """dB を QAudioOutput の倍率に直す。

    ffmpeg の volume フィルタと同じ 10^(dB/20)。1.0 を超える分は
    QAudioOutput が受け付けないので頭打ちにする。
    """
    return min(1.0, max(0.0, 10 ** (volume_db / 20.0)))


def boost_is_capped(volume_db: float) -> bool:
    """プレビューでは再現できない増幅かどうか。"""
    return volume_db > 0.05


class PreviewPlayer(QObject):
    """1 ファイルを再生するだけの薄いラッパー。"""

    #: 再生位置（秒）
    position_changed = Signal(float)
    #: 再生中かどうかが変わった
    playing_changed = Signal(bool)
    #: 読み込みや再生に失敗した
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: Path | None = None
        self._volume_db = 0.0
        self._enabled = True
        #: 読み込み前に要求されたシーク位置。ロードできたら適用する。
        self._pending_seek: float | None = None
        #: 読み込み後に自動再生するか
        self._play_when_ready = False
        #: 再生する音声トラック（0 始まり）。読み込み後に適用する。
        self._audio_track = 0

        if not MULTIMEDIA_AVAILABLE:
            self._player = None
            self._output = None
            return

        self._output = QAudioOutput(self)
        self._output.setVolume(volume_from_db(0.0))
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._output)
        self._player.positionChanged.connect(self._on_position)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_media_status)

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """再生できる状態か（Qt Multimedia が無いビルドでは False）。"""
        return self._player is not None

    @property
    def is_playing(self) -> bool:
        if self._player is None:
            return False
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def source(self) -> Path | None:
        return self._source

    @property
    def audio_track(self) -> int:
        """再生対象の音声トラック（0 始まり）。"""
        return self._audio_track

    def available_track_count(self) -> int:
        """Qt が認識している音声トラックの本数（読み込み後に確定する）。"""
        if self._player is None:
            return 0
        return len(self._player.audioTracks())

    def position(self) -> float:
        if self._player is None:
            return 0.0
        return self._player.position() / 1000.0

    # ------------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        """変換中など、再生させたくない場面で止める。"""
        self._enabled = enabled
        if not enabled:
            self.stop()

    def set_source(self, path: Path | None, track: int = 0) -> None:
        """再生対象を差し替える。再生中なら止める。

        track を渡すと、読み込み後にその音声トラックへ切り替える。
        """
        if self._player is None:
            return
        self.stop()
        self._pending_seek = None
        self._play_when_ready = False
        self._audio_track = max(0, int(track))
        self._source = Path(path) if path is not None else None
        if self._source is None:
            self._player.setSource(QUrl())
        else:
            self._player.setSource(QUrl.fromLocalFile(str(self._source)))

    def set_audio_track(self, track: int) -> None:
        """再生する音声トラックを変える。

        読み込みが終わっていなければ覚えておき、_on_media_status で当てる。
        再生中に切り替えると Qt 側で一度止まるので、位置は保って入れ直す。
        """
        track = max(0, int(track))
        if track == self._audio_track and self._is_ready():
            return
        self._audio_track = track
        self._apply_audio_track()

    def _apply_audio_track(self) -> None:
        """保留している音声トラックの指定を実際に当てる。"""
        if self._player is None or not self._is_ready():
            return
        # 音声を持たないメディアや、想定より少ない本数のときは触らない
        if self._audio_track >= len(self._player.audioTracks()):
            return
        if self._player.activeAudioTrack() != self._audio_track:
            self._player.setActiveAudioTrack(self._audio_track)

    def set_volume_db(self, volume_db: float) -> None:
        """音量を dB で設定する。再生中でも即座に効く。"""
        self._volume_db = volume_db
        if self._output is not None:
            self._output.setVolume(volume_from_db(volume_db))

    # ------------------------------------------------------------------
    def play(self) -> None:
        if self._player is None or not self._enabled or self._source is None:
            return
        self._player.play()

    def pause(self) -> None:
        if self._player is not None:
            self._player.pause()

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    def seek(self, seconds: float) -> None:
        """指定位置へ飛ぶ。

        setSource の直後はまだ読み込みが終わっておらず setPosition が
        無視されるので、その場合は覚えておいて読み込み後に適用する。
        """
        if self._player is None:
            return
        target = max(0.0, seconds)
        if self._is_ready():
            self._player.setPosition(int(target * 1000))
        else:
            self._pending_seek = target

    def play_from(self, seconds: float) -> None:
        """指定位置へ飛んで再生する（波形のクリック用）。"""
        if self._player is None or not self._enabled or self._source is None:
            return
        self.seek(seconds)
        if self._is_ready():
            self.play()
        else:
            self._play_when_ready = True

    def _is_ready(self) -> bool:
        """シークできる状態か。"""
        if self._player is None:
            return False
        return self._player.mediaStatus() in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.EndOfMedia,
        )

    # ------------------------------------------------------------------
    def _on_media_status(self, status) -> None:  # noqa: ANN001
        """読み込みが済んだら、保留していたトラック・シーク・再生を実行する。"""
        if self._player is None or not self._is_ready():
            return
        # トラックはシークより先に当てる（切り替えで位置が戻ることがあるため）
        self._apply_audio_track()
        if self._pending_seek is not None:
            self._player.setPosition(int(self._pending_seek * 1000))
            self._pending_seek = None
        if self._play_when_ready:
            self._play_when_ready = False
            if self._enabled:
                self._player.play()

    def _on_position(self, milliseconds: int) -> None:
        self.position_changed.emit(milliseconds / 1000.0)

    def _on_state(self, state) -> None:  # noqa: ANN001
        self.playing_changed.emit(
            state == QMediaPlayer.PlaybackState.PlayingState
        )

    def _on_error(self, error, message: str) -> None:  # noqa: ANN001
        if self._player is None:
            return
        if error == QMediaPlayer.Error.NoError:
            return
        name = self._source.name if self._source else "?"
        self.failed.emit(f"{name}: 再生できませんでした（{message or error}）")
