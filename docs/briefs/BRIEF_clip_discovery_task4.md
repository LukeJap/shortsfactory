# Task 4 — hooks that are hooks, more clips, and length variety

`app/analyze.py`. Three changes, budgeted against the 8192-token ceiling up front
so this does not repeat the task 2 timeout.

## Where task 3 landed

```
region 0 [    0- 107s] EMPTY
region 1 [  107- 215s] EMPTY
region 2 [  215- 322s] Toast is ready
region 3 [  322- 429s] Plankton serves sandwich / Knuckle sandwich solution
region 4 [  429- 537s] Mule feed costs exceed profits / Hip squeak steals profits
region 5 [  537- 644s] Sea nut butter recipe revealed
```

Titles display, the reason template is gone, and the trim log
(`4/7, 4/5, 4/7, 4/10, 4/11, 4/6`) shows every region reaching the final pass.
Regions 0–1 are now empty because the **ranker scored them low**, not because the
trim starved them. That is a real judgment we can leave alone for now.

Two new problems and one deferred one.

## The 8192 budget

Measured generation rates: ~7 tok/s at `num_ctx=16384`, ~35–50 tok/s at 8192. The
16k context spills the KV cache past the 2070's 8GB and forces CPU offload.

**Treat 8192 as a hard ceiling, not a parameter.** Every change below is sized to
stay inside `prompt_tokens + num_predict + 512 <= 8192`. Add a guard that
estimates this before each request, logs the estimate, and shrinks the candidate
pool rather than stepping `num_ctx` to 16384.

## Change 1 — hooks must stop being quotes

Every hook in the last run is a line lifted verbatim from the clip:

```
Mule feed costs exceed profits   <- "That won't cover the feed we gave the mule..."
Hip squeak steals profits        <- "You let go my profits, hip squeak!"
Toast is ready                   <- "Hey, I was gonna use that! Why you!"
```

The reference product writes "Why Does She Hate Chocolate? A Culinary Mystery!" —
a curiosity gap that makes you click. A quote is not that.

In the titling prompt:

- `title`: 4–9 words, a claim or a question, no trailing period. It should state
  what is at stake or what is strange, not label the scene. "Toast is ready" is a
  label; "Their secret recipe was never a secret" is a title.
- `hook`: one sentence, max 80 chars, describing why a viewer should stay.
  **Explicitly forbid quoting the clip.**
- Give two worked examples built from this episode's own transcript, written as
  finished titles — not as descriptions of what a title should be. Every previous
  iteration copied the example's framing verbatim ("Concrete transcript detail
  that makes this window work", "The candidate is anchored by the line"), so the
  example *is* the specification.

Add a mechanical guard alongside the existing generic-phrase check: reject a hook
whose normalized text appears as a substring of the clip's transcript. That is a
quote by definition. On rejection, retry once within the same call budget, then
fall back to the local generator.

## Change 2 — return 10 clips, not 6

The reference returns 16 from one episode. Six is the current ceiling for no
reason other than the default.

Raise the default `target_clip_count` to 10, and size the pool for it:

```
FINAL_POOL_MAX_CANDIDATES = 26
```

Budget check at 26 candidates (~695 prompt chars each, measured from the 24-
candidate run at 16,661 chars):

```
prompt   ~18,070 chars  ~5,163 tokens
output   26 x 60        ~1,816 tokens  (incl. 256 base)
margin                     512 tokens
total                    ~7,491  <= 8192   OK
```

Region count follows `target_clip_count`, so 10 regions of ~64s each on this
episode — finer coverage, which is what you want when returning more clips.

Expect fewer than 10 sometimes: overlap filtering is never relaxed (correctly),
so a dense episode may yield 8. Log the shortfall rather than padding.

## Change 3 — length variety

Durations across the last three runs: ~85s throughout, then ~35s throughout, now
19.8–34.9s with nothing in the 40–90s band. The ranker keeps collapsing onto one
length, and the candidate targets (20/35/55/80) are not reaching the output.

The reference mixes 0:14 through 1:37 in a single set. Add a band quota to
`select_distinct_ranked_windows` alongside the existing over-70s cap:

- at least 2 selections of 45s or longer, when candidates exist in that band;
- at most half under 30s.

Apply it the same way as the region quota — skip and take the next best, then
relax during backfill.

## Tests

- A hook that is a substring of its clip transcript is rejected.
- A title ending in a period, or under 4 words, is rejected.
- The band quota returns at least 2 clips >= 45s when the pool allows.
- The pool guard shrinks the candidate count when the token estimate exceeds the
  ceiling, and never raises `num_ctx` above 8192.
- `target_clip_count = 10` produces 10 regions.

## Live test

Find Best Clips on `input/s17e9b farmers market feud.mp4`, default count. Pass:

- 8–10 clips;
- at least 2 of them 45s or longer;
- no hook appears verbatim in its own clip's transcript;
- every `num_ctx` in the log is 8192;
- total run under ~3 minutes.

## Still deferred

The model bake-off. Regions 0–1 scoring low may be correct — a farmers-market
episode's first 3.5 minutes are probably setup. But if titles still read flat
after this change, that is `llama3.1:8b` reaching its ceiling on editorial
judgment, and the 35–50 tok/s headroom now makes a larger model affordable.
