"""Staged, grounded recap writing with repair and quality validation."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .llm import JsonModel, ModelGeneration
from .models import (
    PRESENTATION_HINTS,
    SCHEMA_VERSION,
    RecapValidationError,
    utc_now,
    validate_recap_script,
    write_json,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTLINE_PROMPT_PATH = ROOT / "prompts" / "recap_outline.md"
DEFAULT_PROMPT_PATH = ROOT / "prompts" / "recap_writer.md"
DEFAULT_CRITIC_PROMPT_PATH = ROOT / "prompts" / "recap_critic.md"
DEFAULT_REPAIR_PROMPT_PATH = ROOT / "prompts" / "recap_repair.md"
WRITER_PROMPT_VERSION = "recap-writer-rich-plan-authoritative-v5"


class RecapWritingError(RuntimeError):
    """Raised when a recap cannot be grounded into the shared handoff."""


@dataclass(frozen=True)
class RecapWritingConfig:
    target_duration_seconds: int = 120
    target_word_count: int = 310
    voice_style: str = "fast_story_recap"
    minimum_word_count: int = 180
    maximum_word_count: int = 360
    max_repair_attempts: int = 2
    max_revision_attempts: int = 2


def _word_count(text: str) -> int:
    return len(str(text or "").split())


def _beat_map(
    story_map: dict[str, Any],
    *,
    verified_only: bool = False,
) -> dict[str, dict[str, Any]]:
    return {
        str(beat.get("beat_id")): beat
        for beat in story_map.get("beats", [])
        if isinstance(beat, dict)
        and beat.get("beat_id")
        and (
            not verified_only
            or beat.get("verification_status") == "verified"
        )
    }


def _story_payload(story_map: dict[str, Any]) -> dict[str, Any]:
    """Keep prompts focused on verified evidence and avoid segment ambiguity."""
    fields = (
        "beat_id",
        "chronological_order",
        "segment_id",
        "summary",
        "story_purpose",
        "characters",
        "location",
        "importance",
        "motivation",
        "change",
        "emotional_conflict",
        "payoff_significance",
        "causal_parents",
        "causal_children",
        "causal_reasoning",
        "semantic_unit_ids",
        "actual_video_evidence_ranges",
        "original_dialogue_candidates",
        "confidence",
    )
    beats = [
        {field: beat.get(field) for field in fields if field in beat}
        for beat in story_map.get("beats", [])
        if isinstance(beat, dict)
        and beat.get("verification_status") == "verified"
    ]
    return {
        "canonical_identity": story_map.get("canonical_identity", {}),
        "duration_seconds": story_map.get("duration_seconds"),
        "confidence": story_map.get("confidence"),
        "warnings": list(story_map.get("warnings", []) or []),
        "verified_beats": beats,
    }


def _rich_thought_payload(
    story_map: dict[str, Any],
    outline: dict[str, Any],
) -> dict[str, Any]:
    """Expose only the selected factual material to the RICH prose call."""
    verified_beats = _beat_map(story_map, verified_only=True)
    fields = (
        "summary",
        "characters",
        "motivation",
        "change",
        "emotional_conflict",
        "payoff_significance",
    )
    thoughts: list[dict[str, Any]] = []
    for planned in outline.get("planned_segments", []):
        facts = []
        for beat_id in planned.get("beat_ids", []):
            beat = verified_beats.get(str(beat_id))
            if beat is None:
                continue
            fact = {
                field: beat[field]
                for field in fields
                if field in beat and _meaningful_text(beat.get(field))
            }
            if fact:
                facts.append(fact)
        thoughts.append(
            {
                "plan_id": str(planned.get("plan_id", "")),
                "story_role": str(planned.get("function", "")),
                "assigned_facts": facts,
                "target_words": int(planned.get("target_words", 0) or 0),
                "word_range": list(planned.get("word_range", [])),
            }
        )
    return {"planned_thoughts": thoughts}


def _ranges_for_beats(
    beat_ids: list[str],
    beats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_beat: list[tuple[str, list[dict[str, Any]]]] = []
    seen: set[tuple[float, float]] = set()
    for beat_id in beat_ids:
        beat = beats.get(beat_id, {})
        candidates: list[dict[str, Any]] = []
        for item in beat.get("actual_video_evidence_ranges", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                start = float(item.get("start"))
                end = float(item.get("end"))
            except (TypeError, ValueError):
                continue
            if end <= start or (start, end) in seen:
                continue
            candidates.append(
                {
                    "start": round(start, 4),
                    "end": round(end, 4),
                    "score": round(float(item.get("confidence", 0.0) or 0.0), 4),
                    "reason": f"Primary verified source evidence for story beat {beat_id}.",
                }
            )
        candidates.sort(
            key=lambda value: (
                value["end"] - value["start"],
                -value["score"],
                value["start"],
            )
        )
        if candidates:
            by_beat.append((beat_id, candidates))

    ranges: list[dict[str, Any]] = []
    for _, candidates in by_beat:
        candidate = candidates[0]
        key = (candidate["start"], candidate["end"])
        if key not in seen:
            seen.add(key)
            ranges.append(candidate)
    extras = sorted(
        (
            candidate
            for _, candidates in by_beat
            for candidate in candidates[1:]
        ),
        key=lambda value: (
            value["end"] - value["start"],
            -value["score"],
            value["start"],
        ),
    )
    for candidate in extras:
        if len(ranges) >= 4:
            break
        key = (candidate["start"], candidate["end"])
        if key in seen:
            continue
        seen.add(key)
        candidate = dict(candidate)
        candidate["reason"] = "Additional distinct verified evidence for this narration thought."
        ranges.append(candidate)
    return ranges[:4]


def _dialogue_sources_for_beat(beat: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = [
        dict(item)
        for item in beat.get("original_dialogue_candidates", []) or []
        if isinstance(item, dict)
    ]
    if explicit:
        return explicit
    purpose = str(beat.get("story_purpose", "") or "").casefold()
    conflict = str(beat.get("emotional_conflict", "") or "").casefold()
    if not any(
        token in purpose or token in conflict
        for token in ("reversal", "reveal", "payoff", "climax", "resolution", "emotional", "conflict", "anger", "heartbreak")
    ):
        return []
    candidates: list[dict[str, Any]] = []
    for evidence in beat.get("actual_video_evidence_ranges", []) or []:
        if not isinstance(evidence, dict):
            continue
        excerpt = " ".join(
            str(evidence.get("transcript_excerpt", "") or "").split()
        )
        try:
            start = float(evidence.get("start"))
            end = float(evidence.get("end"))
        except (TypeError, ValueError):
            continue
        if not excerpt or _word_count(excerpt) > 32 or end <= start or end - start > 18:
            continue
        candidates.append(
            {
                "start": round(start, 4),
                "end": round(end, 4),
                "score": round(float(evidence.get("confidence", 0.7) or 0.7), 4),
                "reason": "Verified concise source dialogue for a high-value story beat.",
            }
        )
    return candidates[:1]


def _dialogue_for_beats(
    beat_ids: list[str],
    beats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for beat_id in beat_ids:
        for item in _dialogue_sources_for_beat(beats.get(beat_id, {})):
            if isinstance(item, dict):
                candidates.append(dict(item))
    return candidates[:2]


def _purpose(beat: dict[str, Any]) -> str:
    return str(beat.get("story_purpose", "") or "").casefold()


def _meaningful_text(value: Any) -> bool:
    normalized = _normalized_prose(str(value or ""))
    return bool(normalized) and normalized not in {
        "none",
        "unknown",
        "unclear",
        "not stated",
        "not available",
        "n a",
        "no conflict",
    }


def _ordered_verified_beats(story_map: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        _beat_map(story_map, verified_only=True).values(),
        key=lambda beat: (
            int(beat.get("chronological_order", 0) or 0),
            str(beat.get("beat_id", "")),
        ),
    )


def _has_story_signal(beat: dict[str, Any]) -> bool:
    return any(
        _meaningful_text(beat.get(field, ""))
        for field in (
            "motivation",
            "change",
            "emotional_conflict",
            "payoff_significance",
        )
    )


def _hook_score(beat: dict[str, Any]) -> float:
    purpose = _purpose(beat)
    score = 3.0 * float(beat.get("importance", 0.5) or 0.5)
    score += 1.4 if _meaningful_text(beat.get("emotional_conflict")) else 0.0
    score += 0.8 if _meaningful_text(beat.get("motivation")) else 0.0
    score += 0.8 if _meaningful_text(beat.get("change")) else 0.0
    if "inciting" in purpose:
        score += 1.7
    elif any(token in purpose for token in ("escalation", "conflict")):
        score += 1.5
    elif "attempt" in purpose:
        score += 0.9
    elif "setup" in purpose:
        score -= 0.7
    elif any(token in purpose for token in ("resolution", "button")):
        score -= 1.3
    # A reveal can frame a hook, but avoid spending the ending when a strong
    # conflict beat can open the story instead.
    elif any(token in purpose for token in ("reversal", "reveal", "payoff", "climax")):
        score += 0.2
    return score


def _is_attempt_or_consequence(beat: dict[str, Any]) -> bool:
    purpose = _purpose(beat)
    return any(token in purpose for token in ("attempt", "failure", "consequence"))


def _is_downstream_reaction(beat: dict[str, Any]) -> bool:
    if _is_attempt_or_consequence(beat):
        return True
    purpose = _purpose(beat)
    return _meaningful_text(beat.get("emotional_conflict")) and any(
        token in purpose for token in ("reaction", "response", "coping")
    )


def _central_conflict_prerequisite(
    beats: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the available choice or conflict that makes a later response legible."""
    downstream = next((beat for beat in beats if _is_downstream_reaction(beat)), None)
    if downstream is None:
        return None
    order = int(downstream.get("chronological_order", 0) or 0)
    candidates = [
        beat
        for beat in beats
        if int(beat.get("chronological_order", 0) or 0) < order
        and not _is_attempt_or_consequence(beat)
        and "setup" not in _purpose(beat)
    ]
    if not candidates:
        return None

    choice_terms = ("choice", "choose", "chooses", "chose", "reject", "refuse", "leave", "lose", "abandon")

    def score(beat: dict[str, Any]) -> tuple[int, int, float]:
        summary = str(beat.get("summary", "") or "").casefold()
        purpose = _purpose(beat)
        semantic_priority = int(any(term in summary for term in choice_terms))
        semantic_priority += int(
            any(term in purpose for term in ("inciting", "conflict", "escalation"))
        )
        return (
            semantic_priority,
            int(beat.get("chronological_order", 0) or 0),
            float(beat.get("importance", 0.5) or 0.5),
        )

    return max(candidates, key=score)


