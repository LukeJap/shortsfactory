from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import emoji_planner
import sfx_engine
from editor_asset_plan import load_editor_asset_plan, save_editor_asset_plan
from gui_app.mixins.editor_assets import EditorAssetsMixin
from recap_media.effects import RECAP_TIME_BASIS


def _plan(source: Path, *, kind: str, clip_id: str) -> dict:
    return {
        "version": 1,
        "source_video": str(source),
        "selection_start": 0.0,
        "selection_end": 85.543,
        "clips": [
            {
                "id": clip_id,
                "kind": kind,
                "start": 4.0,
                "end": 5.0,
                "active": True,
                "manual_override": True,
                "locked": True,
            }
        ],
    }


def test_recap_generation_context_uses_recap_plan_and_final_timeline(tmp_path):
    source = tmp_path / "final_recap.mp4"
    plan_path = tmp_path / "editor_asset_plan.json"
    captions_path = tmp_path / "recap_captions.json"
    captions_path.write_text("{}", encoding="utf-8")
    class _Window(EditorAssetsMixin):
        pass

    window = _Window()
    window.recap_editor_asset_context = (str(source), 0.0, 85.543)
    window.recap_editor_asset_plan_path = plan_path
    window.recap_artifact_context = SimpleNamespace(
        recap_caption_plan_path=captions_path
    )

    context = EditorAssetsMixin.editor_asset_generation_context(window)
    transcript = EditorAssetsMixin.editor_asset_generation_transcript_path(window)

    assert context == (plan_path, str(source), 0.0, 85.543, RECAP_TIME_BASIS)
    assert transcript == captions_path


def test_recap_context_is_not_retargeted_to_editor_base_media(tmp_path):
    source = tmp_path / "final_recap.mp4"
    editor_base = tmp_path / "final_recap_editor_base.mp4"

    class _Window(EditorAssetsMixin):
        def save_editor_asset_plan_state(self):
            pass

        def refresh_editor_asset_timeline(self):
            pass

    window = _Window()
    window.video_path = editor_base
    window.start_ms = 0
    window.end_ms = 85_543
    window.recap_editor_asset_context = (str(source), 0.0, 85.543)
    window.editor_asset_plan = _plan(source, kind="SFX", clip_id="sfx_manual")
    window.selected_sfx_clip_id = "sfx_manual"
    window.selected_emoji_clip_id = None

    window.ensure_current_editor_asset_context(clear_on_change=True)

    assert window.editor_asset_plan["source_video"] == str(source)
    assert window.editor_asset_plan["clips"][0]["id"] == "sfx_manual"


def test_recap_caption_cues_are_valid_emoji_planner_words(tmp_path):
    captions_path = tmp_path / "recap_captions.json"
    captions_path.write_text(
        json.dumps(
            {
                "time_basis": RECAP_TIME_BASIS,
                "cues": [
                    {"start": 1.0, "end": 1.4, "text": "shock"},
                    {"start": 90.0, "end": 90.4, "text": "outside"},
                ],
            }
        ),
        encoding="utf-8",
    )

    words = emoji_planner.words_in_selection(captions_path, 0.0, 85.543)

    assert words == [{"start": 1.0, "end": 1.4, "text": "shock", "word": "shock"}]


def test_emoji_generation_writes_active_recap_plan_and_preserves_manual_clip(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "final_recap.mp4"
    plan_path = tmp_path / "editor_asset_plan.json"
    asset_path = tmp_path / "emoji.png"
    asset_path.write_bytes(b"png")
    save_editor_asset_plan(
        _plan(source, kind="EMOJI", clip_id="emoji_manual"),
        plan_path,
    )
    monkeypatch.setattr(emoji_planner, "resolve_event_asset", lambda _event: asset_path)

    result = emoji_planner.write_editor_emoji_plan(
        [{"emoji": "test", "start": 12.0, "end": 13.0, "position_x": 0.5, "position_y": 0.4}],
        0.0,
        85.543,
        editor_plan_path=plan_path,
        source_video=str(source),
        time_basis=RECAP_TIME_BASIS,
    )

    clips = load_editor_asset_plan(plan_path)["clips"]
    assert result["event_count"] == 1
    assert {clip["id"] for clip in clips} == {"emoji_manual", "emoji_auto_01"}
    generated = next(clip for clip in clips if clip["id"] == "emoji_auto_01")
    assert generated["time_basis"] == RECAP_TIME_BASIS
    assert (generated["start"], generated["end"]) == (12.0, 13.0)


def test_sfx_generation_writes_active_recap_plan_and_preserves_manual_clip(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "final_recap.mp4"
    plan_path = tmp_path / "editor_asset_plan.json"
    save_editor_asset_plan(
        _plan(source, kind="SFX", clip_id="sfx_manual"),
        plan_path,
    )
    prepared = {
        "id": "sfx_auto_01",
        "start": 20.0,
        "duration": 0.4,
        "category": "ding",
        "asset_path": str(tmp_path / "ding.wav"),
    }
    monkeypatch.setattr(sfx_engine, "editor_candidates", lambda *args, **kwargs: [prepared])
    monkeypatch.setattr(sfx_engine, "select_events", lambda candidates, *args, **kwargs: candidates)
    monkeypatch.setattr(sfx_engine, "index_local_assets", lambda: {})
    monkeypatch.setattr(sfx_engine, "local_asset_counts", lambda _assets: {})
    monkeypatch.setattr(sfx_engine, "prepare_events", lambda *args, **kwargs: ([prepared], []))
    monkeypatch.setattr(sfx_engine, "write_plan", lambda _plan: None)
    monkeypatch.setattr(sfx_engine, "event_from_sfx_clip", lambda clip: dict(clip))

    result = sfx_engine.write_editor_sfx_plan(
        0.0,
        85.543,
        "PUNCHY",
        "AUTO",
        editor_plan_path=plan_path,
        source_video=str(source),
        time_basis=RECAP_TIME_BASIS,
        transcript_path=tmp_path / "recap_captions.json",
    )

    clips = load_editor_asset_plan(plan_path)["clips"]
    assert result["event_count"] == 1
    assert {clip["id"] for clip in clips} == {"sfx_manual", "sfx_auto_01"}
    generated = next(clip for clip in clips if clip["id"] == "sfx_auto_01")
    assert generated["time_basis"] == RECAP_TIME_BASIS
    assert generated["start"] == 20.0


def test_sfx_transcript_candidates_accept_final_timeline_cues(tmp_path):
    captions_path = tmp_path / "recap_captions.json"
    captions_path.write_text(
        json.dumps(
            {
                "time_basis": RECAP_TIME_BASIS,
                "cues": [
                    {"start": 10.0, "end": 10.5, "text": "A sudden reveal"},
                    {"start": 100.0, "end": 100.5, "text": "outside"},
                ],
            }
        ),
        encoding="utf-8",
    )

    candidates = sfx_engine.transcript_editor_candidates(
        0.0,
        85.543,
        captions_path,
    )

    assert len(candidates) == 1
    assert candidates[0]["trigger"] == "A sudden reveal"
    assert 0.0 <= candidates[0]["start"] < 85.543
