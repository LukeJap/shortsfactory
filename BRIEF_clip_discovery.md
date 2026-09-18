# Plan — Opus-Clips-style clip discovery

Target behavior (per the reference product): one episode yields **10–20 clips**,
each **variable length**, each starting and ending on a real content boundary,
each with a **score** and an **accurate title/hook**, presented as a grid.

Current behavior: 40 fixed windows on a rigid grid, ranked down to 6, no titles.

---

## Root cause of poor clip quality

`generate_valid_windows` in `app/analyze.py` builds candidates like this:

```python
start = 0.0
while start <= latest_start:
    starts.append(start)
    start += WINDOW_STRIDE_SECONDS      # 15
...
window_end = window_start + MAX_CLIP_SECONDS   # always exactly 60.0
```

Every candidate is an arbitrary 60-second crop on a 15-second grid. Its docstring
defends this ("a strong sixty-second scene should not become 52 seconds just
because a transcript line ends there") — but that reasoning is backwards for
short-form. A good Short starts on the setup line and ends on the payoff line.
On a 15s grid the true start is on average 7.5s off, and the true length is
almost never 60.0s.

So the ranker is choosing the least-bad of 40 arbitrary crops. No amount of
ranker improvement fixes that — the good clip is usually **not in the candidate
set at all**. This, not ranking, is why the clips are mediocre.

The reference screenshot confirms it: those clips run 0:14, 0:15, 0:25, 0:30,
0:33, 0:34, 0:51, 1:18, 1:29, 1:32, 1:37. Not one is 60s.

**Decision taken: retire the exact-60.0s rule** in favor of bounded variable
length. CLAUDE.md rule #7 has been updated accordingly.

---

## Three tasks, in order. Do task 1 only for now.

### Task 1 — variable-length moment candidates (this task)

Replace grid windows with boundary-derived moments. `app/analyze.py` only.

**Beats.** Build a list of "beats" from transcript segments: a beat boundary
falls where the silence gap between consecutive segments is `>= BEAT_GAP_SECONDS`
(start at 0.6). Consecutive segments closer than that merge into one beat. Keep
each beat's start, end, text and terminal punctuation.

**Candidates.** For each beat as a potential start, emit candidates ending at
each later beat end whose total duration falls within
`[MIN_CLIP_SECONDS, MAX_CLIP_SECONDS]` — new values **15.0** and **90.0**. Cap at
the 4 longest qualifying ends per start beat, so candidate count stays roughly
`4 x beat_count` rather than quadratic. Drop candidates whose start beat begins
mid-sentence (previous beat's text has no terminal punctuation) and whose end
beat does not end on terminal punctuation — a clip that starts or ends mid-clause
is never good.

Dedupe candidates whose start and end are both within 1.0s of an already-kept
candidate. Expect roughly 80–200 survivors on a 12-minute episode; log the count.

**Keep:** whole-source coverage, `CandidateWindow` as the return type, the
existing `snap_window_to_sentence_boundaries` helper if it still applies, and the
existing ranking path — it should keep working unchanged against a
variable-length candidate list.

**Constants:** `MIN_CLIP_SECONDS = 15.0`, `MAX_CLIP_SECONDS = 90.0`,
`BEAT_GAP_SECONDS = 0.6`, `MAX_ENDS_PER_START = 4`. Delete
`WINDOW_STRIDE_SECONDS`, `PREFERRED_MIN_CLIP_SECONDS`,
`PREFERRED_MAX_CLIP_SECONDS`, `EXTENDED_CLIP_MIN_SECONDS` if they become unused —
grep first.

**Tests.** Update every test asserting `duration_seconds == 60` to assert
`15.0 <= duration <= 90.0` instead. Add: candidates never start or end
mid-sentence; candidate count stays bounded on a long synthetic transcript;
coverage reaches the final beat of the source; dedupe collapses near-identical
candidates.

**Live test.** `input/s17e9b farmers market feud.mp4`, Find Best Clips for 6.
Pass: completes; clips vary in length; each one starts on a clean line and ends
on a payoff rather than mid-sentence.

---

### Task 2 — per-clip score + title (next)

Change the ranker contract so every returned clip carries a title and hook, and
return 16 instead of 6.

- Stage 1 stays cheap: batches of 8, output `{window_id, score}` only — small
  output, fast on an 8B model, scores *every* candidate rather than shortlisting.
- Stage 2 runs on the top ~20 by score: richer prompt returning
  `{window_id, score, title, hook, reason}` where `title` is <= 60 chars and must
  be grounded in that clip's own transcript text (the existing
  `grounded_title_from_text` / `is_generic_hook` guards already exist and should
  be reused to reject "In this episode…" style output).
- Then overlap-filter and return the top N with scores attached.

### Task 3 — results grid UI (after task 2)

`ai_clip_hunter.py` mixin: card grid with score badge, title, duration and a
thumbnail, replacing the current list. Mirrors the reference layout.

---

## Separate experiment worth running

Clip *judgment* is model-bound, and `llama3.1:8b` is a weak judge. On an RTX 2070
(8GB) the realistic alternatives are `qwen2.5:7b-instruct` and, tight but
possibly viable at q4, `mistral-nemo:12b`. Worth a bake-off on one episode with a
fixed candidate set: same 20 candidates, each model scores them, compare against
your own ranking of those 20. Cheap to run, and it tells you whether to invest
further in prompt work or in a bigger model. Do this *after* task 1 — comparing
judges on arbitrary 60s crops proves nothing.