def _middle_story_groups(middle: list[dict[str, Any]]) -> list[tuple[list[str], bool]]:
    """Compress dense repeated attempts without discarding their evidence IDs."""
    groups: list[tuple[list[str], bool]] = []
    index = 0
    while index < len(middle):
        attempt_indexes = [
            position
            for position in range(index, len(middle))
            if _is_attempt_or_consequence(middle[position])
        ]
        if attempt_indexes:
            cluster = [attempt_indexes[0]]
            for position in attempt_indexes[1:]:
                if position - cluster[-1] <= 3:
                    cluster.append(position)
                else:
                    break
            if len(cluster) >= 2:
                end = min(len(middle), cluster[-1] + 4)
                groups.append(
                    (
                        [
                            str(beat["beat_id"])
                            for beat in middle[index:end]
                        ],
                        True,
                    )
                )
                index = end
                continue

        # Keep an isolated later response with the event immediately before and
        # after it, so a new response does not become a detached consequence.
        if (
            index + 2 < len(middle)
            and _is_attempt_or_consequence(middle[index + 1])
        ):
            groups.append(
                (
                    [
                        str(beat["beat_id"])
                        for beat in middle[index : index + 3]
                    ],
                    False,
                )
            )
            index += 3
            continue

        groups.append(([str(middle[index]["beat_id"])], False))
        index += 1
    return groups


def _choice_payload(beat_ids: list[str], intent: str) -> dict[str, Any]:
    return {"beat_ids": beat_ids, "intent": intent}


def _segment_budget(function: str, beat_count: int) -> int:
    base = {
        "hook": 39,
        "setup": 38,
        "escalation": 64,
        "reversal_payoff": 84,
        "resolution": 25,
    }.get(function, 42)
    return base + max(0, beat_count - 1) * 6


def _normalize_plan_budgets(
    planned_segments: list[dict[str, Any]],
    config: RecapWritingConfig,
) -> None:
    """Scale only over-budget plans while retaining their story-role weighting."""
    current_total = sum(
        int(segment.get("target_words", 0) or 0) for segment in planned_segments
    )
    if not planned_segments or current_total <= 0:
        return

    hard_ceiling = min(340, max(1, config.maximum_word_count))
    complexity_allowance = min(10, max(0, len(planned_segments) - 6) * 2)
    preferred_ceiling = min(hard_ceiling, 310 + complexity_allowance)
    target_total = min(current_total, preferred_ceiling)
    if current_total <= target_total:
        return

    role_floors = {
        "hook": 24,
        "setup": 22,
        "escalation": 32,
        "reversal_payoff": 52,
        "resolution": 14,
    }
    floors = [
        role_floors.get(str(segment.get("function", "")), 18)
        for segment in planned_segments
    ]
    floor_total = sum(floors)
    if floor_total > target_total:
        scale = target_total / floor_total
        floors = [max(1, int(floor * scale)) for floor in floors]
        while sum(floors) > target_total:
            largest = max(range(len(floors)), key=lambda index: floors[index])
            floors[largest] -= 1

    raw_allocations = [
        int(segment["target_words"]) * target_total / current_total
        for segment in planned_segments
    ]
    allocations = [
        max(floor, int(allocation))
        for floor, allocation in zip(floors, raw_allocations)
    ]
    while sum(allocations) < target_total:
        index = max(
            range(len(allocations)),
            key=lambda position: (
                raw_allocations[position] - allocations[position],
                raw_allocations[position],
                -position,
            ),
        )
        allocations[index] += 1
    while sum(allocations) > target_total:
        candidates = [
            index
            for index, allocation in enumerate(allocations)
            if allocation > floors[index]
        ]
        if not candidates:
            break
        index = max(
            candidates,
            key=lambda position: (allocations[position], -position),
        )
        allocations[index] -= 1

    for segment, target_words in zip(planned_segments, allocations):
        segment["target_words"] = target_words
        segment["word_range"] = [max(12, target_words - 8), target_words + 8]


def build_narration_plan(
    story_map: dict[str, Any],
    config: RecapWritingConfig,
) -> dict[str, Any]:
    """Select and budget a grounded story spine without asking the model."""
    beats = _ordered_verified_beats(story_map)
    if not beats:
        raise RecapWritingError("No verified beats are available for recap writing")

    non_terminal = [
        beat
        for beat in beats
        if not any(
            token in _purpose(beat)
            for token in ("reversal", "reveal", "payoff", "climax", "resolution", "button")
        )
    ]
    signaled = [beat for beat in non_terminal if _has_story_signal(beat)]
    central_conflict = _central_conflict_prerequisite(beats)
    hook_pool = signaled or non_terminal or beats
    hook = central_conflict or max(hook_pool, key=_hook_score)
    hook_id = str(hook["beat_id"])
    hook_order = int(hook.get("chronological_order", 0) or 0)

    setup_candidates = [
        beat
        for beat in beats
        if int(beat.get("chronological_order", 0) or 0) < hook_order
        and any(
            token in _purpose(beat)
            for token in ("setup", "inciting", "conflict", "escalation", "attempt")
        )
    ]
    meaningful_setup = [beat for beat in setup_candidates if _has_story_signal(beat)]
    setup = max(
        meaningful_setup or setup_candidates,
        key=lambda beat: (
            int(beat.get("chronological_order", 0) or 0),
            float(beat.get("importance", 0.5) or 0.5),
        ),
        default=None,
    )

    terminal = [
        beat
        for beat in beats
        if any(token in _purpose(beat) for token in ("reversal", "reveal", "payoff", "climax"))
    ]
    resolution = [
        beat
        for beat in beats
        if any(token in _purpose(beat) for token in ("resolution", "button"))
    ]
    selected = []
    for beat in beats:
        order = int(beat.get("chronological_order", 0) or 0)
        keep = (
            beat is hook
            or beat is setup
            or beat in terminal
            or beat in resolution
            or float(beat.get("importance", 0.5) or 0.5) >= 0.52
            or _has_story_signal(beat)
        )
        # Once a later conflict earns the hook, earlier connective beats are
        # compressed into the single chosen setup instead of being narrated as
        # equal-weight chronology.
        if order < hook_order and beat is not setup and not _meaningful_text(
            beat.get("emotional_conflict")
        ):
            keep = False
        if keep:
            selected.append(beat)
    selected_ids = [str(beat["beat_id"]) for beat in selected]

    planned_segments: list[dict[str, Any]] = []

    def add_segment(
        function: str,
        beat_ids: list[str],
        *,
        compressed_attempts: bool = False,
    ) -> None:
        ids = list(dict.fromkeys(value for value in beat_ids if value))
        if not ids:
            return
        target = _segment_budget(function, len(ids))
        if compressed_attempts:
            target = min(88, target)
        planned_segments.append(
            {
                "plan_id": f"P{len(planned_segments) + 1:02d}",
                "function": function,
                "beat_ids": ids,
                "target_words": target,
                "word_range": [max(12, target - 8), target + 8],
            }
        )

    setup_id = str(setup["beat_id"]) if setup is not None else ""
    add_segment("hook", [hook_id])
    add_segment("setup", [setup_id])
    used = {hook_id, setup_id} - {""}

    terminal_ids = {str(beat["beat_id"]) for beat in terminal}
    resolution_ids = {str(beat["beat_id"]) for beat in resolution}
    middle = [
        beat_id
        for beat_id in selected_ids
        if beat_id not in used | terminal_ids | resolution_ids
    ]
    middle_beats = [
        _beat_map(story_map, verified_only=True)[beat_id]
        for beat_id in middle
        if beat_id in _beat_map(story_map, verified_only=True)
    ]
    for beat_ids, compressed_attempts in _middle_story_groups(middle_beats):
        add_segment(
            "escalation",
            beat_ids,
            compressed_attempts=compressed_attempts,
        )
    add_segment(
        "reversal_payoff",
        [str(beat["beat_id"]) for beat in terminal],
    )
    add_segment(
        "resolution",
        [str(beat["beat_id"]) for beat in resolution],
    )

    # Tiny fixtures and genuinely tiny stories still need a useful plan.
    if not planned_segments:
        add_segment("hook", [hook_id])

    _normalize_plan_budgets(planned_segments, config)

    sections: list[dict[str, Any]] = []
    if len(planned_segments) <= 2:
        section_groups = [("full_story", planned_segments)]
    else:
        opening = [
            item for item in planned_segments if item["function"] in {"hook", "setup"}
        ]
        ending = [
            item
            for item in planned_segments
            if item["function"] in {"reversal_payoff", "resolution"}
        ]
        middle_segments = [
            item for item in planned_segments if item not in opening and item not in ending
        ]
        section_groups = [
            ("hook_setup", opening),
            ("conflict_escalation", middle_segments),
            ("reversal_payoff_resolution", ending),
        ]
    for section_id, items in section_groups:
        if not items:
            continue
        sections.append(
            {
                "section_id": section_id,
                "planned_segments": items,
                "beat_ids": list(
                    dict.fromkeys(
                        beat_id
                        for item in items
                        for beat_id in item["beat_ids"]
                    )
                ),
                "target_words": sum(item["target_words"] for item in items),
            }
        )

    reversal_ids = [
        str(beat["beat_id"])
        for beat in terminal
        if any(token in _purpose(beat) for token in ("reversal", "reveal"))
    ]
    payoff_ids = [
        str(beat["beat_id"])
        for beat in terminal
        if any(token in _purpose(beat) for token in ("payoff", "climax"))
    ]
    escalation_groups = [
        _choice_payload(item["beat_ids"], "Material conflict growth or response.")
        for item in planned_segments
        if item["function"] == "escalation"
    ]
    omitted = [str(beat["beat_id"]) for beat in beats if str(beat["beat_id"]) not in selected_ids]
    return {
        "hook": _choice_payload(
            [hook_id],
            "Open on the strongest grounded conflict or emotionally consequential choice.",
        ),
        "minimum_setup": _choice_payload(
            [str(setup["beat_id"])] if setup is not None else [],
            "Only the context required to understand the conflict.",
        ),
        "essential_causal_chain": [
            _choice_payload(item["beat_ids"], f"Selected {item['function']} story movement.")
            for item in planned_segments
        ],
        "escalation_beats": escalation_groups,
        "reversal": _choice_payload(reversal_ids, "The verified turn or reveal."),
        "payoff_climax": _choice_payload(payoff_ids, "The earned verified outcome."),
        "resolution_button": _choice_payload(
            [str(beat["beat_id"]) for beat in resolution],
            "The shortest useful consequence after the payoff.",
        ),
        "omitted_beat_ids": omitted,
        "planned_segments": planned_segments,
        "sections": sections,
        "planned_word_count": sum(item["target_words"] for item in planned_segments),
    }


