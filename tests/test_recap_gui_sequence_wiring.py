"""
Focused regression test for a real Track B wiring defect: the GUI's
production "Generate Sequence" path (RecapMixin.generate_recap_sequence())
loaded a normalized verified_story_map via load_recap_inputs() but never
passed it into assemble_sequence(), so verified_story_map defaulted to
None and every shot silently fell back to recap_script.json's own
candidates only (candidate_origin always "recap_script", beat_id always
null, zero verified-story-map supplemental shots) -- with no test
failure, since the existing integration test called assemble_sequence()
directly with the map already threaded through by hand, never exercising
the actual GUI method.

This test calls the real, unmodified RecapMixin.generate_recap_sequence()
method itself (not a hand-wired call to assemble_sequence()) against a
lightweight stand-in for `self` -- no QApplication/Qt widgets needed,
since the method's only `self` dependencies are append_recap_log() and
generate_recap_voiceover_button.setEnabled(). Track A's own loader
(load_recap_inputs) and the disk-writing step (write_recap_sequence) are
monkeypatched at the module level so this stays a fast, disk-free unit
test; recap_media.sequence's real assemble_sequence()/
verified_candidates_for_segment() run unmodified.
"""

from __future__ import annotations

import gui_app.mixins.recap as recap_module
from recap_media.loader import RecapInputs


class _FakeButton:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, value):
        self.enabled = value


class _FakeRecapWindow(recap_module.RecapMixin):
    """Just enough of ShortsFactoryWindow for generate_recap_sequence()."""

    def __init__(self):
        self.log_lines: list[str] = []
        self.generate_recap_voiceover_button = _FakeButton()

    def append_recap_log(self, message: str):
        self.log_lines.append(message)


def _recap_script():
    return {
        "schema_version": 1,
        "target_duration_seconds": 120,
        "target_word_count": 50,
        "voice_style": "fast_story_recap",
        "segments": [
            {
                "segment_id": "VO_001",
                "order": 1,
                "text": "It all starts when a mysterious letter shows up under the door.",
                "beat_ids": ["B001"],
                "presentation_hint": "narration_over_source",
                "importance": 0.8,
                # Deliberately narrower than B001's verified source_evidence
                # below, so a genuinely new (not a duplicate range) verified
                # supplemental candidate is reachable only if
                # verified_story_map actually makes it into assemble_sequence().
                "candidate_visuals": [
                    {
                        "start": 14.1,
                        "end": 15.0,
                        "score": 0.7,
                        "reason": "Weak recap-script-only candidate.",
                    }
                ],
                "original_dialogue_candidates": [],
            }
        ],
    }


def _verified_story_map():
    return {
        "schema_version": 1,
        "beats": [
            {
                "beat_id": "B001",
                "order": 1,
                "summary": "The protagonist discovers a mysterious letter under the door.",
                "importance": 0.8,
                "source_evidence": [
                    {
                        "start": 40.0,
                        "end": 46.0,
                        "type": "local_video",
                        "confidence": 0.95,
                    }
                ],
            }
        ],
    }


def test_load_recap_inputs_loads_a_normalized_verified_story_map(monkeypatch, tmp_path):
    # Sanity check on the fixture data itself: a real load_recap_inputs()
    # call really does return a populated, non-empty verified_story_map --
    # not an assumption about the mixin's monkeypatch below.
    episode_identity_path = tmp_path / "episode_identity.json"
    story_map_path = tmp_path / "verified_story_map.json"
    script_path = tmp_path / "recap_script.json"

    import json

    episode_identity_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "title": "Test Episode",
                "media_type": "tv_episode",
                "confidence": 0.9,
                "season": 1,
                "episode": 1,
            }
        ),
        encoding="utf-8",
    )
    story_map_path.write_text(json.dumps(_verified_story_map()), encoding="utf-8")
    script_path.write_text(json.dumps(_recap_script()), encoding="utf-8")

    from recap_media.loader import load_recap_inputs

    inputs = load_recap_inputs(episode_identity_path, story_map_path, script_path)

    assert inputs.verified_story_map is not None
    assert inputs.verified_story_map["beats"][0]["beat_id"] == "B001"
    assert inputs.verified_story_map["beats"][0]["source_evidence"]


def test_production_generate_recap_sequence_passes_verified_story_map_through(monkeypatch):
    fake_inputs = RecapInputs(
        episode_identity={"title": "Test Episode"},
        verified_story_map=_verified_story_map(),
        recap_script=_recap_script(),
    )

    captured_verified_story_map = {}

    real_assemble_sequence = recap_module.assemble_sequence

    def _spy_assemble_sequence(
        recap_script,
        narration_durations=None,
        verified_story_map=None,
        source_video=None,
        transcript_cache_dir=None,
    ):
        captured_verified_story_map["value"] = verified_story_map
        return real_assemble_sequence(
            recap_script,
            narration_durations,
            verified_story_map=verified_story_map,
            source_video=source_video,
            transcript_cache_dir=transcript_cache_dir,
        )

    monkeypatch.setattr(recap_module, "load_recap_inputs", lambda: fake_inputs)
    monkeypatch.setattr(recap_module, "load_voiceover_durations", lambda: {})
    monkeypatch.setattr(recap_module, "assemble_sequence", _spy_assemble_sequence)
    monkeypatch.setattr(recap_module, "write_recap_sequence", lambda sequence: None)

    window = _FakeRecapWindow()
    window.generate_recap_sequence()

    # The production call site must pass the real loaded map through --
    # this is the exact defect: it previously defaulted to None.
    assert captured_verified_story_map["value"] is fake_inputs.verified_story_map
    assert captured_verified_story_map["value"] is not None

    # And because it did, a verified-story-map supplemental candidate is
    # actually reachable through the production path, not just through a
    # test that calls assemble_sequence() directly.
    shots = window.recap_sequence["segments"][0]["shots"]
    verified_shots = [s for s in shots if s["candidate_origin"] == "verified_story_map"]
    assert verified_shots, "no verified_story_map-origin shot reached the production path"
    assert verified_shots[0]["beat_id"] == "B001"

    assert window.generate_recap_voiceover_button.enabled is True
