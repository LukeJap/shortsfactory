# Task 7 — score one candidate per call

`app/analyze.py`. Do this before the model bake-off; it is cheaper and it tests a
better hypothesis.

## What the seed test showed

The same clip, the same transcript, the same model — only the prompt order
differed:

```
  00:00:51  seed1= 42  seed2= 72   swing 30
  00:02:20  seed1= 34  seed2=  0   swing 34
  00:02:46  seed1=  0  seed2= 25   swing 25
  00:05:20  seed1= 22  seed2=  0   swing 22
  00:05:57  seed1= 14  seed2= 41   swing 27
  00:08:00  seed1=  0  seed2= 57   swing 57
  00:08:50  seed1=  7  seed2= 84   swing 77
```

Mean swing 38.9 of 100. Only 7 of 10 clips appear in both runs, so the selection
itself is order-dependent. Spearman versus episode position is -0.62 in one run
and +0.37 in the other, so the monotonic ramp really is gone — task 6 fixed that.
What is underneath it is not judgment. It is noise.

## The hypothesis worth testing first

The final pass asks for **26 judgments inside one generated response**. Every
selection after the first is produced with all the previous ones already in
context, so each score is conditioned on the scores already written. That is
positional by construction. Shuffling changes which candidate lands in the
position that gets scored 84 and which lands in the one that gets scored 7 — it
does not remove the effect, it just randomizes who suffers it.

This is a property of the request shape, not of the model. A larger model in the
same shape would be more expensive and still conditioned on its own output.

## Change — one candidate per call

Replace the single 26-selection request with 26 independent requests, each
scoring exactly one window.

- Prompt: the rubric instructions plus that one window's transcript. ~1,300
  chars, ~370 tokens.
- Schema: the four rubric fields for a single object. No `window_id` needed —
  the caller knows which window it sent.
- Output: ~30 tokens.
- `num_ctx`: ~900 needed. Set 2048 and stop worrying about the 8192 ceiling; the
  whole pool-shrinking guard becomes unnecessary for this pass (keep it for the
  first-stage batches).

Cost, using the measured rates: 26 calls at 1.2–2.5s each is 31–65s, against
38.9s and 55.1s for the current single call. **No slower, and every judgment is
made with no other candidate in context, so position cannot influence it at
all.**

Keep `keep_alive` so the model stays resident across the 26 calls — without it
this gets much slower.

If a single call fails or returns no sub-scores, score that window 0 and log it,
rather than failing the run. One bad call should cost one candidate.

## How to know if it worked

Run the same seed comparison:

```powershell
$env:SHORTS_RANK_SEED=1
.\.venv\Scripts\python.exe app\analyze.py --clip-discovery-only --max-clips 10 `
  --video "input\s17e9b farmers market feud.mp4" `
  --transcript "output\transcript_cache\6fe326573024492b9f0fe287.json"
copy output\analysis.json output\analysis_seed1.json
# repeat with seed 2, then:
.\.venv\Scripts\python.exe tools\compare_rank_seeds.py output\analysis_seed1.json output\analysis_seed2.json
```

With per-candidate scoring the seed should become **irrelevant** — it only
controls presentation order, and there is no longer any shared context for order
to act through. Expect mean delta near 0 and 10 of 10 shared clips. Anything
above ~10 means something else is non-deterministic (check that temperature is
still 0).

This is a much stronger pass condition than last time: not "stable enough" but
"identical".

## Tests

- The scoring pass issues one request per candidate.
- A failed single request scores that candidate 0 and does not abort the run.
- Two runs with different seeds produce identical scores for the same window,
  given a stubbed model.
- `num_ctx` for this pass is small and independent of pool size.

## If it does not work

Then the model genuinely cannot apply the rubric, and the bake-off is next. Run
it properly:

1. Freeze the 26-window pool from one run to a file so every model scores the
   identical set.
2. Score that set three times per model with different seeds.
3. **First metric is self-consistency** — mean swing for the same window across
   the three runs. It needs no human labeling, and a model that cannot reproduce
   its own score is unusable whatever its taste. Anything above ~10 fails here.
4. Only for models that pass, compare their top 10 against your own ranking of
   the 26.
5. Candidates: `llama3.1:8b` (baseline), `qwen2.5:7b-instruct`, `gemma2:9b`, and
   `mistral-nemo:12b` at q4 — the last is tight on 8GB, so watch for the
   generation-rate collapse that flagged the KV-cache spill in task 2a.

Self-consistency first, taste second. It is the cheap filter and this project has
now spent four iterations on a scorer that never passed it.
