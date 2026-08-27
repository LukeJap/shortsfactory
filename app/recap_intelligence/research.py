"""Research collection and provenance-preserving dossier synthesis."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol, Sequence

from .models import IdentityCandidate, SCHEMA_VERSION, utc_now
from .providers import ResearchPacket


class ResearchProvider(Protocol):
    name: str

    def research(self, identity: dict[str, Any]) -> ResearchPacket:
        ...


class ResearchUnavailableError(RuntimeError):
    """Raised when no research provider can support a confirmed identity."""


class ResearchGroundingError(RuntimeError):
    """Raised when accepted research is contaminated or identity-ambiguous."""


SOURCE_OUTCOMES = {
    "accepted",
    "rejected_identity_mismatch",
    "rejected_low_relevance",
    "rejected_duplicate",
    "provider_error",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", str(value or "").casefold())
        if len(token) > 2
    }


def _unique(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def _synopsis_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _normalize_plot_point(
    raw: Any,
    index: int,
    provider: str,
) -> dict[str, Any] | None:
    if isinstance(raw, str):
        raw = {"summary": raw}
    if not isinstance(raw, dict):
        return None
    summary = str(raw.get("summary", "") or "").strip()
    if not summary:
        return None
    try:
        order = int(raw.get("order", index) or index)
    except (TypeError, ValueError):
        order = index
    plot_id = str(raw.get("plot_id") or f"{provider.upper()}_P{index:03d}")
    provenance = [
        dict(item)
        for item in raw.get("provenance", [])
        if isinstance(item, dict)
    ] if isinstance(raw.get("provenance", []), list) else []
    return {
        "plot_id": plot_id,
        "order": order,
        "summary": summary,
        "story_purpose": str(
            raw.get("story_purpose") or raw.get("phase") or ""
        ).strip(),
        "characters": _unique(raw.get("characters", [])),
        "locations": _unique(raw.get("locations", [])),
        "causal_parents": _unique(
            raw.get("causal_parents") or raw.get("depends_on") or []
        ),
        "source_providers": [provider],
        "provenance": provenance,
        "importance": float(raw.get("importance", 0.5) or 0.5),
    }


@dataclass
class ResearchResult:
    dossier: dict[str, Any]
    packets: list[ResearchPacket]
    warnings: list[str]


class ResearchService:
    """Collect provider facts without merging unrelated story segments."""

    def __init__(self, providers: Sequence[ResearchProvider]):
        self.providers = list(providers)

    def collect(
        self,
        identity: IdentityCandidate | dict[str, Any],
    ) -> ResearchResult:
        identity_dict = (
            identity.to_dict()
            if isinstance(identity, IdentityCandidate)
            else dict(identity)
        )
        packets: list[ResearchPacket] = []
        evaluations: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_sources: set[tuple[str, str]] = set()
        for provider in self.providers:
            try:
                packet = provider.research(identity_dict)
            except Exception as exc:
                provider_name = getattr(
                    provider, "name", type(provider).__name__
                )
                reason = f"{provider_name} unavailable: {exc}"
                warnings.append(reason)
                evaluations.append(
                    {
                        "provider": provider_name,
                        "title": "",
                        "url": "",
                        "status": "provider_error",
                        "reason": reason,
                        "claims": [],
                    }
                )
                continue
            if not isinstance(packet, ResearchPacket):
                provider_name = getattr(
                    provider, "name", type(provider).__name__
                )
                reason = f"{provider_name} returned an invalid research packet"
                warnings.append(reason)
                evaluations.append(
                    {
                        "provider": provider_name,
                        "title": "",
                        "url": "",
                        "status": "provider_error",
                        "reason": reason,
                        "claims": [],
                    }
                )
                continue
            for child in packet.expanded_packets():
                status, reason = self._classify_packet(
                    identity_dict,
                    child,
                    seen_sources,
                )
                child.assessment_status = status
                child.assessment_reason = reason
                evaluation = child.source_record()
                evaluation["status"] = status
                evaluation["reason"] = reason
                if status != "accepted":
                    evaluation["claims"] = []
                evaluations.append(evaluation)
                if status == "accepted":
                    packets.append(child)
                else:
                    warnings.append(
                        f"{child.provider} rejected {child.title or child.url}: {reason}"
                    )

        if not packets:
            raise ResearchUnavailableError(
                "No research provider returned usable facts for the confirmed identity."
            )
        return ResearchResult(
            dossier=self._build_dossier(
                identity_dict,
                packets,
                warnings,
                evaluations,
            ),
            packets=packets,
            warnings=warnings,
        )

    @staticmethod
    def _classify_packet(
        identity: dict[str, Any],
        packet: ResearchPacket,
        seen_sources: set[tuple[str, str]],
    ) -> tuple[str, str]:
        status = str(packet.assessment_status or "unassessed")
        if status != "unassessed":
            if status not in SOURCE_OUTCOMES:
                return "rejected_low_relevance", "provider returned an unknown assessment"
            if status != "accepted":
                return status, packet.assessment_reason or "provider rejected source"

        selected_titles = [
            str(segment.get("title", "") or "").strip()
            for segment in identity.get("segments", [])
            if isinstance(segment, dict) and segment.get("title")
        ]
        container_parts = re.split(
            r"\s*&\s*|\s+/\s+|\s+\+\s+",
            str(identity.get("container_title", "") or ""),
        )
        is_compound = len([part for part in container_parts if part.strip()]) > 1
        if selected_titles and is_compound:
            if not packet.segment_title:
                return (
                    "rejected_low_relevance",
                    "compound source packet was not tied to the selected segment",
                )
            if max(
                _synopsis_similarity(packet.segment_title, title)
                for title in selected_titles
            ) < 0.5 and max(
                (
                    1.0
                    if packet.segment_title.casefold() == title.casefold()
                    else 0.0
                )
                for title in selected_titles
            ) < 1.0:
                return (
                    "rejected_identity_mismatch",
                    "source belongs to a different compound-episode segment",
                )

        context = packet.identity_context or {}
        expected_series = str(
            identity.get("series_title") or identity.get("title") or ""
        ).strip()
        found_series = str(context.get("found_series_title", "") or "").strip()
        if expected_series and found_series:
            if _synopsis_similarity(expected_series, found_series) < 0.5:
                return (
                    "rejected_identity_mismatch",
                    f"source series {found_series} does not match {expected_series}",
                )
        found_segment = str(context.get("found_segment_title", "") or "").strip()
        if packet.segment_title and found_segment:
            if _synopsis_similarity(packet.segment_title, found_segment) < 0.5:
                return (
                    "rejected_identity_mismatch",
                    f"source episode {found_segment} does not match {packet.segment_title}",
                )
        if context.get("source_scope") == "series" and (
            packet.claims or packet.plot_points or packet.short_synopsis
        ):
            return (
                "rejected_low_relevance",
                "generic series description cannot supply episode plot evidence",
            )

        source_key = (
            packet.provider.casefold(),
            (packet.url or packet.title).strip().casefold(),
        )
        if source_key[1] and source_key in seen_sources:
            return "rejected_duplicate", "duplicate provider source"
        seen_sources.add(source_key)
        return "accepted", packet.assessment_reason or "identity and segment matched"

    def _build_dossier(
        self,
        identity: dict[str, Any],
        packets: list[ResearchPacket],
        warnings: list[str],
        evaluations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        expanded_packets = list(packets)
        groups: dict[str, list[ResearchPacket]] = {}
        for packet in expanded_packets:
            key = packet.segment_id or "__single__"
            groups.setdefault(key, []).append(packet)

        segment_records: list[dict[str, Any]] = []
        all_plot_points: list[dict[str, Any]] = []
        for group_key, group_packets in groups.items():
            is_segmented = group_key != "__single__"
            segment_id = "" if not is_segmented else group_key
            segment_title = next(
                (
                    packet.segment_title
                    for packet in group_packets
                    if packet.segment_title
                ),
                "",
            )
            points = self._merge_plot_points(
                group_packets,
                segment_id=segment_id,
            )
            segment_records.append(
                self._segment_record(
                    segment_id=segment_id,
                    segment_title=segment_title,
                    packets=group_packets,
                    plot_points=points,
                )
            )
            all_plot_points.extend(points)

        all_plot_points.sort(
            key=lambda point: (
                int(point.get("order", 0)),
                str(point.get("segment_id", "")),
                str(point.get("plot_id", "")),
            )
        )
        for index, point in enumerate(all_plot_points, start=1):
            point["order"] = index
            if not point.get("segment_id"):
                point["plot_id"] = f"P{index:03d}"

        phase_values = {
            str(point.get("story_purpose", "")).casefold(): point["summary"]
            for point in all_plot_points
            if point.get("story_purpose")
        }
        synopsis_values = [
            packet.short_synopsis
            for packet in expanded_packets
            if packet.short_synopsis
        ]
        detailed_values = [
            packet.detailed_synopsis
            for packet in expanded_packets
            if packet.detailed_synopsis
        ]
        disagreements = [
            disagreement
            for group_packets in groups.values()
            for disagreement in self._find_disagreements(group_packets)
        ]
        reliabilities = [
            packet.reliability
            for packet in expanded_packets
            if packet.reliability is not None
        ]
        confidence = sum(reliabilities) / len(reliabilities) if reliabilities else 0.0
        if len(expanded_packets) > 1:
            confidence = min(0.98, confidence + 0.06)
        if disagreements:
            confidence = max(0.2, confidence - 0.12)

        dossier: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": utc_now(),
            "canonical_identity": identity,
            "sources": [
                packet.source_record() for packet in expanded_packets
            ],
            "source_evaluations": evaluations,
            "characters": _unique(
                [
                    character
                    for packet in expanded_packets
                    for character in packet.characters
                ]
            ),
            "locations": _unique(
                [
                    location
                    for packet in expanded_packets
                    for location in packet.locations
                ]
            ),
            "short_synopsis": synopsis_values[0] if synopsis_values else "",
            "detailed_synopsis": detailed_values[0] if detailed_values else "",
            "ordered_plot_points": all_plot_points,
            "setup": phase_values.get("setup", ""),
            "inciting_incident": phase_values.get("inciting_incident", ""),
            "escalation": phase_values.get("escalation", ""),
            "complications": phase_values.get("complication", ""),
            "reversals": phase_values.get("reversal", ""),
            "climax": phase_values.get("climax", ""),
            "resolution": phase_values.get("resolution", ""),
            "character_motivations": [
                motivation
                for packet in expanded_packets
                for motivation in packet.character_motivations
                if isinstance(motivation, dict)
            ],
            "jokes_or_payoffs": _unique(
                [
                    joke
                    for packet in expanded_packets
                    for joke in packet.jokes_or_payoffs
                ]
            ),
            "transcript_events": self._merge_transcript_events(expanded_packets),
            "source_disagreements": disagreements,
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "warnings": list(warnings),
        }

        if any(record["segment_id"] for record in segment_records):
            dossier["segments"] = segment_records
            dossier["container"] = {
                "identity": identity,
                "title": identity.get("container_title")
                or identity.get("series_title", ""),
                "season": identity.get("season"),
                "container_episode": identity.get("container_episode"),
                "segments": segment_records,
            }
        return dossier

    def _merge_plot_points(
        self,
        packets: list[ResearchPacket],
        *,
        segment_id: str,
    ) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        by_key: dict[str, dict[str, Any]] = {}
        source_lookup: dict[tuple[int, str], dict[str, Any]] = {}
        pending_parents: list[tuple[dict[str, Any], int, list[str]]] = []
        for packet_index, packet in enumerate(packets):
            for index, raw in enumerate(packet.plot_points, start=1):
                point = _normalize_plot_point(raw, index, packet.provider)
                if point is None:
                    continue
                old_id = str(point["plot_id"])
                parents = list(point.get("causal_parents", []))
                key = " ".join(sorted(_tokens(point["summary"])))
                existing = by_key.get(key)
                if existing is None:
                    by_key[key] = point
                    points.append(point)
                    target = point
                else:
                    target = existing
                    existing["source_providers"] = _unique(
                        existing.get("source_providers", [])
                        + [packet.provider]
                    )
                    existing["characters"] = _unique(
                        existing.get("characters", []) + point["characters"]
                    )
                    existing["locations"] = _unique(
                        existing.get("locations", []) + point["locations"]
                    )
                    existing["provenance"] = list(existing.get("provenance", [])) + [
                        item
                        for item in point.get("provenance", [])
                        if item not in existing.get("provenance", [])
                    ]
                source_lookup[(packet_index, old_id)] = target
                pending_parents.append((target, packet_index, parents))

        points.sort(
            key=lambda point: (
                int(point.get("order", 0)),
                str(point.get("plot_id", "")),
            )
        )
        target_to_new = {
            id(point): (
                f"{segment_id}_P{index:03d}"
                if segment_id
                else f"P{index:03d}"
            )
            for index, point in enumerate(points, start=1)
        }
        target_parents: dict[int, set[int]] = {}
        for target, packet_index, parent_ids in pending_parents:
            for parent_id in parent_ids:
                parent = source_lookup.get((packet_index, parent_id))
                if parent is not None and parent is not target:
                    target_parents.setdefault(id(target), set()).add(id(parent))
        for index, point in enumerate(points, start=1):
            point["plot_id"] = target_to_new[id(point)]
            point["order"] = index
            if segment_id:
                point["segment_id"] = segment_id
            point["causal_parents"] = [
                target_to_new[parent]
                for parent in sorted(target_parents.get(id(point), set()))
                if parent in target_to_new
            ]
        return points

    def _segment_record(
        self,
        *,
        segment_id: str,
        segment_title: str,
        packets: list[ResearchPacket],
        plot_points: list[dict[str, Any]],
    ) -> dict[str, Any]:
        purposes = {
            str(point.get("story_purpose", "")).casefold(): point["summary"]
            for point in plot_points
            if point.get("story_purpose")
        }
        return {
            "segment_id": segment_id,
            "title": segment_title
            or next((packet.title for packet in packets), ""),
            "sources": [packet.source_record() for packet in packets],
            "characters": _unique(
                [
                    character
                    for packet in packets
                    for character in packet.characters
                ]
            ),
            "locations": _unique(
                [
                    location
                    for packet in packets
                    for location in packet.locations
                ]
            ),
            "short_synopsis": next(
                (
                    packet.short_synopsis
                    for packet in packets
                    if packet.short_synopsis
                ),
                "",
            ),
            "detailed_synopsis": next(
                (
                    packet.detailed_synopsis
                    for packet in packets
                    if packet.detailed_synopsis
                ),
                "",
            ),
            "ordered_plot_points": plot_points,
            "setup": purposes.get("setup", ""),
            "inciting_incident": purposes.get("inciting_incident", ""),
            "escalation": purposes.get("escalation", ""),
            "complications": purposes.get("complication", ""),
            "reversals": purposes.get("reversal", ""),
            "climax": purposes.get("climax", ""),
            "resolution": purposes.get("resolution", ""),
            "character_motivations": [
                motivation
                for packet in packets
                for motivation in packet.character_motivations
                if isinstance(motivation, dict)
            ],
            "jokes_or_payoffs": _unique(
                [
                    joke
                    for packet in packets
                    for joke in packet.jokes_or_payoffs
                ]
            ),
            "transcript_events": self._merge_transcript_events(packets),
            "source_disagreements": self._find_disagreements(packets),
            "confidence": round(
                sum(packet.reliability for packet in packets) / len(packets),
                4,
            ) if packets else 0.0,
        }

    @staticmethod
    def _merge_transcript_events(
        packets: list[ResearchPacket],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for packet in packets:
            for raw in packet.transcript_events:
                if not isinstance(raw, dict):
                    continue
                speaker = " ".join(str(raw.get("speaker", "") or "").split())
                dialogue = " ".join(str(raw.get("dialogue", "") or "").split())
                actions = _unique(raw.get("actions", []))
                key = (speaker.casefold(), dialogue.casefold(), "|".join(actions).casefold())
                if key in seen or not any(key):
                    continue
                seen.add(key)
                event = dict(raw)
                event["speaker"] = speaker
                event["dialogue"] = dialogue
                event["actions"] = actions
                event["segment_id"] = packet.segment_id
                event["segment_title"] = packet.segment_title
                event["source_provider"] = packet.provider
                event["source_url"] = packet.url
                event["timing_authority"] = "none"
                events.append(event)
        for index, event in enumerate(events, start=1):
            event["order"] = index
            event["source_event_id"] = event.get("event_id", "")
            event["event_id"] = f"{str(event.get('source_provider', 'research')).upper()}_T{index:04d}"
        return events

    @staticmethod
    def _find_disagreements(
        packets: list[ResearchPacket],
    ) -> list[dict[str, Any]]:
        disagreements: list[dict[str, Any]] = []
        for index, left in enumerate(packets):
            for right in packets[index + 1 :]:
                if not left.short_synopsis or not right.short_synopsis:
                    continue
                similarity = _synopsis_similarity(
                    left.short_synopsis,
                    right.short_synopsis,
                )
                if similarity >= 0.65:
                    continue
                disagreements.append(
                    {
                        "segment_id": left.segment_id or right.segment_id,
                        "sources": [left.provider, right.provider],
                        "claim_type": "synopsis",
                        "left": left.short_synopsis,
                        "right": right.short_synopsis,
                        "similarity": round(similarity, 4),
                        "resolution": "local_source_video_is_final_authority",
                    }
                )
        return disagreements


def validate_research_grounding(dossier: dict[str, Any]) -> None:
    """Reject identity contamination before local story alignment starts."""
    errors: list[str] = []
    evaluations = dossier.get("source_evaluations", [])
    if not isinstance(evaluations, list):
        errors.append("research source evaluations are missing")
        evaluations = []

    accepted = [
        item
        for item in evaluations
        if isinstance(item, dict) and item.get("status") == "accepted"
    ]
    if not accepted:
        errors.append("no identity-validated research source was accepted")

    for item in evaluations:
        if not isinstance(item, dict):
            errors.append("research source evaluation is malformed")
            continue
        status = str(item.get("status", ""))
        if status not in SOURCE_OUTCOMES:
            errors.append(f"research source has invalid outcome {status!r}")
        if status != "accepted" and item.get("claims"):
            errors.append("rejected research source retained claims")
        context = item.get("identity_context", {})
        if (
            status == "accepted"
            and isinstance(context, dict)
            and context.get("source_scope") == "series"
            and item.get("claims")
        ):
            errors.append("generic series description was admitted as episode evidence")

    identity = dossier.get("canonical_identity", {})
    selected_titles = {
        str(segment.get("title", "") or "").strip().casefold()
        for segment in identity.get("segments", [])
        if isinstance(segment, dict) and segment.get("title")
    } if isinstance(identity, dict) else set()
    if selected_titles:
        for segment in dossier.get("segments", []):
            if not isinstance(segment, dict):
                errors.append("research segment is malformed")
                continue
            title = str(segment.get("title", "") or "").strip().casefold()
            if title not in selected_titles:
                errors.append(
                    f"selected-segment leakage from research segment {title or '<untitled>'}"
                )

    if errors:
        raise ResearchGroundingError(
            "Research grounding quality gate failed: " + "; ".join(errors)
        )
