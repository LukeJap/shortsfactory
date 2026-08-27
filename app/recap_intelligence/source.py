"""Local transcript, scene, and source-video evidence handling."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from .cache import source_fingerprint
from .identity import normalize_title, parse_compound_title
from .llm import JsonModel, ModelGeneration
from .models import SCHEMA_VERSION, utc_now, write_json


STOP_WORDS = {
    "about",
    "after",
    "again",
    "because",
    "before",
    "being",
    "between",
    "could",
    "from",
    "have",
    "into",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "through",
    "what",
    "when",
    "which",
    "with",
    "would",
}
TIMESTAMP_RE = re.compile(
    r"(?P<hours>\d{1,2}):(?P<minutes>\d{2}):(?P<seconds>\d{2}(?:[.,]\d{1,3})?)"
)
SCENE_PTS_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEMANTIC_PROMPT_PATH = ROOT / "prompts" / "recap_semantic_story.md"
DEFAULT_SEMANTIC_UNIT_PROMPT_PATH = ROOT / "prompts" / "recap_semantic_units.md"
SEMANTIC_PROMPT_VERSION = "recap-semantic-story-v3-fandom-priors"
SEMANTIC_PURPOSES = {
    "setup",
    "inciting_incident",
    "escalation",
    "attempt_failure",
    "emotional_turn",
    "reversal_reveal",
    "payoff_climax",
    "resolution",
    "supporting_event",
}


class SourceMismatchError(RuntimeError):
    """Raised when a transcript has no support for the researched episode."""


class StoryGroundingError(RuntimeError):
    """Raised when a story map violates selected-source grounding rules."""


class SemanticInterpretationError(RuntimeError):
    """Raised when local evidence cannot produce a valid semantic story map."""


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 4),
            "end": round(self.end, 4),
            "text": self.text,
            "words": [dict(word) for word in self.words],
        }


@dataclass(frozen=True)
class TranscriptData:
    path: str
    segments: tuple[TranscriptSegment, ...]
    full_text: str
    duration: float


def _timestamp(value: str) -> float:
    match = TIMESTAMP_RE.search(str(value))
    if not match:
        raise ValueError(f"Invalid timestamp: {value}")
    seconds = float(match.group("seconds").replace(",", "."))
    return (
        int(match.group("hours")) * 3600
        + int(match.group("minutes")) * 60
        + seconds
    )


def _segment(start: Any, end: Any, text: Any, words: Any = ()) -> TranscriptSegment | None:
    try:
        start_value = float(start)
        end_value = float(end)
    except (TypeError, ValueError):
        return None
    cleaned = " ".join(str(text or "").split()).strip()
    if end_value <= start_value or not cleaned:
        return None
    normalized_words: list[dict[str, Any]] = []
    if isinstance(words, list):
        for word in words:
            if isinstance(word, dict):
                normalized_words.append(dict(word))
    return TranscriptSegment(
        start=start_value,
        end=end_value,
        text=cleaned,
        words=tuple(normalized_words),
    )


def _json_transcript(path: Path) -> TranscriptData:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read transcript JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Transcript JSON must be an object")
    segments: list[TranscriptSegment] = []
    raw_segments = payload.get("segments", [])
    if isinstance(raw_segments, list):
        for raw in raw_segments:
            if not isinstance(raw, dict):
                continue
            item = _segment(
                raw.get("start"),
                raw.get("end"),
                raw.get("text"),
                raw.get("words", []),
            )
            if item:
                segments.append(item)
    if not segments:
        raw_words = payload.get("words", [])
        if isinstance(raw_words, list):
            for raw in raw_words:
                if not isinstance(raw, dict):
                    continue
                item = _segment(
                    raw.get("start"),
                    raw.get("end"),
                    raw.get("word") or raw.get("text"),
                )
                if item:
                    segments.append(item)
    segments.sort(key=lambda item: (item.start, item.end))
    full_text = " ".join(segment.text for segment in segments)
    if not full_text:
        full_text = str(payload.get("text", "") or "").strip()
    duration = max(
        [segment.end for segment in segments]
        + [float(payload.get("duration", 0.0) or 0.0)]
    )
    return TranscriptData(
        path=str(path.resolve()),
        segments=tuple(segments),
        full_text=full_text,
        duration=duration,
    )


def _caption_transcript(path: Path) -> TranscriptData:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n"))
    segments: list[TranscriptSegment] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timestamp_line = next(
            (line for line in lines if "-->" in line),
            "",
        )
        if not timestamp_line:
            continue
        start_text, end_text = [part.strip() for part in timestamp_line.split("-->", 1)]
        try:
            start = _timestamp(start_text)
            end = _timestamp(end_text)
        except ValueError:
            continue
        text_lines = [
            line
            for line in lines
            if "-->" not in line and not line.isdigit()
        ]
        item = _segment(start, end, " ".join(text_lines))
        if item:
            segments.append(item)
    segments.sort(key=lambda item: (item.start, item.end))
    return TranscriptData(
        path=str(path.resolve()),
        segments=tuple(segments),
        full_text=" ".join(segment.text for segment in segments),
        duration=max([segment.end for segment in segments] or [0.0]),
    )


def load_transcript(path: Path) -> TranscriptData:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")
    if path.suffix.casefold() == ".json":
        return _json_transcript(path)
    if path.suffix.casefold() in {".srt", ".vtt"}:
        return _caption_transcript(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return TranscriptData(
        path=str(path),
        segments=(),
        full_text=" ".join(text.split()),
        duration=0.0,
    )


def probe_duration(video: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return max(0.0, float(result.stdout.strip()))


def detect_scene_boundaries(
    video: Path,
    *,
    duration: float | None = None,
    threshold: float = 0.31,
    minimum_gap: float = 0.65,
) -> list[float]:
    """Run lightweight FFmpeg scene scoring and return source timestamps."""
    duration = duration if duration is not None else probe_duration(video)
    if duration <= 0:
        return []
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-an",
            "-vf",
            f"select='gt(scene,{threshold:.3f})',showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg scene detection failed:\n" + result.stderr[-2000:]
        )
    cuts: list[float] = []
    for match in SCENE_PTS_RE.finditer(result.stderr):
        value = float(match.group(1))
        if 0.10 < value < duration - 0.10:
            if not cuts or value - cuts[-1] >= minimum_gap:
                cuts.append(round(value, 3))
    return cuts


def sample_keyframes(
    video: Path,
    timestamps: Sequence[float],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Extract one frame per requested timestamp for a future vision adapter."""
    output_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps, start=1):
        path = output_dir / f"frame_{index:04d}_{timestamp:.3f}.png"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{max(0.0, timestamp):.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not path.exists():
            continue
        samples.append(
            {
                "timestamp": round(max(0.0, timestamp), 3),
                "path": str(path.resolve()),
            }
        )
    return samples


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", str(value or "").casefold())
        if len(token) > 2 and token not in STOP_WORDS
    }


