"""Small, dependency-free contracts shared by Track A modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PRESENTATION_HINTS = frozenset(
    {
        "narration_over_source",
        "original_dialogue",
        "reaction_beat",
        "visual_only",
    }
)


class RecapValidationError(ValueError):
    """Raised when a recap handoff artifact is structurally unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecapValidationError(f"Could not read JSON artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise RecapValidationError(f"JSON artifact must be an object: {path}")
    return payload


@dataclass(frozen=True)
class IdentityQuery:
    """User/source identity input with container and segment scopes separated."""

    content_type: str
    title: str
    season: int | None = None
    container_episode: int | None = None
    container_title: str = ""
    segment_titles: tuple[str, ...] = ()
    source_filename: str = ""
    source_runtime_seconds: float | None = None
    # Deprecated aliases retained so existing callers do not silently break.
    episode: int | None = None
    segment_title: str = ""

    def __post_init__(self) -> None:
        container_episode = (
            self.container_episode
            if self.container_episode is not None
            else self.episode
        )
        object.__setattr__(self, "container_episode", container_episode)
        if self.episode is None and container_episode is not None:
            object.__setattr__(self, "episode", container_episode)
        segment_titles = tuple(
            str(value).strip()
            for value in self.segment_titles
            if str(value).strip()
        )
        if not segment_titles and self.segment_title.strip():
            segment_titles = (self.segment_title.strip(),)
        object.__setattr__(self, "segment_titles", segment_titles)
        if not self.segment_title.strip() and segment_titles:
            object.__setattr__(self, "segment_title", segment_titles[0])
        if not self.container_title.strip() and segment_titles:
            object.__setattr__(self, "container_title", " / ".join(segment_titles))

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_type": self.content_type,
            "title": self.title,
            "season": self.season,
            "container_episode": self.container_episode,
            "container_title": self.container_title,
            "segment_titles": list(self.segment_titles),
            "source_filename": self.source_filename,
            "source_runtime_seconds": self.source_runtime_seconds,
        }


@dataclass(frozen=True)
class IdentitySegment:
    title: str
    provider_ids: dict[str, Any] | None = None
    provider_numbering: dict[str, Any] | None = None
    description: str = ""
    url: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "provider_ids": dict(self.provider_ids or {}),
            "provider_numbering": dict(self.provider_numbering or {}),
            "description": self.description,
            "url": self.url,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
        }


