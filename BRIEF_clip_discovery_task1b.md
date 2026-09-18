# Task 1b — fix candidate distribution and count

Task 1 works, but measured against three real Whisper transcripts from
`output/transcript_cache/` it has two problems the synthetic test could not show.
`app/analyze.py` only.

## Measured on real episodes (644s, 677s, 696s)

| | 6fe32657 | 7c850424 | ccfcb9fe |
|---|---|---|---|
| candidates | 365 | 336 | 351 |
| median duration | 75.3s | 69.0s | 72.4s |
| 15–30s | 1.4% | 4.5% | 4.6% |
| 75–90s | 51.2% | 36.6% | 42.5% |

Punctuation held up fine (Whisper terminal-punctuates 141–164 of 148–166
segments), coverage reached 100% of source on all three, and durations do vary.
Those parts of task 1 are sound.

## Problem 1 — the generator is biased long

```python
qualifying_ends.sort(key=lambda item: -item[0])
for _duration, end_index in qualifying_ends[:MAX_ENDS_PER_START]:
```

It keeps the **4 longest** qualifying ends per start beat, so nearly half of all
candidates land in 75–90s and only ~3% land in 15–30s. The reference product's
clips skew the other way — of the 11 visible durations in the screenshot, six are
under 35s. The dedupe step compounds it by keeping the longest of each
near-identical cluster.

**Fix:** select ends *spread across the duration range* instead of the longest.
For each start beat, pick the qualifying end nearest each of
`CANDIDATE_DURATION_TARGETS = (20.0, 35.0, 55.0, 80.0)`, dedupe the resulting
indices, and keep those. Same cost, even coverage.

## Problem 2 — 350 candidates is too many to rank

At batch size 8 that is 44 first-stage Ollama calls plus the final pass. At a
realistic 15s per call on `llama3.1:8b` that is over 11 minutes per analysis, and
44 chances for a batch to time out. The old grid produced 40 candidates in 6
calls.

**Fix:** require a real pause before a start beat, and widen dedupe.

```python
CANDIDATE_START_GAP_SECONDS = 1.5   # silence before the start beat
CANDIDATE_DEDUPE_TOLERANCE_SECONDS = 3.0   # was 1.0
```

A start beat qualifies only if `start_beat.start - previous_beat.end >=
CANDIDATE_START_GAP_SECONDS` (or it is the first beat), on top of the existing
terminal-punctuation rule. A clip that opens after a real pause opens cleanly;
one that opens 0.2s after the previous line is a mid-scene splice.

## Measured result of both fixes together

| | candidates | median | 15–30s | 30–45s | 45–60s | 60–75s | 75–90s |
|---|---|---|---|---|---|---|---|
| now | ~350 | 72s | 3.4% | 7.4% | 13.4% | 32.0% | 43.7% |
| after | ~171 | 42s | 29% | 23% | 23% | 14% | 11% |

~171 candidates → 23 Ollama calls (~6 min at 15s/call), and every duration bucket
is represented. I verified these numbers by running the real generator plus a
prototype of the fix over the three transcripts above — they are measurements,
not estimates.

## Also

`PREFERRED_MIN_CLIP_SECONDS` / `PREFERRED_MAX_CLIP_SECONDS` are still `60` and
still drive the fallback sort in `normalize_candidate_clips` (~line 1975), which
now silently prefers 60s clips against a generator that no longer targets 60s.
Either delete that preference from the sort or set the pair to `(25.0, 60.0)`.
Flagged in the task 1 handoff and worth closing here rather than deferring.

## Tests

- Candidates are spread across duration buckets: on a synthetic transcript with
  many qualifying ends, assert at least one candidate under 30s and at least one
  over 70s from the same start beat region.
- A start beat preceded by a gap smaller than `CANDIDATE_START_GAP_SECONDS` does
  not produce candidates.
- The first beat of the source is always eligible as a start.
- Existing task 1 tests (no mid-sentence starts/ends, bounded count, coverage,
  dedupe) keep passing.

## Live test

Unchanged from task 1: `input/s17e9b farmers market feud.mp4`, Find Best Clips
for 6. Additionally **time the run** and log the candidate count. Pass: under
~7 minutes, candidate count 120–220, and the six returned clips are not all
70–90s.

## Note for task 2

Even 23 calls is slow. Task 2's stage 1 — batches scored with `{window_id, score}`
output only — should cut per-call time sharply, since output tokens dominate
generation time on an 8B model. Worth measuring stage-1 call time before
deciding whether to shrink the candidate set further.
