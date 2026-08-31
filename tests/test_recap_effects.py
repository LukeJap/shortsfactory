import json
from pathlib import Path

import pytest

from editor_asset_plan import load_editor_asset_plan, save_editor_asset_plan
from recap_media.effects import (
    AI_RECAP_ORIGIN,
    RECAP_EFFECTS_SCHEMA_VERSION,
    RECAP_TIME_BASIS,
    build_recap_effects_plan,
    load_recap_effects,
    recap_base_timeline_words,
    recap_effects_for_render,
    recap_emoji_events,
    recap_timeline_blocks,
    retune_recap_emoji_plan,
    source_audio_insert_windows,
    write_recap_effects_plan,
)
from recap_media.timeline import recap_base_to_final_time, recap_final_duration_seconds


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


def _recap_script():
    return {
        "segments": [
            {"segment_id": "VO_001", "block_type": "narration", "beat_ids": ["B001", "B002"]},
        ]
    }


def _emoji_density_sequence():
    return {
        "total_duration_seconds": 32.0,
        "segments": [
            {
                "segment_id": "VO_001",
                "order": 1,
                "narration_duration_seconds": 32.0,
                "timeline_duration_seconds": 32.0,
                "presentation_hint": "narration_over_source",
                "beat_ids": ["B001"],
                "shots": [
                    {
                        "timeline_start_seconds": 0.0,
                        "timeline_end_seconds": 32.0,
                        "timeline_duration_seconds": 32.0,
                    }
                ],
            }
        ],
    }


def _emoji_density_captions():
    return {
        "schema_version": 1,
        "segments": [
            {
                "segment_id": "VO_001",
                "words": [
                    {"text": "first", "start": 0.0, "end": 0.3},
                    {"text": "second", "start": 8.0, "end": 8.3},
                    {"text": "third", "start": 16.0, "end": 16.3},
                    {"text": "fourth", "start": 24.0, "end": 24.3},
                ],
            }
        ],
    }


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


def test_recap_timeline_blocks_uses_sequence_windows_and_script_beats():
    assert recap_timeline_blocks(_sequence(), _recap_script()) == [
        {
            "block_id": "VO_001",
            "block_type": "narration",
            "start": 0.0,
            "end": 4.0,
            "beat_ids": ["B001", "B002"],
        }
    ]


def test_recap_effect_entities_use_final_output_timeline_and_stay_in_bounds(monkeypatch):
    monkeypatch.setattr("recap_media.effects.prepare_emoji_events", _prepared_emoji)
    monkeypatch.setattr(
        "recap_media.effects.prepare_events",
        lambda events, _energy, _assets, _warnings: (
            [{**event, "duration": 0.2, "asset_path": "assets/sfx/ding.mp3"} for event in events],
            [],
        ),
    )
    plan = build_recap_effects_plan(_sequence(), _captions(), _portrait_plan(), _recap_script())
    final_duration = recap_final_duration_seconds(_sequence()["total_duration_seconds"])

    assert plan["time_basis"] == RECAP_TIME_BASIS
    assert plan["final_timeline_duration_seconds"] == final_duration
    timed_families = [
        plan["visual_fx"]["events"],
        plan["visual_fx"]["motion_events"],
        plan["sfx"]["events"],
        plan["automatic_editor_clips"]["EMOJI"],
    ]
    for events in timed_families:
        for event in events:
            start = float(event["start"])
            end = float(event.get("end", start + event.get("duration", 0.0)))
            assert 0.0 <= start < end <= final_duration + 0.001