@dataclass(frozen=True)
class IdentityCandidate:
    canonical_id: str
    content_type: str
    title: str
    episode_title: str = ""
    season: int | None = None
    episode: int | None = None
    description: str = ""
    url: str = ""
    provider: str = ""
    provider_id: str = ""
    confidence: float = 0.0
    alternate_numbering: dict[str, Any] | None = None
    providers: tuple[str, ...] = ()
    series_title: str = ""
    container_title: str = ""
    container_episode: int | None = None
    segments: tuple[IdentitySegment, ...] = ()
    provider_ids: dict[str, Any] | None = None
    provider_numbering: dict[str, Any] | None = None
    source_match: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.series_title.strip():
            object.__setattr__(self, "series_title", self.title)
        if self.container_episode is None and self.episode is not None:
            object.__setattr__(self, "container_episode", self.episode)
        if not self.container_title and self.episode_title.strip():
            object.__setattr__(self, "container_title", self.episode_title)
        if self.provider and self.provider_id and not self.provider_ids:
            object.__setattr__(
                self,
                "provider_ids",
                {self.provider: self.provider_id},
            )
        if self.provider and not self.provider_numbering and self.episode is not None:
            object.__setattr__(
                self,
                "provider_numbering",
                {
                    self.provider: {
                        "season": self.season,
                        "episode": self.episode,
                    }
                },
            )
        if not self.segments and self.episode_title.strip():
            object.__setattr__(
                self,
                "segments",
                (
                    IdentitySegment(
                        title=self.episode_title.strip(),
                        provider_ids={self.provider: self.provider_id} if self.provider else {},
                        provider_numbering={
                            self.provider: {
                                "season": self.season,
                                "episode": self.episode,
                            }
                        }
                        if self.provider
                        else {},
                        description=self.description,
                        url=self.url,
                        confidence=self.confidence,
                    ),
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "content_type": self.content_type,
            "series_title": self.series_title or self.title,
            "container_title": self.container_title,
            "container_episode": self.container_episode,
            "segments": [segment.to_dict() for segment in self.segments],
            "provider_ids": dict(self.provider_ids or {}),
            "provider_numbering": dict(self.provider_numbering or {}),
            "source_match": dict(self.source_match or {}),
            "description": self.description,
            "url": self.url,
            "provider": self.provider,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "providers": list(
                self.providers or ((self.provider,) if self.provider else ())
            ),
        }


def _require_object(payload: Any, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RecapValidationError(f"{name} must be an object")
    return payload


def _require_list(payload: dict[str, Any], key: str, name: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RecapValidationError(f"{name}.{key} must be a list")
    return value


def _number(value: Any, field: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise RecapValidationError(f"{field} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RecapValidationError(f"{field} must be numeric") from exc


def validate_identity_artifact(payload: dict[str, Any]) -> None:
    _require_object(payload, "identity artifact")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RecapValidationError("Unsupported episode identity schema version")
    if payload.get("status") not in {
        "awaiting_confirmation",
        "confirmed",
        "ambiguous",
        "unavailable",
    }:
        raise RecapValidationError("Invalid identity resolution status")
    candidates = _require_list(payload, "candidates", "identity artifact")
    for index, candidate in enumerate(candidates):
        candidate = _require_object(candidate, f"identity candidate {index}")
        if not str(candidate.get("canonical_id", "")).strip():
            raise RecapValidationError("Identity candidates need canonical_id")
        if not str(candidate.get("series_title") or candidate.get("title", "")).strip():
            raise RecapValidationError("Identity candidates need title")
    selected = payload.get("selected")
    if selected is not None:
        _require_object(selected, "identity artifact.selected")


def _validate_range(item: dict[str, Any], name: str) -> None:
    start = _number(item.get("start"), f"{name}.start")
    end = _number(item.get("end"), f"{name}.end")
    if start is None or end is None or end <= start:
        raise RecapValidationError(f"{name} must have end > start")


def validate_research_dossier(payload: dict[str, Any]) -> None:
    _require_object(payload, "research dossier")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RecapValidationError("Unsupported research dossier schema version")
    sources = _require_list(payload, "sources", "research dossier")
    for index, source in enumerate(sources):
        source = _require_object(source, f"research source {index}")
        for field in ("provider", "url", "source_type", "retrieved_at"):
            if not str(source.get(field, "")).strip():
                raise RecapValidationError(f"Research source missing {field}")
        if not isinstance(source.get("claims", []), list):
            raise RecapValidationError("Research source claims must be a list")
    _require_list(payload, "ordered_plot_points", "research dossier")
    transcript_events = payload.get("transcript_events", [])
    if not isinstance(transcript_events, list):
        raise RecapValidationError("research dossier.transcript_events must be a list")
    for index, event in enumerate(transcript_events):
        event = _require_object(event, f"research transcript event {index}")
        if not str(event.get("event_id", "")).strip():
            raise RecapValidationError("Research transcript events need event_id")
        if event.get("timing_authority") not in {None, "none"}:
            raise RecapValidationError("Fandom transcript timing cannot be source authority")
    segments = payload.get("segments")
    if segments is not None:
        if not isinstance(segments, list):
            raise RecapValidationError("research dossier.segments must be a list")
        segment_ids: set[str] = set()
        for index, segment in enumerate(segments):
            segment = _require_object(segment, f"research segment {index}")
            segment_id = str(segment.get("segment_id", "")).strip()
            if not segment_id or segment_id in segment_ids:
                raise RecapValidationError(
                    "Research segment IDs must be present and unique"
                )
            segment_ids.add(segment_id)
            if not str(segment.get("title", "")).strip():
                raise RecapValidationError(
                    f"Research segment {segment_id} needs a title"
                )
            if not isinstance(segment.get("sources", []), list):
                raise RecapValidationError(
                    f"Research segment {segment_id}.sources must be a list"
                )
            if not isinstance(segment.get("ordered_plot_points", []), list):
                raise RecapValidationError(
                    f"Research segment {segment_id}.ordered_plot_points must be a list"
                )
            if not isinstance(segment.get("transcript_events", []), list):
                raise RecapValidationError(
                    f"Research segment {segment_id}.transcript_events must be a list"
                )


def validate_story_map(payload: dict[str, Any]) -> None:
    _require_object(payload, "verified story map")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RecapValidationError("Unsupported verified story map schema version")
    beats = _require_list(payload, "beats", "verified story map")
    beat_ids: set[str] = set()
    for index, beat in enumerate(beats):
        beat = _require_object(beat, f"story beat {index}")
        beat_id = str(beat.get("beat_id", "")).strip()
        if not beat_id or beat_id in beat_ids:
            raise RecapValidationError("Story beat IDs must be present and unique")
        beat_ids.add(beat_id)
        if not str(beat.get("summary", "")).strip():
            raise RecapValidationError(f"{beat_id} needs a summary")
        for field in ("causal_parents", "causal_children", "actual_video_evidence_ranges"):
            if not isinstance(beat.get(field, []), list):
                raise RecapValidationError(f"{beat_id}.{field} must be a list")
        for evidence_index, evidence in enumerate(
            beat.get("actual_video_evidence_ranges", [])
        ):
            evidence = _require_object(
                evidence,
                f"{beat_id}.actual_video_evidence_ranges[{evidence_index}]",
            )
            _validate_range(
                evidence,
                f"{beat_id}.actual_video_evidence_ranges[{evidence_index}]",
            )
        start = _number(beat.get("source_start"), f"{beat_id}.source_start", allow_none=True)
        end = _number(beat.get("source_end"), f"{beat_id}.source_end", allow_none=True)
        if (start is None) != (end is None) or (
            start is not None and end is not None and end <= start
        ):
            raise RecapValidationError(f"{beat_id} has an invalid source range")
    for beat in beats:
        for parent in beat.get("causal_parents", []):
            if parent not in beat_ids:
                raise RecapValidationError(f"Unknown causal parent: {parent}")
        for child in beat.get("causal_children", []):
            if child not in beat_ids:
                raise RecapValidationError(f"Unknown causal child: {child}")


def validate_recap_script(
    payload: dict[str, Any],
    story_map: dict[str, Any] | None = None,
) -> None:
    _require_object(payload, "recap script")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RecapValidationError("Unsupported recap script schema version")
    target_duration = _number(
        payload.get("target_duration_seconds"),
        "recap script.target_duration_seconds",
    )
    if target_duration is None or target_duration <= 0:
        raise RecapValidationError("Recap target duration must be positive")
    target_words = _number(payload.get("target_word_count"), "recap script.target_word_count")
    if target_words is None or target_words <= 0:
        raise RecapValidationError("Recap target word count must be positive")
    segments = _require_list(payload, "segments", "recap script")
    known_beats = {
        str(beat.get("beat_id"))
        for beat in (story_map or {}).get("beats", [])
        if isinstance(beat, dict)
    }
    seen_ids: set[str] = set()
    for index, segment in enumerate(segments):
        segment = _require_object(segment, f"recap segment {index}")
        segment_id = str(segment.get("segment_id", "")).strip()
        if not segment_id or segment_id in seen_ids:
            raise RecapValidationError("Recap segment IDs must be present and unique")
        seen_ids.add(segment_id)
        if not str(segment.get("text", "")).strip():
            raise RecapValidationError(f"{segment_id} needs narration text")
        hint = str(segment.get("presentation_hint", "")).strip()
        if hint not in PRESENTATION_HINTS:
            raise RecapValidationError(f"{segment_id} has invalid presentation_hint")
        beat_ids = segment.get("beat_ids", [])
        if not isinstance(beat_ids, list) or not beat_ids:
            raise RecapValidationError(f"{segment_id} needs beat_ids")
        if known_beats and any(beat_id not in known_beats for beat_id in beat_ids):
            raise RecapValidationError(f"{segment_id} references an unknown story beat")
        for field in ("candidate_visuals", "original_dialogue_candidates"):
            items = segment.get(field, [])
            if not isinstance(items, list):
                raise RecapValidationError(f"{segment_id}.{field} must be a list")
            for item_index, item in enumerate(items):
                item = _require_object(item, f"{segment_id}.{field}[{item_index}]")
                _validate_range(item, f"{segment_id}.{field}[{item_index}]")
