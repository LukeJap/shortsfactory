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
motion, color grading, emoji reactions, sound effects, and background
music.

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
- **Whisper** (local) for transcription and **Ollama** (a local LLM,
  `llama3.1:8b`) for clip selection / content decisions — no cloud AI
  calls, everything runs on-device.

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
4. **Generate Emoji** plans local emoji reactions that remain visible and
   editable in the preview before any render happens.
5. **Placement Editor** — with the clip selected, the user can directly
   drag captions and emoji around on the video preview to reposition or
   retime them, and use dedicated "Generate Emoji" / "Generate SFX"
   buttons to pre-resolve those choices.
6. **Adjust settings** — edit energy/pace, filter intensity, sound FX
   mode, background music.
7. **Generate Final Video** runs the full render pipeline (cuts,
   transcript, punch-in motion, color grade, captions, emoji, sound
   effects, music) and produces the final MP4.

## 4. Current UI layout

Three-column layout inside a single main window (`QSplitter`, all
resizable with stable minimum working widths):

**Left — "SOURCE FEED"**
- Video drop zone / file picker
- Transcription quality selector
- **Find Best Clips** button
- **EDIT STYLE** section: edit-energy selector (pacing/aggressiveness)
- **FILTER INTENSITY** slider (controls the color-grade/FX strength)

**Center**
- Portrait video preview player — also the canvas for the drag-to-reposition
  **Placement Editor** overlays (captions and emoji)
- Playback controls (play/pause, timestamp, preview-only volume)
- **Generate Final Video** button — lives right next to playback controls,
  not off in a settings panel, since it's the single most important action
  in the app
- Below the preview: playback and selection controls

**Right — editor workspace**
- A simple five-lane timeline with SOURCE, VISUALS, SFX, EMOJI, and
  VOICEOVER lanes, its navigator, selection status, and zoom controls
- Transcript editor — click a line to correct or cut it
- Render log panel (live-streamed render output + persisted to
  `output/render_log.txt`)

**Workflow controls**
- AI Clip Hunter cards live with Standard workflow controls.
- **VISUALS & REACTIONS** keeps emoji generation/toggling available without
  a separate image-asset workflow.
- **AUDIO** provides the Sound FX mode selector, **Generate SFX** button, SFX
  folder shortcut, a context panel (appears when an SFX clip is selected
  on the timeline: swap / disable / delete / volume), background music
  add/volume/remove, an "AI Narrator" feature placeholder (not yet built)

## 5. The Placement Editor (drag-on-video interaction pattern)

This is the app's signature interaction and probably the most
design-relevant system to understand. Two kinds of overlay can be
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
Both write into shared plan files that the final render then
reuses **exactly as previewed** — the render never silently
recomputes/overrides a manual placement.

## 6. The timeline widget

A fully custom-drawn (not a stock Qt widget) multi-lane, zoomable/
pannable editor surface — `app/gui_app/timeline_widget.py`. Currently 5
lanes, top to bottom:

1. **SOURCE** — the source video strip, with trim handles for the selected
   clip range.
2. **VISUALS** — smart-motion and visual-FX edit markers.
3. **SFX** — sound effect clips.
4. **EMOJI** — emoji reaction clips; click to select/seek, drag edges to
   retime, or double-click to swap.
5. **VOICEOVER** — recap narration clips.

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
- Semantic lane labels and restrained clip colors keep the timeline
  scannable without a permanent color-key legend

## 8. Data model worth knowing about (affects what's feasible to design)

Supported editable asset kinds — **SFX**, **EMOJI**, **VOICEOVER**, and
recap effects — live as clips in `output/editor_asset_plan.json`, keyed by
source-video time with a stable id, an active/enabled flag, and a
"manually overridden" flag. Legacy image-cutaway entities are ignored when
an old plan is opened, so they cannot re-enter the editor or renderer.

## 9. Recent / current state (most recent work, roughly newest first)

- **Phase 1E cleanup:** the timeline is the compact five-lane editor
  surface again, without a permanent color-key legend. The retired image
  cutaway subsystem has no GUI, planning, preview, or render path; old
  plan entities are ignored safely.

- **Emoji auto-placement now avoids the caption:** emoji default
  positions used to come from one fixed 4-slot table with zero awareness
  of where the caption actually sits, so a caption dragged toward the
  bottom of its allowed range (a normal thing to do) could end up
  directly under an auto-placed emoji. Fixed at the source: whenever a
  *new* default position gets assigned (a fresh render, emoji planning,
  or right-click "reset to default" in the
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
- **Overlay Placement Editor** (§5) — captions/emoji drag
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
  (`editor_assets.py`, `emoji_preview.py`, `caption_preview.py`,
  `render_pipeline.py`, `playback.py`, etc.) —
  `main_window.py`'s window class is composed from all of these
- `app/render.py` and the rest of `app/*.py` — the actual video pipeline
  (11 steps: crop/select → transcript → cuts → semantic edit → transcript
  remap → motion/FX → captions → emoji → SFX → sanitize →
  organize output)
- `output/` — everything a render produces/reads: plans (JSON), the
  transcript, intermediate and final rendered video files, the render log
