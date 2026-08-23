# ShortsFactory — Development Log: macOS Environment Setup, Repo Cleanup, `gui.py` Split

Date: 2026-08-22

This document describes a batch of environment-setup fixes, repository
cleanup, and a structural refactor performed while getting ShortsFactory
running on a fresh macOS (Apple Silicon) machine. The app was originally
developed and validated on Windows (see `SHORTSFACTORY_CURRENT_STATUS.md` /
`SHORTSFACTORY_RECOVERY_AUDIT.md`), so a meaningful amount of the work here
is closing gaps that only show up when running on a different OS with a
from-scratch `.venv`.

No product behavior was intentionally changed anywhere in this pass. Every
change below is either an environment/tooling fix, a repo-hygiene cleanup,
or a pure structural code move.

## 1. macOS development environment setup

### 1.1 Editor/interpreter selection

VS Code had no `.vscode/settings.json`, so its Python extension wasn't
reliably resolving to the project's `.venv`, producing a spurious "PySide6
not found" error in the editor even though PySide6 was installed correctly.
Fixed by adding `.vscode/settings.json` with
`python.defaultInterpreterPath` pointing at `${workspaceFolder}/.venv/bin/python`.

### 1.2 Missing Python packages

The `.venv` was missing several packages the app imports at runtime. None of
these were ever declared anywhere in the repo (there is still no
`requirements.txt` / `pyproject.toml`), so each was found by running the app
and reading the resulting traceback:

- `requests` — used directly by several `app/*.py` scripts (e.g.
  `app/plan_short.py`, `app/image_backend_status.py`) for local HTTP calls to
  Ollama and the Image AI backend.
- `opencv-python` (imported as `cv2`) — used by `app/shot_type.py` and
  related reframing/analysis code.
- `openai-whisper` (imported as `whisper`) — the transcription engine used
  by `app/subtitles.py`.

### 1.3 The Rosetta / numpy / torch issue

Installing `openai-whisper` pulled in `torch` as a dependency. On this
machine, `torch` resolved to **2.2.2** — an old version — because the
project's `.venv` is built on an **x86_64 Homebrew Python** running under
Rosetta translation, despite the Mac itself being Apple Silicon (arm64).
PyTorch stopped publishing macOS x86_64 wheels after 2.2.2, so pip is
permanently capped at that version on this interpreter.

Torch 2.2.2 predates full NumPy 2.x support. Installing it alongside a
default NumPy 2.x resulted in a hard ABI crash the moment `torch`
interoperated with a NumPy array (`RuntimeError: Numpy is not available`).

Fix: pinned `numpy<2` (installed `1.26.4`) and `opencv-python<5` (installed
`4.11.0`, since `opencv-python 5.x` requires NumPy 2). This keeps the
current x86_64/Rosetta `.venv` internally consistent. Torch↔NumPy interop
was verified working after the pin.

**Known limitation carried forward:** because torch is capped at 2.2.2,
Whisper transcription runs CPU-only on this machine — no MPS/GPU
acceleration is available. If that becomes a bottleneck, the real fix is
rebuilding `.venv` from a native arm64 Python (e.g. `/opt/homebrew`'s
`python3.12`) rather than the current Intel Homebrew (`/usr/local`) one —
that unlocks current `torch` releases, native NumPy 2 support, and possible
MPS acceleration. Not done in this pass; flagged as follow-up work.

### 1.4 System binary dependencies

Two non-Python dependencies were missing entirely and had to be installed
via Homebrew:

- **`ffmpeg`** — required for Whisper transcription (it shells out to the
  `ffmpeg` binary) and for the render pipeline. The default Homebrew
  `ffmpeg` formula does **not** include `libass`, so the app's caption
  burn-in step (which uses FFmpeg's `ass`/`subtitles` filter, backed by
  `libass`) failed with a missing-filter error. Fixed by uninstalling
  `ffmpeg` and installing **`ffmpeg-full`** instead (`brew install
  ffmpeg-full`, then `brew link --force ffmpeg-full`, since it's a
  keg-only formula). Verified the `ass` and `subtitles` filters are present
  afterward (`ffmpeg -filters | grep -i ass`).
- **Ollama** — the app's local LLM backend for clip analysis, AI visual
  planning, and content editing (`app/plan_short.py`,
  `app/ai_visual_planner.py`, `app/content_edit.py`,
  `app/semantic_edit.py` all hit `http://127.0.0.1:11434`, hardcoded to
  expect the model `llama3.1:8b`). Neither the Ollama app nor any model was
  installed. Installed the Ollama app and ran `ollama pull llama3.1:8b`
  (~4.9GB).

### 1.5 Reproducible setup: `requirements.txt`

Everything in 1.2/1.3 above had to be discovered by running the app and
reading tracebacks, because nothing in the repo declared these
dependencies. Added `requirements.txt` at the repo root capturing the
actual working set verified in this pass:

```
PySide6==6.11.2
requests==2.34.2
opencv-python==4.11.0.86
numpy==1.26.4
llvmlite==0.45.1
numba==0.62.1
openai-whisper==20250625
torch==2.2.2
```

These aren't independently "latest" pins — they're a known-working
combination. In particular, `llvmlite`/`numba` (transitive dependencies of
`openai-whisper` via `numba`) are pinned to exact versions that have
prebuilt wheels for this platform; without the pin, pip's resolver picks
newer versions that only ship as source distributions, which then fail to
build without `cmake` and a matching LLVM installed. Verified with a real
from-scratch install into a throwaway venv (`pip install -r
requirements.txt`, no special flags) — installs cleanly and
`torch`↔`numpy` interop works.

`requirements.txt` only covers Python packages. The two system-level
dependencies from 1.4 (`ffmpeg-full`, Ollama + the `llama3.1:8b` model)
still need to be installed separately — they're called out in a comment at
the top of `requirements.txt` pointing back to this document.

