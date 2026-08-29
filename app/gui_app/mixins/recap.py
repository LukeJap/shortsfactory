"""
RecapMixin: B8's GUI/editor entry point for AI Recap Mode -- see
SHORTSFACTORY_AI_RECAP_SHARED_CONTRACT.md and
SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR.md's B8. Wires the
recap_media package (B1-B7) into the app: a toggleable recap panel,
Track A status, sequence generation, Orpheus voiceover generation, and
per-segment VOICEOVER clip management (enable/disable/delete/volume/
regenerate) sharing the exact same editor_asset_plan.json mechanism
SFX/AI_VISUAL/EMOJI already use -- including preserve_manual=True on
replace_kind_clips(), which is what makes "manual edits are authoritative
and must survive unrelated regeneration" (the shared contract's own
words) true here for free, not something reimplemented per-kind.

Deliberately NOT wired into the existing SFX/EMOJI/AI_VISUAL selection
state machine (editor_asset_clip_selected() et al. in editor_assets.py,
whose mutual-exclusion resets are scattered across five files) --
VOICEOVER clips appear on the timeline (a real, dedicated lane; see
timeline_widget.py) but their interactive editing lives entirely in this
mixin's own recap panel list widget, so this doesn't need to touch any
of those existing cross-file reset call sites.

Not built in this pass (left for a follow-up): in-GUI narration segment
TEXT editing, source-shot/original-dialogue inspection panels, a recap
style selector, and narration audio preview synced to the video player.
Regenerating one segment updates only that segment's own duration --
it does not re-cascade every later segment's cumulative position; run
"Generate Voiceover" again for a fully repacked timeline.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

from editor_asset_plan import clips_of_kind, replace_kind_clips, upsert_clip
from pipeline_paths import (
    EPISODE_IDENTITY_PATH,
    RECAP_SCRIPT_PATH,
    VERIFIED_STORY_MAP_PATH,
)
from recap_media.loader import RecapInputError, load_recap_inputs
from recap_media.orpheus_provider import DEFAULT_VOICE, KNOWN_VOICES, OrpheusProvider
from recap_media.sequence import (
    assemble_sequence,
    interweave_original_dialogue,
    voiceover_timing_by_segment,
    write_recap_sequence,
)
from recap_media.voiceover import load_voiceover_durations, synthesize_segment, synthesize_segments

from ..settings_keys import RECAP_TARGET_DURATION_SECONDS, RECAP_VOICE

TRACK_A_INPUT_PATHS = (
    EPISODE_IDENTITY_PATH,
    VERIFIED_STORY_MAP_PATH,
    RECAP_SCRIPT_PATH,
)


def _recap_source_filename(episode_identity: dict) -> str | None:
    query = episode_identity.get("query")
    if not isinstance(query, dict):
        return None
    source_filename = query.get("source_filename")
    if not isinstance(source_filename, str) or not source_filename.strip():
        return None
    return source_filename.strip()


class RecapMixin:

    def append_recap_log(self, message: str):

        if hasattr(self, "recap_log"):
            self.recap_log.append(message)

    def toggle_recap_panel(self):

        # self.recap_button.isChecked() (a local widget property Qt
        # already toggled before this handler runs, since the button is
        # setCheckable(True)) rather than self.recap_frame.isVisible() --
        # isVisible() reflects the *whole ancestor chain*, so it reports
        # False for every descendant of a QMainWindow that hasn't been
        # shown yet regardless of this frame's own state, which made the
        # very first toggle a no-op until the window had been shown once.
        visible = self.recap_button.isChecked()
        self.recap_frame.setVisible(visible)
        if visible:
            self.refresh_recap_status()

    def refresh_recap_status(self):

        missing = [path.name for path in TRACK_A_INPUT_PATHS if not path.exists()]

        if missing:
            self.recap_status_label.setText(
                "Recap Intelligence: not found (" + ", ".join(missing) + ") -- run Track A first."
            )
            self.recap_status_label.setProperty("state", "offline")
            self.generate_recap_sequence_button.setEnabled(False)
        else:
            self.recap_status_label.setText("Recap Intelligence: ready.")
            self.recap_status_label.setProperty("state", "ready")
            self.generate_recap_sequence_button.setEnabled(True)

        style = self.recap_status_label.style()
        style.unpolish(self.recap_status_label)
        style.polish(self.recap_status_label)

        self.generate_recap_voiceover_button.setEnabled(
            bool(getattr(self, "recap_sequence", None))
        )
        self.refresh_recap_voiceover_list()

    def refresh_recap_voices(self):

        try:
            voices = OrpheusProvider().list_voices()
        except Exception:
            voices = list(KNOWN_VOICES)

        current = self.recap_voice_combo.currentText() or self.recap_voice
        self.recap_voice_combo.blockSignals(True)
        self.recap_voice_combo.clear()
        self.recap_voice_combo.addItems(voices)
        if current in voices:
            self.recap_voice_combo.setCurrentText(current)
        self.recap_voice_combo.blockSignals(False)

    def recap_voice_changed(self, value: str):

        self.recap_voice = str(value or DEFAULT_VOICE)
        self.settings.setValue(RECAP_VOICE, self.recap_voice)

    def recap_target_duration_changed(self, value: int):

        self.recap_target_duration_seconds = max(10, int(value))
        self.settings.setValue(
            RECAP_TARGET_DURATION_SECONDS, self.recap_target_duration_seconds
        )

    def generate_recap_sequence(self):

        try:
            inputs = load_recap_inputs()
        except RecapInputError as exc:
            self.append_recap_log(f"ERROR: {exc}")
            QMessageBox.warning(self, "Create AI Recap", str(exc))
            return

        narration_durations = load_voiceover_durations()
        sequence = assemble_sequence(
            inputs.recap_script,
            narration_durations,
            verified_story_map=inputs.verified_story_map,
            source_video=_recap_source_filename(inputs.episode_identity),
        )
        sequence = interweave_original_dialogue(
            sequence,
            inputs.recap_script,
            verified_story_map=inputs.verified_story_map,
            source_video=_recap_source_filename(inputs.episode_identity),
        )
        write_recap_sequence(sequence)

        self.recap_sequence = sequence

        # Existing synthesized narration must move after newly-added source
        # audio windows, so editor, captions, and final render share one
        # authoritative timeline. Manual/locked clips remain protected by
        # replace_kind_clips() below.
        if narration_durations:
            new_clips = self._rebuild_voiceover_clips(
                inputs, narration_durations, sequence
            )
            self.editor_asset_plan = replace_kind_clips(
                self.editor_asset_plan, "VOICEOVER", new_clips
            )
            self.save_editor_asset_plan_state()
            self.refresh_editor_asset_timeline()
            self.refresh_recap_voiceover_list()

        segment_count = len(sequence["segments"])
        total_duration = sequence["total_duration_seconds"]
        target_duration = sequence.get("target_duration_seconds")

        self.append_recap_log(
            f"Recap sequence assembled: {segment_count} segment(s), "
            f"{total_duration:.1f}s total"
            + (f" (script targets {target_duration:.0f}s)." if target_duration else ".")
        )
        for warning in sequence.get("sequence_warnings", []):
            self.append_recap_log(f"WARNING: {warning}")

        self.generate_recap_voiceover_button.setEnabled(True)

    def _rebuild_voiceover_clips(
        self,
        inputs,
        durations: dict,
        sequence: dict | None = None,
    ) -> list[dict]:

        cursor = 0.0
        clips = []
        timings = voiceover_timing_by_segment(sequence) if sequence else {}

        for segment in inputs.recap_script["segments"]:
            if (
                segment.get("block_type") == "source_moment"
                or segment.get("presentation_hint") == "visual_only"
            ):
                continue

            segment_id = segment["segment_id"]
            duration = max(0.01, float(durations.get(segment_id, 0.0)) or 0.01)
            timing = timings.get(segment_id)
            start = float(timing["start"]) if timing else cursor
            end = float(timing["end"]) if timing else cursor + duration

            clips.append(
                {
                    "id": segment_id,
                    "kind": "VOICEOVER",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "label": str(segment.get("text", ""))[:60],
                    "active": segment_id in durations,
                    "volume": 1.0,
                    "manual_override": False,
                    "dialogue_pauses": timing.get("dialogue_pauses", []) if timing else [],
                }
            )
            cursor = end

        return clips

    def generate_recap_voiceover(self):

        if not getattr(self, "recap_sequence", None):
            QMessageBox.information(
                self, "Create AI Recap", "Generate the recap sequence first."
            )
            return

        # Same context-sync step generate_sfx()/generate_visual_assets()
        # already do before writing clips -- without it, the plan's
        # stored source_video/selection stays whatever it was before, so
        # editor_asset_context_matches_current_selection() (which
        # visible_editor_asset_clips() gates on) would never match and
        # the new VOICEOVER clips would silently never appear on the
        # timeline.
        self.ensure_current_editor_asset_context(clear_on_change=True)

        try:
            inputs = load_recap_inputs()
        except RecapInputError as exc:
            self.append_recap_log(f"ERROR: {exc}")
            return

        provider = OrpheusProvider()
        readiness = provider.readiness()
        if readiness.get("state") != "online":
            message = (
                "Orpheus-FastAPI isn't reachable "
                f"({readiness.get('message', 'unknown error')}). "
                "Start the local Orpheus server and try again."
            )
            self.append_recap_log(f"ERROR: {message}")
            QMessageBox.warning(self, "Create AI Recap", message)
            return

        self.generate_recap_voiceover_button.setEnabled(False)
        self.append_recap_log("Generating narration voiceover...")
        QCoreApplication.processEvents()

        results = synthesize_segments(
            provider,
            inputs.recap_script["segments"],
            voice=self.recap_voice,
        )

        durations = {
            result.segment_id: result.duration_seconds
            for result in results
            if result.error is None
        }
        errors = [f"{result.segment_id}: {result.error}" for result in results if result.error]

        # Reassemble from the measured WAV durations before creating editor
        # clips. Insert windows are additive, so every following VO starts
        # after the source-audio moments already placed in the sequence.
        sequence = assemble_sequence(
            inputs.recap_script,
            durations,
            verified_story_map=inputs.verified_story_map,
            source_video=_recap_source_filename(inputs.episode_identity),
        )
        sequence = interweave_original_dialogue(
            sequence,
            inputs.recap_script,
            verified_story_map=inputs.verified_story_map,
            source_video=_recap_source_filename(inputs.episode_identity),
        )
        write_recap_sequence(sequence)
        self.recap_sequence = sequence

        new_clips = self._rebuild_voiceover_clips(inputs, durations, sequence)
        self.editor_asset_plan = replace_kind_clips(
            self.editor_asset_plan, "VOICEOVER", new_clips
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()
        self.refresh_recap_voiceover_list()

        self.generate_recap_voiceover_button.setEnabled(True)

        ready_count = sum(1 for clip in new_clips if clip["active"])
        self.append_recap_log(
            f"Voiceover ready: {ready_count}/{len(new_clips)} segment(s)."
        )
        for error in errors:
            self.append_recap_log(f"WARNING: {error}")

    def refresh_recap_voiceover_list(self):

        if not hasattr(self, "recap_voiceover_list"):
            return

        self.recap_voiceover_list.clear()
        for clip in clips_of_kind(self.editor_asset_plan, "VOICEOVER"):
            if clip.get("deleted"):
                continue
            state = "" if clip.get("active", True) else " [disabled]"
            label = clip.get("label", clip.get("id", "?"))
            self.recap_voiceover_list.addItem(f"{clip.get('id', '')}{state}: {label}")

        self.update_recap_voiceover_context()

    def selected_recap_voiceover_clip(self) -> dict | None:

        if not hasattr(self, "recap_voiceover_list"):
            return None

        row = self.recap_voiceover_list.currentRow()
        clips = [
            clip
            for clip in clips_of_kind(self.editor_asset_plan, "VOICEOVER")
            if not clip.get("deleted")
        ]
        if 0 <= row < len(clips):
            return clips[row]
        return None

    def update_recap_voiceover_context(self):

        clip = self.selected_recap_voiceover_clip()

        if clip is None:
            self.recap_voiceover_toggle_button.setEnabled(False)
            self.recap_voiceover_delete_button.setEnabled(False)
            self.recap_voiceover_regenerate_button.setEnabled(False)
            self.recap_voiceover_volume_slider.setEnabled(False)
            self.recap_voiceover_toggle_button.setText("Disable")
            return

        self.recap_voiceover_toggle_button.setEnabled(True)
        self.recap_voiceover_delete_button.setEnabled(True)
        self.recap_voiceover_regenerate_button.setEnabled(True)
        self.recap_voiceover_toggle_button.setText(
            "Enable" if clip.get("active", True) is False else "Disable"
        )

        try:
            volume = float(clip.get("volume", 1.0) or 1.0)
        except (TypeError, ValueError):
            volume = 1.0
        self.recap_voiceover_volume_slider.blockSignals(True)
        self.recap_voiceover_volume_slider.setValue(
            int(round(max(0.0, min(1.0, volume)) * 100))
        )
        self.recap_voiceover_volume_slider.blockSignals(False)
        self.recap_voiceover_volume_slider.setEnabled(True)

    def toggle_selected_recap_voiceover_clip(self):

        clip = self.selected_recap_voiceover_clip()
        if clip is None:
            return

        clip["active"] = not bool(clip.get("active", True))
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(self.editor_asset_plan, clip)
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()
        self.refresh_recap_voiceover_list()

    def delete_selected_recap_voiceover_clip(self):

        clip = self.selected_recap_voiceover_clip()
        if clip is None:
            return

        clip["active"] = False
        clip["deleted"] = True
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(self.editor_asset_plan, clip)
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()
        self.refresh_recap_voiceover_list()

    def recap_voiceover_volume_changed(self, value: int):

        clip = self.selected_recap_voiceover_clip()
        if clip is None:
            return

        clip["volume"] = round(max(0, min(100, int(value))) / 100.0, 2)
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(self.editor_asset_plan, clip)
        self.save_editor_asset_plan_state()

    def regenerate_selected_recap_voiceover_clip(self):

        clip = self.selected_recap_voiceover_clip()
        if clip is None:
            return

        try:
            inputs = load_recap_inputs()
        except RecapInputError as exc:
            self.append_recap_log(f"ERROR: {exc}")
            return

        segment = next(
            (
                segment
                for segment in inputs.recap_script["segments"]
                if segment["segment_id"] == clip["id"]
            ),
            None,
        )
        if segment is None:
            self.append_recap_log(
                f"WARNING: segment {clip['id']} no longer exists in recap_script.json"
            )
            return

        self.recap_voiceover_regenerate_button.setEnabled(False)
        self.append_recap_log(f"Regenerating {clip['id']}...")
        QCoreApplication.processEvents()

        provider = OrpheusProvider()
        result = synthesize_segment(
            provider,
            clip["id"],
            segment["text"],
            voice=self.recap_voice,
            force=True,
        )

        self.recap_voiceover_regenerate_button.setEnabled(True)

        if result.error:
            self.append_recap_log(f"ERROR regenerating {clip['id']}: {result.error}")
            QMessageBox.warning(self, "Create AI Recap", result.error)
            return

        start = float(clip.get("start", 0.0) or 0.0)
        clip["end"] = round(start + result.duration_seconds, 3)
        clip["active"] = True
        self.editor_asset_plan = upsert_clip(self.editor_asset_plan, clip)
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()
        self.refresh_recap_voiceover_list()

        self.append_recap_log(
            f"Regenerated {clip['id']} ({result.duration_seconds:.2f}s). "
            "Run Generate Voiceover again to fully repack later segments' timing."
        )
