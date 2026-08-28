"""
B1 -- fixture-driven loader/validator for Track A's frozen recap inputs
(episode_identity.json, verified_story_map.json, recap_script.json; see
SHORTSFACTORY_AI_RECAP_SHARED_CONTRACT.md). Fails cleanly (RecapInputError)
on missing/malformed/invalid data rather than letting a bad value from
Track A silently propagate into sequence assembly, TTS, or rendering.

Schema note: only recap_script.json's schema is actually specified in the
shared contract. episode_identity.json and verified_story_map.json don't
have a pinned-down schema anywhere yet, so the shapes validated here
(see load_episode_identity()/load_verified_story_map()) are this track's
own proposal, not an agreed contract -- expect to reconcile these once
Track A has a real implementation to compare against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline_paths import (
    EPISODE_IDENTITY_PATH,
    RECAP_SCRIPT_PATH,
    VERIFIED_STORY_MAP_PATH,
)


class RecapInputError(Exception):
    """A Track A recap input file is missing, malformed, or invalid."""


SUPPORTED_SCHEMA_VERSION = 1

VALID_PRESENTATION_HINTS = {
    "narration_over_source",
    "original_dialogue",
    "reaction_beat",
    "visual_only",
}

VALID_MEDIA_TYPES = {
    "tv_episode",
    "movie",
}


@dataclass(frozen=True)
class RecapInputs:
    episode_identity: dict[str, Any]
    verified_story_map: dict[str, Any]
    recap_script: dict[str, Any]


# ============================================================
# Small validation helpers -- raise RecapInputError with enough context
# (which file, which field, which value) to fix the input without
# re-reading this module.
# ============================================================

def _read_json(path: Path, label: str) -> dict[str, Any]:

    if not path.exists():
        raise RecapInputError(f"{label} not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecapInputError(f"{label} could not be read ({path}): {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecapInputError(f"{label} is not valid JSON ({path}): {exc}") from exc

    if not isinstance(data, dict):
        raise RecapInputError(
            f"{label} must be a JSON object at the top level, "
            f"got {type(data).__name__} ({path})"
        )

    return data


def _require_schema_version(data: dict[str, Any], label: str) -> None:

    version = data.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise RecapInputError(
            f"{label} has schema_version={version!r}, "
            f"expected {SUPPORTED_SCHEMA_VERSION}"
        )


def _require_str(data: dict[str, Any], key: str, label: str, allow_empty: bool = False) -> str:

    value = data.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RecapInputError(
            f"{label} missing required "
            f"{'string' if allow_empty else 'non-empty string'} field {key!r}"
        )
    return value


def _require_int(data: dict[str, Any], key: str, label: str, minimum: int | None = None) -> int:

    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecapInputError(f"{label} field {key!r} must be an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise RecapInputError(f"{label} field {key!r}={value} must be >= {minimum}")
    return value


def _require_number(
    data: dict[str, Any],
    key: str,
    label: str,
    low: float | None = None,
    high: float | None = None,
) -> float:

    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecapInputError(f"{label} field {key!r} must be a number, got {value!r}")

    number = float(value)
    if low is not None and number < low:
        raise RecapInputError(f"{label} field {key!r}={number} must be >= {low}")
    if high is not None and number > high:
        raise RecapInputError(f"{label} field {key!r}={number} must be <= {high}")
    return number


def _require_list(
    data: dict[str, Any],
    key: str,
    label: str,
    allow_empty: bool = True,
) -> list:

    value = data.get(key, [] if allow_empty else None)
    if not isinstance(value, list):
        raise RecapInputError(f"{label} field {key!r} must be a list, got {type(value).__name__}")
    if not allow_empty and not value:
        raise RecapInputError(f"{label} field {key!r} must not be empty")
    return value


def _require_time_range(item: dict[str, Any], label: str) -> None:

    start = _require_number(item, "start", label, low=0.0)
    end = _require_number(item, "end", label, low=0.0)
    if start >= end:
        raise RecapInputError(f"{label} 'start' ({start}) must be before 'end' ({end})")


# ============================================================
# Per-file loaders
# ============================================================

def load_episode_identity(path: Path = EPISODE_IDENTITY_PATH) -> dict[str, Any]:
    """
    Load and validate episode_identity.json.

    Proposed shape (see module docstring -- not yet agreed with Track A):
    schema_version, title, media_type ("tv_episode"|"movie"), confidence
    (0-1); season/episode required positive integers when media_type is
    "tv_episode", not validated otherwise.
    """

    label = "episode_identity.json"
    data = _read_json(path, label)
    _require_schema_version(data, label)
    _require_str(data, "title", label)

    media_type = _require_str(data, "media_type", label)
    if media_type not in VALID_MEDIA_TYPES:
        raise RecapInputError(
            f"{label} field 'media_type'={media_type!r} must be one of "
            f"{sorted(VALID_MEDIA_TYPES)}"
        )

    _require_number(data, "confidence", label, low=0.0, high=1.0)

    if media_type == "tv_episode":
        _require_int(data, "season", label, minimum=1)
        _require_int(data, "episode", label, minimum=1)

    return data


def load_verified_story_map(path: Path = VERIFIED_STORY_MAP_PATH) -> dict[str, Any]:
    """
    Load and validate verified_story_map.json.

    Proposed shape (see module docstring -- not yet agreed with Track A):
    schema_version, beats (non-empty list of {beat_id (unique), order,
    summary, importance (0-1), source_evidence}). source_evidence is
    optional per shared rule 5 ("should trace to... where possible") --
    an empty list is fine, but every entry provided is validated
    (start < end, type, confidence 0-1).
    """

    label = "verified_story_map.json"
    data = _read_json(path, label)
    _require_schema_version(data, label)

    beats = _require_list(data, "beats", label, allow_empty=False)
    seen_beat_ids: set[str] = set()

    for index, beat in enumerate(beats):
        beat_label = f"{label} beats[{index}]"

        if not isinstance(beat, dict):
            raise RecapInputError(f"{beat_label} must be a JSON object")

        beat_id = _require_str(beat, "beat_id", beat_label)
        if beat_id in seen_beat_ids:
            raise RecapInputError(f"{label} has duplicate beat_id {beat_id!r}")
        seen_beat_ids.add(beat_id)

        _require_int(beat, "order", beat_label)
        _require_str(beat, "summary", beat_label)
        _require_number(beat, "importance", beat_label, low=0.0, high=1.0)

        evidence = _require_list(beat, "source_evidence", beat_label)
        for evidence_index, item in enumerate(evidence):
            evidence_label = f"{beat_label}.source_evidence[{evidence_index}]"
            if not isinstance(item, dict):
                raise RecapInputError(f"{evidence_label} must be a JSON object")
            _require_time_range(item, evidence_label)
            _require_str(item, "type", evidence_label)
            _require_number(item, "confidence", evidence_label, low=0.0, high=1.0)

    return data


def load_recap_script(path: Path = RECAP_SCRIPT_PATH) -> dict[str, Any]:
    """
    Load and validate recap_script.json -- schema frozen in
    SHORTSFACTORY_AI_RECAP_SHARED_CONTRACT.md.

    One rule enforced here that isn't spelled out verbatim in the
    contract: every segment must offer at least one selectable source
    range (a non-empty candidate_visuals or original_dialogue_candidates
    list) -- otherwise Track B would have narration with literally
    nothing to show underneath it, regardless of presentation_hint.
    """

    label = "recap_script.json"
    data = _read_json(path, label)
    _require_schema_version(data, label)

    _require_number(data, "target_duration_seconds", label, low=0.0)
    _require_int(data, "target_word_count", label, minimum=1)
    _require_str(data, "voice_style", label)

    segments = _require_list(data, "segments", label, allow_empty=False)
    seen_segment_ids: set[str] = set()
    seen_orders: set[int] = set()

    for index, segment in enumerate(segments):
        segment_label = f"{label} segments[{index}]"

        if not isinstance(segment, dict):
            raise RecapInputError(f"{segment_label} must be a JSON object")

        segment_id = _require_str(segment, "segment_id", segment_label)
        if segment_id in seen_segment_ids:
            raise RecapInputError(f"{label} has duplicate segment_id {segment_id!r}")
        seen_segment_ids.add(segment_id)

        order = _require_int(segment, "order", segment_label)
        if order in seen_orders:
            raise RecapInputError(f"{label} has duplicate segment order {order!r}")
        seen_orders.add(order)

        hint = segment.get("presentation_hint")
        if hint not in VALID_PRESENTATION_HINTS:
            raise RecapInputError(
                f"{segment_label} field 'presentation_hint'={hint!r} must be "
                f"one of {sorted(VALID_PRESENTATION_HINTS)}"
            )

        # visual_only segments carry no narration; every other hint needs
        # actual narration text to send to Orpheus.
        _require_str(segment, "text", segment_label, allow_empty=(hint == "visual_only"))

        beat_ids = _require_list(segment, "beat_ids", segment_label, allow_empty=False)
        for beat_id in beat_ids:
            if not isinstance(beat_id, str) or not beat_id.strip():
                raise RecapInputError(
                    f"{segment_label} field 'beat_ids' must contain only non-empty strings"
                )

        _require_number(segment, "importance", segment_label, low=0.0, high=1.0)

        candidate_visuals = _require_list(segment, "candidate_visuals", segment_label)
        dialogue_candidates = _require_list(
            segment, "original_dialogue_candidates", segment_label
        )

        for range_key, ranges in (
            ("candidate_visuals", candidate_visuals),
            ("original_dialogue_candidates", dialogue_candidates),
        ):
            for range_index, candidate in enumerate(ranges):
                candidate_label = f"{segment_label}.{range_key}[{range_index}]"
                if not isinstance(candidate, dict):
                    raise RecapInputError(f"{candidate_label} must be a JSON object")
                _require_time_range(candidate, candidate_label)
                _require_number(candidate, "score", candidate_label, low=0.0, high=1.0)
                _require_str(candidate, "reason", candidate_label)

        if not candidate_visuals and not dialogue_candidates:
            raise RecapInputError(
                f"{segment_label} has no candidate_visuals and no "
                f"original_dialogue_candidates -- nothing to show for this "
                f"segment's narration"
            )

    return data


def load_recap_inputs(
    episode_identity_path: Path = EPISODE_IDENTITY_PATH,
    verified_story_map_path: Path = VERIFIED_STORY_MAP_PATH,
    recap_script_path: Path = RECAP_SCRIPT_PATH,
) -> RecapInputs:
    """
    Load and validate all three Track A input files, plus the one
    cross-file check the shared contract calls out by name (rule 4:
    "Every narration segment traces to verified story beats") -- every
    beat_id referenced from recap_script.json must actually exist in
    verified_story_map.json.
    """

    episode_identity = load_episode_identity(episode_identity_path)
    verified_story_map = load_verified_story_map(verified_story_map_path)
    recap_script = load_recap_script(recap_script_path)

    known_beat_ids = {beat["beat_id"] for beat in verified_story_map["beats"]}

    for segment in recap_script["segments"]:
        for beat_id in segment["beat_ids"]:
            if beat_id not in known_beat_ids:
                raise RecapInputError(
                    f"recap_script.json segment {segment['segment_id']!r} "
                    f"references beat_id {beat_id!r} not found in "
                    f"verified_story_map.json"
                )

    return RecapInputs(
        episode_identity=episode_identity,
        verified_story_map=verified_story_map,
        recap_script=recap_script,
    )
