from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from ..constants import ROOT


class MusicMixin:

    def choose_music(self):

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Background Music",
            str(ROOT),
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


    def music_volume_changed(
        self,
        value: int,
    ):

        self.music_volume = value

        self.music_volume_label.setText(
            f"{value}%"
        )


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


