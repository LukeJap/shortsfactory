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
# A verified source range from an assigned beat not yet represented in the
# thought is editorially more useful than another generic illustrative range.
BEAT_COVERAGE_BONUS = 0.18

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

# Moving footage is allowed to run longer than the fast-cut anchor cadence
# when it comes from a locally verified range. This prevents the timeline
# layer from turning a lack of cuts into frozen clone-frame filler.
MAX_MOVING_COVERAGE_SHOT_SECONDS = 4.5
LOCAL_CONTEXT_EXTENSION_SECONDS = 10.0
MIN_MOVING_CONTINUATION_SECONDS = 0.7

# A small final-pass budget for swapping redundant coverage for additional
# source moments in longer, multi-beat narration thoughts. The source timeline
# and moving-footage total remain unchanged.
MAX_ADDITIONAL_SOURCE_MOMENTS_PER_RECAP = 3
MIN_DISTINCT_SOURCE_MOMENT_SECONDS = 1.8


def _candidate_key(candidate: dict[str, Any]) -> tuple[float, float]:
    """Use source timing, not incidental metadata, to identify one source range."""

    return _range_key(candidate)


def verified_candidates_for_segment(
    segment: dict[str, Any],
    verified_story_map: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return locally verified evidence for only this segment's assigned beats.

    Track A's recap candidates remain the editorial preference.  These
    candidates are Track B's supplemental inventory when a multi-beat thought
    needs more source coverage than the frozen recap_script handoff contains.
    """

    if not verified_story_map:
        return []

    beats_by_id = {
        beat.get("beat_id"): beat
        for beat in verified_story_map.get("beats", [])
        if isinstance(beat, dict) and isinstance(beat.get("beat_id"), str)
    }
    supplemental: list[dict[str, Any]] = []
    seen_ranges: set[tuple[float, float]] = set()

    for beat_id in segment.get("beat_ids", []):
        beat = beats_by_id.get(beat_id)
        if beat is None:
            continue
        for evidence in beat.get("source_evidence", []):
            if not isinstance(evidence, dict):
                continue
            try:
                candidate = {
                    "start": float(evidence["start"]),
                    "end": float(evidence["end"]),
                    "score": float(evidence["confidence"]),
                    "reason": (
                        f"Verified story evidence for {beat_id}: "
                        f"{beat.get('summary', '')}"
                    ),
                    "beat_id": beat_id,
                    "evidence_type": evidence.get("type", ""),
                    "candidate_origin": "verified_story_map",
                }
            except (KeyError, TypeError, ValueError):
                # The loader validates this input. Keep the builder resilient
                # when callers provide a partially constructed in-memory map.
                continue
            if candidate["end"] <= candidate["start"]:
                continue
            key = _candidate_key(candidate)
            if key not in seen_ranges:
                supplemental.append(candidate)
                seen_ranges.add(key)

    return supplemental


def visual_candidates_for_segment(
    segment: dict[str, Any],
    verified_story_map: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Merge preferred script candidates with unique assigned-beat evidence."""

    preferred = [dict(candidate) for candidate in segment.get("candidate_visuals", [])]
    for candidate in preferred:
        candidate.setdefault("candidate_origin", "recap_script")

    seen_ranges = {_candidate_key(candidate) for candidate in preferred}
    verified = verified_candidates_for_segment(segment, verified_story_map)
    verified_by_range = {
        _candidate_key(candidate): candidate for candidate in verified
    }

    # A recap-script range can deliberately duplicate the stronger story-map
    # evidence. Keep the script's editorial preference, but never discard the
    # assigned beat provenance when those ranges are the same.
    for candidate in preferred:
        verified_match = verified_by_range.get(_candidate_key(candidate))
        if verified_match is not None and not candidate.get("beat_id"):
            candidate["beat_id"] = verified_match.get("beat_id")
            candidate["evidence_type"] = verified_match.get("evidence_type", "")

    supplemental = [
        candidate
        for candidate in verified
        if _candidate_key(candidate) not in seen_ranges
    ]
    return preferred + supplemental


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
    covered_beat_ids: set[str],
) -> float:
    """
    B28's internal score: semantic relevance, visual-function diversity,
    and coverage of a not-yet-represented assigned beat, minus reuse and
    redundant adjacent-function penalties.
    """

    score = float(candidate["score"])

    if visual_function not in functions_used:
        score += DIVERSITY_BONUS

    beat_id = candidate.get("beat_id")
    adds_beat_coverage = isinstance(beat_id, str) and beat_id not in covered_beat_ids
    if adds_beat_coverage:
        score += BEAT_COVERAGE_BONUS

    if _range_key(candidate) in used_ranges:
        score -= REUSE_PENALTY

    if (
        last_function is not None
        and visual_function == last_function
        and not (visual_function == DEFAULT_VISUAL_FUNCTION and adds_beat_coverage)
    ):
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