def test_final_timeline_effects_round_trip_to_existing_base_render_domain():
    final_start = recap_base_to_final_time(84.0)
    authoritative = {
        "time_basis": RECAP_TIME_BASIS,
        "visual_fx_events": [{"start": final_start, "end": final_start + 0.4}],
        "motion_events": [{"start": final_start, "end": final_start + 0.4}],
        "sfx_events": [{"start": final_start, "duration": 0.2}],
        "emoji_events": [{"start": final_start, "end": final_start + 1.0}],
    }

    render_effects = recap_effects_for_render(authoritative)

    assert final_start == pytest.approx(56.0)
    for family in ("visual_fx_events", "motion_events", "sfx_events", "emoji_events"):
        assert render_effects[family][0]["start"] == pytest.approx(84.0)
    assert render_effects["sfx_events"][0]["duration"] == pytest.approx(0.3)


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
        _sequence(), _captions(), _portrait_plan(), _recap_script(), energy="PUNCHY"
    )

    assert plan["schema_version"] == RECAP_EFFECTS_SCHEMA_VERSION
    assert plan["time_basis"] == RECAP_TIME_BASIS
    assert plan["visual_fx"]["events"]
    assert plan["visual_fx"]["motion_events"]
    for kind in ("SFX", "EMOJI"):
        for clip in plan["automatic_editor_clips"][kind]:
            assert clip["kind"] == kind
            assert clip["time_basis"] == RECAP_TIME_BASIS
            assert clip["origin"] == AI_RECAP_ORIGIN
            assert clip["block_id"] == "VO_001"
            assert clip["beat_ids"] == ["B001", "B002"]

    for event in plan["visual_fx"]["events"] + plan["visual_fx"]["motion_events"]:
        assert event["origin"] == AI_RECAP_ORIGIN
        assert event["id"]
        assert event["block_id"] == "VO_001"
        assert event["beat_ids"] == ["B001", "B002"]

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


def test_recap_plan_is_deterministic_and_preserves_manual_visual_events(tmp_path, monkeypatch):
    monkeypatch.setattr("recap_media.effects.prepare_emoji_events", _prepared_emoji)
    first = build_recap_effects_plan(_sequence(), _captions(), _portrait_plan(), _recap_script())
    second = build_recap_effects_plan(_sequence(), _captions(), _portrait_plan(), _recap_script())
    assert first["visual_fx"]["events"] == second["visual_fx"]["events"]
    assert first["visual_fx"]["motion_events"] == second["visual_fx"]["motion_events"]
    assert first["automatic_editor_clips"] == second["automatic_editor_clips"]

    effects_path = tmp_path / "effects_plan.json"
    editor_plan_path = tmp_path / "editor_asset_plan.json"
    manual_motion = {
        "id": "manual_motion",
        "start": 5.0,
        "end": 5.4,
        "movement": "punch_in",
        "active": False,
        "manual_override": True,
        "origin": "manual",
    }
    first["visual_fx"]["motion_events"].append(manual_motion)
    write_recap_effects_plan(first, effects_path=effects_path, editor_plan_path=editor_plan_path)
    write_recap_effects_plan(second, effects_path=effects_path, editor_plan_path=editor_plan_path)

    persisted = json.loads(effects_path.read_text(encoding="utf-8"))
    assert manual_motion in persisted["visual_fx"]["motion_events"]


def test_phase_6a_plan_is_not_loaded_for_rendering(tmp_path):
    effects_path = tmp_path / "effects_plan.json"
    editor_plan_path = tmp_path / "editor_asset_plan.json"
    plan = build_recap_effects_plan(_sequence(), _captions(), _portrait_plan(), _recap_script())
    write_recap_effects_plan(plan, effects_path=effects_path, editor_plan_path=editor_plan_path)

    assert load_recap_effects(effects_path=effects_path, editor_plan_path=editor_plan_path) == {
        "visual_fx_events": [],
        "motion_events": [],
        "sfx_events": [],
        "emoji_events": [],
        "time_basis": RECAP_TIME_BASIS,
    }


