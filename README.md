# ShortsFactory

A local Windows desktop app (PySide6/Qt6, Python 3.12) that turns a long
source video — a full TV episode, podcast, movie, whatever — into an
edited, vertical 9:16 "Short" in the style of YouTube Shorts, TikTok, or
Instagram Reels.

## What it does

ShortsFactory automates the parts of Shorts-editing a human would
otherwise do by hand: finding a strong clip in a long source ("Find Best
Clips"), tightening it, captioning it karaoke-style, adding punch-in
camera motion and color grading, emoji reactions, sound effects, and
background music. A separate AI Recap pipeline turns a full episode into
a narrated recap short, with the narrator as the primary storyteller.

It runs entirely on your own machine against your own footage — one
user, one video at a time, local-first, no hosted backend. Every
automated/AI decision becomes a visible, editable entity on the editor
timeline — manual edits (moved emoji, resized SFX, caption edits, music
settings) are authoritative and survive regeneration.

## Requirements

- **Python 3.12**
- **[FFmpeg](https://ffmpeg.org/)**, built with `libass` — required for
  captions and effectively all video/audio processing.
- **[Ollama](https://ollama.com)** running locally (`ollama serve`), with
  the `llama3.1:8b` model pulled — used for clip ranking and recap
  analysis. No cloud AI calls are made.
- **[Orpheus-FastAPI](Orpheus-FastAPI/)** running locally on
  `http://localhost:5005` — used for recap narration TTS. Uses its own
  virtualenv, separate from the project's.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install -r requirements-dev.txt   # for running tests

ollama pull llama3.1:8b
```

Set up `Orpheus-FastAPI/` separately, in its own venv, per its own
instructions.

## Running

```powershell
ollama serve
cd Orpheus-FastAPI; .\venv\Scripts\python.exe -u app.py   # separate terminal
cd C:\Users\lukej\Desktop\ShortsFactory
.\.venv\Scripts\python.exe -m app.gui
```

This launches the desktop editor. From there: import a source video,
click **Find Best Clips** to get AI-suggested moments, select/trim a clip
on the timeline, adjust captions/emoji/effects, then
**Generate Final Video** — or use the AI Recap tab for a narrated recap.

## Running tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The suite (~60 modules under `tests/`) is pure-Python/headless. It
verifies correctness, not the real GUI/media/Ollama/Orpheus behavior —
see `CLAUDE.md` for what still needs a live run.

## Configuration

A few optional environment variables, all with sensible defaults if unset:

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | Ollama server address (default `http://127.0.0.1:11434`) |
| `OLLAMA_MODEL` | Ollama model name (default `llama3.1:8b`) |
| `SHORTSFACTORY_WHISPER_MODEL` | Override the local Whisper transcription model |
| `SHORTSFACTORY_TRANSCRIPTION_QUALITY` | Default transcription quality preset |

## Project layout

See `CLAUDE.md` for the current, maintained layout map, product rules,
and hard-won scoring-pipeline lessons — it's the source of truth for
agents and humans working in this repo.

- `app/gui.py` — desktop app entry point (thin launcher; the real app is
  in `app/gui_app/`)
- `app/gui_app/` — the PySide6 UI: main window, the custom timeline
  widget, the visual style sheet, and one `mixins/` file per feature area
- `app/*.py` — the render pipeline itself (clip discovery, transcription,
  captions, motion, color grading, emoji, SFX, final render)
- `app/recap_intelligence/`, `app/recap_media/` — the AI Recap pipeline
  (identity/research/story map/script, then TTS/sequence/captions/render)
- `tests/` — the automated test suite (`pytest`)
- `docs/briefs/` — design briefs recording why specific implementation
  choices were made
- `docs/history/` — superseded project-status docs, kept for reference
- `output/` — everything a render produces (gitignored)

## Non-negotiable product rules

The full list lives in `CLAUDE.md`. The two most load-bearing:

1. No AI-generated image inserts — removed permanently, do not reintroduce.
2. Manual edits are authoritative and survive regeneration.
