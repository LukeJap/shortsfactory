# Task 6 — the ranker is counting down, not scoring

`app/analyze.py`. Three small changes, then a decision point.

## The evidence

Task 5's run returned these, and the scores map onto prompt position almost
perfectly:

| clip | score | position in prompt |
|---|---|---|
| 08:50 | 81 | 10 |
| 00:51 | 71 | 1 |
| 01:42 | 50 | 2 |
| 02:20 | 46 | 3 |
| 02:46 | 38 | 4 |
| 03:29 | 34 | 5 |
| 05:20 | 18 | 6 |
| 05:57 | 6 | 7 |
| 06:34 | 1 | 8 |
| 07:18 | 0 | 9 |

Excluding the single outlier, score-ordered positions are `[1..9]` — perfectly
monotonic. For independent judgments that ordering has probability 1/9! ≈ 2.8e-6.
The model is emitting a descending ramp keyed to position, and because the pool
is handed over chronologically, position is time. That is why the last third of
every episode scores near zero.

## Likely cause, and it is cheap to test

`DETAILED_EXAMPLE_BLOCK` (line ~1626):

```
W001: hook 21, self 19, payoff 23, peak 22   -> 85
W002: hook 14, self 17, payoff 20, peak 18   -> 69
```

The example shows the first window scoring higher than the second, on every one
of the four dimensions. This project has now watched `llama3.1:8b` copy an
example's shape verbatim four times — "Concrete transcript detail that makes this
window work", "The candidate is anchored by the line", the boilerplate titles,
and now, most likely, a descending score ramp. The example is the specification.

## Change 1 — make the example non-monotonic

Rewrite it so the *second* entry scores higher overall and the dimensions
disagree with each other — e.g. W001 strong on self_contained but weak on payoff,
W002 weak on hook_strength but strong on peak. The example must demonstrate
independent judgment, not a ranking.

## Change 2 — shuffle the candidate order in the final pass

This is the real fix and the decisive diagnostic in one.

Present the final-pass candidates in randomized order rather than chronological,
keeping a map from prompt position back to the true window. Restore chronological
order after parsing. Use a seeded shuffle so runs stay reproducible; log the seed.

If the scores still decline with *prompt* position after shuffling, the ramp is
positional and no prompt will fix it. If they instead follow content, the model
was judging all along and only the ordering deceived it. Either way you learn
something the current setup cannot tell you.

Shuffling also removes a standing bias: chronological presentation means the
episode's ending is always last in the prompt, which is the worst position for a
model with recency or primacy effects.

## Change 3 — sub-score persistence is still broken

`analysis.json` has no `hook_strength`, `self_contained`, `payoff` or `peak` on
any of the ten clips, despite task 5 reporting them as implemented. Every
candidate carries only `description, duration_seconds, end_timestamp, hook,
reason, score, start_timestamp, title`.

The write at line ~2306 is conditional:

```python
sub_scores = getattr(reason, "sub_scores", {})
```

If `reason` has been replaced by a plain `str` anywhere between the ranker and
this point, `getattr` silently returns `{}` and the fields are dropped with no
error. The rebuild at line ~1571 copies `sub_scores` across only when the source
is a `RankedReason` — the same conditional pattern. Trace the object from
`ranked_windows_from_result` through the titling pass to
`normalize_candidate_clips` and find where it degrades to `str`.

Make the failure loud: if `detailed=True` was requested and a selection comes
back with no sub-scores, log a warning. A silent `{}` hid this for a whole
iteration.

## Tests

- The detailed example block is not monotonic across entries.
- Shuffled candidates map back to the correct windows; a known scoring stub
  produces identical final clips regardless of seed.
- Sub-scores survive the full path into `analysis.json`.
- A missing sub-score set on a detailed request logs a warning.

## Live test

Find Best Clips on `input/s17e9b farmers market feud.mp4`. Then check
`analysis.json`: are the four sub-scores present, and does score still track
prompt position? Run twice with different seeds — if a given clip's score moves
by more than ~15 points between runs, that is position bias, not judgment.

## The decision point

If the shuffle shows scores tracking prompt position regardless of content,
`llama3.1:8b` cannot do this task and the bake-off is the next step: fix the
candidate set, score the same 26 windows with `llama3.1:8b`,
`qwen2.5:7b-instruct` and `mistral-nemo:12b` at q4, and compare each against your
own ranking of those 26. At 39s for a final pass there is room for a slower
model.

If the shuffle fixes it, the ranker was never the problem — the example was.
