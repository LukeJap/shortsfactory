# Task 5 — the GUI throws away 4 of your 10 clips

`app/gui_app/mixins/ai_clip_hunter.py` and `app/analyze.py`.

## What task 4 achieved

Titles became real hooks: "The true cost of a free sample", "A knife handoff with
a sinister twist", "Jim Dandy Jams: a taste sensation?". Coverage now spans 0:51
to 10:11 including the first third, which has been empty for four runs. Duration
spread is 20.1s to 84.6s with two clips over 45s — the band quota works.

Three defects remain.

## Defect 1 — a hardcoded cap of 6

`ai_clip_hunter.py` line ~633:

```python
for rank, candidate in enumerate(
    candidates[:6],
    start=1,
):
```

The analyzer logs "Ranker selected 10 candidate windows" and writes 10 clips to
`analysis.json`. The GUI slices off the last four before building cards, then
reports `f"✓ Found {len(suggestions)} strong clip candidates."` — which is why it
says 6. The count is honest about a list that was already truncated.

**Fix:** drop the `[:6]`. Iterate all returned candidates. If a display cap is
wanted later it should come from the requested clip count, never a literal.

Check the card container while you are there: if it is a fixed set of six widgets
rather than a dynamic layout, the cap exists twice and removing the slice alone
will not show clips 7–10.

## Defect 2 — the fallback generator emits exactly what the guard rejects

Three hooks in the run are quote fragments, two of them cut mid-sentence:

```
#1  "Stop fighting"
#2  "Hey, honey partner, would you like a"
#9  "Agreed"
```

The log explains it: `Titling retry for 4 clip(s) with a rejected title or hook.`
The guard fired, the retry did not satisfy it either, and then
`analyze.py` line ~2093 ran:

```python
candidate["hook"] = ranker_hook or make_specific_hook(analysis, candidate)
```

`make_specific_hook` falls through to `grounded_title_from_text(description)`,
which returns a transcript excerpt — a verbatim quote, truncated at a word
budget. The fallback produces precisely the thing task 4's substring guard exists
to forbid.

**Fix:** when the model's hook is rejected and the retry fails, do not
manufacture a quote. Options in order of preference:

1. Derive the hook from the clip's own accepted `title` (which passed the guard)
   rather than from raw transcript — e.g. reuse the title as the hook and leave
   the hook line off the card, since a duplicate is already suppressed there.
2. Leave `hook` empty and let the card show the title alone.

Either beats a truncated line of dialogue. Assert in a test that no
fallback-produced hook is a substring of its clip transcript — the same check the
guard applies, now applied to the fallback path too.

## Defect 3 — scores collapse to 0

```
81, 71, 50, 46, 38, 34, 18, 6, 1, 0
```

The bottom three are implausible. `score` is the sum of four 0–25 rubric
dimensions, so a 0 means the model scored hook strength, self-containment, payoff
*and* peak all at zero — for a clip it simultaneously titled "The truth about the
booth rental agreement". The scores also decline almost monotonically with
position, which is the signature of a model emitting a descending ramp rather
than judging each candidate.

I cannot confirm this from `analysis.json` because the sub-scores are not
persisted — a candidate carries only `description, duration_seconds,
end_timestamp, hook, reason, score, start_timestamp, title`.

**Fix, in two parts:**

1. Persist `hook_strength`, `self_contained`, `payoff` and `peak` on each
   candidate clip in `analysis.json`. Cheap, and it makes this diagnosable.
2. Stop calling everything strong. `f"✓ Found {n} strong clip candidates."`
   applied to a clip scoring 0 is not credible. Report the count plainly and let
   the per-card score carry the judgment.

Do **not** rescale or normalize the scores to look better. If the model is
producing a ramp, the fix is the model or the prompt, not the presentation.

## Tests

- All returned candidates reach the card list; a 10-clip analysis renders 10.
- No fallback hook is a substring of its clip's transcript.
- Sub-scores round-trip into `analysis.json`.
- The summary line reports the actual count without asserting strength.

## Live test

Find Best Clips on `input/s17e9b farmers market feud.mp4` at the default count.
Pass: 10 cards visible in the panel, the summary count matches, no hook is a
fragment of its own dialogue, and `analysis.json` carries four sub-scores per
clip.

## Then: the bake-off

Once sub-scores are visible, look at the bottom three clips. If their four
dimensions are all 0–2, `llama3.1:8b` is not scoring, it is ranking by position,
and no prompt will fix that. That is the trigger for the model comparison — hold
the candidate set fixed, score the same 26 windows with `llama3.1:8b`,
`qwen2.5:7b-instruct` and `mistral-nemo:12b` at q4, and compare against your own
ranking. The final pass now costs 27s, so a slower model is affordable.
