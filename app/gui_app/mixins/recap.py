"""
RecapMixin: B8's GUI/editor entry point for AI Recap Mode -- see
SHORTSFACTORY_AI_RECAP_SHARED_CONTRACT.md and
SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR.md's B8. Wires the
recap_media package (B1-B7) into the app: a toggleable recap panel,
Track A status, sequence generation, Orpheus voiceover generation, and
per-segment VOICEOVER clip management (enable/disable/delete/volume/
regenerate) sharing the exact same editor_asset_plan.json mechanism
SFX/EMOJI already use -- including preserve_manual=True on
replace_kind_clips(), which is what makes "manual edits are authoritative
and must survive unrelated regeneration" (the shared contract's own
words) true here for free, not something reimplemented per-kind.

Deliberately NOT wired into the existing SFX/EMOJI selection
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

import copy
import json
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from editor_asset_plan import (
    clips_of_kind,
    load_editor_asset_plan,
    replace_kind_clips,
    save_editor_asset_plan,
    upsert_clip,
)
from recap_media.artifacts import (
    RecapArtifactContext,
    resolve_recap_artifact_context,
    resolve_recap_editor_plan_paths,
)
from recap_media.audio_mix import (
    DEFAULT_NARRATION_GAIN_DB,
    build_duck_plan,
    load_duck_plan,
    write_duck_plan,
)
from recap_media.caption_alignment import (
    build_narration_captions,
    write_narration_captions,
    write_narration_captions_ass_file,
)
from recap_media.combined_captions import (
    build_combined_recap_caption_plan,
    load_combined_recap_caption_plan,
    write_combined_recap_caption_ass,
    write_combined_recap_caption_plan,
)
from recap_media.effects import (
    RecapEffectsError,
    create_recap_effects_plan,
    load_recap_effects,
    write_recap_effects_plan,
)
from recap_media.timeline import RECAP_PLAYBACK_SPEED, recap_final_duration_seconds
from recap_media.loader import (
    RecapInputError,
    RecapInputs,
    load_episode_identity,
    load_external_recap_script,
    load_recap_inputs,
    load_verified_story_map,
)
from recap_media.orpheus_provider import DEFAULT_VOICE, KNOWN_VOICES, OrpheusProvider
from recap_media.portrait_framing import (
    build_portrait_framing_plan_for_video,
    load_portrait_framing_plan,
    write_portrait_framing_plan,
)
from recap_media.render import (
    DEFAULT_NARRATION_PITCH_SEMITONES,
    DEFAULT_SOURCE_PITCH_SEMITONES,
    NARRATION_PITCH_SEMITONES_RANGE,
    RecapRenderError,
    render_recap,
    resolve_recap_source_video,
    validate_recap_media_file,
)
from recap_media.sequence import (
    assemble_sequence,
    interweave_original_dialogue,
    voiceover_timing_by_segment,
    write_recap_sequence,
)
from recap_media.voiceover import (
    load_voiceover_durations,
    synthesize_segment,
    synthesize_segments,
    wav_path_for_segment,
)


RECAP_EDITOR_BASE_SCHEMA_VERSION = 2


def _clean_recap_editor_effects() -> dict:
    """The editor base intentionally excludes every editable overlay."""

    return {
        "visual_fx_events": [],
        "motion_events": [],
        "sfx_events": [],
        "emoji_events": [],
        "time_basis": "recap_final_timeline",
    }

from ..settings_keys import (
    RECAP_NARRATION_GAIN_DB,
    RECAP_NARRATION_PITCH_SEMITONES,
    RECAP_SOURCE_PITCH_SEMITONES,
    RECAP_SCRIPT_SOURCE,
    RECAP_SPEED,
    RECAP_TARGET_DURATION_SECONDS,
    RECAP_VOICE,
)
from ..constants import ROOT

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

    def _recap_audio_settings(self) -> dict[str, float]:
        """Return the current, renderable Recap audio controls as one snapshot."""

        try:
            playback_speed = float(getattr(self, "recap_speed", RECAP_PLAYBACK_SPEED))
        except (TypeError, ValueError):
            playback_speed = RECAP_PLAYBACK_SPEED
        if not 0.5 <= playback_speed <= 2.0:
            playback_speed = RECAP_PLAYBACK_SPEED

        low, high = NARRATION_PITCH_SEMITONES_RANGE

        def pitch(attribute: str, default: float) -> float:
            try:
                value = float(getattr(self, attribute, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        try:
            narration_gain_db = float(
                getattr(self, "recap_narration_gain_db", DEFAULT_NARRATION_GAIN_DB)
            )
        except (TypeError, ValueError):
            narration_gain_db = DEFAULT_NARRATION_GAIN_DB

        return {
            "playback_speed": playback_speed,
            "narration_pitch_semitones": pitch(
                "recap_narration_pitch_semitones", DEFAULT_NARRATION_PITCH_SEMITONES
            ),
            "source_pitch_semitones": pitch(
                "recap_source_pitch_semitones", DEFAULT_SOURCE_PITCH_SEMITONES
            ),
            "narration_gain_db": narration_gain_db,
        }

    def _persist_recap_audio_settings(self) -> dict[str, float]:
        """Persist audio controls with the active Recap editor state when present."""

        settings = self._recap_audio_settings()
        if hasattr(self, "settings"):
            self.settings.setValue(RECAP_NARRATION_GAIN_DB, settings["narration_gain_db"])
        plan = getattr(self, "editor_asset_plan", None)
        if isinstance(plan, dict):
            plan["recap_audio_settings"] = dict(settings)
            self.editor_asset_plan = plan
            context = getattr(self, "recap_artifact_context", None)
            if context is not None:
                recap_plan = load_editor_asset_plan(context.editor_asset_plan_path)
                # Title edits can happen before the Recap editor path is
                # bound. Merge the persistent object into the Recap-owned
                # plan instead of allowing a later reload to discard it.
                if "persistent_title" in plan:
                    recap_plan["persistent_title"] = copy.deepcopy(
                        plan["persistent_title"]
                    )
                elif "persistent_title" in recap_plan:
                    plan["persistent_title"] = copy.deepcopy(
                        recap_plan["persistent_title"]
                    )
                recap_plan["recap_audio_settings"] = dict(settings)
                save_editor_asset_plan(recap_plan, context.editor_asset_plan_path)
            if getattr(self, "recap_artifact_context", None) is not None and hasattr(
                self, "save_editor_asset_plan_state"
            ):
                self.save_editor_asset_plan_state()
        return settings

    def sync_persistent_title_to_active_recap_plan(self) -> None:
        """Keep a title edited before opening Recap in its source-bound plan."""

        context = getattr(self, "recap_artifact_context", None)
        plan = getattr(self, "editor_asset_plan", None)
        if context is None or not isinstance(plan, dict) or "persistent_title" not in plan:
            return
        recap_plan = load_editor_asset_plan(context.editor_asset_plan_path)
        recap_plan["persistent_title"] = copy.deepcopy(plan["persistent_title"])
        if "recap_audio_settings" in plan:
            recap_plan["recap_audio_settings"] = copy.deepcopy(
                plan["recap_audio_settings"]
            )
        save_editor_asset_plan(recap_plan, context.editor_asset_plan_path)

    def _restore_recap_audio_settings(self, editor_plan: dict) -> dict[str, float]:
        """Use a Recap-owned settings snapshot when reopening that same Recap."""

        persisted = editor_plan.get("recap_audio_settings") if isinstance(editor_plan, dict) else None
        if not isinstance(persisted, dict):
            return self._recap_audio_settings()

        for attribute, key, default in (
            ("recap_speed", "playback_speed", RECAP_PLAYBACK_SPEED),
            (
                "recap_narration_pitch_semitones",
                "narration_pitch_semitones",
                DEFAULT_NARRATION_PITCH_SEMITONES,
            ),
            (
                "recap_source_pitch_semitones",
                "source_pitch_semitones",
                DEFAULT_SOURCE_PITCH_SEMITONES,
            ),
            ("recap_narration_gain_db", "narration_gain_db", DEFAULT_NARRATION_GAIN_DB),
        ):
            try:
                value = float(persisted.get(key, default))
            except (TypeError, ValueError):
                value = default
            setattr(self, attribute, value)
        if hasattr(self, "recap_speed_combo"):
            self.recap_speed_combo.blockSignals(True)
            self.recap_speed_combo.setCurrentText(f"{self.recap_speed:.2f}x")
            self.recap_speed_combo.blockSignals(False)
        for widget_name, value in (
            ("recap_narration_pitch_spinbox", self.recap_narration_pitch_semitones),
            ("recap_source_pitch_spinbox", self.recap_source_pitch_semitones),
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
        return self._recap_audio_settings()

    def toggle_recap_panel(self):

        # self.recap_button.isChecked() (a local widget property Qt
        # already toggled before this handler runs, since the button is
        # setCheckable(True)) rather than self.recap_frame.isVisible() --
        # isVisible() reflects the *whole ancestor chain*, so it reports
        # False for every descendant of a QMainWindow that hasn't been
        # shown yet regardless of this frame's own state, which made the
        # very first toggle a no-op until the window had been shown once.
        self.set_recap_mode("recap" if self.recap_button.isChecked() else "standard")

    def set_standard_short_mode(self):
        self.set_recap_mode("standard")

    def _configure_recap_source_area(self, recap_mode: bool):
        """Keep the shared source picker useful without crowding recap controls."""

        if not hasattr(self, "drop_zone"):
            return
        if recap_mode:
            self.drop_zone.setMinimumHeight(220)
            self.drop_zone.setMaximumHeight(240)
        else:
            self.drop_zone.setMinimumHeight(360)
            self.drop_zone.setMaximumHeight(16_777_215)
        if hasattr(self, "source_layout"):
            self.source_layout.setStretchFactor(self.drop_zone, 0 if recap_mode else 1)

    def clear_recap_artifact_context(self):
        """Drop recap state when the editor source changes."""

        self.recap_artifact_context = None
        self.recap_existing_editor_assets = None
        self.recap_editor_mode = False
        self.recap_editor_effects_path = None
        self.recap_editor_asset_plan_path = None
        self.recap_editor_asset_context = None
        self.recap_active_inputs = None
        self.recap_active_script = None
        self.recap_external_script_path = None
        self.recap_script_valid = False
        self.recap_sequence = None
        self._refresh_recap_script_preview(None)
        for attribute in (
            "generate_recap_sequence_button",
            "generate_recap_voiceover_button",
            "recap_open_editor_button",
        ):
            if hasattr(self, attribute):
                getattr(self, attribute).setEnabled(False)

    def _active_recap_artifact_context(self) -> RecapArtifactContext:
        source = getattr(self, "video_path", None)
        if not isinstance(source, Path):
            raise RecapInputError("Load a source video to begin AI Recap.")

        source = source.expanduser().resolve(strict=False)
        active = getattr(self, "recap_artifact_context", None)
        if isinstance(active, RecapArtifactContext) and (
            active.source_video == source
            or (
                getattr(self, "recap_editor_mode", False)
                and source in {
                    active.final_recap_path,
                    active.editor_base_recap_path,
                }
            )
        ):
            return active

        context = resolve_recap_artifact_context(source)
        self.recap_artifact_context = context
        return context

    def set_recap_mode(self, mode: str):
        """Switch workflow pages without resetting normal-short state."""

        recap_mode = mode == "recap"
        self._configure_recap_source_area(recap_mode)
        if all(
            hasattr(self, attribute)
            for attribute in (
                "mode_specific_stack",
                "standard_short_mode_frame",
                "recap_scroll_area",
            )
        ):
            page = (
                self.recap_scroll_area
                if recap_mode
                else self.standard_short_mode_frame
            )
            self.mode_specific_stack.setCurrentWidget(page)
        elif hasattr(self, "recap_frame"):
            self.recap_frame.setVisible(recap_mode)
        if hasattr(self, "recap_button"):
            self.recap_button.setChecked(recap_mode)
        if hasattr(self, "standard_short_button"):
            self.standard_short_button.setChecked(not recap_mode)
        self.recap_mode = "recap" if recap_mode else "standard"
        if recap_mode:
            self.refresh_recap_status()

    def _set_recap_status(self, message: str, state: str = "ready"):
        if not hasattr(self, "recap_status_label"):
            return
        self.recap_status_label.setText(message)
        self.recap_status_label.setProperty("state", state)
        style = self.recap_status_label.style()
        style.unpolish(self.recap_status_label)
        style.polish(self.recap_status_label)

    def _set_recap_episode_context(self, episode_identity: dict | None = None):
        if hasattr(self, "recap_source_label"):
            source = getattr(self, "video_path", None)
            source_text = source.name if isinstance(source, Path) else "current input source"
            self.recap_source_label.setText(f"Source: {source_text}")
        if not hasattr(self, "recap_episode_label"):
            return
        if not episode_identity:
            self.recap_episode_label.setText("Episode: not checked")
            return
        title = str(
            episode_identity.get("episode_title")
            or episode_identity.get("title")
            or "verified"
        )
        series = str(episode_identity.get("title") or "").strip()
        detail = f"Identity verified: {title}"
        if series and title != series:
            detail += f" ({series})"
        self.recap_episode_label.setText(detail)

    @staticmethod
    def _recap_block_counts(script: dict) -> tuple[int, int]:
        segments = script.get("segments", [])
        narration = sum(
            isinstance(segment, dict)
            and segment.get("block_type", "narration") == "narration"
            for segment in segments
        )
        source_moments = sum(
            isinstance(segment, dict)
            and segment.get("block_type") == "source_moment"
            for segment in segments
        )
        return narration, source_moments

    def _refresh_recap_script_preview(self, script: dict | None):
        if not hasattr(self, "recap_script_preview"):
            return
        self.recap_script_preview.clear()
        if not script:
            if hasattr(self.recap_script_preview, "setFixedHeight"):
                self.recap_script_preview.setFixedHeight(150)
            return
        segments = sorted(script.get("segments", []), key=lambda item: item["order"])
        for segment in segments:
            block_type = segment.get("block_type", "narration")
            if block_type == "narration":
                text = " ".join(str(segment.get("text", "")).split())
                summary = f"{segment['order']:02d}  {segment['segment_id']}  Narration\n{text}"
            else:
                candidate = next(
                    iter(
                        segment.get("original_dialogue_candidates", [])
                        or segment.get("candidate_visuals", [])
                    ),
                    {},
                )
                start = candidate.get("start", "?")
                end = candidate.get("end", "?")
                summary = (
                    f"{segment['order']:02d}  {segment['segment_id']}  Source Moment\n"
                    f"Beat {', '.join(segment.get('beat_ids', []))}  {start}-{end}"
                )
            self.recap_script_preview.addItem(summary)
        if hasattr(self.recap_script_preview, "setFixedHeight"):
            row_height = max(self.recap_script_preview.sizeHintForRow(0), 36)
            visible_rows = min(len(segments), 15)
            self.recap_script_preview.setFixedHeight(
                max(150, row_height * visible_rows + 8)
            )

    def _set_recap_script_source(self, source: str):
        if source not in {"local", "external"}:
            raise ValueError(f"Unsupported recap script source: {source}")
        self.recap_script_source = source
        if hasattr(self, "settings"):
            self.settings.setValue(RECAP_SCRIPT_SOURCE, source)
        self._sync_recap_script_source_combo()

    def _sync_recap_script_source_combo(self):
        if hasattr(self, "recap_script_source_combo"):
            desired = (
                "Import AI Script"
                if getattr(self, "recap_script_source", "local") == "external"
                else "Local AI"
            )
            self.recap_script_source_combo.blockSignals(True)
            self.recap_script_source_combo.setCurrentText(desired)
            self.recap_script_source_combo.blockSignals(False)

    def select_local_recap_script(self):
        self._set_recap_script_source("local")
        self.recap_active_script = None
        self.recap_active_inputs = None
        self.recap_script_valid = False
        self._refresh_recap_script_preview(None)
        self._set_recap_status(
            "Script source: Local AI. Validate Script to use the current recap script."
        )
        if hasattr(self, "generate_recap_sequence_button"):
            self.generate_recap_sequence_button.setEnabled(False)

    def recap_script_source_changed(self, label: str):
        if label == "Local AI":
            self.select_local_recap_script()
        elif label == "Import AI Script":
            self.choose_external_recap_script()
        elif label == "Paste Script":
            self.open_recap_paste_dialog()

    def choose_external_recap_script(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import AI Recap Script",
            "",
            "JSON files (*.json)",
        )
        if path:
            self.import_external_recap_script(Path(path))
        else:
            self._sync_recap_script_source_combo()

    def open_recap_paste_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Paste AI Recap Script")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Schema-v2 recap JSON"))
        editor = QPlainTextEdit()
        editor.setPlaceholderText('{"schema_version": 2, "segments": [...]}')
        layout.addWidget(editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.import_pasted_recap_script(editor.toPlainText())
        else:
            self._sync_recap_script_source_combo()

    def _accepted_recap_context(self) -> tuple[dict, dict]:
        context = self._active_recap_artifact_context()
        return (
            load_episode_identity(context.episode_identity_path),
            load_verified_story_map(context.verified_story_map_path),
        )

    def _persist_recap_script(self, script: dict, context: RecapArtifactContext):
        context.recap_script_path.parent.mkdir(parents=True, exist_ok=True)
        context.recap_script_path.write_text(json.dumps(script, indent=2), encoding="utf-8")

    def _activate_valid_external_script(
        self,
        script: dict,
        identity: dict,
        story_map: dict,
        context: RecapArtifactContext,
    ):
        self._persist_recap_script(script, context)
        self.recap_artifact_context = context
        self._set_recap_script_source("external")
        self.recap_active_script = script
        self.recap_active_inputs = RecapInputs(identity, story_map, script)
        self.recap_script_valid = True
        narration, source_moments = self._recap_block_counts(script)
        block_count = len(script.get("segments", []))
        self._set_recap_episode_context(identity)
        self._refresh_recap_script_preview(script)
        self._set_recap_status(
            "VALID\n"
            f"{block_count} blocks\n"
            f"{narration} narration / {source_moments} source moments\n"
            "All beat IDs valid. Source moments grounded. Ready to generate."
        )
        self.append_recap_log(
            f"External script selected: {narration} narration block(s), "
            f"{source_moments} source moment(s)."
        )
        if hasattr(self, "generate_recap_sequence_button"):
            self.generate_recap_sequence_button.setEnabled(True)
        self.refresh_recap_editor_readiness()

    def _show_recap_import_error(self, message: str):
        self.recap_script_valid = False
        self.refresh_recap_editor_readiness()
        self._set_recap_status(message, "offline")
        self.append_recap_log(f"ERROR: {message}")
        if isinstance(self, QWidget):
            QMessageBox.warning(self, "AI Recap", message)

    def import_external_recap_script(self, path: Path) -> bool:
        try:
            context = self._active_recap_artifact_context()
            identity, story_map = self._accepted_recap_context()
            script = load_external_recap_script(
                Path(path),
                episode_identity=identity,
                verified_story_map=story_map,
            )
        except RecapInputError as exc:
            self._show_recap_import_error(str(exc))
            self._sync_recap_script_source_combo()
            return False
        self.recap_external_script_path = Path(path)
        self._activate_valid_external_script(script, identity, story_map, context)
        return True

    def import_pasted_recap_script(self, text: str) -> bool:
        try:
            context = self._active_recap_artifact_context()
        except RecapInputError as exc:
            self._show_recap_import_error(str(exc))
            return False
        context.pasted_script_path.parent.mkdir(parents=True, exist_ok=True)
        context.pasted_script_path.write_text(text, encoding="utf-8")
        return self.import_external_recap_script(context.pasted_script_path)

    def validate_active_recap_script(self) -> bool:
        if getattr(self, "recap_script_source", "local") == "external":
            try:
                context = self._active_recap_artifact_context()
            except RecapInputError as exc:
                self._show_recap_import_error(str(exc))
                return False
            path = getattr(self, "recap_external_script_path", None)
            if path:
                return self.import_external_recap_script(path)
            if context.recap_script_path.exists():
                return self.import_external_recap_script(context.recap_script_path)
            if getattr(self, "recap_active_script", None):
                try:
                    identity, story_map = self._accepted_recap_context()
                except RecapInputError as exc:
                    self._show_recap_import_error(str(exc))
                    return False
                self._activate_valid_external_script(
                    self.recap_active_script, identity, story_map, context
                )
                return True
            self._show_recap_import_error("Choose or paste an external recap script first.")
            return False

        try:
            context = self._active_recap_artifact_context()
            inputs = load_recap_inputs(
                context.episode_identity_path,
                context.verified_story_map_path,
                context.recap_script_path,
            )
        except RecapInputError as exc:
            self._show_recap_import_error(str(exc))
            return False
        self.recap_active_inputs = inputs
        self.recap_active_script = inputs.recap_script
        self.recap_script_valid = True
        narration, source_moments = self._recap_block_counts(inputs.recap_script)
        self._set_recap_episode_context(inputs.episode_identity)
        self._refresh_recap_script_preview(inputs.recap_script)
        self._set_recap_status(
            "Local AI script valid\n"
            f"{narration} narration / {source_moments} source moments\n"
            "Ready to generate."
        )
        if hasattr(self, "generate_recap_sequence_button"):
            self.generate_recap_sequence_button.setEnabled(True)
        self.refresh_recap_editor_readiness()
        return True

    def _active_recap_inputs(self):
        active_inputs = getattr(self, "recap_active_inputs", None)
        if active_inputs is not None:
            return active_inputs
        context = self._active_recap_artifact_context()
        return load_recap_inputs(
            context.episode_identity_path,
            context.verified_story_map_path,
            context.recap_script_path,
        )

    def _existing_recap_editor_assets(self) -> tuple[RecapArtifactContext, dict, Path, Path] | None:
        """Hydrate one compatible persisted Recap without regenerating it."""

        if not bool(getattr(self, "recap_script_valid", False)):
            return None
        try:
            context = self._active_recap_artifact_context()
            if not context.final_recap_path.exists() or not context.recap_sequence_path.exists():
                return None
            sequence = json.loads(context.recap_sequence_path.read_text(encoding="utf-8"))
            script = self._active_recap_inputs().recap_script
            sequence_ids = [str(item.get("segment_id", "")) for item in sequence.get("segments", []) if isinstance(item, dict)]
            script_ids = [str(item.get("segment_id", "")) for item in script.get("segments", []) if isinstance(item, dict)]
            if not sequence_ids or sequence_ids != script_ids:
                return None
            effects_path, editor_plan_path = resolve_recap_editor_plan_paths(context)
        except (OSError, ValueError, RecapInputError):
            return None
        return context, sequence, effects_path, editor_plan_path

    def refresh_recap_editor_readiness(self) -> bool:
        """Enable opening only for a source-bound, compatible persisted Recap."""

        ready = self._existing_recap_editor_assets()
        self.recap_existing_editor_assets = ready
        if ready is not None:
            _context, sequence, _effects_path, _editor_plan_path = ready
            self.recap_sequence = sequence
        if hasattr(self, "recap_open_editor_button"):
            self.recap_open_editor_button.setEnabled(ready is not None or bool(getattr(self, "recap_sequence", None)))
        return ready is not None

    def _editor_base_voiceover_clips(
        self,
        inputs: RecapInputs,
        sequence: dict,
        editor_plan_path: Path,
        context: RecapArtifactContext,
    ) -> list[dict]:
        """Return the persisted narration clips needed to rebuild an editor base."""

        expected_ids = {
            str(segment.get("segment_id", ""))
            for segment in inputs.recap_script.get("segments", [])
            if isinstance(segment, dict)
            and segment.get("block_type") != "source_moment"
            and segment.get("presentation_hint") != "visual_only"
        }
        editor_plan = load_editor_asset_plan(editor_plan_path)
        planned_clips = clips_of_kind(editor_plan, "VOICEOVER")
        planned_ids = {str(clip.get("id", "")) for clip in planned_clips if isinstance(clip, dict)}
        if expected_ids and expected_ids.issubset(planned_ids):
            return planned_clips

        durations = load_voiceover_durations(context.voiceover_manifest_path)
        return self._rebuild_voiceover_clips(inputs, durations, sequence)

    def _detach_recap_editor_preview(self, media_path: Path) -> tuple[int, bool] | None:
        """Release a currently-open editor base before atomically replacing it."""

        current_path = getattr(self, "video_path", None)
        if current_path is None or Path(current_path).resolve() != media_path.resolve():
            return None
        player = getattr(self, "player", None)
        if player is None:
            return None
        try:
            position = int(player.position())
            was_playing = bool(player.playbackState().name == "PlayingState")
            player.stop()
            player.setSource(QUrl())
            return position, was_playing
        except Exception:
            return None

    def _reload_recap_editor_preview(
        self,
        media_path: Path,
        prior_state: tuple[int, bool] | None,
    ) -> None:
        """Rebind a validated replacement without resetting editor state."""

        if prior_state is None:
            return
        player = getattr(self, "player", None)
        if player is None:
            return
        position, was_playing = prior_state
        try:
            if hasattr(self, "video_widget"):
                player.setVideoOutput(self.video_widget)
            player.setSource(QUrl.fromLocalFile(str(media_path)))

            def restore_player_state():
                player.setPosition(max(0, position))
                if was_playing:
                    player.play()

            QTimer.singleShot(150, restore_player_state)
        except Exception:
            # Open in Editor will still bind the canonical path normally.
            pass

    def _ensure_recap_editor_base(
        self,
        context: RecapArtifactContext,
        sequence: dict,
        effects_path: Path,
        editor_plan_path: Path,
    ) -> Path:
        """Return the caption-free assembled Recap used by the editor.

        Older accepted Recaps predate this artifact. Rebuild only their base
        render from persisted Track B inputs; narration, sequence, and effects
        plans remain unchanged.
        """

        audio_settings = self._recap_audio_settings()
        try:
            metadata = json.loads(
                context.editor_base_metadata_path.read_text(encoding="utf-8")
            )
            if (
                context.editor_base_recap_path.is_file()
                and context.editor_base_recap_path.stat().st_size > 0
                and metadata.get("schema_version") == RECAP_EDITOR_BASE_SCHEMA_VERSION
                and metadata.get("kind") == "ai_recap_clean_editor_base"
                and metadata.get("recap_audio_settings") == audio_settings
            ):
                return context.editor_base_recap_path
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

        inputs = self._active_recap_inputs()
        try:
            portrait_plan = load_portrait_framing_plan(context.portrait_framing_plan_path)
        except RecapInputError:
            source_video = resolve_recap_source_video(inputs.episode_identity)
            portrait_plan = build_portrait_framing_plan_for_video(
                source_video,
                cache_path=context.portrait_framing_plan_path,
            )
            write_portrait_framing_plan(portrait_plan, context.portrait_framing_plan_path)

        try:
            duck_plan = load_duck_plan(context.audio_duck_plan_path)
            if duck_plan.get("settings", {}).get("narration_gain_db") != audio_settings["narration_gain_db"]:
                raise RecapInputError("Recap narration gain settings changed")
        except RecapInputError:
            duck_plan = build_duck_plan(
                sequence,
                narration_gain_db=audio_settings["narration_gain_db"],
            )
            write_duck_plan(duck_plan, context.audio_duck_plan_path)

        voiceover_clips = self._editor_base_voiceover_clips(
            inputs,
            sequence,
            editor_plan_path,
            context,
        )
        staging_path = context.editor_base_recap_path.with_name(
            f"{context.editor_base_recap_path.stem}.rendering{context.editor_base_recap_path.suffix}"
        )
        self.append_recap_log("Creating caption-free Recap editor base...")
        render_recap(
            inputs.episode_identity,
            sequence,
            voiceover_clips,
            portrait_plan,
            duck_plan,
            output_path=staging_path,
            captions_ass_path=None,
            allow_captionless=True,
            recap_effects=_clean_recap_editor_effects(),
            voiceover_dir=context.voiceover_dir,
            playback_speed=audio_settings["playback_speed"],
            narration_pitch_semitones=audio_settings["narration_pitch_semitones"],
            source_pitch_semitones=audio_settings["source_pitch_semitones"],
        )
        validate_recap_media_file(staging_path)
        prior_preview_state = self._detach_recap_editor_preview(
            context.editor_base_recap_path
        )
        try:
            staging_path.replace(context.editor_base_recap_path)
        except OSError as exc:
            self._reload_recap_editor_preview(
                context.editor_base_recap_path,
                prior_preview_state,
            )
            raise RecapRenderError(
                f"Could not replace the Recap editor preview media: {exc}"
            ) from exc
        self._reload_recap_editor_preview(
            context.editor_base_recap_path,
            prior_preview_state,
        )
        context.editor_base_metadata_path.write_text(
            json.dumps(
                {
                    "schema_version": RECAP_EDITOR_BASE_SCHEMA_VERSION,
                    "kind": "ai_recap_clean_editor_base",
                    "time_basis": "recap_final_timeline",
                    "editable_overlays_baked": False,
                    "recap_audio_settings": audio_settings,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return context.editor_base_recap_path

    def refresh_recap_status(self):

        self._set_recap_episode_context()
        if not isinstance(getattr(self, "video_path", None), Path):
            self._set_recap_episode_context(None)
            self._set_recap_status(
                "Load a source video to begin AI Recap.",
                "offline",
            )
            if hasattr(self, "generate_recap_sequence_button"):
                self.generate_recap_sequence_button.setEnabled(False)
        else:
            try:
                identity, _ = self._accepted_recap_context()
            except RecapInputError as exc:
                self._set_recap_status(str(exc), "offline")
                self.generate_recap_sequence_button.setEnabled(False)
            else:
                self._set_recap_episode_context(identity)
                source = "External" if self.recap_script_source == "external" else "Local AI"
                self._set_recap_status(
                    f"Identity verified. Script source: {source}. Validate Script to continue."
                )
                self.generate_recap_sequence_button.setEnabled(
                    bool(getattr(self, "recap_script_valid", False))
                )

        self.generate_recap_voiceover_button.setEnabled(
            bool(getattr(self, "recap_sequence", None))
        )
        self.refresh_recap_editor_readiness()
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

    def recap_speed_changed(self, value: str):
        try:
            speed = float(str(value).strip().removesuffix("x"))
        except ValueError:
            speed = 1.5
        self.recap_speed = speed
        self.settings.setValue(RECAP_SPEED, speed)
        self._persist_recap_audio_settings()

    def recap_narration_pitch_changed(self, value: float):
        low, high = NARRATION_PITCH_SEMITONES_RANGE
        try:
            semitones = float(value)
        except (TypeError, ValueError):
            semitones = DEFAULT_NARRATION_PITCH_SEMITONES
        self.recap_narration_pitch_semitones = max(low, min(high, semitones))
        self.settings.setValue(
            RECAP_NARRATION_PITCH_SEMITONES,
            self.recap_narration_pitch_semitones,
        )
        self._persist_recap_audio_settings()

    def recap_source_pitch_changed(self, value: float):
        low, high = NARRATION_PITCH_SEMITONES_RANGE
        try:
            semitones = float(value)
        except (TypeError, ValueError):
            semitones = DEFAULT_SOURCE_PITCH_SEMITONES
        self.recap_source_pitch_semitones = max(low, min(high, semitones))
        self.settings.setValue(
            RECAP_SOURCE_PITCH_SEMITONES,
            self.recap_source_pitch_semitones,
        )
        self._persist_recap_audio_settings()

    def generate_recap(self) -> bool:
        """Build a recap sequence, narration, and its final media render."""

        self.append_recap_log("Generating recap sequence...")
        if not self.generate_recap_sequence():
            return False
        if not self.generate_recap_voiceover():
            return False
        return self.generate_recap_final_render()

    def generate_recap_final_render(self) -> bool:
        """Create source-bound Track B render inputs and render the active recap."""

        if not getattr(self, "recap_sequence", None):
            message = "Generate the recap sequence and narration before rendering."
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.information(self, "Create AI Recap", message)
            return False

        try:
            context = self._active_recap_artifact_context()
            inputs = self._active_recap_inputs()
            source_video = resolve_recap_source_video(inputs.episode_identity)
        except (RecapInputError, RecapRenderError) as exc:
            message = str(exc)
            self.append_recap_log(f"ERROR: Recap render setup failed: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Create AI Recap", message)
            return False

        audio_settings = self._persist_recap_audio_settings()

        voiceover_clips = clips_of_kind(self.editor_asset_plan, "VOICEOVER")
        active_voiceover_clips = [
            clip
            for clip in voiceover_clips
            if clip.get("active", True) and not clip.get("deleted")
        ]
        if not active_voiceover_clips:
            message = "No active narration blocks are available for recap rendering."
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Create AI Recap", message)
            return False

        self.append_recap_log("Preparing recap render assets...")
        QCoreApplication.processEvents()
        try:
            title_ass_path = None
            if hasattr(self, "write_persistent_title_for_export"):
                title_ass_path = self.write_persistent_title_for_export(
                    context.persistent_title_ass_path,
                    recap_final_duration_seconds(
                        float(self.recap_sequence.get("total_duration_seconds", 0.0) or 0.0),
                        audio_settings["playback_speed"],
                    ),
                )
            portrait_plan = build_portrait_framing_plan_for_video(
                source_video,
                cache_path=context.portrait_framing_plan_path,
            )
            write_portrait_framing_plan(portrait_plan, context.portrait_framing_plan_path)

            narration_wavs = {
                clip["id"]: wav_path_for_segment(clip["id"], context.voiceover_dir)
                for clip in active_voiceover_clips
            }
            captions = build_narration_captions(
                inputs.recap_script["segments"],
                wav_paths_by_segment=narration_wavs,
            )
            write_narration_captions(captions, context.narration_captions_path)
            combined_captions = build_combined_recap_caption_plan(
                self.recap_sequence,
                captions,
                voiceover_clips,
                playback_speed=audio_settings["playback_speed"],
            )
            write_combined_recap_caption_plan(combined_captions, context.recap_caption_plan_path)
            write_combined_recap_caption_ass(combined_captions, context.narration_captions_ass_path)

            duck_plan = build_duck_plan(
                self.recap_sequence,
                narration_gain_db=audio_settings["narration_gain_db"],
            )
            write_duck_plan(duck_plan, context.audio_duck_plan_path)

            self.append_recap_log("Rendering recap video...")
            for index, segment in enumerate(
                sorted(self.recap_sequence["segments"], key=lambda item: item["order"]),
                start=1,
            ):
                self.append_recap_log(
                    f"Assembling block {index}/{len(self.recap_sequence['segments'])}: "
                    f"{segment['segment_id']}..."
                )
            QCoreApplication.processEvents()

            effects_speed = None
            try:
                effects_payload = json.loads(context.effects_plan_path.read_text(encoding="utf-8"))
                effects_speed = effects_payload.get("playback_speed")
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            if effects_speed != audio_settings["playback_speed"]:
                create_recap_effects_plan(
                    self.recap_sequence,
                    captions,
                    portrait_plan,
                    inputs.recap_script,
                    source_key=str(context.final_recap_path),
                    effects_path=context.effects_plan_path,
                    editor_plan_path=context.editor_asset_plan_path,
                    playback_speed=audio_settings["playback_speed"],
                )
            try:
                recap_effects = load_recap_effects(
                    effects_path=context.effects_plan_path,
                    editor_plan_path=context.editor_asset_plan_path,
                    render_planned_effects=True,
                )
            except RecapEffectsError:
                recap_effects = {}

            base_metadata = {
                "schema_version": RECAP_EDITOR_BASE_SCHEMA_VERSION,
                "kind": "ai_recap_clean_editor_base",
                "time_basis": "recap_final_timeline",
                "editable_overlays_baked": False,
                "recap_audio_settings": audio_settings,
            }
            existing_base_metadata = {}
            try:
                existing_base_metadata = json.loads(
                    context.editor_base_metadata_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                pass
            if (
                not context.editor_base_recap_path.exists()
                or existing_base_metadata != base_metadata
            ):
                render_recap(
                    inputs.episode_identity,
                    self.recap_sequence,
                    voiceover_clips,
                    portrait_plan,
                    duck_plan,
                    output_path=context.editor_base_recap_path,
                    captions_ass_path=None,
                    allow_captionless=True,
                    recap_effects=_clean_recap_editor_effects(),
                    voiceover_dir=context.voiceover_dir,
                    playback_speed=audio_settings["playback_speed"],
                    narration_pitch_semitones=audio_settings["narration_pitch_semitones"],
                    source_pitch_semitones=audio_settings["source_pitch_semitones"],
                )
                context.editor_base_metadata_path.write_text(
                    json.dumps(base_metadata, indent=2) + "\n",
                    encoding="utf-8",
                )

            final_render_kwargs = {
                "captions_ass_path": context.narration_captions_ass_path,
                "output_path": context.final_recap_path,
                "recap_effects": recap_effects,
                "voiceover_dir": context.voiceover_dir,
                "playback_speed": audio_settings["playback_speed"],
                "narration_pitch_semitones": audio_settings["narration_pitch_semitones"],
                "source_pitch_semitones": audio_settings["source_pitch_semitones"],
            }
            if title_ass_path is not None:
                final_render_kwargs["title_ass_path"] = title_ass_path
            output_path = render_recap(
                inputs.episode_identity,
                self.recap_sequence,
                voiceover_clips,
                portrait_plan,
                duck_plan,
                **final_render_kwargs,
            )
        except (RecapRenderError, RecapInputError, OSError, ValueError) as exc:
            message = str(exc)
            self.append_recap_log(f"ERROR: Recap render failed: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Create AI Recap", message)
            return False

        self.append_recap_log(f"Final recap complete: {output_path}")
        if hasattr(self, "open_final_video"):
            self.open_final_video(output_path)
        elif isinstance(self, QWidget):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path)))
        return True

    def generate_recap_sequence(self):

        try:
            context = self._active_recap_artifact_context()
            inputs = self._active_recap_inputs()
        except RecapInputError as exc:
            self.append_recap_log(f"ERROR: {exc}")
            QMessageBox.warning(self, "Create AI Recap", str(exc))
            return False

        narration_durations = load_voiceover_durations(context.voiceover_manifest_path)
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
        write_recap_sequence(sequence, context.recap_sequence_path)

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
        if hasattr(self, "recap_open_editor_button"):
            self.recap_open_editor_button.setEnabled(True)
        return True

    def open_recap_in_editor(self):
        if not getattr(self, "recap_sequence", None):
            self.append_recap_log("Generate a recap sequence first.")
            if isinstance(self, QWidget):
                QMessageBox.information(self, "AI Recap", "Generate a recap sequence first.")
            return
        try:
            context = self._active_recap_artifact_context()
        except RecapInputError as exc:
            # Keep the existing editor entry point usable for an in-memory
            # sequence while its artifact root is still being established.
            if hasattr(self, "refresh_editor_asset_timeline"):
                self.refresh_editor_asset_timeline()
            if hasattr(self, "timeline"):
                self.timeline.setFocus()
            self.append_recap_log(f"Recap sequence opened in the existing editor timeline. ({exc})")
            return

        ready = getattr(self, "recap_existing_editor_assets", None)
        if ready is None or ready[0] != context:
            ready = self._existing_recap_editor_assets()
        if ready is not None:
            _context, sequence, effects_path, editor_plan_path = ready
            self.recap_sequence = sequence
        else:
            effects_path, editor_plan_path = context.effects_plan_path, context.editor_asset_plan_path

        self.editor_asset_plan = load_editor_asset_plan(editor_plan_path)
        audio_settings = self._restore_recap_audio_settings(self.editor_asset_plan)

        try:
            editor_media = self._ensure_recap_editor_base(
                context,
                self.recap_sequence,
                effects_path,
                editor_plan_path,
            )
        except (OSError, ValueError, RecapInputError, RecapRenderError, RecapEffectsError) as exc:
            message = f"Caption-free Recap editor base is unavailable: {exc}"
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Open in Editor", message)
            return

        duration = recap_final_duration_seconds(
            float(self.recap_sequence.get("total_duration_seconds", 0.0) or 0.0),
            audio_settings["playback_speed"],
        )
        self.recap_editor_mode = True
        self.recap_editor_effects_path = effects_path
        self.recap_editor_asset_plan_path = editor_plan_path
        self.editor_asset_plan = load_editor_asset_plan(editor_plan_path)
        if effects_path.exists():
            # This is a schema projection for legacy accepted plans, not a
            # new effects pass: it makes existing motion/FX visible as shared
            # editor clips before the timeline is opened.
            payload = json.loads(effects_path.read_text(encoding="utf-8"))
            write_recap_effects_plan(
                payload,
                source_key=str(self.editor_asset_plan.get("source_video", "recap")),
                effects_path=effects_path,
                editor_plan_path=editor_plan_path,
            )
            self.editor_asset_plan = load_editor_asset_plan(editor_plan_path)
        try:
            caption_plan_speed = None
            if context.recap_caption_plan_path.exists():
                caption_plan = load_combined_recap_caption_plan(context.recap_caption_plan_path)
                caption_plan_speed = caption_plan.get("playback_speed")
            if caption_plan_speed != audio_settings["playback_speed"]:
                narration = json.loads(context.narration_captions_path.read_text(encoding="utf-8"))
                caption_plan = build_combined_recap_caption_plan(
                    self.recap_sequence,
                    narration,
                    clips_of_kind(self.editor_asset_plan, "VOICEOVER"),
                    playback_speed=audio_settings["playback_speed"],
                )
                write_combined_recap_caption_plan(caption_plan, context.recap_caption_plan_path)
            self.recap_editor_caption_plan = caption_plan
            self.recap_editor_caption_segments = [
                {
                    "start_ms": int(round(float(cue["start"]) * 1000)),
                    "end_ms": int(round(float(cue["end"]) * 1000)),
                    "text": str(cue["text"]),
                    "caption_id": cue["id"],
                    "block_id": cue["block_id"],
                    "speaker_domain": cue["speaker_domain"],
                }
                for cue in caption_plan.get("cues", [])
                if isinstance(cue, dict)
            ]
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.append_recap_log(f"WARNING: Recap caption plan unavailable: {exc}")
            self.recap_editor_caption_plan = None
            self.recap_editor_caption_segments = []
        self.recap_editor_asset_context = (
            str(self.editor_asset_plan.get("source_video") or context.final_recap_path),
            float(self.editor_asset_plan.get("selection_start", 0.0) or 0.0),
            float(self.editor_asset_plan.get("selection_end", duration) or duration),
        )

        # The assembled, caption-free Recap is the editor's media source. The
        # original episode remains artifact provenance only.
        if hasattr(self, "load_video"):
            self._preserve_recap_artifact_context = True
            try:
                self.load_video(editor_media)
            finally:
                self._preserve_recap_artifact_context = False

        if hasattr(self, "refresh_editor_asset_timeline"):
            self.refresh_editor_asset_timeline()
        if hasattr(self, "timeline"):
            self.timeline.setFocus()
        self.append_recap_log("Recap sequence opened in the existing editor timeline.")

    def start_recap_editor_export(self) -> bool:
        """Launch the no-replanning export path for an AI Recap editor session."""

        try:
            context = self._active_recap_artifact_context()
            effects_path = Path(self.recap_editor_effects_path)
            editor_plan_path = Path(self.recap_editor_asset_plan_path)
            if not context.editor_base_recap_path.is_file():
                raise RecapInputError("Open the AI Recap in Editor before exporting it.")
            audio_settings = self._persist_recap_audio_settings()
            self._ensure_recap_editor_base(
                context,
                self.recap_sequence,
                effects_path,
                editor_plan_path,
            )
            self.save_editor_asset_plan_state()
            caption_plan = getattr(self, "recap_editor_caption_plan", None)
            if (
                not isinstance(caption_plan, dict)
                or caption_plan.get("playback_speed") != audio_settings["playback_speed"]
            ):
                narration = json.loads(context.narration_captions_path.read_text(encoding="utf-8"))
                caption_plan = build_combined_recap_caption_plan(
                    self.recap_sequence,
                    narration,
                    clips_of_kind(self.editor_asset_plan, "VOICEOVER"),
                    playback_speed=audio_settings["playback_speed"],
                )
                self.recap_editor_caption_plan = caption_plan
            if isinstance(caption_plan, dict):
                write_combined_recap_caption_plan(caption_plan, context.recap_caption_plan_path)
        except (OSError, TypeError, RecapInputError) as exc:
            self.render_log.append(f"ERROR: AI Recap export setup failed: {exc}")
            return False

        duration = recap_final_duration_seconds(
            float(self.recap_sequence.get("total_duration_seconds", 0.0) or 0.0),
            audio_settings["playback_speed"],
        )
        title_ass_path = None
        if hasattr(self, "write_persistent_title_for_export"):
            title_ass_path = self.write_persistent_title_for_export(
                context.persistent_title_ass_path,
                duration,
            )
        self.recap_export_in_progress = True
        self.pending_render_duration_seconds = duration
        self.start_render_progress(duration, bool(getattr(self, "music_path", None)), False)
        self.render_log.clear()
        self.reset_render_log_file()
        self.render_log.append("=== AI RECAP EDITOR EXPORT ===")
        self.render_log.append("Consuming current Recap editor entities; no Standard Short planning will run.")
        self.generate_button.setEnabled(False)
        self.generate_button.setText("Rendering Recap...")
        self.find_clips_button.setEnabled(False)
        self.music_button.setEnabled(False)

        arguments = [
            str(ROOT / "app" / "recap_editor_export.py"),
            "--editor-base", str(context.editor_base_recap_path),
            "--effects-plan", str(effects_path),
            "--editor-plan", str(editor_plan_path),
            "--caption-plan", str(context.recap_caption_plan_path),
            "--caption-ass", str(context.narration_captions_ass_path),
            "--output", str(context.final_recap_path),
        ]
        if title_ass_path is not None:
            arguments.extend(["--title-ass", str(title_ass_path)])
        for option, value in (
            ("--caption-position-x", getattr(self, "caption_position_x", None)),
            ("--caption-position-y", getattr(self, "caption_position_y", None)),
            ("--caption-scale", getattr(self, "caption_scale", None)),
        ):
            if value is not None:
                arguments.extend([option, str(value)])
        music_path = getattr(self, "music_path", None)
        if music_path:
            arguments.extend([
                "--music", str(music_path),
                "--music-volume", str(float(getattr(self, "music_volume", 0)) / 100.0),
            ])
        self.set_render_progress_stage("rendering")
        self.render_process.start(sys.executable, arguments)
        return True

    def finish_recap_editor_export(self):
        """Complete a source-bound Recap export without Standard archiving."""

        context = self._active_recap_artifact_context()
        self.generate_button.setEnabled(True)
        self.generate_button.setText("Generate Again")
        self.find_clips_button.setEnabled(self.video_path is not None)
        self.music_button.setEnabled(True)
        self.finish_render_progress(True)
        self.render_log.append("")
        self.render_log.append("AI RECAP EXPORT COMPLETE")
        self.render_log.append(f"Final Recap: {context.final_recap_path}")
        self.open_final_video(context.final_recap_path)
        self.write_render_log_snapshot()

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

    def generate_recap_voiceover(self) -> bool:

        if not getattr(self, "recap_sequence", None):
            message = "Generate the recap sequence first."
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.information(self, "Create AI Recap", message)
            return False

        # Same context-sync step generate_sfx()/generate_visual_assets()
        # already do before writing clips -- without it, the plan's
        # stored source_video/selection stays whatever it was before, so
        # editor_asset_context_matches_current_selection() (which
        # visible_editor_asset_clips() gates on) would never match and
        # the new VOICEOVER clips would silently never appear on the
        # timeline.
        self.ensure_current_editor_asset_context(clear_on_change=True)

        try:
            context = self._active_recap_artifact_context()
            inputs = self._active_recap_inputs()
        except RecapInputError as exc:
            message = str(exc)
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Create AI Recap", message)
            return False

        narration_segments = [
            segment
            for segment in inputs.recap_script["segments"]
            if not (
                segment.get("block_type") == "source_moment"
                or segment.get("presentation_hint") == "visual_only"
            )
        ]
        if not narration_segments:
            message = "The recap script has no narration blocks to synthesize."
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Create AI Recap", message)
            return False

        self.append_recap_log("Checking Orpheus...")
        provider = OrpheusProvider()
        readiness = provider.readiness()
        if readiness.get("state") != "online":
            message = (
                "Orpheus-FastAPI isn't reachable "
                f"({readiness.get('message', 'unknown error')}). "
                "Start the local Orpheus server and try again."
            )
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Create AI Recap", message)
            return False

        self.append_recap_log("Orpheus ready.")
        self.generate_recap_voiceover_button.setEnabled(False)
        if hasattr(self, "generate_recap_sequence_button"):
            self.generate_recap_sequence_button.setEnabled(False)
        self.append_recap_log(
            f"Generating narration for {len(narration_segments)} block(s)..."
        )
        QCoreApplication.processEvents()

        def report_segment_start(segment_id: str, index: int, total: int):
            self.append_recap_log(f"Narration {index}/{total}: {segment_id} processing...")
            QCoreApplication.processEvents()

        def report_segment_complete(result, index: int, total: int):
            if result.error:
                message = f"ERROR: Narration {index}/{total}: {result.segment_id}: {result.error}"
            elif result.cache_hit:
                message = f"Narration {index}/{total}: {result.segment_id} cached, reusing."
            else:
                message = (
                    f"Narration {index}/{total}: {result.segment_id} complete "
                    f"({result.duration_seconds:.1f}s audio)."
                )
            self.append_recap_log(message)
            QCoreApplication.processEvents()

        try:
            results = synthesize_segments(
                provider,
                inputs.recap_script["segments"],
                voice=self.recap_voice,
                speed=getattr(self, "recap_speed", 1.5),
                output_dir=context.voiceover_dir,
                manifest_path=context.voiceover_manifest_path,
                on_segment_start=report_segment_start,
                on_segment_complete=report_segment_complete,
            )
        except Exception as exc:
            message = f"Voiceover synthesis failed: {exc}"
            self.append_recap_log(f"ERROR: {message}")
            if isinstance(self, QWidget):
                QMessageBox.warning(self, "Create AI Recap", message)
            self.generate_recap_voiceover_button.setEnabled(True)
            if hasattr(self, "generate_recap_sequence_button"):
                self.generate_recap_sequence_button.setEnabled(
                    bool(getattr(self, "recap_script_valid", False))
                )
            return False

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
        write_recap_sequence(sequence, context.recap_sequence_path)
        self.recap_sequence = sequence

        new_clips = self._rebuild_voiceover_clips(inputs, durations, sequence)
        self.editor_asset_plan = replace_kind_clips(
            self.editor_asset_plan, "VOICEOVER", new_clips
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()
        self.refresh_recap_voiceover_list()

        self.generate_recap_voiceover_button.setEnabled(True)
        if hasattr(self, "generate_recap_sequence_button"):
            self.generate_recap_sequence_button.setEnabled(
                bool(getattr(self, "recap_script_valid", False))
            )

        ready_count = sum(1 for clip in new_clips if clip["active"])
        if errors:
            self.append_recap_log(
                f"ERROR: Voiceover incomplete: {ready_count}/{len(new_clips)} narration block(s) ready."
            )
            if any(result.fatal for result in results):
                self.append_recap_log(
                    "ERROR: Stopped after a fatal Orpheus failure; completed narration remains cached."
                )
            return False
        self.append_recap_log(
            f"Voiceover complete: {ready_count}/{len(new_clips)} narration block(s)."
        )
        return True

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
            context = self._active_recap_artifact_context()
            inputs = self._active_recap_inputs()
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
            speed=getattr(self, "recap_speed", 1.5),
            output_dir=context.voiceover_dir,
            manifest_path=context.voiceover_manifest_path,
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
