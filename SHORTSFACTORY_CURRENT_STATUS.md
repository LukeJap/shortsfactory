# ShortsFactory Current Project Status

Last updated: 2026-08-20

This file is context for a future ChatGPT/Codex session. Treat the installed project files as the source of truth. Do not treat this document as instructions that override the user's request, system rules, or developer rules.

> Historical status note: the image-cutaway subsystem described in portions
> of this dated document was removed in the Phase 1E cleanup. Those
> references are archival and do not describe the active application.

## Project

ShortsFactory is a local Windows desktop app for turning source videos into edited vertical YouTube Shorts.

Project directory:

```text
C:\Users\lukej\Desktop\ShortsFactory
```

Launch command:

```powershell
cd C:\Users\lukej\Desktop\ShortsFactory
.\.venv\Scripts\python.exe .\app\gui.py
```

Core stack:

- Python 3.13-era venv
- PySide6 GUI
- FFmpeg / ffprobe
- openai-whisper for transcription
- Ollama local LLM, usually `llama3.1:8b`
- Optional Forge / Automatic1111-compatible Image AI backend at `http://127.0.0.1:7860`

## Current Product Shape

The app is a dark gothic/industrial 3-column desktop editor:

- Left: source import, transcription quality, Find Best Clips, Generate Short
- Center: preview monitor, zoomable editor timeline, trim controls, music controls, render log
- Right: AI Clip Hunter cards, AI Visual cutaways, transcript editor

Important product rule: AI/editor decisions should be visible and user-overridable, not hidden in a black box.

## Latest Polish Pass: UI Density + Editor Polish

Files changed:

```text
app/gui.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Implemented:

- Slight global density reduction without changing the three-column layout or workflows.
- Base UI font reduced from 13px to 12px; headers, badges, utility labels, and console text were scaled down proportionally.
- General button padding reduced; primary `Generate Short` remains visually prominent.
- Left Source Feed initial width reduced from 280px to 258px, with a slightly shorter drag/drop zone.
- Preview transport strip tightened; PLAY/PAUSE now has a clearer active visual state.
- Preview volume slider has a slimmer editor-style track and handle.
- Editor timeline is visually denser:
  - smaller lane labels
  - less dominant grid
  - stronger cyan source clip
  - tighter IN/OUT handles
  - more compact navigator rail
- AI Clip Hunter cards are shorter and denser, with compact score formatting and clearer selected-card styling.
- AI Visual Cutaways controls/status/inspector are more compact; visual slot cards and thumbnails are smaller.
- Transcript Scrap margins/actions/list rows are tighter, giving more room to transcript content.
- Render Status is denser while giving the log more usable height and a cleaner console style.

Validation:

```text
Source scans confirmed the new compact constants/styles are present and old large target values were removed.
py_compile and live UI/offscreen layout testing could not run because the local Python executable is still blocked by the WindowsApps Python stub.
Visual review at the user's normal window size is still needed.
```

## Latest Hotfix: Preview Playback Black Screen / Play Button

Files changed:

```text
app/gui.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Implemented:

- Preview video loading now resolves and verifies the selected path before assigning it to `QMediaPlayer`.
- Loading a new video stops and clears the previous media source before assigning the new source.
- Play is no longer left disabled while waiting for only one ideal Qt media status.
- The app now treats `BufferingMedia` and `StalledMedia` as playable preview states.
- Duration arrival also re-enables Play, because it proves Qt loaded the media.
- After loading, the preview primes the first frame with a brief muted playback tick to avoid the Windows black-frame issue.
- If the player somehow has `NoMedia` when Play is clicked, it reloads the current source before trying playback.

Validation:

```text
ffprobe confirmed the checked source/rendered MP4 files have valid H.264 video and AAC audio streams.
py_compile could not run because the local Python executable is still blocked by the WindowsApps Python stub.
```

## Latest Milestone Slice: MAXIMUM Overdrive Pass 3 SFX Engine

Files changed:

```text
app/sfx_engine.py
app/render.py
app/gui.py
app/music_overlay.py
app/visual_emphasis.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Implemented:

- Added automatic sound design after emoji overlays and before rendered-folder organization.
- New inspectable artifact:

```text
output/sfx_plan.json
```

- Added `Sound FX` mode in the audio bed controls:
  - `AUTO` is the default.
  - `OFF` writes an empty plan and skips SFX.
  - `SFX Folder` opens/creates `assets/sfx`.
- Local SFX priority:
  - Reads `.wav`, `.mp3`, `.ogg`, and `.m4a` files in `assets/sfx`.
  - Uses descriptive filenames such as `whoosh`, `impact`, `bass`, `glitch`, `money`, `sparkle`, `doom`, `beep`, `rewind`, or `replay`.
  - Skips corrupt/unreadable local files and falls back instead of failing render.
- Procedural safe fallback SFX are generated with FFmpeg into:

```text
assets/sfx/generated
```

- The planner uses `output/visual_edit_plan.json` and `output/temporal_edit_plan.json`, including visual stack IDs and final output timings.
- Stack-aware selection keeps one strong audio decision per hero/coordinated visual stack.
- Energy budgets:
  - LOW: up to 2 subtle SFX
  - PUNCHY: up to 6 professional SFX
  - MAXIMUM: up to 12 aggressive but spaced SFX
- Collision protection skips events that are too close together.
- Dialogue protection:
  - non-hero dialogue-adjacent accents are gain-reduced
  - SFX are mixed under the existing final audio
  - final SFX mix uses an audio limiter
- Background music is preserved. If music is added after SFX, `app/music_overlay.py` reads `output/sfx_plan.json` and ducks music around planned SFX events.
- SFX failures are non-fatal; render continues and warnings are written into the plan/log.

Validation performed in the current shell:

```text
FFmpeg procedural whoosh fallback smoke: passed
FFmpeg music-ducking/amix/alimiter expression smoke: passed
Source scan confirmed GUI/render/music/SFX hooks are present.
```

Validation blocked:

```text
py_compile, full app launch, and full render pipeline could not run because the local Python launcher is still blocked by the WindowsApps Python stub.
No interactive listening QA is available in this Codex session; the next real render should be listened to for taste/volume balance.
```

## Latest Hotfix: Render Progress Bar

Files changed:

```text
app/gui.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Implemented:

