"""Regression coverage for the AI Recap Generate Recap controller handoff."""

from __future__ import annotations

from pathlib import Path

import gui_app.mixins.recap as recap_module
from recap_media.artifacts import RecapArtifactContext
from recap_media.loader import RecapInputs
from recap_media.voiceover import SegmentSynthesisResult


class _Button:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, value):
        self.enabled = value


class _RecapWindow(recap_module.RecapMixin):
    def __init__(self, context: RecapArtifactContext, inputs: RecapInputs):
        self.video_path = context.source_video
        self.recap_artifact_context = context
        self.recap_active_inputs = inputs
        self.recap_sequence = {"segments": [{"segment_id": "placeholder"}]}
        self.recap_script_valid = True
        self.recap_voice = "tara"
        self.recap_speed = 1.5
        self.generate_recap_sequence_button = _Button()
        self.generate_recap_voiceover_button = _Button()
        self.editor_asset_plan = {"version": 1, "clips": []}
        self.log_lines: list[str] = []
        self.saved_plan = False
        self.timeline_refreshed = False

    def append_recap_log(self, message: str):
        self.log_lines.append(message)

    def ensure_current_editor_asset_context(self, clear_on_change: bool):
        assert clear_on_change is True

    def save_editor_asset_plan_state(self):
        self.saved_plan = True

    def refresh_editor_asset_timeline(self):
        self.timeline_refreshed = True

    def refresh_recap_voiceover_list(self):
        pass


def _context(tmp_path: Path) -> RecapArtifactContext:
    root = tmp_path / "recap_source_bound"
    voiceover_dir = root / "voiceover"
    source = (tmp_path / "episode.mkv").resolve()
    return RecapArtifactContext(
        root=root,
        source_video=source,
        episode_identity_path=root / "episode_identity.json",
        verified_story_map_path=root / "verified_story_map.json",
        recap_script_path=root / "recap_script.json",
        recap_sequence_path=root / "recap_sequence.json",
        voiceover_dir=voiceover_dir,
        voiceover_manifest_path=voiceover_dir / "voiceover_manifest.json",
        pasted_script_path=root / "external_recap_script_paste.json",
    )


def _inputs() -> RecapInputs:
    return RecapInputs(
        episode_identity={"title": "Example Episode"},
        verified_story_map={"beats": []},
        recap_script={
            "schema_version": 2,
            "segments": [
                {
                    "segment_id": "N_001",
                    "block_type": "narration",
                    "text": "The first narrated event.",
                    "beat_ids": ["B001"],
                    "presentation_hint": "narration_over_source",
                },
                {
                    "segment_id": "S_001",
                    "block_type": "source_moment",
                    "text": "",
                    "beat_ids": ["B001"],
                    "presentation_hint": "original_dialogue",
                },
                {
                    "segment_id": "N_002",
                    "block_type": "narration",
                    "text": "The second narrated event.",
                    "beat_ids": ["B002"],
                    "presentation_hint": "narration_over_source",
                },
            ],
        },
    )


def test_generate_recap_continues_from_voiceover_to_final_render():
    calls: list[str] = []

    class _Window:
        def append_recap_log(self, message: str):
            calls.append(message)

        def generate_recap_sequence(self):
            calls.append("sequence")
            return True

        def generate_recap_voiceover(self):
            calls.append("voiceover")
            return True

        def generate_recap_final_render(self):
            calls.append("render")
            return True

    window = _Window()

    assert recap_module.RecapMixin.generate_recap(window) is True
    assert calls == ["Generating recap sequence...", "sequence", "voiceover", "render"]


