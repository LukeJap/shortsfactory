"""
B4-B13 (SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR_CREATIVE_REVISED.md)
-- exact recap sequence assembly. Turns each recap_script.json segment's
candidate ranges (recap_media.loader.load_recap_script()) into an
ordered list of exact source shots and writes
output/recap/recap_sequence.json.

recap_sequence.json's schema belongs entirely to Track B (the shared
contract never defines it, and Track A never reads it back) -- documented
inline below rather than in the shared contract doc.

Revision note: this supersedes the original (simpler) B3 spec's
lexicographic "unused, then highest score, then earliest start" shot
selection. The creative-quality revision asks for genuine editorial
judgment -- visual-function diversity, non-contiguous evidence used
naturally, avoiding giant-range/single-candidate bias, and NOT padding a
segment to an arbitrary target duration once nothing left is worth
adding. See select_shots()/infer_visual_function() below for how that's
approximated without a real Track A (whose frozen recap_script.json
schema has no visual-function field yet -- inferred from each
candidate's own "reason" text as an honest, documented stand-in).

Shot-selection policy, in order of priority:
1. Never invent time outside a candidate's own verified (start, end) --
   a shot's duration is clamped to that span before anything else.
2. Score each remaining candidate: semantic relevance (Track A's own
   "score"), plus a diversity bonus for a visual function not yet used
   in this segment, minus a reuse penalty (this exact range already
   used elsewhere in the sequence) and a same-function-as-the-previous-
   shot penalty (discourages two near-duplicate-feeling shots back to
   back). Highest score wins; earliest start breaks ties.
3. Every segment with any candidates gets at least one shot regardless
   of score. Beyond the first, a candidate must clear
   MIN_USEFUL_SELECTION_SCORE or selection stops -- a segment may
   legitimately end up shorter than its own target_duration rather than
   padding with a low-value reuse.
4. Each shot's duration targets a cadence band chosen from *that
   specific candidate's* inferred visual function first (reaction/
   detail/payoff get their own bands), falling back to the segment's
   own importance only when the function gives no stronger signal --
   never at the expense of rule 1.
5. The selected set is then reordered toward a cause-then-reaction-then-
   consequence-like progression (by inferred visual function) rather
   than left in raw selection order -- a no-op today whenever every
   candidate infers to the same default, since the sort is stable.

"Chronological source progression" / "no nonsensical jumps" are not
enforced by refusing a candidate outright (Track A's own scoring already
reflects source-evidence quality, which this shouldn't second-guess) --
instead a large backward jump between consecutive segments' shots is
surfaced as an inspectable warning (segment-level and in the top-level
sequence_warnings list), the same "AI proposes, human/inspector reviews"
principle the rest of ShortsFactory already follows.

Not attempted in this pass (left for a follow-up): the motion/FX
vocabulary and narrative intensity hierarchy (hook/escalation/payoff/
exit), black-frame/invalid-frame validation via real frame sampling, and
shot-level GUI editing (replace/trim/reorder/lock one shot) -- those are
each a separately-scoped, substantial piece of the creative revision.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from pipeline_paths import RECAP_SEQUENCE_PATH
from recap_media.loader import RecapInputError

SEQUENCE_SCHEMA_VERSION = 1

# Per-shot cadence bands, seconds (low, high) -- see
# SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR_CREATIVE_REVISED.md's B5.
# Keyed by inferred visual function rather than one band per segment.
CADENCE_DETAIL = (0.7, 1.4)
CADENCE_REACTION = (0.6, 1.5)
CADENCE_ILLUSTRATIVE = (1.2, 2.5)
CADENCE_IMPORTANT = (2.0, 3.5)
CADENCE_ORIGINAL_DIALOGUE = (1.5, 4.5)
CADENCE_PAYOFF = (2.0, 4.5)

# A segment's own "importance" (0-1, from recap_script.json) at or above
# this uses the wider "important"/"payoff" bands instead of the default
# illustrative one, when a candidate's own inferred function doesn't
# already imply a band.
IMPORTANT_THRESHOLD = 0.75
PAYOFF_THRESHOLD = 0.9

DEFAULT_VISUAL_FUNCTION = "illustrative"

# Crude, honest stand-in for a real Track A "visual_function" tag (not
# part of the frozen recap_script.json schema) -- keyword-matched
# against a candidate's own "reason" text. An explicit
# candidate["visual_function"], if a future Track A ever provides one,
# always wins over this inference.
VISUAL_FUNCTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "reaction": ("react", "reaction", "shock", "surpris", "gasp", "stare", "stunned", "horrified"),
    "payoff": ("payoff", "reveal", "climax", "twist", "resolution", "finally"),
    "detail": ("detail", "object", "close-up", "closeup", "prop"),
    "consequence": ("result", "consequence", "aftermath", "ruin", "wreck", "damage"),
    "context": ("location", "establish", "context", "setting", "wide shot", "background"),
    "escalation": ("escalat", "worse", "intensif", "spiral"),
}

# Cause -> reaction -> consequence -like ordering (B9) applied to an
# already-selected shot list. Unranked/tied functions keep their
# selection order (Python's sort is stable).
VISUAL_FUNCTION_PROGRESSION_ORDER = [
    "context",
    "before",
    "action",
    DEFAULT_VISUAL_FUNCTION,
    "escalation",
    "reaction",
    "consequence",
    "payoff",
    "detail",
]

# Diversity/reuse/redundancy scoring (B28/B29) -- see _selection_score().
DIVERSITY_BONUS = 0.15
REUSE_PENALTY = 0.5
ADJACENT_SAME_FUNCTION_PENALTY = 0.2

# Below this score, adding another shot isn't worth it -- stop selection
# short of target_duration rather than pad with a low-value/reused shot
# (B27). Never applied to a segment's first shot (every segment with any
# candidates gets at least one, regardless of score).
MIN_USEFUL_SELECTION_SCORE = 0.35

# A shot shorter than this doesn't communicate anything on its own --
# stop selection rather than add a sliver.
MIN_SHOT_DURATION_SECONDS = 0.4

# Speaking-rate fallback (~150 wpm) for estimating a segment's target
# duration before Orpheus has actually measured one (voiceover.py's
# SegmentSynthesisResult.duration_seconds) -- lets sequence assembly be
# exercised/tested without waiting on B2 having run yet, same spirit as
# B1's fixture-driven loading not waiting on Track A.
WORDS_PER_SECOND_ESTIMATE = 2.5

# How far back in source time a segment's first shot can start relative
# to the previous segment's last shot before it's flagged as worth a
# human look, rather than assumed to be an intentional editorial choice.
NONSEQUENTIAL_JUMP_TOLERANCE_SECONDS = 5.0

MAX_SHOTS_PER_SEGMENT = 20


def infer_visual_function(candidate: dict[str, Any]) -> str:
    """See the module docstring's note on visual-function inference."""

    explicit = candidate.get("visual_function")
    if explicit:
        return str(explicit).strip().lower()

    reason = str(candidate.get("reason", "")).lower()
    for function, keywords in VISUAL_FUNCTION_KEYWORDS.items():
        if any(keyword in reason for keyword in keywords):
            return function

    return DEFAULT_VISUAL_FUNCTION