def _evidence_ranges(
    query: str,
    transcript: TranscriptData,
    *,
    limit: int = 3,
    window_start: float = 0.0,
    window_end: float | None = None,
) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    scored: list[tuple[float, TranscriptSegment]] = []
    for segment in transcript.segments:
        if segment.end <= window_start:
            continue
        if window_end is not None and segment.start >= window_end:
            continue
        text_tokens = _tokens(segment.text)
        overlap = query_tokens & text_tokens
        minimum_overlap = 1 if len(query_tokens) <= 2 else 2
        query_coverage = len(overlap) / len(query_tokens)
        if len(overlap) < minimum_overlap or query_coverage < 0.4:
            continue
        score = (
            0.65 * query_coverage
            + 0.35 * len(overlap) / max(1, len(text_tokens))
        )
        if score < 0.45:
            continue
        scored.append((score, segment))
    scored.sort(key=lambda item: (-item[0], item[1].start))
    ranges: list[dict[str, Any]] = []
    for score, segment in scored[:limit]:
        ranges.append(
            {
                "start": round(segment.start, 4),
                "end": round(segment.end, 4),
                "confidence": round(min(0.99, max(0.05, score)), 4),
                "evidence_type": "transcript",
                "transcript_excerpt": segment.text,
            }
        )
    return ranges


def _selected_source_window(
    identity: dict[str, Any],
    transcript: TranscriptData,
    duration: float,
    scene_boundaries: Sequence[float] | None,
) -> dict[str, Any]:
    container_parts = parse_compound_title(
        str(identity.get("container_title", "") or "")
    )
    selected_titles = [
        str(segment.get("title", "") or "").strip()
        for segment in identity.get("segments", [])
        if isinstance(segment, dict) and segment.get("title")
    ]
    if len(container_parts) < 2 or len(selected_titles) != 1 or duration <= 0:
        return {
            "start": 0.0,
            "end": duration,
            "selected_titles": selected_titles,
            "container_parts": container_parts,
            "selection_index": 0,
            "boundary_method": "full_source",
        }

    selected_key = normalize_title(selected_titles[0])
    part_keys = [normalize_title(part) for part in container_parts]
    if selected_key not in part_keys:
        return {
            "start": 0.0,
            "end": duration,
            "selected_titles": selected_titles,
            "container_parts": container_parts,
            "selection_index": 0,
            "boundary_method": "full_source_unmatched_title",
        }
    selection_index = part_keys.index(selected_key)
    boundaries = [0.0]
    boundary_methods: list[str] = []
    cuts = sorted(
        float(value)
        for value in scene_boundaries or []
        if 0 < float(value) < duration
    )
    for split_index in range(1, len(container_parts)):
        nominal = duration * split_index / len(container_parts)
        search_radius = duration * 0.14
        gap_candidates: list[tuple[float, float]] = []
        for left, right in zip(transcript.segments, transcript.segments[1:]):
            midpoint = (left.end + right.start) / 2
            gap = right.start - left.end
            if gap > 1.5 and abs(midpoint - nominal) <= search_radius:
                gap_candidates.append((gap, midpoint))
        if gap_candidates:
            _, midpoint = max(
                gap_candidates,
                key=lambda item: (item[0], -abs(item[1] - nominal)),
            )
            nearby_cuts = [cut for cut in cuts if abs(cut - midpoint) <= 20.0]
            boundary = min(nearby_cuts, key=lambda cut: abs(cut - midpoint)) \
                if nearby_cuts else midpoint
            boundary_methods.append("transcript_gap_with_scene_cut" if nearby_cuts else "transcript_gap")
        elif cuts:
            boundary = min(cuts, key=lambda cut: abs(cut - nominal))
            boundary_methods.append("nearest_scene_cut")
        else:
            boundary = nominal
            boundary_methods.append("equal_duration")
        boundaries.append(boundary)
    boundaries.append(duration)
    return {
        "start": round(boundaries[selection_index], 4),
        "end": round(boundaries[selection_index + 1], 4),
        "selected_titles": selected_titles,
        "container_parts": container_parts,
        "selection_index": selection_index,
        "boundary_method": boundary_methods[selection_index - 1]
        if selection_index > 0
        else boundary_methods[0],
    }


def _evidence_quality(text: str) -> float:
    tokens = re.findall(r"[a-z0-9']+", str(text or "").casefold())
    if not tokens:
        return 0.0
    counts = {token: tokens.count(token) for token in set(tokens)}
    unique_ratio = len(counts) / len(tokens)
    max_repeat_ratio = max(counts.values()) / len(tokens)
    length_score = min(1.0, len(tokens) / 12.0)
    quality = (
        0.3
        + 0.35 * unique_ratio
        + 0.2 * length_score
        + 0.15 * (1.0 - max_repeat_ratio)
    )
    return round(max(0.2, min(0.95, quality)), 4)


def _compact_words(text: str, limit: int) -> str:
    words = str(text or "").split()
    if len(words) <= limit:
        return " ".join(words)
    lead = max(1, int(limit * 0.65))
    return " ".join(words[:lead] + ["..."] + words[-(limit - lead):])


def _candidate_semantic_units(
    transcript: TranscriptData,
    window: dict[str, Any],
    scene_boundaries: Sequence[float] | None,
) -> list[dict[str, Any]]:
    selected = [
        segment
        for segment in transcript.segments
        if segment.end > float(window["start"])
        and segment.start < float(window["end"])
    ]
    expanded: list[TranscriptSegment] = []
    for segment in selected:
        if segment.end - segment.start <= 32.0 or not segment.words:
            expanded.append(segment)
            continue
        chunk: list[dict[str, Any]] = []
        chunk_start = float(segment.words[0].get("start", segment.start))
        for word in segment.words:
            word_end = float(word.get("end", chunk_start))
            if chunk and word_end - chunk_start > 24.0:
                expanded_segment = _segment(
                    chunk_start,
                    float(chunk[-1].get("end", word_end)),
                    " ".join(str(item.get("word", "")) for item in chunk),
                    chunk,
                )
                if expanded_segment is not None:
                    expanded.append(expanded_segment)
                chunk = []
                chunk_start = float(word.get("start", word_end))
            chunk.append(dict(word))
        if chunk:
            expanded_segment = _segment(
                chunk_start,
                float(chunk[-1].get("end", segment.end)),
                " ".join(str(item.get("word", "")) for item in chunk),
                chunk,
            )
            if expanded_segment is not None:
                expanded.append(expanded_segment)
    selected = expanded
    if not selected:
        return []
    groups: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    cuts = sorted(
        float(value)
        for value in scene_boundaries or []
        if float(window["start"]) < float(value) < float(window["end"])
    )
    group_start = 0.0
    previous: TranscriptSegment | None = None
    for segment in selected:
        if current and previous is not None:
            gap = segment.start - previous.end
            current_duration = previous.end - group_start
            if gap >= 8.0 or (gap >= 1.5 and current_duration >= 9.0):
                groups.append(current)
                current = []
        if not current:
            group_start = segment.start
        current.append(segment)
        elapsed = segment.end - group_start
        has_scene_transition = any(
            group_start + 4.0 <= cut <= segment.end + 0.25
            for cut in cuts
        )
        if (elapsed >= 16.0 and has_scene_transition) or elapsed >= 32.0:
            groups.append(current)
            current = []
        previous = segment
    if current:
        gap_from_previous = (
            current[0].start - groups[-1][-1].end if groups else float("inf")
        )
        if (
            groups
            and current[-1].end - current[0].start < 4.0
            and gap_from_previous <= 4.0
        ):
            groups[-1].extend(current)
        else:
            groups.append(current)

    units: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        full_text = " ".join(segment.text for segment in group)
        units.append(
            {
                "unit_id": f"U{index:03d}",
                "start": round(group[0].start, 4),
                "end": round(group[-1].end, 4),
                "transcript": _compact_words(full_text, 40),
                "evidence_quality": _evidence_quality(full_text),
            }
        )
    for index, unit in enumerate(units):
        unit["context_before"] = (
            _compact_words(units[index - 1]["transcript"], 10)
            if index > 0
            else ""
        )
        unit["context_after"] = (
            _compact_words(units[index + 1]["transcript"], 10)
            if index + 1 < len(units)
            else ""
        )
    return units


