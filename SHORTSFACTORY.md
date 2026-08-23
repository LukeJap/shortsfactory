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

## 6. What's still open

This pass did not touch:

- Test coverage — still effectively none for a large codebase.
- Further splitting `gui_app/mixins/ai_visual_slots.py`, which remains the
  largest file in the new package.
