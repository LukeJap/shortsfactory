from __future__ import annotations

import json

from editor_asset_plan import load_editor_asset_plan, save_editor_asset_plan


def test_legacy_image_cutaways_are_ignored_while_supported_assets_survive(
    tmp_path,
):
    path = tmp_path / "editor_asset_plan.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "clips": [
                    {"id": "old-visual", "kind": "AI_VISUAL"},
                    {"id": "old-image", "kind": "image_cutaway"},
                    {"id": "sfx-1", "kind": "SFX"},
                    {"id": "emoji-1", "kind": "EMOJI"},
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = load_editor_asset_plan(path)

    assert [clip["id"] for clip in plan["clips"]] == [
        "sfx-1",
        "emoji-1",
    ]

    save_editor_asset_plan(plan, path)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert [clip["id"] for clip in persisted["clips"]] == [
        "sfx-1",
        "emoji-1",
    ]