def test_explicit_render_opt_in_uses_shared_entities_without_promoting_plan(tmp_path, monkeypatch):
    effects_path = tmp_path / "effects_plan.json"
    editor_plan_path = tmp_path / "editor_asset_plan.json"
    sfx_path = Path("assets/sfx/oxidvideos-ding-editing-sfx-414336.mp3").resolve()
    emoji_path = Path("assets/emoji/23866-spongebob-dance.gif").resolve()
    plan = {
        "schema_version": RECAP_EFFECTS_SCHEMA_VERSION,
        "time_basis": RECAP_TIME_BASIS,
        "render_enabled": False,
        "base_timeline_duration_seconds": 8.0,
        "visual_fx": {
            "events": [
                {"id": "fx_01", "start": 1.25, "end": 1.75, "active": True},
                {"id": "fx_disabled", "start": 2.0, "end": 2.5, "active": False},
            ],
            "motion_events": [
                {"id": "motion_01", "start": 1.2, "end": 1.8, "active": True},
                {"id": "motion_disabled", "start": 2.0, "end": 2.5, "active": False},
            ],
        },
        "automatic_editor_clips": {
            "SFX": [
                {
                    "id": "sfx_01",
                    "kind": "SFX",
                    "time_basis": RECAP_TIME_BASIS,
                    "start": 1.25,
                    "end": 1.5,
                    "duration": 0.25,
                    "asset_path": str(sfx_path),
                    "active": True,
                },
                {
                    "id": "sfx_disabled",
                    "kind": "SFX",
                    "time_basis": RECAP_TIME_BASIS,
                    "start": 2.0,
                    "end": 2.25,
                    "duration": 0.25,
                    "asset_path": str(sfx_path),
                    "active": False,
                },
            ],
            "EMOJI": [
                {
                    "id": "emoji_01",
                    "kind": "EMOJI",
                    "time_basis": RECAP_TIME_BASIS,
                    "start": 1.25,
                    "end": 2.75,
                    "emoji": "spongebob dance",
                    "asset_path": str(emoji_path),
                    "active": True,
                }
            ],
        },
    }
    write_recap_effects_plan(plan, effects_path=effects_path, editor_plan_path=editor_plan_path)
    monkeypatch.setattr("recap_media.effects.prepare_emoji_events", _prepared_emoji)

    renderable = load_recap_effects(
        effects_path=effects_path,
        editor_plan_path=editor_plan_path,
        render_planned_effects=True,
    )

    persisted = json.loads(effects_path.read_text(encoding="utf-8"))
    assert persisted["render_enabled"] is False
    assert [event["id"] for event in renderable["visual_fx_events"]] == ["fx_01"]
    assert [event["id"] for event in renderable["motion_events"]] == ["motion_01"]
    assert [event["id"] for event in renderable["sfx_events"]] == ["sfx_01"]
    assert [event["id"] for event in renderable["emoji_events"]] == ["emoji_01"]
    assert renderable["sfx_events"][0]["start"] == 1.25
    assert renderable["emoji_events"][0]["start"] == 1.25


