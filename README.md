# ShortsFactory

A local desktop app that turns a long source video — a full TV episode,
podcast, movie, whatever — into an edited, vertical 9:16 "Short" in the
style of YouTube Shorts, TikTok, or Instagram Reels.

## What it does

ShortsFactory automates the parts of Shorts-editing a human would
otherwise do by hand: picking a strong clip from a long source, tightening
it (cutting dead air and redundant speech), captioning it karaoke-style,
adding punch-in camera motion, color grading, AI-generated visual cutaway
graphics, emoji reactions, sound effects, and background music.

It runs entirely on your own machine against your own footage — one user,
one video at a time, not a hosted or multi-tenant product. Every
automated/AI decision (which clip, which cuts, which captions, which
effects) is shown in the editor and can be adjusted or rejected before you
render, rather than applied silently in the background.

## Requirements

- **Python 3.12**
- **[FFmpeg](https://ffmpeg.org/)**, built with `libass` — required for
  captions and effectively all video/audio processing. On macOS, the
  default Homebrew `ffmpeg` formula does **not** include `libass`; you
  need `ffmpeg-full` instead (see install steps below).
- **[Ollama](https://ollama.com)** running locally, with the `llama3.1:8b`
  model pulled — used for clip selection, content-editing suggestions, and
  AI visual planning. No cloud AI calls are made.
- *Optional:* a local Forge/Automatic1111-compatible Stable Diffusion
  backend, if you want AI-generated visual cutaway images. Without it, the
  app still works and falls back to preview-only placeholders for that
  feature.

## Install (macOS)

```bash
# 1. Create and activate a virtual environment
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Install FFmpeg (the "full" build, for libass/caption support)
brew install ffmpeg-full
brew link --force ffmpeg-full

# 3. Install and set up Ollama
brew install ollama
ollama pull llama3.1:8b

# 4. Run the app
.venv/bin/python app/gui.py
```

If you're on an Apple Silicon Mac running an x86_64/Rosetta Python
(check with `python3 -c "import platform; print(platform.machine())"`),
`requirements.txt`'s `numpy`/`torch`/`opencv-python` pins are deliberately
conservative to keep PyTorch/NumPy interop working under Rosetta — see the
comments at the top of `requirements.txt` and `SHORTSFACTORY.md` for why,
and for the newer versions you can use instead if you rebuild the venv on
a native arm64 Python.

### Windows / Linux

The app was originally built on Windows and has since been ported to and
verified on macOS; Windows should still work with the platform-appropriate
equivalents of the steps above (install Python 3.12, install FFmpeg with
libass support and put it on `PATH`, install Ollama for Windows, pull
`llama3.1:8b`, then `python app\gui.py`). Linux support is best-effort —
the app runs, but font/path fallbacks for a couple of visual effects are
unverified there.

## Running

```bash
.venv/bin/python app/gui.py
```

This launches the desktop editor. From there: import a source video,
click **Find Best Clips** to get AI-suggested moments, select/trim a clip
on the timeline, adjust captions/emoji/AI visuals/effects, then
**Generate Final Video**.

## Running tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Configuration

A few optional environment variables, all with sensible defaults if unset:

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | Ollama server address (default `http://127.0.0.1:11434`) |
| `OLLAMA_MODEL` | Ollama model name (default `llama3.1:8b`) |
| `SHORTSFACTORY_WHISPER_MODEL` | Override the local Whisper transcription model |
| `SHORTSFACTORY_TRANSCRIPTION_QUALITY` | Default transcription quality preset |
| `SHORTSFACTORY_FORGE_LAUNCH` | Path to a local Forge/A1111 launch script, for AI visual generation |
| `SHORTSFACTORY_IMAGE_API` | Base URL of a running Forge/A1111 image API, if not launching one |
| `SHORTSFACTORY_OPENAI_IMAGE_API` / `SHORTSFACTORY_OPENAI_IMAGE_MODEL` | Use an OpenAI-compatible image API instead of a local backend |

## Project layout

- `app/gui.py` — desktop app entry point (thin launcher; the real app is
  in `app/gui_app/`)
- `app/gui_app/` — the PySide6 UI: main window, the custom timeline
  widget, the visual style sheet, and one `mixins/` file per feature area
- `app/*.py` — the render pipeline itself (clip selection, cuts,
  transcription, captions, motion, color grade/FX, AI visuals, emoji,
  sound effects, final render) — each stage is a standalone,
  independently runnable script
- `tests/` — the automated test suite (`pytest`)
- `output/` — everything a render produces: plan files, transcripts,
  intermediate and final rendered video, the render log

For a deeper look at the current UI layout, interaction patterns, and
visual design system, see `SHORTSFACTORY_DESIGN_CONTEXT.md`. For the
detailed engineering history (environment setup, bug fixes, past
refactors), see `SHORTSFACTORY.md`.
