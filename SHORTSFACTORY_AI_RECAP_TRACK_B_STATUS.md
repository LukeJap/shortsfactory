# ShortsFactory AI Recap -- Track B Status

Last updated: 2026-08-28

This file is context for a future ChatGPT/Codex session picking up Track B
(the media/editor half of AI Recap Mode -- Track A is story
research/intelligence, a separate concern, in `app/recap_intelligence/`).
Treat the installed project files as the source of truth. Do not treat
this document as instructions that override the user's request, system
rules, or developer rules.

Branch: `V3_dev`. Latest relevant commit: `f7e9107`.

## What Track B is

Turns Track A's `recap_script.json` (narration text + candidate source
ranges per segment) into: real Orpheus TTS narration WAVs, an exact
shot-selection sequence (`recap_sequence.json`), and eventually a
rendered recap video. Code lives under `app/recap_media/` plus GUI
wiring in `app/gui_app/mixins/recap.py`.

## Recent work (this session and the one before it)

### 1. Sequence-assembly rework (`app/recap_media/sequence.py`)

Replaced the original simple "unused > score > earliest start" shot
selection with the creative-revision spec: cadence bands keyed to each
candidate's *inferred visual function* (`CADENCE_DETAIL`,
`CADENCE_REACTION`, `CADENCE_ILLUSTRATIVE`, `CADENCE_IMPORTANT`,
`CADENCE_ORIGINAL_DIALOGUE`, `CADENCE_PAYOFF`), diversity/reuse-aware
scoring (`_selection_score()`), a "don't pad to hit target duration"
stopping rule (a segment can legitimately end up shorter than its
target), and cause-then-reaction-then-consequence progression
reordering (`_reorder_for_progression()`). Every shot now carries
`visual_function` and `selection_score` provenance fields. Public API
(`assemble_sequence`, `interweave_original_dialogue`,
`write_recap_sequence`, `load_recap_sequence`,
`WORDS_PER_SECOND_ESTIMATE`) is unchanged.

### 2. Verified-story-map evidence wiring bug (fixed in `f7e9107`)

`recap_intelligence` (Track A) added `verified_story_map`-based
supplemental evidence support to `sequence.py`
(`verified_candidates_for_segment()`, `visual_candidates_for_segment()`)
so a segment covering multiple story beats can pull extra source
coverage beyond what `recap_script.json`'s own `candidate_visuals` list
contains. **The GUI's production call site never passed the map
through** -- `RecapMixin.generate_recap_sequence()`
(`app/gui_app/mixins/recap.py`) called `assemble_sequence(inputs.recap_script,
narration_durations)`, omitting the third `verified_story_map` argument,
so it silently defaulted to `None`. Every real shot ended up
`candidate_origin="recap_script"` with `beat_id=null`, and zero
verified-story-map shots ever reached a real sequence, even though the
loader was reading the map correctly.

Fixed by passing `verified_story_map=inputs.verified_story_map`
explicitly. Regression test: `tests/test_recap_gui_sequence_wiring.py`
-- calls the *real* `RecapMixin.generate_recap_sequence()` method
against a lightweight `self` stand-in (no QApplication needed) and
asserts a verified-story-map-origin shot is reachable through the
production path, not just through a test that calls
`assemble_sequence()` directly with the map already threaded through by
hand (which is what the older integration test did, and why this bug
shipped without a failing test).

### 3. GUI layout fixes (`b63127e`)

