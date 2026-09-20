# Task 3 — show the titles, retire the reason template, make the trim coverage-aware

`app/analyze.py` and `app/gui_app/mixins/ai_clip_hunter.py`.

## Context

The 2a run produced good titles and they were never shown. From
`output/analysis.json`:

| # | title | hook |
|---|---|---|
| 1 | Booth rental agreement revealed | You both signed the booth rental agreement. |
| 2 | Narlene passes the knife | My pleasure, Plankton! |
| 3 | Knuckle sandwich threat | The only sandwich I'm dreaming of is a knuckle sandwich! |
| 4 | Holler hoagie feeding frenzy | I'd like one sandwich, please. |
| 5 | Peanut butter and jam sensation | You must try it! |
| 6 | Jim Dandy Jams promotion | These flavors would even knock our socks off! |

Both the CLI log and the GUI card display `reason` and raw transcript excerpts
instead. The feature works; the surface lies about it.

## Change 1 — the CLI clip line shows the title

`app/analyze.py` line ~2950:

```python
"Clip: {start} -> {end} | {duration:.1f}s | score {score} | {reason}".format(
    ...
    reason=candidate.get("reason", ""),
)
```

Print `title` as the primary text and `hook` after it. Something like:

```
Clip: 00:05:40.980 -> 00:06:15.280 | 34.3s | score 77 | Knuckle sandwich threat
       hook: The only sandwich I'm dreaming of is a knuckle sandwich!
```

Fall back to the existing local generators only when `title` is empty.

## Change 2 — the GUI card reads the title field

`app/gui_app/mixins/ai_clip_hunter.py` line ~209:

```python
title = hook or quote or "Transcript moment"
```

The card **never reads `candidate["title"]`**. That is the bug. Read it:

```python
title = (
    str(candidate.get("title", "") or "").strip()
    or hook
    or quote
    or "Transcript moment"
)
```

Then rebuild the card so the title leads, since the headline currently shows
`quote or title` followed by `grounded_reason`:

```
AI PICK #1   •   89/100
00:07:18 → 00:07:53  ·  35.0s
Booth rental agreement revealed
You both signed the booth rental agreement.
```

Title on its own line, hook beneath it, the raw transcript quote demoted to the
tooltip. Keep `_card_text_lines` wrapping and the existing 54-character title
clamp. Keep the generic-text guard on the title — if `is_generic_editor_text`
rejects it, fall through to `hook`, then `quote`, exactly as now.

Add duration to the card line — it is in the data and it is the thing you most
want to see when choosing between candidates.

## Change 3 — retire the reason template

`grounded_reason_from_text` (line ~2283) builds
`The candidate is anchored by the line "..."`. Now that `title` and `hook` are
real, `reason` carries nothing a user wants to read.

- Stop writing `reason` into candidate clips where `hook` is present.
- Remove `reason` from the CLI log line and the GUI card body; leave it in the
  tooltip only if it is non-generic.
- Keep the function itself — other call sites (~2405, ~2424) still use it for the
  non-clip-discovery analysis path. Do not delete it, just stop routing clip
  candidates through it.

## Change 4 — the top-24 trim must preserve coverage

`app/analyze.py` line ~1967:

```python
shortlist = sorted(shortlist, key=lambda candidate: -candidate[1])[
    :FINAL_POOL_MAX_CANDIDATES
]
```

Pure global score sort, no coverage guarantee. In the 2a run the six clips
started at 2:25, 4:03, 4:48, 5:40, 6:18 and 7:18 of a 10:44 episode — the last
2.8 minutes produced nothing, and that is where a sitcom payoff lives. The region
quota in `select_distinct_ranked_windows` cannot fix this, because by then the
late candidates are already gone.

Make the trim region-aware:

1. Divide the source into `target_clip_count` equal regions by window start.
2. Take the top `ceil(FINAL_POOL_MAX_CANDIDATES / region_count)` from each region
   by first-stage score.
3. Fill any remaining slots from the leftovers by global score.
4. Re-sort chronologically as now.

Every region with candidates then reaches the final pass. Log the per-region
counts so the next run shows whether the tail is being starved by the trim or
genuinely has nothing worth picking.

## Tests

- The CLI log line contains the title, not the reason template.
- A candidate with a `title` renders it on the card; one without falls back to
  hook, then quote.
- A generic title falls through to the hook.
- Clip candidates carry no `reason` when a hook exists.
- The trim returns candidates from every region that has them, and still honors
  `FINAL_POOL_MAX_CANDIDATES`.
- A source whose candidates all sit in one region still fills the pool.

## Live test

Find Best Clips for 6 on `input/s17e9b farmers market feud.mp4`. Pass: the log
and the cards both show real titles; no "anchored by the line" anywhere in the
UI; per-region trim counts are logged; and at least one clip comes from the final
third of the episode.

## After this

The hooks are currently quotes lifted from the clip — "My pleasure, Plankton!",
"You must try it!" — rather than reasons to keep watching. Compare the reference
product: "Why Does She Hate Chocolate? A Culinary Mystery!" is a curiosity gap,
not a line of dialogue. That is a titling-prompt change, worth doing once these
are visible and you can judge them at a glance. Do not bundle it here.