- Added a render progress strip directly below the render status log.
- Progress strip shows:
  - current stage (`FRAMING`, `RENDERING`, `MUSIC MIX`, `COMPLETE`, or `FAILED`)
  - progress bar
  - elapsed time and estimated remaining time while rendering
  - final completion time when done
- Progress starts when `Generate Short` begins and advances through the existing reframe, render, and optional music stages.
- Progress is estimate-based, not exact FFmpeg telemetry; it is meant to give a useful time feel without blocking the UI.
- The render status area is back to the normal stacked layout under the editor content.
- The temporary render-status splitter/resizable-window experiment was removed.
- Added styling for the render progress bar, timing label, and stage badge.

Validation:

```text
Previous py_compile/offscreen smoke passed before the local Python launcher broke.
Current shell cannot execute .venv\Scripts\python.exe because pyvenv.cfg points to a WindowsApps Python stub that returns Access is denied.
```

## Latest Milestone Slice: MAXIMUM Overdrive Pass 2

Files changed:

```text
app/temporal_edit.py
app/subtitles.py
app/visual_emphasis.py
app/smart_motion.py
app/apply_ai_visuals.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Implemented:

- Added a dedicated temporal edit pass after final tight-video transcription and transcript correction.
- New debug artifact: `output/temporal_edit_plan.json`.
- Temporal events include `speed_up`, `speed_ramp`, `whip_transition`, `slow_down`, `freeze`, `micro_replay`, and `reverse_blip`.
- The pass mutates `output/rendered/short1_tight.mp4` before smart motion, AI visuals, visual FX, captions, and emoji overlays.
- The pass rewrites `output/subtitles.json` through a centralized source/edit-time to final-output-time map.
- Captions, caption emphasis, emoji events, smart motion, visual FX, and later music now use remapped final-time transcript timestamps.
- AI visual overlays now map through both the existing cut map and the new temporal map.
- New debug artifact: `output/ai_visual_mapped_plan.json`.
- The central `output/visual_edit_plan.json` now includes temporal events and the temporal time mapping summary.
- MAXIMUM smart motion now has harder fake-camera cuts, quick impact jolts, and stronger overshoot/settle behavior.
- PUNCHY can receive occasional restrained hard reframes, but replay/reverse remain MAXIMUM-only.

Safety rules:

- Temporal editing only operates on the already-approved `short1_tight.mp4`, so manual transcript cuts and automatic cuts remain authoritative.
- Speed-up/ramp/whip candidates are selected from word gaps.
- Slowdown ranges are short and use FFmpeg `atempo` to preserve pitch.
- Freeze/replay/reverse inserts use silence instead of repeating phonemes.
- Events crossing detected scene cuts are rejected.
- If the temporal FFmpeg pass fails, rendering logs a warning and continues without blocking the Short.

Validation performed in the current shell:

```text
ffmpeg/ffprobe available: C:\Users\lukej\ffmpeg\bin
temporal speed/freeze FFmpeg filter smoke: passed
temporal replay/reverse FFmpeg filter smoke: passed
smart-motion hard reframe/jolt zoompan expression smoke: passed
visual spot checks of generated smoke frames: nonblank and framed
```

Validation blocked:

```text
py_compile and full Python render pipeline could not run because .venv\Scripts\python.exe points to:
C:\Users\lukej\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe
That executable returns: Access is denied.
No interactive desktop/video-player control is available in this Codex session, so full muted/listening watch-through QA still needs user review after the Python environment is repaired.
```

## Latest Hotfix: Rendered Folder Organization

Files changed:

```text
app/gui.py
app/render.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Implemented:

- A successful render now keeps the finished video at:

```text
output/rendered/short1_captioned.mp4
```

- Intermediate/debug/artifact files in `output/rendered` are moved into:

```text
output/rendered/_components
```

- This is intentionally an organize-not-delete behavior, so useful debugging files are still available without cluttering the final output folder.
- The current `output/rendered` folder has already been organized with this rule.
- After a successful render, the GUI now opens `output/rendered/short1_captioned.mp4` automatically using the Windows default video app.

Validation:

```text
Current output/rendered top level:
_components
short1_captioned.mp4

py_compile could not run because the local Python executable is still blocked by the WindowsApps Python stub.
```

## Latest Hotfix: Spacebar In Transcript Correction Editor

Files changed:

```text
app/gui.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Implemented:

- The global Space play/pause shortcut now treats `QPlainTextEdit` as a text editor.
- This lets Space insert normal spaces in the `Correct Transcript` multiline editor instead of pausing/playing preview video.
- Existing Space playback shortcut remains active outside text-entry widgets.

Validation:

```text
Source scan confirmed `QPlainTextEdit` is imported and included in the text-editor focus guard.
py_compile could not run because the local Python executable is still blocked by the WindowsApps Python stub.
```

## Latest Milestone Slice: MAXIMUM Overdrive Pass 1

Files changed:

```text
app/visual_emphasis.py
app/visual_fx.py
app/make_captions.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Generated/updated validation artifacts:

