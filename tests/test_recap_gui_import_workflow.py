from __future__ import annotations

import json
from pathlib import Path

import gui_app.mixins.recap as recap_module
from recap_media.artifacts import RecapArtifactContext
from recap_media.loader import RecapInputError
from recap_media.orpheus_provider import DEFAULT_VOICE


class _Button:
    def __init__(self):
        self.enabled = None
        self.checked = False
        self.visible = None

    def setEnabled(self, value):
        self.enabled = value

    def setChecked(self, value):
        self.checked = value

    def setVisible(self, value):
        self.visible = value


class _ModeStack:
    def __init__(self, *pages):
        self.pages = pages
        self.current = None

    def setCurrentWidget(self, page):
        self.current = page
        for candidate in self.pages:
            candidate.setVisible(candidate is page)


class _Style:
    def unpolish(self, _widget):
        pass

    def polish(self, _widget):
        pass


class _Label:
    def __init__(self):
        self.text = ""
        self.properties = {}

    def setText(self, text):
        self.text = text

    def setProperty(self, key, value):
        self.properties[key] = value

    def style(self):
        return _Style()


class _Preview:
    def __init__(self):
        self.items = []

    def clear(self):
        self.items.clear()

    def addItem(self, item):
        self.items.append(item)


class _Combo:
    def __init__(self):
        self.value = ""

    def blockSignals(self, _value):
        pass

    def setCurrentText(self, value):
        self.value = value


class _Settings:
    def __init__(self):
        self.values = {}

    def setValue(self, key, value):
        self.values[key] = value


class _Timeline:
    def __init__(self):
        self.focused = False

    def setFocus(self):
        self.focused = True


class _RecapWindow(recap_module.RecapMixin):
    def __init__(self):
        self.video_path = Path("accepted_episode.mkv")
        self.recap_frame = _Button()
        self.recap_button = _Button()
        self.standard_short_button = _Button()
        self.recap_status_label = _Label()
        self.recap_source_label = _Label()
        self.recap_episode_label = _Label()
        self.recap_script_preview = _Preview()
        self.recap_script_source_combo = _Combo()
        self.generate_recap_sequence_button = _Button()
        self.generate_recap_voiceover_button = _Button()
        self.recap_open_editor_button = _Button()
        self.settings = _Settings()
        self.recap_script_source = "local"
        self.recap_active_script = None
        self.recap_active_inputs = None
        self.recap_external_script_path = None
        self.recap_script_valid = False
        self.recap_sequence = None
        self.recap_speed = 1.5
        self.recap_voice = DEFAULT_VOICE
        self.log_lines = []
        self.editor_refreshes = 0
        self.timeline = _Timeline()
        self.normal_short_state = {"selection": (12.0, 20.0)}

    def append_recap_log(self, message):
        self.log_lines.append(message)

    def refresh_recap_voiceover_list(self):
        pass

    def refresh_editor_asset_timeline(self):
        self.editor_refreshes += 1


def _identity():
    return {
        "schema_version": 1,
        "title": "Example Show",
        "episode_title": "The Letter",
        "media_type": "tv_episode",
        "season": 1,
        "episode": 1,
        "confidence": 0.9,
    }


def _story_map():
    return {
        "schema_version": 1,
        "beats": [
            {
                "beat_id": "B001",
                "order": 1,
                "summary": "The letter is discovered.",
                "importance": 0.8,
                "source_evidence": [
                    {"start": 12.0, "end": 18.0, "type": "dialogue", "confidence": 0.9}
                ],
            }
        ],
    }


