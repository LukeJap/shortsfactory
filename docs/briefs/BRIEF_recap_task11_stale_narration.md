# Task 11 — stale narration in the editor

`app/gui_app/mixins/recap.py`, plus a small addition to the editor asset plan.
First of the Recap blockers. Take this one before the others: while the editor
can play old audio, no other Recap fix is verifiable — you cannot tell a broken
fix from a cached result.

## The bug

`_editor_base_voiceover_clips` (line ~751):

```python
editor_plan = load_editor_asset_plan(editor_plan_path)
planned_clips = clips_of_kind(editor_plan, "VOICEOVER")
planned_ids = {str(clip.get("id", "")) for clip in planned_clips ...}
if expected_ids and expected_ids.issubset(planned_ids):
    return planned_clips

durations = load_voiceover_durations(context.voiceover_manifest_path)
return self._rebuild_voiceover_clips(inputs, durations, sequence)
```

The staleness check is **segment IDs only**. If the persisted editor plan
contains clips whose ids cover the current script's segment ids, the old planned
clips are returned wholesale — their paths, their durations, everything — without
ever asking whether the narration behind those ids changed.

Segment ids are positional (`S_001`, `S_002`, …). Rewriting the script keeps
them. So the normal case — edit the script, regenerate narration, Open in Editor
— takes the early return and reuses the previous plan. That is the handoff's
§24 symptom exactly: "same segment ID, same filename, stale manifest, cached
sequence."

Note what is *not* broken. `synthesize_segment` is correct: its cache key is
`sha256(text, voice, speed)` and it verifies the WAV still exists, so new
narration really is written to disk. `load_voiceover_durations` reads duration
from the WAV itself, not from an estimate. The audio on disk is right; the editor
just does not go and look at it.

## The fix

The voiceover manifest already stores a `content_hash` per segment. Carry it into
the editor plan and compare on load.

1. When `_rebuild_voiceover_clips` builds a VOICEOVER clip, record that segment's
   `content_hash` from the manifest onto the clip (a new field, e.g.
   `narration_hash`).
2. In `_editor_base_voiceover_clips`, replace the id-subset test with: reuse the
   planned clips only if, for **every** expected segment id, the planned clip's
   `narration_hash` equals the manifest's current `content_hash` for that
   segment. Any mismatch, any missing hash, any missing segment → rebuild.
3. A planned clip with no `narration_hash` at all is a plan written before this
   change. Treat it as a mismatch and rebuild once; do not attempt migration.

That makes the editor's reuse decision depend on the same inputs that decide
whether the WAV itself is regenerated, which is the property the handoff asks
for: "if any change, the old narration asset should not be treated as current."

Log one line when the plan is rebuilt, naming how many segments mismatched. This
bug hid for a long time behind a silent early return.

## Second, smaller finding — pitch

`synthesize_segments` is called with `speed=narration_speed` and **no pitch**, and
`_content_hash` covers `(text, voice, speed)` only. That is correct as it stands,
because narrator pitch is applied downstream — `narration_pitch_semitones` is
passed to the sequence and render stages, not to Orpheus.

So pitch does **not** belong in the TTS cache key. But it does belong in the
staleness check for anything derived *after* synthesis. If any processed or
mixed narration asset is cached between the raw WAV and playback, its key must
include `narration_pitch_semitones` and `playback_speed`. Check whether such a
cache exists; if it does not, note that in a comment so the next person does not
re-litigate this.

Do not "fix" this by adding pitch to `_content_hash` — that would force a full
Orpheus re-synthesis on every pitch nudge, which is slow and pointless.

## Tests

- A planned VOICEOVER clip whose `narration_hash` differs from the manifest
  triggers a rebuild.
- Matching hashes for every segment reuse the plan (the fast path still works).
- A plan with clips but no `narration_hash` field rebuilds.
- A script that adds a segment rebuilds (existing behavior, keep it).
- `_rebuild_voiceover_clips` writes `narration_hash` onto each clip.

## Live test

This is the one that matters, and it is quick:

1. Load `input/s17e9a duct tape dystopia.mp4` and its recap artifacts.
2. Generate narration. Open in Editor. Play — note the wording of one segment.
3. Edit that segment's text in `recap_script.json` (change a few words).
4. Regenerate narration. Open in Editor again.
5. **The edited wording must be audible without restarting the app.**

Then the regression check in the other direction: Open in Editor twice with no
script change, and confirm the second open does *not* rebuild (the log line
should not appear). Reuse still has to work, or every editor open pays for a
needless rebuild.

## After this

With narration trustworthy, the rest of the Recap list becomes testable in the
order the handoff gives: dropped narration words, source-caption remapping into
recap-timeline coordinates, SFX and emoji lane population, live music parity.
