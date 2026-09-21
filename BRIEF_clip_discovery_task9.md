# Task 9 — the scale is pinned to its top band

`app/analyze.py`. Two causes, both in the prompt text, both specific.

## The data

Sub-scores across the ten selected clips:

```
  hook_strength    [20]                 1 distinct value, always the band floor
  self_contained   [15,18,19,20,22]     5 distinct, all >= 15
  payoff           [15,18,22,23]        4 distinct, all >= 15
  peak             [20,21,22]           3 distinct, all >= 20
```

Across 10 clips and 4 dimensions, **nothing scored below 15**. The 0-14 range is
entirely unused: a 0-25 scale behaving as a 15-23 scale. Totals run 75-86 against
calibration anchors of 86 and 19.

Anchoring worked mechanically — `self_contained` and `payoff` now produce real
spread, which they did not before. Two dimensions are broken, not the approach.

## Cause 1 — the hook_strength bands contradict each other

```
  0-5   the line is filler, a greeting, or a mid-sentence continuation
  20-25 cannot be understood without watching on -- a real open loop
```

A clip cut from mid-episode almost always opens mid-scene. Its first line
*literally* cannot be understood without watching on — because it is a
fragment, not because it is a hook. Both the top band and the bottom band
describe the same observable text, and the model resolves the ambiguity upward
every single time. Hence 20, on all ten, in both runs.

**Fix:** make the top band about deliberate withholding, and make the
fragment case explicitly low:

```
hook_strength (0-25): judge ONLY the first spoken line.
  0-5   filler, a greeting, or a fragment that is merely confusing --
        a mid-sentence cut scores here, however intriguing it sounds
  6-12  a complete thought, but it promises nothing
  13-19 asks a question, makes a claim, or opens a disagreement
  20-25 states something a viewer cannot leave unresolved, and is
        understandable on its own while doing so
```

The key addition is the last clause: a hook must be *both* self-explanatory
*and* unresolved. A fragment fails the first half.

Apply the same test to `peak` (20-22 on nine of ten) — check whether its top
band can be satisfied by any clip containing raised voices, which in this
content is all of them.

## Cause 2 — the calibration anchors bracket a range that does not exist

The weak example is a filler greeting scoring 19. The strong example is an
invented, perfectly-formed scene scoring 86. Real candidates score 75-86.

Every real mid-episode window looks excellent next to "Hey, good morning
everybody. So today we're gonna be doing the usual stuff around the kitchen."
The anchors are outside the candidate distribution, so they compress everything
against the top.

**Fix:** anchor inside the distribution, using **real windows from this
pipeline** rather than invented text.

1. Take three actual candidate windows from a past run — one you consider
   genuinely good, one clearly mediocre, one weak-but-plausible. Not extremes;
   the kind of thing the pool actually contains.
2. Score them yourself, by hand, using the bands.
3. Use those three as the calibration block, with your scores and a one-line
   justification per dimension.

Three anchors beat two, because the middle one is what the model needs and has
never been given. Keep them from a different episode than the one being
analyzed, keep the block byte-identical across candidates, and re-run the seed
comparison to confirm determinism survives.

If you would rather not hand-score three clips, a cheaper version: keep the
current strong anchor, and replace the weak one with a real candidate window
that scored 75 last run, labelled with what you think it actually deserves
(say, total 45). That one substitution tells the model the 75-86 cluster is
wrong.

## A smaller thing worth watching

Task 7 produced byte-identical output across seeds. Task 8 reports mean delta
1.4, worst 6. That is small, but it is not zero, and temperature is still 0.
The likely source is the longer prompt changing batching on the GPU. Not worth
chasing now — but if it grows after this change, it is a real signal rather
than noise.

## Tests

- No rubric band description can be satisfied by a mid-sentence fragment at both
  ends of the scale (assert the hook_strength text contains the "understandable
  on its own" clause).
- The calibration block is byte-identical across every per-candidate request.
- Seeds still produce matching clip sets.

## Live test

Same two-seed run. Pass: at least one dimension uses a value below 13 on at
least one clip, at most two clips share a total, and the clip sets still match
across seeds. Then look at the top three and decide whether you would publish
them — that is still the only question that matters.
