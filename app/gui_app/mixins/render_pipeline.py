"""
RenderPipelineMixin: the "Generate Final Video" button and everything
downstream of clicking it -- builds render_settings.json via
save_render_settings(), launches render.py as a QProcess, streams its
output into both the render log widget and output/render_log.txt
(overwritten fresh each render, for handing a complete log to an AI
assistant or an issue report without truncation), and handles the
success/failure/music-mix-follow-up flow. Every subprocess this app
launches has PYTHONUNBUFFERED=1 set on its environment (see
main_window.py) so this log actually reflects progress in real time
rather than bunching up at the end.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices

from ..constants import ROOT

try:
    from render_archive import archive_final_video
except ImportError:
    from ...render_archive import archive_final_video

RENDER_LOG_FILE_PATH = ROOT / "output" / "render_log.txt"

# Every ffmpeg invocation prints this same fixed, zero-information banner
# (version, build config, library versions) before doing anything useful.
# The render pipeline runs several ffmpeg passes per render, so this adds
# up to real noise in the persisted log with nothing unique to say twice.
# Only applied when writing the final render_log.txt snapshot (never to
# the live widget text as it streams in), since that's a single complete
# string with real line boundaries -- filtering un-line-aligned stdout
# chunks as they arrive would risk mangling genuinely useful output.
FFMPEG_BANNER_PATTERN = re.compile(
    r"^ffmpeg version .*\n(?:  .*\n)*",
    re.MULTILINE,
)


def strip_ffmpeg_banner(text: str) -> str:
    return FFMPEG_BANNER_PATTERN.sub(
        "",
        text,
    )


class RenderPipelineMixin:

    def is_recap_editor_export(self) -> bool:
        """An explicit editor context, never a filename heuristic, controls routing."""

        return bool(
            getattr(self, "recap_editor_mode", False)
            and getattr(self, "recap_editor_effects_path", None)
            and getattr(self, "recap_editor_asset_plan_path", None)
        )

    def reset_render_log_file(self):
        """
        Start a fresh output/render_log.txt for this render. Overwritten
        (not appended) each time Generate Final Video runs, so it always
        reflects only the most recent render -- e.g. for pasting into an
        issue report or handing to an AI assistant for analysis, without
        needing to copy the render log widget by hand.
        """

        try:
            RENDER_LOG_FILE_PATH.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            RENDER_LOG_FILE_PATH.write_text(
                (
                    f"ShortsFactory render log -- "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Source: {self.video_path}\n"
                    "=" * 60
                    + "\n\n"
                ),
                encoding="utf-8",
            )
        except OSError:
            pass


    def append_render_log_file(self, text: str):

        if not text:
            return

        try:
            with RENDER_LOG_FILE_PATH.open(
                "a",
                encoding="utf-8",
            ) as f:
                f.write(text)
        except OSError:
            pass


    def write_render_log_snapshot(self):
        """
        Overwrite output/render_log.txt with the full contents of the
        render log widget. Runs at the end of a render (success or
        failure) so status-header lines the GUI itself writes directly
        into the widget (progress/stage announcements scattered across
        the render mixins) end up in the file too, not just the raw
        ffmpeg/whisper subprocess output streamed by read_render_output()/
        read_render_error().
        """

        try:
            RENDER_LOG_FILE_PATH.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            RENDER_LOG_FILE_PATH.write_text(
                strip_ffmpeg_banner(
                    self.render_log.toPlainText()
                ),
                encoding="utf-8",
            )
        except OSError:
            pass


    def format_progress_duration(
        self,
        seconds: float,
    ) -> str:

        seconds = max(
            0,
            int(
                round(
                    seconds
                )
            ),
        )
        minutes = seconds // 60
        remaining = seconds % 60

        return f"{minutes:02d}:{remaining:02d}"


    def estimate_render_seconds(
        self,
        clip_duration_seconds: float,
        has_music: bool,
        has_reframe: bool,
    ) -> float:

        duration = max(
            1.0,
            float(
                clip_duration_seconds
                or 1.0
            ),
        )
        energy = self.current_edit_energy()
        multiplier = {
            "LOW": 2.2,
            "PUNCHY": 2.85,
            "MAXIMUM": 3.55,
        }.get(
            energy,
            2.85,
        )

        estimate = 30.0 + duration * multiplier

        if has_reframe:
            estimate += max(
                10.0,
                duration * 0.55,
            )

        if has_music:
            estimate += max(
                12.0,
                duration * 0.55,
            )

        return max(
            45.0,
            estimate,
        )


    def active_generation_processes(
        self,
    ) -> list[tuple[int, str]]:

        activities: list[tuple[int, str]] = []

        def add_if_running(
            process,
            priority: int,
            label: str,
        ):
            if (
                process.state()
                != QProcess.ProcessState.NotRunning
            ):
                activities.append(
                    (
                        int(priority),
                        str(label),
                    )
                )

        add_if_running(
            self.web_image_process,
            92,
            (
                "DOWNLOADING WEB IMAGE"
                if self.web_image_operation == "download"
                else "SEARCHING WEB IMAGES"
            ),
        )
        add_if_running(
            self.visual_asset_process,
            90,
            (
                "GENERATING CHATGPT IMAGE"
                if getattr(
                    self,
                    "visual_asset_provider",
                    "auto",
                ) == "openai"
                else "GENERATING IMAGES"
            ),
        )
        add_if_running(
            self.visual_process,
            80,
            "PLANNING VISUALS",
        )
        add_if_running(
            self.analysis_process,
            75,
            (
                "TRANSCRIBING SOURCE"
                if self.analysis_stage == "transcribe"
                else "FINDING BEST CLIPS"
            ),
        )
        add_if_running(
            self.sfx_process,
            70,
            "GENERATING SFX",
        )
        add_if_running(
            self.transcript_preload_process,
            65,
            "TRANSCRIBING SOURCE",
        )
        add_if_running(
            self.image_status_process,
            60,
            (
                "LOADING IMAGE MODEL"
                if self.pending_image_model_change
                else "STARTING IMAGE AI"
            ),
        )
        add_if_running(
            self.reframe_process,
            95,
            "FRAMING",
        )
        add_if_running(
            self.render_process,
            100,
            "RENDERING",
        )
        add_if_running(
            self.music_process,
            96,
            "MUSIC MIX",
        )

        return activities


    def update_global_progress(self):

        if not hasattr(
            self,
            "render_progress_bar",
        ):
            return

        # The render pipeline already has a useful estimated percentage and
        # stage model. Never replace it with the generic busy animation.
        if self.render_progress_active:
            return

        activities = self.active_generation_processes()

        if activities:
            activities.sort(
                key=lambda item: item[0],
                reverse=True,
            )
            _priority, label = activities[0]

            # QProgressBar range 0..0 is Qt's native indeterminate/busy mode.
            # It communicates real activity without inventing fake percentages
            # for AI/backend jobs that do not expose measurable completion.
            self.render_progress_bar.setRange(
                0,
                0,
            )
            self.render_progress_stage_label.setText(
                label
            )
            self.render_progress_time_label.setText(
                (
                    "Working..."
                    if len(activities) == 1
                    else f"Working...  •  {len(activities)} tasks active"
                )
            )
            self.render_progress_stage = "background"
            return

        # Preserve a completed/failed render result until the next operation
        # begins. All other background activity returns to a neutral READY
        # footer as soon as its process exits.
        if self.render_progress_stage in {
            "complete",
            "failed",
        }:
            return

        self.render_progress_bar.setRange(
            0,
            100,
        )
        self.render_progress_bar.setValue(
            0
        )
        self.render_progress_stage_label.setText(
            "READY"
        )
        self.render_progress_time_label.setText(
            "Idle"
        )
        self.render_progress_stage = "idle"


    def start_render_progress(
        self,
        clip_duration_seconds: float,
        has_music: bool,
        has_reframe: bool,
    ):

        self.render_progress_active = True
        self.render_progress_started_at = time.monotonic()
        self.render_progress_stage_started_at = (
            self.render_progress_started_at
        )
        self.render_progress_estimate_seconds = (
            self.estimate_render_seconds(
                clip_duration_seconds,
                has_music,
                has_reframe,
            )
        )
        self.render_progress_last_value = 0
        self.render_progress_bar.setRange(
            0,
            100,
        )
        self.render_progress_bar.setValue(
            0
        )
        self.render_progress_time_label.setText(
            (
                "Estimated "
                + self.format_progress_duration(
                    self.render_progress_estimate_seconds
                )
            )
        )
        self.set_render_progress_stage(
            "framing"
            if has_reframe
            else "rendering"
        )
        self.render_progress_timer.start()


    def set_render_progress_stage(
        self,
        stage: str,
    ):

        if not hasattr(
            self,
            "render_progress_bar",
        ):
            return

        self.render_progress_stage = stage
        self.render_progress_stage_started_at = time.monotonic()

        stage_map = {
            "framing": (
                "FRAMING",
                0,
                15,
                0.18,
            ),
            "rendering": (
                "RENDERING",
                15,
                92
                if self.music_path
                else 98,
                0.72,
            ),
            "music": (
                "MUSIC MIX",
                92,
                99,
                0.16,
            ),
            "complete": (
                "COMPLETE",
                100,
                100,
                0.01,
            ),
            "failed": (
                "FAILED",
                self.render_progress_last_value,
                self.render_progress_last_value,
                0.01,
            ),
            "idle": (
                "WAITING",
                0,
                100,
                1.0,
            ),
        }

        label, floor, ceiling, ratio = stage_map.get(
            stage,
            stage_map[
                "idle"
            ],
        )

        self.render_progress_floor = int(
            floor
        )
        self.render_progress_ceiling = int(
            ceiling
        )
        self.render_progress_stage_estimate_seconds = max(
            1.0,
            self.render_progress_estimate_seconds
            * float(
                ratio
            ),
        )

        self.render_progress_stage_label.setText(
            label
        )
        self.update_render_progress()


    def update_render_progress(self):

        if not hasattr(
            self,
            "render_progress_bar",
        ):
            return

        if not self.render_progress_active:
            return

        now = time.monotonic()
        elapsed = max(
            0.0,
            now
            - self.render_progress_started_at,
        )
        remaining = max(
            0.0,
            self.render_progress_estimate_seconds
            - elapsed,
        )

        if self.render_progress_stage in {
            "complete",
            "failed",
        }:
            return

        stage_elapsed = max(
            0.0,
            now
            - self.render_progress_stage_started_at,
        )
        stage_fraction = min(
            0.96,
            stage_elapsed
            / max(
                1.0,
                self.render_progress_stage_estimate_seconds,
            ),
        )
        value = int(
            round(
                self.render_progress_floor
                + (
                    self.render_progress_ceiling
                    - self.render_progress_floor
                )
                * stage_fraction
            )
        )
        value = max(
            self.render_progress_last_value,
            min(
                self.render_progress_ceiling,
                value,
            ),
        )
        self.render_progress_last_value = value
        self.render_progress_bar.setValue(
            value
        )
        self.render_progress_time_label.setText(
            (
                f"Elapsed {self.format_progress_duration(elapsed)}"
                "  /  "
                f"ETA {self.format_progress_duration(remaining)}"
            )
        )


    def finish_render_progress(
        self,
        success: bool,
    ):

        if not hasattr(
            self,
            "render_progress_bar",
        ):
            return

        self.render_progress_timer.stop()
        elapsed = (
            time.monotonic()
            - self.render_progress_started_at
            if self.render_progress_started_at
            else 0.0
        )
        self.render_progress_active = False

        self.render_progress_bar.setRange(
            0,
            100,
        )

        if success:
            self.render_progress_stage = "complete"
            self.render_progress_last_value = 100
            self.render_progress_bar.setValue(
                100
            )
            self.render_progress_stage_label.setText(
                "COMPLETE"
            )
            self.render_progress_time_label.setText(
                (
                    "Finished in "
                    + self.format_progress_duration(
                        elapsed
                    )
                )
            )
        else:
            self.render_progress_stage = "failed"
            self.render_progress_stage_label.setText(
                "FAILED"
            )
            self.render_progress_time_label.setText(
                (
                    "Stopped after "
                    + self.format_progress_duration(
                        elapsed
                    )
                )
            )


    def finish_short_success(self):

        current_final_video = (
            ROOT
            / "output"
            / "rendered"
            / "short1_captioned.mp4"
        )

        try:
            final_video = archive_final_video(
                current_final_video
            )
        except OSError as exc:
            # The render itself already succeeded. Keep the fixed-path final
            # usable if Windows briefly has it open, while making the issue
            # visible instead of turning a completed render into a failure.
            final_video = current_final_video
            self.render_log.append(
                "WARNING: Could not archive final video as a numbered "
                f"clip: {exc}"
            )

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
            True
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "✓ SHORT COMPLETE"
        )

        self.render_log.append(
            f"Final video: {final_video}"
        )

        self.load_render_timeline_overlays()
        self.open_final_video(final_video)
        self.write_render_log_snapshot()


    def preserve_existing_final_before_render(self):
        """Archive a legacy fixed-path final before the next render replaces it."""

        current_final_video = (
            ROOT
            / "output"
            / "rendered"
            / "short1_captioned.mp4"
        )

        if not current_final_video.exists():
            return

        try:
            archived_video = archive_final_video(
                current_final_video
            )
        except OSError as exc:
            self.render_log.append(
                "WARNING: Could not preserve the previous final video "
                f"before rendering: {exc}"
            )
            return

        self.render_log.append(
            f"Previous final preserved as: {archived_video}"
        )


    def open_final_video(
        self,
        final_video: Path,
    ):

        if not final_video.exists():
            self.render_log.append(
                "Could not auto-open final video because the file was not found."
            )
            return

        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                str(
                    final_video
                )
            )
        )

        if opened:
            self.render_log.append(
                "Opened final video in the default video player."
            )
        else:
            self.render_log.append(
                "Could not auto-open final video."
            )


    def generate_short(self):

        if not self.video_path:
            return

        if self.is_recap_editor_export():
            self.start_recap_editor_export()
            return

        if self.end_ms <= self.start_ms:
            self.render_log.append(
                "ERROR: Please select a valid start and end point."
            )
            return

        start_seconds = (
            self.start_ms
            / 1000
        )

        end_seconds = (
            self.end_ms
            / 1000
        )

        duration_seconds = (
            end_seconds
            - start_seconds
        )

        self.pending_original_start_seconds = (
            start_seconds
        )

        self.pending_original_end_seconds = (
            end_seconds
        )

        self.pending_render_duration_seconds = (
            duration_seconds
        )

        # Feed the original source directly to the renderer; render.py owns
        # the final 9:16 crop-to-fill composition (scales up and
        # center-crops to cover the frame edge-to-edge, no letterboxing --
        # see render_base_video()).
        self.pending_render_source = None

        self.start_render_progress(
            duration_seconds,
            bool(
                self.music_path
            ),
            False,
        )

        self.render_log.clear()
        self.reset_render_log_file()

        self.preserve_existing_final_before_render()

        self.render_log.append(
            "Starting ShortsFactory..."
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            f"Source: {self.video_path.name}"
        )

        self.save_render_settings()

        self.render_log.append(
            f"Edit energy: {self.current_edit_energy()}"
        )

        self.render_log.append(
            (
                "Selection: "
                f"{start_seconds:.2f}s "
                "→ "
                f"{end_seconds:.2f}s"
            )
        )

        self.render_log.append(
            ""
        )

        self.generate_button.setEnabled(
            False
        )

        self.generate_button.setText(
            "Rendering..."
        )

        self.find_clips_button.setEnabled(
            False
        )

        self.music_button.setEnabled(
            False
        )

        if self.music_path:

            self.render_log.append(
                (
                    "Background music queued: "
                    f"{self.music_path.name} "
                    f"at {self.music_volume}%"
                )
            )

            self.render_log.append(
                ""
            )

        self.render_log.append(
            "=== PRE-RENDER: 9:16 CROP-TO-FILL ==="
        )
        self.render_log.append(
            "Scaling up and center-cropping to fill the frame edge-to-edge; "
            "left/right (or top/bottom) source edges outside 9:16 will be cut off."
        )

        self.start_main_render(
            self.video_path,
            start_seconds,
            end_seconds,
        )


    def start_main_render(
        self,
        source_path: Path,
        start_seconds: float,
        end_seconds: float,
    ):

        render_script = (
            ROOT
            / "app"
            / "render.py"
        )

        arguments = [
            str(
                render_script
            ),
            "--source",
            str(
                source_path
            ),
            "--start",
            f"{start_seconds:.3f}",
            "--end",
            f"{end_seconds:.3f}",
        ]

        self.generate_button.setText(
            "Rendering..."
        )

        self.set_render_progress_stage(
            "rendering"
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "=== MAIN SHORTSFACTORY RENDER ==="
        )

        self.render_log.append(
            ""
        )

        self.render_process.start(
            sys.executable,
            arguments,
        )


    def read_reframe_output(self):

        data = (
            self.reframe_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        if data:

            self.render_log.moveCursor(
                self.render_log
                .textCursor()
                .MoveOperation
                .End
            )

            self.render_log.insertPlainText(
                data
            )

            scrollbar = (
                self.render_log
                .verticalScrollBar()
            )

            scrollbar.setValue(
                scrollbar.maximum()
            )


    def read_reframe_error(self):

        data = (
            self.reframe_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        if data:

            self.render_log.moveCursor(
                self.render_log
                .textCursor()
                .MoveOperation
                .End
            )

            self.render_log.insertPlainText(
                data
            )

            scrollbar = (
                self.render_log
                .verticalScrollBar()
            )

            scrollbar.setValue(
                scrollbar.maximum()
            )


    def reframe_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        duration = max(
            0.01,
            self.pending_render_duration_seconds,
        )

        if (
            exit_code == 0
            and self.pending_render_source is not None
            and self.pending_render_source.exists()
        ):

            self.render_log.append(
                ""
            )

            self.render_log.append(
                "✓ Subject-aware framing stage complete."
            )

            # The temporary source is already the exact selected clip
            # and already 1080x1920. Feed it to render.py from t=0.
            self.start_main_render(
                self.pending_render_source,
                0.0,
                duration,
            )

            return

        self.render_log.append(
            ""
        )

        self.render_log.append(
            (
                "WARNING: Smart reframe failed. "
                "Falling back to the existing center crop."
            )
        )

        self.start_main_render(
            self.video_path,
            self.pending_original_start_seconds,
            self.pending_original_end_seconds,
        )


    def read_render_output(self):

        data = (
            self.render_process
            .readAllStandardOutput()
            .data()
            .decode("utf-8", errors="replace")
        )

        if data:
            self.render_log.moveCursor(
                self.render_log.textCursor().MoveOperation.End
            )
            self.render_log.insertPlainText(data)
            scrollbar = self.render_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            self.append_render_log_file(data)

    def read_render_error(self):

        data = (
            self.render_process
            .readAllStandardError()
            .data()
            .decode("utf-8", errors="replace")
        )

        if data:
            self.render_log.moveCursor(
                self.render_log.textCursor().MoveOperation.End
            )
            self.render_log.insertPlainText(data)
            scrollbar = self.render_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            self.append_render_log_file(data)

    def render_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        if getattr(self, "recap_export_in_progress", False):
            self.recap_export_in_progress = False
            if exit_code == 0:
                self.finish_recap_editor_export()
                return

        if exit_code == 0:

            if self.music_path:

                self.start_music_mix()

            else:

                self.finish_short_success()

            return

        self.generate_button.setEnabled(
            True
        )

        self.generate_button.setText(
            "Generate Final Video"
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
            "✕ RENDER FAILED"
        )

        self.render_log.append(
            f"Exit code: {exit_code}"
        )

        self.write_render_log_snapshot()