def _normalize_ranges(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RecapWritingError(f"{field} must be a list")
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise RecapWritingError(f"{field}[{index}] must be an object")
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError) as exc:
            raise RecapWritingError(
                f"{field}[{index}] needs numeric start/end"
            ) from exc
        if end <= start:
            raise RecapWritingError(f"{field}[{index}] must have end > start")
        try:
            score = float(raw.get("score", raw.get("confidence", 0.5)) or 0.5)
        except (TypeError, ValueError):
            score = 0.5
        output.append(
            {
                "start": round(start, 4),
                "end": round(end, 4),
                "score": round(max(0.0, min(1.0, score)), 4),
                "reason": str(raw.get("reason", "") or "").strip(),
            }
        )
    return output


def normalize_script(
    raw: dict[str, Any],
    story_map: dict[str, Any],
    config: RecapWritingConfig,
    *,
    deterministic_segment_ids: bool = False,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError("Recap writer response must be an object")
    raw_segments = raw.get("segments", [])
    if not isinstance(raw_segments, list) or not raw_segments:
        raise RecapWritingError("Recap writer returned no segments")

    all_beats = _beat_map(story_map)
    verified_beats = _beat_map(story_map, verified_only=True)
    segments: list[dict[str, Any]] = []
    seen_segment_ids: set[str] = set()
    for index, raw_segment in enumerate(raw_segments, start=1):
        if not isinstance(raw_segment, dict):
            raise RecapWritingError(f"Recap segment {index} is not an object")
        raw_beat_ids = raw_segment.get("beat_ids", [])
        if not isinstance(raw_beat_ids, list) or not raw_beat_ids:
            raise RecapWritingError(f"Recap segment {index} needs beat_ids")
        beat_ids = list(dict.fromkeys(str(value) for value in raw_beat_ids))
        unknown = [beat_id for beat_id in beat_ids if beat_id not in all_beats]
        unverified = [
            beat_id
            for beat_id in beat_ids
            if beat_id in all_beats and beat_id not in verified_beats
        ]
        if unknown:
            raise RecapWritingError(
                f"Recap segment {index} references unknown beat IDs: {unknown}"
            )
        if unverified:
            raise RecapWritingError(
                f"Recap segment {index} references unverified beat IDs: {unverified}"
            )

        text = " ".join(str(raw_segment.get("text", "") or "").split()).strip()
        if not text:
            raise RecapWritingError(f"Recap segment {index} has no narration text")
        hint = str(
            raw_segment.get("presentation_hint", "narration_over_source")
        ).strip()
        if hint not in PRESENTATION_HINTS:
            raise RecapWritingError(f"Invalid presentation hint: {hint}")

        # Grounding metadata is deterministic. The language model authors the
        # narration, but cannot invent timing ranges or flatten beat priority.
        visuals = _ranges_for_beats(beat_ids, verified_beats)
        dialogue = _normalize_ranges(
            _dialogue_for_beats(beat_ids, verified_beats),
            f"segment {index}.original_dialogue_candidates",
        )

        importance = max(
            [
                float(verified_beats[beat_id].get("importance", 0.5) or 0.5)
                for beat_id in beat_ids
            ]
            or [0.5]
        )
        purposes = " ".join(_purpose(verified_beats[beat_id]) for beat_id in beat_ids)
        if any(token in purposes for token in ("payoff", "climax", "reversal", "reveal")):
            importance += 0.025
        if deterministic_segment_ids:
            segment_id = f"VO_{index:03d}"
        else:
            segment_id = str(raw_segment.get("segment_id") or f"VO_{index:03d}").strip()
            if not segment_id or segment_id in seen_segment_ids:
                raise RecapWritingError("Recap segment IDs must be present and unique")
            seen_segment_ids.add(segment_id)
        segments.append(
            {
                "segment_id": segment_id,
                "order": index,
                "text": text,
                "word_count": _word_count(text),
                "beat_ids": beat_ids,
                "presentation_hint": hint,
                "importance": round(max(0.0, min(1.0, importance)), 4),
                "candidate_visuals": visuals,
                "original_dialogue_candidates": dialogue,
            }
        )

    script = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "target_duration_seconds": config.target_duration_seconds,
        "target_word_count": config.target_word_count,
        "voice_style": config.voice_style,
        "actual_word_count": sum(segment["word_count"] for segment in segments),
        "segments": segments,
        "warnings": list(raw.get("warnings", []) or []),
    }
    validate_recap_script(script, story_map)
    return script


def _normalized_prose(value: str) -> str:
    return " ".join(
        "".join(character if character.isalnum() else " " for character in value.casefold()).split()
    )


def _looks_copied_from_summary(text: str, beat: dict[str, Any]) -> bool:
    summary = _normalized_prose(str(beat.get("summary", "") or ""))
    narration = _normalized_prose(text)
    if _word_count(summary) < 6 or not narration:
        return False
    if summary == narration:
        return True
    if summary in narration and _word_count(narration) <= _word_count(summary) + 5:
        return True
    return SequenceMatcher(None, summary, narration).ratio() >= 0.92


def _validate_narration_originality(
    script: dict[str, Any],
    story_map: dict[str, Any],
    *,
    check_intro: bool,
) -> None:
    segments = script.get("segments", [])
    if check_intro and segments:
        opening = _normalized_prose(str(segments[0].get("text", "") or ""))
        generic_openings = (
            "in this episode",
            "welcome back",
            "today we",
            "here is a recap",
            "heres a recap",
        )
        if any(opening.startswith(value) for value in generic_openings):
            raise RecapWritingError("The narration opens with a generic introduction")
    beats = _beat_map(story_map, verified_only=True)
    for segment in segments:
        text = str(segment.get("text", "") or "")
        copied = [
            beat_id
            for beat_id in segment.get("beat_ids", [])
            if beat_id in beats and _looks_copied_from_summary(text, beats[beat_id])
        ]
        if copied:
            raise RecapWritingError(
                "Narration must synthesize verified evidence instead of copying "
                f"story-map summaries verbatim: {copied}"
            )