def _order_selected_shots(
    shots: list[dict[str, Any]],
    segment: dict[str, Any],
) -> list[dict[str, Any]]:
    """Keep a multi-beat narration thought in source/story chronology.

    Score selection decides which ranges earn inclusion. For ordinary source
    narration spanning several beats, their final playback order should still
    follow the verified source timeline. Single-beat and special-presentation
    segments retain the existing visual-function progression behavior.
    """

    if (
        segment.get("presentation_hint") == "narration_over_source"
        and len(set(segment.get("beat_ids", []))) > 1
    ):
        return sorted(shots, key=lambda shot: (shot["start"], shot["end"]))
    return _reorder_for_progression(shots)


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
    remaining_candidates = list(candidates)
    remaining = target_duration
    functions_used: set[str] = set()
    covered_beat_ids: set[str] = set()
    last_function: str | None = None

    while (
        remaining_candidates
        and (not shots or remaining > MIN_SHOT_DURATION_SECONDS)
        and len(shots) < MAX_SHOTS_PER_SEGMENT
    ):

        scored = [
            (
                _selection_score(
                    candidate,
                    infer_visual_function(candidate),
                    used_ranges,
                    functions_used,
                    last_function,
                    covered_beat_ids,
                ),
                candidate,
            )
            for candidate in remaining_candidates
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
                "candidate_start": round(float(best_candidate["start"]), 3),
                "candidate_end": round(float(best_candidate["end"]), 3),
                "source_list": source_list_name,
                "score": best_candidate["score"],
                "selection_score": round(best_score, 3),
                "reason": best_candidate.get("reason", ""),
                "visual_function": visual_function,
                "reused": reused,
                "beat_id": best_candidate.get("beat_id"),
                "candidate_origin": best_candidate.get("candidate_origin", source_list_name),
            }
        )

        used_ranges.add(_range_key(best_candidate))
        remaining_candidates.remove(best_candidate)
        beat_id = best_candidate.get("beat_id")
        if isinstance(beat_id, str):
            covered_beat_ids.add(beat_id)
        functions_used.add(visual_function)
        last_function = visual_function
        remaining -= shot_duration

    return _order_selected_shots(shots, segment)