def _unique_names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen and cleaned.casefold() != "unknown":
            seen.add(key)
            output.append(cleaned)
    return output


def _research_priors(
    dossier: dict[str, Any],
    selected_window: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_titles = {
        normalize_title(str(value))
        for value in selected_window.get("selected_titles", [])
        if str(value).strip()
    }
    allowed_segment_ids = {
        str(segment.get("segment_id", "") or "")
        for segment in dossier.get("segments", [])
        if isinstance(segment, dict)
        and (
            not selected_titles
            or normalize_title(str(segment.get("title", "") or "")) in selected_titles
        )
    }
    priors: list[dict[str, Any]] = []
    for point in dossier.get("ordered_plot_points", []):
        if not isinstance(point, dict):
            continue
        segment_id = str(point.get("segment_id", "") or "")
        if allowed_segment_ids and segment_id and segment_id not in allowed_segment_ids:
            continue
        summary = " ".join(str(point.get("summary", "") or "").split())
        prior_id = str(point.get("plot_id", "") or "")
        if not summary or not prior_id:
            continue
        provenance = point.get("provenance", [])
        source = next(
            (item for item in provenance if isinstance(item, dict)),
            {},
        ) if isinstance(provenance, list) else {}
        priors.append(
            {
                "prior_id": prior_id,
                "prior_type": "episode_plot",
                "order": int(point.get("order", len(priors) + 1) or len(priors) + 1),
                "event": summary,
                "story_purpose": str(point.get("story_purpose", "") or ""),
                "characters": list(point.get("characters", []) or []),
                "source_provider": str(source.get("provider", "") or ""),
                "source_url": str(source.get("url", "") or ""),
                "segment_id": segment_id,
                "timing_authority": "none",
            }
        )
    for event in dossier.get("transcript_events", []):
        if not isinstance(event, dict):
            continue
        segment_id = str(event.get("segment_id", "") or "")
        if allowed_segment_ids and segment_id and segment_id not in allowed_segment_ids:
            continue
        event_id = str(event.get("event_id", "") or "")
        text = " ".join(
            value
            for value in (
                str(event.get("speaker", "") or ""),
                str(event.get("dialogue", "") or ""),
                " ".join(str(value) for value in event.get("actions", []) or []),
            )
            if value
        )
        if not event_id or not text:
            continue
        priors.append(
            {
                "prior_id": event_id,
                "prior_type": "episode_transcript",
                "order": int(event.get("order", len(priors) + 1) or len(priors) + 1),
                "event": _compact_words(text, 48),
                "speaker": str(event.get("speaker", "") or ""),
                "source_provider": str(event.get("source_provider", "") or ""),
                "source_url": str(event.get("source_url", "") or ""),
                "segment_id": segment_id,
                "timing_authority": "none",
            }
        )
    return priors


def _fuzzy_token_score(
    left: str,
    right: str,
    ignored_tokens: set[str] | None = None,
) -> float:
    ignored = ignored_tokens or set()
    left_tokens = sorted(_tokens(left) - ignored)
    right_tokens = sorted(_tokens(right) - ignored)
    if not left_tokens or not right_tokens:
        return 0.0
    matched_left: set[int] = set()
    matched_right: set[int] = set()
    for left_index, left_token in enumerate(left_tokens):
        best_index = -1
        best_score = 0.0
        for right_index, right_token in enumerate(right_tokens):
            if right_index in matched_right:
                continue
            score = 1.0 if left_token == right_token else SequenceMatcher(
                None, left_token, right_token
            ).ratio()
            if score > best_score:
                best_index = right_index
                best_score = score
        if best_index >= 0 and best_score >= 0.72:
            matched_left.add(left_index)
            matched_right.add(best_index)
    if not matched_left:
        return 0.0
    coverage = len(matched_left) / min(len(left_tokens), 12)
    precision = len(matched_right) / min(len(right_tokens), 12)
    sequence = SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
    return round(min(1.0, 0.55 * coverage + 0.3 * precision + 0.15 * sequence), 4)


def _align_research_priors(
    priors: list[dict[str, Any]],
    units: list[dict[str, Any]],
    scene_boundaries: Sequence[float] | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    if not priors or not units:
        return [], {}
    type_counts: dict[str, int] = {}
    token_frequency: dict[str, int] = {}
    for prior in priors:
        prior_type = str(prior["prior_type"])
        type_counts[prior_type] = max(type_counts.get(prior_type, 0), int(prior["order"]))
        for token in _tokens(str(prior["event"])):
            token_frequency[token] = token_frequency.get(token, 0) + 1
    common_tokens = {
        token
        for token, count in token_frequency.items()
        if len(priors) >= 12 and count >= max(6, int(len(priors) * 0.2))
    }
    cuts = sorted(float(value) for value in scene_boundaries or [])
    alignments: list[dict[str, Any]] = []
    by_unit: dict[str, list[dict[str, Any]]] = {}
    for prior in priors:
        expected_position = (
            (int(prior["order"]) - 1) / max(1, type_counts[str(prior["prior_type"])] - 1)
        )
        scored: list[tuple[float, float, dict[str, Any]]] = []
        for unit_index, unit in enumerate(units):
            lexical = _fuzzy_token_score(
                str(prior["event"]),
                str(unit["transcript"]),
                common_tokens,
            )
            if lexical < 0.2:
                continue
            unit_position = unit_index / max(1, len(units) - 1)
            position_support = max(0.0, 1.0 - abs(unit_position - expected_position) * 2.0)
            confidence = min(0.98, 0.9 * lexical + 0.1 * position_support)
            if confidence >= 0.25:
                scored.append((confidence, lexical, unit))
        scored.sort(key=lambda item: (-item[0], item[2]["start"]))
        candidates: list[dict[str, Any]] = []
        for confidence, lexical, unit in scored[:2]:
            nearby_cuts = [
                round(cut, 4)
                for cut in cuts
                if float(unit["start"]) - 2.0 <= cut <= float(unit["end"]) + 2.0
            ]
            candidate = {
                "unit_id": unit["unit_id"],
                "start": unit["start"],
                "end": unit["end"],
                "confidence": round(confidence, 4),
                "lexical_confidence": round(lexical, 4),
                "scene_support": nearby_cuts,
                "evidence_type": "candidate_local_alignment",
            }
            candidates.append(candidate)
            by_unit.setdefault(str(unit["unit_id"]), []).append(
                {
                    "prior_id": prior["prior_id"],
                    "prior_type": prior["prior_type"],
                    "event": prior["event"],
                    "speaker": prior.get("speaker", ""),
                    "story_purpose": prior.get("story_purpose", ""),
                    "alignment_confidence": round(confidence, 4),
                    "timing_authority": "none",
                }
            )
        alignment = dict(prior)
        alignment["candidate_local_ranges"] = candidates
        alignment["alignment_status"] = "candidate" if candidates else "unaligned"
        alignments.append(alignment)
    for unit_id, values in by_unit.items():
        values.sort(key=lambda item: -float(item["alignment_confidence"]))
        by_unit[unit_id] = values[:4]
    return alignments, by_unit


class SemanticStoryInterpreter:
    """Convert local evidence units into validated semantic story beats."""

    def __init__(
        self,
        model: JsonModel,
        *,
        prompt_path: Path = DEFAULT_SEMANTIC_PROMPT_PATH,
        unit_prompt_path: Path = DEFAULT_SEMANTIC_UNIT_PROMPT_PATH,
        max_repair_attempts: int = 2,
        unit_batch_size: int = 0,
    ):
        self.model = model
        self.prompt_path = prompt_path
        self.unit_prompt_path = unit_prompt_path
        self.max_repair_attempts = max(0, max_repair_attempts)
        if not unit_batch_size:
            try:
                unit_batch_size = int(
                    os.getenv("RECAP_SEMANTIC_UNIT_BATCH_SIZE", "8")
                )
            except (TypeError, ValueError):
                unit_batch_size = 8
        self.unit_batch_size = max(1, unit_batch_size)
        self.debug_dir: Path | None = None
        self.last_diagnostics: dict[str, Any] = {}

    def set_debug_dir(self, path: Path) -> None:
        self.debug_dir = path

    def cache_identity(self) -> tuple[str, str]:
        prompt = self.unit_prompt_path.read_bytes() + self.prompt_path.read_bytes()
        digest = hashlib.sha256(prompt).hexdigest()[:16]
        model_name = str(
            getattr(self.model, "model", "") or type(self.model).__name__
        )
        return f"{SEMANTIC_PROMPT_VERSION}:{digest}", model_name

    def _prompt(
        self,
        interpreted_units: list[dict[str, Any]],
        identity: dict[str, Any],
        research_hints: Sequence[Any],
    ) -> str:
        identity_context = {
            "series_title": identity.get("series_title"),
            "container_title": identity.get("container_title"),
            "selected_segments": [
                segment.get("title")
                for segment in identity.get("segments", [])
                if isinstance(segment, dict)
            ],
        }
        return (
            self.prompt_path.read_text(encoding="utf-8").strip()
            + "\n\nCONFIRMED IDENTITY:\n"
            + json.dumps(identity_context, indent=2, ensure_ascii=False)
            + "\n\nOPTIONAL IDENTITY-VALIDATED RESEARCH HINTS:\n"
            + json.dumps(list(research_hints), indent=2, ensure_ascii=False)
            + "\n\nSEMANTICALLY INTERPRETED TIMED UNITS:\n"
            + json.dumps(interpreted_units, indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    def _unit_prompt(
        self,
        units: list[dict[str, Any]],
        identity: dict[str, Any],
        prior_interpretations: Sequence[dict[str, Any]] = (),
    ) -> str:
        identity_context = {
            "series_title": identity.get("series_title"),
            "container_title": identity.get("container_title"),
            "selected_segments": [
                segment.get("title")
                for segment in identity.get("segments", [])
                if isinstance(segment, dict)
            ],
        }
        return (
            self.unit_prompt_path.read_text(encoding="utf-8").strip()
            + "\n\nCONFIRMED IDENTITY:\n"
            + json.dumps(identity_context, indent=2, ensure_ascii=False)
            + "\n\nPRIOR ACCEPTED LOCAL INTERPRETATIONS FOR CONTINUITY:\n"
            + json.dumps(list(prior_interpretations), indent=2, ensure_ascii=False)
            + "\n\nLOCAL UNIT INTERPRETATION TASK:\n"
            + json.dumps(units, indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    @staticmethod
    def _normalize_unit_interpretations(
        raw: dict[str, Any],
        units: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw_units = raw.get("units", [])
        if not isinstance(raw_units, list):
            raise SemanticInterpretationError(
                "Local semantic response must contain a units list"
            )
        expected = {unit["unit_id"]: unit for unit in units}
        seen: set[str] = set()
        output: list[dict[str, Any]] = []
        errors: list[str] = []
        for raw_unit in raw_units:
            if not isinstance(raw_unit, dict):
                errors.append("local unit interpretation is not an object")
                continue
            unit_id = str(raw_unit.get("unit_id", "") or "").strip()
            if unit_id not in expected or unit_id in seen:
                errors.append(f"unexpected or duplicate local unit {unit_id!r}")
                continue
            seen.add(unit_id)
            event = " ".join(str(raw_unit.get("event", "") or "").split())
            if len(event.split()) < 4:
                errors.append(f"{unit_id} needs a semantic event")
            transcript = str(expected[unit_id].get("transcript", "") or "")
            similarity = SequenceMatcher(
                None,
                event.casefold(),
                transcript.casefold(),
            ).ratio()
            if len(event.split()) >= 8 and (
                event.casefold() in transcript.casefold() or similarity > 0.86
            ):
                errors.append(f"{unit_id} copies transcript instead of interpreting it")
            try:
                confidence = float(raw_unit.get("semantic_confidence"))
            except (TypeError, ValueError):
                errors.append(f"{unit_id} needs numeric semantic_confidence")
                confidence = 0.0
            if not 0.0 <= confidence <= 1.0:
                errors.append(f"{unit_id} confidence must be between 0 and 1")
            evidence_quality = float(expected[unit_id]["evidence_quality"])
            output.append(
                {
                    "unit_id": unit_id,
                    "start": expected[unit_id]["start"],
                    "end": expected[unit_id]["end"],
                    "event": event,
                    "characters": _unique_names(raw_unit.get("characters", [])),
                    "locations": _unique_names(raw_unit.get("locations", [])),
                    "motivation": str(raw_unit.get("motivation", "") or "").strip(),
                    "change": str(raw_unit.get("change", "") or "").strip(),
                    "emotional_conflict": str(
                        raw_unit.get("emotional_conflict", "") or ""
                    ).strip(),
                    "narrative_signal": str(
                        raw_unit.get("narrative_signal", "unknown") or "unknown"
                    ).strip(),
                    "semantic_confidence": round(
                        min(confidence, 0.15 + 0.8 * evidence_quality), 4
                    ),
                    "evidence_quality": evidence_quality,
                }
            )
        missing = set(expected) - seen
        if missing:
            errors.append(f"local response omitted units {sorted(missing)}")
        if errors:
            raise SemanticInterpretationError("; ".join(errors))
        return sorted(output, key=lambda item: item["start"])

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
            raise SemanticInterpretationError(
                "Semantic model returned an unsupported response"
            )
        result = self.model.generate_json(prompt)
        if not isinstance(result, dict):
            raise SemanticInterpretationError(
                "Semantic model response must be a JSON object"
            )
        return ModelGeneration(
            raw_text=json.dumps(result, ensure_ascii=False),
            parsed=result,
        )

    @staticmethod
    def _normalize(
        raw: dict[str, Any],
        units: list[dict[str, Any]],
        segment_id: str,
        visual_evidence: Sequence[dict[str, Any]] | None,
        research_hints: Sequence[Any] = (),
    ) -> dict[str, Any]:
        raw_beats = raw.get("beats", [])
        if not isinstance(raw_beats, list) or not raw_beats:
            raise SemanticInterpretationError("Semantic response returned no beats")
        unit_map = {unit["unit_id"]: unit for unit in units}
        unit_order = {unit["unit_id"]: index for index, unit in enumerate(units)}
        errors: list[str] = []
        specs: list[dict[str, Any]] = []
        seen_semantic_ids: set[str] = set()
        used_units: set[str] = set()
        for index, raw_beat in enumerate(raw_beats, start=1):
            if not isinstance(raw_beat, dict):
                errors.append(f"beat {index} is not an object")
                continue
            semantic_id = str(
                raw_beat.get("semantic_id") or f"S{index:03d}"
            ).strip()
            if semantic_id in seen_semantic_ids:
                errors.append(f"duplicate semantic_id {semantic_id}")
            seen_semantic_ids.add(semantic_id)
            unit_ids = [
                str(value)
                for value in raw_beat.get("unit_ids", [])
                if str(value) in unit_map
            ] if isinstance(raw_beat.get("unit_ids"), list) else []
            if not unit_ids:
                errors.append(f"{semantic_id} has no valid evidence units")
                continue
            duplicate_units = used_units & set(unit_ids)
            if duplicate_units:
                errors.append(
                    f"{semantic_id} reuses evidence units {sorted(duplicate_units)}"
                )
            used_units.update(unit_ids)
            summary = " ".join(
                str(raw_beat.get("summary", "") or "").split()
            ).strip()
            if len(summary.split()) < 5:
                errors.append(f"{semantic_id} needs a semantic event summary")
            evidence_text = " ".join(unit_map[value]["transcript"] for value in unit_ids)
            similarity = SequenceMatcher(
                None,
                summary.casefold(),
                evidence_text.casefold(),
            ).ratio()
            if len(summary.split()) >= 8 and (
                summary.casefold() in evidence_text.casefold() or similarity > 0.86
            ):
                errors.append(
                    f"{semantic_id} copies transcript instead of summarizing the event"
                )
            purpose = str(raw_beat.get("story_purpose", "")).strip().casefold()
            purpose = re.sub(r"[^a-z]+", "_", purpose).strip("_")
            purpose = {
                "attempt": "attempt_failure",
                "failure": "attempt_failure",
                "turn": "emotional_turn",
                "reveal": "reversal_reveal",
                "reversal": "reversal_reveal",
                "payoff": "payoff_climax",
                "climax": "payoff_climax",
            }.get(purpose, purpose)
            if purpose not in SEMANTIC_PURPOSES:
                errors.append(f"{semantic_id} has invalid story_purpose {purpose!r}")
            try:
                importance = float(raw_beat.get("importance"))
                semantic_confidence = float(raw_beat.get("semantic_confidence"))
            except (TypeError, ValueError):
                errors.append(f"{semantic_id} needs numeric importance and confidence")
                importance = 0.0
                semantic_confidence = 0.0
            if not 0.0 <= importance <= 1.0:
                errors.append(f"{semantic_id} importance must be between 0 and 1")
            if not 0.0 <= semantic_confidence <= 1.0:
                errors.append(f"{semantic_id} confidence must be between 0 and 1")
            selected_units = [unit_map[value] for value in unit_ids]
            evidence_quality = sum(
                float(unit["evidence_quality"]) for unit in selected_units
            ) / len(selected_units)
            confidence_cap = 0.15 + 0.8 * evidence_quality
            specs.append(
                {
                    "semantic_id": semantic_id,
                    "unit_ids": unit_ids,
                    "unit_order": min(unit_order[value] for value in unit_ids),
                    "source_start": min(float(unit["start"]) for unit in selected_units),
                    "source_end": max(float(unit["end"]) for unit in selected_units),
                    "summary": summary,
                    "characters": _unique_names(raw_beat.get("characters", [])),
                    "locations": _unique_names(raw_beat.get("locations", [])),
                    "motivation": str(raw_beat.get("motivation", "") or "").strip(),
                    "change": str(raw_beat.get("change", "") or "").strip(),
                    "emotional_conflict": str(
                        raw_beat.get("emotional_conflict", "") or ""
                    ).strip(),
                    "story_purpose": purpose,
                    "importance": round(importance, 4),
                    "semantic_confidence": round(
                        min(semantic_confidence, confidence_cap), 4
                    ),
                    "evidence_quality": round(evidence_quality, 4),
                    "payoff_significance": str(
                        raw_beat.get("payoff_significance", "") or ""
                    ).strip(),
                }
            )

        if len(specs) >= 4:
            importances = [spec["importance"] for spec in specs]
            if max(importances) - min(importances) < 0.08:
                errors.append("importance scores are uniformly hard-coded")
            purposes = {spec["story_purpose"] for spec in specs}
            if len(purposes) < 3:
                errors.append("story purposes do not distinguish narrative roles")
        if len(specs) > 14:
            errors.append(
                f"story synthesis returned {len(specs)} beats; merge coherent events to at most 14"
            )
        specs.sort(key=lambda spec: spec["unit_order"])

        links = raw.get("causal_links", [])
        if not isinstance(links, list):
            errors.append("causal_links must be a list")
            links = []
        link_specs: list[dict[str, str]] = []
        valid_ids = {spec["semantic_id"] for spec in specs}
        seen_links: set[tuple[str, str]] = set()
        for raw_link in links:
            if not isinstance(raw_link, dict):
                errors.append("causal link is not an object")
                continue
            parent = str(
                raw_link.get("parent_id") or raw_link.get("source") or ""
            ).strip()
            child = str(
                raw_link.get("child_id") or raw_link.get("target") or ""
            ).strip()
            reason = " ".join(str(raw_link.get("reason", "") or "").split())
            if parent not in valid_ids or child not in valid_ids or parent == child:
                errors.append(f"invalid causal link {parent!r} -> {child!r}")
                continue
            if len(reason.split()) < 4:
                errors.append(f"causal link {parent} -> {child} needs a reason")
            if (parent, child) not in seen_links:
                seen_links.add((parent, child))
                link_specs.append(
                    {"parent_id": parent, "child_id": child, "reason": reason}
                )
        if len(specs) >= 4 and not link_specs:
            errors.append("story synthesis needs selective reasoned causal links")
        hint_purposes = {
            str(item.get("story_purpose", "") or "").casefold()
            for item in research_hints
            if isinstance(item, dict)
        }
        spec_purposes = {spec["story_purpose"] for spec in specs}
        if "reversal" in hint_purposes and "reversal_reveal" not in spec_purposes:
            errors.append("story synthesis omits the locally supported reversal")
        if (
            hint_purposes & {"payoff", "payoff_climax", "climax"}
            and "payoff_climax" not in spec_purposes
        ):
            errors.append("story synthesis omits the locally supported payoff")
        if errors:
            raise SemanticInterpretationError("; ".join(errors))

        semantic_to_beat = {
            spec["semantic_id"]: f"B{index:03d}"
            for index, spec in enumerate(specs, start=1)
        }
        beats: list[dict[str, Any]] = []
        for index, spec in enumerate(specs, start=1):
            semantic_id = spec["semantic_id"]
            parent_links = [
                link for link in link_specs if link["child_id"] == semantic_id
            ]
            child_links = [
                link for link in link_specs if link["parent_id"] == semantic_id
            ]
            selected_units = [unit_map[value] for value in spec["unit_ids"]]
            evidence_units = (
                selected_units
                if len(selected_units) <= 2
                else [selected_units[0], selected_units[-1]]
            )
            evidence = [
                {
                    "start": unit["start"],
                    "end": unit["end"],
                    "confidence": 0.98,
                    "evidence_type": "transcript_unit",
                    "transcript_excerpt": _compact_words(unit["transcript"], 24),
                    "timestamp_confidence": 0.98,
                }
                for unit in evidence_units
            ]
            evidence.extend(
                _visual_ranges(
                    visual_evidence,
                    float(spec["source_start"]),
                    float(spec["source_end"]),
                )
            )
            aligned_plot_ids = sorted(
                {
                    str(prior.get("prior_id", ""))
                    for unit in selected_units
                    for prior in unit.get("candidate_priors", [])
                    if prior.get("prior_type") == "episode_plot"
                    and float(prior.get("alignment_confidence", 0.0) or 0.0) >= 0.25
                    and str(prior.get("prior_id", "")).strip()
                }
            )
            beats.append(
                {
                    "beat_id": f"B{index:03d}",
                    "chronological_order": index,
                    "segment_id": segment_id,
                    "source_start": round(float(spec["source_start"]), 4),
                    "source_end": round(float(spec["source_end"]), 4),
                    "summary": spec["summary"],
                    "story_purpose": spec["story_purpose"],
                    "characters": spec["characters"],
                    "location": spec["locations"],
                    "importance": spec["importance"],
                    "motivation": spec["motivation"],
                    "change": spec["change"],
                    "emotional_conflict": spec["emotional_conflict"],
                    "payoff_significance": spec["payoff_significance"],
                    "causal_parents": [
                        semantic_to_beat[link["parent_id"]]
                        for link in parent_links
                    ],
                    "causal_children": [
                        semantic_to_beat[link["child_id"]]
                        for link in child_links
                    ],
                    "causal_reasoning": [
                        {
                            "parent": semantic_to_beat[link["parent_id"]],
                            "reason": link["reason"],
                        }
                        for link in parent_links
                    ],
                    "research_plot_ids": aligned_plot_ids,
                    "semantic_unit_ids": spec["unit_ids"],
                    "actual_video_evidence_ranges": evidence,
                    "verification_status": "verified",
                    "evidence_confidence": spec["evidence_quality"],
                    "semantic_confidence": spec["semantic_confidence"],
                    "confidence": spec["semantic_confidence"],
                }
            )
        return {
            "beats": beats,
            "warnings": [
                str(value)
                for value in raw.get("warnings", [])
                if str(value).strip()
            ] if isinstance(raw.get("warnings", []), list) else [],
        }

    def interpret(
        self,
        *,
        units: list[dict[str, Any]],
        identity: dict[str, Any],
        segment_id: str,
        research_hints: Sequence[Any] = (),
        visual_evidence: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.last_diagnostics = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "prompt_version": self.cache_identity()[0],
            "model_version": self.cache_identity()[1],
            "status": "running",
            "unit_count": len(units),
            "repair_attempt_count": 0,
            "attempts": [],
        }
        try:
            interpreted_units: list[dict[str, Any]] = []
            for batch_index, start in enumerate(
                range(0, len(units), self.unit_batch_size),
                start=1,
            ):
                batch = units[start : start + self.unit_batch_size]
                interpreted_units.extend(
                    self._run_stage(
                        f"local_units_{batch_index:02d}",
                        self._unit_prompt(
                            batch,
                            identity,
                            interpreted_units[-3:],
                        ),
                        lambda raw, batch=batch: self._normalize_unit_interpretations(
                            raw,
                            batch,
                        ),
                    )
                )
            prompt = self._prompt(
                interpreted_units,
                identity,
                research_hints,
            )
            result = self._run_stage(
                "story_synthesis",
                prompt,
                lambda raw: self._normalize(
                    raw,
                    units,
                    segment_id,
                    visual_evidence,
                    research_hints,
                ),
            )
            self.last_diagnostics.update(
                {
                    "status": "success",
                    "interpreted_unit_count": len(interpreted_units),
                    "beat_count": len(result["beats"]),
                }
            )
            self._persist_diagnostics()
            return result
        except Exception as exc:
            error = exc if isinstance(exc, SemanticInterpretationError) else SemanticInterpretationError(str(exc))
            self.last_diagnostics.update({"status": "failed", "error": str(error)})
            self._persist_diagnostics()
            raise error

    def _run_stage(
        self,
        stage: str,
        prompt: str,
        validator: Callable[[dict[str, Any]], Any],
    ) -> Any:
        current_prompt = prompt
        attempts = 1 + self.max_repair_attempts
        last_error = ""
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
                raise SemanticInterpretationError(
                    f"{stage} model call failed: {exc}"
                ) from exc
            error = ""
            normalized: Any = None
            if generation.parsed is None:
                error = generation.parse_error or "response was not JSON"
            else:
                try:
                    normalized = validator(generation.parsed)
                except (SemanticInterpretationError, ValueError) as exc:
                    error = str(exc)
            self.last_diagnostics["attempts"].append(
                {
                    "stage": stage,
                    "attempt": attempt,
                    "kind": "initial" if attempt == 1 else "repair",
                    "raw_response": generation.raw_text,
                    "parse_error": generation.parse_error,
                    "validation_errors": [error] if error else [],
                }
            )
            if normalized is not None and not error:
                return normalized
            last_error = error
            if attempt >= attempts:
                break
            self.last_diagnostics["repair_attempt_count"] += 1
            invalid_excerpt = generation.raw_text
            repair_limit = 14000 if stage == "story_synthesis" else 4000
            if len(invalid_excerpt) > repair_limit:
                invalid_excerpt = (
                    invalid_excerpt[:repair_limit] + "\n...[truncated for repair]"
                )
            if stage == "story_synthesis":
                current_prompt = (
                    "Repair the complete story-synthesis JSON below. Return one object with a beats list, a causal_links list, and warnings. Do not return local unit objects or keyed units. Preserve the response's grounded facts and unit_ids; change only what the validation errors require. Every causal link needs parent_id, child_id, and an evidence-based reason.\n\n"
                    "VALIDATION ERRORS:\n"
                    + json.dumps([error], indent=2)
                    + "\n\nINVALID STORY RESPONSE:\n"
                    + invalid_excerpt
                    + "\n\nReturn the complete repaired story JSON only."
                )
            else:
                current_prompt = (
                    "Repair the JSON response using the validation errors. Preserve only facts grounded in the original evidence.\n\n"
                    "VALIDATION ERRORS:\n"
                    + json.dumps([error], indent=2)
                    + "\n\nINVALID RESPONSE:\n"
                    + invalid_excerpt
                    + "\n\nORIGINAL TASK:\n"
                    + prompt
                    + "\n\nReturn repaired JSON only."
                )
        raise SemanticInterpretationError(
            f"{stage} remained invalid after {attempts} attempts: {last_error}"
        )

    def _persist_diagnostics(self) -> None:
        if self.debug_dir is not None:
            write_json(
                self.debug_dir / "semantic_story_diagnostics.json",
                self.last_diagnostics,
            )


def _visual_ranges(
    visual_evidence: Sequence[dict[str, Any]] | None,
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in visual_evidence or []:
        if not isinstance(item, dict):
            continue
        try:
            item_start = float(item.get("start"))
            item_end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if item_end <= start or item_start >= end or item_end <= item_start:
            continue
        output.append(
            {
                "start": max(start, round(item_start, 4)),
                "end": min(end, round(item_end, 4)),
                "confidence": float(item.get("confidence", 0.5) or 0.5),
                "evidence_type": "visual_keyframe",
                "keyframe_path": str(item.get("keyframe_path", "") or ""),
                "description": str(item.get("description", "") or ""),
            }
        )
    return output


def align_story_map(
    *,
    identity: dict[str, Any],
    dossier: dict[str, Any],
    source_video: Path,
    transcript_path: Path | None = None,
    visual_evidence: Sequence[dict[str, Any]] | None = None,
    scene_boundaries: Sequence[float] | None = None,
    semantic_interpreter: SemanticStoryInterpreter | None = None,
) -> dict[str, Any]:
    """Align researched plot points to evidence in the local source."""
    if "source_evaluations" in dossier:
        from .research import validate_research_grounding

        validate_research_grounding(dossier)
    transcript = (
        load_transcript(transcript_path)
        if transcript_path is not None and transcript_path.exists()
        else TranscriptData(
            path="",
            segments=(),
            full_text="",
            duration=0.0,
        )
    )
    try:
        duration = probe_duration(source_video)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        duration = transcript.duration
    duration = max(duration, transcript.duration)
    selected_window = _selected_source_window(
        identity,
        transcript,
        duration,
        scene_boundaries,
    )
    plot_points = dossier.get("ordered_plot_points", [])
    if not isinstance(plot_points, list):
        plot_points = []

    beats: list[dict[str, Any]] = []
    semantic_units: list[dict[str, Any]] = []
    research_prior_alignments: list[dict[str, Any]] = []
    semantic_warnings: list[str] = []
    used_semantic_interpreter = False
    unmatched = 0
    for point in plot_points:
        if not isinstance(point, dict):
            continue
        summary = str(point.get("summary", "") or "").strip()
        query = " ".join(
            [
                summary,
                " ".join(str(item) for item in point.get("characters", []) or []),
                " ".join(str(item) for item in point.get("locations", []) or []),
            ]
        )
        evidence = _evidence_ranges(
            query,
            transcript,
            window_start=float(selected_window["start"]),
            window_end=float(selected_window["end"]),
        )
        if evidence:
            source_start = evidence[0]["start"]
            source_end = evidence[0]["end"]
            verification = "verified"
            confidence = max(
                item["confidence"]
                for item in evidence
            )
        else:
            source_start = None
            source_end = None
            verification = "unverified"
            confidence = 0.0
            unmatched += 1
            if transcript.segments:
                continue
        beat_id = f"B{len(beats) + 1:03d}"
        if source_start is not None and source_end is not None:
            evidence = evidence + _visual_ranges(
                visual_evidence,
                source_start,
                source_end,
            )
        beats.append(
            {
                "beat_id": beat_id,
                "chronological_order": len(beats) + 1,
                "segment_id": str(point.get("segment_id", "") or ""),
                "source_start": source_start,
                "source_end": source_end,
                "summary": summary,
                "story_purpose": str(point.get("story_purpose", "") or ""),
                "characters": list(point.get("characters", []) or []),
                "location": (
                    list(point.get("locations", []) or [])
                    if isinstance(point.get("locations", []), list)
                    else []
                ),
                "importance": float(point.get("importance", 0.5) or 0.5),
                "causal_parents": [],
                "causal_children": [],
                "research_plot_ids": [str(point.get("plot_id", ""))],
                "actual_video_evidence_ranges": evidence,
                "verification_status": verification,
                "confidence": round(confidence, 4),
                "_research_parent_ids": list(
                    point.get("causal_parents", []) or []
                ),
            }
        )

    if transcript.segments and semantic_interpreter is not None:
        segment_id = next(
            (
                str(segment.get("segment_id", "") or "")
                for segment in dossier.get("segments", [])
                if isinstance(segment, dict) and segment.get("segment_id")
            ),
            "SEG_01" if selected_window.get("selected_titles") else "",
        )
        semantic_units = _candidate_semantic_units(
            transcript,
            selected_window,
            scene_boundaries,
        )
        if not semantic_units:
            raise SourceMismatchError(
                "The selected source segment contains no semantic evidence units."
            )
        priors = _research_priors(dossier, selected_window)
        research_prior_alignments, unit_prior_map = _align_research_priors(
            priors,
            semantic_units,
            scene_boundaries,
        )
        for unit in semantic_units:
            unit["candidate_priors"] = unit_prior_map.get(unit["unit_id"], [])
        aligned_hints = [
            {
                "prior_id": alignment["prior_id"],
                "prior_type": alignment["prior_type"],
                "event": alignment["event"],
                "story_purpose": alignment.get("story_purpose", ""),
                "candidate_unit_ids": [
                    candidate["unit_id"]
                    for candidate in alignment["candidate_local_ranges"]
                ],
                "alignment_confidence": max(
                    (
                        float(candidate["confidence"])
                        for candidate in alignment["candidate_local_ranges"]
                    ),
                    default=0.0,
                ),
                "timing_authority": "none",
            }
            for alignment in research_prior_alignments
            if alignment["candidate_local_ranges"]
        ]
        aligned_hints.sort(
            key=lambda item: (
                0 if item["prior_type"] == "episode_plot" else 1,
                -float(item["alignment_confidence"]),
            )
        )
        plot_hints = [
            item for item in aligned_hints
            if item["prior_type"] == "episode_plot"
        ]
        global_hints = plot_hints[:24] or [
            item for item in aligned_hints
            if item["prior_type"] == "episode_transcript"
        ][:12]
        result = semantic_interpreter.interpret(
            units=semantic_units,
            identity=identity,
            segment_id=segment_id,
            research_hints=(
                global_hints
                or [
                    str(beat.get("summary", ""))
                    for beat in beats
                    if beat.get("verification_status") == "verified"
                ]
            ),
            visual_evidence=visual_evidence,
        )
        beats = result["beats"]
        semantic_warnings = result["warnings"]
        used_semantic_interpreter = True

    if not used_semantic_interpreter:
        plot_to_beat = {
            plot_id: beat["beat_id"]
            for beat in beats
            for plot_id in beat.get("research_plot_ids", [])
            if plot_id
        }
        by_id = {beat["beat_id"]: beat for beat in beats}
        for beat in beats:
            beat["causal_parents"] = [
                plot_to_beat[parent]
                for parent in beat.pop("_research_parent_ids", [])
                if parent in plot_to_beat
            ] or beat["causal_parents"]
            for parent in beat["causal_parents"]:
                if parent in by_id:
                    by_id[parent]["causal_children"].append(beat["beat_id"])

    if transcript.segments and not beats:
        raise SourceMismatchError(
            "The selected source segment has no verified story beats; configure semantic interpretation when research is sparse."
        )

    confidences = [beat["confidence"] for beat in beats]
    warnings: list[str] = []
    if not transcript.segments:
        warnings.append(
            "No timed transcript was supplied; story beats remain unverified."
        )
    if not visual_evidence:
        warnings.append(
            "No visual observations supplied; transcript evidence is not visual confirmation."
        )
    if unmatched:
        warnings.append(
            f"Excluded {unmatched} research plot point(s) without meaningful local support."
        )
    if transcript.segments and plot_points and not any(
        beat.get("research_plot_ids") for beat in beats
    ) and not used_semantic_interpreter:
        warnings.append(
            "External plot claims did not pass local verification; story beats were derived from the selected transcript window."
        )
    if used_semantic_interpreter:
        warnings.extend(semantic_warnings)
        warnings.append(
            "Story semantics were inferred from transcript evidence; confidence reflects interpretation quality, not visual confirmation."
        )
        if research_prior_alignments:
            aligned_count = sum(
                bool(item.get("candidate_local_ranges"))
                for item in research_prior_alignments
            )
            warnings.append(
                f"Research priors supplied {aligned_count} candidate local alignment(s); local source evidence remained authoritative."
            )
    segment_records: list[dict[str, Any]] = []
    grouped_beats: dict[str, list[dict[str, Any]]] = {}
    for beat in beats:
        segment_id = str(beat.get("segment_id", "") or "")
        if segment_id:
            grouped_beats.setdefault(segment_id, []).append(beat)
    for segment_id, segment_beats in grouped_beats.items():
        segment_records.append(
            {
                "segment_id": segment_id,
                "title": next(
                    (
                        str(segment.get("title", ""))
                        for segment in dossier.get("segments", [])
                        if isinstance(segment, dict)
                        and segment.get("segment_id") == segment_id
                    ),
                    "",
                ),
                "beats": segment_beats,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "canonical_identity": identity,
        "source_video": str(source_video.expanduser().resolve()),
        "source_identity": source_fingerprint(source_video),
        "transcript": {
            "path": transcript.path,
            "segment_count": len(transcript.segments),
            "duration": round(transcript.duration, 4),
        },
        "duration_seconds": round(duration, 4),
        "selected_source_window": selected_window,
        "scene_boundaries": [round(float(value), 4) for value in scene_boundaries or []],
        "semantic_units": [
            {
                "unit_id": unit["unit_id"],
                "start": unit["start"],
                "end": unit["end"],
                "evidence_quality": unit["evidence_quality"],
            }
            for unit in semantic_units
        ],
        "research_prior_alignments": research_prior_alignments,
        "keyframe_samples": [
            {
                "timestamp": round((beat["source_start"] + beat["source_end"]) / 2, 4),
                "beat_id": beat["beat_id"],
            }
            for beat in beats
            if beat["source_start"] is not None and beat["source_end"] is not None
        ],
        "beats": beats,
        "segments": segment_records,
        "causal_graph": [
            {
                "parent": parent,
                "child": beat["beat_id"],
                "reason": next(
                    (
                        item.get("reason", "")
                        for item in beat.get("causal_reasoning", [])
                        if item.get("parent") == parent
                    ),
                    "",
                ),
            }
            for beat in beats
            for parent in beat["causal_parents"]
        ],
        "confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "warnings": warnings,
    }


def validate_story_grounding(
    story_map: dict[str, Any],
    dossier: dict[str, Any],
) -> None:
    """Enforce selected-window and local-evidence guarantees before A5."""
    errors: list[str] = []
    beats = story_map.get("beats", [])
    if not isinstance(beats, list) or not beats:
        errors.append("story map has no locally grounded beats")
        beats = []
    window = story_map.get("selected_source_window", {})
    try:
        window_start = float(window.get("start", 0.0))
        window_end = float(window.get("end", story_map.get("duration_seconds", 0.0)))
    except (AttributeError, TypeError, ValueError):
        errors.append("selected source window is malformed")
        window_start = 0.0
        window_end = 0.0

    transcript = story_map.get("transcript", {})
    has_transcript = isinstance(transcript, dict) and int(
        transcript.get("segment_count", 0) or 0
    ) > 0
    semantic_units = story_map.get("semantic_units", [])
    has_semantic_interpretation = isinstance(semantic_units, list) and bool(
        semantic_units
    )
    for beat in beats:
        if not isinstance(beat, dict):
            errors.append("story beat is malformed")
            continue
        start = beat.get("source_start")
        end = beat.get("source_end")
        if has_transcript and beat.get("verification_status") != "verified":
            errors.append(f"{beat.get('beat_id')} is not locally verified")
        if has_transcript and not beat.get("actual_video_evidence_ranges"):
            errors.append(f"{beat.get('beat_id')} has no local evidence")
        if has_semantic_interpretation:
            try:
                semantic_confidence = float(beat.get("semantic_confidence"))
            except (TypeError, ValueError):
                errors.append(
                    f"{beat.get('beat_id')} lacks semantic confidence"
                )
            else:
                if not 0.0 <= semantic_confidence <= 1.0:
                    errors.append(
                        f"{beat.get('beat_id')} has invalid semantic confidence"
                    )
        if start is not None and end is not None:
            if float(start) < window_start - 0.01 or float(end) > window_end + 0.01:
                errors.append(
                    f"{beat.get('beat_id')} leaks outside the selected source window"
                )

    selected_titles = {
        str(value).casefold()
        for value in window.get("selected_titles", [])
        if str(value).strip()
    } if isinstance(window, dict) else set()
    if selected_titles:
        allowed_segment_ids = {
            str(segment.get("segment_id", "") or "")
            for segment in dossier.get("segments", [])
            if isinstance(segment, dict)
            and str(segment.get("title", "") or "").casefold() in selected_titles
        }
        for beat in beats:
            segment_id = str(beat.get("segment_id", "") or "")
            if allowed_segment_ids and segment_id not in allowed_segment_ids:
                errors.append(
                    f"{beat.get('beat_id')} belongs to an unselected research segment"
                )

    if has_semantic_interpretation and len(beats) >= 4:
        importances = [float(beat.get("importance", 0.0) or 0.0) for beat in beats]
        if max(importances) - min(importances) < 0.08:
            errors.append("semantic story importance is uniformly hard-coded")
        purposes = {
            str(beat.get("story_purpose", "") or "") for beat in beats
        }
        if len(purposes) < 3:
            errors.append("semantic story does not distinguish narrative roles")
        research_purposes = {
            str(item.get("story_purpose", "") or "").casefold()
            for item in story_map.get("research_prior_alignments", [])
            if isinstance(item, dict)
            and item.get("prior_type") == "episode_plot"
            and item.get("candidate_local_ranges")
        }
        if "reversal" in research_purposes and "reversal_reveal" not in purposes:
            errors.append("semantic story omits the locally aligned research reversal")
        if (
            research_purposes & {"payoff", "payoff_climax", "climax"}
            and "payoff_climax" not in purposes
        ):
            errors.append("semantic story omits the locally aligned research payoff")
        if not story_map.get("causal_graph"):
            errors.append("semantic story has no reasoned causal links")
    if has_semantic_interpretation:
        for edge in story_map.get("causal_graph", []):
            if isinstance(edge, dict) and not str(edge.get("reason", "")).strip():
                errors.append(
                    f"causal edge {edge.get('parent')} -> {edge.get('child')} lacks reasoning"
                )

    if errors:
        raise StoryGroundingError(
            "Story grounding quality gate failed: " + "; ".join(errors)
        )