```text
output/rendered/short1_punchy_overdrive_test.mp4
output/rendered/short1_maximum_overdrive_test.mp4
output/visual_fx_plan_punchy_overdrive_test.json
output/visual_fx_plan_maximum_overdrive_test.json
output/visual_edit_plan_punchy_overdrive_test.json
output/visual_edit_plan_maximum_overdrive_test.json
output/rendered/qa_punchy_15s.jpg
output/rendered/qa_maximum_15s.jpg
output/rendered/qa_punchy_24s.jpg
output/rendered/qa_maximum_24s.jpg
```

Implemented:

- MAXIMUM is now architecturally different from PUNCHY instead of a simple multiplier.
- Added semantic effect recipes:
  - `wtf_chaos`
  - `money`
  - `doom_negative`
  - `hype_win`
  - `fail_awkward`
  - `creepy_cold`
  - `nostalgia_memory`
  - `reaction`
  - `speech_emphasis`
- Added a time-varying intensity model with structural regions:
  - `hook`
  - `setup`
  - `build`
  - `payoff`
  - `ending`
- `output/visual_fx_plan.json` and `output/visual_edit_plan.json` now expose:
  - `intensity_model`
  - `intensity_curve`
  - `semantic_recipe_counts`
  - `hero_moments`
  - per-event `recipe`
  - per-event `region`
  - per-event `intensity`
  - per-event `stack_id`
  - per-event `coordinated_stack`
- MAXIMUM now selects semantic moments first and expands them into coordinated stacks.
- Coordinated stack events are marked as deliberate in collision notes rather than treated as accidental collisions.
- PUNCHY remains cleaner: selected moments generally become one filter accent each.
- MAXIMUM base grade is now stronger:
  - more contrast
  - more saturation
  - slightly darker tonal separation
  - stronger sharpening
  - heavier vignette
- Added/strengthened renderable FFmpeg effect vocabulary:
  - `rgb_split`
  - `posterize_hit`
  - `bloom_flash`
  - `spotlight`
  - `detail_hit`
  - `overdrive_flash`
  - stronger `magenta_hype`
  - existing contrast/desat/cold/warm/money/danger/glitch effects remain
- MAXIMUM captions are larger for emphasis/impact/extreme moments.
- Added a magenta semantic caption accent for chaos/hype words.
- MAXIMUM smart motion budget increased through the shared energy profile.

PUNCHY vs MAXIMUM test:

```text
Source/base context:
Same current selected SpongeBob source/base edit.

PUNCHY:
output/rendered/short1_punchy_overdrive_test.mp4
base_look: viral_pop
intensity_model: clean_single_accent_moments
moments: 4
events: 4
hero moments: 0
recipes: reaction=3, wtf_chaos=1

MAXIMUM:
output/rendered/short1_maximum_overdrive_test.mp4
base_look: maximum_overdrive
intensity_model: semantic_recipes_with_time_curve
moments: 7
events: 24
hero moments: 3
recipes: speech_emphasis=1, hype_win=1, reaction=4, wtf_chaos=1
```

Limited visual QA performed:

- Extracted matched still frames at ~15.20s and ~24.25s for PUNCHY and MAXIMUM.
- MAXIMUM frames were visibly more aggressive: colder/heavier grade, RGB stripe accents, centered slam text, larger caption treatment, and stronger impact look.
- Checked sampled frames for obvious text clipping; captions and slam text remained inside the frame.

Important limitation:

- I could not perform a full real-time muted watch-through in an interactive video player from this environment. User visual review of both comparison MP4s is still required.

Known weaknesses / deferred to later passes:

- Freeze-frame treatment is represented visually through posterize/flash/graphic stacks, not true time-freezing.
- No reaction PNG library, editable FX clips, or preset system were added in Pass 1. Later passes added temporal edits and the automatic SFX engine.
- RGB split is a lightweight FFmpeg-safe stripe/color accent, not true per-channel displacement.
- Shot-type analysis still logs the existing `cv2.CascadeClassifier` warning on this machine, then smart motion continues with fallback behavior.
- Render pipeline still uses multiple encode stages; this pass did not attempt a risky renderer rewrite.

## Latest Milestone Slice: Brainrot Editing + Editable Cyan Source Clip

Files changed:

