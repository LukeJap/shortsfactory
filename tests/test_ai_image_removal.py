from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_retired_image_cutaway_modules_are_not_present():
    removed_paths = (
        "app/ai_visual_planner.py",
        "app/apply_ai_visuals.py",
        "app/generate_ai_visual_assets.py",
        "app/image_backend_status.py",
        "app/web_image_sources.py",
        "app/gui_app/mixins/ai_visual_pipeline.py",
        "app/gui_app/mixins/ai_visual_preview.py",
        "app/gui_app/mixins/ai_visual_slots.py",
        "app/gui_app/mixins/image_ai.py",
        "app/gui_app/mixins/web_images.py",
    )

    assert all(not (ROOT / path).exists() for path in removed_paths)


def test_live_entry_points_do_not_restore_image_cutaway_dependencies():
    entry_points = (
        ROOT / "app/gui_app/main_window.py",
        ROOT / "app/gui_app/mixins/render_pipeline.py",
        ROOT / "app/subtitles.py",
    )
    retired_dependencies = (
        "ai_visual_pipeline",
        "ai_visual_preview",
        "ai_visual_slots",
        "apply_ai_visuals",
        "generate_ai_visual_assets",
        "image_backend_status",
        "web_image_sources",
    )

    live_source = "\n".join(
        path.read_text(encoding="utf-8") for path in entry_points
    )

    assert all(dependency not in live_source for dependency in retired_dependencies)
