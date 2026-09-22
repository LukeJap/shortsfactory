# Repository cleanup — remaining items

Most of the original plan is done: `docs/briefs/` and `docs/history/` exist, the
decompiler scratch, `recovered_gui*.py`, both `.venv-*-backup` folders,
`short1.*`, the root-level test scripts and the 14 stale recap artifact folders
are all gone, and `README.md` and `.gitignore` have been updated.

What is left is small. Hand this file to Claude Code — it has a shell and can do
all of it; this session can read and write files on the machine but cannot move
or delete them.

---

## 1. Three files still in the repo root

```
BRIEF_recap_task13_editor_source_identity.md  ->  docs/briefs/
BRIEF_editor_task15_undo_and_clipboard.md     ->  docs/briefs/
CLEANUP_plan.md                               ->  docs/  (or delete once done)
```

These were written after the reorganization, which is why they missed it. Worth
remembering that every new brief lands in the root by default.

## 2. Fourteen stale files in `output/`

`output/` is gitignored, so none of this touches git — it is disk hygiene and,
more usefully, removing files that misrepresent what the app currently does.

The split is clean by date. Everything the current editor session uses was
written Sep 21-22. Everything below is from **Aug 18 - Sep 1** and belongs to
features that no longer exist:

```
Aug 18  short_plan.json
Aug 18  manual_clip.json
Aug 18  emoji_overlays.json
Aug 19  content_edit_plan.json
Aug 20  visual_fx_plan_punchy_overdrive_test.json
Aug 20  visual_edit_plan_punchy_overdrive_test.json
Aug 20  visual_fx_plan_maximum_overdrive_test.json
Aug 20  visual_edit_plan_maximum_overdrive_test.json
Aug 23  clip_finder_context.json
Aug 23  ai_visual_plan.json
Aug 23  image_ai_launch.lock
Aug 23  ai_visual_mapped_plan.json
Aug 30  editor_asset_plan_pre_editorial_pacing.json
Sep 01  title_export_validation.ass
```

Why each group is dead:

- **`ai_visual_*` and `image_ai_launch.lock`** — artifacts of the AI image
  generation feature, removed permanently (product rule 1). Keeping planning
  output for a feature that cannot be reintroduced is actively confusing.
- **The four `*_overdrive_test.json`** — from the LOW/PUNCHY/MAXIMUM preset era,
  replaced by the 0-100 sliders (product rule 5).
- **`editor_asset_plan_pre_editorial_pacing.json`** — a one-off manual backup.
- **The rest** — superseded single-run outputs; the app regenerates them.

Also delete `output/.pytest_tmp_available_final/` — the last pytest temp dir.

**Before deleting the `ai_visual_*` files**, grep `app/` for those filenames.
`tests/test_editor_asset_plan_legacy_images.py` exists, which means there is
deliberate backward-compatibility handling for legacy image clips; confirm it
reads clips out of an editor plan rather than loading these specific files.

## 3. Optional

`output/transcript_cache/` holds ~160 Whisper transcripts. It is a real cache
that saves re-transcription time, so leave it unless disk is tight. If you prune
it, keep `6fe326573024492b9f0fe287.json` (farmers market feud) and
`ccfcb9fe226d5e5930b6cee8.json` (duct tape dystopia) — those are the two
regression sources named in `CLAUDE.md`.

`output/analysis_seed1.json` and `analysis_seed2.json` are from the seed
comparison test. Regenerable; delete or keep as you prefer.

## 4. Keep `.gitignore` honest

It was already updated. Worth confirming it now covers `.venv-*/` so the venv
backups cannot silently reappear as untracked noise.