```text
app/gui.py
app/subtitles.py
app/visual_emphasis.py
app/visual_fx.py
app/make_captions.py
app/smart_motion.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Generated/updated output files during validation:

```text
output/rendered/short1_base.mp4
output/rendered/short1_tight.mp4
output/rendered/short1_captioned.mp4
output/captions.ass
output/emoji_events.json
output/visual_fx_plan.json
output/visual_edit_plan.json
output/smart_motion_plan.json
```

Implemented from the viral brainrot editing/source trim pass:

- The active source range is now represented as a real V1 source clip on the editor timeline.
- The source clip uses a cyan media-clip body instead of the old passive gray backing.
- The cyan clip is selectable; selected state uses a brighter cyan outline/edges.
- Hovering the left/right clip edges uses a horizontal trim cursor.
- Dragging the left edge trims the active source IN.
- Dragging the right edge trims the active source OUT.
- Dragging the center of the selected source clip moves the selected range while preserving duration and clamps to source duration.
- Timeline trimming updates the shared `selection_start` / `selection_end` state, so the old Set Start/Set End, AI candidate selection, preview loop, render settings, and render command all remain synchronized.
- During timeline trimming/body dragging, the preview player seeks to the adjusted source IN/OUT so the user gets immediate frame feedback.
- The timeline now shows compact markers for:
  - cyan source clip and cyan scene cuts
  - red manual cuts
  - amber transcript corrections
  - gold caption emphasis
  - violet smart motion
  - magenta filter FX
  - green graphics/AI visual moments
- Added `app/visual_fx.py`, a non-AI FFmpeg visual FX stage that runs before captions are burned.
- Every export now gets a baseline visual grade based on Edit Energy:
  - `LOW`: subtle contrast/saturation/sharpening
  - `PUNCHY`: stronger pop, sharpening, vignette
  - `MAXIMUM`: aggressive brainrot contrast/saturation/sharpening/vignette
- Dynamic filter events now include contrast hits, exposure/contrast flashes, desaturation, cold blue, warm gold, green money, red danger, magenta hype, and glitch-style drawbox accents.
- Local motion graphics now include `slam_text` overlays for number/money/extreme words and major reaction words in PUNCHY/MAXIMUM.
- `drawtext` graphics use an explicit Windows font path fallback because FFmpeg's default fontconfig lookup fails in this local environment.
- `subtitles.py` now invokes smart motion, AI visuals, then the visual FX pass before caption generation/burn. If Image AI or visual FX fails, the pipeline logs a warning and continues.
- `visual_emphasis.py` now supports `EXTREME` word classification, per-energy filter budgets, and loading visual FX events into the central `visual_edit_plan.json`.
- Captions now include `NORMAL`, `EMPHASIS`, `IMPACT`, and `EXTREME` sizing/animation levels, with heavier stroke/shadow for bigger moments.
- Smart motion has a richer movement vocabulary: punch in, punch out, slow push, directional push, and impact punch.
- MAXIMUM mode now selects more frequent/harder smart motion events and reaction graphics.

Current representative MAXIMUM validation render:

```text
Source:
C:\Users\lukej\Desktop\ShortsFactory\input\SpongeBob SquarePants S02E09 Survival of the Idiots&Dumped 1080p SKST WEB-DL DD 2 0 H 264-playWEB.mkv

Source selection:
380.34s -> 418.263s

Final review file:
output/rendered/short1_captioned.mp4
```

Current `output/visual_fx_plan.json` for that sample:

```text
Edit energy: MAXIMUM
Base look: brainrot
Dynamic FX events: 7
1. contrast_hit at 5.61s, trigger=paneling!
2. warm_gold at 7.14s, trigger=right,
3. slam_text WHO at 10.94s
4. slam_text WHAT at 15.16s
5. slam_text OW! at 24.20s
6. slam_text OW! at 27.56s
7. slam_text OW! at 34.50s
```

Validation:

```text
py_compile app/gui.py app/visual_fx.py app/subtitles.py app/visual_emphasis.py app/make_captions.py app/smart_motion.py app/render.py: passed

visual FX planner smoke:
MAXIMUM 7 [('filter', 'contrast_hit'), ('filter', 'warm_gold'), ('graphic', 'slam_text'), ...]

drawtext font smoke:
FFmpeg drawtext failed without explicit fontconfig.
FFmpeg drawtext passed with C:/Windows/Fonts/arialbd.ttf.

full sample render:
app/render.py --source <SpongeBob MKV> --start 380.34 --end 418.263: passed

visual FX rerun after font fix:
Visual FX applied.

caption/emoji final burn:
final_caption_emoji_steps complete

timeline brainrot smoke:
timeline_brainrot_smoke True 1 2 2 980 260
```

Known limitations / next visual-review notes:

- I could not perform an actual human watch-through in an interactive video player from this environment. The final aesthetic QA still needs user review of `output/rendered/short1_captioned.mp4`.
- Only the MAXIMUM representative render was exported in this pass. LOW and PUNCHY code paths exist, but three-way visual comparison exports were not produced.
- The local motion graphics are currently slam-text overlays. Counters, arrows, maps, progress meters, comparison graphics, freeze-frame bundles, and transition vocabulary are not fully built out yet.
- AI Visual architecture was preserved, but AI visual category expansion was not deeply implemented in this slice; offline Image AI still skips cleanly.
- The render pipeline still has multiple encode stages. This pass added the visual FX stage without a larger renderer consolidation.
- The source clip timeline behavior was verified with headless widget tests. A visible manual long-source workflow test is still recommended.

## Latest Milestone Slice: Visual Editing Stage 1-3 Start

Files changed:

```text
app/gui.py
app/render.py
app/make_captions.py
app/smart_motion.py
app/visual_emphasis.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Generated/updated output files during validation:

```text
output/render_settings.json
output/captions.ass
output/emoji_events.json
output/visual_edit_plan.json
```

Implemented from the staged viral visual editing plan:

- Added user-facing `Edit Energy` control with `LOW`, `PUNCHY`, and `MAXIMUM`.
- Default/persisted energy is `PUNCHY`.
- GUI writes `output/render_settings.json` before rendering.
- `render.py`, `make_captions.py`, and `smart_motion.py` now read the shared render settings.
- Added `app/visual_emphasis.py` as the first shared coordination layer.
- Caption generation now classifies important words as `NORMAL`, `EMPHASIS`, or `IMPACT`.
- Caption emphasis detects curated reaction words, negation, numbers, money/date-like values, punctuation, and strong emotional words.
- Captions now use semantic size/color/pop tags while preserving karaoke word timing.
- Emoji density now follows edit energy:
  - `LOW`: sparse
  - `PUNCHY`: current default density
  - `MAXIMUM`: can allow one extra good reaction
- Smart motion now uses edit energy for event count, spacing, duration, and zoom vocabulary.
- `output/visual_edit_plan.json` is now written as an inspectable coordination/debug plan containing caption emphasis, emoji, smart motion, and AI visual events where available.
- The visual plan annotates near-time priority collisions instead of hiding them.

