"""
B3 -- exact recap sequence assembly. Turns each recap_script.json
segment's candidate ranges (recap_media.loader.load_recap_script()) into
an ordered list of exact source shots and writes
output/recap/recap_sequence.json.

recap_sequence.json's schema belongs entirely to Track B (the shared
contract never defines it, and Track A never reads it back) -- documented
inline below rather than in the shared contract doc.

Shot-selection policy, in order of priority:
1. Never invent time outside a candidate's own verified (start, end) --
   a shot's duration is clamped to that span before anything else.
2. Prefer a candidate not already used elsewhere in the sequence
   ("minimal unnecessary range reuse"); only reuse one when every
   available candidate for a segment has already been used at least
   once, and mark that shot reused=true so it's inspectable.
3. Among remaining ties, prefer the highest-scored candidate ("direct
   visual support for narration"), then the earliest start time
   (deterministic).
4. Each shot's duration targets the presentation type's cadence band
   (narration importance decides "normal illustrative" vs "important";
   reaction_beat and original_dialogue have their own bands) -- but
   never at the expense of rule 1.

"Chronological source progression" / "no nonsensical jumps" are not
enforced by refusing a candidate outright (Track A's own scoring already
reflects source-evidence quality, which this shouldn't second-guess) --
instead a large backward jump between consecutive segments' shots is
surfaced as an inspectable warning (segment-level and in the top-level
sequence_warnings list), the same "AI proposes, human/inspector reviews"
principle the rest of ShortsFactory already follows.
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
# SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR.md's "Initial cadence
# heuristics".
CADENCE_NORMAL_ILLUSTRATIVE = (1.3, 3.0)
CADENCE_IMPORTANT = (2.5, 4.0)
CADENCE_QUICK_REACTION = (0.6, 1.5)
CADENCE_ORIGINAL_DIALOGUE = (1.5, 4.5)

# A segment's own "importance" (0-1, from recap_script.json) at or above
# this uses the wider "important shot" band instead of the normal one.
IMPORTANT_THRESHOLD = 0.75

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


def cadence_for_segment(
    segment: dict[str, Any],
    use_dialogue_band: bool = False,
) -> tuple[float, float]:

    if use_dialogue_band:
        return CADENCE_ORIGINAL_DIALOGUE

    if segment.get("presentation_hint") == "reaction_beat":
        return CADENCE_QUICK_REACTION

    importance = float(segment.get("importance", 0.0))
    return CADENCE_IMPORTANT if importance >= IMPORTANT_THRESHOLD else CADENCE_NORMAL_ILLUSTRATIVE


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
        low, high = cadence_for_segment(segment)
        return (low + high) / 2.0, "visual_only_default"

    if segment_id in narration_durations:
        return float(narration_durations[segment_id]), "measured"

    return estimate_narration_seconds(segment["text"]), "estimated"


def _range_key(candidate: dict[str, Any]) -> tuple[float, float]:
    return (round(float(candidate["start"]), 2), round(float(candidate["end"]), 2))


def select_shots(
    candidates: list[dict[str, Any]],
    target_duration: float,
    cadence_low: float,
    cadence_high: float,
    used_ranges: set[tuple[float, float]],
    source_list_name: str,
) -> list[dict[str, Any]]:

    if not candidates:
        return []

    shots: list[dict[str, Any]] = []
    remaining = target_duration

    while remaining > 0.05 and len(shots) < MAX_SHOTS_PER_SEGMENT:

        candidate = min(
            candidates,
            key=lambda c: (
                _range_key(c) in used_ranges,  # unused ranges sort first
                -float(c["score"]),  # then highest score
                float(c["start"]),  # then earliest start (deterministic)
            ),
        )

        span = float(candidate["end"]) - float(candidate["start"])
        shot_duration = min(span, cadence_high, max(cadence_low, remaining))
        shot_start = float(candidate["start"])
        shot_end = round(shot_start + shot_duration, 3)
        reused = _range_key(candidate) in used_ranges

        shots.append(
            {
                "start": round(shot_start, 3),
                "end": shot_end,
                "duration": round(shot_duration, 3),
                "source_list": source_list_name,
                "score": candidate["score"],
                "reason": candidate.get("reason", ""),
                "reused": reused,
            }
        )

        used_ranges.add(_range_key(candidate))
        remaining -= shot_duration

    return shots


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
        cadence_low, cadence_high = cadence_for_segment(segment, use_dialogue_band=use_dialogue_band)
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
            target_duration,
            cadence_low,
            cadence_high,
            used_ranges,
            source_list_name,
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
            "reason": best.get("reason", ""),
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