def test_final_render_uses_the_active_root_and_only_narration_wavs(monkeypatch, tmp_path):
    context = _context(tmp_path)
    inputs = _inputs()
    window = _RecapWindow(context, inputs)
    window.recap_sequence = {
        "segments": [
            {"segment_id": "N_001", "order": 1, "shots": []},
            {"segment_id": "S_001", "order": 2, "shots": []},
            {"segment_id": "N_002", "order": 3, "shots": []},
        ]
    }
    window.editor_asset_plan = {
        "version": 1,
        "clips": [
            {"id": "N_001", "kind": "VOICEOVER", "active": True},
            {"id": "N_002", "kind": "VOICEOVER", "active": True},
        ],
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(recap_module, "resolve_recap_source_video", lambda identity: context.source_video)
    monkeypatch.setattr(recap_module, "build_portrait_framing_plan_for_video", lambda path: {"source": path})
    monkeypatch.setattr(recap_module, "write_portrait_framing_plan", lambda plan, path: captured.setdefault("portrait_path", path))

    def _captions(segments, **kwargs):
        captured["caption_wavs"] = kwargs["wav_paths_by_segment"]
        return {"segments": []}

    monkeypatch.setattr(recap_module, "build_narration_captions", _captions)
    monkeypatch.setattr(recap_module, "write_narration_captions", lambda captions, path: captured.setdefault("captions_path", path))
    monkeypatch.setattr(recap_module, "write_narration_captions_ass_file", lambda captions, clips, path, portrait: captured.setdefault("ass_path", path))
    monkeypatch.setattr(recap_module, "build_duck_plan", lambda sequence: {"narration_keyframes": []})
    monkeypatch.setattr(recap_module, "write_duck_plan", lambda plan, path: captured.setdefault("duck_path", path))

    def _render(identity, sequence, clips, portrait, duck, **kwargs):
        captured["render_identity"] = identity
        captured["render_sequence"] = sequence
        captured["render_clips"] = clips
        captured["render_kwargs"] = kwargs
        return kwargs["output_path"]

    monkeypatch.setattr(recap_module, "render_recap", _render)

    assert window.generate_recap_final_render() is True
    assert set(captured["caption_wavs"]) == {"N_001", "N_002"}
    assert captured["portrait_path"] == context.portrait_framing_plan_path
    assert captured["captions_path"] == context.narration_captions_path
    assert captured["ass_path"] == context.narration_captions_ass_path
    assert captured["duck_path"] == context.audio_duck_plan_path
    assert captured["render_kwargs"] == {
        "captions_ass_path": context.narration_captions_ass_path,
        "output_path": context.final_recap_path,
        "recap_effects": {},
        "voiceover_dir": context.voiceover_dir,
    }
    assert [segment["segment_id"] for segment in captured["render_sequence"]["segments"]] == [
        "N_001",
        "S_001",
        "N_002",
    ]


def test_final_render_failure_is_logged_without_touching_cached_voiceover(monkeypatch, tmp_path):
    context = _context(tmp_path)
    context.voiceover_dir.mkdir(parents=True)
    cached_wav = context.voiceover_dir / "N_001.wav"
    cached_wav.write_bytes(b"cached voiceover")
    window = _RecapWindow(context, _inputs())
    window.recap_sequence = {"segments": [{"segment_id": "N_001", "order": 1, "shots": []}]}
    window.editor_asset_plan = {
        "version": 1,
        "clips": [{"id": "N_001", "kind": "VOICEOVER", "active": True}],
    }

    monkeypatch.setattr(recap_module, "resolve_recap_source_video", lambda identity: context.source_video)
    monkeypatch.setattr(recap_module, "build_portrait_framing_plan_for_video", lambda path: {})
    monkeypatch.setattr(recap_module, "write_portrait_framing_plan", lambda *args: None)
    monkeypatch.setattr(recap_module, "build_narration_captions", lambda *args, **kwargs: {"segments": []})
    monkeypatch.setattr(recap_module, "write_narration_captions", lambda *args: None)
    monkeypatch.setattr(recap_module, "write_narration_captions_ass_file", lambda *args: None)
    monkeypatch.setattr(recap_module, "build_duck_plan", lambda sequence: {"narration_keyframes": []})
    monkeypatch.setattr(recap_module, "write_duck_plan", lambda *args: None)
    monkeypatch.setattr(
        recap_module,
        "render_recap",
        lambda *args, **kwargs: (_ for _ in ()).throw(recap_module.RecapRenderError("ffmpeg failed")),
    )

    assert window.generate_recap_final_render() is False
    assert cached_wav.read_bytes() == b"cached voiceover"
    assert any("ERROR: Recap render failed: ffmpeg failed" in line for line in window.log_lines)


def test_v2_generate_voiceover_uses_active_root_and_only_narration(monkeypatch, tmp_path):
    context = _context(tmp_path)
    inputs = _inputs()
    window = _RecapWindow(context, inputs)
    captured: dict[str, object] = {}

    class _Provider:
        def readiness(self):
            return {"state": "online"}

    def _synthesize(provider, segments, **kwargs):
        assert isinstance(provider, _Provider)
        captured["output_dir"] = kwargs["output_dir"]
        captured["manifest_path"] = kwargs["manifest_path"]
        callback = kwargs["on_segment_start"]
        narration = [
            segment
            for segment in segments
            if segment.get("block_type") != "source_moment"
        ]
        captured["narration_ids"] = [segment["segment_id"] for segment in narration]
        for index, segment in enumerate(narration, start=1):
            callback(segment["segment_id"], index, len(narration))
        results = [
            SegmentSynthesisResult(
                segment_id=segment["segment_id"],
                wav_path=None,
                duration_seconds=float(index + 1),
                cache_hit=False,
            )
            for index, segment in enumerate(narration)
        ]
        completion = kwargs["on_segment_complete"]
        for index, result in enumerate(results, start=1):
            completion(result, index, len(results))
        return results

    sequence_paths: list[Path] = []
    monkeypatch.setattr(recap_module, "OrpheusProvider", _Provider)
    monkeypatch.setattr(recap_module, "synthesize_segments", _synthesize)
    monkeypatch.setattr(
        recap_module,
        "assemble_sequence",
        lambda *args, **kwargs: {"segments": [], "total_duration_seconds": 3.0},
    )
    monkeypatch.setattr(recap_module, "interweave_original_dialogue", lambda sequence, *args, **kwargs: sequence)
    monkeypatch.setattr(
        recap_module,
        "write_recap_sequence",
        lambda sequence, path: sequence_paths.append(path),
    )

    assert window.generate_recap_voiceover() is True
    assert captured["narration_ids"] == ["N_001", "N_002"]
    assert captured["output_dir"] == context.voiceover_dir
    assert captured["manifest_path"] == context.voiceover_manifest_path
    assert sequence_paths == [context.recap_sequence_path]
    assert any("Narration 1/2: N_001 processing" in line for line in window.log_lines)
    assert any("Narration 2/2: N_002 processing" in line for line in window.log_lines)
    assert any("Narration 1/2: N_001 complete" in line for line in window.log_lines)
    assert any("Narration 2/2: N_002 complete" in line for line in window.log_lines)
    assert any("Voiceover complete: 2/2 narration block(s)." in line for line in window.log_lines)


def test_voiceover_exception_is_logged_and_returned_to_the_controller(monkeypatch, tmp_path):
    context = _context(tmp_path)
    window = _RecapWindow(context, _inputs())

    class _Provider:
        def readiness(self):
            return {"state": "online"}

    monkeypatch.setattr(recap_module, "OrpheusProvider", _Provider)
    monkeypatch.setattr(
        recap_module,
        "synthesize_segments",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("server disconnected")),
    )

    assert window.generate_recap_voiceover() is False
    assert any("ERROR: Voiceover synthesis failed: server disconnected" in line for line in window.log_lines)
    assert window.generate_recap_voiceover_button.enabled is True
    assert window.generate_recap_sequence_button.enabled is True


