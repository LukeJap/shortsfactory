# Task 16 — expression tags must not reach the screen

`app/recap_media/caption_alignment.py` (or wherever narration cues are built),
`app/recap_media/voiceover.py`. Product rule 8, currently violated in every
render.

## Confirmed from the live artifacts

`output/recap_duct_tape_dystopia_v2/recap_captions.json`, rendered 2026-09-22:

```
   0.00 -   1.50  N_001  narration  '<chuckle>'
  51.40 -  53.60  N_004  narration  '<gasp>'
 104.65 - 105.63  N_007  narration  '<laugh>'
```

Three cues whose entire visible text is a TTS instruction. The first one occupies
**the opening 1.5 seconds** — the hook, the single most important frame-range in
a Short, showing `<chuckle>` to the viewer.

These are burned into `narration.ass` and therefore into every export.

The script itself predicted this. `recap_script.json` carries the warning:
"If ShortsFactory has not yet implemented TTS-only tag handling, these tags may
also appear in visible narration captions."

## Also worth knowing: caption remapping is fine

While verifying this I tested §25 (source captions on the wrong timeline) against
the same artifacts: all 310 cues land inside their own segment's window once
`playback_speed: 1.25` is applied. **Zero** misplaced. That item can come off the
open list — the earlier symptom appears to have been fixed already.

## The model

Keep `text` as the authored field, exactly as it is on disk today, and derive
two views rather than requiring every script to be rewritten:

```
tts_text     = segment.get("tts_text")     or text          # what Orpheus speaks
display_text = segment.get("display_text") or strip_tags(text)   # what viewers read
```

`strip_tags` removes `<laugh> <sigh> <chuckle> <cough> <sniffle> <groan> <yawn>
<gasp>` and collapses the whitespace left behind. Existing scripts keep working
untouched; a future authoring UI can write the explicit fields.

## Alignment — the part to get right

Do **not** align `display_text` against the audio. The WAV contains a real
non-verbal vocalization where the tag was, and aligning text that has no token
for it will drift the whole segment's word timings.

Align `tts_text` as now, then post-filter the resulting cues:

1. strip tags from each cue's visible text;
2. **drop any cue whose text is empty after stripping** — do not emit a blank
   caption holding the screen for 1.5s;
3. leave the neighbouring cues' timings untouched.

That keeps timing derived from the audio that actually exists, and removes only
what should never have been visible.

## Cache key

`voiceover._content_hash(text, voice, speed)` should hash **`tts_text`**, which
is what gets synthesized. Because `tts_text` falls back to `text`, every existing
manifest entry hashes identically — no mass re-synthesis, no invalidation of the
WAVs you just generated. Verify that: the live test below must not re-run
Orpheus for unchanged segments.

This also satisfies rule 10's "cache keys include text, tts_text, voice, speed".

## Small adjacent fix

`recap_script.json` carries a stale `word_count` (17 on a 16-word line) because
it was edited by hand. It only matters as the fallback when a WAV is missing, but
the loader should recompute it on import rather than trust an externally edited
number.

## Tests

- A segment whose text is `"<chuckle> The Krusty Krab is ..."` produces a
  display cue with no tag and a `tts_text` that keeps it.
- A cue that is only a tag is dropped, not blanked.
- Word timings of the surrounding cues are unchanged by stripping.
- `_content_hash` is byte-identical for a segment with no explicit `tts_text`
  (no re-synthesis of existing WAVs).
- An explicit `display_text` overrides the derived one.
- `word_count` is recomputed on import.

## Live test

Re-render the duct tape recap. Then:

```powershell
python -c "import json,re; c=json.load(open(r'output\recap_duct_tape_dystopia_v2\recap_captions.json')); print([x['text'] for x in c['cues'] if re.search(r'<(laugh|sigh|chuckle|cough|sniffle|groan|yawn|gasp)>', x['text'], re.I)])"
```

Must print `[]`. Then watch the first two seconds of the render — the opening
caption should be the first real words, not a tag, and the chuckle should still
be audible.
