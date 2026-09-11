"""
MusicMixin: background music track selection, volume, and the final
post-render music mix step -- launches music_overlay.py as a subprocess
after the main render/caption pipeline finishes, then calls
finish_short_success() once mixing completes (or immediately, if no
music track is selected).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QFileDialog

from music_overlay import music_gain_at_time, normalized_music_gain

from ..constants import ROOT


def music_picker_start_directory() -> Path:
    """Prefer the project music library without retaining a prior browse path."""

    music_directory = ROOT / "assets" / "music"
    return music_directory if music_directory.is_dir() else ROOT


def editor_position_to_music_position(
    position_ms: int,
    timeline_start_ms: int = 0,
    playback_speed: float = 1.0,
) -> int:
    """Map source/editor transport time onto the final-output music clock."""

    try:
        speed = float(playback_speed)
    except (TypeError, ValueError):
        speed = 1.0
    if speed <= 0:
        speed = 1.0
    return max(0, round((int(position_ms) - int(timeline_start_ms)) / speed))


class MusicMixin:

    def music_preview_duck_events(self) -> list[dict]:
        visible_clips = getattr(self, "visible_editor_asset_clips", lambda: [])()
        events = []
        for clip in visible_clips:
            if str(clip.get("kind", "")).upper() != "SFX" or not clip.get("active", True):
                continue
            try:
                start = float(clip["start"])
                end = float(clip["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end > start:
                events.append({"start": start, "end": end})
        return events

    def current_music_preview_gain(self, position_ms: int | None = None) -> float:
        music_gain = normalized_music_gain(
            float(getattr(self, "music_volume", 0)) / 100.0
        )
        master_gain = normalized_music_gain(
            float(getattr(self, "preview_volume", 100)) / 100.0
        )
        if position_ms is None:
            player = getattr(self, "player", None)
            position_ms = player.position() if player is not None else 0
        ducked_gain = music_gain_at_time(
            music_gain,
            self.music_preview_duck_events(),
            max(0, int(position_ms)) / 1000.0,
        )
        return ducked_gain * master_gain

    def update_music_preview_volume(self, position_ms: int | None = None):
        output = getattr(self, "music_preview_audio", None)
        if output is not None:
            output.setVolume(self.current_music_preview_gain(position_ms))

    def music_preview_timeline_position(self, position_ms: int | None = None) -> int:
        player = getattr(self, "player", None)
        if position_ms is None:
            position_ms = player.position() if player is not None else 0
        speed = player.playbackRate() if player is not None else 1.0
        start_ms = int(getattr(self, "start_ms", 0) or 0)
        return editor_position_to_music_position(position_ms, start_ms, speed)

    def sync_music_preview(self, position_ms: int | None = None, force: bool = False):
        preview = getattr(self, "music_preview_player", None)
        music_path = getattr(self, "music_path", None)
        if preview is None or music_path is None:
            return

        expected = self.music_preview_timeline_position(position_ms)
        duration = int(preview.duration() or 0)
        if duration > 0:
            expected %= duration
        if force or abs(int(preview.position()) - expected) > 180:
            preview.setPosition(expected)
        self.update_music_preview_volume(position_ms)

    def update_music_preview_playback_state(self, state):
        preview = getattr(self, "music_preview_player", None)
        if preview is None or getattr(self, "music_path", None) is None:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.sync_music_preview(force=True)
            preview.play()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            preview.pause()
        else:
            preview.stop()
            self.sync_music_preview(force=True)

    def configure_music_preview(self):
        preview = getattr(self, "music_preview_player", None)
        music_path = getattr(self, "music_path", None)
        if preview is None:
            return
        preview.stop()
        preview.setSource(
            QUrl.fromLocalFile(str(music_path)) if music_path is not None else QUrl()
        )
        self.update_music_preview_volume()
        if music_path is None:
            return
        self.sync_music_preview(force=True)
        player = getattr(self, "player", None)
        if (
            player is not None
            and player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            preview.play()

    def choose_music(self):

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Background Music",
            str(music_picker_start_directory()),
            (
                "Audio Files "
                "(*.mp3 *.wav *.m4a *.aac *.flac *.ogg)"
            ),
        )

        if not filename:
            return

        self.music_path = Path(
            filename
        )

        self.music_label.setText(
            self.music_path.name
        )

        self.clear_music_button.setEnabled(
            True
        )

        self.music_button.setText(
            "♫ Change Music"
        )

        self.configure_music_preview()


    def clear_music(self):

        self.music_path = None

        self.music_label.setText(
            "No background music"
        )

        self.clear_music_button.setEnabled(
            False
        )

        self.music_button.setText(
            "♫ Add Music"
        )

        self.configure_music_preview()


    def music_volume_changed(
        self,
        value: int,
    ):

        self.music_volume = value

        self.music_volume_label.setText(
            f"{value}%"
        )

        self.update_music_preview_volume()


    def append_music_log(
        self,
        data: str,
    ):

        if not data:
            return

        self.render_log.moveCursor(
            self.render_log.textCursor().MoveOperation.End
        )

        self.render_log.insertPlainText(
            data
        )

        scrollbar = (
            self.render_log.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )


    def read_music_output(self):

        data = (
            self.music_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_music_log(
            data
        )


    def read_music_error(self):

        data = (
            self.music_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_music_log(
            data
        )


    def start_music_mix(self):

        if not self.music_path:
            self.finish_short_success()
            return

        final_video = (
            ROOT
            / "output"
            / "rendered"
            / "short1_captioned.mp4"
        )

        music_script = (
            ROOT
            / "app"
            / "music_overlay.py"
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "=== FINAL AUDIO: Mixing background music ==="
        )

        self.render_log.append(
            f"Music: {self.music_path.name}"
        )

        self.render_log.append(
            f"Music volume: {self.music_volume}%"
        )

        self.generate_button.setText(
            "Adding Music..."
        )

        self.set_render_progress_stage(
            "music"
        )

        self.music_process.start(
            sys.executable,
            [
                str(music_script),

                "--video",
                str(final_video),

                "--music",
                str(self.music_path),

                "--volume",
                str(
                    self.music_volume / 100
                ),
            ],
        )


    def music_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        if exit_code == 0:

            self.finish_short_success()

            return

        self.generate_button.setEnabled(
            True
        )

        self.generate_button.setText(
            "Generate Again"
        )

        self.find_clips_button.setEnabled(
            self.video_path is not None
        )

        self.music_button.setEnabled(
            True
        )

        self.finish_render_progress(
            False
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "⚠ SHORT RENDERED, BUT MUSIC MIX FAILED"
        )

        self.render_log.append(
            f"Music process exit code: {exit_code}"
        )


