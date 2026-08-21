# ShortsFactory Recovery Audit

Date: August 20, 2026
Branch: `recovery/2026-08-20`

This is Batch 0 from `SHORTSFACTORY_AUG20_249PM_TO_10PM_RECOVERY_DOSSIER.md`.

## Source Baseline

| Item | Result |
| --- | --- |
| Git status before recovery | Clean |
| Active recovery branch | `recovery/2026-08-20` |
| Restored GUI source | `app/gui.py` |
| 2:49 PM GUI snapshot | `app/gui8.20.py` |
| GUI hash match | `app/gui.py` and `app/gui8.20.py` match exactly |
| Damaged GUI reference | `recovered_gui.py` only; read-only forensic reference |

## Forensic Feature Table

| Feature | Present in 2:49 clean GUI? | Present in damaged GUI? | Engine support survives? | State artifact survives? | Recovery required? | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| Render progress strip | Yes | Yes | N/A | N/A | No | High |
| Render stage / elapsed / ETA | Yes | Yes | N/A | N/A | No | High |
| Auto-open final MP4 | No | Unclear | Final path exists in GUI/render | N/A | Yes | Medium |
| Spacebar guard for multiline editors | Partial: `QLineEdit`/`QTextEdit` only | Likely same or improved | N/A | N/A | Yes, add `QPlainTextEdit` | High |
| Sound FX AUTO/OFF setting | No GUI control | Yes | `visual_emphasis.py`, `render.py`, `sfx_engine.py` | `output/render_settings.json` | Yes | High |
| SFX Folder button | No | Yes | `assets/sfx`, `sfx_engine.py` | N/A | Yes | High |
| Generate SFX button | No | Yes | `sfx_engine.py --editor-only` | `output/editor_asset_plan.json` | Yes | High |
| SFX timeline lane | No | Yes | `editor_asset_plan.py`, `sfx_engine.py` | `output/editor_asset_plan.json` | Yes | High |
| SFX clip select/move/trim | No | Yes | `editor_asset_plan.py` | `output/editor_asset_plan.json` | Yes | High |
| SFX mute/delete/volume | No | Yes | `sfx_engine.py` consumes edited plan | `output/editor_asset_plan.json` | Yes | High |
| SFX swap | No | Yes | `asset_metadata_for_path`, editor plan merge | `output/editor_asset_plan.json` | Yes | High |
| SFX preview in editor | No | Yes | Qt multimedia available | SFX asset paths exist | Yes | High |
| Four-lane timeline including SFX | No: V1/EDITS/VISUALS only | Yes | N/A | `editor_asset_plan.json` | Yes | High |
| Two-row Audio/SFX/Music layout | No | Yes | N/A | N/A | Yes | High |
| Editable AI visual timeline blocks | No, only ranges | Yes | `editor_asset_plan.py`, `apply_ai_visuals.py` | `output/editor_asset_plan.json` | Yes | High |
| AI visual live monitor preview | No | Yes | `apply_ai_visuals.py` has display modes | AI visual manifest exists | Yes | High |
| Image display mode control | No | Yes | `apply_ai_visuals.py` supports `OVERLAY_CARD`, `FULL_FRAME_CONTAIN`, `FULL_FRAME_COVER` | `editor_asset_plan.json` has values | Yes | High |
| Image scale control | No | Yes | `apply_ai_visuals.py` consumes `scale` | `editor_asset_plan.json` has values | Yes | High |
| Image X/Y position controls | No | Not proven | Not found in renderer | No | New work | Medium |
| Multiple image variants | No GUI | Yes | `generate_ai_visual_assets.py --new-variant` | manifest and editor plan variants/paths | Yes | High |
| Keep/save/lock image variant | Partial `KEEP` state only | Yes | asset generator preserves variants | manifest/editor plan | Yes | High |
| Plan visuals and keep changes | Partial: `KEEP MY VISUALS` exists | Not proven exact | Planner/generator preserve assets partially | editor plan/manual flags | Yes | Medium |
| Background transcript preload | No | Yes | Analyze/transcript cache support exists | transcript cache exists | Yes | Medium |
| Cross-source transcript job safety | No | Yes | N/A | N/A | Yes | Medium |
| Forge auto-launch on app startup | No; manual `CHECK IMAGE AI` | Partial/on-demand autolaunch | `image_backend_status.py --autolaunch` | launch lock exists | Yes | High |
| Remove Check Image AI button | No | No, damaged GUI still had button | backend supports passive status | N/A | Yes | High |
| Web image sourcing | No GUI | Not proven | `web_image_sources.py` exists | web-sourced entries in editor plan | Later/new work | Medium |
| Rendered folder organization | GUI N/A | N/A | `render.py organize_rendered_output` | `output/rendered/_components` expected | No GUI recovery | High |
| MAXIMUM/temporal/visual FX engines | GUI setting only | Yes | `visual_emphasis.py`, `visual_fx.py`, `temporal_edit.py`, `smart_motion.py` | output plans exist | No engine rebuild | High |

## Surviving Engine Modules

The following post-2:49 modules are present and should be reused rather than reimplemented:

- `app/editor_asset_plan.py`
- `app/sfx_engine.py`
- `app/music_overlay.py`
- `app/apply_ai_visuals.py`
- `app/generate_ai_visual_assets.py`
- `app/image_backend_status.py`
- `app/web_image_sources.py`
- `app/temporal_edit.py`
- `app/visual_emphasis.py`
- `app/visual_fx.py`
- `app/smart_motion.py`
- `app/subtitles.py`
- `app/render.py`

## Recovery Direction

The current GUI baseline is clean and should remain the source of truth. Missing work should be recovered by adding narrow GUI wiring to the existing modules and state files, with manual editor state treated as authoritative.

Next batch: recover only confirmed GUI-only fixes that are missing from the 2:49 baseline.