Full macOS setup from scratch, in order:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
brew install ffmpeg-full
brew link --force ffmpeg-full
brew install ollama
ollama pull llama3.1:8b
.venv/bin/python app/gui.py
```

If your `.venv` ends up on a native arm64 Python instead of an x86_64/
Rosetta one (check with `python3 -c "import platform;
print(platform.machine())"`), the `numpy<2`/`torch==2.2.2` pins above are
probably unnecessarily conservative — see 1.3 for why they exist on this
specific machine, and consider using current releases of `numpy`, `torch`,
and `opencv-python` instead if you rebuild on arm64.

## 2. Repository hygiene cleanup

The git history shows this codebase was reconstructed after `app/gui.py`
was lost/corrupted on the original Windows machine and recovered via
decompiling `.pyc` bytecode (see `SHORTSFACTORY_RECOVERY_AUDIT.md`). That
recovery effort left several artifacts behind that had outlived their
purpose:

- **`app/gui8.20.py`** (11,911 lines) — a snapshot of `gui.py` taken during
  recovery triage, kept as a reference point. It had drifted thousands of
  lines out of date from the real `app/gui.py` and nothing in the codebase
  referenced it.
- **`app/analyze_backup.py`** (1,382 lines) — an old backup of
  `app/analyze.py`, also unreferenced.
- **`.tmp_decompyle3/`** (~300 files, 5.1MB) — a leftover decompiler tool
  environment (vendored copies of `pip`/`setuptools`/`wheel`/`packaging`
  internals), unrelated to the app itself.
- **`.tmp_pycdecompiler.py`** — another recovery-tooling leftover.

