from __future__ import annotations

from editor_asset_plan import load_editor_asset_plan, save_editor_asset_plan
from persistent_title import (
    persistent_title_ass,
    persistent_title_from_plan,
    set_persistent_title_on_plan,
    write_persistent_title_ass,
)


def test_persistent_title_round_trips_through_the_existing_editor_plan(tmp_path):
    path = tmp_path / "editor_asset_plan.json"
    plan = {"version": 1, "clips": []}

    set_persistent_title_on_plan(plan, "  A   persistent\nvideo title  ")
    save_editor_asset_plan(plan, path)

    loaded = load_editor_asset_plan(path)
    assert persistent_title_from_plan(loaded) == "A persistent video title"
    assert loaded["persistent_title"] == {"text": "A persistent video title"}


def test_persistent_title_ass_is_full_duration_and_uses_a_title_style(tmp_path):
    path = tmp_path / "persistent_title.ass"
    result = write_persistent_title_ass("Gary chooses Patrick", 87.25, path)

    assert result == path
    content = path.read_text(encoding="utf-8")
    assert "Style: PersistentTitle,Arial,72" in content
    assert "Dialogue: 0,0:00:00.00,0:01:27.25,PersistentTitle" in content
    assert "Gary chooses Patrick" in content
    assert "background-color" not in content


def test_empty_persistent_title_removes_stale_export_layer(tmp_path):
    path = tmp_path / "persistent_title.ass"
    path.write_text(persistent_title_ass("Old title", 5.0), encoding="utf-8")

    assert write_persistent_title_ass("   ", 5.0, path) is None
    assert not path.exists()


def test_standard_export_passes_title_layer_without_any_preview_ui(monkeypatch):
    import render

    commands = []
    monkeypatch.setattr(render, "run_command", commands.append)

    render.add_emoji_overlay(render.PERSISTENT_TITLE_ASS_PATH)

    command = commands[0]
    assert "--title" in command
    assert str(render.PERSISTENT_TITLE_ASS_PATH.relative_to(render.ROOT)) in command
    assert "YouTubeShortsMockOverlay" not in " ".join(command)
