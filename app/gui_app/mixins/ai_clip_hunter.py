"""
AIClipHunterMixin: the "Find Best Clips" flow. Kicks off transcription
(if needed) then analyze.py as a subprocess to score candidate clip
windows, renders the resulting candidate cards in the right panel, and
handles selecting one into the timeline.
"""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QProcess

from ..constants import ROOT
from ..helpers import (
    format_time,
    is_generic_editor_text,
    timestamp_to_seconds,
    transcript_excerpt,
)


class AIClipHunterMixin:

    def reset_clip_cards(self):

        if not hasattr(
            self,
            "clip_cards",
        ):
            return

        for index, card in enumerate(
            self.clip_cards
        ):

            card.setText(
                f"AI PICK #{index + 1}\nRun Find Best Clips to populate"
            )

            card.setEnabled(
                False
            )

            card.setVisible(
                False
            )

            card.setProperty(
                "selected",
                False,
            )

            card.style().unpolish(
                card
            )

            card.style().polish(
                card
            )


    def refresh_clip_card_selection(
        self,
        selected_index: int | None,
    ):

        for index, card in enumerate(
            self.clip_cards
        ):

            is_selected = (
                selected_index is not None
                and index == selected_index
            )

            card.setProperty(
                "selected",
                is_selected,
            )

            card.style().unpolish(
                card
            )

            card.style().polish(
                card
            )

            card.update()


    def populate_clip_cards(self):

        for index, card in enumerate(
            self.clip_cards
        ):

            if index >= len(
                self.ai_candidates
            ):

                card.setVisible(
                    False
                )

                continue

            candidate = (
                self.ai_candidates[index]
            )

            rank = int(
                candidate.get(
                    "rank",
                    index + 1,
                )
            )

            start_ms = int(
                candidate.get(
                    "start_ms",
                    0,
                )
            )

            end_ms = int(
                candidate.get(
                    "end_ms",
                    0,
                )
            )

            score = int(
                candidate.get(
                    "score",
                    0,
                )
            )

            hook = str(
                candidate.get(
                    "hook",
                    "",
                )
                or ""
            ).strip()

            reason = str(
                candidate.get(
                    "reason",
                    "",
                )
                or ""
            ).strip()

            description = str(
                candidate.get(
                    "description",
                    "",
                )
                or ""
            ).strip()

            if is_generic_editor_text(
                hook
            ):
                hook = transcript_excerpt(
                    description,
                    max_words=7,
                )

            # Keep cards compact enough to feel like an editor,
            # not a wall of AI-generated text.
            quote = transcript_excerpt(
                description,
                max_words=12,
            )

            grounded_reason = reason
            if is_generic_editor_text(
                grounded_reason
            ):
                grounded_reason = (
                    f'Anchored by "{quote}".'
                    if quote
                    else "Transcript-grounded candidate."
                )

            title = hook or quote or "Transcript moment"

            if len(title) > 54:

                title = title[:51].rstrip() + "..."

            if len(grounded_reason) > 92:
                grounded_reason = grounded_reason[:89].rstrip() + "..."

            duration = max(
                0,
                end_ms
                - start_ms,
            ) / 1000

            headline = (
                f"\"{quote or title}\"\n"
                f"{grounded_reason}"
            )

            card.setText(
                f"AI PICK #{rank}   •   {score}/100\n"
                f"{format_time(start_ms)} → {format_time(end_ms)}\n"
                f"{headline}"
            )

            card.setToolTip(
                (
                    f"AI Pick #{rank}\n"
                    f"Score: {score}/100\n"
                    f"Range: "
                    f"{start_ms / 1000:.2f}s → "
                    f"{end_ms / 1000:.2f}s\n\n"
                    f"Hook: {hook or '—'}\n\n"
                    f"Description: {description or '—'}\n\n"
                    f"Why selected: {reason or '—'}"
                )
            )

            card.setEnabled(
                True
            )

            card.setVisible(
                True
            )

        self.refresh_clip_card_selection(
            0
            if self.ai_candidates
            else None
        )


    def select_ai_card(
        self,
        card_index: int,
    ):

        if (
            card_index < 0
            or card_index >= len(
                self.ai_candidates
            )
        ):
            return

        candidate = (
            self.ai_candidates[
                card_index
            ]
        )

        self.select_ai_suggestion(
            int(
                candidate["rank"]
            ),
            int(
                candidate["start_ms"]
            ),
            int(
                candidate["end_ms"]
            ),
            int(
                candidate["score"]
            ),
        )


    def select_ai_suggestion(
        self,
        rank: int,
        start_ms: int,
        end_ms: int,
        score: int,
    ):

        self.start_ms = start_ms
        self.end_ms = end_ms

        self.timeline.set_selected_suggestion(
            rank - 1
        )

        self.timeline.set_selection_range(
            start_ms,
            end_ms,
        )

        if (
            self.timeline.maximum() > 120000
            and end_ms
            - start_ms
            < self.timeline.maximum()
            * 0.25
        ):
            self.timeline.fit_selection()
        else:
            self.reveal_timeline_range(
                start_ms,
                end_ms,
            )

        self.refresh_clip_card_selection(
            rank - 1
        )

        self.update_selection_label()
        self.clear_visual_plan_display()
        self.selected_sfx_clip_id = None
        self.selected_emoji_clip_id = None
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

        self.seek_video(
            start_ms
        )

        self.suggestions_label.setText(
            f"Selected AI clip #{rank}: "
            f"{format_time(start_ms)}–{format_time(end_ms)} "
            f"({score}/100). "
            "Click another purple range to switch."
        )

        self.render_log.append(
            f"Selected AI clip #{rank}: "
            f"{start_ms / 1000:.2f}s → "
            f"{end_ms / 1000:.2f}s "
            f"({score}/100)"
        )


    def append_analysis_log(
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


    def read_analysis_output(self):

        data = (
            self.analysis_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_analysis_log(
            data
        )


    def read_analysis_error(self):

        data = (
            self.analysis_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_analysis_log(
            data
        )


    def find_best_clips(self):

        if not self.video_path:
            return

        if (
            self.analysis_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        self.render_log.clear()

        self.render_log.append(
            "Finding strong Short candidates..."
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            f"Source: {self.video_path.name}"
        )

        transcription_quality = self.current_transcription_quality()

        self.render_log.append(
            f"Transcription quality: {transcription_quality}"
        )

        self.render_log.append(
            "Stage 1/2: Transcribing the full source video..."
        )

        self.find_clips_button.setEnabled(
            False
        )

        self.find_clips_button.setText(
            "Analyzing..."
        )

        self.generate_button.setEnabled(
            False
        )

        self.timeline.clear_suggestions()

        self.suggestions_label.setText(
            "Analyzing the source video..."
        )

        self.pending_find_best_after_preload = False

        if self.cached_transcript_ready_for_current_source():
            self.load_source_transcript()
            self.render_log.append(
                "Stage 1/2: Reusing the prepared transcript."
            )
            self.start_clip_analyzer()
            return

        if (
            self.transcript_preload_process.state()
            != QProcess.ProcessState.NotRunning
            and self.transcript_preload_source
            == str(self.video_path)
            and self.transcript_preload_quality
            == transcription_quality
        ):
            self.pending_find_best_after_preload = True
            self.render_log.append(
                "Stage 1/2: Waiting for background transcript preload..."
            )
            self.transcript_status_label.setText(
                "TRANSCRIBING..."
            )
            return

        subtitles_script = (
            ROOT
            / "app"
            / "subtitles.py"
        )

        self.analysis_stage = (
            "transcribe"
        )

        self.analysis_process.start(
            sys.executable,
            [
                str(subtitles_script),
                "--quality",
                transcription_quality,
                str(self.video_path),
            ],
        )


    def start_clip_analyzer(self):

        if not self.video_path:
            return

        analyzer_script = (
            ROOT
            / "app"
            / "analyze.py"
        )

        transcript_path = (
            ROOT
            / "output"
            / "subtitles.json"
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "Stage 2/2: Ranking the strongest clip windows with local AI..."
        )

        self.analysis_stage = (
            "analyze"
        )

        source_duration_ms = max(
            0,
            self.player.duration(),
        )

        target_clip_count = (
            6
            if source_duration_ms >= 10 * 60 * 1000
            else 3
        )

        self.render_log.append(
            f"Requesting {target_clip_count} clip candidates "
            f"for a {source_duration_ms / 60000:.1f}-minute source."
        )

        self.analysis_process.start(
            sys.executable,
            [
                str(analyzer_script),

                "--video",
                str(self.video_path),

                "--transcript",
                str(transcript_path),

                "--clip-discovery-only",

                "--max-clips",
                str(target_clip_count),
            ],
        )


    def load_clip_suggestions(self):

        analysis_path = (
            ROOT
            / "output"
            / "analysis.json"
        )

        if not analysis_path.exists():

            raise FileNotFoundError(
                f"Analysis file not found: {analysis_path}"
            )

        with analysis_path.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        analysis = data.get(
            "analysis",
            data,
        )

        self.load_source_transcript()

        candidates = analysis.get(
            "candidate_clips",
            [],
        )

        suggestions = []
        label_parts = []

        self.ai_candidates = []

        for rank, candidate in enumerate(
            candidates[:6],
            start=1,
        ):

            start_seconds = timestamp_to_seconds(
                candidate.get(
                    "start_timestamp",
                    "",
                )
            )

            end_seconds = timestamp_to_seconds(
                candidate.get(
                    "end_timestamp",
                    "",
                )
            )

            if (
                start_seconds is None
                or end_seconds is None
                or end_seconds <= start_seconds
            ):
                continue

            score = int(
                candidate.get(
                    "score",
                    0,
                )
                or 0
            )

            start_ms = int(
                round(
                    start_seconds * 1000
                )
            )

            end_ms = int(
                round(
                    end_seconds * 1000
                )
            )

            suggestions.append(
                (
                    start_ms,
                    end_ms,
                    score,
                )
            )

            self.ai_candidates.append(
                {
                    "rank": rank,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "score": score,
                    "hook": str(
                        candidate.get(
                            "hook",
                            "",
                        )
                        or ""
                    ).strip(),
                    "description": str(
                        candidate.get(
                            "description",
                            "",
                        )
                        or ""
                    ).strip(),
                    "reason": str(
                        candidate.get(
                            "reason",
                            "",
                        )
                        or ""
                    ).strip(),
                }
            )

            label_parts.append(
                f"#{rank} "
                f"{format_time(start_ms)}–{format_time(end_ms)} "
                f"({score}/100)"
            )

        if not suggestions:

            self.timeline.clear_suggestions()

            self.ai_candidates = []

            self.reset_clip_cards()

            self.suggestions_label.setText(
                "AI analysis completed, but no usable clip ranges were returned."
            )

            return

        self.timeline.set_suggestions(
            suggestions
        )

        self.timeline.set_selected_suggestion(
            0
        )

        self.populate_clip_cards()

        self.suggestions_label.setText(
            "AI suggestions — click a purple range or a card: "
            + "   •   ".join(label_parts)
        )

        # Automatically select the highest-ranked suggestion while still
        # showing all candidate ranges on the timeline.
        best_start, best_end, best_score = suggestions[0]

        self.start_ms = best_start
        self.end_ms = best_end

        self.timeline.set_selection_range(
            best_start,
            best_end,
        )

        if (
            self.timeline.maximum() > 120000
            and best_end
            - best_start
            < self.timeline.maximum()
            * 0.25
        ):
            self.timeline.fit_selection()

        self.update_selection_label()
        self.selected_sfx_clip_id = None
        self.selected_emoji_clip_id = None
        self.refresh_editor_asset_timeline()

        self.seek_video(
            best_start
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            f"✓ Found {len(suggestions)} strong clip candidates."
        )

        self.render_log.append(
            "The highest-ranked range has been selected automatically. "
            "Click any purple range to switch candidates."
        )


    def analysis_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        if exit_code != 0:

            self.analysis_stage = None

            self.find_clips_button.setEnabled(
                self.video_path is not None
            )

            self.find_clips_button.setText(
                "✦ Find Best Clips"
            )

            self.generate_button.setEnabled(
                self.video_path is not None
            )

            self.suggestions_label.setText(
                "AI clip discovery failed. See the render log below."
            )

            self.render_log.append(
                ""
            )

            self.render_log.append(
                f"✕ CLIP DISCOVERY FAILED (exit code {exit_code})"
            )

            return

        if self.analysis_stage == "transcribe":

            self.start_clip_analyzer()

            return

        if self.analysis_stage == "analyze":

            self.analysis_stage = None

            try:

                self.load_clip_suggestions()

            except Exception as exc:

                self.render_log.append(
                    ""
                )

                self.render_log.append(
                    f"Could not display clip suggestions: {exc}"
                )

            self.find_clips_button.setEnabled(
                self.video_path is not None
            )

            self.find_clips_button.setText(
                "✦ Refresh Clips"
            )

            self.generate_button.setEnabled(
                self.video_path is not None
            )