All four were removed from the working tree and un-tracked from git
(`git rm --cached`), then added to `.gitignore` so they can't silently
reappear. Nothing else in the codebase imported or referenced any of them
(verified by grep before removing). All remain recoverable from git history
if ever needed (commit `1b3bc7d`, "Remove stale recovery artifacts and
duplicate files").

## 3. Splitting `app/gui.py` into a package

### 3.1 The problem

`app/gui.py` had grown to 20,051 lines. Nearly the entire desktop
application lived in a single class, `ShortsFactoryWindow(QMainWindow)`
(lines 4345–20034 of the old file — about 15,690 lines, 231 methods),
covering playback, the custom timeline editor, transcript editing, Image AI
backend status, AI visual slot/variant/web-image management, AI Clip
Hunter, music, SFX, the render pipeline, and application styling, all in one
place. This made the file very difficult to navigate, review, or safely
change.

### 3.2 Approach

The refactor is a **pure structural move** — no behavior changes. Method
bodies were relocated verbatim (via precise line-range extraction, not
manual retyping, to avoid transcription errors across ~15,700 lines); signal
wiring, state, and control flow are unchanged.

`ShortsFactoryWindow`'s 231 methods reference each other constantly across
what would otherwise be natural module boundaries — e.g.
`refresh_editor_asset_timeline` is called from timeline code, AI-visual-slot
code, and AI-suggestion code alike. All of these cross-references go through
`self`. Because of that, the class was decomposed using **mixins** (multiple
inheritance sharing one `self`) rather than composition/controller objects.
Mixins let every existing `self.foo(...)` call keep working completely
unchanged after the move — a composition rewrite would have required
threading explicit references between separate controller objects
everywhere this pattern occurs, which is a much larger and riskier change
for what is meant to be a non-behavioral refactor.

Three additional classes that preceded `ShortsFactoryWindow` in the old file
were fully self-contained (no references to `ShortsFactoryWindow`, no
cross-references to each other) and were extracted first, with the lowest
risk:

- `SuggestionSlider(QSlider)` — not "just a slider," this is the entire
  custom multi-lane timeline/editor canvas (paint/mouse/wheel event
  handling, zoom/pan, drag-to-trim, six custom Qt signals). ~3,400 lines on
  its own.
- `TimelineNavigator(QWidget)` — the compact full-source navigator strip.
- `DropZone(QFrame)` — the drag-and-drop source-video import widget.

### 3.3 New structure

`app/gui.py` is now a ~15-line launcher shim:

```python
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui_app.main_window import main


if __name__ == "__main__":
    raise SystemExit(main())
```

This preserves the documented launch command exactly
(`python app/gui.py` / `.venv/bin/python app/gui.py`) — nothing about how the
app is started changed.

The actual code now lives in `app/gui_app/`:

```
app/gui_app/
    __init__.py
    constants.py            # ROOT, SUPPORTED_EXTENSIONS, and other shared
                             # module-level constants, computed once here
                             # (files at different depths in the new package
                             # need a different number of Path(...).parent
                             # hops to reach the repo root — centralizing
                             # this avoids that class of bug)
    helpers.py               # small module-level formatting/text helpers
    timeline_widget.py       # SuggestionSlider
    widgets.py                # TimelineNavigator, DropZone
    style.py                  # the application QSS stylesheet
    main_window.py            # ShortsFactoryWindow: __init__, build_ui,
                               # keyboard handling, layout persistence,
                               # closeEvent, apply_style, and the class
                               # declaration composing all the mixins below
    mixins/
        __init__.py
        playback.py             # preview player, timeline viewport/selection
        transcript.py           # transcript loading, editing, corrections
        image_ai.py              # Image AI (Forge) backend status
        settings.py               # quality/energy/SFX-mode/render settings
        ai_visual_slots.py        # AI visual slot/variant/inspector state
        web_images.py             # Openverse web image search/download
        ai_visual_pipeline.py     # AI visual plan/generate/asset pipeline
        ai_clip_hunter.py         # candidate cards + clip analysis
        music.py                  # music track selection/mix
        render_pipeline.py        # progress tracking + the render pipeline
        editor_assets.py          # SFX timeline / editor asset plan
        ai_visual_preview.py      # live AI visual preview overlay
```

`ShortsFactoryWindow` becomes:

```python
class ShortsFactoryWindow(
    QMainWindow,
    PlaybackMixin,
    TranscriptMixin,
    ImageAIMixin,
    SettingsMixin,
    AIVisualSlotsMixin,
    WebImagesMixin,
    AIVisualPipelineMixin,
    AIClipHunterMixin,
    MusicMixin,
    RenderPipelineMixin,
    EditorAssetsMixin,
    AIVisualPreviewMixin,
):
    ...
```

Mixins are plain classes with no `__init__` of their own — Python's MRO
handles this cleanly since only `ShortsFactoryWindow.__init__` ever calls
`super().__init__()`.

### 3.4 Size result

The largest single file dropped from 20,051 lines to 3,525
(`ai_visual_slots.py`, still the biggest remaining file — it's a genuinely
large functional area and was already split from the surrounding
web-image/generation code at natural existing boundaries). Every other file
in the new package is under ~2,325 lines, and most are in the 150–1,400 line
range.

| File | Lines |
|---|---|
| `app/gui.py` (shim) | 14 |
| `gui_app/constants.py` | 32 |
| `gui_app/helpers.py` | 138 |
| `gui_app/mixins/settings.py` | 158 |
| `gui_app/mixins/music.py` | 239 |
| `gui_app/mixins/image_ai.py` | 409 |
| `gui_app/style.py` | 619 |
| `gui_app/widgets.py` | 714 |
| `gui_app/mixins/ai_clip_hunter.py` | 841 |
| `gui_app/mixins/web_images.py` | 930 |
| `gui_app/mixins/render_pipeline.py` | 959 |
| `gui_app/mixins/ai_visual_preview.py` | 1,039 |
| `gui_app/mixins/playback.py` | 1,079 |
| `gui_app/mixins/ai_visual_pipeline.py` | 1,161 |
| `gui_app/mixins/editor_assets.py` | 1,305 |
| `gui_app/mixins/transcript.py` | 1,393 |
| `gui_app/main_window.py` | 2,322 |
| `gui_app/timeline_widget.py` | 3,427 |
| `gui_app/mixins/ai_visual_slots.py` | 3,525 |

### 3.5 Verification performed

- `python -m py_compile` clean across every new/changed file.
- `pyflakes` (installed into `.venv` for this check) run across the whole
  new package — caught several real missing imports during the split
  (mostly `from pathlib import Path` in files that needed it, plus a couple
  of missing app-module imports) that a manual per-file import review had
  missed; all fixed, then re-verified clean. The one remaining pyflakes
  warning (`ai_clip_hunter.py`, an unused local variable) was confirmed to
  already exist in the pre-refactor file — not introduced by this change,
  left as-is since this pass makes no behavioral edits.
- The app was launched (`QT_QPA_PLATFORM=offscreen .venv/bin/python
  app/gui.py`) and confirmed to construct its entire widget tree and start
  its event loop with zero exceptions. This is a structural smoke test —
  not a substitute for a full manual feature-by-feature regression pass,
  which should still be done before relying on this in production.

## 4. Cross-platform path fixes (Forge launch, drawtext fonts)

Two spots in the codebase had Windows-only hardcoded paths with no
platform branching at all, both noted as follow-up work in section 4 (old
numbering) above. Both are now dynamic based on the OS actually running the
app, while remaining fully backward compatible on Windows.

### 4.1 `app/image_backend_status.py` — Forge auto-launch

`DEFAULT_FORGE_LAUNCH` previously hardcoded `C:\AI\Forge\run.bat` with no
fallback. It's now computed by `_default_forge_launch_path()`:

- Windows: `C:\AI\Forge\run.bat` (unchanged).
- macOS/Linux: `~/AI/Forge/webui.sh`.

Still fully overridable via the existing `SHORTSFACTORY_FORGE_LAUNCH`
environment variable on any platform — that mechanism was already there and
didn't need to change.

`launch_forge()` itself also branched on `sys.platform`: Windows keeps
launching via `cmd.exe /c <path>` with `CREATE_NEW_CONSOLE` (unchanged);
macOS/Linux now launches via `/bin/bash <path>` with `start_new_session=True`
(the POSIX equivalent of detaching the child process). Previously, the
macOS code path attempted to run `cmd.exe` directly, which doesn't exist on
macOS — it failed safely (caught by the existing `except OSError`, so no
crash) but never actually worked. Verified end-to-end with a real fake
`webui.sh` test script: the launch call correctly starts it and its output
confirms the process ran.

### 4.2 `app/visual_fx.py` — `drawtext` font resolution

`FONT_CANDIDATES` (used by `drawtext_font_option()` to pick a bold/impact
font for slam-text visual FX overlays) previously listed only Windows font
paths (`C:\Windows\Fonts\arialbd.ttf`, etc.). On any other OS, none of those
paths exist, so it silently fell through to FFmpeg's default fontconfig
lookup — not a crash, but a quiet loss of the intended bold/impact styling.

Replaced with `_default_font_candidates()`, branching on `sys.platform`:

- Windows: `arialbd.ttf` → `impact.ttf` → `arial.ttf` (unchanged).
- macOS: `/System/Library/Fonts/Supplemental/Arial Bold.ttf` →
  `Impact.ttf` → `Arial.ttf`, with `/System/Library/Fonts/Helvetica.ttc` as
  a last-resort fallback. All four confirmed present on a real macOS
  install.
- Linux: a DejaVu/Liberation Sans Bold fallback chain (best-effort — not
  verified on an actual Linux machine, but harmless if absent since the
  same existence-check fallback to FFmpeg's default lookup still applies).

Verified with an actual `ffmpeg -vf drawtext=fontfile=...` render (not just
a path-exists check) that the resolved macOS font file loads and renders
correctly.

Both changes verified with `python -m py_compile` and `pyflakes` (clean),
plus an offscreen app-launch smoke test.

## 5. macOS playback bug: pausing reset the video to frame 0

**Symptom:** on macOS, pausing a video anywhere in the middle of playback
snapped it back to the very start (frame 0) instead of staying paused at
the current position.

**Root cause:** `PlaybackMixin.prime_preview_frame()`
(`app/gui_app/mixins/playback.py`) is a workaround originally written for a
Windows Qt Multimedia quirk — some Windows backends don't paint the first
frame of a freshly loaded video until playback advances briefly, so this
method nudges playback forward a few milliseconds (muted) right after load,
then pauses. That workaround is correctly guarded to only fire when the
player is freshly `StoppedState` with no video position yet.

The bug: the method's very first action, `self.player.setPosition(0)`, sat
*outside* that guard, unconditionally, at the top of the function. This
method isn't only called right after loading a new video — it's also
invoked from `media_status_changed()` any time the player reports
`BufferedMedia`. On macOS's FFmpeg-based Qt Multimedia backend (confirmed
in this environment: `qt.multimedia.ffmpeg: Using Qt multimedia with
FFmpeg version 7.1.5`), pausing mid-playback legitimately re-emits a
`BufferedMedia` status as the pipeline settles — which the Windows backend
this workaround was written for apparently doesn't do the same way. Every
such pause therefore triggered `prime_preview_frame()`, which reset the
position to 0 before ever reaching the guard that would have skipped it.

**Fix:** moved `self.player.setPosition(0)` inside the existing guard, so
position is only reset to 0 as part of the actual "prime the first frame
after a fresh load" workaround, never as a side effect of an ordinary
mid-playback pause. Verified with `py_compile`, `pyflakes`, and an offscreen
app-launch smoke test.

## 6. `OLLAMA_MODEL`/`OLLAMA_HOST` deduplication and exception-handling review

### 6.1 Shared Ollama config

`OLLAMA_HOST`/`OLLAMA_MODEL` (defaulting to `http://127.0.0.1:11434` /
`llama3.1:8b`) were each independently defined as env-var-backed module
constants in four separate files: `app/plan_short.py`,
`app/content_edit.py`, `app/semantic_edit.py`, `app/ai_visual_planner.py`.
Extracted into a new `app/ollama_config.py`, imported by all four via the
repo's existing relative-then-absolute import fallback pattern (the same
pattern already used elsewhere for cross-module imports, since `app/` isn't
a real Python package). `app/analyze.py` was left as-is — it has its own,
different pattern (discovering whichever model is actually installed in
Ollama rather than defaulting to a fixed name), which is intentionally
different and out of scope here.

