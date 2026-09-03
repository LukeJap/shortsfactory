from __future__ import annotations

import pytest

from editor_asset_plan import load_editor_asset_plan, save_editor_asset_plan
from sfx_engine import (
    build_sfx_mix_filter_complex,
    sfx_clip_from_event,
    sfx_gain_db_for_clip,
    sfx_linear_gain_for_clip,
)


def test_sfx_gain_defaults_to_zero_db_without_saved_metadata():
    clip = {"id": "sfx-default", "kind": "SFX"}

    assert sfx_gain_db_for_clip(clip) == 0.0
    assert sfx_linear_gain_for_clip(clip) == 1.0


def test_legacy_linear_volume_keeps_its_existing_audible_level():
    clip = {"id": "legacy", "kind": "SFX", "volume": 0.25}

    assert sfx_gain_db_for_clip(clip) == pytest.approx(-12.041, abs=0.001)
    assert sfx_linear_gain_for_clip(clip) == 0.25


def test_sfx_clip_converts_automatic_linear_volume_to_one_gain_db_field():
    clip = sfx_clip_from_event({"id": "sfx", "start": 1.0, "duration": 0.2, "volume": 0.25})

    assert clip["gain_db"] == -12.0
    assert "volume" not in clip


def test_sfx_gain_persists_without_altering_another_clip(tmp_path):
    path = tmp_path / "editor_asset_plan.json"
    plan = {
        "version": 1,
        "clips": [
            {"id": "quiet", "kind": "SFX", "gain_db": -12.0},
            {"id": "loud", "kind": "SFX", "gain_db": 6.0},
        ],
    }

    save_editor_asset_plan(plan, path)
    reloaded = load_editor_asset_plan(path)

    assert reloaded["clips"][0]["gain_db"] == -12.0
    assert reloaded["clips"][1]["gain_db"] == 6.0


def test_shared_sfx_mixer_applies_db_converted_gain_and_keeps_limiter():
    events = [
        {"start": 1.0, "duration": 0.2, "volume": sfx_linear_gain_for_clip({"gain_db": -12.0})},
        {"start": 2.0, "duration": 0.2, "volume": sfx_linear_gain_for_clip({"gain_db": 6.0})},
    ]

    filters = build_sfx_mix_filter_complex("[0:a]", events, first_input_index=1)

    assert "volume=0.2512" in filters
    assert "volume=1.9953" in filters
    assert "alimiter=limit=0.92" in filters
