# Task brief — Find Best Clips: make ranking resilient to short/partial model results

**Scope:** `app/analyze.py` only (plus `tests/test_analyze.py`).
**Do not** change window generation, the exact-60.0s rule, batch sizes, prompt
wording, or anything outside the ranking path.

---

## Problem

Hierarchical ranking already works (batches of 8 → shortlist → final pass). But a
single imperfect model response still kills the entire run.

`rank_window_request` (~line 1212) ends with:

```python
if len(ranked) != selection_count:
    raise RuntimeError(
        f"Ollama returned {len(ranked)} valid selections for {request_label}; "
        f"expected {selection_count}."
    )
```

The JSON schema pins `minItems == maxItems == selection_count`, so the model
almost always returns the right *array length*. The shortfall comes from
`ranked_windows_from_result`, which drops duplicate `window_id` values. When
`llama3.1:8b` repeats a window ID — common at temperature 0 on a weak batch — the
list comes back short and the whole analysis aborts.

An analysis makes ~6 ranking requests. One repeat anywhere = total failure.
`rank_first_stage_batch` only catches `WindowRankingTimeout`, so this path gets no
retry at all.

---

## Change 1 — accept partial results, retry once, never abort on a first-stage batch

In `rank_window_request`, add a keyword-only parameter `minimum_count: int = 0`.

Replace the strict equality check with:

- If `len(ranked) >= selection_count` → return as-is (trimmed to `selection_count`).
- If `minimum_count <= len(ranked) < selection_count` → log a warning naming the
  request label, requested count and actual count, then return what came back.
- If `len(ranked) < minimum_count` → raise a new
  `WindowRankingShortfall(RuntimeError)` carrying `request_label`, `requested` and
  `returned`, so callers can distinguish it from transport errors.

In `rank_first_stage_batch`:

- Call `rank_window_request` with `minimum_count=1`.
- Widen the `except` to `(WindowRankingTimeout, WindowRankingShortfall)` so the
  existing half-split retry covers both. Keep the "split once" behavior — no
  retry loops.
- If the retry still yields nothing for that batch, log a warning and **return an
  empty list** rather than raising. Losing one chronological batch's shortlist
  must not fail the run; the other batches still cover the source.

In `rank_exact_minute_windows`:

- After the shortlist loop, if `shortlist` is empty, raise a clear
  `RuntimeError("No candidate windows survived first-stage ranking.")` — that case
  is genuinely fatal.
- Call the final `rank_window_request` with
  `minimum_count=min(target_clip_count, len(rankable_windows))` so a slightly
  short final pass still produces clips, but a collapsed one is reported.

`select_distinct_ranked_windows` already returns fewer than requested when
overlap filtering removes candidates, so downstream is safe with short lists.
Confirm the caller at ~line 2266 logs a warning and continues when it receives
fewer than the requested count instead of treating it as an error.

---

## Change 2 — set an explicit Ollama context window

In `call_ollama_window_ranker` (~line 944), the payload options are only
`temperature` and `top_p`. Ollama defaults `llama3.1:8b` to a 4096-token context,
and a batch of 8 one-minute transcripts plus instructions plus
`FIRST_STAGE_CONTEXT_MAX_CHARS = 2400` can exceed that — silently truncating the
prompt, which is the most likely cause of the historical "0 valid candidates"
reports.

Add to `options`:

```python
"num_ctx": RANKING_NUM_CTX,     # new module constant, 8192
"num_predict": RANKING_NUM_PREDICT,  # new module constant, 1024
```

and add `"keep_alive": "10m"` at the top level of the payload so the model is not
unloaded and reloaded between the ~6 ranking requests.

Do not raise `REQUEST_TIMEOUT_SECONDS` (currently 180). The point is to stop
truncation and reloading, not to wait longer.

---

## Change 3 — log the raw response when parsing yields nothing

In `rank_window_request`, when `ranked` is empty, log the first 500 characters of
the raw model response text before raising or returning. Parse failures must be
debuggable and must never be reported as "the model thinks every clip is bad."

This requires `call_ollama_window_ranker` to make the raw text available — return
it alongside the parsed object, or stash it on the raised/returned value.
Whichever is least invasive.

---

## Change 4 — remove dead constant

`MAX_VALID_WINDOWS_FOR_PROMPT = 30` (line 50) is defined and never referenced.
Delete it. Verify with a repo-wide grep first.

---

## Tests to add in `tests/test_analyze.py`

All with a stubbed Ollama call — no network.

1. `rank_window_request` returns 5 items when 6 were requested and
   `minimum_count=1`, and logs a warning.
2. `rank_window_request` raises `WindowRankingShortfall` when the result is below
   `minimum_count`.
3. `rank_first_stage_batch` returns `[]` (does not raise) when both the initial
   call and the split retry come back empty.
4. `rank_exact_minute_windows` still returns exact-60.0s, non-overlapping windows
   when one of four batches contributes nothing.
5. `call_ollama_window_ranker` payload includes `num_ctx`, `num_predict` and
   `keep_alive`.

Existing `tests/test_analyze.py` must keep passing unchanged.

---

## Live test

```powershell
ollama serve
cd C:\Users\lukej\Desktop\ShortsFactory
.\.venv\Scripts\python.exe -m pytest tests/test_analyze.py -x -q
```

Then in the app: load `input/s17e9b farmers market feud.mp4`, run Find Best Clips
for 6 candidates.

Pass criteria:

- completes without "Ollama took too long" and without an aborted analysis;
- log shows per-batch candidate count, prompt chars, timeout and elapsed time;
- returns 6 clips (or fewer *with a warning*, never a crash);
- every clip is exactly 60.0s;
- clips are non-overlapping and spread across the episode, not clustered in one
  two-minute stretch, and not drawn from the intro/theme.