def test_recap_emoji_retune_targets_four_local_events_without_replanning_other_families(
    monkeypatch, tmp_path
):
    local_assets = [tmp_path / f"reaction_{index}.png" for index in range(4)]
    for asset in local_assets:
        asset.write_bytes(b"asset")
    candidates = [
        {
            "start": index * 8.0,
            "end": index * 8.0 + 0.3,
            "emoji": f"reaction {index}",
            "matched_word": f"moment_{index}",
            "asset_path": str(asset),
            "asset_description": f"reaction {index}",
        }
        for index, asset in enumerate(local_assets)
    ]
    candidates.append({"start": 28.0, "end": 28.3, "emoji": "network glyph"})
    monkeypatch.setattr("recap_media.effects.build_emoji_candidates", lambda _words: candidates)
    monkeypatch.setattr(
        "recap_media.effects.resolve_event_asset",
        lambda event: Path(event["asset_path"]) if event.get("asset_path") else None,
    )

    sequence = _emoji_density_sequence()
    captions = _emoji_density_captions()
    emoji_events = recap_emoji_events(sequence, captions, _portrait_plan(), _recap_script())
    assert len(emoji_events) == 4
    assert [event["matched_word"] for event in emoji_events] == [
        "moment_0",
        "moment_1",
        "moment_2",
        "moment_3",
    ]
    for event in emoji_events:
        assert event["origin"] == AI_RECAP_ORIGIN
        assert event["block_id"] == "VO_001"
        assert event["beat_ids"] == ["B001"]
        assert event["reason"] == "ai_recap_emoji"

    base_plan = build_recap_effects_plan(
        sequence, captions, _portrait_plan(), _recap_script(), energy="PUNCHY"
    )
    assert len(base_plan["automatic_editor_clips"]["EMOJI"]) == 4
    base_plan["sfx"] = {"events": [{"id": "sfx_keep"}], "skipped": [], "warnings": []}
    base_plan["automatic_editor_clips"]["SFX"] = [{"id": "sfx_clip_keep", "kind": "SFX"}]
    base_plan["visual_fx"] = {
        "events": [{"id": "fx_keep"}],
        "motion_events": [{"id": "motion_keep"}],
    }

    first = retune_recap_emoji_plan(base_plan, sequence, captions, _portrait_plan(), _recap_script())
    second = retune_recap_emoji_plan(base_plan, sequence, captions, _portrait_plan(), _recap_script())

    assert first["automatic_editor_clips"]["EMOJI"] == second["automatic_editor_clips"]["EMOJI"]
    assert len(first["automatic_editor_clips"]["EMOJI"]) == 4
    assert first["sfx"] == base_plan["sfx"]
    assert first["automatic_editor_clips"]["SFX"] == base_plan["automatic_editor_clips"]["SFX"]
    assert first["visual_fx"] == base_plan["visual_fx"]
    assert base_plan["automatic_editor_clips"]["EMOJI"] == first["automatic_editor_clips"]["EMOJI"]


def test_recap_emoji_retune_preserves_manual_emoji_clips(tmp_path, monkeypatch):
    asset = tmp_path / "reaction.png"
    asset.write_bytes(b"asset")
    monkeypatch.setattr(
        "recap_media.effects.build_emoji_candidates",
        lambda _words: [
            {
                "start": index * 8.0,
                "end": index * 8.0 + 0.3,
                "emoji": f"reaction {index}",
                "matched_word": f"moment_{index}",
                "asset_path": str(asset),
                "asset_description": "reaction",
            }
            for index in range(4)
        ],
    )
    monkeypatch.setattr("recap_media.effects.resolve_event_asset", lambda _event: asset)

    sequence = _emoji_density_sequence()
    captions = _emoji_density_captions()
    base_plan = build_recap_effects_plan(sequence, captions, _portrait_plan(), _recap_script())
    retuned = retune_recap_emoji_plan(base_plan, sequence, captions, _portrait_plan(), _recap_script())
    effects_path = tmp_path / "effects_plan.json"
    editor_plan_path = tmp_path / "editor_asset_plan.json"
    write_recap_effects_plan(retuned, effects_path=effects_path, editor_plan_path=editor_plan_path)

    editor_plan = load_editor_asset_plan(editor_plan_path)
    editor_plan["clips"].append(
        {
            "id": "manual_emoji_keep",
            "kind": "EMOJI",
            "time_basis": RECAP_TIME_BASIS,
            "start": 30.0,
            "end": 31.0,
            "asset_path": str(asset),
            "active": True,
            "manual_override": True,
            "origin": "manual",
        }
    )
    save_editor_asset_plan(editor_plan, editor_plan_path)

    write_recap_effects_plan(retuned, effects_path=effects_path, editor_plan_path=editor_plan_path)
    persisted = load_editor_asset_plan(editor_plan_path)
    assert any(clip["id"] == "manual_emoji_keep" for clip in persisted["clips"])
