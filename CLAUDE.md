# ShortsFactory — Agent Guide

Local Windows desktop app (PySide6/Qt6, Python 3.12) that turns long source video
into 1080x1920 9:16 Shorts. Local-first, single user, no hosted backend.

## Run / test

```powershell
cd C:\Users\lukej\Desktop\ShortsFactory
.\.venv\Scripts\python.exe -m app.gui          # launch the app
.\.venv\Scripts\python.exe -m pytest           # full suite (tests/, pythonpath=app)
.\.venv\Scripts\python.exe -m pytest tests/test_analyze.py -x -q
```

External services the app expects:

```powershell
ollama serve                                   # llama3.1:8b, clip ranking + analysis
cd Orpheus-FastAPI; .\venv\Scripts\python.exe -u app.py   # TTS, http://localhost:5005
```

Use the Orpheus venv for Orpheus, the project `.venv` for everything else.

## Branch / commit rules

- Work on `V3_dev`.
- `output/`, `input/`, `Orpheus-FastAPI/` and recovery files are gitignored.
- Never `git add .` — generated emoji PNGs under `assets/emoji/` and scratch
  recovery files have repeatedly shown up untracked. Stage explicit paths.
- Never discard uncommitted local work without asking.

## Layout

```
app/analyze.py                  clip discovery + Ollama ranking (Find Best Clips)
app/render.py                   final render
app/visual_fx.py                shared FX engine
app/sfx_engine.py               SFX planning
app/emoji_planner.py            emoji planning
app/emoji_overlay.py            emoji compositing
app/music_overlay.py            music mix
app/standard_audio_pitch.py     standard-mode pitch
app/standard_video_speed.py     standard-mode speed
app/recap_intelligence/         Track A: identity, research, story map, script
app/recap_media/                Track B: TTS, sequence, captions, mix, render
app/gui_app/main_window.py      main window
app/gui_app/mixins/             feature mixins (recap, editor_assets, playback,
                                music, transcript, ai_clip_hunter, emoji_preview…)
app/gui_app/timeline_widget.py  timeline
app/gui_app/style.py            dark theme (incl. QMessageBox styling)
tests/                          ~60 test modules, all pure-python/headless
```

## Non-negotiable product rules

1. **No AI-generated image inserts.** The feature was removed permanently. Do not
   reintroduce generated cutaways, Stable Diffusion, or AI-visual event lanes.
2. **Manual edits are authoritative.** Moved emoji, resized SFX, caption edits and
   music settings survive regeneration. Regeneration is scoped and explicit; it
   never rebuilds unrelated editor state.
3. **The editor timeline is the source of truth after generation.** Every
   generated decision becomes a visible, editable timeline entity. No hidden
   planner-only state driving the UI. (Recurring bug class: "planner says 18 SFX,
   lane is empty".)
4. **Preview is lightweight, render is high quality — but both read the same
   semantic state** (strength, timing, gain, positions, speed, pitch).
5. **Continuous sliders, not presets.** Visual FX Strength 0–100, AutoCut
   Aggression 0–100, Filter Intensity 0–200%. Do not restore LOW/PUNCHY/MAXIMUM.
6. **Nothing is hard-coded to old experiment values** — recap speed, narrator
   pitch and source pitch stay user-adjustable (no fixed 1.50x / +1.8 semitones).
7. **Find Best Clips returns variable-length clips, 15-90s**, with start and end
   on real content boundaries (clean line in, payoff out — never mid-sentence).
   Spread across the whole source, non-overlapping, no intro/theme bias. Ranking
   quality must be semantic — never content-specific heuristics. The former
   exact-60.0s rule was retired 2026-09-17: it was the main cause of poor clip
   quality, because the good clip was usually not in the candidate set.
8. **TTS expression tags (`<gasp>`, `<chuckle>`…) never appear in captions.**
   Keep `display_text` separate from `tts_text`.
9. **Recap captions live in recap-timeline coordinates**, never raw source
   timestamps: `recap_t = timeline_start + (word.t - source_start) / speed`.
10. **Narration duration comes from the processed WAV**, never a word-count
    estimate. Cache keys include text, tts_text, voice, speed and pitch.
11. Don't claim any editing recipe guarantees fair use, Content ID safety, or
    monetization.

## AI Recap editorial direction

Narrator is the primary storyteller; the recap must hold together with source
dialogue removed. Hook first, then minimal context, then causal escalation
(A causes B forces C) — not scene-by-scene chronology. Compress hard, protect the
payoff, end fast. Rhythm is narration → short source punch (~9–14s) → narration.
No "In this episode…" openers.

## Scoring pipeline — hard-won rules

These cost several iterations each. Do not undo them without reading the
matching brief.

1. **An example in a prompt is a specification.** `llama3.1:8b` copied example
   phrasing verbatim four separate times, and copied an example's *score shape*
   once (a descending pair produced a descending ramp across 26 candidates).
   Never put a descriptive sentence in a JSON example field, and never let two
   example entries imply an ordering.
2. **One judgment per request.** Asking for N scores in one response conditions
   each score on the ones already written, which is positional by construction —
   the same clip scored 7 and 84 depending on prompt order. Per-candidate calls
   are no slower and are exactly reproducible.
3. **`num_ctx` 8192 is a VRAM budget, not a knob.** On the 8GB 2070, 16384 spills
   the KV cache to CPU: generation drops from ~35 tok/s to ~7.
4. **Anchor rating scales inside the candidate distribution.** Extreme anchors
   (a perfect clip vs. a filler greeting) compress every real candidate against
   the top. A mid anchor is what makes the scale work.
5. **Let Python judge what is mechanical.** `hook_strength` returned a constant
   20 across three runs and three wordings; a 15-line rule function gives 5
   distinct values. The model judges meaning; Python counts words and adds up.
6. **Degrade, never abort.** A short, truncated or failed model response costs
   one candidate, not the run.

## Working style

One small issue at a time: find the failure point, make the smallest change, run
one live test, move on. No broad rewrites, no multi-feature batches. State what
you changed and what the single next manual test is.

Unit tests are necessary but not sufficient — stale audio, timeline refresh,
Qt dialog styling, real TTS behavior, Ollama timeouts and render sync need a real
GUI/media run. Regression sources: `input/s17e9a duct tape dystopia.mp4` (recap),
`input/s17e9b farmers market feud.mp4` (clip discovery).

## Current open work

- Find Best Clips: **done** (tasks 1-10, 2026-09-21). Variable-length candidates
  on content boundaries, region-aware trim, one-model-call-per-candidate rubric
  scoring, titles and hooks, results wired to the cards. Verified: 10 clips,
  8 distinct scores, seed-stable (mean delta 0.5), 9/10 deciles covered, ~2 min
  end to end. The briefs `BRIEF_clip_discovery*.md` record why each choice was
  made; `tools/compare_rank_seeds.py` is the regression check.
- Recap: stale narration in editor, dropped narration words, source-caption
  remapping, SFX/emoji lane population, live music preview parity, narrator voice
  selector, `display_text`/`tts_text` split.
- Then: base filter + Visual FX parity for Recap, Orpheus auto-start.
