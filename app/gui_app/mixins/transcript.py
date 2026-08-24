"""
TranscriptMixin: transcript preloading (background subtitles.py run as
soon as a source video loads, ahead of Find Best Clips) and the
right-panel transcript editor (click a line to correct/cut it).
Content-hash-based cache checking (cached_transcript_ready_for_current_source())
avoids re-transcribing a source video the app has already processed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QInputDialog, QListWidgetItem

from ..constants import ROOT
from ..helpers import format_time


class TranscriptMixin:

    def transcript_json_matches_current_source(
        self,
        data: dict,
    ) -> bool:

        if not self.video_path or not isinstance(
            data,
            dict,
        ):
            return False

        source_path = str(
            data.get(
                "source_video_path",
                "",
            )
            or ""
        ).strip()
        if not source_path:
            return False

        try:
            return (
                Path(source_path).resolve()
                == self.video_path.resolve()
            )
        except OSError:
            return False


    def cached_transcript_ready_for_current_source(self) -> bool:

        transcript_path = (
            ROOT
            / "output"
            / "subtitles.json"
        )
        if not transcript_path.exists():
            return False

        try:
            data = json.loads(
                transcript_path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            json.JSONDecodeError,
        ):
            return False

        if not self.transcript_json_matches_current_source(
            data
        ):
            return False

        quality = str(
            data.get(
                "quality",
                "",
            )
            or ""
        ).upper()
        return quality == self.current_transcription_quality()


    def start_transcript_preload(self):

        if not self.video_path:
            return

        if self.cached_transcript_ready_for_current_source():
            self.load_source_transcript()
            self.transcript_status_label.setText(
                "TRANSCRIPT CACHE HIT"
            )

            # Find Best Clips may already be waiting on a background
            # transcript job when the requested quality changes or a valid
            # cache becomes available. A cache hit must resume that pending
            # analysis instead of leaving the AI Clip Hunter stuck forever.
            if self.pending_find_best_after_preload:
                self.pending_find_best_after_preload = False
                self.render_log.append(
                    "Stage 1/2: Transcript ready. Continuing clip analysis..."
                )
                self.start_clip_analyzer()
            return

        if (
            self.transcript_preload_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            if (
                self.transcript_preload_source
                == str(self.video_path)
                and self.transcript_preload_quality
                == self.current_transcription_quality()
            ):
                return

            # A different source/quality was selected while a background
            # Whisper job was running. Preserve a pending Find Best Clips
            # request when only the quality changed for the same source;
            # otherwise killing/restarting the preload strands the disabled
            # AI Clip Hunter button with nothing left to resume it.
            resume_find_best = (
                self.pending_find_best_after_preload
                and self.transcript_preload_source
                == str(self.video_path)
            )
            self.transcript_preload_source = ""
            self.pending_find_best_after_preload = False
            self.transcript_preload_process.kill()
            self.transcript_preload_process.waitForFinished(
                250
            )
            self.pending_find_best_after_preload = resume_find_best

        subtitles_script = (
            ROOT
            / "app"
            / "subtitles.py"
        )
        if not subtitles_script.exists():
            self.transcript_status_label.setText(
                "Transcript engine is not installed."
            )
            return

        self.transcript_preload_source = str(
            self.video_path
        )
        self.transcript_preload_quality = (
            self.current_transcription_quality()
        )
        self.transcript_preload_output = ""
        self.transcript_status_label.setText(
            "TRANSCRIBING..."
        )
        self.render_log.append(
            ""
        )
        self.render_log.append(
            "=== BACKGROUND TRANSCRIPT PRELOAD ==="
        )
        self.render_log.append(
            f"Source: {self.video_path.name}"
        )

        self.transcript_preload_process.start(
            sys.executable,
            [
                str(subtitles_script),
                "--quality",
                self.transcript_preload_quality,
                str(self.video_path),
            ],
        )


    def read_transcript_preload_output(self):

        data = (
            self.transcript_preload_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        self.transcript_preload_output += data

        if "Transcript cache HIT" in data:
            self.transcript_status_label.setText(
                "TRANSCRIPT CACHE HIT"
            )
        elif "Transcript cache MISS" in data:
            self.transcript_status_label.setText(
                "TRANSCRIBING..."
            )


    def read_transcript_preload_error(self):

        data = (
            self.transcript_preload_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        self.transcript_preload_output += data


    def transcript_preload_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        del exit_status

        finished_source = self.transcript_preload_source
        current_source = (
            str(self.video_path)
            if self.video_path
            else ""
        )
        self.transcript_preload_source = ""
        self.transcript_preload_quality = ""

        # Never attach a stale background result to a subsequently imported
        # source video.
        if finished_source != current_source:
            return

        if exit_code != 0:
            self.transcript_status_label.setText(
                "TRANSCRIPT FAILED"
            )
            if self.pending_find_best_after_preload:
                self.pending_find_best_after_preload = False
                self.find_clips_button.setEnabled(
                    self.video_path is not None
                )
                self.find_clips_button.setText(
                    "✦ Find Best Clips"
                )
                self.generate_button.setEnabled(
                    self.video_path is not None
                )
            return

        if not self.cached_transcript_ready_for_current_source():
            self.transcript_status_label.setText(
                "TRANSCRIPT STALE"
            )
            if self.pending_find_best_after_preload:
                self.pending_find_best_after_preload = False
                self.find_clips_button.setEnabled(
                    self.video_path is not None
                )
                self.find_clips_button.setText(
                    "✦ Find Best Clips"
                )
                self.generate_button.setEnabled(
                    self.video_path is not None
                )
            return

        self.load_source_transcript()
        self.transcript_status_label.setText(
            "TRANSCRIPT CACHE HIT"
            if "Transcript cache HIT"
            in self.transcript_preload_output
            else "TRANSCRIPT READY"
        )

        if self.pending_find_best_after_preload:
            self.pending_find_best_after_preload = False
            self.start_clip_analyzer()


    def load_source_transcript(self):

        transcript_path = (
            ROOT
            / "output"
            / "subtitles.json"
        )

        self.source_transcript_segments = []

        if not transcript_path.exists():

            self.transcript_status_label.setText(
                "Source transcript is not available."
            )

            self.transcript_list.clear()
            return

        try:

            with transcript_path.open(
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

        except (
            OSError,
            json.JSONDecodeError,
        ):

            self.transcript_status_label.setText(
                "Could not read the source transcript."
            )

            self.transcript_list.clear()
            return

        if not self.transcript_json_matches_current_source(
            data
        ):
            self.transcript_status_label.setText(
                "Transcript belongs to a different source."
            )
            self.transcript_list.clear()
            return

        segments = data.get(
            "segments",
            [],
        )

        if not isinstance(
            segments,
            list,
        ):

            segments = []

        for segment in segments:

            if not isinstance(
                segment,
                dict,
            ):
                continue

            try:
                start = float(
                    segment.get(
                        "start",
                        0,
                    )
                )

                end = float(
                    segment.get(
                        "end",
                        start,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            text = str(
                segment.get(
                    "text",
                    "",
                )
                or ""
            ).strip()

            if (
                not text
                or end <= start
            ):
                continue

            self.source_transcript_segments.append(
                {
                    "start_ms": int(
                        round(
                            start * 1000
                        )
                    ),
                    "end_ms": int(
                        round(
                            end * 1000
                        )
                    ),
                    "text": text,
                    "original_text": text,
                }
            )

        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()

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


    def update_transcript_panel(self):

        if not hasattr(
            self,
            "transcript_list",
        ):
            return

        self.transcript_list.clear()

        if not self.source_transcript_segments:

            self.transcript_status_label.setText(
                "Run Find Best Clips to load the source transcript."
            )

            return

        visible_segments = [
            segment
            for segment in self.source_transcript_segments
            if (
                segment["end_ms"] > self.start_ms
                and segment["start_ms"] < self.end_ms
            )
        ]

        visible_cut_seconds = sum(
            max(
                0,
                min(segment["end_ms"], self.end_ms)
                - max(segment["start_ms"], self.start_ms),
            )
            for segment in visible_segments
            if (
                segment["start_ms"],
                segment["end_ms"],
            ) in self.manual_cut_segments
        ) / 1000

        total_cut_seconds = sum(
            max(0, end_ms - start_ms)
            for start_ms, end_ms in self.manual_cut_segments
        ) / 1000

        self.transcript_status_label.setText(
            f"{len(visible_segments)} lines in selection  //  "
            f"CUT {visible_cut_seconds:.1f}s here  //  "
            f"{total_cut_seconds:.1f}s total.  "
            f"{len(self.transcript_corrections)} corrected.  "
            "Click = seek  •  Double-click = EDIT TEXT"
        )

        for segment in visible_segments:

            timestamp = format_time(
                segment["start_ms"]
            )

            segment_key = (
                segment["start_ms"],
                segment["end_ms"],
            )

            is_cut = (
                segment_key
                in self.manual_cut_segments
            )

            segment_key = (
                segment["start_ms"],
                segment["end_ms"],
            )

            corrected_text = self.transcript_corrections.get(
                segment_key,
                segment["text"],
            )

            state_parts = []

            if is_cut:
                state_parts.append("CUT")
            else:
                state_parts.append("KEEP")

            if segment_key in self.transcript_corrections:
                state_parts.append("EDITED")

            state_marker = (
                "["
                + " / ".join(state_parts)
                + "]"
            )

            item = QListWidgetItem(
                f"{state_marker}  {timestamp}    {corrected_text}"
            )

            item.setData(
                Qt.ItemDataRole.UserRole,
                segment["start_ms"],
            )

            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                segment["end_ms"],
            )

            font = QFont(
                item.font()
            )

            if is_cut:

                font.setStrikeOut(
                    True
                )

                item.setForeground(
                    QColor(
                        "#d76572"
                    )
                )

                item.setBackground(
                    QColor(
                        "#211216"
                    )
                )

            else:

                font.setStrikeOut(
                    False
                )

                item.setForeground(
                    QColor(
                        "#d3cdc5"
                    )
                )

            item.setFont(
                font
            )

            item.setToolTip(
                (
                    f"{segment['start_ms'] / 1000:.2f}s → "
                    f"{segment['end_ms'] / 1000:.2f}s\n"
                    f"{corrected_text}\n\n"
                    + (
                        (
                            f"Original Whisper text: {segment['text']}\n\n"
                            if segment_key in self.transcript_corrections
                            else ""
                        )
                    )
                    + (
                        "Marked for removal from the final edit."
                        if is_cut
                        else "Kept in the final edit."
                    )
                )
            )

            self.transcript_list.addItem(
                item
            )


    def transcript_item_clicked(
        self,
        item: QListWidgetItem,
    ):

        position = item.data(
            Qt.ItemDataRole.UserRole
        )

        if position is None:
            return

        try:
            position = int(
                position
            )
        except (
            TypeError,
            ValueError,
        ):
            return

        self.player.setPosition(
            position
        )

        self.timeline.setValue(
            position
        )

        self.reveal_timeline_time(
            position
        )


    def selected_transcript_segment_key(self) -> tuple[int, int] | None:

        item = self.transcript_list.currentItem()

        if item is None:
            return None

        start_ms = item.data(
            Qt.ItemDataRole.UserRole
        )

        end_ms = item.data(
            Qt.ItemDataRole.UserRole + 1
        )

        try:
            return (
                int(start_ms),
                int(end_ms),
            )
        except (TypeError, ValueError):
            return None


    def transcript_segment_for_key(
        self,
        segment_key: tuple[int, int],
    ) -> dict | None:

        start_ms, end_ms = segment_key

        return next(
            (
                segment
                for segment in self.source_transcript_segments
                if (
                    segment["start_ms"] == start_ms
                    and segment["end_ms"] == end_ms
                )
            ),
            None,
        )


    def edit_selected_transcript_segment(self):

        item = self.transcript_list.currentItem()

        if item is None:
            return

        self.edit_transcript_item(
            item
        )


    def edit_transcript_item(
        self,
        item: QListWidgetItem,
    ):

        start_ms = item.data(
            Qt.ItemDataRole.UserRole
        )

        end_ms = item.data(
            Qt.ItemDataRole.UserRole + 1
        )

        try:
            segment_key = (
                int(start_ms),
                int(end_ms),
            )
        except (TypeError, ValueError):
            return

        segment = self.transcript_segment_for_key(
            segment_key
        )

        if segment is None:
            return

        current_text = self.transcript_corrections.get(
            segment_key,
            segment.get(
                "text",
                "",
            ),
        )

        corrected_text, accepted = QInputDialog.getMultiLineText(
            self,
            "Correct Transcript",
            (
                "Edit exactly what should appear in the captions.\n"
                "Timing will stay attached to this spoken segment."
            ),
            current_text,
        )

        if not accepted:
            return

        corrected_text = " ".join(
            corrected_text.split()
        ).strip()

        if not corrected_text:
            return

        original_text = str(
            segment.get(
                "text",
                "",
            )
            or ""
        ).strip()

        if corrected_text == original_text:
            self.transcript_corrections.pop(
                segment_key,
                None,
            )
        else:
            self.transcript_corrections[
                segment_key
            ] = corrected_text

        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()


    def reset_selected_transcript_text(self):

        segment_key = (
            self.selected_transcript_segment_key()
        )

        if segment_key is None:
            return

        self.transcript_corrections.pop(
            segment_key,
            None,
        )

        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()


    def refresh_transcript_timeline_overlays(self):

        self.timeline.set_manual_cut_ranges(
            list(
                self.manual_cut_segments
            )
        )

        self.timeline.set_edited_transcript_ranges(
            list(
                self.transcript_corrections.keys()
            )
        )


    def load_render_timeline_overlays(self):
        """
        Load scene and punch-in decisions from the most recent render and
        display them on the source timeline. Scene positions are exact for
        the selected source range. Motion positions are mapped back using
        the current selection offset.
        """

        selection_offset_ms = int(
            self.start_ms
        )

        scene_positions: list[int] = []
        motion_ranges: list[
            tuple[int, int]
        ] = []
        fx_ranges: list[
            tuple[int, int]
        ] = []
        graphic_ranges: list[
            tuple[int, int]
        ] = []
        caption_impact_ranges: list[
            tuple[int, int]
        ] = []

        scene_plan = (
            ROOT
            / "output"
            / "scene_plan.json"
        )

        if scene_plan.exists():

            try:
                data = json.loads(
                    scene_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            raw_cuts = data.get(
                "cuts",
                [],
            )

            if isinstance(
                raw_cuts,
                list,
            ):

                for value in raw_cuts:

                    try:
                        seconds = float(
                            value
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    scene_positions.append(
                        selection_offset_ms
                        + int(
                            round(
                                seconds
                                * 1000
                            )
                        )
                    )

        motion_plan = (
            ROOT
            / "output"
            / "smart_motion_plan.json"
        )

        if motion_plan.exists():

            try:
                data = json.loads(
                    motion_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            events = data.get(
                "events",
                [],
            )

            if isinstance(
                events,
                list,
            ):

                for event in events:

                    if not isinstance(
                        event,
                        dict,
                    ):
                        continue

                    try:
                        start = float(
                            event.get(
                                "start",
                                0.0,
                            )
                        )

                        end = float(
                            event.get(
                                "end",
                                start,
                            )
                        )

                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    if end <= start:
                        continue

                    motion_ranges.append(
                        (
                            selection_offset_ms
                            + int(
                                round(
                                    start
                                    * 1000
                                )
                            ),
                            selection_offset_ms
                            + int(
                                round(
                                    end
                                    * 1000
                                )
                            ),
                        )
                    )

        visual_fx_plan = (
            ROOT
            / "output"
            / "visual_fx_plan.json"
        )

        if visual_fx_plan.exists():

            try:
                data = json.loads(
                    visual_fx_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            events = data.get(
                "events",
                [],
            )

            if isinstance(
                events,
                list,
            ):

                for event in events:

                    if not isinstance(
                        event,
                        dict,
                    ):
                        continue

                    try:
                        start = float(
                            event.get(
                                "start",
                                0.0,
                            )
                        )
                        end = float(
                            event.get(
                                "end",
                                start,
                            )
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    if end <= start:
                        continue

                    mapped_range = (
                        selection_offset_ms
                        + int(
                            round(
                                start
                                * 1000
                            )
                        ),
                        selection_offset_ms
                        + int(
                            round(
                                end
                                * 1000
                            )
                        ),
                    )

                    if event.get(
                        "type"
                    ) == "graphic":
                        graphic_ranges.append(
                            mapped_range
                        )
                    else:
                        fx_ranges.append(
                            mapped_range
                        )

        visual_edit_plan = (
            ROOT
            / "output"
            / "visual_edit_plan.json"
        )

        if visual_edit_plan.exists():

            try:
                data = json.loads(
                    visual_edit_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            events = data.get(
                "events",
                [],
            )

            if isinstance(
                events,
                list,
            ):

                for event in events:

                    if not isinstance(
                        event,
                        dict,
                    ):
                        continue

                    if event.get(
                        "type"
                    ) != "caption_emphasis":
                        continue

                    try:
                        start = float(
                            event.get(
                                "start",
                                0.0,
                            )
                        )
                        end = float(
                            event.get(
                                "end",
                                start,
                            )
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    if end <= start:
                        continue

                    caption_impact_ranges.append(
                        (
                            selection_offset_ms
                            + int(
                                round(
                                    start
                                    * 1000
                                )
                            ),
                            selection_offset_ms
                            + int(
                                round(
                                    end
                                    * 1000
                                )
                            ),
                        )
                    )

        self.timeline.set_scene_cut_positions(
            scene_positions
        )

        self.timeline.set_motion_ranges(
            motion_ranges
        )

        self.timeline.set_fx_ranges(
            fx_ranges
        )

        self.timeline.set_graphic_ranges(
            graphic_ranges
        )

        self.timeline.set_caption_impact_ranges(
            caption_impact_ranges
        )

        self.refresh_transcript_timeline_overlays()


    def save_transcript_corrections(self):

        output_path = (
            ROOT
            / "output"
            / "transcript_corrections.json"
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        corrections = []

        for (
            start_ms,
            end_ms,
        ), corrected_text in sorted(
            self.transcript_corrections.items()
        ):

            segment = self.transcript_segment_for_key(
                (
                    start_ms,
                    end_ms,
                )
            )

            corrections.append(
                {
                    "start": start_ms / 1000,
                    "end": end_ms / 1000,
                    "original_text": (
                        segment.get(
                            "text",
                            "",
                        )
                        if segment
                        else ""
                    ),
                    "corrected_text": corrected_text,
                }
            )

        payload = {
            "source_video": (
                str(self.video_path)
                if self.video_path
                else ""
            ),
            "selection_start": self.start_ms / 1000,
            "selection_end": self.end_ms / 1000,
            "correction_count": len(
                corrections
            ),
            "corrections": corrections,
        }

        try:
            output_path.write_text(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass


    def cut_selected_transcript_segment(self):

        segment_key = (
            self.selected_transcript_segment_key()
        )

        if segment_key is None:
            return

        self.manual_cut_segments.add(
            segment_key
        )

        self.save_manual_edit_plan()
        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()
        self.clear_visual_plan_display()
        self.refresh_transcript_timeline_overlays()


    def restore_selected_transcript_segment(self):

        segment_key = (
            self.selected_transcript_segment_key()
        )

        if segment_key is None:
            return

        self.manual_cut_segments.discard(
            segment_key
        )

        self.save_manual_edit_plan()
        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()
        self.clear_visual_plan_display()
        self.refresh_transcript_timeline_overlays()


    def toggle_transcript_item_cut(
        self,
        item: QListWidgetItem,
    ):

        start_ms = item.data(
            Qt.ItemDataRole.UserRole
        )

        end_ms = item.data(
            Qt.ItemDataRole.UserRole + 1
        )

        try:
            segment_key = (
                int(start_ms),
                int(end_ms),
            )
        except (TypeError, ValueError):
            return

        if segment_key in self.manual_cut_segments:

            self.manual_cut_segments.discard(
                segment_key
            )

        else:

            self.manual_cut_segments.add(
                segment_key
            )

        self.save_manual_edit_plan()
        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()
        self.clear_visual_plan_display()
        self.refresh_transcript_timeline_overlays()


    def save_manual_edit_plan(self):

        output_path = (
            ROOT
            / "output"
            / "manual_edit_plan.json"
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        cuts = []

        for start_ms, end_ms in sorted(
            self.manual_cut_segments
        ):

            matching_segment = next(
                (
                    segment
                    for segment
                    in self.source_transcript_segments
                    if (
                        segment["start_ms"] == start_ms
                        and segment["end_ms"] == end_ms
                    )
                ),
                None,
            )

            cuts.append(
                {
                    "start": start_ms / 1000,
                    "end": end_ms / 1000,
                    "duration": (
                        end_ms - start_ms
                    ) / 1000,
                    "text": (
                        matching_segment.get("text", "")
                        if matching_segment
                        else ""
                    ),
                    "source": "manual_transcript_cut",
                }
            )

        payload = {
            "source_video": (
                str(self.video_path)
                if self.video_path
                else ""
            ),
            "selection_start": self.start_ms / 1000,
            "selection_end": self.end_ms / 1000,
            "cut_count": len(cuts),
            "time_removed_seconds": round(
                sum(
                    cut["duration"]
                    for cut in cuts
                ),
                3,
            ),
            "cuts": cuts,
        }

        try:

            output_path.write_text(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                ) + "\n",
                encoding="utf-8",
            )

        except OSError:
            pass