def test_fatal_tts_timeout_is_visible_without_claiming_orpheus_is_offline(monkeypatch, tmp_path):
    context = _context(tmp_path)
    window = _RecapWindow(context, _inputs())

    class _Provider:
        def readiness(self):
            return {"state": "online"}

    def _timed_out(*args, **kwargs):
        result = SegmentSynthesisResult(
            segment_id="N_001",
            wav_path=None,
            duration_seconds=0.0,
            cache_hit=False,
            error="Orpheus-FastAPI timed out after 600s of active synthesis.",
            fatal=True,
        )
        kwargs["on_segment_start"]("N_001", 1, 2)
        kwargs["on_segment_complete"](result, 1, 2)
        return [result]

    monkeypatch.setattr(recap_module, "OrpheusProvider", _Provider)
    monkeypatch.setattr(recap_module, "synthesize_segments", _timed_out)
    monkeypatch.setattr(
        recap_module,
        "assemble_sequence",
        lambda *args, **kwargs: {"segments": [], "total_duration_seconds": 0.0},
    )
    monkeypatch.setattr(recap_module, "interweave_original_dialogue", lambda sequence, *args, **kwargs: sequence)
    monkeypatch.setattr(recap_module, "write_recap_sequence", lambda *args, **kwargs: None)

    assert window.generate_recap_voiceover() is False
    assert any("N_001: Orpheus-FastAPI timed out after 600s" in line for line in window.log_lines)
    assert any("Stopped after a fatal Orpheus failure" in line for line in window.log_lines)
    assert not any("isn't reachable" in line for line in window.log_lines)