### 6.2 Exception-handling review

A grep for `except Exception:` across `app/*.py` found three occurrences.
Reviewed each individually rather than blanket-narrowing:

- `app/generate_ai_visual_assets.py` (parsing an API error body for a
  friendlier message) — already safe by construction, since the code
  always falls through to `response.raise_for_status()` regardless of
  whether the friendlier message parses. Narrowed to `except ValueError:`
  (covers `json.JSONDecodeError`) purely for precision; no behavior change.
- `app/emoji_overlay.py`'s `normalize_emoji` (decoding an escaped emoji
  string) — narrowed to `except UnicodeDecodeError:`, the only realistic
  failure mode here; unchanged fallback behavior.
- `app/emoji_overlay.py`'s `resolve_event_asset` — this one was a real
  correctness issue, not just an overly-broad catch. This function checks
  that a resolved emoji asset path stays inside `EMOJI_DIR` (a path-
  traversal guard), but on *any* exception during resolution it silently
  fell back to trusting the **unresolved** path and skipped the
  containment check — fail-open rather than fail-closed. Fixed by narrowing
  the catch to `OSError` (what `Path.resolve()` can actually raise, e.g. a
  symlink loop) and rejecting the asset (returning `None`) in that case
  instead of silently trusting it. Verified: legitimate in-folder assets
  still resolve correctly, and a path-traversal attempt
  (`../../../etc/passwd`) is still correctly rejected.

### 6.3 `shot_type.py` CascadeClassifier warning — not reproducible here

The project status docs mention `shot_type.py` logging an unresolved
`cv2.CascadeClassifier` warning "on this machine" (the original Windows dev
box). Directly tested cascade loading on this macOS setup
(`cv2.data.haarcascades` → `haarcascade_frontalface_default.xml` →
`CascadeClassifier(...).empty()`) and ran a full real `analyze_shots()`
pass against the sample video — both clean, `detector.empty()` is `False`,
no warnings. Very likely specific to the original machine's OpenCV
install (different/older `opencv-python` version, or a broken install)
rather than a code bug; this session's clean `opencv-python==4.11.0.86`
install doesn't exhibit it. No code change made here.

## 7. First real automated test suite

Until now the only "test" files in the repo (`test_ollama.py`,
`test_ranker.py`, at the repo root) were manual smoke scripts with no
assertions, requiring a live local Ollama server, and `pytest` wasn't even
installed. Added real `pytest`-based tests as a *start*, not exhaustive
coverage — targeting the highest-value **pure decision logic** in the three
areas most likely to regress silently: transcript caching, the visual FX
planner, and the SFX planner. These modules are mostly FFmpeg/subprocess
orchestration, but each also contains cleanly-separable pure functions that
decide *what* happens (which caption emphasis level, which SFX category,
whether a cached transcript is still valid) without doing any I/O — exactly
the kind of logic where a subtle bug doesn't crash anything, it just quietly
produces worse output.

### 7.1 Infrastructure

- `requirements-dev.txt` (new) — dev-only dependencies, kept separate from
  the runtime `requirements.txt`: `pytest==9.1.1`, `pyflakes==3.4.0`.
