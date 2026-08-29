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

# AI Recap Mode (see SHORTSFACTORY_AI_RECAP_SHARED_CONTRACT.md) -- a
# separate output/recap/ subtree, not the flat output/ layout above.
# Track A (research/story-mapping, a separate workstream) produces the
# first four; Track B (this codebase's app/recap_media/) produces
# recap_sequence.json and reads the first four as authoritative input.
RECAP_DIR = OUTPUT_DIR / "recap"
EPISODE_IDENTITY_PATH = RECAP_DIR / "episode_identity.json"
EPISODE_RESEARCH_DOSSIER_PATH = RECAP_DIR / "episode_research_dossier.json"
VERIFIED_STORY_MAP_PATH = RECAP_DIR / "verified_story_map.json"
RECAP_SCRIPT_PATH = RECAP_DIR / "recap_script.json"
RECAP_SEQUENCE_PATH = RECAP_DIR / "recap_sequence.json"
RECAP_AUDIO_DUCK_PLAN_PATH = RECAP_DIR / "audio_duck_plan.json"
RECAP_NARRATION_CAPTIONS_PATH = RECAP_DIR / "narration_captions.json"
RECAP_PORTRAIT_PLAN_PATH = RECAP_DIR / "portrait_framing_plan.json"
RECAP_FINAL_OUTPUT_PATH = RECAP_DIR / "final_recap.mp4"
# Recap effects are authored on Track B's base timeline. Keeping their plan
# beside recap artifacts prevents one recap from overwriting a normal Short.
RECAP_EDITOR_ASSET_PLAN_PATH = RECAP_DIR / "editor_asset_plan.json"
RECAP_EFFECTS_PLAN_PATH = RECAP_DIR / "effects_plan.json"