def cadence_for_candidate(
    visual_function: str,
    segment_importance: float,
    use_dialogue_band: bool = False,
) -> tuple[float, float]:

    if use_dialogue_band:
        return CADENCE_ORIGINAL_DIALOGUE

    if visual_function == "reaction":
        return CADENCE_REACTION
    if visual_function == "detail":
        return CADENCE_DETAIL
    if visual_function == "payoff" or segment_importance >= PAYOFF_THRESHOLD:
        return CADENCE_PAYOFF
    if segment_importance >= IMPORTANT_THRESHOLD:
        return CADENCE_IMPORTANT

    return CADENCE_ILLUSTRATIVE


def cadence_for_shot(
    segment: dict[str, Any],
    candidate: dict[str, Any] | None,
    use_dialogue_band: bool = False,
) -> tuple[float, float]:
    """
    Per-shot cadence: the segment's presentation_hint can force a band
    outright (reaction_beat -> always CADENCE_REACTION, dialogue
    treatment -> always CADENCE_ORIGINAL_DIALOGUE); otherwise this
    candidate's own inferred visual function decides, falling back to
    the segment's importance.
    """

    if use_dialogue_band:
        return CADENCE_ORIGINAL_DIALOGUE

    if segment.get("presentation_hint") == "reaction_beat":
        return CADENCE_REACTION

    importance = float(segment.get("importance", 0.0))
    visual_function = infer_visual_function(candidate) if candidate else DEFAULT_VISUAL_FUNCTION
    return cadence_for_candidate(visual_function, importance)