Validation:

```text
py_compile app/gui.py app/render.py app/make_captions.py app/smart_motion.py app/visual_emphasis.py: passed

visual emphasis helper smoke:
LOW 2
PUNCHY 4
MAXIMUM 6
never -> IMPACT / negation
$12,000 -> IMPACT / money
huge -> EMPHASIS
ordinary -> NORMAL

GUI settings smoke:
edit_energy PUNCHY PUNCHY
has_combo True

caption generation smoke:
Edit energy: PUNCHY
Caption events: 73
Caption emphasis events: 7
Visual edit plan events: 13
Emoji candidates found: 5
Emoji events selected: 2

smart motion selector smoke:
LOW selected 2 motion events
PUNCHY selected 4 motion events
MAXIMUM selected 6 motion events

render CLI import/parse:
app/render.py --help: passed
```

Remaining risks:

- Full FFmpeg export and visual review were not run in this slice.
- The new visual edit plan is currently inspectable/debuggable; it does not yet fully suppress lower-priority render events.
- Filter events and native motion graphics are not implemented yet.
- Caption styling is generated through ASS tags and needs real rendered-video review for final size/color tuning.

## Previous Hotfix: Preview Play Button Reliability

Files changed:

```text
app/gui.py
SHORTSFACTORY_CURRENT_STATUS.md
```

User-reported issue addressed:

- Clicking `PLAY` could appear to do nothing in at least two editor states:
  - immediately after loading a source while Qt was still in `LoadingMedia`
  - right after a timeline seek, while the app's internal muted paused-frame refresh had briefly put `QMediaPlayer` into `PlayingState`

Fixes:

- Source load now disables the play button and labels it `LOADING` until Qt reports `LoadedMedia` or `BufferedMedia`.
- Explicit user Play now cancels any pending muted seek-refresh state before starting playback.
- User Play now takes ownership of the player even if the internal refresh was already in `PlayingState`.
- The play button now ignores internal refresh playback when deciding whether to show `PLAY` or `PAUSE`.
- A delayed playback-start verification logs a clear preview playback message if Qt refuses to start.
- Qt media status/error hooks now report media load or codec failures to the render log and terminal.

Validation:

```text
py_compile app/gui.py: passed

immediate-after-load smoke:
after_load False LOADING MediaStatus.LoadingMedia
immediate_disabled_click PlaybackState.StoppedState 0 False LOADING
after_loaded True PLAY MediaStatus.LoadedMedia
after_click_loaded PlaybackState.PlayingState PAUSE
after_wait PlaybackState.PlayingState True PAUSE

pending seek-refresh smoke:
pending_before_click True PlaybackState.PlayingState PLAY
pending_after_click False PlaybackState.PlayingState PAUSE
pending_after_wait PlaybackState.PlayingState True PAUSE
```

## Previous Hotfix: Custom Emoji Asset Incorporation

Files changed:

```text
app/make_captions.py
app/emoji_overlay.py
SHORTSFACTORY_CURRENT_STATUS.md
```

User-reported request addressed:

- New `.png` and `.gif` files placed in `assets/emoji` are now indexed as local reaction assets when their filenames are descriptions rather than Unicode codepoint filenames.
- Existing Twemoji codepoint files such as `1f440.png` and `2705.png` are still treated as Unicode cache files and are not indexed as custom reactions.
- Caption generation now prefers a matching local reaction asset before falling back to Unicode emoji.
- Generated `output/emoji_events.json` events can now include:
  - `asset_path`
  - `asset_description`
  - `asset_type: local`
- The emoji renderer now uses `asset_path` directly when present.
- Local PNG assets render as static overlays.
- Local GIF assets are passed to FFmpeg as animated image inputs.

Examples from the current `assets/emoji` folder:

```text
right -> assets/emoji/58588-thumbs-up-joe.png
think -> assets/emoji/339345-cathink.png
died -> assets/emoji/25174-skull-lmfao.gif
time -> assets/emoji/450135-timeisticking.png
secret -> assets/emoji/54836-shhh.png
sad -> assets/emoji/84710-joesad.png
wow -> assets/emoji/5518-joe-wow.png
funny -> assets/emoji/25174-skull-lmfao.gif
```

Validation:

```text
py_compile app/make_captions.py app/emoji_overlay.py: passed
custom_asset_count 27
right thumbs up joe assets/emoji/58588-thumbs-up-joe.png local
think cathink assets/emoji/339345-cathink.png local
died skull lmfao assets/emoji/25174-skull-lmfao.gif local
time timeisticking assets/emoji/450135-timeisticking.png local
secret shhh assets/emoji/54836-shhh.png local
sad joesad assets/emoji/84710-joesad.png local
wow joe wow assets/emoji/5518-joe-wow.png local
funny skull lmfao assets/emoji/25174-skull-lmfao.gif local
event asset resolution smoke: passed
```

Remaining risk:

- Full FFmpeg render with animated GIF overlays was not run in this pass.

## Previous Hotfix: Selection Loop / Preview Volume / Transcript Cache / Timeline Labels

Files changed:

```text
app/gui.py
app/subtitles.py
SHORTSFACTORY_CURRENT_STATUS.md
```

User-reported issues addressed:

- Highlighted timeline selections now act like preview loops while playback is active and the playhead is inside the selected IN/OUT range.
- Clicking or scrubbing outside the selected highlighted range disables that selection loop.
- The selection readout appends `LOOP` while the selected range is actively looping.
- A `Preview` volume slider was added to the playback controls. It changes only the editor `QAudioOutput` preview volume and does not affect rendered/exported video audio.
- The timeline lane labels were simplified to one-line labels (`V1 SOURCE`, `EDITS`, `VISUALS`) to avoid the previous overlapping/cluttered text in the left rail.
- Transcript cache handling now checks the old pre-quality `base` cache identity and migrates it to the newer quality-aware cache identity when valid.
- Transcript cache miss logs now explain whether cache was bypassed, stale identity was found, or no matching cache file existed.

Important behavior notes:

- A cache miss can still be legitimate if the source path, file size, modified timestamp, selected model, or quality mode differs.
- The preview volume setting is saved under `preview/volume` in `QSettings`.
- Selection loop behavior is preview-only. It does not change trim metadata or render output.

Validation:

```text
py_compile app/gui.py app/subtitles.py: passed
offscreen GUI smoke:
loop_inside True True True
loop_after_outside_seek False False
preview_volume 0.35 35%
timeline_render True 900 260

transcript cache helper smoke:
legacy_valid True
migrated_valid True
quality_model AUTO base
cache_names_differ True
```

## Previous Hotfix: Timeline Scroll / Transcript Dialog Color

Files changed:

```text
app/gui.py
SHORTSFACTORY_CURRENT_STATUS.md
```

Screenshot-driven fix:

- The center editor column is now wrapped in a `QScrollArea`, so the preview/timeline/control stack scrolls vertically instead of clipping the timeline when the window is short.
- The preview video minimum height was reduced from 360 px to 260 px.
- The preview/timeline splitter now has a stronger minimum height and gives the timeline more protected space.
- The timeline panel minimum height was increased to 260 px.
- Transcript correction dialogs now style their editable text controls as black text on a white background.

Validation:

```text
py_compile app/gui.py: passed
short-window layout smoke:
center_scroll_max 517
timeline_height 174
splitter_height 590
paint_ok True
```

## Previous Milestone: Critical Timeline / Playback Correction

Files changed:

```text
app/gui.py
SHORTSFACTORY_CURRENT_STATUS.md
```

### Cause Of Playback Regression

The previous timeline pass put the custom timeline inside the same horizontal row as the play button and time labels. That made the timeline visually and behaviorally collapse back toward a thin progress slider.

The paused seek helper also briefly played the media and scheduled a pause after seeks. During frequent scrubbing, that could fight the user's intended playback state.

### Playback Fix

Playback is now deliberate and obvious:

- The preview control uses a clear `PLAY` / `PAUSE` button.
- Loading a video no longer auto-starts playback.
- The play button is disabled before a source is loaded and enabled after load.
- Clicking `PLAY` starts the existing `QMediaPlayer`.
- Clicking `PAUSE` pauses it.
- Pending paused-seek refreshes are cancelled when the user explicitly presses Play.

Important locations:

```text
app/gui.py:3419   PLAY button creation
app/gui.py:4323   toggle_playback
app/gui.py:4412   seek_video
```

### Keyboard Behavior

Editor-wide keyboard behavior was added with an app-level event filter:

- `Space`: Play/Pause
- `F`: Fit Selection
- `Ctrl+0`: Fit Source

Space/F/Ctrl+0 are not hijacked when the event target is an editable text field such as a transcript correction field, AI visual prompt editor, or line edit.

Important locations:

```text
app/gui.py:9718   text_editor_has_focus
app/gui.py:9754   handle_editor_shortcut
```

### Timeline Surface

The detailed timeline is now taller and lane-based instead of a thin slider:

- Time ruler across the top.
- Full-height fresh-cut red playhead.
- Primary `V1 SOURCE` video lane.
- `EDIT` lane for transcript cuts, transcript corrections, scene cuts, and punch-in markers.
- `VIS` lane for AI visual ranges.
- IN/OUT selection uses legible vertical handles and a visible selected range overlay.
- Exact IN/OUT time is shown while dragging a handle.

All existing markers continue to use the same absolute source timestamp mapping.

Important location:

```text
app/gui.py:193    SuggestionSlider
```

### Navigator / Viewport

A custom full-source navigator replaced the old thin nav slider:

- The navigator shows the entire source.
- The highlighted thumb represents the visible detailed timeline viewport.
- Drag the thumb center to pan through the source.
- Drag the left or right thumb edge to resize the viewport.
- Smaller thumb means deeper zoom; larger thumb means more source visible.
- Ctrl+wheel zoom and the navigator update the same `viewport_start` / `viewport_end` state.

Important locations:

```text
app/gui.py:2268   TimelineNavigator
app/gui.py:4518   timeline_navigator_changed
```

### Resizable Timeline Area

The preview and timeline now live inside a vertical splitter, so the user can allocate more space to the timeline for precise editing.

Important location:

```text
app/gui.py:3541   preview_timeline_splitter
```

### Validation Run For This Correction

Syntax check:

```powershell
.\.venv\Scripts\python.exe -m py_compile app\gui.py app\analyze.py app\subtitles.py
```

Result: passed.

Headless GUI/timeline smoke test:

```text
fit_selection_viewport 166600 207400
scrub_value 777553
zoom_viewport (0, 1470000) (412500, 1147500)
navigator_pan (412500, 1147500) (730338, 1465338)
fit_source_viewport 0 1470000
space_line_handled False
space_timeline_handled True
load_enabled True PLAY StoppedState
after_play_toggle PAUSE PlayingState
after_pause_toggle PLAY PausedState
paint_ok True True
```

This used a simulated 24:30 source plus local `input/short1.mp4` for a best-effort offscreen playback toggle check.

Image AI preservation check:

```powershell
.\.venv\Scripts\python.exe app\image_backend_status.py
```