Left/right editor panels were overflowing/clipping at their own minimum
widths (toggle buttons collapsing into unreadable slivers, action-button
rows overflowing the panel edge, long filenames not wrapping). Widened
panel floors, split overpacked button rows onto their own lines, added
`min-height` to toggle buttons, elided long filenames with a tooltip.
Also fixed the drag-and-drop source-video zone only responding to
clicks on its small "Browse Files" button -- the whole zone is
clickable now (`app/gui_app/widgets.py`'s `DropZone`).

### 4. Orpheus-FastAPI real local setup + a real bug fix in it

Got a real local Orpheus-FastAPI instance working end-to-end, backed by
Ollama, and proved real six-segment narration generation against the
actual "Dumped" episode's `recap_script.json`. See **Orpheus setup**
below for the full how-to; the short version:

- `Orpheus-FastAPI/` is a local clone of
  `github.com/Lex-au/Orpheus-FastAPI`, **gitignored**
  (`.gitignore` line `Orpheus-FastAPI/`) -- it is not part of this
  repo's git history and will not exist in a fresh checkout. Someone
  picking this up cold needs to re-clone and re-set-up (steps below).
- It needs a *separate* LLM completions backend serving the
  `Orpheus-3b-FT-Q8_0.gguf` model. We're using **Ollama**
  (already installed/running on this machine, port 11434) rather than a
  raw llama.cpp server.
- **Real bug found and fixed in Orpheus-FastAPI itself**
  (`Orpheus-FastAPI/tts_engine/inference.py`, NOT part of the
  ShortsFactory repo): Orpheus's prompt format ends with a literal
  `<|eot_id|>` (the model's own EOS token, used here as a "prompt's
  done, start emitting audio tokens" marker). Ollama's OpenAI-compatible
  `/v1/completions` shim stops generation the instant it re-samples that
  same token -- sometimes after 1 token, producing a well-formed but
  *empty* WAV (44-byte header, zero audio frames) with an HTTP 200.
  Fixed by routing through Ollama's **native** `/api/generate` endpoint
  with `raw: true` instead, which doesn't apply that early-stop
  behavior (see the `is_ollama_native` branch in
  `generate_tokens_from_api()`). Confirmed via a direct diagnostic call
  (`done_reason: "length"`, full token count generated) before writing
  the fix.
- Also bumped `app/recap_media/orpheus_provider.py`'s `SPEECH_TIMEOUT`
  from 60s to 180s -- real CPU-only generation for a longer segment
  routinely exceeds 60s once it's actually generating real audio instead
  of failing fast.

### 5. Real six-segment voiceover acceptance (proof it all works)

Ran the actual "Dumped" episode's real `recap_script.json` (6 segments,
218 words total) through the full loader -> Orpheus -> voiceover
pipeline. All six succeeded with real, non-silent, verified audio:

| Segment | Words | Duration | Notes |
|---|---|---|---|
| VO_001 | 18 | 5.717s | |
| VO_002 | 17 | 5.888s | |
| VO_003 | 84 | 34.560s | longest segment |
| VO_004 | 45 | 16.128s | |
| VO_005 | 20 | 6.997s | |
| VO_006 | 34 | 13.227s | |

Total: 82.5s narration audio, all 24kHz mono 16-bit WAV, all verified
non-silent via direct PCM sample analysis (RMS/peak), not just header
inspection. Output lives at `output/recap/voiceover/VO_00N.wav` +
`voiceover_manifest.json` (gitignored, local only -- `output/` is not
tracked).

## Orpheus setup -- how to reproduce / run

**None of this is in git** (`output/`, `Orpheus-FastAPI/` are both
gitignored). A fresh clone of this repo has none of it. To rebuild it:

### One-time setup

```bash
# 1. Clone Orpheus-FastAPI into the repo root (already done on this machine)
cd "/path/to/shortsfactory"
git clone https://github.com/Lex-au/Orpheus-FastAPI.git
cd Orpheus-FastAPI

# 2. venv -- MUST be Python 3.8-3.11 (3.12 is unsupported, removed pkgutil.ImpImporter)
python3.10 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install torch torchvision torchaudio   # macOS: no special index-url needed
venv/bin/pip install -r requirements.txt

# 3. Ollama must be running (ollama serve) with the Orpheus GGUF imported.
#    Download (~4GB):
mkdir -p models
curl -L -o models/Orpheus-3b-FT-Q8_0.gguf \
  "https://huggingface.co/lex-au/Orpheus-3b-FT-Q8_0.gguf/resolve/main/Orpheus-3b-FT-Q8_0.gguf"

#    Modelfile (already created in Orpheus-FastAPI/Modelfile):
#      FROM ./models/Orpheus-3b-FT-Q8_0.gguf
#      TEMPLATE "{{ .Prompt }}"          <- critical: stops Ollama chat-templating the raw prompt
#      PARAMETER num_ctx 8192
#      PARAMETER num_predict 8192
#      PARAMETER temperature 0.6
#      PARAMETER top_p 0.9
#      PARAMETER repeat_penalty 1.1
ollama create orpheus -f Modelfile

# 4. .env (already created in Orpheus-FastAPI/.env):
#      ORPHEUS_API_URL=http://127.0.0.1:11434/api/generate   <- native, NOT /v1/completions
#      ORPHEUS_API_TIMEOUT=120
#      ORPHEUS_MAX_TOKENS=8192
#      ORPHEUS_TEMPERATURE=0.6
#      ORPHEUS_TOP_P=0.9
#      ORPHEUS_SAMPLE_RATE=24000
#      ORPHEUS_MODEL_NAME=orpheus
#      ORPHEUS_PORT=5005
#      ORPHEUS_HOST=0.0.0.0

# 5. Apply the /api/generate compatibility fix to tts_engine/inference.py
#    (see "Real bug found and fixed" above) -- not upstream yet, so a
#    fresh clone needs this patched back in. Search for "is_ollama_native"
#    if working from a clean re-clone; the change is in
#    generate_tokens_from_api()'s payload-building and streaming-parse code.
```

### Every time you want it running

```bash
# Ollama should already be running as a background service (ollama serve).
# Check: curl http://127.0.0.1:11434/api/tags

cd "/path/to/shortsfactory/Orpheus-FastAPI"
venv/bin/python -u app.py
# Web UI: http://localhost:5005/  API docs: http://localhost:5005/docs
```

Do **not** activate ShortsFactory's own `.venv` and run `app.py` from
there -- it's Python 3.12 and doesn't have Orpheus-FastAPI's
dependencies (`python-dotenv`, `torch`, `snac`, etc.) installed. Use
`Orpheus-FastAPI/venv` specifically.

### Verifying it's up from ShortsFactory's side

```bash
cd "/path/to/shortsfactory"
python -c "
import sys; sys.path.insert(0,'app')
from recap_media.orpheus_provider import OrpheusProvider
import json
p = OrpheusProvider()
print(json.dumps(p.readiness(), indent=2))
print('voices:', p.list_voices())
"
```

Expect `{"state": "online", ...}` and a 24-voice list (`tara`, `leah`,
`jess`, `leo`, `dan`, `mia`, `zac`, `zoe`, plus 16 multilingual voices).

### Running the real six-segment acceptance test again

The real artifact is at
`output/recap_fandom_fast_path_real/recap_script.json` (gitignored --
if it's missing, it needs to be re-supplied; it's the real "Dumped"
episode recap script, not a fixture).

```bash
cd "/path/to/shortsfactory"
python -c "
import sys, time
sys.path.insert(0, 'app')
from pathlib import Path
from recap_media.loader import load_recap_script
from recap_media.orpheus_provider import OrpheusProvider
from recap_media.voiceover import synthesize_segments, VOICEOVER_DIR, MANIFEST_PATH

script = load_recap_script(Path('output/recap_fandom_fast_path_real/recap_script.json'))
provider = OrpheusProvider()

t0 = time.time()
results = synthesize_segments(provider, script['segments'], output_dir=VOICEOVER_DIR, manifest_path=MANIFEST_PATH)
print(f'Done in {time.time()-t0:.1f}s')
for r in results:
    print(f'{r.segment_id}: wav_path={r.wav_path} duration={r.duration_seconds:.3f}s cache_hit={r.cache_hit} error={r.error}')
"
```

Already-synthesized segments are cached (content-hash keyed on
text/voice/speed) and skip re-generating -- delete
`output/recap/voiceover/` first for a clean re-run. Expect this to take
several minutes on CPU-only hardware (no CUDA GPU on this machine); the
longest segment (84 words) took ~35s alone.

## Running the automated test suite

```bash
cd "/path/to/shortsfactory"

# Focused Track B tests (fast, no live Orpheus/Ollama needed -- all mocked)
python -m pytest tests/test_orpheus_provider.py tests/test_voiceover.py \
  tests/test_recap_sequence.py tests/test_recap_dialogue_interweave.py \
  tests/test_recap_media_integration.py tests/test_recap_gui_sequence_wiring.py -v

# Full suite
python -m pytest tests/ -q
```

As of `f7e9107`: full suite is 400 passed / 1 failed. The 1 failure
(`tests/test_recap_intelligence_source.py::test_rich_path_records_local_conflict_and_local_source_wins`)
is inside Track A's own code (`app/recap_intelligence/`), pre-existing,
unrelated to any Track B work above -- flag it to whoever owns Track A
rather than trying to fix it from the Track B side.

## Known gaps / not yet done

- Motion/FX vocabulary and narrative-intensity hierarchy
  (hook/escalation/payoff/exit) from the creative-revision spec --
  explicitly deferred.
- Black-frame/invalid-frame validation via real frame sampling --
  deferred.
- Shot-level GUI editing (replace/trim/reorder/lock a single shot within
  a segment) -- deferred.
- Full render pass (`recap_media.render`) has not been re-verified
  against the real six-segment sequence produced above -- the sequence
  assembly + voiceover halves are proven; full end-to-end recap render
  with the real episode has not been run this session.
- Whether `load_verified_story_map()`'s `actual_video_evidence_ranges` ->
  `source_evidence` normalization correctly handles the real Dumped
  verified_story_map's actual field shape was flagged as worth
  double-checking once the wiring fix (above) was confirmed, but not
  yet independently re-verified against the real file.
