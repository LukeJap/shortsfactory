# Task 8 — anchor the rubric so scores discriminate

`app/analyze.py`. Task 7 succeeded; this addresses what it exposed.

## Where task 7 landed

Two runs with different seeds produced byte-identical output — same ten clips,
same scores, same titles. Scoring took 26.4s and 25.5s for 26 candidates with
zero failures. Position bias is gone, and the model was never the problem.

The cost of isolating each judgment is visible in the scores:

```
  77  #
  75  #
  62  #
  60  #
  50  ######      <- six of ten, tied exactly at the midpoint
```

Six clips scored exactly 50 — four dimensions summing to the neutral middle.
Ranking among those six is decided by tie-break, not judgment. Compare the old
shared-context run, which spread 80 down to 0 but could not reproduce itself.

This is the expected trade. With no other candidate in context the model has
nothing to calibrate against, so it retreats to the midpoint whenever a clip is
not obviously remarkable. The fix is to give it a fixed reference frame — not
other candidates.

## Change 1 — behaviorally anchored scales

Right now each dimension is a question with a 0–25 range and no guidance on what
any number means:

```
- hook_strength: does the first line make a viewer stay?
```

Replace each with explicit band descriptors, so a score maps to an observable
property rather than a feeling. For example:

```
hook_strength (0-25): judge ONLY the first spoken line.
  0-5   the line is filler, a greeting, or mid-sentence continuation
  6-12  understandable but gives no reason to keep watching
  13-19 poses a question, states something odd, or starts an argument
  20-25 cannot be understood without watching on -- a real open loop
```

Do the same for `self_contained`, `payoff` and `peak`. Keep each band to one
line. Anchored scales are the standard fix for exactly this failure — raters,
human or otherwise, stop defaulting to the middle once the middle has a
definition they must assert.

Add one instruction: **do not return the same total for different clips unless
they are genuinely equivalent.** A midpoint score must be earned by matching the
midpoint descriptors, not chosen as a hedge.

## Change 2 — two fixed calibration examples

Include the same two worked examples in every per-candidate prompt: one clearly
strong, one clearly weak, each with its four scores and a one-line justification
per dimension. Draw them from a different episode than the one being analyzed so
they cannot bias toward specific content.

These are constant context. They give the model a reference frame without putting
any candidate in context with another, so determinism is preserved. Verify that
by rerunning the seed comparison — it must still produce identical output.

Prompt cost: roughly +400 chars, well inside the 2048 `num_ctx`.

## Change 3 — break ties with the first-stage score

The first-stage batches already make a comparative judgment: two of eight are
chosen per batch, and every shortlisted window carries the score that got it
there. That information is currently discarded once the final pass runs.

When two candidates tie on the rubric total, break the tie by first-stage score
before falling back to start time. Free, already computed, and principled — it is
the one comparative signal in the pipeline that isn't order-contaminated, since
each batch is scored independently.

## What good looks like

Six or more distinct totals across ten clips, with at most two sharing any single
value, and still byte-identical across seeds. If anchoring produces spread but
breaks determinism, the examples are leaking — check that they are identical for
every candidate.

## Tests

- Each rubric dimension has four band descriptors in the prompt.
- The calibration examples are byte-identical across every per-candidate request.
- Two candidates with equal rubric totals order by first-stage score.
- Seeds 1, 2 and 3 still yield identical scores with a stubbed model.

## Live test

The same two-seed run. Pass: identical output across seeds **and** at most two
clips sharing any score. Then read the top three titles and ask the only question
that matters — would you publish those three clips?

## A note on the titles

"Toast is ready" ranked second at 75. It is a stage direction, not a hook, and it
came through the titling pass rather than the fallback. The title guard rejects
generic phrases and quotes, but not a title that is simply weak. Worth a look
once scores discriminate — a weak clip ranked second is a scoring problem first
and a titling problem second.
