# ShortsFactory — automation roadmap

Goal, as stated: drop in an episode, get a batch of finished Shorts with titles,
descriptions and pinned comments, uploaded to YouTube and scheduled six hours
apart, with no manual step in between.

Most of that is buildable on what already exists. Two things change the design,
and one part of the goal needs restating before anything is built on it.

---

## The part I can't build as described

> "set the strength that is proven to get past copyright and have ShortsFactory
> use that exact strength every time"

There is no such strength setting, and a feature built on the belief that there
is would be actively harmful to the channel.

Content ID matches an audio and video *fingerprint* of the source. It is
specifically engineered to survive the transformations a filter/speed/pitch dial
produces — that is the problem it was built to solve. Speeding a clip up, shifting
pitch, cropping to 9:16 and grading it does not remove the fingerprint. Any
setting that appeared to work would also be a moving target, since matching is
updated continuously; a value that passed last month tells you nothing about next
month.

There is also a difference in kind between *transformation* and *evasion*.
Deliberately tuning output to defeat detection is a Terms of Service problem, and
the downside is not a copyright strike on one video — it is the channel. That is
a bad trade for an asset you are trying to build.

What actually holds up is transformation that changes the work's purpose, which
is exactly what AI Recap already does: original narration carrying the story,
source footage used as evidence for a point the narrator is making, heavy
compression, authored structure. That direction is both the more defensible one
and the better product. It is worth more investment than the filter dial.

I am not a lawyer and this is not legal advice. If the channel is going to be a
real revenue source, an hour with a media lawyer is cheaper than losing it.

### What can be built instead, serving the same underlying want

1. **A locked editing profile.** One saved JSON — filter intensity, FX strength,
   AutoCut aggression, speed, pitch, caption style, music bed, emoji density —
   applied identically to every clip in every batch. Reproducible house style,
   which is a good product feature regardless of why you want it, and the thing
   you actually asked for minus the claim attached to it.
2. **An outcome log.** Every upload records its profile, and later its actual
   result: claimed / not claimed / monetization state. Over 50 uploads that is
   real data about what happens to your content, which beats a number someone
   asserted on a forum. Honest measurement, not a guarantee.

---

## Constraint 1 — the API quota caps you at ~6 uploads/day

`videos.insert` costs **1,600 quota units**; the default daily quota for a Google
Cloud project is **10,000 units**, resetting at midnight Pacific. That is 6
uploads per day maximum, and that is before any other API call.

A 6-hour cadence is 4 uploads/day. **It fits**, with ~3,600 units of headroom.
But 6 hours is close to the ceiling — 4-hour cadence (6/day) would consume the
entire quota with nothing spare, and anything faster is impossible without a
quota extension, which is requested through the Cloud Console and is not
guaranteed.

Design consequence: build the uploader to read its cadence from config, and to
refuse to schedule more than 5 uploads in a rolling 24h window rather than
failing mid-batch on a quota error.

## Constraint 2 — unverified projects can only upload private videos

This is the one that will ambush you if you find it late.

Videos uploaded via `videos.insert` from **API projects created after 28 July
2020 that have not passed a compliance audit are locked to private**. They cannot
be made public — not by `publishAt`, not by editing them afterwards in Studio.

So the sequence matters: apply for the API audit **before** building the
uploader, because approval takes time and everything downstream of it is blocked.
Until it clears, the pipeline can still upload privately, which is enough to
develop and test against.

## Scheduling is YouTube's job, not yours

Upload with `privacyStatus: "private"` plus `status.publishAt` set to an RFC3339
timestamp, and YouTube publishes it at that moment on its own.

This is better than it sounds for your setup: you do not need a scheduler running
every six hours, and your PC does not need to be awake when each video goes live.
One batch run uploads four videos with `publishAt` at +6h, +12h, +18h, +24h, and
the machine can then sleep. No drafts, no manual step in Studio.

OAuth scope: `https://www.googleapis.com/auth/youtube.upload` — the narrowest one
that works. The refresh token is stored locally; treat it like a password.

