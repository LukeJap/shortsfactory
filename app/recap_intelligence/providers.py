"""Provider adapters for identity resolution and episode research."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import os
import re
from typing import Any, Callable
from urllib.parse import quote

import requests

from .identity import (
    normalize_content_type,
    normalize_title,
    parse_compound_title,
    title_similarity,
)
from .models import IdentityCandidate, IdentityQuery, IdentitySegment, utc_now


DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_USER_AGENT = os.getenv(
    "SHORTSFACTORY_WIKIMEDIA_USER_AGENT",
    "ShortsFactory/1.0 (AI Recap research; local desktop app)",
)
JsonFetcher = Callable[..., Any]


class ProviderError(RuntimeError):
    """Raised when a provider response is unavailable or malformed."""


def request_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise ProviderError(f"Request failed for {url}: {exc}") from exc


def strip_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _episode_numbers(value: Any) -> tuple[int | None, int | None]:
    if not isinstance(value, dict):
        return None, None
    try:
        season = int(value.get("season")) if value.get("season") is not None else None
    except (TypeError, ValueError):
        season = None
    try:
        number = int(value.get("number")) if value.get("number") is not None else None
    except (TypeError, ValueError):
        number = None
    return season, number


def _requested_segment_titles(query: IdentityQuery) -> tuple[str, ...]:
    titles = tuple(
        str(value).strip()
        for value in query.segment_titles
        if str(value).strip()
    )
    if titles:
        return titles
    value = query.segment_title or query.container_title
    return tuple(parse_compound_title(value)) if value else ()


@dataclass
class ResearchPacket:
    provider: str
    title: str
    url: str
    source_type: str
    reliability: float
    short_synopsis: str = ""
    detailed_synopsis: str = ""
    characters: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    plot_points: list[dict[str, Any]] = field(default_factory=list)
    character_motivations: list[dict[str, Any]] = field(default_factory=list)
    jokes_or_payoffs: list[str] = field(default_factory=list)
    transcript_events: list[dict[str, Any]] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    retrieved_at: str = field(default_factory=utc_now)
    segment_title: str = ""
    segment_id: str = ""
    segment_packets: list["ResearchPacket"] = field(default_factory=list)
    assessment_status: str = "unassessed"
    assessment_reason: str = ""
    identity_context: dict[str, Any] = field(default_factory=dict)

    def source_record(self) -> dict[str, Any]:
        record = {
            "provider": self.provider,
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
            "retrieved_at": self.retrieved_at,
            "reliability": round(max(0.0, min(1.0, self.reliability)), 4),
            "claims": list(self.claims),
        }
        if self.segment_id or self.segment_title:
            record["segment_id"] = self.segment_id
            record["segment_title"] = self.segment_title
        if self.assessment_status != "unassessed":
            record["assessment_status"] = self.assessment_status
        if self.assessment_reason:
            record["assessment_reason"] = self.assessment_reason
        if self.identity_context:
            record["identity_context"] = dict(self.identity_context)
        if self.transcript_events:
            record["transcript_event_count"] = len(self.transcript_events)
        return record

    def expanded_packets(self) -> list["ResearchPacket"]:
        return list(self.segment_packets) if self.segment_packets else [self]


class TVMazeProvider:
    name = "tvmaze"

    def __init__(
        self,
        fetch_json: JsonFetcher = request_json,
        base_url: str = "https://api.tvmaze.com",
    ):
        self.fetch_json = fetch_json
        self.base_url = base_url.rstrip("/")

    def resolve(self, query: IdentityQuery) -> list[IdentityCandidate]:
        if normalize_content_type(query.content_type) != "tv":
            return []
        payload = self.fetch_json(
            f"{self.base_url}/search/shows",
            params={"q": query.title},
        )
        if not isinstance(payload, list):
            raise ProviderError("TVMaze search response was not a list")
        requested_titles = _requested_segment_titles(query)
        results: list[IdentityCandidate] = []
        for item in payload[:10]:
            show = item.get("show", {}) if isinstance(item, dict) else {}
            if not isinstance(show, dict) or not show.get("id"):
                continue
            show_similarity = title_similarity(query.title, show.get("name", ""))
            if show_similarity <= 0:
                continue
            if not requested_titles and (
                query.season is None or query.container_episode is None
            ):
                results.append(self._show_candidate(show, show_similarity))
                continue

            show_id = show.get("id")
            episodes = self.fetch_json(
                f"{self.base_url}/shows/{show_id}/episodes",
                params={"specials": "1"},
            )
            if not isinstance(episodes, list):
                continue
            season_episodes = [
                episode
                for episode in episodes
                if isinstance(episode, dict)
                and (
                    query.season is None
                    or _episode_numbers(episode)[0] == query.season
                )
            ]
            matches: list[tuple[str, dict[str, Any], float]] = []
            for requested_title in requested_titles:
                options = [
                    (
                        episode,
                        title_similarity(
                            requested_title,
                            episode.get("name", ""),
                        ),
                    )
                    for episode in season_episodes
                    if title_similarity(
                        requested_title,
                        episode.get("name", ""),
                    ) >= 0.72
                ]
                if not options:
                    matches = []
                    break
                episode, score = max(
                    options,
                    key=lambda option: (
                        option[1],
                        -int(option[0].get("id", 0) or 0),
                    ),
                )
                matches.append((requested_title, episode, score))
            if requested_titles and len(matches) == len(requested_titles):
                results.append(
                    self._compound_candidate(
                        show,
                        query,
                        matches,
                        show_similarity,
                    )
                )

            if query.season is None or query.container_episode is None:
                continue
            numeric_episode = next(
                (
                    episode
                    for episode in season_episodes
                    if _episode_numbers(episode)
                    == (query.season, query.container_episode)
                ),
                None,
            )
            if numeric_episode is None:
                continue
            numeric_title = str(numeric_episode.get("name", ""))
            numeric_agreement = max(
                [
                    title_similarity(title, numeric_title)
                    for title in requested_titles
                ]
                or [0.0],
            )
            if requested_titles and numeric_agreement < 0.72:
                results.append(
                    self._numeric_conflict_candidate(
                        show,
                        query,
                        numeric_episode,
                        show_similarity,
                    )
                )
            elif not requested_titles:
                results.append(
                    self._compound_candidate(
                        show,
                        query,
                        [(numeric_title, numeric_episode, 1.0)],
                        show_similarity,
                        match_type="number",
                    )
                )
        return results

    @staticmethod
    def _show_candidate(
        show: dict[str, Any],
        confidence: float,
    ) -> IdentityCandidate:
        show_id = show.get("id")
        show_name = str(show.get("name", ""))
        return IdentityCandidate(
            canonical_id=f"tvmaze:show:{show_id}",
            content_type="tv",
            title=show_name,
            description=strip_html(show.get("summary")),
            url=str(show.get("url", "")),
            provider="tvmaze",
            provider_id=str(show_id),
            confidence=confidence * 0.72,
            alternate_numbering={"show_id": show_id},
            series_title=show_name,
            provider_ids={"tvmaze": {"series_id": show_id}},
        )

    def _segment_from_episode(
        self,
        requested_title: str,
        episode: dict[str, Any],
        confidence: float,
    ) -> IdentitySegment:
        season, number = _episode_numbers(episode)
        episode_id = episode.get("id")
        numbering = {
            "season": season,
            "episode": number,
            "provider_id": episode_id,
            "airdate": episode.get("airdate"),
        }
        return IdentitySegment(
            title=requested_title,
            provider_ids={self.name: str(episode_id)},
            provider_numbering={self.name: numbering},
            description=strip_html(episode.get("summary")),
            url=str(episode.get("url", "")),
            confidence=confidence,
        )

    def _compound_candidate(
        self,
        show: dict[str, Any],
        query: IdentityQuery,
        matches: list[tuple[str, dict[str, Any], float]],
        show_similarity: float,
        *,
        match_type: str = "compound_title",
    ) -> IdentityCandidate:
        segments = tuple(
            self._segment_from_episode(title, episode, score)
            for title, episode, score in matches
        )
        title = query.container_title.strip() or " / ".join(
            title for title, _, _ in matches
        )
        title_agreement = sum(score for _, _, score in matches) / len(matches)
        show_id = show.get("id")
        show_name = str(show.get("name", ""))
        return IdentityCandidate(
            canonical_id=f"tvmaze:container:{show_id}:{normalize_title(title)}",
            content_type="tv",
            title=show_name,
            episode_title=title,
            season=query.season,
            episode=None,
            description="\n\n".join(
                segment.description
                for segment in segments
                if segment.description
            ),
            url=str(show.get("url", "")),
            provider=self.name,
            provider_id=str(show_id),
            confidence=min(
                0.96,
                0.85 * title_agreement + 0.15 * show_similarity,
            ),
            alternate_numbering={"show_id": show_id},
            series_title=show_name,
            container_title=title,
            container_episode=query.container_episode,
            segments=segments,
            provider_ids={self.name: {"series_id": show_id}},
            provider_numbering={
                self.name: {
                    "segments": [
                        dict(segment.provider_numbering[self.name])
                        for segment in segments
                    ],
                },
            },
            source_match={
                "match_type": match_type,
                "title_agreement": round(title_agreement, 4),
                "numbering_conflict": False,
            },
        )

    def _numeric_conflict_candidate(
        self,
        show: dict[str, Any],
        query: IdentityQuery,
        episode: dict[str, Any],
        show_similarity: float,
    ) -> IdentityCandidate:
        title = str(episode.get("name", ""))
        segment = self._segment_from_episode(title, episode, 0.0)
        provider_season, provider_episode = _episode_numbers(episode)
        show_id = show.get("id")
        episode_id = episode.get("id")
        show_name = str(show.get("name", ""))
        return IdentityCandidate(
            canonical_id=f"tvmaze:episode:{episode_id}",
            content_type="tv",
            title=show_name,
            episode_title=title,
            season=query.season,
            episode=None,
            description=segment.description,
            url=segment.url,
            provider=self.name,
            provider_id=str(episode_id),
            confidence=min(0.35, max(0.1, show_similarity * 0.25)),
            alternate_numbering={"show_id": show_id},
            series_title=show_name,
            container_title=query.container_title or title,
            container_episode=query.container_episode,
            segments=(segment,),
            provider_ids={self.name: {"series_id": show_id}},
            provider_numbering={
                self.name: {
                    "segments": [
                        dict(segment.provider_numbering[self.name])
                    ],
                },
            },
            source_match={
                "match_type": "numbering_conflict",
                "title_agreement": 0.0,
                "numbering_conflict": True,
                "supplied_container": {
                    "season": query.season,
                    "episode": query.container_episode,
                    "title": query.container_title,
                },
                "provider_numeric": {
                    "season": provider_season,
                    "episode": provider_episode,
                    "title": title,
                },
            },
        )

    def research(self, identity: dict[str, Any]) -> ResearchPacket:
        if identity.get("segments"):
            return self._research_compound(identity)
        alternate = identity.get("alternate_numbering", {})
        provider_id = identity.get("provider_id") or alternate.get("episode_id")
        show_id = alternate.get("show_id") or provider_id
        if not show_id:
            raise ProviderError("TVMaze identity has no provider ID")
        show = self._safe_fetch(f"{self.base_url}/shows/{show_id}")
        episode = None
        if provider_id and str(provider_id) != str(show_id):
            episode = self._safe_fetch(
                f"{self.base_url}/episodes/{provider_id}"
            )
        if not episode and identity.get("season") is not None:
            episode = self._episode_by_number(
                show_id,
                identity.get("season"),
                identity.get("episode"),
            )
        return self._episode_packet(
            show if isinstance(show, dict) else {},
            episode if isinstance(episode, dict) else {},
            identity.get("episode_title") or identity.get("container_title", ""),
        )

    def _research_compound(self, identity: dict[str, Any]) -> ResearchPacket:
        provider_ids = identity.get("provider_ids", {}).get(self.name, {})
        alternate = identity.get("alternate_numbering", {})
        show_id = (
            provider_ids.get("series_id")
            if isinstance(provider_ids, dict)
            else None
        )
        show_id = show_id or alternate.get("show_id") or identity.get("provider_id")
        if not show_id:
            raise ProviderError("TVMaze compound identity has no series ID")
        show = self._safe_fetch(f"{self.base_url}/shows/{show_id}")
        show = show if isinstance(show, dict) else {}
        packets: list[ResearchPacket] = []
        for index, raw_segment in enumerate(identity.get("segments", []), start=1):
            if not isinstance(raw_segment, dict):
                continue
            segment_ids = raw_segment.get("provider_ids", {}).get(self.name, {})
            episode_id = (
                segment_ids
                if not isinstance(segment_ids, dict)
                else segment_ids.get("episode_id")
            )
            episode = (
                self._safe_fetch(f"{self.base_url}/episodes/{episode_id}")
                if episode_id
                else {}
            )
            if not episode:
                numbering = raw_segment.get(
                    "provider_numbering", {}
                ).get(self.name, {})
                episode = self._episode_by_number(
                    show_id,
                    numbering.get("season"),
                    numbering.get("episode"),
                )
            packets.append(
                self._episode_packet(
                    show,
                    episode if isinstance(episode, dict) else {},
                    str(raw_segment.get("title", "")),
                    segment_id=str(
                        raw_segment.get("segment_id")
                        or f"SEG_{index:02d}"
                    ),
                )
            )
        return ResearchPacket(
            provider=self.name,
            title=str(
                identity.get("container_title")
                or identity.get("series_title", "")
            ),
            url=str(show.get("url", "")),
            source_type="episode_container",
            reliability=0.78,
            claims=[claim for packet in packets for claim in packet.claims],
            segment_packets=packets,
        )

    def _safe_fetch(self, url: str, **kwargs: Any) -> Any:
        try:
            return self.fetch_json(url, **kwargs)
        except Exception:
            return {}

    def _episode_by_number(
        self,
        show_id: Any,
        season: Any,
        number: Any,
    ) -> dict[str, Any]:
        if season is None or number is None:
            return {}
        episodes = self._safe_fetch(
            f"{self.base_url}/shows/{show_id}/episodes",
            params={"specials": "1"},
        )
        if not isinstance(episodes, list):
            return {}
        return next(
            (
                episode
                for episode in episodes
                if _episode_numbers(episode) == (season, number)
            ),
            {},
        )

    def _episode_packet(
        self,
        show: dict[str, Any],
        episode: dict[str, Any],
        segment_title: str,
        *,
        segment_id: str = "",
    ) -> ResearchPacket:
        # A show description establishes series identity, but is never episode
        # plot evidence when the episode itself has no synopsis.
        summary = strip_html(episode.get("summary"))
        episode_name = str(episode.get("name") or segment_title or "")
        show_name = str(show.get("name", "") or "")
        title = " - ".join(
            part for part in (show.get("name"), episode_name) if part
        )
        claims = [summary] if summary else []
        if episode_name:
            claims.insert(0, f"Episode title: {episode_name}")
        return ResearchPacket(
            provider=self.name,
            title=title or segment_title,
            url=str(episode.get("url") or show.get("url") or ""),
            source_type="episode_database",
            reliability=0.78,
            short_synopsis=summary,
            detailed_synopsis=summary,
            characters=[
                person.get("person", {}).get("name", "")
                for person in show.get("_embedded", {}).get("cast", [])
                if isinstance(person, dict)
                and isinstance(person.get("person"), dict)
                and person.get("person", {}).get("name")
            ],
            plot_points=[
                {
                    "plot_id": "P001",
                    "order": 1,
                    "summary": summary,
                    "story_purpose": "synopsis",
                    "characters": [],
                    "locations": [],
                    "causal_parents": [],
                }
            ] if summary else [],
            claims=claims,
            segment_title=segment_title,
            segment_id=segment_id,
            identity_context={
                "expected_series_title": show_name,
                "expected_segment_title": segment_title,
                "found_series_title": show_name,
                "found_segment_title": episode_name,
                "provider_episode_id": episode.get("id"),
                "series_description": strip_html(show.get("summary")),
                "source_scope": "episode",
            },
        )


class TMDBProvider:
    name = "tmdb"

    def __init__(
        self,
        api_key: str = "",
        fetch_json: JsonFetcher = request_json,
        base_url: str = "https://api.themoviedb.org/3",
    ):
        self.api_key = api_key or os.getenv("TMDB_API_KEY", "")
        self.fetch_json = fetch_json
        self.base_url = base_url.rstrip("/")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.api_key:
            return {}
        merged = dict(params or {})
        merged["api_key"] = self.api_key
        return self.fetch_json(f"{self.base_url}{path}", params=merged)

    def resolve(self, query: IdentityQuery) -> list[IdentityCandidate]:
        if not self.api_key:
            return []
        if normalize_content_type(query.content_type) == "movie":
            payload = self._get("/search/movie", {"query": query.title})
            results = payload.get("results", []) if isinstance(payload, dict) else []
            candidates: list[IdentityCandidate] = []
            for item in results[:10]:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                item_id = item.get("id")
                item_title = str(item.get("title", ""))
                candidates.append(
                    IdentityCandidate(
                        canonical_id=f"tmdb:movie:{item_id}",
                        content_type="movie",
                        title=item_title,
                        description=str(item.get("overview", "")),
                        url=f"https://www.themoviedb.org/movie/{item_id}",
                        provider=self.name,
                        provider_id=str(item_id),
                        confidence=title_similarity(query.title, item_title),
                        series_title=item_title,
                        provider_ids={self.name: {"movie_id": item_id}},
                    )
                )
            return candidates

        payload = self._get("/search/tv", {"query": query.title})
        results = payload.get("results", []) if isinstance(payload, dict) else []
        candidates: list[IdentityCandidate] = []
        requested_titles = _requested_segment_titles(query)
        for show in results[:10]:
            if not isinstance(show, dict) or not show.get("id"):
                continue
            show_similarity = title_similarity(query.title, show.get("name", ""))
            if not requested_titles and (
                query.season is None or query.container_episode is None
            ):
                candidates.append(self._show_candidate(show, show_similarity))
                continue
            show_id = show.get("id")
            season_payload = (
                self._get(
                    f"/tv/{show_id}/season/{query.season}",
                )
                if query.season is not None
                else {}
            )
            episodes = (
                season_payload.get("episodes", [])
                if isinstance(season_payload, dict)
                else []
            )
            matches: list[tuple[str, dict[str, Any], float]] = []
            for requested_title in requested_titles:
                options = [
                    (
                        episode,
                        title_similarity(
                            requested_title,
                            episode.get("name", ""),
                        ),
                    )
                    for episode in episodes
                    if isinstance(episode, dict)
                    and title_similarity(
                        requested_title,
                        episode.get("name", ""),
                    ) >= 0.72
                ]
                if not options:
                    matches = []
                    break
                episode, score = max(options, key=lambda option: option[1])
                matches.append((requested_title, episode, score))
            if requested_titles and len(matches) == len(requested_titles):
                candidates.append(
                    self._compound_candidate(
                        show,
                        query,
                        matches,
                        show_similarity,
                    )
                )
            if query.season is None or query.container_episode is None:
                continue
            numeric = self._get(
                f"/tv/{show_id}/season/{query.season}/episode/{query.container_episode}",
            )
            if not isinstance(numeric, dict) or not numeric.get("id"):
                continue
            numeric_title = str(numeric.get("name", ""))
            agreement = max(
                [
                    title_similarity(title, numeric_title)
                    for title in requested_titles
                ]
                or [0.0],
            )
            if requested_titles and agreement < 0.72:
                candidates.append(
                    self._numeric_conflict_candidate(
                        show,
                        query,
                        numeric,
                        show_similarity,
                    )
                )
            elif not requested_titles:
                candidates.append(
                    self._compound_candidate(
                        show,
                        query,
                        [(numeric_title, numeric, 1.0)],
                        show_similarity,
                        match_type="number",
                    )
                )
        return candidates

    def _show_candidate(
        self,
        show: dict[str, Any],
        confidence: float,
    ) -> IdentityCandidate:
        show_id = show.get("id")
        show_name = str(show.get("name", ""))
        return IdentityCandidate(
            canonical_id=f"tmdb:tv:{show_id}",
            content_type="tv",
            title=show_name,
            description=str(show.get("overview", "")),
            url=f"https://www.themoviedb.org/tv/{show_id}",
            provider=self.name,
            provider_id=str(show_id),
            confidence=confidence * 0.72,
            alternate_numbering={"show_id": show_id},
            series_title=show_name,
            provider_ids={self.name: {"series_id": show_id}},
        )

    def _segment_from_episode(
        self,
        requested_title: str,
        episode: dict[str, Any],
        confidence: float,
        show_id: Any,
    ) -> IdentitySegment:
        season_number = episode.get("season_number", episode.get("season"))
        episode_number = episode.get("episode_number")
        return IdentitySegment(
            title=requested_title,
            provider_ids={self.name: str(episode.get("id", ""))},
            provider_numbering={
                self.name: {
                    "season": season_number,
                    "episode": episode_number,
                    "provider_id": episode.get("id"),
                }
            },
            description=str(episode.get("overview", "")),
            url=(
                f"https://www.themoviedb.org/tv/{show_id}/season/"
                f"{season_number or ''}/episode/{episode_number or ''}"
            ),
            confidence=confidence,
        )

    def _compound_candidate(
        self,
        show: dict[str, Any],
        query: IdentityQuery,
        matches: list[tuple[str, dict[str, Any], float]],
        show_similarity: float,
        *,
        match_type: str = "compound_title",
    ) -> IdentityCandidate:
        show_id = show.get("id")
        segments = tuple(
            self._segment_from_episode(
                title,
                episode,
                score,
                show_id,
            )
            for title, episode, score in matches
        )
        title = query.container_title.strip() or " / ".join(
            title for title, _, _ in matches
        )
        title_agreement = sum(score for _, _, score in matches) / len(matches)
        show_name = str(show.get("name", ""))
        return IdentityCandidate(
            canonical_id=f"tmdb:container:{show_id}:{normalize_title(title)}",
            content_type="tv",
            title=show_name,
            episode_title=title,
            season=query.season,
            episode=None,
            description="\n\n".join(
                segment.description
                for segment in segments
                if segment.description
            ),
            url=f"https://www.themoviedb.org/tv/{show_id}",
            provider=self.name,
            provider_id=str(show_id),
            confidence=min(
                0.96,
                0.85 * title_agreement + 0.15 * show_similarity,
            ),
            alternate_numbering={"show_id": show_id},
            series_title=show_name,
            container_title=title,
            container_episode=query.container_episode,
            segments=segments,
            provider_ids={self.name: {"series_id": show_id}},
            provider_numbering={
                self.name: {
                    "segments": [
                        dict(segment.provider_numbering[self.name])
                        for segment in segments
                    ],
                },
            },
            source_match={
                "match_type": match_type,
                "title_agreement": round(title_agreement, 4),
                "numbering_conflict": False,
            },
        )

    def _numeric_conflict_candidate(
        self,
        show: dict[str, Any],
        query: IdentityQuery,
        episode: dict[str, Any],
        show_similarity: float,
    ) -> IdentityCandidate:
        title = str(episode.get("name", ""))
        segment = self._segment_from_episode(
            title,
            episode,
            0.0,
            show.get("id"),
        )
        show_id = show.get("id")
        provider_numbering = segment.provider_numbering[self.name]
        return IdentityCandidate(
            canonical_id=(
                f"tmdb:episode:{show_id}:{query.season}:"
                f"{query.container_episode}"
            ),
            content_type="tv",
            title=str(show.get("name", "")),
            episode_title=title,
            season=query.season,
            episode=None,
            description=segment.description,
            url=segment.url,
            provider=self.name,
            provider_id=str(episode.get("id")),
            confidence=min(0.35, max(0.1, show_similarity * 0.25)),
            alternate_numbering={"show_id": show_id},
            series_title=str(show.get("name", "")),
            container_title=query.container_title or title,
            container_episode=query.container_episode,
            segments=(segment,),
            provider_ids={self.name: {"series_id": show_id}},
            provider_numbering={
                self.name: {
                    "segments": [
                        dict(segment.provider_numbering[self.name])
                    ],
                },
            },
            source_match={
                "match_type": "numbering_conflict",
                "title_agreement": 0.0,
                "numbering_conflict": True,
                "supplied_container": {
                    "season": query.season,
                    "episode": query.container_episode,
                    "title": query.container_title,
                },
                "provider_numeric": {
                    "season": provider_numbering.get("season"),
                    "episode": provider_numbering.get("episode"),
                    "title": title,
                },
            },
        )

    def research(self, identity: dict[str, Any]) -> ResearchPacket:
        if not self.api_key:
            raise ProviderError("TMDB_API_KEY is not configured")
        provider_ids = identity.get("provider_ids", {}).get(self.name, {})
        show_id = (
            provider_ids.get("series_id")
            if isinstance(provider_ids, dict)
            else None
        )
        show_id = show_id or identity.get("provider_id")
        if not show_id:
            raise ProviderError("TMDB identity has no series ID")
        if identity.get("segments"):
            packets: list[ResearchPacket] = []
            for index, raw_segment in enumerate(
                identity.get("segments", []),
                start=1,
            ):
                if not isinstance(raw_segment, dict):
                    continue
                numbering = raw_segment.get(
                    "provider_numbering", {}
                ).get(self.name, {})
                season = numbering.get("season")
                number = numbering.get("episode")
                episode = self._get(
                    f"/tv/{show_id}/season/{season}/episode/{number}"
                )
                packets.append(
                    self._episode_packet(
                        episode if isinstance(episode, dict) else {},
                        str(raw_segment.get("title", "")),
                        str(
                            raw_segment.get("segment_id")
                            or f"SEG_{index:02d}"
                        ),
                        show_id,
                        season,
                        number,
                    )
                )
            return ResearchPacket(
                provider=self.name,
                title=str(
                    identity.get("container_title")
                    or identity.get("series_title", "")
                ),
                url=f"https://www.themoviedb.org/tv/{show_id}",
                source_type="episode_container",
                reliability=0.82,
                claims=[claim for packet in packets for claim in packet.claims],
                segment_packets=packets,
            )
        alternate = identity.get("alternate_numbering", {})
        season = identity.get("season")
        number = identity.get("container_episode") or identity.get("episode")
        if season is None or number is None:
            raise ProviderError("TMDB identity lacks season/episode details")
        episode = self._get(
            f"/tv/{show_id}/season/{season}/episode/{number}"
        )
        return self._episode_packet(
            episode if isinstance(episode, dict) else {},
            str(identity.get("episode_title", "")),
            "",
            show_id,
            season,
            number,
        )

    def _episode_packet(
        self,
        episode: dict[str, Any],
        segment_title: str,
        segment_id: str,
        show_id: Any,
        season: Any,
        number: Any,
    ) -> ResearchPacket:
        summary = str(episode.get("overview", "") or "").strip()
        episode_name = str(episode.get("name") or segment_title or "")
        return ResearchPacket(
            provider=self.name,
            title=episode_name,
            url=(
                f"https://www.themoviedb.org/tv/{show_id}/season/"
                f"{season}/episode/{number}"
            ),
            source_type="episode_database",
            reliability=0.82,
            short_synopsis=summary,
            detailed_synopsis=summary,
            characters=[
                str(item.get("name", ""))
                for item in episode.get("guest_stars", [])
                if isinstance(item, dict) and item.get("name")
            ],
            plot_points=[
                {
                    "plot_id": "P001",
                    "order": 1,
                    "summary": summary,
                    "story_purpose": "synopsis",
                    "characters": [],
                    "locations": [],
                    "causal_parents": [],
                }
            ] if summary else [],
            claims=[summary] if summary else [],
            segment_title=segment_title,
            segment_id=segment_id,
            identity_context={
                "expected_segment_title": segment_title,
                "found_segment_title": episode_name,
                "provider_episode_id": episode.get("id"),
                "provider_series_id": show_id,
                "source_scope": "episode",
            },
        )


def _fandom_clean_wikitext(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\[\[(?:File|Image|Category):[^\]]+\]\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    for _ in range(5):
        updated = re.sub(
            r"\{\{(?:Link|L|Time|Sup|Debut|Mentioned|Flag)\|([^{}|]+)(?:\|[^{}]*)?\}\}",
            r"\1",
            text,
            flags=re.IGNORECASE,
        )
        updated = re.sub(r"\{\{[^{}]*\}\}", " ", updated)
        if updated == text:
            break
        text = updated
    text = re.sub(r"\[(?:https?://\S+)(?:\s+([^\]]+))?\]", lambda match: match.group(1) or " ", text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split()).strip(" -|:")


def _fandom_section(wikitext: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?mi)^(?P<marks>={{2,}})\s*{re.escape(heading)}\s*(?P=marks)\s*$"
    )
    match = pattern.search(wikitext)
    if not match:
        return ""
    level = len(match.group("marks"))
    tail = wikitext[match.end():]
    next_heading = re.search(rf"(?m)^={{2,{level}}}[^=].*?={{2,{level}}}\s*$", tail)
    return tail[: next_heading.start()] if next_heading else tail


def _fandom_infobox_field(wikitext: str, field_name: str) -> str:
    match = re.search(
        rf"(?mi)^\|\s*{re.escape(field_name)}\s*=\s*(.+?)\s*$",
        wikitext,
    )
    return _fandom_clean_wikitext(match.group(1)) if match else ""


def _fandom_list_items(section: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for line in section.splitlines():
        if not re.match(r"^\s*\*+", line):
            continue
        value = _fandom_clean_wikitext(re.sub(r"^\s*\*+", "", line))
        value = re.sub(r"\s+\([^)]*(?:debut|mentioned|appearance)[^)]*\)\s*$", "", value, flags=re.IGNORECASE)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return values


def _fandom_compact(value: str, word_limit: int = 32) -> str:
    words = " ".join(str(value or "").split()).split()
    if len(words) <= word_limit:
        return " ".join(words)
    return " ".join(words[:word_limit]).rstrip(",;:") + "."


def _fandom_plot_points(synopsis: str, source_url: str) -> list[dict[str, Any]]:
    cleaned = _fandom_clean_wikitext(synopsis)
    sentences: list[str] = []
    raw_sentences: list[str] = []
    pending = ""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", cleaned):
        pending = f"{pending} {sentence}".strip() if pending else sentence
        if pending.count('"') % 2:
            continue
        raw_sentences.append(pending)
        pending = ""
    if pending:
        raw_sentences.append(pending)
    for sentence in raw_sentences:
        if len(sentence.split()) < 5:
            continue
        clauses = [sentence]
        if len(sentence.split()) > 44:
            clauses = [
                value.strip(" ,")
                for value in re.split(r",\s+(?:and|but)\s+|;\s+", sentence)
                if len(value.split()) >= 5
            ] or [sentence]
        sentences.extend(_fandom_compact(value, 44) for value in clauses)
    sentences = sentences[:24]
    points: list[dict[str, Any]] = []
    for index, summary in enumerate(sentences, start=1):
        lower = summary.casefold()
        if index == 1:
            purpose = "setup"
        elif any(token in lower for token in ("realizes", "all along", "turns out", "reveals")):
            purpose = "reversal"
        elif index == len(sentences):
            purpose = "resolution"
        elif any(
            token in lower
            for token in (
                "attempts", "tries", "first,", "next,", "begs", "pleads", "offers"
            )
        ):
            purpose = "attempt_failure"
        elif any(token in lower for token in ("returns to", "reunites", "comes back")):
            purpose = "payoff_climax"
        elif index == 2:
            purpose = "inciting_incident"
        else:
            purpose = "escalation"
        points.append(
            {
                "plot_id": f"FANDOM_P{index:03d}",
                "order": index,
                "summary": summary,
                "story_purpose": purpose,
                "characters": [],
                "locations": [],
                "causal_parents": [],
                "importance": 0.9 if purpose in {"reversal", "resolution"} else 0.65,
                "provenance": [
                    {
                        "provider": "fandom",
                        "url": source_url,
                        "claim_type": "episode_synopsis_event",
                    }
                ],
            }
        )
    return points


def _fandom_top_level_parts(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    braces = 0
    brackets = 0
    index = 0
    while index < len(value):
        pair = value[index:index + 2]
        if pair == "{{":
            braces += 1
            index += 2
            continue
        if pair == "}}" and braces:
            braces -= 1
            index += 2
            continue
        if pair == "[[":
            brackets += 1
            index += 2
            continue
        if pair == "]]" and brackets:
            brackets -= 1
            index += 2
            continue
        if value[index] == "|" and braces == 0 and brackets == 0:
            parts.append(value[start:index])
            start = index + 1
        index += 1
    parts.append(value[start:])
    return parts


def _fandom_templates(wikitext: str, template_name: str) -> list[str]:
    marker = "{{" + template_name + "|"
    values: list[str] = []
    cursor = 0
    while True:
        start = wikitext.find(marker, cursor)
        if start < 0:
            return values
        depth = 0
        index = start
        while index < len(wikitext) - 1:
            pair = wikitext[index:index + 2]
            if pair == "{{":
                depth += 1
                index += 2
                continue
            if pair == "}}":
                depth -= 1
                index += 2
                if depth == 0:
                    values.append(wikitext[start + 2:index - 2])
                    cursor = index
                    break
                continue
            index += 1
        else:
            return values


def _fandom_transcript_events(wikitext: str, source_url: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in _fandom_templates(wikitext, "L"):
        parts = _fandom_top_level_parts(raw)
        if not parts or parts[0].strip().casefold() != "l":
            continue
        speaker = _fandom_clean_wikitext(parts[1]) if len(parts) >= 3 else ""
        content = "|".join(parts[2:] if len(parts) >= 3 else parts[1:])
        actions = [
            _fandom_compact(_fandom_clean_wikitext(value), 28)
            for value in re.findall(r"(?<!\[)\[([^\[\]]+)\](?!\])", content)
            if _fandom_clean_wikitext(value)
        ]
        dialogue = re.sub(r"(?<!\[)\[[^\[\]]+\](?!\])", " ", content)
        dialogue = _fandom_compact(_fandom_clean_wikitext(dialogue), 36)
        if not speaker and not dialogue and not actions:
            continue
        order = len(events) + 1
        events.append(
            {
                "event_id": f"FANDOM_T{order:04d}",
                "order": order,
                "speaker": speaker,
                "dialogue": dialogue,
                "actions": actions,
                "source_provider": "fandom",
                "source_url": source_url,
                "timing_authority": "none",
            }
        )
    return events


class FandomProvider:
    """Identity-locked supplemental episode intelligence from Fandom wikis."""

    name = "fandom"

    def __init__(
        self,
        fetch_json: JsonFetcher = request_json,
        wiki_urls: list[str] | tuple[str, ...] | None = None,
    ):
        self.fetch_json = fetch_json
        self.wiki_urls = tuple(wiki_urls or ())

    @staticmethod
    def _candidate_wikis(series_title: str) -> list[str]:
        configured = [
            value.strip().rstrip("/")
            for value in os.getenv("SHORTSFACTORY_FANDOM_WIKI_URLS", "").split(";")
            if value.strip()
        ]
        words = re.findall(r"[a-z0-9]+", series_title.casefold())
        slugs = ["".join(words)]
        if words and words[0] not in {"the", "a", "an"}:
            slugs.append(words[0])
        if words and words[0] in {"the", "a", "an"} and len(words) > 1:
            slugs.append("".join(words[1:]))
        output = configured + [f"https://{slug}.fandom.com" for slug in slugs if slug]
        return list(dict.fromkeys(output))

    @staticmethod
    def _wiki_matches_series(series_title: str, site_name: str, server: str) -> bool:
        corpus = re.sub(r"[^a-z0-9]+", "", f"{site_name} {server}".casefold())
        tokens = [
            token for token in re.findall(r"[a-z0-9]+", series_title.casefold())
            if len(token) >= 4 and token not in {"series", "show", "television"}
        ]
        return bool(tokens) and any(token in corpus for token in tokens)

    def _site_info(self, api_url: str) -> dict[str, Any]:
        payload = self.fetch_json(
            api_url,
            params={
                "action": "query",
                "meta": "siteinfo",
                "siprop": "general",
                "format": "json",
                "formatversion": 2,
            },
        )
        general = payload.get("query", {}).get("general", {}) if isinstance(payload, dict) else {}
        if not isinstance(general, dict) or not general:
            raise ProviderError("Fandom siteinfo was unavailable")
        return general

    def _parse_page(self, api_url: str, title: str) -> dict[str, Any] | None:
        payload = self.fetch_json(
            api_url,
            params={
                "action": "parse",
                "page": title,
                "prop": "wikitext|sections|displaytitle",
                "format": "json",
                "formatversion": 2,
                "redirects": 1,
            },
        )
        parsed = payload.get("parse") if isinstance(payload, dict) else None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _page_url(wiki_url: str, title: str) -> str:
        return f"{wiki_url}/wiki/{quote(title.replace(' ', '_'), safe='/()!$&\'*,;=:@?')}"

    def _identity_context(
        self,
        identity: dict[str, Any],
        segment: dict[str, Any],
        *,
        site_name: str,
        wiki_url: str,
        found_title: str,
        source_scope: str,
        sister_episode: str = "",
    ) -> dict[str, Any]:
        return {
            "expected_series_title": identity.get("series_title") or identity.get("title"),
            "expected_segment_title": segment.get("title"),
            "found_series_title": identity.get("series_title") or identity.get("title"),
            "found_segment_title": found_title.removesuffix("/transcript"),
            "expected_season": next(
                (
                    details.get("season")
                    for details in segment.get("provider_numbering", {}).values()
                    if isinstance(details, dict) and details.get("season") is not None
                ),
                identity.get("season"),
            ),
            "canonical_provider_ids": dict(segment.get("provider_ids", {})),
            "container_title": identity.get("container_title"),
            "fandom_wiki_name": site_name,
            "fandom_wiki_url": wiki_url,
            "sister_episode": sister_episode,
            "source_scope": source_scope,
        }

    def _locked_status(
        self,
        identity: dict[str, Any],
        segment_title: str,
        found_title: str,
        *,
        site_matches: bool,
        sister_episode: str,
    ) -> tuple[str, str]:
        if not site_matches:
            return "rejected_identity_mismatch", "Fandom wiki did not match the confirmed series"
        if title_similarity(segment_title, found_title.removesuffix("/transcript")) < 0.8:
            return "rejected_identity_mismatch", "Fandom page did not match the selected episode"
        container_parts = parse_compound_title(str(identity.get("container_title", "") or ""))
        siblings = [part for part in container_parts if normalize_title(part) != normalize_title(segment_title)]
        if sister_episode and siblings and max(title_similarity(sister_episode, value) for value in siblings) < 0.65:
            return "rejected_identity_mismatch", "Fandom sister episode conflicted with container identity"
        return "accepted", "series, selected episode, and container context matched"

    def _episode_packet(
        self,
        identity: dict[str, Any],
        segment: dict[str, Any],
        segment_id: str,
        *,
        wiki_url: str,
        site_name: str,
        site_matches: bool,
        parsed: dict[str, Any],
    ) -> ResearchPacket:
        title = str(parsed.get("title", segment.get("title", "")) or "")
        wikitext = str(parsed.get("wikitext", "") or "")
        source_url = self._page_url(wiki_url, title)
        sister_episode = _fandom_infobox_field(wikitext, "sisterep")
        status, reason = self._locked_status(
            identity,
            str(segment.get("title", "")),
            title,
            site_matches=site_matches,
            sister_episode=sister_episode,
        )
        accepted = status == "accepted"
        short_synopsis = _fandom_infobox_field(wikitext, "briefsummary") if accepted else ""
        synopsis_wikitext = _fandom_section(wikitext, "Synopsis") if accepted else ""
        plot_points = _fandom_plot_points(synopsis_wikitext, source_url) if accepted else []
        characters = _fandom_list_items(_fandom_section(wikitext, "Characters")) if accepted else []
        locations = _fandom_list_items(_fandom_section(wikitext, "Locations")) if accepted else []
        motivations: list[dict[str, Any]] = []
        for point in plot_points:
            match = re.search(
                r"\b([A-Z][A-Za-z' -]{1,30})\s+(?:wants|tries|attempts|hopes)\s+(.+)",
                point["summary"],
            )
            if match:
                motivations.append(
                    {
                        "character": match.group(1).strip(),
                        "motivation": _fandom_compact(match.group(2), 18),
                        "source_provider": self.name,
                        "source_url": source_url,
                    }
                )
        payoffs = [
            point["summary"]
            for point in plot_points
            if point["story_purpose"] in {"reversal", "resolution"}
        ]
        claims = []
        if accepted:
            claims = [f"Episode title: {segment.get('title', '')}"]
            claims.extend(f"Character: {value}" for value in characters)
            claims.extend(f"Location: {value}" for value in locations)
            if short_synopsis:
                claims.append(short_synopsis)
        return ResearchPacket(
            provider=self.name,
            title=title,
            url=source_url,
            source_type="fandom_episode",
            reliability=0.72,
            short_synopsis=short_synopsis,
            detailed_synopsis=" ".join(point["summary"] for point in plot_points),
            characters=characters,
            locations=locations,
            plot_points=plot_points,
            character_motivations=motivations,
            jokes_or_payoffs=payoffs,
            claims=claims,
            segment_title=str(segment.get("title", "")),
            segment_id=segment_id,
            assessment_status=status,
            assessment_reason=reason,
            identity_context=self._identity_context(
                identity,
                segment,
                site_name=site_name,
                wiki_url=wiki_url,
                found_title=title,
                source_scope="episode",
                sister_episode=sister_episode,
            ),
        )

    def _transcript_packet(
        self,
        identity: dict[str, Any],
        segment: dict[str, Any],
        segment_id: str,
        *,
        wiki_url: str,
        site_name: str,
        parsed: dict[str, Any],
    ) -> ResearchPacket | None:
        title = str(parsed.get("title", "") or "")
        expected_title = f"{segment.get('title', '')}/transcript"
        if title_similarity(expected_title, title) < 0.8:
            return None
        source_url = self._page_url(wiki_url, title)
        events = _fandom_transcript_events(str(parsed.get("wikitext", "") or ""), source_url)
        if not events:
            return None
        return ResearchPacket(
            provider=self.name,
            title=title,
            url=source_url,
            source_type="fandom_episode_transcript",
            reliability=0.7,
            transcript_events=events,
            claims=[f"Transcript subpage for {segment.get('title', '')}"],
            segment_title=str(segment.get("title", "")),
            segment_id=segment_id,
            assessment_status="accepted",
            assessment_reason="transcript belongs to the identity-locked episode page",
            identity_context=self._identity_context(
                identity,
                segment,
                site_name=site_name,
                wiki_url=wiki_url,
                found_title=title,
                source_scope="episode_transcript",
            ),
        )

    def research(self, identity: dict[str, Any]) -> ResearchPacket:
        series_title = str(identity.get("series_title") or identity.get("title") or "").strip()
        segments = [
            segment for segment in identity.get("segments", [])
            if isinstance(segment, dict) and segment.get("title")
        ]
        if not segments:
            segments = [{"title": identity.get("episode_title") or identity.get("container_title")}]
        wiki_candidates = list(self.wiki_urls) or self._candidate_wikis(series_title)
        failures: list[str] = []
        rejected_packets: list[ResearchPacket] = []
        for wiki_url in wiki_candidates:
            api_url = wiki_url.rstrip("/") + "/api.php"
            try:
                site_info = self._site_info(api_url)
            except Exception as exc:
                failures.append(f"{wiki_url}: {exc}")
                continue
            site_name = str(site_info.get("sitename", "") or "")
            server = str(site_info.get("server", wiki_url) or wiki_url)
            site_matches = self._wiki_matches_series(series_title, site_name, server)
            packets: list[ResearchPacket] = []
            for index, segment in enumerate(segments, start=1):
                title = str(segment.get("title", "") or "").strip()
                if not title:
                    continue
                try:
                    episode_page = self._parse_page(api_url, title)
                except Exception as exc:
                    failures.append(f"{wiki_url}/{title}: {exc}")
                    continue
                if episode_page is None:
                    continue
                segment_id = str(segment.get("segment_id") or f"SEG_{index:02d}")
                episode_packet = self._episode_packet(
                    identity,
                    segment,
                    segment_id,
                    wiki_url=wiki_url.rstrip("/"),
                    site_name=site_name,
                    site_matches=site_matches,
                    parsed=episode_page,
                )
                if episode_packet.assessment_status != "accepted":
                    rejected_packets.append(episode_packet)
                    continue
                packets.append(episode_packet)
                try:
                    transcript_page = self._parse_page(api_url, f"{title}/transcript")
                except Exception as exc:
                    failures.append(f"{wiki_url}/{title}/transcript: {exc}")
                    transcript_page = None
                if transcript_page is not None:
                    transcript_packet = self._transcript_packet(
                        identity,
                        segment,
                        segment_id,
                        wiki_url=wiki_url.rstrip("/"),
                        site_name=site_name,
                        parsed=transcript_page,
                    )
                    if transcript_packet is not None:
                        packets.append(transcript_packet)
            if packets:
                return ResearchPacket(
                    provider=self.name,
                    title=str(identity.get("container_title") or series_title),
                    url=packets[0].url,
                    source_type="fandom_episode_container",
                    reliability=0.72,
                    claims=[claim for packet in packets for claim in packet.claims],
                    segment_packets=packets,
                )
        if rejected_packets:
            return ResearchPacket(
                provider=self.name,
                title=str(identity.get("container_title") or series_title),
                url=rejected_packets[0].url,
                source_type="fandom_episode_container",
                reliability=0.0,
                segment_packets=rejected_packets,
            )
        detail = "; ".join(failures[-3:])
        raise ProviderError(f"Fandom returned no identity-locked episode page{': ' + detail if detail else ''}")


class MediaWikiProvider:
    name = "wikipedia"

    def __init__(
        self,
        fetch_json: JsonFetcher = request_json,
        api_url: str = "https://en.wikipedia.org/w/api.php",
    ):
        self.fetch_json = fetch_json
        self.api_url = api_url

    def resolve(self, query: IdentityQuery) -> list[IdentityCandidate]:
        requested_titles = _requested_segment_titles(query)
        titles = requested_titles or (query.title,)
        matches: list[IdentitySegment] = []
        for requested_title in titles:
            search = (
                f"{query.title} {requested_title}"
                if requested_title != query.title
                else query.title
            )
            payload = self.fetch_json(
                self.api_url,
                params={
                    "action": "opensearch",
                    "search": search,
                    "limit": 5,
                    "namespace": 0,
                    "format": "json",
                },
            )
            if not isinstance(payload, list) or len(payload) < 4:
                continue
            options = list(zip(payload[1], payload[2], payload[3]))
            if not options:
                continue
            page_title, description, url = max(
                options,
                key=lambda item: title_similarity(requested_title, item[0]),
            )
            score = title_similarity(requested_title, page_title)
            if score <= 0:
                continue
            matches.append(
                IdentitySegment(
                    title=requested_title,
                    provider_ids={self.name: str(page_title)},
                    provider_numbering={
                        self.name: {"page": str(page_title)}
                    },
                    description=str(description or ""),
                    url=str(url or ""),
                    confidence=score * 0.6,
                )
            )
        if not matches:
            return []
        container_title = query.container_title or " / ".join(
            segment.title for segment in matches
        )
        title_agreement = sum(
            segment.confidence for segment in matches
        ) / len(matches)
        return [
            IdentityCandidate(
                canonical_id=(
                    f"wikipedia:container:{normalize_title(query.title)}:"
                    f"{normalize_title(container_title)}"
                ),
                content_type=normalize_content_type(query.content_type),
                title=query.title,
                episode_title=container_title,
                season=query.season,
                episode=None,
                description="\n\n".join(
                    segment.description for segment in matches
                ),
                url=matches[0].url,
                provider=self.name,
                provider_id=matches[0].provider_ids[self.name],
                confidence=min(0.75, 0.45 + 0.35 * title_agreement),
                series_title=query.title,
                container_title=container_title,
                container_episode=query.container_episode,
                segments=tuple(matches),
                provider_ids={
                    self.name: {
                        "segments": [
                            segment.provider_ids[self.name]
                            for segment in matches
                        ],
                    }
                },
                provider_numbering={
                    self.name: {
                        "segments": [
                            dict(segment.provider_numbering[self.name])
                            for segment in matches
                        ],
                    }
                },
                source_match={
                    "match_type": "title_search",
                    "title_agreement": round(title_agreement, 4),
                    "numbering_conflict": False,
                },
            )
        ]

    def research(self, identity: dict[str, Any]) -> ResearchPacket:
        series_title = str(
            identity.get("series_title") or identity.get("title") or ""
        ).strip()
        if identity.get("segments"):
            packets = [
                self._research_page(
                    str(segment.get("title", "")),
                    segment.get("provider_ids", {}).get(self.name),
                    str(segment.get("segment_id") or f"SEG_{index:02d}"),
                    series_title=series_title,
                    season=identity.get("season"),
                    container_title=str(identity.get("container_title", "")),
                )
                for index, segment in enumerate(
                    identity.get("segments", []),
                    start=1,
                )
                if isinstance(segment, dict)
            ]
            packets = [packet for packet in packets if packet is not None]
            return ResearchPacket(
                provider=self.name,
                title=str(
                    identity.get("container_title")
                    or identity.get("series_title", "")
                ),
                url=packets[0].url if packets else "",
                source_type="episode_container",
                reliability=0.68,
                claims=[claim for packet in packets for claim in packet.claims],
                segment_packets=packets,
            )
        packet = self._research_page(
            str(
                identity.get("episode_title")
                or identity.get("container_title")
                or identity.get("series_title", "")
            ),
            identity.get("provider_id"),
            "",
            series_title=series_title,
            season=identity.get("season"),
            container_title=str(identity.get("container_title", "")),
        )
        if packet is None:
            raise ProviderError("Wikipedia returned no research page")
        return packet

    def _research_page(
        self,
        query: str,
        page_hint: Any,
        segment_id: str,
        *,
        series_title: str,
        season: Any = None,
        container_title: str = "",
    ) -> ResearchPacket | None:
        search = " ".join(
            part
            for part in (
                f'"{series_title}"' if series_title else "",
                f'"{query}"' if query else "",
                "episode" if series_title and query else "",
                f"season {season}" if season is not None else "",
                str(page_hint or ""),
            )
            if part
        )
        payload = self.fetch_json(
            self.api_url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": search,
                "srlimit": 1,
                "prop": "info",
                "inprop": "url",
                "format": "json",
            },
        )
        result = (
            payload.get("query", {}).get("search", [])
            if isinstance(payload, dict)
            else []
        )
        if not result:
            return None
        page = result[0]
        page_id = page.get("pageid")
        title = str(page.get("title", ""))
        extract_payload = self.fetch_json(
            self.api_url,
            params={
                "action": "query",
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "pageids": page_id,
                "format": "json",
            },
        )
        pages = (
            extract_payload.get("query", {}).get("pages", {})
            if isinstance(extract_payload, dict)
            else {}
        )
        details = (
            next(iter(pages.values()), {})
            if isinstance(pages, dict) and pages
            else {}
        )
        summary = str(details.get("extract", "") or "").strip()
        url = str(
            details.get("fullurl")
            or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
        )
        identity_corpus = " ".join((title, summary, url))
        series_score = title_similarity(series_title, identity_corpus)
        segment_score = title_similarity(query, identity_corpus)
        status = "accepted"
        reason = "page matched the confirmed series and selected segment"
        if series_title and series_score < 0.45:
            status = "rejected_identity_mismatch"
            reason = (
                f"title matched but page did not match confirmed series "
                f"{series_title}"
            )
        elif query and segment_score < 0.45:
            status = "rejected_low_relevance"
            reason = f"page did not establish selected segment {query}"

        accepted = status == "accepted"
        return ResearchPacket(
            provider=self.name,
            title=title,
            url=url,
            source_type="encyclopedia",
            reliability=0.68,
            short_synopsis=summary if accepted else "",
            detailed_synopsis=summary if accepted else "",
            plot_points=[
                {
                    "plot_id": "P001",
                    "order": 1,
                    "summary": summary,
                    "story_purpose": "synopsis",
                    "characters": [],
                    "locations": [],
                    "causal_parents": [],
                }
            ] if summary and accepted else [],
            claims=[summary] if summary and accepted else [],
            segment_title=query,
            segment_id=segment_id,
            assessment_status=status,
            assessment_reason=reason,
            identity_context={
                "expected_series_title": series_title,
                "expected_segment_title": query,
                "expected_season": season,
                "container_title": container_title,
                "research_query": search,
                "found_page_title": title,
                "series_match_score": round(series_score, 4),
                "segment_match_score": round(segment_score, 4),
                "source_scope": "episode_or_segment",
            },
        )
