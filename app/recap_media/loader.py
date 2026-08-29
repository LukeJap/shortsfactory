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
from copy import deepcopy
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
SUPPORTED_RECAP_SCRIPT_SCHEMA_VERSIONS = frozenset({1, 2})
EXTERNAL_RECAP_SCRIPT_SCHEMA_VERSION = 2
VALID_BLOCK_TYPES = {"narration", "source_moment"}

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

# An external script may describe editorial blocks, but it must never choose
# a different media file from the accepted episode identity.
EXTERNAL_SOURCE_OVERRIDE_FIELDS = frozenset(
    {
        "source_video",
        "source_video_path",
        "source_path",
        "source_file",
        "input_video",
        "video_path",
        "episode_identity_path",
    }
)
SOURCE_EVIDENCE_TOLERANCE_SECONDS = 0.05


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


def _require_schema_version(
    data: dict[str, Any],
    label: str,
    supported_versions: frozenset[int] | None = None,
) -> int:

    version = data.get("schema_version")
    expected = supported_versions or frozenset({SUPPORTED_SCHEMA_VERSION})
    if version not in expected:
        expected_text = ", ".join(str(item) for item in sorted(expected))
        raise RecapInputError(
            f"{label} has schema_version={version!r}, "
            f"expected one of {{{expected_text}}}"
        )
    return int(version)


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

def _normalize_track_a_identity(data: dict[str, Any], label: str) -> dict[str, Any]:
    """Adapt Track A's confirmed canonical identity without altering its provenance."""
    if isinstance(data.get("title"), str) and data["title"].strip():
        return data

    selected = data.get("selected")
    if not isinstance(selected, dict):
        return data

    content_type = _require_str(selected, "content_type", f"{label}.selected")
    confidence = _require_number(selected, "confidence", f"{label}.selected", low=0.0, high=1.0)
    normalized = dict(data)
    normalized["confidence"] = confidence

    if content_type == "movie":
        normalized["title"] = _require_str(selected, "title", f"{label}.selected")
        normalized["media_type"] = "movie"
        return normalized
    if content_type != "tv":
        raise RecapInputError(
            f"{label}.selected field 'content_type'={content_type!r} must be 'tv' or 'movie'"
        )

    normalized["title"] = _require_str(
        selected, "series_title", f"{label}.selected"
    )
    normalized["media_type"] = "tv_episode"
    segments = _require_list(selected, "segments", f"{label}.selected", allow_empty=False)
    if len(segments) != 1 or not isinstance(segments[0], dict):
        raise RecapInputError(
            f"{label}.selected must contain exactly one selected segment for Track B"
        )
    segment = segments[0]
    normalized["episode_title"] = _require_str(
        segment, "title", f"{label}.selected.segments[0]"
    )
    provider_numbering = segment.get("provider_numbering")
    if not isinstance(provider_numbering, dict):
        raise RecapInputError(
            f"{label}.selected.segments[0] missing provider numbering for the selected segment"
        )
    numberings: set[tuple[int, int]] = set()
    for provider, numbering in provider_numbering.items():
        if not isinstance(numbering, dict):
            continue
        if "season" not in numbering or "episode" not in numbering:
            continue
        season = _require_int(
            numbering,
            "season",
            f"{label}.selected.segments[0].provider_numbering.{provider}",
            minimum=1,
        )
        episode = _require_int(
            numbering,
            "episode",
            f"{label}.selected.segments[0].provider_numbering.{provider}",
            minimum=1,
        )
        numberings.add((season, episode))
    if len(numberings) != 1:
        raise RecapInputError(
            f"{label}.selected.segments[0] needs one unambiguous provider season/episode"
        )
    normalized["season"], normalized["episode"] = numberings.pop()
    return normalized


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
    data = _normalize_track_a_identity(data, label)
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

    raw_beats = data.get("beats")
    if isinstance(raw_beats, list) and any(
        isinstance(beat, dict)
        and "order" not in beat
        and "chronological_order" in beat
        for beat in raw_beats
    ):
        normalized_beats: list[dict[str, Any]] = []
        seen_chronological_orders: set[int] = set()
        for index, raw_beat in enumerate(raw_beats):
            beat_label = f"{label} beats[{index}]"
            if not isinstance(raw_beat, dict):
                normalized_beats.append(raw_beat)
                continue
            beat = dict(raw_beat)
            chronological_order = _require_int(
                beat,
                "chronological_order",
                beat_label,
                minimum=1,
            )
            if chronological_order in seen_chronological_orders:
                raise RecapInputError(
                    f"{label} has ambiguous chronological_order {chronological_order!r}"
                )
            seen_chronological_orders.add(chronological_order)
            beat["order"] = chronological_order
            if "source_evidence" not in beat:
                source_ranges = beat.get("actual_video_evidence_ranges", [])
                if not isinstance(source_ranges, list):
                    raise RecapInputError(
                        f"{beat_label} field 'actual_video_evidence_ranges' must be a list"
                    )
                normalized_ranges = []
                for range_index, item in enumerate(source_ranges):
                    if not isinstance(item, dict):
                        raise RecapInputError(
                            f"{beat_label}.actual_video_evidence_ranges[{range_index}] "
                            "must be a JSON object"
                        )
                    normalized_ranges.append(
                        {
                            "start": item.get("start"),
                            "end": item.get("end"),
                            "type": item.get("evidence_type"),
                            "confidence": item.get("confidence"),
                        }
                    )
                beat["source_evidence"] = normalized_ranges
            normalized_beats.append(beat)
        data = {**data, "beats": normalized_beats}

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