def estimate_narration_seconds(text: str) -> float:

    word_count = len(text.split())
    return max(0.1, word_count / WORDS_PER_SECOND_ESTIMATE)


def target_duration_for_segment(
    segment: dict[str, Any],
    narration_durations: dict[str, float],
) -> tuple[float, str]:
    """
    Returns (target_duration_seconds, source) where source is one of
    "measured" (a real Orpheus WAV duration was supplied), "estimated"
    (word-count fallback), or "visual_only_default" (no narration to
    measure or estimate at all -- a single cadence-band-width shot).
    """

    segment_id = segment["segment_id"]

    if segment.get("presentation_hint") == "visual_only":
        importance = float(segment.get("importance", 0.0))
        low, high = cadence_for_candidate(DEFAULT_VISUAL_FUNCTION, importance)
        return (low + high) / 2.0, "visual_only_default"

    if segment_id in narration_durations:
        return float(narration_durations[segment_id]), "measured"

    return estimate_narration_seconds(segment["text"]), "estimated"


def _range_key(candidate: dict[str, Any]) -> tuple[float, float]:
    return (round(float(candidate["start"]), 2), round(float(candidate["end"]), 2))


def _selection_score(
    candidate: dict[str, Any],
    visual_function: str,
    used_ranges: set[tuple[float, float]],
    functions_used: set[str],
    last_function: str | None,
) -> float:
    """
    B28's "internal score": Track A's own semantic relevance, plus a
    diversity bonus (this visual function hasn't been used yet in this
    segment), minus a reuse penalty (this exact range already appears
    elsewhere in the sequence) and a same-function-as-immediately-
    before penalty (B29 -- avoid two near-duplicate-feeling shots back
    to back).
    """

    score = float(candidate["score"])

    if visual_function not in functions_used:
        score += DIVERSITY_BONUS

    if _range_key(candidate) in used_ranges:
        score -= REUSE_PENALTY

    if last_function is not None and visual_function == last_function:
        score -= ADJACENT_SAME_FUNCTION_PENALTY

    return score