def _choice(
    raw: Any,
    field: str,
    verified_beats: dict[str, dict[str, Any]],
    *,
    required: bool,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError(f"outline.{field} must be an object")
    values = raw.get("beat_ids", [])
    if not isinstance(values, list):
        raise RecapWritingError(f"outline.{field}.beat_ids must be a list")
    beat_ids = list(dict.fromkeys(str(value) for value in values))
    invalid = [beat_id for beat_id in beat_ids if beat_id not in verified_beats]
    if invalid:
        raise RecapWritingError(
            f"outline.{field} references invalid beat IDs: {invalid}"
        )
    if required and not beat_ids:
        raise RecapWritingError(f"outline.{field} must choose at least one beat")
    return {
        "beat_ids": beat_ids,
        "intent": " ".join(str(raw.get("intent", "") or "").split()).strip(),
    }


def normalize_outline(
    raw: dict[str, Any],
    story_map: dict[str, Any],
    config: RecapWritingConfig,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError("Narrative outline response must be an object")
    verified_beats = _beat_map(story_map, verified_only=True)
    if not verified_beats:
        raise RecapWritingError("No verified beats are available for recap writing")

    outline: dict[str, Any] = {
        "hook": _choice(raw.get("hook"), "hook", verified_beats, required=True),
        "minimum_setup": _choice(
            raw.get("minimum_setup"),
            "minimum_setup",
            verified_beats,
            required=False,
        ),
        "reversal": _choice(
            raw.get("reversal"), "reversal", verified_beats, required=False
        ),
        "payoff_climax": _choice(
            raw.get("payoff_climax"),
            "payoff_climax",
            verified_beats,
            required=False,
        ),
        "resolution_button": _choice(
            raw.get("resolution_button"),
            "resolution_button",
            verified_beats,
            required=False,
        ),
    }
    for field in ("essential_causal_chain", "escalation_beats"):
        values = raw.get(field)
        if not isinstance(values, list):
            raise RecapWritingError(f"outline.{field} must be a list")
        outline[field] = [
            _choice(item, f"{field}[{index}]", verified_beats, required=True)
            for index, item in enumerate(values)
        ]
    if not outline["essential_causal_chain"]:
        raise RecapWritingError("outline.essential_causal_chain cannot be empty")

    payoff_beats = {
        beat_id
        for beat_id, beat in verified_beats.items()
        if any(
            token in str(beat.get("story_purpose", "") or "").casefold()
            for token in ("payoff", "climax")
        )
    }
    if payoff_beats and not (
        payoff_beats & set(outline["payoff_climax"]["beat_ids"])
    ):
        raise RecapWritingError(
            "outline.payoff_climax must include a verified payoff/climax beat"
        )

    outline["omitted_beat_ids"] = [
        str(value)
        for value in raw.get("omitted_beat_ids", [])
        if str(value) in verified_beats
    ]
    return outline


def _supported_range(
    candidate: dict[str, Any],
    sources: list[dict[str, Any]],
) -> bool:
    try:
        start = float(candidate.get("start"))
        end = float(candidate.get("end"))
    except (TypeError, ValueError):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        try:
            source_start = float(source.get("start"))
            source_end = float(source.get("end"))
        except (TypeError, ValueError):
            continue
        if abs(start - source_start) <= 0.001 and abs(end - source_end) <= 0.001:
            return True
    return False


def validate_script_quality_invariants(
    script: dict[str, Any],
    story_map: dict[str, Any],
    outline: dict[str, Any],
    config: RecapWritingConfig,
    *,
    allow_compact_protected_thoughts: bool = False,
) -> None:
    try:
        validate_recap_script(script, story_map)
    except RecapValidationError as exc:
        raise RecapWritingError(str(exc)) from exc
    segments = script.get("segments", [])
    if not segments:
        raise RecapWritingError("Recap writer returned no segments")

    _validate_narration_originality(script, story_map, check_intro=True)

    verified_beats = _beat_map(story_map, verified_only=True)
    represented: set[str] = set()
    for segment in segments:
        beat_ids = [str(value) for value in segment.get("beat_ids", [])]
        represented.update(beat_ids)
        evidence = [
            item
            for beat_id in beat_ids
            for item in verified_beats.get(beat_id, {}).get(
                "actual_video_evidence_ranges", []
            )
            if isinstance(item, dict)
        ]
        visuals = segment.get("candidate_visuals", [])
        if not visuals:
            raise RecapWritingError(
                f"{segment.get('segment_id')} needs candidate visuals"
            )
        for visual in visuals:
            if not _supported_range(visual, evidence):
                raise RecapWritingError(
                    f"{segment.get('segment_id')} has a candidate visual range "
                    "that is not present in its referenced verified beats"
                )
            if not str(visual.get("reason", "") or "").strip():
                raise RecapWritingError(
                    f"{segment.get('segment_id')} has a candidate visual without a reason"
                )

        dialogue_sources = [
            item
            for beat_id in beat_ids
            for item in _dialogue_sources_for_beat(verified_beats.get(beat_id, {}))
            if isinstance(item, dict)
        ]
        for dialogue in segment.get("original_dialogue_candidates", []):
            if not _supported_range(dialogue, dialogue_sources):
                raise RecapWritingError(
                    f"{segment.get('segment_id')} has an unsupported dialogue range"
                )

    words = int(script.get("actual_word_count", 0) or 0)
    evidence_scaled_minimum = min(
        config.minimum_word_count,
        max(1, len(verified_beats) * 16),
    )
    if words < evidence_scaled_minimum:
        raise RecapWritingError(
            f"Recap has {words} words; minimum sensible budget is "
            f"{evidence_scaled_minimum} for the available verified story material"
        )
    if words > config.maximum_word_count:
        raise RecapWritingError(
            f"Recap has {words} words; maximum budget is {config.maximum_word_count}"
        )

    first_ids = set(str(value) for value in segments[0].get("beat_ids", []))
    if not first_ids.intersection(outline["hook"]["beat_ids"]):
        raise RecapWritingError("The first narration segment does not ground the chosen hook")
    chain_ids = {
        beat_id
        for item in outline["essential_causal_chain"]
        for beat_id in item["beat_ids"]
    }
    missing_chain = sorted(chain_ids - represented)
    if missing_chain:
        raise RecapWritingError(
            f"Recap omits essential causal-chain beats: {missing_chain}"
        )
    payoff_ids = set(outline["payoff_climax"]["beat_ids"])
    if payoff_ids and not payoff_ids.intersection(represented):
        raise RecapWritingError("Recap omits the selected payoff/climax")
    if payoff_ids and config.minimum_word_count >= 100:
        payoff_words = sum(
            int(segment.get("word_count", 0) or 0)
            for segment in segments
            if payoff_ids.intersection(segment.get("beat_ids", []))
        )
        payoff_floor = 24
        if allow_compact_protected_thoughts:
            protected_thoughts = [
                item
                for item in outline.get("planned_segments", [])
                if str(item.get("function", ""))
                in {"reversal_payoff", "payoff_climax"}
                and payoff_ids.intersection(item.get("beat_ids", []))
            ]
            if protected_thoughts:
                planned_floor = min(
                    int(item.get("word_range", [24])[0] or 24)
                    for item in protected_thoughts
                )
                payoff_floor = max(12, planned_floor // 4)
        if payoff_words < payoff_floor:
            raise RecapWritingError(
                "The selected payoff/climax is underdeveloped; it needs at least "
                f"{payoff_floor} grounded narration words"
            )

    importance_values = {
        round(float(segment.get("importance", 0.5) or 0.5), 3)
        for segment in segments
    }
    beat_importance_values = {
        round(float(beat.get("importance", 0.5) or 0.5), 3)
        for beat in verified_beats.values()
    }
    if len(segments) >= 4 and len(beat_importance_values) > 1 and len(importance_values) == 1:
        raise RecapWritingError(
            "Narration importance is uniform despite varied verified beat importance"
        )


def normalize_critique(
    raw: dict[str, Any],
    expected_segment_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError("Quality critique response must be an object")
    if not isinstance(raw.get("passes"), bool):
        raise RecapWritingError("Quality critique needs a boolean passes field")
    raw_grounding = raw.get("segment_grounding")
    if not isinstance(raw_grounding, list):
        raise RecapWritingError("Quality critique needs segment_grounding")
    grounding: list[dict[str, Any]] = []
    seen_grounding: set[str] = set()
    for index, item in enumerate(raw_grounding):
        if not isinstance(item, dict):
            raise RecapWritingError(f"Segment grounding {index} must be an object")
        segment_id = str(item.get("segment_id", "") or "").strip()
        if not segment_id or segment_id in seen_grounding:
            raise RecapWritingError(
                "Segment grounding IDs must be present and unique"
            )
        if not isinstance(item.get("supported"), bool):
            raise RecapWritingError(
                f"Segment grounding {segment_id} needs a boolean supported field"
            )
        claims = item.get("unsupported_claims", [])
        if not isinstance(claims, list):
            raise RecapWritingError(
                f"Segment grounding {segment_id}.unsupported_claims must be a list"
            )
        normalized_claims = [
            " ".join(str(value).split()).strip()
            for value in claims
            if " ".join(str(value).split()).strip()
        ]
        if not item["supported"] and not normalized_claims:
            normalized_claims = [
                "The critic marked this segment unsupported without itemizing the claim."
            ]
        seen_grounding.add(segment_id)
        grounding.append(
            {
                "segment_id": segment_id,
                "supported": item["supported"],
                "unsupported_claims": normalized_claims,
            }
        )
    expected = set(expected_segment_ids or [])
    if expected and seen_grounding != expected:
        missing = sorted(expected - seen_grounding)
        extra = sorted(seen_grounding - expected)
        raise RecapWritingError(
            f"Segment grounding coverage mismatch; missing={missing}, extra={extra}"
        )
    raw_issues = raw.get("issues", [])
    if not isinstance(raw_issues, list):
        raise RecapWritingError("Quality critique issues must be a list")
    issues: list[dict[str, Any]] = []
    for index, issue in enumerate(raw_issues):
        if not isinstance(issue, dict):
            raise RecapWritingError(f"Quality issue {index} must be an object")
        message = " ".join(str(issue.get("message", "") or "").split()).strip()
        if not message:
            raise RecapWritingError(f"Quality issue {index} needs a message")
        severity = str(issue.get("severity", "major") or "major").casefold()
        if severity not in {"minor", "major"}:
            raise RecapWritingError(f"Quality issue {index} has invalid severity")
        issues.append(
            {
                "category": str(issue.get("category", "quality") or "quality"),
                "severity": severity,
                "message": message,
                "segment_ids": [
                    str(value) for value in issue.get("segment_ids", []) or []
                ],
            }
        )
    passes = raw["passes"]
    if any(issue["severity"] == "major" for issue in issues) or any(
        not item["supported"] for item in grounding
    ):
        passes = False
    if not passes and not issues:
        raise RecapWritingError("A failing quality critique must explain its issues")
    instructions = raw.get("revision_instructions", [])
    if not isinstance(instructions, list):
        raise RecapWritingError("revision_instructions must be a list")
    return {
        "passes": passes,
        "segment_grounding": grounding,
        "issues": issues,
        "revision_instructions": [
            " ".join(str(value).split()).strip()
            for value in instructions
            if " ".join(str(value).split()).strip()
        ],
    }


class RecapWriter:
    """Build a selected story spine, draft it, validate it, and revise it."""

    prompt_version = WRITER_PROMPT_VERSION

    def __init__(
        self,
        model: JsonModel,
        *,
        prompt_path: Path = DEFAULT_PROMPT_PATH,
        outline_prompt_path: Path = DEFAULT_OUTLINE_PROMPT_PATH,
        critic_prompt_path: Path = DEFAULT_CRITIC_PROMPT_PATH,
        repair_prompt_path: Path = DEFAULT_REPAIR_PROMPT_PATH,
        config: RecapWritingConfig = RecapWritingConfig(),
        debug_dir: Path | None = None,
    ):
        self.model = model
        self.prompt_path = prompt_path
        self.outline_prompt_path = outline_prompt_path
        self.critic_prompt_path = critic_prompt_path
        self.repair_prompt_path = repair_prompt_path
        self.config = config
        self.debug_dir = debug_dir
        self.last_diagnostics: dict[str, Any] = {}

    @property
    def model_version(self) -> str:
        name = str(getattr(self.model, "model", "") or "").strip()
        return f"ollama:{name}" if name else type(self.model).__name__

    def cache_identity(self) -> tuple[str, str]:
        digest = hashlib.sha256()
        for path in (
            self.outline_prompt_path,
            self.prompt_path,
            self.critic_prompt_path,
            self.repair_prompt_path,
        ):
            digest.update(path.read_bytes())
        prompt_identity = f"{self.prompt_version}:{digest.hexdigest()[:16]}"
        return prompt_identity, self.model_version

    def set_debug_dir(self, path: Path) -> None:
        self.debug_dir = path

    @staticmethod
    def _uses_rich_fast_path(story_map: dict[str, Any]) -> bool:
        research_depth = story_map.get("research_depth", {})
        return (
            isinstance(research_depth, dict)
            and str(research_depth.get("level", "")).upper() == "RICH"
        )

    @staticmethod
    def _read_prompt(path: Path) -> str:
        return path.read_text(encoding="utf-8").strip()

    def build_prompt(self, story_map: dict[str, Any]) -> str:
        """Retain a useful public prompt builder for callers and diagnostics."""
        return (
            self._read_prompt(self.prompt_path)
            + "\n\nVERIFIED STORY MAP:\n"
            + json.dumps(_story_payload(story_map), indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    def _outline_prompt(self, story_map: dict[str, Any]) -> str:
        verified_beat_ids = list(_beat_map(story_map, verified_only=True))
        return (
            self._read_prompt(self.outline_prompt_path)
            + "\n\nTARGET:\n"
            + json.dumps(
                {
                    "duration_seconds": self.config.target_duration_seconds,
                    "target_word_count": self.config.target_word_count,
                },
                indent=2,
            )
            + "\n\nVALID VERIFIED BEAT IDS (the only allowed IDs):\n"
            + json.dumps(verified_beat_ids)
            + "\n\nVERIFIED STORY MAP:\n"
            + json.dumps(_story_payload(story_map), indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    def _draft_prompt(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
        *,
        prior_script: dict[str, Any] | None = None,
        critique: dict[str, Any] | None = None,
    ) -> str:
        if prior_script is not None and critique is not None:
            compact_beats = [
                {
                    "beat_id": beat.get("beat_id"),
                    "summary": beat.get("summary"),
                    "story_purpose": beat.get("story_purpose"),
                    "characters": beat.get("characters", []),
                    "motivation": beat.get("motivation"),
                    "change": beat.get("change"),
                    "emotional_conflict": beat.get("emotional_conflict"),
                    "payoff_significance": beat.get("payoff_significance"),
                    "causal_reasoning": beat.get("causal_reasoning", []),
                    "evidence_excerpts": [
                        str(item.get("transcript_excerpt", "") or "")
                        for item in beat.get("actual_video_evidence_ranges", []) or []
                        if isinstance(item, dict)
                        and str(item.get("transcript_excerpt", "") or "").strip()
                    ],
                }
                for beat in _ordered_verified_beats(story_map)
            ]
            compact_script = {
                "actual_word_count": prior_script.get("actual_word_count"),
                "segments": [
                    {
                        "segment_id": segment.get("segment_id"),
                        "text": segment.get("text"),
                        "beat_ids": segment.get("beat_ids"),
                    }
                    for segment in prior_script.get("segments", [])
                ],
            }
            return (
                self._read_prompt(self.prompt_path)
                + "\n\nREVISION TASK: Return the complete revised narration. "
                "Resolve every major issue while preserving grounded facts, beat "
                "references, the payoff, and the 280-330 word target.\n\n"
                + "QUALITY CRITIQUE:\n"
                + json.dumps(critique, indent=2, ensure_ascii=False)
                + "\n\nCURRENT SCRIPT:\n"
                + json.dumps(compact_script, indent=2, ensure_ascii=False)
                + "\n\nCOMPACT VERIFIED STORY EVIDENCE:\n"
                + json.dumps(compact_beats, indent=2, ensure_ascii=False)
                + "\n\nReturn JSON only."
            )
        verified_beat_ids = list(_beat_map(story_map, verified_only=True))
        prompt = (
            self._read_prompt(self.prompt_path)
            + "\n\nSELECTED NARRATIVE OUTLINE:\n"
            + json.dumps(outline, indent=2, ensure_ascii=False)
            + "\n\nVALID VERIFIED BEAT IDS (the only allowed IDs):\n"
            + json.dumps(verified_beat_ids)
            + "\n\nVERIFIED STORY MAP:\n"
            + json.dumps(_story_payload(story_map), indent=2, ensure_ascii=False)
        )
        return prompt + "\n\nReturn JSON only."

    def _rich_main_narration_prompt(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
    ) -> str:
        return (
            "Write original, grounded recap narration using only the factual "
            "material in the authoritative plan below. Do not copy summaries "
            "verbatim or invent facts, motives, dialogue, stakes, or transitions. "
            "Keep each thought understandable as audio-only narration and stay near "
            "its supplied word range.\n\nRICH AUTHORITATIVE NARRATION PLAN:\n"
            + json.dumps(
                _rich_thought_payload(story_map, outline),
                indent=2,
                ensure_ascii=False,
            )
            + "\n\nFor this RICH task, the response contract below overrides "
            "generic instructions about segment IDs, beat IDs, visuals, dialogue, "
            "importance, and presentation hints. Write only the narration text for "
            "each supplied plan_id. Do not add, remove, reorder, split, merge, or "
            "reinterpret planned thoughts.\n\nRICH NARRATION RESPONSE CONTRACT:\n"
            + "Return ONLY one JSON object with this top-level shape:\n"
            + json.dumps(
                {
                    "narration": [
                        {
                            "plan_id": "P01",
                            "text": "Original grounded narration.",
                        }
                    ]
                },
                indent=2,
            )
            + "\nThe top-level key must be narration. Return exactly one item for "
            "every supplied plan_id, in the supplied order. Each item may contain "
            "only plan_id and text. Do not return segments, story beats, beat IDs, "
            "an outline, a story map, visuals, dialogue, importance, or presentation hints."
        )

    def _section_prompt(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
        section: dict[str, Any],
    ) -> str:
        beats = _beat_map(story_map, verified_only=True)
        section_story = {
            **_story_payload(story_map),
            "verified_beats": [
                beats[beat_id]
                for beat_id in section["beat_ids"]
                if beat_id in beats
            ],
        }
        return (
            self._read_prompt(self.prompt_path)
            + "\n\nDETERMINISTIC NARRATION PLAN:\n"
            + json.dumps(
                {
                    "hook": outline["hook"],
                    "minimum_setup": outline["minimum_setup"],
                    "reversal": outline["reversal"],
                    "payoff_climax": outline["payoff_climax"],
                    "resolution_button": outline["resolution_button"],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n\nSECTION TO WRITE:\n"
            + json.dumps(section, indent=2, ensure_ascii=False)
            + "\n\nVERIFIED EVIDENCE FOR THIS SECTION:\n"
            + json.dumps(section_story, indent=2, ensure_ascii=False)
            + "\n\nWrite every planned segment. Stay near each segment's word_range. "
            "Return exactly one narration segment for each planned_segments item, "
            "in that order, with beat_ids exactly matching that item. Do not split "
            "a planned multi-beat thought into one segment per event. "
            "Synthesize across summary, purpose, motivation, change, emotional "
            "conflict, payoff significance, and evidence. Do not copy summary "
            "sentences. Use only this section's beat IDs. Return JSON only."
        )

    def _expansion_prompt(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
        script: dict[str, Any],
        minimum_words: int,
        budget_deficits: list[dict[str, Any]],
        *,
        patch_only: bool,
    ) -> str:
        beats = _beat_map(story_map, verified_only=True)
        target_ids = {
            str(beat_id)
            for item in budget_deficits
            for beat_id in item.get("beat_ids", [])
        }
        focus_ids = set(target_ids)
        if not patch_only and any(
            item.get("function") == "complete_script" for item in budget_deficits
        ):
            focus_ids.update(beats)
        if not patch_only:
            for beat_id in list(focus_ids):
                beat = beats.get(beat_id, {})
                focus_ids.update(
                    str(value) for value in beat.get("causal_parents", []) or []
                )
                focus_ids.update(
                    str(value) for value in beat.get("causal_children", []) or []
                )
            for field in ("reversal", "payoff_climax", "resolution_button"):
                focus_ids.update(outline.get(field, {}).get("beat_ids", []))

        fields = (
            "beat_id",
            "summary",
            "story_purpose",
            "characters",
            "importance",
            "motivation",
            "change",
            "emotional_conflict",
            "payoff_significance",
            "causal_parents",
            "causal_children",
            "causal_reasoning",
        )
        focus_beats = [
            {field: beat.get(field) for field in fields if field in beat}
            for beat_id, beat in beats.items()
            if beat_id in focus_ids
        ]
        compact_script = {
            "actual_word_count": script.get("actual_word_count"),
            "segments": [
                {
                    "segment_id": segment.get("segment_id"),
                    **(
                        {}
                        if patch_only
                        else {"text": segment.get("text")}
                    ),
                    "beat_ids": segment.get("beat_ids"),
                    "presentation_hint": segment.get("presentation_hint"),
                }
                for segment in script.get("segments", [])
                if not patch_only
                or target_ids.intersection(segment.get("beat_ids", []))
            ],
        }
        deficit = max(0, minimum_words - int(script.get("actual_word_count", 0) or 0))
        additional_words = max(
            [
                int(item.get("minimum_words", 0) or 0)
                - int(item.get("actual_words", 0) or 0)
                for item in budget_deficits
            ]
            or [deficit]
        )
        task_instructions = (
            [
                "Return only new additive segments for the deficit beat IDs.",
                "Do not rewrite or repeat the existing script.",
                "Add one compact grounded thought that develops unexpressed meaning.",
            ]
            if patch_only
            else ["Return the complete revised script, not a patch."]
        )
        return (
            self._read_prompt(self.prompt_path)
            + "\n\nTARGETED BUDGET EXPANSION TASK:\n"
            + json.dumps(
                {
                    "current_words": script.get("actual_word_count"),
                    "minimum_words": minimum_words,
                    "deficit_words": deficit,
                    "minimum_additional_words": max(10, additional_words),
                    "mode": "additive_segments" if patch_only else "complete_script",
                    "only_valid_beat_ids": sorted(target_ids) if patch_only else sorted(beats),
                    "story_function_deficits": budget_deficits,
                    "preferred_total_words": min(
                        self.config.maximum_word_count,
                        max(minimum_words, self.config.target_word_count),
                    ),
                    "instructions": [
                        *task_instructions,
                        "Expand only with unexpressed verified causal, emotional, or payoff information.",
                        "Protect the reversal and payoff budget.",
                        "Do not repeat a fact merely to add words.",
                    ],
                },
                indent=2,
            )
            + "\n\nDETERMINISTIC NARRATION PLAN:\n"
            + json.dumps(
                (
                    {"deficit_beat_ids": sorted(target_ids)}
                    if patch_only
                    else {
                        "hook": outline["hook"],
                        "reversal": outline["reversal"],
                        "payoff_climax": outline["payoff_climax"],
                        "resolution_button": outline["resolution_button"],
                    }
                ),
                indent=2,
                ensure_ascii=False,
            )
            + "\n\nCURRENT SCRIPT:\n"
            + json.dumps(compact_script, indent=2, ensure_ascii=False)
            + "\n\nVERIFIED MATERIAL RELEVANT TO THE DEFICIT:\n"
            + json.dumps(focus_beats, indent=2, ensure_ascii=False)
            + "\n\nThe top-level response must be an object with a non-empty "
            "segments array. Return JSON only."
        )

    def _validate_expansion_patch(
        self,
        raw: dict[str, Any],
        story_map: dict[str, Any],
        budget_deficits: list[dict[str, Any]],
        existing_script: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            str(beat_id)
            for item in budget_deficits
            for beat_id in item.get("beat_ids", [])
        }
        normalized_raw = dict(raw) if isinstance(raw, dict) else raw
        if isinstance(normalized_raw, dict) and isinstance(
            normalized_raw.get("segments"), list
        ):
            normalized_raw["segments"] = [
                {**segment, "segment_id": f"PATCH_{index:03d}"}
                if isinstance(segment, dict)
                else segment
                for index, segment in enumerate(normalized_raw["segments"], start=1)
            ]
        script = normalize_script(normalized_raw, story_map, self.config)
        scoped_segments = [
            segment
            for segment in script["segments"]
            if set(segment["beat_ids"]).issubset(allowed)
        ]
        existing_texts = [
            _normalized_prose(str(segment.get("text", "") or ""))
            for segment in existing_script.get("segments", [])
        ]
        scoped_segments = [
            segment
            for segment in scoped_segments
            if not any(
                SequenceMatcher(
                    None,
                    _normalized_prose(segment["text"]),
                    existing,
                ).ratio()
                >= 0.9
                for existing in existing_texts
                if existing
            )
        ]
        verified_beats = _beat_map(story_map, verified_only=True)
        scoped_segments = [
            segment
            for segment in scoped_segments
            if not any(
                _looks_copied_from_summary(
                    segment["text"],
                    verified_beats.get(beat_id, {}),
                )
                for beat_id in segment["beat_ids"]
            )
        ]
        if not scoped_segments:
            raise RecapWritingError(
                "Expansion patch returned no new non-duplicative segment scoped "
                "to the deficit beats"
            )
        script = normalize_script(
            raw_segments_payload(
                [
                    {
                        "segment_id": f"ADD_{index:03d}",
                        "text": segment["text"],
                        "beat_ids": segment["beat_ids"],
                        "presentation_hint": segment["presentation_hint"],
                    }
                    for index, segment in enumerate(scoped_segments, start=1)
                ]
            ),
            story_map,
            self.config,
        )
        represented = {
            str(beat_id)
            for segment in script["segments"]
            for beat_id in segment["beat_ids"]
        }
        uncovered = [
            str(item.get("function", "deficit"))
            for item in budget_deficits
            if item.get("function") in {"reversal_payoff", "payoff_climax"}
            and not represented.intersection(item.get("beat_ids", []))
        ]
        if uncovered:
            raise RecapWritingError(
                "Expansion patch omits required deficit functions: "
                + ", ".join(uncovered)
            )
        required_addition = max(
            10,
            max(
                max(
                    0,
                    int(item.get("minimum_words", 0) or 0)
                    - int(item.get("actual_words", 0) or 0)
                    - (10 if item.get("function") == "narration_target" else 0),
                )
                for item in budget_deficits
            ),
        )
        if script["actual_word_count"] < required_addition:
            raise RecapWritingError(
                f"Expansion patch has {script['actual_word_count']} words; "
                f"at least {required_addition} new grounded words are required"
            )
        current_totals = [
            int(item.get("actual_words", 0) or 0)
            for item in budget_deficits
            if item.get("function") in {"complete_script", "narration_target"}
        ]
        if current_totals:
            maximum_addition = max(1, self.config.maximum_word_count - max(current_totals))
            if script["actual_word_count"] > maximum_addition:
                raise RecapWritingError(
                    f"Expansion patch has {script['actual_word_count']} words; "
                    f"at most {maximum_addition} can be added without exceeding "
                    "the script budget"
                )
        _validate_narration_originality(script, story_map, check_intro=False)
        return script

    def _append_expansion(
        self,
        script: dict[str, Any],
        patch: dict[str, Any],
        story_map: dict[str, Any],
    ) -> dict[str, Any]:
        raw_segments: list[dict[str, Any]] = []
        pending = list(patch["segments"])
        for segment in script["segments"]:
            raw_segments.append(
                {
                    "text": segment["text"],
                    "beat_ids": segment["beat_ids"],
                    "presentation_hint": segment["presentation_hint"],
                }
            )
            matching = [
                addition
                for addition in pending
                if set(addition["beat_ids"]).intersection(segment["beat_ids"])
            ]
            for addition in matching:
                raw_segments.append(
                    {
                        "text": addition["text"],
                        "beat_ids": addition["beat_ids"],
                        "presentation_hint": addition["presentation_hint"],
                    }
                )
                pending.remove(addition)
        raw_segments.extend(
            {
                "text": addition["text"],
                "beat_ids": addition["beat_ids"],
                "presentation_hint": addition["presentation_hint"],
            }
            for addition in pending
        )
        for index, segment in enumerate(raw_segments, start=1):
            segment["segment_id"] = f"VO_{index:03d}"
        return normalize_script(raw_segments_payload(raw_segments), story_map, self.config)

    def _validate_section(
        self,
        raw: dict[str, Any],
        story_map: dict[str, Any],
        outline: dict[str, Any],
        section: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_raw = dict(raw) if isinstance(raw, dict) else raw
        if isinstance(normalized_raw, dict) and isinstance(
            normalized_raw.get("segments"), list
        ):
            normalized_raw["segments"] = [
                {
                    **segment,
                    "segment_id": f"SECTION_{index:03d}",
                }
                if isinstance(segment, dict)
                else segment
                for index, segment in enumerate(
                    normalized_raw["segments"],
                    start=1,
                )
            ]
        script = normalize_script(normalized_raw, story_map, self.config)
        if self.config.minimum_word_count >= 100:
            coalesced: list[dict[str, Any]] = []
            source_segments = list(script["segments"])
            for planned in section.get("planned_segments", []):
                planned_ids = set(planned.get("beat_ids", []))
                fragments = [
                    segment
                    for segment in source_segments
                    if set(segment["beat_ids"]).issubset(planned_ids)
                    and planned_ids.intersection(segment["beat_ids"])
                ]
                if fragments:
                    coalesced.append(
                        {
                            "segment_id": f"VO_{len(coalesced) + 1:03d}",
                            "text": " ".join(fragment["text"] for fragment in fragments),
                            "beat_ids": list(planned.get("beat_ids", [])),
                            "presentation_hint": "narration_over_source",
                        }
                    )
            if coalesced:
                script = normalize_script(
                    raw_segments_payload(coalesced),
                    story_map,
                    self.config,
                )
        allowed = set(section["beat_ids"])
        represented: set[str] = set()
        for segment in script["segments"]:
            ids = set(segment["beat_ids"])
            invalid = sorted(ids - allowed)
            if invalid:
                raise RecapWritingError(
                    f"Section {section['section_id']} references out-of-section beats: {invalid}"
                )
            represented.update(ids)
        missing = sorted(allowed - represented)
        if missing:
            raise RecapWritingError(
                f"Section {section['section_id']} omits planned beats: {missing}"
            )
        if self.config.minimum_word_count >= 100:
            budget_errors: list[str] = []
            for planned in section.get("planned_segments", []):
                planned_ids = set(planned.get("beat_ids", []))
                matching = [
                    segment
                    for segment in script["segments"]
                    if set(segment["beat_ids"]) == planned_ids
                ]
                if len(matching) != 1:
                    budget_errors.append(
                        f"Planned thought {planned.get('plan_id')} must be exactly "
                        "one narration segment with beat_ids "
                        f"{sorted(planned_ids)}; found {len(matching)}"
                    )
                    continue
                planned_minimum = int(planned.get("word_range", [1])[0] or 1)
                minimum = max(10, planned_minimum // 4)
                if matching[0]["word_count"] < minimum:
                    budget_errors.append(
                        f"Planned thought {planned.get('plan_id')} has "
                        f"{matching[0]['word_count']} words; its grounded allocation "
                        f"requires at least {minimum}"
                    )
            if budget_errors:
                raise RecapWritingError("; ".join(budget_errors))
        starts_story = bool(allowed.intersection(outline["hook"]["beat_ids"]))
        _validate_narration_originality(
            script,
            story_map,
            check_intro=starts_story,
        )
        if starts_story:
            first_ids = set(script["segments"][0]["beat_ids"])
            if not first_ids.intersection(outline["hook"]["beat_ids"]):
                raise RecapWritingError(
                    "The opening section must begin on the selected hook beat"
                )
        return script

    def _assemble_sections(
        self,
        scripts: list[dict[str, Any]],
        story_map: dict[str, Any],
    ) -> dict[str, Any]:
        raw_segments: list[dict[str, Any]] = []
        for script in scripts:
            for segment in script.get("segments", []):
                raw_segments.append(
                    {
                        "segment_id": f"VO_{len(raw_segments) + 1:03d}",
                        "text": segment["text"],
                        "beat_ids": segment["beat_ids"],
                        "presentation_hint": segment.get(
                            "presentation_hint", "narration_over_source"
                        ),
                    }
                )
        return normalize_script(raw_segments_payload(raw_segments), story_map, self.config)

    def _minimum_words(self, story_map: dict[str, Any]) -> int:
        return min(
            self.config.minimum_word_count,
            max(1, len(_beat_map(story_map, verified_only=True)) * 16),
        )

    def _budget_deficits(
        self,
        script: dict[str, Any],
        story_map: dict[str, Any],
        outline: dict[str, Any],
    ) -> list[dict[str, Any]]:
        deficits: list[dict[str, Any]] = []
        actual_words = int(script.get("actual_word_count", 0) or 0)
        minimum_words = self._minimum_words(story_map)
        if actual_words < minimum_words:
            selected_ids = list(
                dict.fromkeys(
                    beat_id
                    for item in outline.get("planned_segments", [])
                    for beat_id in item.get("beat_ids", [])
                )
            )
            deficits.append(
                {
                    "function": "complete_script",
                    "beat_ids": selected_ids,
                    "actual_words": actual_words,
                    "minimum_words": minimum_words,
                }
            )
        if self.config.minimum_word_count < 100:
            return deficits

        for item in outline.get("planned_segments", []):
            function = str(item.get("function", "") or "")
            if function not in {"escalation", "reversal_payoff"}:
                continue
            beat_ids = set(item.get("beat_ids", []))
            section_words = sum(
                int(segment.get("word_count", 0) or 0)
                for segment in script.get("segments", [])
                if beat_ids.intersection(segment.get("beat_ids", []))
            )
            planned_floor = int(item.get("word_range", [0])[0] or 0)
            # The per-thought ranges direct generation. Expansion is reserved
            # for material misses, not a few harmless words of variance.
            material_floor = 24 if function == "reversal_payoff" else max(20, planned_floor // 2)
            if section_words < material_floor:
                deficits.append(
                    {
                        "function": function,
                        "beat_ids": sorted(beat_ids),
                        "actual_words": section_words,
                        "minimum_words": material_floor,
                        "planned_range": item.get("word_range", []),
                    }
                )
        payoff_ids = set(outline.get("payoff_climax", {}).get("beat_ids", []))
        if payoff_ids:
            payoff_words = sum(
                int(segment.get("word_count", 0) or 0)
                for segment in script.get("segments", [])
                if payoff_ids.intersection(segment.get("beat_ids", []))
            )
            if payoff_words < 24 and not any(
                item.get("function") == "payoff_climax" for item in deficits
            ):
                deficits.append(
                    {
                        "function": "payoff_climax",
                        "beat_ids": sorted(payoff_ids),
                        "actual_words": payoff_words,
                        "minimum_words": 24,
                    }
                )
        preferred_floor = min(
            self.config.maximum_word_count,
            max(minimum_words, self.config.target_word_count - 30),
        )
        if len(_beat_map(story_map, verified_only=True)) >= 6 and actual_words < preferred_floor:
            focus_ids = list(
                dict.fromkeys(
                    beat_id
                    for item in outline.get("planned_segments", [])
                    for beat_id in item.get("beat_ids", [])
                )
            )
            if not focus_ids:
                focus_ids = list(outline.get("hook", {}).get("beat_ids", []))
            deficits.append(
                {
                    "function": "narration_target",
                    "beat_ids": focus_ids,
                    "actual_words": actual_words,
                    "minimum_words": preferred_floor,
                }
            )
        return deficits

    def _critique_prompt(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
        script: dict[str, Any],
    ) -> str:
        critique_script = {
            "actual_word_count": script.get("actual_word_count"),
            "voice_style": script.get("voice_style"),
            "segments": [
                {
                    "segment_id": segment.get("segment_id"),
                    "text": segment.get("text"),
                    "beat_ids": segment.get("beat_ids"),
                }
                for segment in script.get("segments", [])
            ],
        }
        compact_beats = []
        for beat in _ordered_verified_beats(story_map):
            compact_beats.append(
                {
                    "beat_id": beat.get("beat_id"),
                    "summary": beat.get("summary"),
                    "story_purpose": beat.get("story_purpose"),
                    "motivation": beat.get("motivation"),
                    "change": beat.get("change"),
                    "emotional_conflict": beat.get("emotional_conflict"),
                    "payoff_significance": beat.get("payoff_significance"),
                    "causal_reasoning": beat.get("causal_reasoning", []),
                    "evidence_excerpts": [
                        str(item.get("transcript_excerpt", "") or "")
                        for item in beat.get("actual_video_evidence_ranges", []) or []
                        if isinstance(item, dict)
                        and str(item.get("transcript_excerpt", "") or "").strip()
                    ],
                }
            )
        compact_outline = {
            field: outline[field]
            for field in (
                "hook",
                "minimum_setup",
                "essential_causal_chain",
                "reversal",
                "payoff_climax",
                "resolution_button",
            )
        }
        return (
            self._read_prompt(self.critic_prompt_path)
            + "\n\nSELECTED NARRATIVE OUTLINE:\n"
            + json.dumps(compact_outline, indent=2, ensure_ascii=False)
            + "\n\nCOMPACT VERIFIED STORY EVIDENCE:\n"
            + json.dumps(compact_beats, indent=2, ensure_ascii=False)
            + "\n\nRECAP SCRIPT:\n"
            + json.dumps(critique_script, indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    @staticmethod
    def _revision_target_ids(critique: dict[str, Any]) -> list[str]:
        ids = [
            str(segment_id)
            for issue in critique.get("issues", [])
            if issue.get("severity") == "major"
            for segment_id in issue.get("segment_ids", [])
        ]
        ids.extend(
            str(item.get("segment_id"))
            for item in critique.get("segment_grounding", [])
            if not item.get("supported", True)
        )
        return list(dict.fromkeys(value for value in ids if value))

    def _revision_prompt(
        self,
        story_map: dict[str, Any],
        script: dict[str, Any],
        critique: dict[str, Any],
        target_ids: list[str],
    ) -> str:
        targets = [
            {
                "segment_id": segment["segment_id"],
                "text": segment["text"],
                "beat_ids": segment["beat_ids"],
            }
            for segment in script["segments"]
            if segment["segment_id"] in target_ids
        ]
        beat_ids = {
            str(beat_id)
            for segment in targets
            for beat_id in segment["beat_ids"]
        }
        evidence = [
            {
                "beat_id": beat.get("beat_id"),
                "summary": beat.get("summary"),
                "story_purpose": beat.get("story_purpose"),
                "characters": beat.get("characters", []),
                "motivation": beat.get("motivation"),
                "change": beat.get("change"),
                "emotional_conflict": beat.get("emotional_conflict"),
                "payoff_significance": beat.get("payoff_significance"),
                "causal_reasoning": beat.get("causal_reasoning", []),
                "evidence_excerpts": [
                    str(item.get("transcript_excerpt", "") or "")
                    for item in beat.get("actual_video_evidence_ranges", []) or []
                    if isinstance(item, dict)
                    and str(item.get("transcript_excerpt", "") or "").strip()
                ],
            }
            for beat in _ordered_verified_beats(story_map)
            if str(beat.get("beat_id")) in beat_ids
        ]
        forbidden_summaries = {
            str(beat.get("beat_id")): str(beat.get("summary", "") or "")
            for beat in _ordered_verified_beats(story_map)
            if str(beat.get("beat_id")) in beat_ids
            and str(beat.get("summary", "") or "").strip()
        }
        return (
            self._read_prompt(self.prompt_path)
            + "\n\nTARGETED REVISION TASK: Rewrite only the listed segments. "
            "Return exactly one segment for each listed segment_id, preserve each "
            "segment_id and its exact beat_ids, and resolve every cited issue. Do "
            "not return unaffected segments. Keep roughly the same word count per "
            "segment and do not add unsupported interpretation. None of the "
            "forbidden summary sentences may appear verbatim; change the syntax "
            "and synthesize from the other verified fields.\n\n"
            + "QUALITY CRITIQUE:\n"
            + json.dumps(critique, indent=2, ensure_ascii=False)
            + "\n\nSEGMENTS TO REVISE:\n"
            + json.dumps(targets, indent=2, ensure_ascii=False)
            + "\n\nFORBIDDEN VERBATIM SUMMARY SENTENCES:\n"
            + json.dumps(forbidden_summaries, indent=2, ensure_ascii=False)
            + "\n\nVERIFIED EVIDENCE FOR THOSE SEGMENTS:\n"
            + json.dumps(evidence, indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    def _validate_revision_patch(
        self,
        raw: dict[str, Any],
        story_map: dict[str, Any],
        script: dict[str, Any],
        critique: dict[str, Any],
        target_ids: list[str],
    ) -> dict[str, Any]:
        patch = normalize_script(raw, story_map, self.config)
        expected = {
            segment["segment_id"]: segment
            for segment in script["segments"]
            if segment["segment_id"] in target_ids
        }
        actual = {segment["segment_id"]: segment for segment in patch["segments"]}
        if set(actual) != set(expected):
            raise RecapWritingError(
                "Revision patch segment IDs must exactly match targets; "
                f"expected={sorted(expected)}, actual={sorted(actual)}"
            )
        patch = normalize_script(
            raw_segments_payload(
                [
                    {
                        "segment_id": segment_id,
                        "text": actual[segment_id]["text"],
                        "beat_ids": expected[segment_id]["beat_ids"],
                        "presentation_hint": actual[segment_id][
                            "presentation_hint"
                        ],
                    }
                    for segment_id in target_ids
                ]
            ),
            story_map,
            self.config,
        )
        _validate_narration_originality(
            patch,
            story_map,
            check_intro="VO_001" in actual,
        )
        return patch

    def _merge_revision_patch(
        self,
        script: dict[str, Any],
        patch: dict[str, Any],
        story_map: dict[str, Any],
    ) -> dict[str, Any]:
        replacements = {
            segment["segment_id"]: segment for segment in patch["segments"]
        }
        segments = []
        for segment in script["segments"]:
            replacement = replacements.get(segment["segment_id"], segment)
            segments.append(
                {
                    "segment_id": segment["segment_id"],
                    "text": replacement["text"],
                    "beat_ids": segment["beat_ids"],
                    "presentation_hint": replacement.get(
                        "presentation_hint", segment["presentation_hint"]
                    ),
                }
            )
        return normalize_script(raw_segments_payload(segments), story_map, self.config)

    def _repair_prompt(
        self,
        *,
        stage: str,
        original_prompt: str,
        generation: ModelGeneration,
        errors: list[str],
    ) -> str:
        return (
            f"RICH NARRATION REPAIR. STAGE: {stage}\n"
            + "\nVALIDATION ERRORS:\n"
            + json.dumps(errors, indent=2, ensure_ascii=False)
            + "\n\nINVALID RESPONSE:\n"
            + generation.raw_text
            + "\n\nORIGINAL TASK AND SOURCE OF TRUTH:\n"
            + original_prompt
            + "\n\nRepair every listed error. If narration copied a summary, rebuild "
            "that thought from the beat's purpose, motivation, change, conflict, "
            "payoff significance, and evidence; do not preserve the copied "
            "sentence structure or merely swap synonyms."
            + "\n\nReturn the repaired JSON object only."
        )

    def _rich_repair_prompt(
        self,
        *,
        stage: str,
        original_prompt: str,
        generation: ModelGeneration,
        errors: list[str],
    ) -> str:
        return (
            self._read_prompt(self.repair_prompt_path)
            + f"\n\nSTAGE: {stage}\n"
            + "\nVALIDATION ERRORS:\n"
            + json.dumps(errors, indent=2, ensure_ascii=False)
            + "\n\nINVALID RESPONSE:\n"
            + generation.raw_text
            + "\n\nAUTHORITATIVE RICH PLAN:\n"
            + original_prompt
            + "\n\nRepair only the missing or invalid narration text items. Keep valid "
            "plan_id/text pairs unchanged. Return the complete narration array in "
            "the original exact plan order; do not alter the plan, grouping, or "
            "factual assignment. Return ONLY {\"narration\":[{\"plan_id\":\"P01\","
            "\"text\":\"...\"}]} with one item per plan_id."
        )

    def _generate(self, prompt: str) -> ModelGeneration:
        generate = getattr(self.model, "generate", None)
        if callable(generate):
            result = generate(prompt)
            if isinstance(result, ModelGeneration):
                return result
            if isinstance(result, dict):
                return ModelGeneration(
                    raw_text=json.dumps(result, ensure_ascii=False),
                    parsed=result,
                )
            raise RecapWritingError("Model generate() returned an unsupported result")
        result = self.model.generate_json(prompt)
        if not isinstance(result, dict):
            raise RecapWritingError("Model response must be a JSON object")
        return ModelGeneration(
            raw_text=json.dumps(result, ensure_ascii=False),
            parsed=result,
        )

    def _run_stage(
        self,
        stage: str,
        prompt: str,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        max_model_calls: int | None = None,
        repair_prompt_builder: Callable[[ModelGeneration, list[str]], str] | None = None,
    ) -> dict[str, Any]:
        current_prompt = prompt
        last_errors: list[str] = []
        attempts = (
            max(1, max_model_calls)
            if max_model_calls is not None
            else 1 + max(0, self.config.max_repair_attempts)
        )
        for attempt in range(1, attempts + 1):
            try:
                generation = self._generate(current_prompt)
            except Exception as exc:
                self.last_diagnostics["attempts"].append(
                    {
                        "stage": stage,
                        "attempt": attempt,
                        "kind": "initial" if attempt == 1 else "repair",
                        "raw_response": "",
                        "parse_error": str(exc),
                        "validation_errors": [str(exc)],
                    }
                )
                raise RecapWritingError(f"{stage} model call failed: {exc}") from exc

            errors: list[str] = []
            normalized: dict[str, Any] | None = None
            if generation.parsed is None:
                errors.append(
                    "Response is not a JSON object: "
                    + (generation.parse_error or "unknown parse error")
                )
            else:
                try:
                    normalized = validator(generation.parsed)
                except (RecapWritingError, RecapValidationError, ValueError) as exc:
                    errors.append(str(exc))
            self.last_diagnostics["attempts"].append(
                {
                    "stage": stage,
                    "attempt": attempt,
                    "kind": "initial" if attempt == 1 else "repair",
                    "raw_response": generation.raw_text,
                    "parse_error": generation.parse_error,
                    "validation_errors": errors,
                }
            )
            if not errors and normalized is not None:
                return normalized

            last_errors = errors
            if attempt >= attempts:
                break
            self.last_diagnostics["repair_attempt_count"] += 1
            current_prompt = (
                repair_prompt_builder(generation, errors)
                if repair_prompt_builder is not None
                else self._repair_prompt(
                    stage=stage,
                    original_prompt=prompt,
                    generation=generation,
                    errors=errors,
                )
            )
        raise RecapWritingError(
            f"{stage} remained invalid after {attempts} attempts: "
            + "; ".join(last_errors)
        )

    def _persist_diagnostics(self) -> None:
        if self.debug_dir is None:
            return
        write_json(
            self.debug_dir / "recap_writer_diagnostics.json",
            self.last_diagnostics,
        )

    def _write_rich_fast_path(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
    ) -> dict[str, Any]:
        """Write a rich researched story in one draft plus one bounded repair."""
        self.last_diagnostics.update(
            {
                "control_flow": "rich_fast_path",
                "critic_bypassed": True,
                "revision_attempt_count": 0,
                "targeted_expansion_used": False,
            }
        )
        script = self._run_stage(
            "rich_main_narration",
            self._rich_main_narration_prompt(story_map, outline),
            lambda raw: self._validated_rich_narration(raw, story_map, outline),
            max_model_calls=2,
            repair_prompt_builder=lambda generation, errors: self._rich_repair_prompt(
                stage="rich_main_narration",
                original_prompt=self._rich_main_narration_prompt(story_map, outline),
                generation=generation,
                errors=errors,
            ),
        )
        self.last_diagnostics.update(
            {
                "status": "success",
                "word_count": script["actual_word_count"],
                "segment_count": len(script["segments"]),
                "model_call_count": len(self.last_diagnostics["attempts"]),
                "targeted_repair_used": len(self.last_diagnostics["attempts"]) > 1,
            }
        )
        return script

    def write(self, story_map: dict[str, Any]) -> dict[str, Any]:
        self.last_diagnostics = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "status": "running",
            "repair_attempt_count": 0,
            "revision_attempt_count": 0,
            "attempts": [],
        }
        try:
            if not _beat_map(story_map, verified_only=True):
                raise RecapWritingError(
                    "No verified story beats are available for the Ollama writer"
                )
            outline = build_narration_plan(story_map, self.config)
            self.last_diagnostics["narration_plan"] = outline
            if self._uses_rich_fast_path(story_map):
                script = self._write_rich_fast_path(story_map, outline)
                self._persist_diagnostics()
                return script
            section_scripts: list[dict[str, Any]] = []
            for section in outline["sections"]:
                section_scripts.append(
                    self._run_stage(
                        f"narration_section_{section['section_id']}",
                        self._section_prompt(story_map, outline, section),
                        lambda raw, current=section: self._validate_section(
                            raw,
                            story_map,
                            outline,
                            current,
                        ),
                    )
                )
            script = self._assemble_sections(section_scripts, story_map)
            minimum_words = self._minimum_words(story_map)
            budget_deficits = self._budget_deficits(
                script,
                story_map,
                outline,
            )
            self.last_diagnostics["initial_word_count"] = script["actual_word_count"]
            self.last_diagnostics["minimum_word_count"] = minimum_words
            self.last_diagnostics["initial_budget_deficits"] = budget_deficits
            if budget_deficits:
                self.last_diagnostics["targeted_expansion_used"] = True
                patch_only = (
                    self.config.minimum_word_count >= 100
                    and all(item.get("beat_ids") for item in budget_deficits)
                )
                if patch_only:
                    expansion_validator = lambda raw: self._validate_expansion_patch(
                        raw,
                        story_map,
                        budget_deficits,
                        script,
                    )
                else:
                    expansion_validator = lambda raw: self._validated_script(
                        raw,
                        story_map,
                        outline,
                    )
                expansion = self._run_stage(
                    "targeted_budget_expansion",
                    self._expansion_prompt(
                        story_map,
                        outline,
                        script,
                        minimum_words,
                        budget_deficits,
                        patch_only=patch_only,
                    ),
                    expansion_validator,
                )
                if patch_only:
                    script = self._append_expansion(script, expansion, story_map)
                    validate_script_quality_invariants(
                        script,
                        story_map,
                        outline,
                        self.config,
                    )
                else:
                    script = expansion
            else:
                self.last_diagnostics["targeted_expansion_used"] = False
                validate_script_quality_invariants(
                    script,
                    story_map,
                    outline,
                    self.config,
                )
            critique = self._run_stage(
                "quality_critique",
                self._critique_prompt(story_map, outline, script),
                lambda raw: normalize_critique(
                    raw,
                    [segment["segment_id"] for segment in script["segments"]],
                ),
            )
            revision = 0
            while not critique["passes"] and revision < self.config.max_revision_attempts:
                revision += 1
                self.last_diagnostics["revision_attempt_count"] = revision
                target_ids = self._revision_target_ids(critique)
                if not target_ids:
                    target_ids = [
                        segment["segment_id"] for segment in script["segments"]
                    ]
                for part, start in enumerate(range(0, len(target_ids)), start=1):
                    chunk_ids = target_ids[start : start + 1]
                    current_script = script
                    revision_patch = self._run_stage(
                        f"narration_revision_{revision}_part_{part}",
                        self._revision_prompt(
                            story_map,
                            current_script,
                            critique,
                            chunk_ids,
                        ),
                        lambda raw, expected=chunk_ids, base=current_script: self._validate_revision_patch(
                            raw,
                            story_map,
                            base,
                            critique,
                            expected,
                        ),
                    )
                    script = self._merge_revision_patch(
                        current_script,
                        revision_patch,
                        story_map,
                    )
                validate_script_quality_invariants(
                    script,
                    story_map,
                    outline,
                    self.config,
                )
                critique = self._run_stage(
                    f"quality_critique_{revision + 1}",
                    self._critique_prompt(story_map, outline, script),
                    lambda raw: normalize_critique(
                        raw,
                        [
                            segment["segment_id"]
                            for segment in script["segments"]
                        ],
                    ),
                )
            if not critique["passes"]:
                messages = [issue["message"] for issue in critique["issues"]]
                raise RecapWritingError(
                    "Recap failed the quality gate after bounded revision: "
                    + "; ".join(messages)
                )

            self.last_diagnostics.update(
                {
                    "status": "success",
                    "word_count": script["actual_word_count"],
                    "segment_count": len(script["segments"]),
                    "model_call_count": len(self.last_diagnostics["attempts"]),
                    "final_critique": critique,
                }
            )
            self._persist_diagnostics()
            return script
        except Exception as exc:
            error = exc if isinstance(exc, RecapWritingError) else RecapWritingError(str(exc))
            self.last_diagnostics.update(
                {
                    "status": "failed",
                    "error": str(error),
                }
            )
            self._persist_diagnostics()
            raise error

    def _validated_script(
        self,
        raw: dict[str, Any],
        story_map: dict[str, Any],
        outline: dict[str, Any],
    ) -> dict[str, Any]:
        script = normalize_script(
            raw,
            story_map,
            self.config,
            deterministic_segment_ids=self._uses_rich_fast_path(story_map),
        )
        validate_script_quality_invariants(
            script,
            story_map,
            outline,
            self.config,
        )
        return script

    def _validated_rich_narration(
        self,
        raw: dict[str, Any],
        story_map: dict[str, Any],
        outline: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise RecapWritingError("RICH narration response must be an object")
        if set(raw) != {"narration"}:
            raise RecapWritingError(
                "RICH narration response must contain only the narration array"
            )
        narration = raw.get("narration")
        if not isinstance(narration, list):
            raise RecapWritingError("RICH narration response needs a narration array")

        planned = list(outline.get("planned_segments", []))
        expected_ids = [str(item.get("plan_id", "")) for item in planned]
        actual_ids = [
            str(item.get("plan_id", ""))
            for item in narration
            if isinstance(item, dict)
        ]
        if len(narration) != len(planned) or actual_ids != expected_ids:
            raise RecapWritingError(
                "RICH narration must contain exactly one item for each plan_id "
                f"in deterministic order; expected={expected_ids}, actual={actual_ids}"
            )

        raw_segments: list[dict[str, Any]] = []
        for planned_item, narration_item in zip(planned, narration):
            if not isinstance(narration_item, dict):
                raise RecapWritingError("RICH narration items must be objects")
            if set(narration_item) != {"plan_id", "text"}:
                raise RecapWritingError(
                    "RICH narration items may contain only plan_id and text"
                )
            text = " ".join(str(narration_item.get("text", "") or "").split())
            if not text:
                raise RecapWritingError(
                    f"RICH narration item {narration_item['plan_id']} has no text"
                )
            raw_segments.append(
                {
                    "segment_id": f"VO_{len(raw_segments) + 1:03d}",
                    "text": text,
                    "beat_ids": list(planned_item.get("beat_ids", [])),
                    "presentation_hint": "narration_over_source",
                }
            )

        script = normalize_script(
            raw_segments_payload(raw_segments),
            story_map,
            self.config,
            deterministic_segment_ids=True,
        )
        validate_script_quality_invariants(
            script,
            story_map,
            outline,
            self.config,
            allow_compact_protected_thoughts=True,
        )
        return script


class TemplateRecapWriter:
    """Offline baseline that repeats only verified beat summaries."""

    prompt_version = "recap-template-v1"
    model_version = "deterministic-template-v1"

    def __init__(self, config: RecapWritingConfig = RecapWritingConfig()):
        self.config = config

    def cache_identity(self) -> tuple[str, str]:
        return self.prompt_version, self.model_version

    def write(self, story_map: dict[str, Any]) -> dict[str, Any]:
        raw_segments: list[dict[str, Any]] = []
        beats = _beat_map(story_map)
        for beat in story_map.get("beats", []):
            if not isinstance(beat, dict):
                continue
            if beat.get("verification_status") != "verified":
                continue
            summary = str(beat.get("summary", "") or "").strip()
            if not summary:
                continue
            beat_id = str(beat.get("beat_id"))
            raw_segments.append(
                {
                    "segment_id": f"VO_{len(raw_segments) + 1:03d}",
                    "text": summary,
                    "beat_ids": [beat_id],
                    "presentation_hint": "narration_over_source",
                    "candidate_visuals": _ranges_for_beats([beat_id], beats),
                    "original_dialogue_candidates": _dialogue_for_beats(
                        [beat_id], beats
                    ),
                }
            )
        if not raw_segments:
            raise RecapWritingError(
                "No verified story beats are available for a grounded recap script."
            )
        return normalize_script(
            raw_segments_payload(raw_segments), story_map, self.config
        )


def raw_segments_payload(segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {"segments": segments}
