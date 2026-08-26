# ShortsFactory — Project Context for Design

*Written as a handoff doc so a designer (or a designer's AI assistant)
can get oriented without reading the codebase. Current as of 2026-08-25.*

## 1. What this is

ShortsFactory is a **local desktop app** (not a web app, not hosted) that
turns a long source video — a full TV episode, podcast, movie, whatever —
into an edited, vertical 9:16 "Short" in the style of YouTube Shorts /
TikTok / Instagram Reels. One user, running it on their own machine,
against their own footage.

It automates the parts of Shorts-editing a human would tediously do by
hand: picking a strong moment, tightening it (cutting dead air and
redundant speech), captioning it karaoke-style, adding punch-in camera
motion, color grading, AI-generated visual cutaway images, emoji
reactions, sound effects, and background music.

**The one design rule that matters most:** every automated/AI decision —
which clip, which cuts, which captions, which effects, which emoji —
must be **visible and user-overridable in the editor**, never a silent
black box. The whole UI is built around "here's what the AI proposed,
here's how you change it," not "click render and hope."

## 2. Platform & stack (brief — for context, not because a designer needs to touch it)

- **PySide6 (Qt6)** for the GUI — a native desktop app, currently
  developed/run on macOS (Apple Silicon), originally built on Windows.
- **FFmpeg** does all actual video/audio processing. The app shells out
  to `ffmpeg`/`ffprobe` directly.
- **Whisper** (local) for transcription, **Ollama** (a local LLM,
  `llama3.1:8b`) for clip selection / content decisions / AI visual
  planning — no cloud AI calls, everything runs on-device.
- Optional local Stable Diffusion backend (Forge/Automatic1111) for
  generating the AI visual cutaway images.

Because everything runs locally and a full render does several real
video encodes, **render time is a real UX constraint** — this is why the
app is built around a cheap, instant *preview* (drag things around, see
them live on the video) with an explicit, separate "Generate Final
Video" step that's gated behind that preview, rather than re-rendering
on every tweak.

## 3. The user's workflow, start to finish

1. **Import** a source video (drag-and-drop) into the left panel.
2. **Find Best Clips** — AI scans the source and proposes candidate
   clips (with scores/reasoning) in the right panel.
3. **Select and trim a clip** on the timeline (or accept an AI pick).
4. **Plan Visuals** — kicks off AI visual cutaway planning *and* a first
   pass of emoji placement, so both are visible in the preview before
   any render happens.
5. **Placement Editor** — with the clip selected, the user can directly
   drag captions, emoji, and AI-visual cutaways around on the video
   preview itself (see §5) to reposition/retime/swap them, and use
   dedicated "Generate Assets" / "Generate Emoji" / "Generate SFX"
   buttons to lock in and pre-resolve those choices.
6. **Adjust settings** — edit energy/pace, filter intensity, sound FX
   mode, background music.
7. **Generate Final Video** — runs the full render pipeline (cuts,
   transcript, punch-in motion, color grade, AI visuals, captions, emoji,
   sound effects, music) and produces the final MP4.

## 4. Current UI layout

Three-column layout inside a single main window (`QSplitter`, roughly
280px / 760px / 440px by default, all resizable/collapsible):

**Left — "SOURCE FEED"**
- Video drop zone / file picker
- Transcription quality selector
- **Find Best Clips** button
- **EDIT STYLE** section: edit-energy selector (pacing/aggressiveness)
- **FILTER INTENSITY** slider (controls the color-grade/FX strength)

**Center**
- Video preview player — this is also the canvas for the drag-to-reposition
  **Placement Editor** overlays (captions, emoji, AI-visual cutaways — see
  §5)
- Playback controls (play/pause, timestamp, preview-only volume)
- **Generate Final Video** button — lives right next to playback controls,
  not off in a settings panel, since it's the single most important action
  in the app
- Below the preview, in a vertical splitter: the **timeline widget**
  (see §6) and its zoom/pan controls
- **AUDIO** panel: Sound FX mode selector, **Generate SFX** button, SFX
  folder shortcut, a context panel (appears when an SFX clip is selected
  on the timeline: swap / disable / delete / volume), background music
  add/volume/remove, an "AI Narrator" feature placeholder (not yet built)
- Render log panel (live-streamed render output + persisted to
  `output/render_log.txt`)

**Right**
- **AI CLIP HUNTER** — up to 6 AI-proposed clip candidate cards
- **AI VISUAL CUTAWAYS** panel: **Plan Visuals** / **Generate Assets** /
  **Generate Emoji** buttons, a list of planned visual slots, an
  inspector (position/scale/display-mode controls for the selected
  visual), and a context panel for the selected emoji (swap / disable /
  delete — appears when an emoji clip is selected on the timeline)
- Transcript editor — click a line to correct or cut it

## 5. The Placement Editor (drag-on-video interaction pattern)

This is the app's signature interaction and probably the most
design-relevant system to understand. Three kinds of overlay can be
dragged **directly on the video preview**, live, before any render:

- **Captions** — drag to reposition the caption block; clamped to a safe
  range so it can't be dragged somewhere the real render would refuse to
  honor (kept out from under where a platform's own UI — like/comment
  rail, caption strip — typically sits).
- **Emoji reactions** — drag to reposition; **double-click** opens a
  picker (grid of local "reaction" image/GIF assets, plus a custom-emoji
  text field) to change *which* reaction is shown at that moment.
  Right-click resets to the default position. That default position is
  now caption-aware (see §9) — it auto-picks a spot above or below
  wherever the caption currently sits, including a caption the user has
  manually dragged, rather than a fixed spot that could land on top of it.
- **AI visual cutaways** — drag to reposition/scale; a "FULL FRAME" tag
  toggle for cutaways meant to cover the whole canvas rather than sit as
  a card.

All three write into shared plan files that the final render then
reuses **exactly as previewed** — the render never silently
recomputes/overrides a manual placement.

## 6. The timeline widget

A fully custom-drawn (not a stock Qt widget) multi-lane, zoomable/
pannable editor surface — `app/gui_app/timeline_widget.py`. Currently 5
lanes, top to bottom:

1. **V1 SOURCE** — the source video strip, with trim handles for the
   selected clip range
2. **EDITS** — transcript cut markers, manual edits, motion/FX/graphic
   event markers — six marker categories split across two rows (cuts/
   transcript/caption-impact on top, motion/FX/graphics below) so they
   don't visually overlap each other
3. **VISUALS** — AI visual cutaway clips (draggable to retime; overlap
   stacks into rows)
4. **SFX** — sound effect clips (orange; overlap stacks into rows, same
   as VISUALS)
5. **EMOJI** — emoji reaction clips (gold) — click to select/seek, drag
   edges to retime, double-click to swap, plus a Disable/Delete context
   panel in the right column; overlap stacks into rows, same as VISUALS/SFX

Selecting a clip in any of these lanes seeks the video preview to that
moment and syncs the corresponding placement-editor overlay, so the
timeline and the on-video drag interactions always agree with each other.

## 7. Visual design system

Single QSS stylesheet, `app/gui_app/style.py`. Dark, gothic/industrial
aesthetic:

- **Panel backgrounds:** near-black (`#09090A`, `#0A0A0B`, `#0B0B0D`)
- **Primary text:** off-white/cream (`#DED6C8`, `#F2ECE4`)
- **Secondary/muted text:** greys (`#B8AEA1`, `#918B84`, `#7E7670`)
- **Accent (borders, highlights, active states):** blood-red/rust
  (`#741C28`, `#C9384F`, `#733B2D`, `#d04b5f`)
- Minimal corner rounding (3–5px) — reads as sharp/structural, not soft
- Section headers use a bold, small-caps-style label pattern
  ("SOURCE FEED", "EDIT STYLE", "AI CLIP HUNTER", etc.)
- Color-coded timeline lanes (green-ish for AI visuals, orange for SFX,
  gold for emoji) so clip kind is identifiable at a glance without
  reading a label

## 8. Data model worth knowing about (affects what's feasible to design)

Three "editor asset" kinds — **AI_VISUAL**, **SFX**, **EMOJI** — all live
as clips in one shared file, `output/editor_asset_plan.json`, keyed by
source-video time with a stable id, an active/enabled flag, and a
"manually overridden" flag. This is *why* the three timeline lanes and
their context panels (swap/disable/delete) behave identically — they're
the same underlying mechanism with different visual/audio content.
Anything proposed for one of these three tends to be straightforward to
extend to the other two, since the plumbing is already shared.

## 9. Recent / current state (most recent work, roughly newest first)

- **Emoji auto-placement now avoids the caption:** emoji default
  positions used to come from one fixed 4-slot table with zero awareness
  of where the caption actually sits, so a caption dragged toward the
  bottom of its allowed range (a normal thing to do) could end up
  directly under an auto-placed emoji. Fixed at the source: whenever a
  *new* default position gets assigned (a fresh render, the pre-render
  "Plan Visuals" preview, or right-click "reset to default" in the
  editor), the app now computes the caption's current position first and
  picks an above-caption or below-caption spot instead. Existing/
  manually-placed emoji are left exactly where they are — this only
  changes what a brand-new default position resolves to.
- **Timeline lane readability pass:** two related legibility bugs in the
  custom timeline widget (§6), both reported as things looking "smushed"/
  too small to read. (1) SFX and EMOJI clips had no overlap handling, so
  two clips close together in time literally painted on top of each
  other — now they stack into up to 2 rows, the same mechanism the
  VISUALS lane already used. (2) The EDITS lane crammed 6 different
  marker categories (cuts, transcript edits, caption-impact, motion, FX,
  graphics) into a fixed 26px-tall strip using 3-7px slivers that
  sometimes literally overlapped each other — the lane is now taller and
  split into 2 clearly separated rows, so each marker type reads as an
  actual visible block rather than a hairline.
- **Dead-code cleanup:** four long-superseded scripts (an early LLM
  content-trim planner, an early LLM short-planner, a face-tracking
  auto-reframe stage made obsolete when the render switched to
  crop-to-fill framing, and its speaker-focus helper) moved out of
  `app/` into a top-level `archive/` folder. No behavior change — purely
  less clutter when browsing the live pipeline.
- **Render pipeline efficiency pass:** merged two of the render's
  internal video-encode passes into one, dropped a faster encode preset
  onto intermediate stages, fixed leaked source-video chapter metadata,
  cleaned up render-log noise. Backend-only, no UI change, but means
  renders are meaningfully faster now.
- **Emoji pre-generation + full timeline integration:** the "Generate
  Emoji" button, the EMOJI timeline lane (§6), and syncing the drag-on-
  preview emoji overlay with it bidirectionally — all new this cycle.
  Also fixed a bug where a manually-retimed emoji's on-screen duration
  wasn't actually respected by the final render.
- **Overlay Placement Editor** (§5) — captions/emoji/AI-visual drag
  repositioning directly on the video preview, plus the filter-intensity
  slider and the "Generate Final Video" button's relocation next to
  playback controls, shipped together as one feature.
- A `feature/LukeV2` branch merge brought in temporal-edit fixes and a
  transcript-reuse/remapping system (mostly backend).
- A broad codebase cleanup pass: consolidated scattered config constants,
  added docstrings throughout, and made several of the largest/most
  tangled pipeline functions more readable — none of this changes
  behavior, purely maintainability.

## 10. Where things live (for when a design idea needs an engineering estimate)

- `app/gui_app/main_window.py` — the whole window's layout/widget wiring
  (large file; layout code lives inline where each panel is built)
- `app/gui_app/timeline_widget.py` — the custom timeline canvas
- `app/gui_app/style.py` — the entire visual design system (one QSS string)
- `app/gui_app/mixins/` — one file per feature area's *behavior*
  (`editor_assets.py`, `emoji_preview.py`, `ai_visual_preview.py`,
  `caption_preview.py`, `render_pipeline.py`, `playback.py`, etc.) —
  `main_window.py`'s window class is composed from all of these
- `app/render.py` and the rest of `app/*.py` — the actual video pipeline
  (11 steps: crop/select → transcript → cuts → semantic edit → transcript
  remap → motion/FX/AI-visuals → captions → emoji → SFX → sanitize →
  organize output)
- `output/` — everything a render produces/reads: plans (JSON), the
  transcript, intermediate and final rendered video files, the render log