def _source_window_from_story_map(
    verified_story_map: dict[str, Any] | None,
) -> tuple[float, float] | None:
    """Return the locally verified source window for the selected story only."""

    if not verified_story_map:
        return None

    ranges: list[tuple[float, float]] = []
    for beat in verified_story_map.get("beats", []):
        if not isinstance(beat, dict):
            continue
        for evidence in beat.get("source_evidence", []):
            if not isinstance(evidence, dict):
                continue
            try:
                start = float(evidence["start"])
                end = float(evidence["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end > start:
                ranges.append((start, end))

    if not ranges:
        return None
    return min(start for start, _ in ranges), max(end for _, end in ranges)


def _uncovered_intervals(
    start: float,
    end: float,
    occupied: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Portions of a source range not already selected in this thought."""

    cursor = start
    intervals: list[tuple[float, float]] = []
    for occupied_start, occupied_end in sorted(occupied):
        if occupied_end <= cursor or occupied_start >= end:
            continue
        if occupied_start > cursor:
            intervals.append((cursor, min(occupied_start, end)))
        cursor = max(cursor, occupied_end)
        if cursor >= end:
            break
    if cursor < end:
        intervals.append((cursor, end))
    return intervals


def _append_moving_coverage(
    shots: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    segment: dict[str, Any],
    target_duration: float,
    used_ranges: set[tuple[float, float]],
    source_list_name: str,
    story_source_window: tuple[float, float] | None,
) -> list[dict[str, Any]]:
    """Use remaining verified/contextual source footage before any hold.

    The initial selector chooses concise editorial anchors. This second pass
    keeps those anchors, then consumes unused locally verified evidence for
    the thought and finally bounded contiguous local context around those
    anchors. Context never leaves the verified selected-story window.
    """

    if segment.get("presentation_hint") != "narration_over_source" or not shots:
        return shots

    remaining = target_duration - sum(float(shot["duration"]) for shot in shots)
    if remaining <= 0:
        return shots

    occupied = [(float(shot["start"]), float(shot["end"])) for shot in shots]
    selected_keys = {
        (round(float(shot.get("candidate_start", shot["start"])), 2),
         round(float(shot.get("candidate_end", shot["end"])), 2))
        for shot in shots
    }

    selected_candidates = [
        candidate
        for candidate in candidates
        if _candidate_key(candidate) in selected_keys
        and float(candidate.get("score", 0.0)) >= MIN_USEFUL_SELECTION_SCORE
    ]
    supplemental_candidates = [
        candidate
        for candidate in candidates
        if _candidate_key(candidate) not in selected_keys
        and _candidate_key(candidate) not in used_ranges
        and float(candidate.get("score", 0.0)) >= MIN_USEFUL_SELECTION_SCORE
    ]
    supplemental_candidates.sort(
        key=lambda candidate: (
            candidate.get("candidate_origin") != "verified_story_map",
            -float(candidate["score"]),
            float(candidate["start"]),
        )
    )

    def append_from_range(
        start: float,
        end: float,
        candidate: dict[str, Any],
        coverage_mode: str,
    ) -> None:
        nonlocal remaining
        for available_start, available_end in _uncovered_intervals(start, end, occupied):
            cursor = available_start
            while (
                cursor < available_end
                and remaining > 0.001
                and len(shots) < MAX_SHOTS_PER_SEGMENT
            ):
                duration = min(
                    MAX_MOVING_COVERAGE_SHOT_SECONDS,
                    available_end - cursor,
                    remaining,
                )
                if duration < MIN_MOVING_CONTINUATION_SECONDS:
                    # A small, contiguous tail is still useful when it can
                    # extend an already-selected shot from this exact source
                    # range. Folding it in avoids a visually meaningless
                    # rapid cut; otherwise leave it uncovered rather than
                    # creating filler from a tiny fragment.
                    extension_target = next(
                        (
                            shot
                            for shot in shots
                            if round(float(shot["end"]), 3) == round(cursor, 3)
                            and (
                                round(float(shot.get("candidate_start", shot["start"])), 2),
                                round(float(shot.get("candidate_end", shot["end"])), 2),
                            ) == _candidate_key(candidate)
                        ),
                        None,
                    )
                    if extension_target is None:
                        return
                    extended_duration = float(extension_target["duration"]) + duration
                    if extended_duration > MAX_MOVING_COVERAGE_SHOT_SECONDS:
                        # Keep the moving-shot cap absolute. Rebalance this
                        # contiguous same-candidate span instead of turning
                        # its small tail into an oversized source shot.
                        if len(shots) >= MAX_SHOTS_PER_SEGMENT:
                            return
                        chunk_count = int(
                            (extended_duration + MAX_MOVING_COVERAGE_SHOT_SECONDS - 0.001)
                            // MAX_MOVING_COVERAGE_SHOT_SECONDS
                        )
                        chunk_count = max(2, chunk_count)
                        chunk_duration = round(extended_duration / chunk_count, 3)
                        chunk_start = float(extension_target["start"])
                        extension_target["end"] = round(chunk_start + chunk_duration, 3)
                        extension_target["duration"] = chunk_duration
                        rebalanced_shot = dict(extension_target)
                        rebalanced_shot["start"] = extension_target["end"]
                        rebalanced_shot["end"] = round(cursor + duration, 3)
                        rebalanced_shot["duration"] = round(
                            float(rebalanced_shot["end"]) - float(rebalanced_shot["start"]),
                            3,
                        )
                        shots.append(rebalanced_shot)
                    else:
                        extension_target["end"] = round(cursor + duration, 3)
                        extension_target["duration"] = round(extended_duration, 3)
                    occupied[:] = [
                        (float(shot["start"]), float(shot["end"]))
                        for shot in shots
                    ]
                    remaining -= duration
                    cursor += duration
                    continue
                shot_end = round(cursor + duration, 3)
                visual_function = infer_visual_function(candidate)
                shots.append(
                    {
                        "start": round(cursor, 3),
                        "end": shot_end,
                        "duration": round(duration, 3),
                        "candidate_start": round(float(candidate["start"]), 3),
                        "candidate_end": round(float(candidate["end"]), 3),
                        "source_list": source_list_name,
                        "score": candidate["score"],
                        "selection_score": round(float(candidate["score"]), 3),
                        "reason": candidate.get("reason", ""),
                        "visual_function": visual_function,
                        "reused": False,
                        "beat_id": candidate.get("beat_id"),
                        "candidate_origin": candidate.get("candidate_origin", source_list_name),
                        "coverage_mode": coverage_mode,
                    }
                )
                occupied.append((cursor, shot_end))
                remaining -= duration
                cursor = shot_end

    # Use distinct useful evidence before extending an already-used anchor.
    # This preserves beat diversity and prevents a tiny tail from crowding
    # out another strong source moment.
    for candidate in supplemental_candidates + selected_candidates:
        if remaining <= 0.001 or len(shots) >= MAX_SHOTS_PER_SEGMENT:
            break
        append_from_range(
            float(candidate["start"]),
            float(candidate["end"]),
            candidate,
            "verified_range_extension",
        )
        used_ranges.add(_candidate_key(candidate))

    # The final moving-coverage fallback is contiguous local footage directly
    # around an assigned verified anchor. It remains inside the selected
    # story's global locally verified window, so it cannot spill into a sister
    # episode in a compound source file.
    if remaining > 0.001 and story_source_window is not None:
        window_start, window_end = story_source_window
        context_candidates = sorted(
            selected_candidates + supplemental_candidates,
            key=lambda candidate: float(candidate["start"]),
        )
        for candidate in context_candidates:
            if remaining <= 0.001 or len(shots) >= MAX_SHOTS_PER_SEGMENT:
                break
            start = float(candidate["start"])
            end = float(candidate["end"])
            context_ranges = (
                (max(window_start, start - LOCAL_CONTEXT_EXTENSION_SECONDS), start),
                (end, min(window_end, end + LOCAL_CONTEXT_EXTENSION_SECONDS)),
            )
            for context_start, context_end in context_ranges:
                if remaining <= 0.001:
                    break
                append_from_range(
                    context_start,
                    context_end,
                    candidate,
                    "contiguous_local_context",
                )

    return _order_selected_shots(shots, segment)


def _add_trailing_evidence_moment(
    shots: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    segment: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Trade redundant coverage for one unused, locally verified source moment."""

    if (
        segment.get("presentation_hint") != "narration_over_source"
        or len(set(segment.get("beat_ids", []))) < 2
    ):
        return shots, False

    occupied = [(float(shot["start"]), float(shot["end"])) for shot in shots]
    beat_counts: dict[str, int] = {}
    for shot in shots:
        beat_id = shot.get("beat_id")
        if isinstance(beat_id, str):
            beat_counts[beat_id] = beat_counts.get(beat_id, 0) + 1

    eligible: list[tuple[dict[str, Any], tuple[float, float]]] = []
    for candidate in candidates:
        if float(candidate.get("score", 0.0)) < MIN_USEFUL_SELECTION_SCORE:
            continue
        unused_intervals = _uncovered_intervals(
            float(candidate["start"]), float(candidate["end"]), occupied
        )
        tail = next(
            (
                interval
                for interval in reversed(unused_intervals)
                if interval[1] - interval[0] >= MIN_DISTINCT_SOURCE_MOMENT_SECONDS
            ),
            None,
        )
        if tail is not None:
            eligible.append((candidate, tail))

    if not eligible:
        return shots, False

    # Favor unrepresented/underrepresented assigned beats, with locally
    # verified supplemental evidence ahead of an equivalent script preference.
    eligible.sort(
        key=lambda item: (
            item[0].get("candidate_origin") != "verified_story_map",
            beat_counts.get(str(item[0].get("beat_id", "")), 0),
            -float(item[0]["score"]),
            -(item[1][1] - item[1][0]),
            float(item[0]["start"]),
        )
    )
    candidate, tail = eligible[0]

    # Preserve a readable source moment on both sides of the swap. Favor a
    # repeated beat as the donor so a new beat/context moment adds variety.
    donors = [
        shot
        for shot in shots
        if float(shot["duration"]) >= 2 * MIN_DISTINCT_SOURCE_MOMENT_SECONDS
        and shot.get("treatment", "narration_over_source") != "original_dialogue"
    ]
    if not donors:
        return shots, False
    donor = max(
        donors,
        key=lambda shot: (
            beat_counts.get(str(shot.get("beat_id", "")), 0) > 1,
            float(shot["duration"]),
            -float(shot["start"]),
        ),
    )

    moment_duration = min(
        CADENCE_ILLUSTRATIVE[1],
        tail[1] - tail[0],
        float(donor["duration"]) - MIN_DISTINCT_SOURCE_MOMENT_SECONDS,
    )
    if moment_duration < MIN_DISTINCT_SOURCE_MOMENT_SECONDS:
        return shots, False

    # Shorten the donor and spend the same timeline duration on an unused tail
    # of the verified candidate. This adds a source moment without changing
    # narration timing, total coverage, or the hard moving-shot cap.
    donor["end"] = round(float(donor["end"]) - moment_duration, 3)
    donor["duration"] = round(float(donor["duration"]) - moment_duration, 3)
    moment_start = round(tail[1] - moment_duration, 3)
    moment_end = round(tail[1], 3)
    shots.append(
        {
            "start": moment_start,
            "end": moment_end,
            "duration": round(moment_end - moment_start, 3),
            "candidate_start": round(float(candidate["start"]), 3),
            "candidate_end": round(float(candidate["end"]), 3),
            "source_list": "candidate_visuals",
            "score": candidate["score"],
            "selection_score": round(float(candidate["score"]), 3),
            "reason": candidate.get("reason", ""),
            "visual_function": infer_visual_function(candidate),
            "reused": False,
            "beat_id": candidate.get("beat_id"),
            "candidate_origin": candidate.get("candidate_origin", "candidate_visuals"),
            "coverage_mode": "trailing_evidence_moment",
        }
    )
    return _order_selected_shots(shots, segment), True


def _apply_timeline_coverage(
    shots: list[dict[str, Any]],
    narration_duration: float,
) -> tuple[float, float, float, float]:
    """Make selected source shots occupy one narration window exactly.

    ``duration`` remains the actual moving source span. Any remaining
    shortfall is explicit metadata; the renderer rejects a material shortfall
    rather than turning it into a multi-second frozen frame.
    """

    target = round(max(0.0, float(narration_duration)), 3)
    remaining = target
    trimmed: list[dict[str, Any]] = []

    # Selection normally stays within the target.  Guard the edge case where
    # a cadence minimum slightly overshoots it so every output track still has
    # exactly the narration timeline length.
    for shot in shots:
        raw_duration = max(0.0, float(shot["duration"]))
        kept_duration = min(raw_duration, remaining)
        if kept_duration <= 0:
            continue
        if kept_duration != raw_duration:
            shot["duration"] = round(kept_duration, 3)
            shot["end"] = round(float(shot["start"]) + kept_duration, 3)
        shot["hold_duration_seconds"] = 0.0
        shot["timeline_duration_seconds"] = round(kept_duration, 3)
        trimmed.append(shot)
        remaining -= kept_duration

    shots[:] = trimmed
    raw_total = round(sum(float(shot["duration"]) for shot in shots), 3)
    shortfall = round(max(0.0, target - raw_total), 3)
    timeline_total = target
    return raw_total, 0.0, timeline_total, shortfall


def _refresh_segment_timeline(segment: dict[str, Any]) -> None:
    """Refresh raw-footage and output-timeline metrics after shot changes."""

    raw_total, hold_total, timeline_total, shortfall = _apply_timeline_coverage(
        segment["shots"], segment["narration_duration_seconds"]
    )
    segment["shots_total_duration_seconds"] = raw_total
    segment["visual_hold_duration_seconds"] = hold_total
    segment["timeline_duration_seconds"] = timeline_total
    segment["visual_coverage_shortfall_seconds"] = shortfall


def assemble_sequence(
    recap_script: dict[str, Any],
    narration_durations: dict[str, float] | None = None,
    verified_story_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the full recap_sequence.json structure from an already-loaded
    and validated recap_script.json (see recap_media.loader.
    load_recap_script()) and an optional {segment_id: duration_seconds}
    map of real Orpheus measurements (recap_media.voiceover.
    synthesize_segments() results). Segments missing from that map fall
    back to a word-count duration estimate (or a fixed default for
    visual_only segments, which never have narration). A provided normalized
    verified story map contributes additional source evidence for a segment's
    assigned beat_ids when its recap-script candidates are not enough.
    """

    narration_durations = narration_durations or {}
    story_source_window = _source_window_from_story_map(verified_story_map)
    extra_source_moments_remaining = (
        MAX_ADDITIONAL_SOURCE_MOMENTS_PER_RECAP if verified_story_map else 0
    )
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

        candidates = (
            visual_candidates_for_segment(segment, verified_story_map)
            if primary_list == "candidate_visuals"
            else segment.get(primary_list) or []
        )
        source_list_name = primary_list
        if not candidates:
            # Nothing in this segment's preferred list -- better to show
            # something from the other list than nothing at all.
            candidates = (
                visual_candidates_for_segment(segment, verified_story_map)
                if fallback_list == "candidate_visuals"
                else segment.get(fallback_list) or []
            )
            source_list_name = fallback_list

        shots = select_shots(
            candidates,
            segment,
            target_duration,
            used_ranges,
            source_list_name,
            use_dialogue_band=use_dialogue_band,
        )
        shots = _append_moving_coverage(
            shots,
            candidates,
            segment,
            target_duration,
            used_ranges,
            source_list_name,
            story_source_window,
        )
        if extra_source_moments_remaining:
            shots, added_moment = _add_trailing_evidence_moment(
                shots, candidates, segment
            )
            if added_moment:
                extra_source_moments_remaining -= 1

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

        output_segment = {
            "segment_id": segment["segment_id"],
            "order": segment["order"],
            "presentation_hint": hint,
            "beat_ids": segment["beat_ids"],
            "narration_duration_seconds": round(target_duration, 3),
            "narration_duration_source": duration_source,
            "shots": shots,
            "has_dialogue_insert": False,
            "warnings": warnings,
        }
        _refresh_segment_timeline(output_segment)
        segments_out.append(output_segment)

        sequence_warnings.extend(
            f"{segment['segment_id']}: {warning}" for warning in warnings
        )

    total_duration = round(
        sum(segment["timeline_duration_seconds"] for segment in segments_out), 3
    )
    raw_source_duration = round(
        sum(segment["shots_total_duration_seconds"] for segment in segments_out), 3
    )
    visual_hold_duration = round(
        sum(segment["visual_hold_duration_seconds"] for segment in segments_out), 3
    )
    visual_coverage_shortfall = round(
        sum(segment["visual_coverage_shortfall_seconds"] for segment in segments_out), 3
    )

    return {
        "schema_version": SEQUENCE_SCHEMA_VERSION,
        "target_duration_seconds": recap_script.get("target_duration_seconds"),
        "total_duration_seconds": total_duration,
        "raw_source_duration_seconds": raw_source_duration,
        "visual_hold_duration_seconds": visual_hold_duration,
        "visual_coverage_shortfall_seconds": visual_coverage_shortfall,
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
    existing shot list. When the inserted dialogue is shorter than the
    displaced locally verified shot, retain the unused source remainder so
    the insert cannot create a visual-coverage shortfall. Requires at least
    2 shots going in, so at least one illustrative shot survives on each
    side (the caller enforces this) -- otherwise "VOICEOVER RESUMES" would
    not mean anything.
    """

    insertion_index = len(shots) // 2
    displaced = shots[insertion_index]
    remaining = shots[:insertion_index] + shots[insertion_index + 1:]
    remaining.insert(insertion_index, insert_shot)

    unused_duration = round(
        float(displaced["duration"]) - float(insert_shot["duration"]), 3
    )
    if unused_duration > 0:
        retained = dict(displaced)
        retained["end"] = round(float(retained["start"]) + unused_duration, 3)
        retained["duration"] = unused_duration
        remaining.insert(insertion_index + 1, retained)
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

        total_duration = seq_segment["timeline_duration_seconds"]
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
        _refresh_segment_timeline(seq_segment)
        seq_segment["has_dialogue_insert"] = True

        used_ranges.add(_range_key(best))

    sequence["total_duration_seconds"] = round(
        sum(segment["timeline_duration_seconds"] for segment in sequence["segments"]), 3
    )
    sequence["raw_source_duration_seconds"] = round(
        sum(segment["shots_total_duration_seconds"] for segment in sequence["segments"]), 3
    )
    sequence["visual_hold_duration_seconds"] = round(
        sum(segment["visual_hold_duration_seconds"] for segment in sequence["segments"]), 3
    )
    sequence["visual_coverage_shortfall_seconds"] = round(
        sum(
            segment.get("visual_coverage_shortfall_seconds", 0.0)
            for segment in sequence["segments"]
        ),
        3,
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