Result: expected offline JSON, no crash.

### Remaining Risks

- A visible desktop/manual test with an actual 20+ minute source still needs to be performed by the user.
- Full render regression was not run.
- The timeline is now structurally closer to an NLE, but visual polish should be reviewed in the real app window.
- Draggable AI Visual timeline clips were not implemented.

## Previous Milestone: Editor Foundation / Accuracy / Style Pass

Files changed:

```text
app/gui.py
app/analyze.py
app/subtitles.py
SHORTSFACTORY_CURRENT_STATUS.md
```

### Timeline Foundation

`app/gui.py` now has a professional editor-style timeline viewport.

Key behavior:

- Source duration and visible viewport are separate concepts.
- Edit data remains stored in absolute source timestamps.
- Zooming and panning only change the time-to-pixel mapping.
- Ctrl + mouse wheel over the timeline zooms progressively.
- Zoom is cursor-centered so the timestamp under the pointer stays approximately under the pointer.
- Shift + mouse wheel pans horizontally when zoomed in.
- A subtle navigation slider allows horizontal movement through long source media.
- `FIT` button and `F` key fit the current IN/OUT selection.
- `SRC` button and `Ctrl+0` fit the full source.
- Clicking AI candidates on long videos auto-focuses the candidate selection instead of leaving it as a tiny sliver.
- Timeline overlays share the same viewport transform:
  - Purple: AI candidate ranges
  - Red: manual transcript cuts
  - Amber: transcript corrections
  - Cyan: scene/camera cuts
  - Violet: smart motion / punch-ins
  - Green: AI visual cutaways

Important locations:

```text
app/gui.py:171    SuggestionSlider
app/gui.py:421    fit_selection
app/gui.py:450    zoom_around
app/gui.py:590    horizontal_pan
app/gui.py:3370   timeline_viewport_changed
```

### Layout / Scrolling

`app/gui.py` now uses resizable splitters:

- Main horizontal splitter between source, center editor, and right inspector stack.
- Right vertical splitter between AI Clip Hunter, AI Visuals, and Transcript.
- Splitter state is persisted with `QSettings`.

Scroll fixes:

- AI Clip Hunter content is wrapped in a scroll area.
- AI Visual content is wrapped in a scroll area.
- Transcript remains list-based and scrollable.
- Scrollbars were styled to match the new editor aesthetic.

Important locations:

```text
app/gui.py:2306   main QSplitter
app/gui.py:2594   right QSplitter
app/gui.py:3152   AI Clip Hunter scroll area
app/gui.py:3158   AI Visuals scroll area
app/gui.py:8582   restore_layout_settings
```

### Transcription Quality

The current installed transcription backend is `openai-whisper`.

Observed package state:

```text
openai-whisper: installed, version 20250625
faster-whisper: not installed
```

`app/subtitles.py` previously hardcoded the Whisper `base` model. It now accepts:

```powershell
--quality AUTO
--quality FAST
--quality ACCURATE
```

Current local model plan:

- `FAST`: `base`
- `AUTO`: `small`, then fallback to `base`
- `ACCURATE`: `medium`, then fallback to `small`, then fallback to `base`

Cache identity now includes:

- engine
- quality
- model
- compute mode
- language
- source file identity

This prevents transcripts from different quality/model runs from colliding in the cache.

The GUI now exposes a compact `TRANSCRIPTION` selector beside the source controls and passes the selected quality into the AI Clip Hunter transcription stage.

Important caveat: a real faster-whisper benchmark and before/after transcript comparison have not been completed yet. `faster-whisper` is absent from the venv, and no model download/install was performed in this pass.

### AI Clip Hunter Quality

`app/analyze.py` and `app/gui.py` now reject or repair generic editor language such as:

```text
becomes the center of attention
clear setup and payoff
engaging conversation
interesting moment
```

Fallback hooks and card text are now grounded in actual transcript excerpts. Example checked from the current SpongeBob analysis:

```text
Old generic hook detected: true
Transcript excerpt: SpongeBob, did you hear that?
New hook: SpongeBob, did you hear that
New reason: The candidate is anchored by the line "SpongeBob, did you hear that?".
```

Important locations:

```text
app/analyze.py:29     generic phrase list
app/analyze.py:1269   grounded_title_from_text
app/analyze.py:1279   grounded_reason_from_text
app/analyze.py:1286   make_specific_hook
app/analyze.py:1348   normalize_candidate_clip
app/gui.py:7158       populate_clip_cards
```

### Style Pass

The editor has been restyled toward a harder gothic/punk industrial identity:

- darker steel/black panels
- blood/rust accents
- sharper panel corners
- heavier badges and labels
- styled splitters and scrollbars
- less dashboard-like spacing
- timeline ruler and overlays now read more like an editing surface

Important location:

```text
app/gui.py:8629   apply_style
```

## Image AI Status

Image AI was preserved. Live backend testing is deferred while Forge/checkpoint installation is still in progress.

Current status check result when Forge is not running:

```json
{"state": "offline", "message": "Could not connect to Image AI.", "models": []}
```

No Forge install, checkpoint download, provider switch, or live image generation debugging was performed in this milestone.

Existing Image AI files remain:

```text
app/image_backend_status.py
app/generate_ai_visual_assets.py
app/apply_ai_visuals.py
```

## Validation Run

Syntax check:

```powershell
.\.venv\Scripts\python.exe -m py_compile app\gui.py app\analyze.py app\subtitles.py
```

Result: passed.

Image AI status:

```powershell
.\.venv\Scripts\python.exe app\image_backend_status.py
```

Result: expected offline JSON, no crash.

Subtitle CLI quality argument check:

