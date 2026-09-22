# Task 1c — the final ranking pass is being truncated mid-JSON

`app/analyze.py` only. Small, targeted.

## What the log actually shows

Task 1b worked. 23 batches at 4.7–7.7s each is roughly 2.5 minutes of ranking —
inside the ~7 minute target — and 23 batches of 8 means ~180 candidates, inside
the 120–220 target. Every first-stage batch returned its 2 selections cleanly.

The run died on the **final shortlist** pass:

```
Ranking final shortlist: 46 candidates, 22664 prompt chars, timeout=180s
Analysis failed: The window-ranking response was not valid JSON:
Expecting ',' delimiter: line 52 column 6 (char 3549)
```

The response was not malformed. It was **cut off**. Character 3549 at roughly
3.47 chars/token is ~1020 tokens — `RANKING_NUM_PREDICT = 1024`. The model hit
the output cap partway through the 11th or 12th selection and stopped mid-object.

## Root cause

`RANKING_NUM_PREDICT` is a fixed 1024 regardless of how many selections the
request asks for. The final pass asks for 12 (`max(target, target*2)`), and each
selection carries an unbounded free-text `reason`:

```python
"reason": {"type": "string"},          # window_ranking_json_schema, no maxLength
```

Worse, the prompt's example (line ~1221) literally reads:

```
"reason": "Concrete transcript detail that makes this window work."
```

so the model copies that 55-character phrase as a prefix on **every** reason
before writing any content. At ~320 chars per selection, 12 selections need
~1,100–1,300 output tokens. The cap is 1,024. It was always going to truncate;
the first-stage batches only survived because they ask for 2 selections each.

## Four changes

**1. Scale the output budget with the request.**

```python
RANKING_NUM_PREDICT_BASE = 256
RANKING_NUM_PREDICT_PER_SELECTION = 120
```

and in `call_ollama_window_ranker`, compute
`num_predict = RANKING_NUM_PREDICT_BASE + RANKING_NUM_PREDICT_PER_SELECTION * selection_count`.
For 12 selections that is 1,696 tokens — comfortable headroom over the ~1,300
actually needed. Delete the fixed `RANKING_NUM_PREDICT`.

**2. Scale `num_ctx` too — it must cover prompt *and* output.**

The final prompt was 22,664 chars ≈ 6,500 tokens. Add 1,696 output and it
exceeds the current `RANKING_NUM_CTX = 8192`, so even with a bigger
`num_predict` the context would silently truncate the prompt instead.

```python
num_ctx = max(8192, next_power_of_two(len(prompt) // 3 + num_predict + 512))
```

capped at 16384. Keep 8192 as the floor so first-stage batches are unaffected.

**3. Bound the reason, and stop seeding the boilerplate.**

Add `"maxLength": 160` to the `reason` property in `window_ranking_json_schema`,
and replace the prompt example with a short concrete one — e.g.
`"reason": "Krabs refuses to pay, then bills the customers."` — so the model
stops prefixing 55 characters of filler onto every entry. Note: llama.cpp's
JSON-schema-to-grammar support for `maxLength` varies by version, so treat this
as belt-and-braces on top of change 1, not a substitute for it.

**4. Salvage a truncated array instead of failing the run.**

This is the durable fix, and the same principle as the ranker-resilience work: a
partial response should degrade, not abort. When `extract_json_object` fails on a
window-ranking response, scan the `"selections"` array and recover every
*complete* object, discarding the partial tail, then hand the result to the
existing `minimum_count` path.

I prototyped and tested this. On a response truncated at exactly character 3549
(the real failure point), it recovers **11 of 12 selections** with correct
window_ids and scores; a well-formed response round-trips byte-identical. A
brace-depth scan that tracks string state and escapes is enough — no regex, no
new dependency. Log at warning level when salvage fires, including how many
selections were recovered versus requested, so this never fails silently.

## Tests

- A truncated-mid-object response recovers N-1 complete selections and does not
  raise.
- A well-formed response is unchanged by the salvage path.
- A response truncated before the first complete object still raises.
- `num_predict` scales with `selection_count`; `num_ctx` rises above 8192 for a
  long prompt and stays at 8192 for a short one.

## Live test

Same as before: Find Best Clips for 6 on `input/s17e9b farmers market feud.mp4`.
Pass: completes end to end, six clips returned, durations not all 70–90s. The
final-pass log line should show the computed `num_predict` and `num_ctx`.