def _validate_recap_script_payload(
    data: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Validate one already-decoded recap-script payload."""

    schema_version = _require_schema_version(
        data, label, SUPPORTED_RECAP_SCRIPT_SCHEMA_VERSIONS
    )

    _require_number(data, "target_duration_seconds", label, low=0.0)
    _require_int(data, "target_word_count", label, minimum=1)
    _require_str(data, "voice_style", label)

    segments = _require_list(data, "segments", label, allow_empty=False)
    seen_segment_ids: set[str] = set()
    seen_orders: set[int] = set()

    normalized_segments: list[dict[str, Any]] = []
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

        block_type = "narration" if schema_version == 1 else segment.get("block_type")
        if block_type not in VALID_BLOCK_TYPES:
            raise RecapInputError(
                f"{segment_label} field 'block_type'={block_type!r} must be "
                f"one of {sorted(VALID_BLOCK_TYPES)}"
            )

        # Schema-v1 preserves its visual_only behavior. In schema-v2 the
        # semantic block type, rather than presentation metadata, decides
        # whether a segment carries narration text.
        allow_empty_text = (
            hint == "visual_only" if schema_version == 1 else block_type == "source_moment"
        )
        text = _require_str(segment, "text", segment_label, allow_empty=allow_empty_text)
        if schema_version == 2 and block_type == "source_moment" and text.strip():
            raise RecapInputError(
                f"{segment_label} source_moment block must have empty text"
            )

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

        if block_type == "source_moment" and not dialogue_candidates:
            raise RecapInputError(
                f"{segment_label} source_moment needs an original_dialogue_candidates range"
            )
        if not candidate_visuals and not dialogue_candidates:
            raise RecapInputError(
                f"{segment_label} has no candidate_visuals and no "
                f"original_dialogue_candidates -- nothing to show for this "
                f"segment's narration"
            )

        normalized_segment = dict(segment)
        normalized_segment["block_type"] = block_type
        normalized_segments.append(normalized_segment)

    return {**data, "schema_version": schema_version, "segments": normalized_segments}


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
    return _validate_recap_script_payload(_read_json(path, label), label)


def _external_source_range_is_supported(
    candidate: dict[str, Any],
    evidence: dict[str, Any],
) -> bool:
    """Accept an evidence-contained range or one that substantially overlaps it."""

    start = float(candidate["start"])
    end = float(candidate["end"])
    evidence_start = float(evidence["start"])
    evidence_end = float(evidence["end"])
    tolerance = SOURCE_EVIDENCE_TOLERANCE_SECONDS

    if start >= evidence_start - tolerance and end <= evidence_end + tolerance:
        return True

    overlap = max(0.0, min(end, evidence_end) - max(start, evidence_start))
    duration = end - start
    return duration > 0 and overlap / duration >= 0.8


def _accepted_beats_by_id(verified_story_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(verified_story_map, dict):
        raise RecapInputError("accepted verified_story_map must be a JSON object")
    beats = verified_story_map.get("beats")
    if not isinstance(beats, list) or not beats:
        raise RecapInputError("accepted verified_story_map must contain verified beats")

    beats_by_id: dict[str, dict[str, Any]] = {}
    for index, beat in enumerate(beats):
        label = f"accepted verified_story_map beats[{index}]"
        if not isinstance(beat, dict):
            raise RecapInputError(f"{label} must be a JSON object")
        beat_id = _require_str(beat, "beat_id", label)
        _require_int(beat, "order", label)
        evidence = _require_list(beat, "source_evidence", label)
        for evidence_index, item in enumerate(evidence):
            evidence_label = f"{label}.source_evidence[{evidence_index}]"
            if not isinstance(item, dict):
                raise RecapInputError(f"{evidence_label} must be a JSON object")
            _require_time_range(item, evidence_label)
        beats_by_id[beat_id] = beat
    return beats_by_id


def _validate_external_source_identity(episode_identity: dict[str, Any]) -> None:
    if not isinstance(episode_identity, dict):
        raise RecapInputError("accepted episode_identity must be a JSON object")
    _require_str(episode_identity, "title", "accepted episode_identity")
    _require_str(episode_identity, "media_type", "accepted episode_identity")


def _validate_external_beat_ids(
    segments: list[dict[str, Any]],
    beats_by_id: dict[str, dict[str, Any]],
) -> None:
    for segment in segments:
        segment_id = str(segment.get("segment_id", "<unknown>"))
        for beat_id in segment.get("beat_ids", []):
            if beat_id not in beats_by_id:
                raise RecapInputError(f"Unknown beat {beat_id} in block {segment_id}.")


def _validate_external_source_grounding(
    segments: list[dict[str, Any]],
    beats_by_id: dict[str, dict[str, Any]],
) -> None:
    for segment in segments:
        if segment.get("block_type") != "source_moment":
            continue
        segment_id = str(segment["segment_id"])
        assigned_evidence = [
            evidence
            for beat_id in segment["beat_ids"]
            for evidence in beats_by_id[beat_id].get("source_evidence", [])
        ]
        for field in ("candidate_visuals", "original_dialogue_candidates"):
            for candidate in segment.get(field, []):
                if any(
                    _external_source_range_is_supported(candidate, evidence)
                    for evidence in assigned_evidence
                ):
                    continue
                beat_label = ", ".join(segment["beat_ids"])
                raise RecapInputError(
                    f"Source moment {segment_id} range is not supported by accepted "
                    f"evidence for {beat_label}."
                )


def _validate_external_chronology(
    segments: list[dict[str, Any]],
    beats_by_id: dict[str, dict[str, Any]],
) -> None:
    """Allow one opening hook-to-setup backfill, but reject later regressions."""

    seen_beat_ids: set[str] = set()
    highest_order: int | None = None
    used_opening_backfill = False

    for segment in sorted(segments, key=lambda item: item["order"]):
        new_beat_ids = [
            beat_id for beat_id in segment["beat_ids"] if beat_id not in seen_beat_ids
        ]
        if not new_beat_ids:
            continue
        new_orders = [int(beats_by_id[beat_id]["order"]) for beat_id in new_beat_ids]
        earliest_new_order = min(new_orders)
        if highest_order is not None and earliest_new_order < highest_order:
            # A non-chronological hook followed by a minimum earlier setup is
            # an established recap pattern. It is safe only before the story
            # has progressed through more than its first distinct beat.
            can_backfill_opening = len(seen_beat_ids) == 1 and not used_opening_backfill
            if not can_backfill_opening:
                raise RecapInputError(
                    f"Block {segment['segment_id']} moves backwards from accepted "
                    "story chronology."
                )
            used_opening_backfill = True
        seen_beat_ids.update(new_beat_ids)
        highest_order = max(highest_order or new_orders[0], max(new_orders))


def _supplement_missing_external_visuals(
    segments: list[dict[str, Any]],
    beats_by_id: dict[str, dict[str, Any]],
) -> None:
    """Supply only accepted local evidence when narration has no visuals at all."""

    for segment in segments:
        if segment.get("block_type") != "narration":
            continue
        if segment.get("candidate_visuals") or segment.get("original_dialogue_candidates"):
            continue
        supplemental: list[dict[str, Any]] = []
        for beat_id in segment["beat_ids"]:
            for evidence in beats_by_id[beat_id].get("source_evidence", []):
                supplemental.append(
                    {
                        "start": evidence["start"],
                        "end": evidence["end"],
                        "score": evidence.get("confidence", 0.5),
                        "reason": f"Accepted verified story evidence for {beat_id}.",
                        "beat_id": beat_id,
                    }
                )
        if supplemental:
            segment["candidate_visuals"] = supplemental


def load_external_recap_script(
    path: Path,
    *,
    episode_identity: dict[str, Any],
    verified_story_map: dict[str, Any],
) -> dict[str, Any]:
    """Load an external schema-v2 script against accepted Track A artifacts.

    ``episode_identity`` and ``verified_story_map`` must be the normalized,
    already-accepted outputs of :func:`load_episode_identity` and
    :func:`load_verified_story_map`. The imported file contributes creative
    blocks only; it never selects a media source or changes story evidence.
    """

    _validate_external_source_identity(episode_identity)
    beats_by_id = _accepted_beats_by_id(verified_story_map)
    data = _read_json(path, "external recap script")
    if data.get("schema_version") != EXTERNAL_RECAP_SCRIPT_SCHEMA_VERSION:
        raise RecapInputError("External import requires schema v2.")
    for field in EXTERNAL_SOURCE_OVERRIDE_FIELDS:
        if data.get(field) not in (None, ""):
            raise RecapInputError(
                f"External recap script cannot override accepted source identity via {field!r}."
            )

    normalized = deepcopy(data)
    raw_segments = normalized.get("segments")
    if not isinstance(raw_segments, list):
        # Let the existing contract validator provide its standard field error.
        return _validate_recap_script_payload(normalized, "external recap script")

    # Keep external prose exactly as supplied. Only missing source metadata is
    # derived, and only from verified evidence for the block's own beat IDs.
    _validate_external_beat_ids(
        [segment for segment in raw_segments if isinstance(segment, dict)], beats_by_id
    )
    _supplement_missing_external_visuals(
        [segment for segment in raw_segments if isinstance(segment, dict)], beats_by_id
    )
    parsed = _validate_recap_script_payload(normalized, "external recap script")

    orders = [segment["order"] for segment in parsed["segments"]]
    if orders != sorted(orders):
        raise RecapInputError("External recap script segment orders must be strictly increasing.")
    _validate_external_beat_ids(parsed["segments"], beats_by_id)
    _validate_external_source_grounding(parsed["segments"], beats_by_id)
    _validate_external_chronology(parsed["segments"], beats_by_id)

    authoring_source = data.get("authoring_source")
    if not isinstance(authoring_source, str) or not authoring_source.strip():
        authoring_source = "external"
    return {
        **parsed,
        "authoring_source": authoring_source.strip(),
        "script_source": "external",
        "imported_from": Path(path).name,
    }


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