def _external_script():
    return {
        "schema_version": 2,
        "script_source": "external",
        "segments": [
            {
                "segment_id": "N_001",
                "order": 1,
                "block_type": "narration",
                "text": "The letter changes everything.",
                "beat_ids": ["B001"],
                "presentation_hint": "narration_over_source",
                "importance": 0.8,
                "candidate_visuals": [],
                "original_dialogue_candidates": [],
            },
            {
                "segment_id": "S_001",
                "order": 2,
                "block_type": "source_moment",
                "text": "",
                "beat_ids": ["B001"],
                "presentation_hint": "original_dialogue",
                "importance": 0.9,
                "candidate_visuals": [],
                "original_dialogue_candidates": [
                    {"start": 12.0, "end": 18.0, "score": 0.9, "reason": "Accepted line."}
                ],
            },
            {
                "segment_id": "N_002",
                "order": 3,
                "block_type": "narration",
                "text": "Now the rival has to answer for it.",
                "beat_ids": ["B001"],
                "presentation_hint": "narration_over_source",
                "importance": 0.8,
                "candidate_visuals": [],
                "original_dialogue_candidates": [],
            },
        ],
    }


def _patch_external_context(monkeypatch, tmp_path, script=None):
    selected_script = script or _external_script()
    calls = []
    voiceover_dir = tmp_path / "voiceover"
    context = RecapArtifactContext(
        root=tmp_path,
        source_video=Path("accepted_episode.mkv").resolve(),
        episode_identity_path=tmp_path / "episode_identity.json",
        verified_story_map_path=tmp_path / "verified_story_map.json",
        recap_script_path=tmp_path / "recap_script.json",
        recap_sequence_path=tmp_path / "recap_sequence.json",
        voiceover_dir=voiceover_dir,
        voiceover_manifest_path=voiceover_dir / "voiceover_manifest.json",
        pasted_script_path=tmp_path / "external_recap_script_paste.json",
    )
    monkeypatch.setattr(
        recap_module,
        "resolve_recap_artifact_context",
        lambda _source: context,
    )
    monkeypatch.setattr(recap_module, "load_episode_identity", lambda _path: _identity())
    monkeypatch.setattr(recap_module, "load_verified_story_map", lambda _path: _story_map())

    def _load_external(path, *, episode_identity, verified_story_map):
        calls.append((Path(path), episode_identity, verified_story_map))
        return selected_script

    monkeypatch.setattr(recap_module, "load_external_recap_script", _load_external)
    return calls


def test_standard_short_and_recap_switch_preserve_normal_short_state():
    window = _RecapWindow()

    window.set_recap_mode("recap")
    window.set_standard_short_mode()

    assert window.recap_frame.visible is False
    assert window.recap_mode == "standard"
    assert window.video_path == Path("accepted_episode.mkv")
    assert window.normal_short_state == {"selection": (12.0, 20.0)}


def test_mode_stack_shows_only_the_active_workflow_page():
    window = _RecapWindow()
    window.standard_short_mode_frame = _Button()
    window.recap_scroll_area = _Button()
    window.mode_specific_stack = _ModeStack(
        window.standard_short_mode_frame,
        window.recap_scroll_area,
    )

    window.set_standard_short_mode()
    assert window.standard_short_mode_frame.visible is True
    assert window.recap_scroll_area.visible is False

    window.set_recap_mode("recap")
    assert window.standard_short_mode_frame.visible is False
    assert window.recap_scroll_area.visible is True

    window.set_standard_short_mode()
    assert window.standard_short_mode_frame.visible is True
    assert window.recap_scroll_area.visible is False


def test_local_ai_selection_clears_only_recap_script_state():
    window = _RecapWindow()
    window.recap_script_source = "external"
    window.recap_active_script = _external_script()
    window.recap_active_inputs = object()

    window.select_local_recap_script()

    assert window.recap_script_source == "local"
    assert window.recap_active_script is None
    assert window.recap_active_inputs is None
    assert window.settings.values[recap_module.RECAP_SCRIPT_SOURCE] == "local"
    assert window.video_path == Path("accepted_episode.mkv")