- `pytest.ini` (new, repo root) — `testpaths = tests`, `pythonpath = app`.
  The `pythonpath` setting (pytest 7+) puts `app/` on `sys.path` for every
  test run, so tests do plain `import subtitles`, `import visual_emphasis`,
  etc. — matching the flat-import convention already used throughout
  `app/*.py` itself, no `conftest.py` needed.
- `tests/` (new directory), four files, 37 tests total, all passing,
  runtime well under a second.

### 7.2 What's covered

- `tests/test_subtitles_cache.py` (8 tests) — `normalize_quality`,
  `cache_is_valid` (the actual cache-hit/miss decision — checks
  `cache_identity` dict equality plus `segments`/`words` being lists),
  `migrate_cached_transcript` (verified it shallow-copies rather than
  mutating the input), `normalized_segments` (malformed Whisper segments
  with `end<=start` get dropped, blank words filtered, missing
  `probability` defaults to `0.0`), `source_fingerprint`/
  `cache_path_for_video` (deterministic given the same file+quality+model,
  different for a different quality/model).
- `tests/test_visual_emphasis.py` (14 tests) — `normalize_energy`/
  `normalize_sfx_mode`, `classify_word` (the caption-emphasis classifier —
  verified against known-good examples already documented in
  `SHORTSFACTORY_CURRENT_STATUS.md`: `"never"` → `IMPACT`, `"huge"` →
  `EMPHASIS`), `word_time` (minimum-duration enforcement, garbage input →
  `None`), `mark_collisions` (two events within 0.35s → the lower-priority
  one gets flagged; same-`stack_id` events get tagged as deliberate
  coordination instead of colliding), `build_intensity_curve` (five
  narrative regions spanning the full duration contiguously; a region with
  more moments scores at least as high as an identical one with fewer).
- `tests/test_sfx_engine_planning.py` (9 tests) — `stable_hash`,
  `filename_words`, `infer_category_from_words` (including the fallback
  behavior: an invalid fallback category name is silently ignored in favor
  of `"pop"`), `category_cap` (scales with energy level), `choose_event_category`
  (verified it actually avoids repeating the immediately-preceding
  category when an alternative exists), `event_start`/`event_end`
  (field-fallback order, `end` clamped to never be before `start`).
- `tests/test_render_helpers.py` (6 tests) — `resolve_source_video`,
  `component_target_for` (the output-folder-organization collision
  avoidance — verified 0, 1, and 2 collision levels all resolve to the
  expected `_2`/`_3` suffixed paths), `python_executable`'s deterministic
  branch.

Every expected value in these tests was confirmed by calling the real
function directly (not hand-computed) before being written as an assertion.

### 7.3 What's explicitly not covered by this pass

The FFmpeg/subprocess-invoking parts of these same modules (actual video
rendering, actual transcription, actual SFX mixing), the GUI package
(`app/gui_app/`), and every module not named above. This is a foundation to
build on, not full coverage.

