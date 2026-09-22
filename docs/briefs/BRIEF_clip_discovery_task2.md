# Task 2 — rubric scoring, per-clip titles, and selection diversity

`app/analyze.py`. This changes the final-pass prompt and schema once, and fixes
selection at the same time, rather than touching the same code twice.

## What the 1c live run proved

Source is 644.1s (10.7 min), 166 segments. Five clips came back:

```
  209.4 ->  294.0   84.6s  score 95
  321.0 ->  341.6   20.6s  score 80
  357.1 ->  438.3   81.2s  score 84
  440.0 ->  524.2   84.2s  score 90
  530.3 ->  611.7   81.4s  score 88
```

Three separate defects, none of them in the plumbing:

**1. The ranker is length-biased, independently of the candidate pool.**
Task 1b made the candidate pool 29% under 30s and only 11% over 75s. Yet four of
the five selected clips are 81–85s against a 90s ceiling. A longer window's
transcript simply contains more text, so it reads as richer to an 8B model. The
prompt's "judge the moment, not the duration" instruction is not surviving
contact with that signal.

**2. Selection collapses to a contiguous wall.** The five clips occupy 352s of
the 402s band they sit in — 87% coverage of one stretch — and are nearly
adjacent: 209→294, 321→341, 357→438, 440→524, 530→611. The first 33% of the
episode produced nothing. This is not six diverse moments; it is the back half of
the episode chopped into 85-second pieces. That pattern is the signature of a
ranker that is not really discriminating between moments.

**3. Twelve selections became five clips.** The final pass returned 12, and 7
were dropped by overlap filtering, leaving 5 against a requested 6. There is no
backfill, so overlap loss silently reduces the result count.

No titles yet — that part is expected, it was never implemented.

## Change 1 — score on a rubric, not a single number

Replace the single `score` with four sub-scores in
`window_ranking_json_schema`, each 0–25, plus the derived total:

```
hook_strength     does the first line make you stay?
self_contained    does it make sense with no prior context?
payoff            does it resolve, land a joke, or reveal something?
peak              is there a genuine emotional or comedic high point?
```

Forcing four independent judgments makes "this window has more words in it" a
much weaker attractor than a single holistic score does. Compute
`score = sum(sub_scores)` in Python, not in the model — do not ask it to add.

## Change 2 — titles and hooks

Add to each selection:

```python
"title": {"type": "string", "maxLength": 60},
"hook":  {"type": "string", "maxLength": 80},
```

`title` is what appears on the results card. `hook` is the one-line reason a
viewer would stay. Both must be grounded in that clip's own transcript text —
reuse the existing `is_generic_hook` and `grounded_title_from_text` guards to
reject and regenerate anything matching the generic-opener patterns. Show the
model two worked examples drawn from real transcript lines, not a template
sentence — the 1c experience showed it copies whatever phrasing the example uses.

## Change 3 — show duration and state the preference honestly

Each candidate in the prompt should carry its duration, with an explicit
instruction: a Short lives or dies in its first three seconds, so a tight 25s
moment beats a padded 85s one; choose a longer window only when the payoff
genuinely needs the runtime. Right now the model cannot see duration at all, so
it cannot weigh it even if told to.

## Change 4 — diversity quotas and backfill in selection

In `select_distinct_ranked_windows`, after the existing overlap filter:

- **Duration quota:** of the returned clips, at most half may exceed 70s. Skip a
  candidate that would breach the quota and take the next-best instead.
- **Region quota:** divide the source into `target_clip_count` equal regions by
  start time and allow at most two selections per region. This directly prevents
  the contiguous-wall failure and forces the unused first third to compete.
- **Backfill:** if overlap and quota filtering leave fewer than
  `target_clip_count`, fill from the remaining ranked candidates by score,
  relaxing the quotas first and the overlap rule last. Never return fewer than
  requested when non-overlapping candidates still exist.

Raise the final `selection_count` from `target*2` to `target*3` so there is a
real pool to backfill from. At 18 selections `ranking_num_predict` gives 2,416
tokens, which the 1c context sizing already handles.

## Tests

- Selections whose sub-scores sum highest win, and `score` is computed in Python.
- A response where every high-scoring candidate is over 70s still returns a set
  where at most half exceed 70s.
- Three candidates in the same region yield at most two selections.
- Overlap loss triggers backfill up to `target_clip_count`.
- A generic title ("In this episode…") is rejected.
- Titles and hooks survive the salvage path from 1c.

## Live test

Find Best Clips for 6 on `input/s17e9b farmers market feud.mp4`. Pass: six clips;
at most three over 70s; at least one from the first third of the episode; every
clip has a title that names something that actually happens in it.

## If this does not move quality

The contiguous-wall pattern suggests weak discrimination rather than a prompt
bug, which is the evidence that finally justifies the model bake-off. Hold the
candidate set fixed, score the same 20 candidates with `llama3.1:8b`,
`qwen2.5:7b-instruct` and `mistral-nemo:12b` at q4, and compare against your own
ranking of those 20. The final pass already costs 130s of the ~160s run, so a
slower-but-sharper model is affordable if it actually ranks better.
