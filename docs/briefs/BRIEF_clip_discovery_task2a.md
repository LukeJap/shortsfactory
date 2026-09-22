# Task 2a — split scoring from titling

`app/analyze.py`. This corrects a mistake in the task 2 brief, not in the
implementation. Task 2 was built as specified; the specification was wrong.

## Why it timed out

Two real data points calibrate the machine:

```
1c: 12 selections, num_predict=1696, prompt 22379 chars -> 130.2s  completed
t2: 18 selections, num_predict=3136, prompt 23317 chars -> >180s   timed out
```

From the 1c run, ~792 output tokens in 130.2s (after ~18s of prefill) implies a
generation rate of **~7.1 tokens/sec**. Task 2's payload is ~73 output tokens per
selection (window_id + four sub-scores + 60-char title + 80-char hook) across 18
selections — about 1,314 tokens, or **~204 seconds**. The timeout is 180.

It could not have passed. Task 2 raised the selection count by 50% and the
per-selection payload by ~10% in the same change, and I did not check the product
against the timeout before writing the brief.

## The real design error

Scoring and naming are different jobs with different cardinalities, and task 2
fused them. The final pass generates a title and hook for 18 candidates so that
6 can be shown — two thirds of the most expensive tokens in the run are written
for clips that get discarded seconds later.

Split them.

## Change 1 — the final ranking pass scores only

Remove `title` and `hook` from the final-pass schema. It returns `window_id` plus
the four rubric sub-scores, nothing textual. That is ~30 output tokens per
selection instead of ~73.

## Change 2 — trim the final pool to the top 24

The final pass currently ranks all 46 shortlisted candidates in a 23,317-char
prompt. Take the **top 24 by first-stage score** instead. The prompt drops to
~12,000 chars (~3,500 tokens), which with a ~540-token output fits inside
`num_ctx = 8192`.

That matters for speed, not just correctness. On an 8GB RTX 2070, `llama3.1:8b`
at q4 is ~4.7GB of weights, and a 16k-token KV cache is roughly another 2GB. That
is at or past the card's capacity, so `num_ctx=16384` likely forces partial CPU
offload — which is the most plausible explanation for the 7.1 tok/s rate, slow
for an 8B model on that GPU. Staying at 8192 keeps the whole thing resident.

24 candidates still gives the quotas and backfill from task 2 a real pool to work
with.

## Change 3 — a new titling pass over the chosen clips only

After `select_distinct_ranked_windows` returns the final N clips, make one more
call that takes only those N transcripts and returns `{window_id, title, hook}`
for each. For 6 clips that is a ~3,000-char prompt and ~340 output tokens.

Keep every title/hook guard from task 2 — the generic-phrase rejection, the
4+ letter word grounding check, and the `grounded_title_from_text` /
`make_specific_hook` fallbacks. They move to this pass unchanged.

If the titling call fails or times out, **fall back to the local title/hook
generators and return the clips anyway**. Never lose a completed ranking run to a
cosmetic step.

## Change 4 — scale the timeout with the output budget

A fixed 180s for every request has now failed twice from opposite directions. Let
it track the work:

```python
timeout = max(REQUEST_TIMEOUT_SECONDS, int(num_predict / 5) + 60)
```

At 3,136 tokens that yields 687s. This is a backstop, not the fix — changes 1–3
are what make the run fast. It exists so a slow model or a long episode degrades
into "slower" rather than "failed".

## Projected timing

| stage | work | est. |
|---|---|---|
| 1 | 23 batches, score-only | ~97s |
| 2 | rank top 24, rubric only, num_ctx 8192 | ~86s |
| 3 | title the 6 chosen clips | ~51s |
| | **total** | **~3.9 min** |

Versus 1c's ~2.7 min for a run that produced no titles and five clips.

## On the two task 2 deviations

Both were right calls. Never relaxing the overlap rule is correct — product rule
7 forbids overlapping clips, and returning five good clips beats six with two
that are the same moment. And the GUI not yet reading `title` is task 3's job,
not something to smuggle in here.

## Tests

- The final-pass schema contains no `title` or `hook`.
- The final pass receives at most 24 candidates, chosen by first-stage score.
- The titling pass is called once, with exactly the selected clips.
- A failed or timed-out titling pass still returns clips, with locally generated
  titles.
- `timeout` scales with `num_predict` and never drops below 180s.

## Live test

Find Best Clips for 6 on `input/s17e9b farmers market feud.mp4`. Pass: completes
under ~5 minutes; six clips; at most three over 70s; at least one from the first
third; every clip has a title naming something that actually happens in it. Log
each stage's elapsed time separately so the table above can be checked.
