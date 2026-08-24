"""
Single source of truth for the shared JSON/plan-file paths under output/
that multiple pipeline stages read and write. Previously each consumer
redefined its own copy of these paths independently -- e.g. subtitles.json
had 11 separate `ROOT / "output" / "subtitles.json"` definitions across
the tree, combined_edit_plan.json had 5 -- so a rename or relocation of any
one of these files meant hunting down every redefinition by hand.

Scoped to plan/state JSON (and the one .ass caption file) only, not video
output paths (short1_base.mp4 etc.), which already have more complex
placement logic (see render.py's component_target_for()) better left
alone.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

OUTPUT_DIR = ROOT / "output"

SUBTITLES_PATH = OUTPUT_DIR / "subtitles.json"
SHORT_PLAN_PATH = OUTPUT_DIR / "short_plan.json"
SEMANTIC_EDIT_PLAN_PATH = OUTPUT_DIR / "semantic_edit_plan.json"
EDIT_PLAN_PATH = OUTPUT_DIR / "edit_plan.json"
COMBINED_EDIT_PLAN_PATH = OUTPUT_DIR / "combined_edit_plan.json"
EMOJI_EVENTS_PATH = OUTPUT_DIR / "emoji_events.json"
SFX_PLAN_PATH = OUTPUT_DIR / "sfx_plan.json"
VISUAL_FX_PLAN_PATH = OUTPUT_DIR / "visual_fx_plan.json"
AI_VISUAL_PLAN_PATH = OUTPUT_DIR / "ai_visual_plan.json"
AI_VISUAL_MAPPED_PLAN_PATH = OUTPUT_DIR / "ai_visual_mapped_plan.json"
VISUAL_EDIT_PLAN_PATH = OUTPUT_DIR / "visual_edit_plan.json"
EDITOR_ASSET_PLAN_PATH = OUTPUT_DIR / "editor_asset_plan.json"
RENDER_SETTINGS_PATH = OUTPUT_DIR / "render_settings.json"
CAPTIONS_PATH = OUTPUT_DIR / "captions.ass"
TEMPORAL_EDIT_PLAN_PATH = OUTPUT_DIR / "temporal_edit_plan.json"
TEMPORAL_SCENE_PLAN_PATH = OUTPUT_DIR / "temporal_scene_plan.json"
MOTION_SCENE_PLAN_PATH = OUTPUT_DIR / "motion_scene_plan.json"
SHOT_TYPE_MOTION_PLAN_PATH = OUTPUT_DIR / "shot_type_motion_plan.json"
SMART_MOTION_PLAN_PATH = OUTPUT_DIR / "smart_motion_plan.json"
MANUAL_EDIT_PLAN_PATH = OUTPUT_DIR / "manual_edit_plan.json"
TRANSCRIPT_CORRECTIONS_PATH = OUTPUT_DIR / "transcript_corrections.json"