---

## Recap shape and publishing cadence (decided 2026-09-21)

**Three self-contained beat recaps per episode**, not a three-part serial. Each
covers a different beat of the episode with its own hook and its own payoff, and
works for a viewer who has seen neither of the others. The Shorts feed is
discovery-first, so arriving cold is the normal case, not the edge case — and
average % viewed is a ranking signal, which a deliberately unresolved ending
fights directly.

Connect them by identity, not dependency: same voice, same look, and a line in
the pinned comment pointing at the others. A soft tease at the end of a recap is
fine ("and that's not even the worst thing that happens to him") as long as
nothing is withheld that the viewer needed.

Checked against real data: all three thirds of the test episode yield usable
material (mean scores 71.0 / 66.3 / 64.0 across the task 10 run), so the choice
is about narrative structure and distribution, not about raw material.

### Upload once, publish apart

`videos.insert` and go-live are decoupled. One batch run uploads all three beats
as `privacyStatus: private` with `status.publishAt` timestamps; YouTube publishes
them on schedule with the machine off.

Never publish an episode's beats simultaneously — they compete for the same
audience segment in the initial algorithmic test.

### Cadence: 8 hours

| spacing | videos/day | episodes/day | quota/day |
|---|---|---|---|
| 6h | 4.0 | 1.33 | 6,400 |
| **8h** | **3.0** | **1.00** | **4,800** |
| 12h | 2.0 | 0.67 | 3,200 |

8h covers exactly 24 hours with three videos: one episode per day, one batch run,
48% of the 10,000-unit daily quota. The original 6h target assumed one recap per
episode; three beats changes the arithmetic.

### Queue rules

1. Publish an episode's beats in descending score order. The first slot gets the
   freshest test — spend it on the strongest beat. Self-contained recaps make
   narrative order irrelevant.
2. Once 2-3 episodes are buffered, interleave: never publish two beats from the
   same source within 24h. Cheap to design in now, awkward to retrofit.
3. `recap_mode` lives on the editing profile — `whole_episode | beats | thirds` —
   so the serial format stays testable rather than being argued about. Two weeks
   of each, compared on average % viewed and channel-page visits, settles it with
   data about this audience rather than anyone's intuition.

## Build order

Each phase is useful on its own and testable before the next starts.

### Phase A — batch pipeline, no upload

One episode in, N finished MP4s out, plus a metadata JSON per clip. This is
mostly orchestration over parts that already work: clip discovery already returns
10 scored, titled candidates; the editor pipeline already renders.

New: an `EditingProfile` dataclass persisted to JSON, a batch runner that loops
clips through render with that profile, and an output folder per episode. No GUI
work — a CLI entry point is the right surface, and it makes the whole thing
scriptable.

The one real design question: today every automated decision becomes editable
editor state, and a human approves it. Batch mode has no human. Keep writing that
state to disk — it is what lets you open any clip afterwards and see what was
decided — but stop requiring anyone to look at it.

### Phase B — metadata generation

Title already exists. Add description and pinned comment as one more per-clip
model call, sharing the titling pass's guards (no generic phrases, grounded in
the clip). A pinned comment is a good place for the "full episode" framing and a
question that drives replies.

Cheap: three short fields, one call per clip, a few seconds each.

### Phase C — uploader

OAuth flow, `videos.insert` with `publishAt`, retry on transient failure, and an
upload log. Gate every run on the rolling-24h quota check. Do the API audit first.

### Phase D — the loop

A scheduled task that runs the whole chain on any new file dropped in a watch
folder. Everything above is already headless by then, so this is the small part.

---

## What this does not change

The Recap blockers from the original handoff are still open and still the larger
body of work: stale narration in the editor, dropped narration words, source
caption remapping, SFX and emoji lane population, live music parity. Automating a
pipeline whose Recap mode still plays stale audio would just produce broken
videos faster.

Clip discovery is nearly done. I would finish task 10, then go back to Recap
correctness, then build Phase A on a pipeline that is actually trustworthy.
