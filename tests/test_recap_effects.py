from pathlib import Path

from editor_asset_plan import load_editor_asset_plan, save_editor_asset_plan
from recap_media.effects import (
    RECAP_EFFECTS_SCHEMA_VERSION,
    RECAP_TIME_BASIS,
    build_recap_effects_plan,
    load_recap_effects,
    recap_base_timeline_words,
    source_audio_insert_windows,
    write_recap_effects_plan,
)


def _sequence():
    return {
        "total_duration_seconds": 6.0,
        "segments": [
            {
                "segment_id": "VO_001",
                "order": 1,
                "narration_duration_seconds": 5.0,
                "timeline_duration_seconds": 6.0,
                "presentation_hint": "narration_over_source",
                "shots": [
                    {
                        "timeline_start_seconds": 0.0,
                        "timeline_end_seconds": 2.0,
                        "timeline_duration_seconds": 2.0,
                    },
                    {
                        "timeline_start_seconds": 2.0,
                        "timeline_end_seconds": 3.0,
                        "timeline_duration_seconds": 1.0,
                        "source_audio_insert": True,
                        "narration_pause_offset_seconds": 2.0,
                    },
                    {
                        "timeline_start_seconds": 3.0,
                        "timeline_end_seconds": 6.0,
                        "timeline_duration_seconds": 3.0,
                    },
                ],
            }
        ],
    }


def _captions():
    return {
        "schema_version": 1,
        "segments": [
            {
                "segment_id": "VO_001",
                "words": [
                    {"text": "story", "start": 0.2, "end": 0.5},
                    {"text": "setup", "start": 0.6, "end": 1.0},
                    {"text": "crazy", "start": 2.1, "end": 2.6},
                    {"text": "return", "start": 3.2, "end": 3.6},
                ],
            }
        ],
    }


def _portrait_plan():
    return {"content_y": 656, "content_height": 608}


def _prepared_emoji(events):
    return [
        {
            "id": event["id"],
            "emoji": event["emoji"],
            "path": Path("assets/emoji/1f602.png"),
            "start": event["start"],
            "end": event["end"],
            "matched_word": event.get("matched_word", ""),
            "position_x": event.get("position_x"),
            "position_y": event.get("position_y"),
            "scale": event.get("scale", 1.0),
        }
        for event in events
    ]


def test_recap_words_use_base_timeline_and_pause_for_source_dialogue():
    words = recap_base_timeline_words(_sequence(), _captions())

    assert [(word["word"], word["start"], word["end"]) for word in words] == [
        ("story", 0.2, 0.5),
        ("setup", 0.6, 1.0),
        ("crazy", 3.1, 3.6),
        ("return", 4.2, 4.6),
    ]


def test_recap_adapter_reuses_shared_entity_schemas_and_protects_dialogue(monkeypatch):
    monkeypatch.setattr("recap_media.effects.prepare_emoji_events", _prepared_emoji)
    monkeypatch.setattr(
        "recap_media.effects.prepare_events",
        lambda events, _energy, _assets, _warnings: (
            [
                {
                    **event,
                    "duration": 0.2,
                    "volume": 0.2,
                    "asset_path": "assets/sfx/oxidvideos-ding-editing-sfx-414336.mp3",
                }
                for event in events
            ],
            [],
        ),
    )

    plan = build_recap_effects_plan(
        _sequence(), _captions(), _portrait_plan(), energy="PUNCHY"
    )

    assert plan["schema_version"] == RECAP_EFFECTS_SCHEMA_VERSION
    assert plan["time_basis"] == RECAP_TIME_BASIS
    assert plan["visual_fx"]["events"]
    assert plan["visual_fx"]["motion_events"]
    for kind in ("SFX", "EMOJI"):
        for clip in plan["automatic_editor_clips"][kind]:
            assert clip["kind"] == kind
            assert clip["time_basis"] == RECAP_TIME_BASIS
            assert clip["origin"] == "automatic"

    protected = source_audio_insert_windows(_sequence())
    for event in plan["visual_fx"]["events"] + plan["visual_fx"]["motion_events"]:
        assert not any(event["start"] < end and event["end"] > start for start, end in protected)
    for clip in plan["automatic_editor_clips"]["SFX"] + plan["automatic_editor_clips"]["EMOJI"]:
        assert not any(clip["start"] < end and clip["end"] > start for start, end in protected)


def test_recap_plan_keeps_manual_entities_and_disabled_entities_do_not_render(tmp_path):
    effects_path = tmp_path / "effects_plan.json"
    editor_plan_path = tmp_path / "editor_asset_plan.json"
    normal_plan_path = tmp_path / "normal_editor_asset_plan.json"
    normal_plan_path.write_text('{"version": 1, "clips": [{"id": "normal_short"}]}\n', encoding="utf-8")
    normal_before = normal_plan_path.read_text(encoding="utf-8")
    sfx_path = Path("assets/sfx/oxidvideos-ding-editing-sfx-414336.mp3").resolve()
    effects = {
        "schema_version": RECAP_EFFECTS_SCHEMA_VERSION,
        "time_basis": RECAP_TIME_BASIS,
        "base_timeline_duration_seconds": 8.0,
        "visual_fx": {"events": [{"start": 1.0, "end": 1.4, "active": False}]},
        "automatic_editor_clips": {
            "SFX": [
                {
                    "id": "recap_sfx_auto_01",
                    "kind": "SFX",
                    "time_basis": RECAP_TIME_BASIS,
                    "start": 1.0,
                    "end": 1.2,
                    "asset_path": str(sfx_path),
                    "category": "ding",
                    "active": True,
                    "origin": "automatic",
                }
            ],
            "EMOJI": [],
        },
    }
    write_recap_effects_plan(effects, effects_path=effects_path, editor_plan_path=editor_plan_path)

    editor_plan = load_editor_asset_plan(editor_plan_path)
    editor_plan["clips"][0]["active"] = False
    editor_plan["clips"][0]["manual_override"] = True
    editor_plan["clips"].append(
        {
            "id": "manual_sfx",
            "kind": "SFX",
            "time_basis": RECAP_TIME_BASIS,
            "start": 4.0,
            "end": 4.2,
            "asset_path": str(sfx_path),
            "category": "ding",
            "active": False,
            "manual_override": True,
        }
    )
    save_editor_asset_plan(editor_plan, editor_plan_path)

    write_recap_effects_plan(effects, effects_path=effects_path, editor_plan_path=editor_plan_path)
    persisted = load_editor_asset_plan(editor_plan_path)
    assert any(clip["id"] == "manual_sfx" for clip in persisted["clips"])

    renderable = load_recap_effects(effects_path=effects_path, editor_plan_path=editor_plan_path)
    assert renderable["visual_fx_events"] == []
    assert renderable["sfx_events"] == []
    assert normal_plan_path.read_text(encoding="utf-8") == normal_before
