"""Episode identity normalization and confirmation workflow."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, Protocol, Sequence

from .models import IdentityCandidate, IdentityQuery, utc_now, write_json


class IdentityResolutionError(RuntimeError):
    """Raised when no usable identity can be resolved."""


class IdentityConfirmationRequired(IdentityResolutionError):
    """Raised when candidates exist but the user has not confirmed one."""


class IdentityProvider(Protocol):
    name: str

    def resolve(self, query: IdentityQuery) -> list[IdentityCandidate]:
        ...


def normalize_content_type(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"tv", "television", "series", "show", "episode"}:
        return "tv"
    if normalized in {"movie", "film"}:
        return "movie"
    raise ValueError("content_type must be tv or movie")


def normalize_title(value: str) -> str:
    value = str(value or "").casefold()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def title_similarity(left: str, right: str) -> float:
    left_tokens = set(normalize_title(left).split())
    right_tokens = set(normalize_title(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    if left_tokens == right_tokens:
        return 1.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    score = overlap / max(1, union)
    if normalize_title(left) in normalize_title(right) or normalize_title(right) in normalize_title(left):
        score = max(score, 0.82)
    return round(score, 4)


_COMPOUND_TITLE_RE = re.compile(r"\s*&\s*|\s+/\s+|\s+\+\s+")
_SOURCE_EPISODE_RE = re.compile(
    r"(?i)S(?P<season>\d{1,2})E(?P<episode>\d{1,3})"
)


def parse_compound_title(value: str) -> list[str]:
    """Parse conservative paired-story title separators."""
    cleaned = " ".join(str(value or "").replace("_", " ").split()).strip()
    if not cleaned:
        return []
    parts = [part.strip(" -_.") for part in _COMPOUND_TITLE_RE.split(cleaned)]
    if len(parts) < 2 or any(len(part) < 3 for part in parts):
        return [cleaned]
    return parts


def parse_source_filename(filename: str) -> dict[str, Any]:
    """Extract a container slot and title from a source filename when present."""
    stem = Path(str(filename or "")).stem
    match = _SOURCE_EPISODE_RE.search(stem)
    if not match:
        return {
            "season": None,
            "container_episode": None,
            "container_title": "",
            "segment_titles": [],
        }
    title = stem[match.end() :].lstrip(" -_.")
    segment_titles = parse_compound_title(title) if title else []
    return {
        "season": int(match.group("season")),
        "container_episode": int(match.group("episode")),
        "container_title": title,
        "segment_titles": segment_titles,
    }


@dataclass
class IdentityResolution:
    query: IdentityQuery
    status: str
    candidates: list[IdentityCandidate]
    selected: IdentityCandidate | None = None
    warnings: list[str] | None = None
    resolved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "resolved_at": self.resolved_at or utc_now(),
            "status": self.status,
            "query": self.query.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected": self.selected.to_dict() if self.selected else None,
            "warnings": list(self.warnings or []),
            "user_confirmed": self.selected is not None and self.status == "confirmed",
        }


class EpisodeIdentityResolver:
    """Combines provider candidates without silently guessing."""

    def __init__(self, providers: Sequence[IdentityProvider]):
        self.providers = list(providers)

    def resolve(
        self,
        query: IdentityQuery,
        *,
        confirm_index: int | None = None,
    ) -> IdentityResolution:
        parsed_filename = parse_source_filename(query.source_filename)
        container_title = str(query.container_title or "").strip()
        if not container_title:
            container_title = str(parsed_filename.get("container_title", "")).strip()
        segment_titles = tuple(query.segment_titles)
        if not segment_titles:
            if query.segment_title:
                segment_titles = tuple(parse_compound_title(query.segment_title))
            elif container_title:
                segment_titles = tuple(parse_compound_title(container_title))
            else:
                segment_titles = tuple(parsed_filename.get("segment_titles", []))
        season = query.season if query.season is not None else parsed_filename.get("season")
        container_episode = (
            query.container_episode
            if query.container_episode is not None
            else query.episode
            if query.episode is not None
            else parsed_filename.get("container_episode")
        )
        normalized_query = IdentityQuery(
            content_type=normalize_content_type(query.content_type),
            title=str(query.title).strip(),
            season=season,
            container_episode=container_episode,
            container_title=container_title,
            segment_titles=segment_titles,
            source_filename=query.source_filename,
            source_runtime_seconds=query.source_runtime_seconds,
        )
        if not normalized_query.title:
            raise ValueError("A show or movie title is required")

        warnings: list[str] = []
        merged: dict[str, IdentityCandidate] = {}
        provider_names: dict[str, set[str]] = {}

        for provider in self.providers:
            try:
                candidates = provider.resolve(normalized_query)
            except Exception as exc:
                warnings.append(f"{getattr(provider, 'name', type(provider).__name__)} unavailable: {exc}")
                continue
            for candidate in candidates:
                key = self._candidate_key(candidate)
                existing = merged.get(key)
                if existing is None or candidate.confidence > existing.confidence:
                    merged[key] = candidate
                provider_names.setdefault(key, set()).add(getattr(provider, "name", candidate.provider))

        candidates = []
        for key, candidate in merged.items():
            names = tuple(sorted(name for name in provider_names.get(key, set()) if name))
            candidates.append(
                IdentityCandidate(
                    canonical_id=candidate.canonical_id,
                    content_type=candidate.content_type,
                    title=candidate.title,
                    episode_title=candidate.episode_title,
                    season=candidate.season,
                    episode=candidate.episode,
                    description=candidate.description,
                    url=candidate.url,
                    provider=candidate.provider,
                    provider_id=candidate.provider_id,
                    confidence=candidate.confidence,
                    alternate_numbering=candidate.alternate_numbering,
                    providers=names or candidate.providers,
                    series_title=candidate.series_title,
                    container_title=candidate.container_title,
                    container_episode=candidate.container_episode,
                    segments=candidate.segments,
                    provider_ids=candidate.provider_ids,
                    provider_numbering=candidate.provider_numbering,
                    source_match=candidate.source_match,
                )
            )
        candidates.sort(key=lambda item: (-item.confidence, item.canonical_id))

        if not candidates:
            return IdentityResolution(
                query=normalized_query,
                status="unavailable",
                candidates=[],
                warnings=warnings or ["No provider returned a matching identity."],
                resolved_at=utc_now(),
            )

        if confirm_index is not None:
            if confirm_index < 0 or confirm_index >= len(candidates):
                raise IdentityResolutionError(
                    f"confirm_index {confirm_index} is outside the {len(candidates)} available candidates"
                )
            return IdentityResolution(
                query=normalized_query,
                status="confirmed",
                candidates=candidates,
                selected=candidates[confirm_index],
                warnings=warnings,
                resolved_at=utc_now(),
            )

        status = "ambiguous" if len(candidates) > 1 else "awaiting_confirmation"
        return IdentityResolution(
            query=normalized_query,
            status=status,
            candidates=candidates,
            warnings=warnings,
            resolved_at=utc_now(),
        )

    @staticmethod
    def _candidate_key(candidate: IdentityCandidate) -> str:
        segment_titles = [segment.title for segment in candidate.segments]
        segment_key = "|".join(normalize_title(title) for title in segment_titles)
        container_title = candidate.container_title or candidate.episode_title
        return "|".join(
            [
                normalize_content_type(candidate.content_type),
                normalize_title(candidate.series_title or candidate.title),
                normalize_title(container_title),
                segment_key,
            ]
        )

    def require_confirmed(
        self,
        query: IdentityQuery,
        *,
        confirm_index: int | None = None,
    ) -> IdentityResolution:
        resolution = self.resolve(query, confirm_index=confirm_index)
        if resolution.selected is None:
            raise IdentityConfirmationRequired(
                "Episode identity needs explicit user confirmation. "
                "Inspect episode_identity.json and rerun with confirm_index."
            )
        return resolution


def write_identity_artifact(path, resolution: IdentityResolution) -> None:
    write_json(path, resolution.to_dict())
