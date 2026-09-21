# Task 10 — compute hook_strength in Python

`app/analyze.py`. Task 9 fixed two dimensions. The third needs a different kind
of fix.

## What task 9 achieved

```
  self_contained   4 distinct  [14, 18, 19, 20]
  payoff           5 distinct  [5, 6, 13, 15, 22]
  peak             6 distinct  [8, 15, 20, 21, 22, 23]
  hook_strength    1 distinct  [20]
```

The 0-14 range is in use — `payoff` reaches 5, `peak` reaches 8. Anchoring inside
the distribution worked, and the MID example did the job. Totals went from 5
distinct to 6, with the worst tie dropping from 6 clips to 4.

`hook_strength` did not move. It has now returned **exactly 20 on every clip,
across three runs and three different band wordings.**

## Why it will not respond to wording

The plumbing is correct. `first_spoken_line` extracts the opening utterance
properly and `build_single_window_prompt` puts it in a `FIRST LINE:` field. Here
is what the model was actually shown, and what it scored:

| first line | model |
|---|---|
| `Oh!` | 20 |
| `Ah.` | 20 |
| `Fuse!` | 20 |
| `Toast is ready!` | 20 |
| `I'm okay, ma.` | 20 |
| `Yeah, that checks out.` | 20 |
| `You want a refund?` | 20 |

`Oh!` and `Ah.` are the 0-5 band's own example text. The model is not reading the
field; it emits a constant. Three wordings have failed, and the value is always
the band floor — the signature of a default, not a judgment.

## The change

Score this dimension in Python. It is the one rubric dimension whose criteria are
mechanical — length, question form, filler openers — and the project already
follows this split: the model judges what needs judgment, Python does the
arithmetic.

```python
FILLER_OPENERS = {
    "oh","ah","uh","um","hey","hi","hello","yeah","yes","no","wow","huh",
    "well","so","okay","ok","hmm","aw","ugh","whoa","good morning",
    "good afternoon","good evening",
}

def hook_strength(first_line: str) -> int:
    """Deterministic 0-25 score for a clip's opening utterance."""
    line = (first_line or "").strip()
    if not line:
        return 0
    words = re.findall(r"[A-Za-z']+", line)
    n = len(words)
    lowered = " ".join(w.lower() for w in words)
    first = words[0].lower() if words else ""

    if n <= 2:                                   # "Oh!", "Fuse!"
        return 2
    if first in FILLER_OPENERS and n <= 5:       # "Yeah, that checks out."
        return 4
    if lowered in FILLER_OPENERS:
        return 2
    if line.rstrip().endswith("?") and n >= 4:   # a question is an open loop
        return 22 if n >= 6 else 20
    if n <= 5:
        return 8
    if n <= 10:
        return 14
    return 17
```

Remove `hook_strength` from the model's schema and from the rubric block, so it
scores three dimensions. Compute the fourth locally and add it to the total. The
`RUBRIC_FIELDS` tuple stays four-long so persistence and the cards are unchanged.

## Measured effect

Applying this to the same ten clips, holding the other three dimensions at what
the model actually returned:

```
  hook_strength:  1 distinct value -> 5 distinct  [2, 4, 8, 17, 20]
  totals:         6 distinct, worst tie 4x -> 9 distinct, worst tie 2x
```

That meets the task 8 pass criterion for the first time.

It also reorders the top. `09:17` falls from 85 to 67 because its first line is
`Oh!`, and `00:51` rises to the top on `Hey, honey partner, would you like a free
sample of one of...`. Look at those two and decide whether you agree — a Short
opening on "Oh!" is a real weakness, but this is the change's most visible
judgment call and it is yours, not mine.

## Tests

- Each first line in the table above gets its documented score.
- An empty or whitespace first line scores 0.
- The model schema has three fields; the persisted candidate still has four.
- Totals remain 0-100.
- Seeds still produce matching clip sets.

## Live test

The two-seed run. Pass: at most two clips share a total, at least one dimension
below 13, clip sets identical across seeds. Then the real question — read the top
three titles and decide whether you would publish them.

## After this

Clip discovery will have a deterministic, discriminating scorer. That is the end
of this thread. The remaining items from the original handoff are untouched and
larger: stale narration in the Recap editor, dropped narration words, source
caption remapping, SFX and emoji lane population, live music parity. Those are
the Recap blockers, and they have been waiting since 2026-09-17.