def test_import_invokes_external_backend_and_persists_active_script(monkeypatch, tmp_path):
    calls = _patch_external_context(monkeypatch, tmp_path)
    window = _RecapWindow()
    source = tmp_path / "external.json"
    source.write_text("{}", encoding="utf-8")

    assert window.import_external_recap_script(source) is True

    assert calls[0][0] == source
    assert window.recap_script_source == "external"
    assert window.recap_script_valid is True
    persisted = json.loads((tmp_path / "recap_script.json").read_text(encoding="utf-8"))
    assert persisted["segments"][0]["text"] == "The letter changes everything."


def test_paste_uses_the_same_external_import_backend(monkeypatch, tmp_path):
    calls = _patch_external_context(monkeypatch, tmp_path)
    window = _RecapWindow()
    pasted = '{"schema_version": 2, "segments": []}'

    assert window.import_pasted_recap_script(pasted) is True

    pasted_path = tmp_path / "external_recap_script_paste.json"
    assert pasted_path.read_text(encoding="utf-8") == pasted
    assert calls[0][0] == pasted_path


def test_valid_external_import_reports_block_counts_and_ordered_preview(monkeypatch, tmp_path):
    _patch_external_context(monkeypatch, tmp_path)
    window = _RecapWindow()

    assert window.import_external_recap_script(tmp_path / "external.json") is True

    assert "2 narration / 1 source moments" in window.recap_status_label.text
    assert [item.split("  ")[1] for item in window.recap_script_preview.items] == [
        "N_001",
        "S_001",
        "N_002",
    ]
    assert "The letter changes everything." in window.recap_script_preview.items[0]


def test_invalid_external_import_surfaces_readable_error(monkeypatch, tmp_path):
    _patch_external_context(monkeypatch, tmp_path)
    window = _RecapWindow()
    monkeypatch.setattr(
        recap_module,
        "load_external_recap_script",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RecapInputError("Unknown beat B999 in block S_003.")
        ),
    )

    assert window.import_external_recap_script(tmp_path / "invalid.json") is False

    assert window.recap_status_label.text == "Unknown beat B999 in block S_003."
    assert window.log_lines[-1] == "ERROR: Unknown beat B999 in block S_003."


def test_external_import_uses_only_the_external_backend(monkeypatch, tmp_path):
    calls = _patch_external_context(monkeypatch, tmp_path)
    window = _RecapWindow()

    assert window.import_external_recap_script(tmp_path / "external.json") is True

    assert len(calls) == 1
    assert window.recap_active_script["script_source"] == "external"


def test_active_external_source_and_one_point_five_speed_are_preserved():
    window = _RecapWindow()

    window._set_recap_script_source("external")
    window.recap_speed_changed("1.50x")

    assert window.recap_script_source_combo.value == "Import AI Script"
    assert window.recap_speed == 1.5
    assert window.recap_voice == "tara"
    assert window.settings.values[recap_module.RECAP_SPEED] == 1.5


def test_recap_pitch_settings_are_persisted_separately_from_overall_speed():
    window = _RecapWindow()

    window.recap_narration_pitch_changed(2.0)
    window.recap_source_pitch_changed(1.8)

    assert window.recap_narration_pitch_semitones == 2.0
    assert window.settings.values[recap_module.RECAP_NARRATION_PITCH_SEMITONES] == 2.0
    assert window.recap_source_pitch_semitones == 1.8
    assert window.settings.values[recap_module.RECAP_SOURCE_PITCH_SEMITONES] == 1.8


def test_empty_session_does_not_claim_stale_recap_identity():
    window = _RecapWindow()
    window.video_path = None

    window.refresh_recap_status()

    assert window.recap_episode_label.text == "Episode: not checked"
    assert window.recap_status_label.text == "Load a source video to begin AI Recap."
    assert window.generate_recap_sequence_button.enabled is False


def test_open_in_editor_requires_sequence_and_uses_existing_editor():
    window = _RecapWindow()

    window.open_recap_in_editor()
    assert window.editor_refreshes == 0

    window.recap_sequence = {"segments": []}
    window.open_recap_in_editor()

    assert window.editor_refreshes == 1
    assert window.timeline.focused is True