```powershell
.\.venv\Scripts\python.exe app\subtitles.py --quality FAST missing_input.mp4
```

Result: parsed `FAST`, selected `base`, then correctly stopped because the test input file did not exist.

Headless GUI/timeline smoke test:

```text
quality AUTO
quality_after ACCURATE
paint_ok True
viewport 621800 642200
splitters True True
```

The test created the GUI offscreen, changed the transcription combo, rendered the custom timeline, fit a 30-second selection inside a simulated 60-minute source, zoomed, panned, and confirmed both splitters exist. The saved test preference was restored to `AUTO` afterward.

Analyzer fallback check:

```text
old_hook_generic True
quote SpongeBob, did you hear that?
new_hook SpongeBob, did you hear that
new_reason The candidate is anchored by the line "SpongeBob, did you hear that?".
```

## Not Yet Completed

These are still real follow-ups:

- Full manual GUI regression in a visible desktop session.
- Full render regression after the timeline/layout/style changes.
- Real 20+ minute source-video timeline test.
- faster-whisper install/integration decision.
- Actual transcription benchmark on this machine.
- Before/after transcript comparison for dialogue-heavy footage.
- Observed GPU/VRAM behavior for larger transcription models.

## Editor Interactivity / Asset Persistence Pass

Implemented in this pass:

- Added `output/editor_asset_plan.json` as the central persistent plan for editable `AI_VISUAL` and `SFX` clips.
- Added a pinned render progress footer below the workspace so stage, progress, and time stay visible while the center column scrolls.
- Added background transcript preload on source load using the existing `subtitles.py` cache identity rules.
- Updated Find/Refresh Clips to reuse a preloaded/cached transcript or wait for the active preload instead of launching duplicate transcription.
- Added source-safety checks so finished preload output is ignored if it belongs to another video.
- Added editable timeline asset clips with a dedicated `SFX` lane and selectable/draggable/trimmable AI visual and SFX blocks.
- Added SFX selection controls, SFX swap from `assets/sfx`, disable/enable, and preview playback triggers during editor playback.
- Added AI visual variant persistence with `KEEP`, `GENERATE MORE`, previous/next selection, and active variant preservation.
- Updated AI visual generation to create stable `visual_##_variant_###` assets when generating more.
- Updated AI visual compositing to prefer active clips from `editor_asset_plan.json`.
- Updated SFX mixing and music ducking to use edited SFX timing from the final editor asset plan.
- Added approximate live AI visual preview overlay in the source monitor when the playhead enters an active visual clip.

Known caveats from this pass:

- Sandboxed Python validation was blocked by the WindowsApps Python 3.13 stub, but the same `.venv\Scripts\python.exe -m py_compile ...` check passed when run outside the sandbox.
- Visible desktop testing and a real end-to-end render should still be performed.
- SFX preview is editor-layer triggering, not sample-accurate waveform playback.
- AI visual live preview is an identity/timing preview, not full Ken Burns render simulation.

## Image AI Auto-Launch / Editable SFX Preview Pass

Implemented in this pass:

- Added Forge auto-launch support through `app/image_backend_status.py`.
- Forge launch target is `C:\AI\Forge\run.bat`; API target remains `http://127.0.0.1:7860`.
- Image AI actions now request auto-launch on demand instead of starting Forge when the app opens.
- Added a launch lock at `output/image_ai_launch.lock` so repeated checks/generation attempts do not spawn duplicate Forge instances while the backend is still starting.
- `CHECK IMAGE AI`, model switching, `GENERATE ASSETS`, `REGENERATE`, and `GENERATE MORE` now route through the auto-launch-aware backend path.
- `app/generate_ai_visual_assets.py` can auto-launch Forge before deciding whether to use real generation or preview placeholders.
- Added a visible `Generate SFX` button in the editor audio strip.
- Added SFX editor planning mode in `app/sfx_engine.py` via `--editor-plan`.
- Editor SFX generation writes editable clips into `output/editor_asset_plan.json` without requiring a rendered `.mp4`.
- Added SFX delete and selected-clip volume controls in the editor.
- SFX clips now preserve `time_basis`; source-timed editor SFX are converted back to final-output timing during render.
- SFX preview triggering and timeline display account for source-timed vs final-output-timed clips.

Validation:

```powershell
.\.venv\Scripts\python.exe -m py_compile app\gui.py app\sfx_engine.py app\image_backend_status.py app\generate_ai_visual_assets.py
```

Result: passed.

Safe backend probe without auto-launch:

```powershell
.\.venv\Scripts\python.exe app\image_backend_status.py
```

Result: returned `state: offline`, `started_by_shortsfactory: false`, and did not launch Forge.

SFX editor-plan CLI check:

```powershell
.\.venv\Scripts\python.exe app\sfx_engine.py --help
```

Result: passed and showed `--editor-plan`, `--selection-start`, and `--selection-end`.

Not manually tested yet:

- Clicking `CHECK IMAGE AI` and confirming Forge launches from `C:\AI\Forge\run.bat`.
- Clicking `GENERATE ASSETS` while Forge is closed and confirming it waits for Forge, then generates real images.
- Clicking `Generate SFX`, dragging/trimming/muting/swapping/deleting clips, and verifying final render timing.

## Handoff Reminder

Before making more changes:

1. Inspect the actual installed files in `app/`.
2. Treat the installed project as source of truth.
3. Preserve transcript editing, manual cuts, corrections, scene markers, reframing, punch-ins, captions, emojis, music, rendering, and Image AI UX.
4. Keep local AI failures non-fatal.
5. Do not debug Forge/Image AI live generation until the user says the backend installation is ready.