### 7.4 Running the tests

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest
```

## 8. Playback bug: scrubbing the timeline while paused looked frozen

**Symptom:** dragging the preview timeline's playhead while the video was
paused didn't visibly update the video frame at all until the mouse was
released — it looked completely frozen mid-drag, then jumped to the
correct frame on release.

**Root cause:** `PlaybackMixin.seek_video()` (`app/gui_app/mixins/playback.py`)
always calls `self.player.setPosition(position)` on every drag update (via
`sliderMoved`), so the player's actual position was updating correctly the
whole time — the bug was purely visual. Directly confirmed: on this
machine's Qt Multimedia backend (`qt.multimedia.ffmpeg`, the FFmpeg-based
backend), a bare `setPosition()` while paused produces **zero** repaint —
sampled the rendered widget before and after a 15-second seek with no
nudge, and not a single pixel changed. `seek_video()` already has a
workaround for exactly this ("Some Qt multimedia backends... never repaint
a paused video after a bare setPosition()" — briefly nudging playback
forward while muted, then pausing again, forces a real repaint), but it was
explicitly skipped whenever `self.timeline.scrubbing_playhead` was `True`
— i.e. it was disabled for the entire duration of a drag, and only ran
once on release.

**First attempt (insufficient):** removed the scrubbing-specific skip so
the existing async nudge (`self.player.play()` then
`QTimer.singleShot(45, self.finish_paused_seek)`) would also run during a
drag, throttled by the existing `paused_seek_refresh_pending` in-flight
guard. This didn't fix it — confirmed live, the preview still only updated
on release.

**Actual root cause:** the async nudge depends on a `QTimer.singleShot`
callback actually firing 45ms later to pause the player again and clear
`paused_seek_refresh_pending`. Reproduced the failure directly: simulated a
tight loop of `seek_video()` calls with no event-loop yield between them
(the worst case for a fast drag, since macOS's native drag-tracking can
starve regular `QTimer` callbacks even while direct method calls and
repaints elsewhere keep working) — `paused_seek_refresh_pending` got stuck
`True` after the very first call and never reset, so the player was left
sitting in `PlayingState` for the rest of the simulated drag instead of
returning to `PausedState`; only the first seek's nudge ever actually
completed.

**Fix:** replaced the async nudge in `seek_video()` specifically (not the
other call sites of this pattern — `prime_preview_frame`'s initial-load
nudge and `toggle_playback` are untouched, out of scope here) with a
synchronous version: `player.play()` → `QCoreApplication.processEvents()`
→ `player.pause()`, all inline, no `QTimer` involved. Re-ran the same
tight-loop stress test against the new version: every single call now
completes fully immediately (`paused_seek_refresh_pending` resets to
`False`, `playbackState` returns to `PausedState`, and the player's actual
position matches exactly what was requested) — a direct, controlled
before/after comparison under identical conditions, not just a code-reading
argument.

This second attempt *also* didn't fix it — confirmed live, still frozen
until release.

**Third attempt — different root cause entirely:** both prior attempts
assumed the bottleneck was "how do we force a repaint," and neither
questioned whether the seeks themselves were completing at all. The more
likely explanation, common to scrubbing implementations generally (not
specific to Qt or macOS): calling `setPosition()` faster than the backend
can seek+decode a frame cancels each in-flight seek before it finishes, so
during a fast drag (mouse-move events firing far faster than any single
seek can complete) *no seek ever finishes* until movement stops and the
final one finally gets uninterrupted time to complete — which matches the
symptom exactly (freezes while moving, snaps to the correct frame the
instant you stop).

**Fix:** throttle how often `seek_video()` actually calls
`player.setPosition()` (and the repaint nudge) while
`self.timeline.scrubbing_playhead` is `True`, using a plain wall-clock
check (`time.monotonic()`, no `QTimer` involved, so it can't be affected by
the same callback-starvation risk as attempt one) — at most once per 80ms.
Cheap UI updates (time label, AI-visual-preview overlay, selection-loop
state) still run on every single call so the UI stays responsive; only the
actual expensive player seek is throttled. Outside of active dragging (a
single click, or the final position on release — `scrubbing_playhead` is
already `False` by the time `sliderReleased` fires) seeks are never
throttled. Verified directly with a 3-part stress test: a burst of calls
with no delay between them applies only the *first* seek and coalesces the
rest (throttle engaged); calls spaced 100ms apart (past the 80ms window)
each apply individually (throttle doesn't block a real drag); and a
post-release call always applies immediately regardless of timing. This is
a materially different, independently-verified mechanism from both prior
attempts, not another tweak to the same one.

**Verification caveat:** offscreen pixel-grab testing was ruled out as a
verification method for this bug across all three attempts — even normal
continuous playback showed zero captured pixel change via
`QVideoWidget.grab()` under `QT_QPA_PLATFORM=offscreen` (likely tied to the
"`No RHI backend. Using CPU conversion`" fallback this backend logs in
headless mode), so it can't distinguish a real fix from a broken one either
way. Verification instead relied on directly exercising the actual
call-frequency/throttle behavior under controlled stress tests, plus
`py_compile`/`pyflakes` clean and the full test suite passing (37/37). Both
earlier attempts were also verified as thoroughly as this environment
allows and both still failed live — confirming in the actual running app
remains the real test, this is not a guarantee.

**Confirmed live** (attempt 3, main playhead scrubbing): the throttle fixed
it.

### 8.1 Same fix extended to trimming the source clip

The user reported the identical frozen-preview symptom also applies when
dragging the source clip's IN/OUT trim handles, or dragging the whole clip
body — a separate code path, `PlaybackMixin.timeline_selection_changed()`,
which had the exact same unthrottled `player.setPosition()` call on every
`selectionChanged` emission during a drag, and — unlike `seek_video()` —
never had the repaint nudge at all. Applied the identical fix: throttle the
actual seek to at most once per 80ms while `timeline.dragging_handle` or
`timeline.dragging_source_clip` is truthy (sharing the same
`_last_actual_seek_time` throttle window as `seek_video()`, since both
compete for the same underlying player), plus the same synchronous
play→processEvents→pause repaint nudge, which this path was missing
entirely. Cheap updates (the time label) still run on every call.

One behavioral note carried over unchanged from the pre-existing code, not
introduced by this fix: `timeline_selection_changed()` only ever calls
`setPosition()` while `dragging_handle`/`dragging_source_clip` is still
`True` — by the time `mouseReleaseEvent` fires the final `selectionChanged`
emission, that flag is already cleared, so (both before and after this
fix) the final release event doesn't independently re-sync position; it
relies on the last in-drag update having already landed close enough.
Throttling makes that last-applied position up to ~80ms staler than
before, which matches the same acceptable trade-off already confirmed live
for the main scrubber, but is worth knowing about.

Verification: `py_compile`/`pyflakes` clean, full test suite still passes
(37/37), app launches cleanly offscreen. Could not get a clean isolated
stress-test replica of this exact code path the way `seek_video()` got one
(this method's tail calls into several other GUI-update methods not worth
fully mocking out) — the throttle/nudge block itself is textually
identical to the already-stress-tested one in `seek_video()`, just not
independently re-exercised in isolation here. Not yet confirmed live.

## 9. Trackpad horizontal scroll to pan the editor timeline

`SuggestionSlider.wheelEvent()` (`app/gui_app/timeline_widget.py`) already
supported `Ctrl+wheel` to zoom and `Shift+wheel` to pan, but a plain
two-finger trackpad horizontal swipe (no modifier key) did nothing —
`event.ignore()`. Added a new branch: when no modifier is held and the
horizontal scroll component (`angleDelta().x()`) is the dominant one
(`>= abs(angleDelta().y())`), pan the timeline directly using the same
`horizontal_pan()` used by `Shift+wheel`. An ordinary vertical scroll
(dominant `y` component) still falls through to `event.ignore()`
unchanged, so it doesn't interfere with anything a parent widget might do
with vertical scroll input.

Verified directly with a synthetic `QWheelEvent`: a horizontal scroll pans
the viewport, the opposite direction pans back symmetrically to the exact
same range, and a vertical-only scroll leaves the viewport untouched and
correctly reports `event.isAccepted() == False`. `py_compile`/`pyflakes`
clean, full test suite passes (37/37), app launches cleanly offscreen.

**Uncertainty worth flagging:** the pan direction reuses the same sign
convention already used for `Shift+wheel`, for internal consistency, but
which direction feels "natural" for a two-finger swipe is genuinely
platform/settings-dependent (macOS's "natural scrolling" toggle inverts
the convention) and wasn't confirmed live. If it pans backwards from what
feels right, it's a one-line sign flip (`direction = -1 if
horizontal_delta > 0 else 1` → `1 if ... else -1`).

## 10. Visual effects were positioning relative to the full letterboxed canvas, not the real video content

`render_base_video()` (STEP 1 of the render pipeline, `app/render.py`) fits
the source video into the 1080x1920 output canvas at its native aspect
ratio and letterboxes it (`scale=...:force_original_aspect_ratio=decrease`
+ `pad=1080:1920:...:black`) rather than cropping to fill — a deliberate
earlier change to stop cutting off parts of the source frame. For a
typical landscape source this means the real video content ends up as a
smaller centered rectangle with black bars around it (e.g. the sample
clip in this repo, 638x480, fits to full 1080 width but only ~813 of the
1920 canvas height — more than half the vertical canvas is black bar).

Every later pipeline stage operates on this already-letterboxed video, but
two of them were written assuming the video content fills the full canvas
edge-to-edge, which stopped being true once letterboxing was introduced:

- **`app/smart_motion.py`**'s zoompan "punch-in" effect centered/panned
  using `iw`/`ih`, which resolve to the full canvas at that point in the
  filter chain — and worse, the zoom crop window itself was sized as a
  fraction of the full 1920 canvas height, so even a 1.3x "zoom in" barely
  shrank the black bars or enlarged the actual subject; it was mostly
  zooming into a mix of content and black bar.
- **`app/apply_ai_visuals.py`**'s AI visual overlay positioning
  (`OVERLAY_CARD` and `FULL_FRAME_CONTAIN` modes) centered/positioned
  cards using the ffmpeg `overlay` filter's `W`/`H` (full canvas), so
  overlays could land across or beyond the actual visible content into the
  black bars.

### Fix: a shared "content rect"

Added `content_rect_for_source()` (`app/render.py`) — a pure function
mirroring the exact scale+pad math to compute where the real content sits
within the canvas (x, y, width, height) — called from
`render_base_video()` (after a new `ffprobe_source_dimensions()` helper,
adapted from the same pattern already in the now-dead `app/smart_reframe.py`)
and persisted into the already-shared `output/render_settings.json` (both
target files already read this file for `edit_energy`/`sfx_mode`, so this
reuses existing plumbing rather than inventing new). Added
`content_rect_from_settings()` in `app/visual_emphasis.py` for downstream
stages to read it back, defaulting to the full canvas (no letterboxing
assumed) if absent — fully backward compatible with any render predating
this change.

- **`smart_motion.py`**: `apply_motion()` now wraps the existing zoompan
  filter with `crop={content}` before it and `pad=1080:1920:{content_x}:
  {content_y}:black` after. Key insight: `zoom_expression`/`x_expression`/
  `y_expression`'s `iw`/`ih` references are ffmpeg-runtime symbols that
  resolve to whatever frame is actually fed into `zoompan` — cropping away
  the black bars first means all of that existing zoom-selection/bias/
  shot-aware-strength logic needed **zero changes** and automatically
  becomes correct.
- **`apply_ai_visuals.py`**: `build_filter()` gained a `content_rect`
  parameter; `OVERLAY_CARD` and `FULL_FRAME_CONTAIN` positioning math now
  reference the content rect instead of raw `W`/`H`. `FULL_FRAME_COVER`
  mode is deliberately unchanged — it's meant to cover the entire canvas
  edge-to-edge by design, not a bug.

Confirmed **not** part of this problem, left alone: `app/smart_reframe.py`
(dead code — a comment in `app/gui_app/mixins/render_pipeline.py:606-610`
confirms this old stage was explicitly disabled when letterboxing was
adopted); `app/make_captions.py`'s `PlayResX`/`PlayResY` (correctly
describes the actual canvas, not a bug) — its margin constants are
canvas-relative by design, to stay clear of TikTok/Reels/Shorts' own
on-screen UI controls, which is a separate, more debatable design question
left untouched here.

**Correction (see section 11 below):** this section originally also
classified `app/visual_fx.py`'s `drawbox` color washes as "deliberately
whole-screen, not a bug." That was wrong for the *spatially-varying*
effects in that same file (`vignette`, RGB-split accent stripes, drawtext
slam-text positioning) — only the genuinely uniform full-frame color washes
were actually fine as-is. Fixed in the next pass.

### Verification

- New `tests/test_render_content_rect.py` (4 tests) for
  `content_rect_for_source()`, including the exact real sample video
  dimensions (638x480).
- Cross-checked the computed rect against ffmpeg's own `cropdetect` filter
  run on the actually-rendered letterboxed output: computed `(0, 553,
  1080, 813)` vs. ffmpeg's independently detected `crop=1080:812:0:552` —
  within 1px, well inside cropdetect's own detection tolerance.
- Ran the real `render_base_video()` write path against the sample video
  and confirmed `render_settings.json` got the correct fields merged in
  without disturbing existing keys (`edit_energy`, `source_video`,
  `selection_start`/`end`, etc.).
- **Visually confirmed both fixes with real ffmpeg renders, not just
  geometry checks**: extracted actual frames before/after applying a 1.3x
  `smart_motion` zoom event on a real clip — the black bars visibly and
  proportionally shrink and the subject visibly gets larger/closer, which
  would barely have happened before this fix. Extracted a frame from a
  real `apply_ai_visuals` composite — a test overlay card now sits flush
  against the top of the actual visible content instead of positioned
  relative to the far larger letterboxed canvas.
- `py_compile`/`pyflakes` clean, full test suite passes (41/41), app
  launches cleanly offscreen.

## 11. Color grade/vignette FX were also computed relative to the full canvas

After generating an actual video with section 10's fix applied, color
filters were still visibly wrong — the vignette effect in particular. This
extends the same content-rect fix to `app/visual_fx.py`, which section 10
incorrectly cleared as "deliberately whole-screen, not a bug."

That assessment held for the genuinely uniform effects (the full-frame
`drawbox` color washes, `eq=` contrast/saturation adjustments — flat
per-pixel transforms with no spatial dependency), but missed the
*spatially-varying* ones in the same filter chain:

- `vignette=PI/4.2` (and `PI/7`, `PI/3.2`) darkens the frame based on
  distance from center, calibrated to the full 1080x1920 canvas — but
  since the real content is usually much shorter than 1920px tall, the
  vignette's falloff distance was far larger than the actual content, so
  within the visible video it looked barely-there, with most of the
  effect's "budget" wasted darkening bars that were already black.
- `drawtext` slam-text positioning (`y=h*0.28`) and the RGB-split accent
  stripes (`y=h*0.16/0.22/0.67`) were computed as fractions of the full
  canvas height rather than the actual content height.

Fix: identical pattern to section 10 — `apply_visual_fx()`
(`app/visual_fx.py`) now wraps its entire existing filter chain with
`crop={content}` before and `pad=1080:1920:{content_x}:{content_y}:black`
after, using the same `content_rect_from_settings()` helper. Every filter
in the chain (`eq`, `unsharp`, `vignette`, `drawbox`, `drawtext`) already
used relative `iw`/`ih`/`w`/`h` ffmpeg symbols with zero hardcoded absolute
pixels, so — same as `smart_motion.py` in section 10 — **no changes were
needed to any individual filter string**, only the top-level wrap.

### Verification

Visually confirmed with a real ffmpeg render, not just code reading:
extracted the same frame from the MAXIMUM-energy baseline grade
(`eq=contrast=1.42:...`, `vignette=PI/4.2`) both unwrapped (old behavior)
and wrapped with the real content rect (new behavior). The new version
shows a distinctly stronger, properly-contained vignette — visibly darker
corners within the actual content bounds and a more concentrated bright
center — while the old version's vignette was diluted, barely affecting
the real content because most of its falloff radius was calibrated to the
much taller full canvas. `py_compile`/`pyflakes` clean, full test suite
passes (41/41), app launches cleanly offscreen.

## 12. Corrupted output from a duration mismatch mid-pipeline

**Symptom:** generating a Short from a ~53-second selection produced a
812-second (13.5 minute) output, and the render log was full of
`Invalid NAL unit size` / `Error splitting the input into NAL units` /
`Decoding error: Invalid data found when processing input` — H.264 decode
garbage.

**Root cause, traced through the actual render log line by line:**

- STEP 1 (`render_base_video()`) correctly rendered `short1_base.mp4` at
  53.47s — confirmed by its own ffmpeg encoding summary.
- STEP 3 (`auto_cut.py`, "ShortsFactory Smart Edit") correctly re-probed
  the same file, still 53.47s, and rendered a tightened preview fine.
- STEP 4 (`semantic_edit.py`) analyzed the transcript, approved 0 cuts,
  touched no video file.
- The next stage — `apply_smart_edit.py`'s `main()` ("ShortsFactory
  Combined Smart Edit") — re-probed `short1_base.mp4` *again* and got
  **1422.21s: the full source episode's duration**, not 53.47s. It then
  built a trim/concat filter including `trim=start=44.11:end=1422.212458`
  against a file that only actually contains 53.47 seconds of real video
  data — reading ~1369 seconds past the end of valid H.264 data, which is
  exactly what produces NAL-unit garbage when something downstream tries
  to decode it.

Nothing in the normal single-pass pipeline touches `short1_base.mp4`
again between steps 1 and 5 — `render.py` writes it once and only reads it
afterward. The only way its probed duration changes mid-pipeline is if
something else wrote to that same fixed, shared path
(`output/rendered/short1_base.mp4`) while this render was still running —
most likely an overlapping second render (a stuck earlier attempt, a
leftover/orphaned process, etc.). The exact trigger couldn't be confirmed
after the fact, and `generate_short()` (`app/gui_app/mixins/
render_pipeline.py`) does disable the Generate button for the duration of
a render, which should block the most obvious same-session double-click
path — but `render.py` has no protection at all against *any* other
process touching the same fixed output path concurrently (a stuck earlier
run, manually invoking `render.py` from a terminal while the GUI is also
rendering, etc.).

**Fix:** rather than chase an unconfirmable race, added a defensive sanity
check in `apply_smart_edit.py`'s `main()`, right after probing
`BASE_VIDEO`'s duration: cross-check it against the expected duration from
`render_settings.json`'s `selection_start`/`selection_end` (which
`render_base_video()` just trimmed the file to exactly match). If the
probed duration exceeds the expected one by more than 5 seconds, log a
clear error and exit with failure instead of silently building a filter
chain that reads past the end of the real file. Converts a silent,
corrupted-output failure into a loud, actionable one — "this usually means
another render overwrote this file while this one was still running" —
without needing to know or fix the exact upstream trigger.

**Verification:** reproduced the exact failure synthetically (a 60-second
test file standing in for `BASE_VIDEO`, an expected selection of only 10
seconds) and confirmed the new check fires with the correct message and
`main()` returns failure; then confirmed the normal case (duration closely
matching the expected selection) proceeds with no false positive.
`py_compile`/`pyflakes` clean, full test suite passes (41/41), app
launches cleanly offscreen.

**Not fixed here, worth doing eventually:** making concurrent renders to
the same output paths structurally impossible (e.g. a lock file, or
per-render-attempt unique temp paths merged in only on success) rather
than just detecting the corruption after the fact.

## 13. What's still open

This pass did not touch:

- Further splitting `gui_app/mixins/ai_visual_slots.py`, which remains the
  largest file in the new package.
- Test coverage for the GUI itself, and for the FFmpeg/subprocess-invoking
  parts of the modules covered in section 7.
