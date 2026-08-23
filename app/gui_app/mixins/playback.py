from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer

from ..helpers import format_precise_time, format_time


class PlaybackMixin:

    def load_video(
        self,
        path: Path,
    ):

        path = Path(path).resolve()
        if not path.exists():
            self.report_playback_message(
                f"source file does not exist: {path}"
            )
            return

        self.cancel_paused_seek_refresh()
        self.hide_ai_visual_preview_overlay()
        self.play_request_counter += 1
        self.video_path = path

        self.file_label.setText(
            path.name
        )

        # Reset the Windows media backend completely when loading a source.
        # Leaving an old/native media surface attached can produce a black
        # preview after background analysis seeks before the first playback.
        self.player.stop()
        self.player.setSource(
            QUrl()
        )
        self.player.setVideoOutput(
            self.video_widget
        )
        self.player.setSource(
            QUrl.fromLocalFile(
                str(path)
            )
        )
        self.player.setPosition(
            0
        )
        self.play_button.setEnabled(
            False
        )
        self.play_button.setText(
            "LOADING"
        )

        # Prime a real frame once Qt has had a chance to load the local file,
        # then recover Play even if the backend lingers in a buffering status.
        QTimer.singleShot(
            250,
            self.prime_preview_frame,
        )
        QTimer.singleShot(
            900,
            self.ensure_preview_playable,
        )

        self.start_ms = 0
        self.end_ms = 0

        self.timeline.set_selection_range(
            0,
            0,
        )

        self.selection_label.setText(
            "Selection: 00:00 → 00:00"
        )

        self.generate_button.setEnabled(
            True
        )

        self.find_clips_button.setEnabled(
            True
        )

        self.find_clips_button.setText(
            "✦ Find Best Clips"
        )

        self.timeline.clear_suggestions()
        self.timeline.clear_editor_overlays()
        self.selected_sfx_clip_id = None
        self.load_editor_asset_plan_state()

        self.ai_candidates = []
        self.visual_plan_slots = []
        self.visual_deleted_slots = []
        self.reset_pending_visual_replan_state()

        if hasattr(
            self,
            "visual_slots_list",
        ):
            self.visual_slots_list.clear()
            self.visual_status_label.setText(
                "Load a transcript, then plan visuals for the active selection."
            )
            self.plan_visuals_button.setEnabled(
                False
            )

            self.generate_visual_assets_button.setEnabled(
                False
            )

        self.source_transcript_segments = []
        self.manual_cut_segments = set()
        self.transcript_corrections = {}
        self.save_manual_edit_plan()
        self.save_transcript_corrections()

        self.reset_clip_cards()

        if hasattr(
            self,
            "transcript_list",
        ):
            self.transcript_list.clear()

            self.transcript_status_label.setText(
                "Run Find Best Clips to load the source transcript."
            )

        self.suggestions_label.setText(
            "AI clips appear as purple ranges on V1. Drag IN / OUT handles to fine-tune the selected clip."
        )
        self.update_sfx_button_state()
        self.set_selection_loop_enabled(
            False
        )
        self.start_transcript_preload()

    def prime_preview_frame(self):

        if not self.video_path:
            return

        if (
            self.player.error()
            != QMediaPlayer.Error.NoError
        ):
            return

        if (
            self.player.mediaStatus()
            == QMediaPlayer.MediaStatus.NoMedia
        ):
            return

        self.player.setPosition(
            0
        )

        if (
            self.player.duration() > 0
            and self.player.playbackState()
            == QMediaPlayer.PlaybackState.StoppedState
            and not self.paused_seek_refresh_pending
        ):
            # Some Windows Qt multimedia backends do not paint the first frame
            # until playback advances briefly. Do that muted, then pause.
            self.paused_seek_refresh_pending = True
            self.audio_output.setMuted(
                True
            )
            self.player.play()
            QTimer.singleShot(
                55,
                self.finish_paused_seek,
            )

    def ensure_preview_playable(self):

        if not self.video_path:
            return

        if not hasattr(
            self,
            "play_button",
        ):
            return

        if (
            self.player.error()
            != QMediaPlayer.Error.NoError
        ):
            return

        if (
            self.player.mediaStatus()
            == QMediaPlayer.MediaStatus.InvalidMedia
        ):
            return

        self.play_button.setEnabled(
            True
        )
        self.update_play_button(
            self.player.playbackState()
        )

    def toggle_playback(self):

        if not self.video_path:
            return

        # Recover if the Windows media backend dropped the source while
        # background transcription/analysis was running.
        if (
            self.player.mediaStatus()
            == QMediaPlayer.MediaStatus.NoMedia
        ):
            self.player.setVideoOutput(
                self.video_widget
            )
            self.player.setSource(
                QUrl.fromLocalFile(
                    str(self.video_path)
                )
            )
            self.player.setPosition(
                0
            )

        refresh_playback = self.paused_seek_refresh_pending

        if (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
            and not refresh_playback
        ):

            self.play_request_counter += 1
            self.player.pause()

        else:

            self.play_request_counter += 1
            play_request = self.play_request_counter
            self.cancel_paused_seek_refresh()
            self.set_selection_loop_enabled(
                self.position_in_selection(
                    self.player.position()
                )
            )
            self.audio_output.setMuted(
                False
            )

            if (
                self.player.duration() > 0
                and self.player.position()
                >= self.player.duration()
                - 80
            ):
                self.player.setPosition(
                    self.start_ms
                    if self.has_active_selection()
                    else 0
            )

            self.player.play()
            self.update_play_button(
                QMediaPlayer.PlaybackState.PlayingState
            )

            QTimer.singleShot(
                350,
                lambda: self.verify_playback_started(
                    play_request
                ),
            )

    def has_active_selection(self) -> bool:

        return (
            self.end_ms
            > self.start_ms
            and self.end_ms
            - self.start_ms
            >= 500
        )

    def position_in_selection(
        self,
        position: int,
    ) -> bool:

        if not self.has_active_selection():
            return False

        return (
            self.start_ms
            <= int(
                position
            )
            < self.end_ms
        )

    def set_selection_loop_enabled(
        self,
        enabled: bool,
    ):

        enabled = bool(
            enabled
            and self.has_active_selection()
        )

        if self.selection_loop_enabled == enabled:
            return

        self.selection_loop_enabled = enabled

        if hasattr(
            self,
            "selection_label",
        ):
            self.update_selection_label()

    def position_changed(
        self,
        position: int,
    ):

        if (
            self.selection_loop_enabled
            and self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
            and self.has_active_selection()
            and position >= self.end_ms - 45
        ):
            self.player.setPosition(
                self.start_ms
            )
            self.timeline.setValue(
                self.start_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.start_ms
                )
            )
            self.update_ai_visual_preview_overlay(
                self.start_ms
            )
            return

        if not self.timeline.isSliderDown():

            self.timeline.setValue(
                position
            )

            if (
                self.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            ):
                self.timeline.follow_playhead(
                    position
                )

        self.current_time_label.setText(
            format_precise_time(position)
        )
        if hasattr(
            self,
            "timeline_time_label",
        ):
            self.timeline_time_label.setText(
                (
                    f"{format_precise_time(position)} / "
                    f"{format_precise_time(self.player.duration())}"
                )
            )

        self.trigger_sfx_previews(
            position
        )
        self.update_ai_visual_preview_overlay(
            position
        )

    def duration_changed(
        self,
        duration: int,
    ):

        if duration > 0:
            # Duration arrival proves Qt has loaded enough media to play even
            # if the ideal LoadedMedia/BufferedMedia signal never arrived.
            self.ensure_preview_playable()
            if (
                self.player.playbackState()
                == QMediaPlayer.PlaybackState.StoppedState
                and self.player.position() <= 0
            ):
                QTimer.singleShot(
                    0,
                    self.prime_preview_frame,
                )

        self.timeline.setRange(
            0,
            duration,
        )
        self.timeline.fit_source()

        self.duration_label.setText(
            format_precise_time(duration)
        )
        if hasattr(
            self,
            "timeline_time_label",
        ):
            self.timeline_time_label.setText(
                (
                    f"{format_precise_time(self.player.position())} / "
                    f"{format_precise_time(duration)}"
                )
            )

        if self.end_ms == 0:

            self.end_ms = duration

            self.timeline.set_selection_range(
                self.start_ms,
                self.end_ms,
            )

            self.update_selection_label()

    def seek_video(
        self,
        position: int,
    ):

        self.set_selection_loop_enabled(
            self.position_in_selection(
                position
            )
        )

        was_playing = (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )

        self.player.setPosition(
            position
        )
        self.current_time_label.setText(
            format_precise_time(position)
        )
        if hasattr(
            self,
            "timeline_time_label",
        ):
            self.timeline_time_label.setText(
                (
                    f"{format_precise_time(position)} / "
                    f"{format_precise_time(self.player.duration())}"
                )
            )

        self.update_ai_visual_preview_overlay(
            position
        )

        if not was_playing and getattr(
            self.timeline,
            "scrubbing_playhead",
            False,
        ):
            return

        # On some Windows multimedia backends, a paused video does
        # not immediately redraw after seeking. Briefly advancing the
        # player while muted forces the preview frame to refresh, but
        # never while actively dragging the playhead.
        if not was_playing and not self.paused_seek_refresh_pending:

            self.paused_seek_refresh_pending = True
            self.audio_output.setMuted(
                True
            )

            self.player.play()

            QTimer.singleShot(
                45,
                self.finish_paused_seek,
            )

    def finish_paused_seek(self):

        if self.paused_seek_refresh_pending:
            self.player.pause()

            self.audio_output.setMuted(
                False
            )

        self.paused_seek_refresh_pending = False

    def cancel_paused_seek_refresh(self):

        self.paused_seek_refresh_pending = False
        self.audio_output.setMuted(
            False
        )

    def verify_playback_started(
        self,
        play_request: int,
    ):

        if not self.video_path:
            return

        if play_request != self.play_request_counter:
            return

        if (
            self.player.error()
            != QMediaPlayer.Error.NoError
        ):
            return

        if (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            return

        self.report_playback_message(
            "Preview did not start playback. "
            "Try clicking the timeline once or reload the source; "
            "if it keeps happening, check the media error above."
        )

    def seek_to_slider_position(self):

        self.seek_video(
            self.timeline.value()
        )

    def timeline_viewport_changed(
        self,
        start_ms: int,
        end_ms: int,
    ):

        if not hasattr(
            self,
            "timeline_navigator",
        ):
            return

        self.updating_timeline_controls = True

        duration = max(
            0,
            self.timeline.maximum(),
        )

        self.timeline_navigator.set_state(
            duration,
            start_ms,
            end_ms,
            self.timeline.minimum_visible_ms,
        )

        self.timeline_zoom_slider.setValue(
            int(
                round(
                    self.timeline.zoom_fraction()
                    * 100
                )
            )
        )

        self.updating_timeline_controls = False

    def timeline_navigator_changed(
        self,
        start_ms: int,
        end_ms: int,
    ):

        if self.updating_timeline_controls:
            return

        self.timeline.set_viewport(
            int(
                start_ms
            ),
            int(
                end_ms
            ),
            manual=True,
        )

    def timeline_zoom_slider_changed(
        self,
        value: int,
    ):

        if self.updating_timeline_controls:
            return

        self.timeline.set_zoom_fraction(
            value / 100
        )

    def fit_timeline_selection(self):

        self.timeline.fit_selection()

    def fit_timeline_source(self):

        self.timeline.fit_source()

    def reveal_timeline_range(
        self,
        start_ms: int,
        end_ms: int,
    ):

        self.timeline.reveal_range(
            int(
                start_ms
            ),
            int(
                end_ms
            ),
            manual=False,
        )

    def reveal_timeline_time(
        self,
        position_ms: int,
    ):

        self.timeline.reveal_time(
            int(
                position_ms
            ),
            manual=False,
        )

    def update_play_button(self, state):

        if (
            state
            == QMediaPlayer.PlaybackState.PlayingState
            and not self.paused_seek_refresh_pending
        ):
            self.play_button.setText(
                "PAUSE"
            )
        else:
            self.play_button.setText(
                "PLAY"
            )

    def report_playback_message(
        self,
        message: str,
    ):

        text = f"Preview playback: {message}"

        print(
            text
        )

        if hasattr(
            self,
            "render_log",
        ):
            self.render_log.append(
                text
            )

        if hasattr(
            self,
            "suggestions_label",
        ):
            self.suggestions_label.setText(
                text
            )

    def media_status_changed(
        self,
        status,
    ):

        if (
            status
            == QMediaPlayer.MediaStatus.LoadingMedia
        ):
            if hasattr(
                self,
                "play_button",
            ):
                self.play_button.setEnabled(
                    False
                )
                self.play_button.setText(
                    "LOADING"
                )

            return

        if status in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        }:
            self.ensure_preview_playable()

            if status in {
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            }:
                QTimer.singleShot(
                    0,
                    self.prime_preview_frame,
                )

            return

        if (
            status
            == QMediaPlayer.MediaStatus.InvalidMedia
        ):
            if hasattr(
                self,
                "play_button",
            ):
                self.play_button.setEnabled(
                    False
                )
                self.play_button.setText(
                    "PLAY"
                )

            details = (
                self.player.errorString()
                or (
                    self.video_path.name
                    if self.video_path
                    else "unknown source"
                )
            )
            self.report_playback_message(
                f"could not load media. {details}"
            )

        elif (
            status
            == QMediaPlayer.MediaStatus.EndOfMedia
        ):
            self.set_selection_loop_enabled(
                False
            )

    def playback_error_occurred(
        self,
        error,
        error_string: str = "",
    ):

        if error == QMediaPlayer.Error.NoError:
            return

        details = (
            error_string
            or self.player.errorString()
            or str(
                error
            )
        )

        self.report_playback_message(
            f"media error. {details}"
        )

    def timeline_selection_changed(
        self,
        start_ms: int,
        end_ms: int,
    ):

        preserve_editor_assets = bool(
            self.editor_asset_context_matches_current_selection()
            and self.editor_asset_plan.get(
                "clips",
                [],
            )
            and (
                getattr(
                    self.timeline,
                    "dragging_handle",
                    None,
                )
                in {
                    "start",
                    "end",
                }
                or getattr(
                    self.timeline,
                    "dragging_source_clip",
                    False,
                )
            )
        )

        self.start_ms = start_ms
        self.end_ms = end_ms

        self.set_selection_loop_enabled(
            self.selection_loop_enabled
            and self.position_in_selection(
                self.player.position()
            )
        )

        if getattr(
            self.timeline,
            "dragging_handle",
            None,
        ) == "start":
            self.player.setPosition(
                self.start_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.start_ms
                )
            )
        elif getattr(
            self.timeline,
            "dragging_handle",
            None,
        ) == "end":
            self.player.setPosition(
                self.end_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.end_ms
                )
            )
        elif getattr(
            self.timeline,
            "dragging_source_clip",
            False,
        ):
            self.player.setPosition(
                self.start_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.start_ms
                )
            )

        self.timeline.set_selected_suggestion(
            None
        )

        self.refresh_clip_card_selection(
            None
        )

        self.update_selection_label()

        self.update_transcript_panel()
        if preserve_editor_assets:
            self.retarget_editor_asset_context_to_current_selection()
            self.apply_editor_visual_overrides_to_slots()
            if self.visual_plan_slots:
                self.save_ai_visual_plan()
            self.refresh_visual_plan_display()
        else:
            self.clear_visual_plan_display()
            self.selected_sfx_clip_id = None
            self.refresh_editor_asset_timeline()

        if hasattr(
            self,
            "plan_visuals_button",
        ):
            self.plan_visuals_button.setEnabled(
                bool(
                    self.source_transcript_segments
                    and self.end_ms > self.start_ms
                )
            )

    def set_start(self):

        preserve_editor_assets = bool(
            self.editor_asset_context_matches_current_selection()
            and self.editor_asset_plan.get(
                "clips",
                [],
            )
        )

        self.timeline.set_selected_suggestion(
            None
        )

        self.refresh_clip_card_selection(
            None
        )

        self.start_ms = (
            self.player.position()
        )

        if (
            self.end_ms
            and self.start_ms >= self.end_ms
        ):

            self.end_ms = (
                self.player.duration()
            )

        self.timeline.set_selection_range(
            self.start_ms,
            self.end_ms,
        )
        self.reveal_timeline_time(
            self.start_ms
        )

        self.update_selection_label()
        if preserve_editor_assets:
            self.retarget_editor_asset_context_to_current_selection()
            self.apply_editor_visual_overrides_to_slots()
            if self.visual_plan_slots:
                self.save_ai_visual_plan()
            self.refresh_visual_plan_display()
        else:
            self.clear_visual_plan_display()

    def set_end(self):

        preserve_editor_assets = bool(
            self.editor_asset_context_matches_current_selection()
            and self.editor_asset_plan.get(
                "clips",
                [],
            )
        )

        self.timeline.set_selected_suggestion(
            None
        )

        self.refresh_clip_card_selection(
            None
        )

        self.end_ms = (
            self.player.position()
        )

        if self.end_ms <= self.start_ms:

            self.start_ms = 0

        self.timeline.set_selection_range(
            self.start_ms,
            self.end_ms,
        )
        self.reveal_timeline_time(
            self.end_ms
        )

        self.update_selection_label()
        if preserve_editor_assets:
            self.retarget_editor_asset_context_to_current_selection()
            self.apply_editor_visual_overrides_to_slots()
            if self.visual_plan_slots:
                self.save_ai_visual_plan()
            self.refresh_visual_plan_display()
        else:
            self.clear_visual_plan_display()

    def update_selection_label(self):

        duration_ms = max(
            0,
            self.end_ms - self.start_ms,
        )

        loop_text = (
            "   •   LOOP"
            if self.selection_loop_enabled
            else ""
        )

        self.selection_label.setText(
            "Selection: "
            f"{format_time(self.start_ms)}"
            " → "
            f"{format_time(self.end_ms)}"
            f"   •   {duration_ms / 1000:.1f}s"
            f"{loop_text}"
        )

        if hasattr(
            self,
            "transcript_list",
        ):
            self.update_transcript_panel()

        self.update_sfx_button_state()

    def preview_volume_changed(
        self,
        value: int,
    ):

        self.preview_volume = max(
            0,
            min(
                100,
                int(
                    value
                ),
            ),
        )

        self.audio_output.setVolume(
            self.preview_volume
            / 100
        )
        if hasattr(
            self,
            "sfx_preview_audio",
        ):
            self.sfx_preview_audio.setVolume(
                self.preview_volume
                / 100
            )

        self.preview_volume_label.setText(
            f"{self.preview_volume}%"
        )

        self.settings.setValue(
            "preview/volume",
            self.preview_volume,
        )