def _reorder_for_progression(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:

    def rank(shot: dict[str, Any]) -> int:
        function = shot.get("visual_function", DEFAULT_VISUAL_FUNCTION)
        try:
            return VISUAL_FUNCTION_PROGRESSION_ORDER.index(function)
        except ValueError:
            return VISUAL_FUNCTION_PROGRESSION_ORDER.index(DEFAULT_VISUAL_FUNCTION)

    return sorted(shots, key=rank)


def select_shots(
    candidates: list[dict[str, Any]],
    segment: dict[str, Any],
    target_duration: float,
    used_ranges: set[tuple[float, float]],
    source_list_name: str,
    use_dialogue_band: bool = False,
) -> list[dict[str, Any]]:

    if not candidates:
        return []

    shots: list[dict[str, Any]] = []
    remaining = target_duration
    functions_used: set[str] = set()
    last_function: str | None = None

    while (not shots or remaining > MIN_SHOT_DURATION_SECONDS) and len(shots) < MAX_SHOTS_PER_SEGMENT:

        scored = [
            (
                _selection_score(
                    candidate,
                    infer_visual_function(candidate),
                    used_ranges,
                    functions_used,
                    last_function,
                ),
                candidate,
            )
            for candidate in candidates
        ]
        best_score, best_candidate = max(
            scored,
            key=lambda item: (item[0], -float(item[1]["start"])),
        )

        # Every segment gets at least one shot regardless of score; only
        # a *second-or-later* shot can be skipped for not being worth it.
        if shots and best_score < MIN_USEFUL_SELECTION_SCORE:
            break

        visual_function = infer_visual_function(best_candidate)
        cadence_low, cadence_high = cadence_for_shot(segment, best_candidate, use_dialogue_band)

        span = float(best_candidate["end"]) - float(best_candidate["start"])
        shot_duration = min(span, cadence_high, max(cadence_low, remaining))
        shot_start = float(best_candidate["start"])
        shot_end = round(shot_start + shot_duration, 3)
        reused = _range_key(best_candidate) in used_ranges

        shots.append(
            {
                "start": round(shot_start, 3),
                "end": shot_end,
                "duration": round(shot_duration, 3),
                "source_list": source_list_name,
                "score": best_candidate["score"],
                "selection_score": round(best_score, 3),
                "reason": best_candidate.get("reason", ""),
                "visual_function": visual_function,
                "reused": reused,
            }
        )

        used_ranges.add(_range_key(best_candidate))
        functions_used.add(visual_function)
        last_function = visual_function
        remaining -= shot_duration

    return _reorder_for_progression(shots)


def assemble_sequence(
    recap_script: dict[str, Any],
    narration_durations: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Build the full recap_sequence.json structure from an already-loaded
    and validated recap_script.json (see recap_media.loader.
    load_recap_script()) and an optional {segment_id: duration_seconds}
    map of real Orpheus measurements (recap_media.voiceover.
    synthesize_segments() results). Segments missing from that map fall
    back to a word-count duration estimate (or a fixed default for
    visual_only segments, which never have narration).
    """

    narration_durations = narration_durations or {}
    used_ranges: set[tuple[float, float]] = set()
    segments_out: list[dict[str, Any]] = []
    sequence_warnings: list[str] = []
    previous_last_shot_end: float | None = None

    for segment in sorted(recap_script["segments"], key=lambda s: s["order"]):

        hint = segment["presentation_hint"]
        use_dialogue_band = hint == "original_dialogue"
        target_duration, duration_source = target_duration_for_segment(segment, narration_durations)

        primary_list = (
            "original_dialogue_candidates" if use_dialogue_band else "candidate_visuals"
        )
        fallback_list = (
            "candidate_visuals" if use_dialogue_band else "original_dialogue_candidates"
        )

        candidates = segment.get(primary_list) or []
        source_list_name = primary_list
        if not candidates:
            # Nothing in this segment's preferred list -- better to show
            # something from the other list than nothing at all.
            candidates = segment.get(fallback_list) or []
            source_list_name = fallback_list

        shots = select_shots(
            candidates,
            segment,
            target_duration,
            used_ranges,
            source_list_name,
            use_dialogue_band=use_dialogue_band,
        )

        # Per-shot, not just per-segment -- a narration_over_source
        # segment that fell back to original_dialogue_candidates (empty
        # candidate_visuals) actually plays as dialogue, not narration
        # over illustrative footage. interweave_original_dialogue()
        # below relies on this being accurate, since it selectively
        # overrides individual shots' treatment without changing the
        # segment's own presentation_hint.
        shot_treatment = (
            "original_dialogue" if source_list_name == "original_dialogue_candidates" else hint
        )
        for shot in shots:
            shot["treatment"] = shot_treatment

        warnings: list[str] = []
        if not shots:
            warnings.append("No usable source candidates -- segment has no assigned shots.")
        elif previous_last_shot_end is not None:
            first_start = shots[0]["start"]
            if first_start < previous_last_shot_end - NONSEQUENTIAL_JUMP_TOLERANCE_SECONDS:
                warnings.append(
                    f"Source time jumps backward from {previous_last_shot_end:.1f}s "
                    f"to {first_start:.1f}s -- review for a nonsensical cut."
                )

        if shots:
            previous_last_shot_end = shots[-1]["end"]

        shots_total = round(sum(shot["duration"] for shot in shots), 3)

        segments_out.append(
            {
                "segment_id": segment["segment_id"],
                "order": segment["order"],
                "presentation_hint": hint,
                "beat_ids": segment["beat_ids"],
                "narration_duration_seconds": round(target_duration, 3),
                "narration_duration_source": duration_source,
                "shots": shots,
                "shots_total_duration_seconds": shots_total,
                "has_dialogue_insert": False,
                "warnings": warnings,
            }
        )

        sequence_warnings.extend(
            f"{segment['segment_id']}: {warning}" for warning in warnings
        )

    total_duration = round(
        sum(segment["shots_total_duration_seconds"] for segment in segments_out), 3
    )

    return {
        "schema_version": SEQUENCE_SCHEMA_VERSION,
        "target_duration_seconds": recap_script.get("target_duration_seconds"),
        "total_duration_seconds": total_duration,
        "segments": segments_out,
        "sequence_warnings": sequence_warnings,
    }


# ============================================================
# B4 -- original-dialogue interweaving
# ============================================================
#
# "Allow the narrator to stop when the source scene is better"
# (VOICEOVER -> ORIGINAL DIALOGUE -> VOICEOVER RESUMES). A whole segment
# already using presentation_hint "original_dialogue" is handled above by
# assemble_sequence() itself -- this is the selective, PARTIAL case: a
# narration_over_source segment that also happens to carry a strong
# original_dialogue_candidates option can have the narrator briefly yield
# to it mid-segment. "Do not force dialogue inserts into every section"
# is enforced by a score threshold, not a fixed rate -- most segments
# should get no insert at all.

DIALOGUE_INSERT_SCORE_THRESHOLD = 0.85

# An insert is capped at this fraction of its segment's own total
# duration, so the narrator genuinely resumes afterward rather than the
# segment becoming effectively all-dialogue (that's what a segment with
# its own presentation_hint of "original_dialogue" is already for).
DIALOGUE_INSERT_MAX_FRACTION = 0.4


def _collect_used_ranges(sequence: dict[str, Any]) -> set[tuple[float, float]]:

    used: set[tuple[float, float]] = set()
    for segment in sequence["segments"]:
        for shot in segment["shots"]:
            used.add((round(shot["start"], 2), round(shot["end"], 2)))
    return used


def _insert_dialogue_shot(
    shots: list[dict[str, Any]],
    insert_shot: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Splice a dialogue-treatment shot into roughly the middle of an
    existing shot list, dropping the one illustrative shot it displaces
    -- keeps the segment's total duration close to what it was rather
    than strictly adding on top of it. Requires at least 2 shots going
    in, so at least one illustrative shot survives on each side (the
    caller enforces this) -- otherwise "VOICEOVER RESUMES" wouldn't mean
    anything.
    """

    insertion_index = len(shots) // 2
    remaining = shots[:insertion_index] + shots[insertion_index + 1:]
    remaining.insert(insertion_index, insert_shot)
    return remaining


def interweave_original_dialogue(
    sequence: dict[str, Any],
    recap_script: dict[str, Any],
    score_threshold: float = DIALOGUE_INSERT_SCORE_THRESHOLD,
    max_fraction_of_segment: float = DIALOGUE_INSERT_MAX_FRACTION,
) -> dict[str, Any]:
    """
    Second pass over an already-assembled sequence (assemble_sequence()).
    Does not mutate its inputs -- returns a new sequence dict.

    Eligible segments: presentation_hint "narration_over_source", at
    least 2 assigned shots (so one can be displaced without emptying the
    segment), and a real original_dialogue_candidates list on the source
    recap_script segment whose best score clears score_threshold. Every
    other segment (including ones already presentation_hint
    "original_dialogue" -- assemble_sequence() already gave those full
    dialogue treatment) passes through unchanged.

    The inserted shot's own duration is still clamped to the original-
    dialogue cadence band and to max_fraction_of_segment of the
    segment's total duration -- never blindly the candidate's full span.
    """

    sequence = copy.deepcopy(sequence)
    script_segments_by_id = {
        segment["segment_id"]: segment for segment in recap_script["segments"]
    }
    used_ranges = _collect_used_ranges(sequence)

    for seq_segment in sequence["segments"]:

        if seq_segment["presentation_hint"] != "narration_over_source":
            continue
        if len(seq_segment["shots"]) < 2:
            continue

        script_segment = script_segments_by_id.get(seq_segment["segment_id"])
        if script_segment is None:
            continue

        dialogue_candidates = script_segment.get("original_dialogue_candidates") or []
        if not dialogue_candidates:
            continue

        best = max(dialogue_candidates, key=lambda candidate: candidate["score"])
        if best["score"] < score_threshold:
            continue

        total_duration = seq_segment["shots_total_duration_seconds"]
        max_insert_duration = total_duration * max_fraction_of_segment
        if max_insert_duration < CADENCE_ORIGINAL_DIALOGUE[0]:
            # Segment too short to fit even the minimum dialogue cadence
            # without eating the whole thing -- skip rather than force it.
            continue

        span = float(best["end"]) - float(best["start"])
        insert_duration = min(
            span,
            CADENCE_ORIGINAL_DIALOGUE[1],
            max(CADENCE_ORIGINAL_DIALOGUE[0], max_insert_duration),
        )
        insert_duration = min(insert_duration, max_insert_duration)
        if insert_duration <= 0:
            continue

        insert_shot = {
            "start": round(float(best["start"]), 3),
            "end": round(float(best["start"]) + insert_duration, 3),
            "duration": round(insert_duration, 3),
            "source_list": "original_dialogue_candidates",
            "score": best["score"],
            "selection_score": best["score"],
            "reason": best.get("reason", ""),
            "visual_function": "original_dialogue",
            "reused": _range_key(best) in used_ranges,
            "treatment": "original_dialogue",
        }

        seq_segment["shots"] = _insert_dialogue_shot(seq_segment["shots"], insert_shot)
        seq_segment["shots_total_duration_seconds"] = round(
            sum(shot["duration"] for shot in seq_segment["shots"]), 3
        )
        seq_segment["has_dialogue_insert"] = True

        used_ranges.add(_range_key(best))

    sequence["total_duration_seconds"] = round(
        sum(segment["shots_total_duration_seconds"] for segment in sequence["segments"]), 3
    )

    return sequence


def write_recap_sequence(
    sequence: dict[str, Any],
    path: Path = RECAP_SEQUENCE_PATH,
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sequence, indent=2), encoding="utf-8")


def load_recap_sequence(path: Path = RECAP_SEQUENCE_PATH) -> dict[str, Any]:
    """
    Read back a previously-written recap_sequence.json. This is Track
    B's own output, not an external/untrusted input like Track A's
    files, so validation here is a light structural sanity check (did
    this actually come from write_recap_sequence(), not a full
    field-by-field re-validation like recap_media.loader's) -- still
    raises RecapInputError so downstream consumers only need to catch
    one exception type across every recap file.
    """

    if not path.exists():
        raise RecapInputError(f"recap_sequence.json not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecapInputError(f"recap_sequence.json is not valid JSON ({path}): {exc}") from exc

    if not isinstance(data, dict) or "segments" not in data:
        raise RecapInputError(
            f"recap_sequence.json is missing required field 'segments' ({path})"
        )

    return data
