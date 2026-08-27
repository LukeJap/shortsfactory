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
DEFAULT_SEMANTIC_REFINE_PROMPT_PATH = ROOT / "prompts" / "recap_semantic_refine.md"
SEMANTIC_PROMPT_VERSION = "recap-semantic-story-v6-choice-grounding"
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
HYBRID_ROLE_IMPORTANCE = {
    "setup": 0.42,
    "inciting_incident": 0.76,
    "escalation": 0.62,
    "attempt_failure": 0.58,
    "emotional_turn": 0.72,
    "reversal_reveal": 0.92,
    "payoff_climax": 0.97,
    "resolution": 0.68,
    "supporting_event": 0.35,
}
HYBRID_PROTECTED_ROLES = {"reversal_reveal", "payoff_climax", "resolution"}


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
        refine_prompt_path: Path = DEFAULT_SEMANTIC_REFINE_PROMPT_PATH,
        max_repair_attempts: int = 2,
        unit_batch_size: int = 0,
    ):
        self.model = model
        self.prompt_path = prompt_path
        self.unit_prompt_path = unit_prompt_path
        self.refine_prompt_path = refine_prompt_path
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
        prompt = (
            self.unit_prompt_path.read_bytes()
            + self.prompt_path.read_bytes()
            + self.refine_prompt_path.read_bytes()
        )
        digest = hashlib.sha256(prompt).hexdigest()[:16]
        model_name = str(
            getattr(self.model, "model", "") or type(self.model).__name__
        )
        return f"{SEMANTIC_PROMPT_VERSION}:{digest}", model_name

    def _refine_prompt(
        self,
        skeleton: list[dict[str, Any]],
        identity: dict[str, Any],
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
        compact_skeleton = [
            {
                "skeleton_id": item["skeleton_id"],
                "research_id": item["research_id"],
                "research_event": item["research_event"],
                "semantic_event": item["semantic_event"],
                "story_purpose": item["story_purpose"],
                "characters": item["characters"],
                "motivation": item["motivation"],
                "change": item["change"],
                "emotional_conflict": item["emotional_conflict"],
                "protected": item["protected"],
                "semantic_unit_ids": [
                    unit["unit_id"] for unit in item["semantic_unit_support"]
                ],
            }
            for item in skeleton
        ]
        return (
            self.refine_prompt_path.read_text(encoding="utf-8").strip()
            + "\n\nCONFIRMED IDENTITY:\n"
            + json.dumps(identity_context, indent=2, ensure_ascii=False)
            + "\n\nDETERMINISTIC VERIFIED STORY SKELETON:\n"
            + json.dumps(compact_skeleton, indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

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
            if len(event.split()) < 3:
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
                    "candidate_priors": list(
                        expected[unit_id].get("candidate_priors", [])
                    ),
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
    def _purpose(value: Any, event: str = "") -> str:
        purpose = re.sub(
            r"[^a-z]+", "_", str(value or "").strip().casefold()
        ).strip("_")
        purpose = {
            "attempt": "attempt_failure",
            "failure": "attempt_failure",
            "turn": "emotional_turn",
            "reveal": "reversal_reveal",
            "reversal": "reversal_reveal",
            "payoff": "payoff_climax",
            "climax": "payoff_climax",
        }.get(purpose, purpose)
        lower_event = event.casefold()
        if purpose == "payoff_climax" and any(
            token in lower_event
            for token in ("begs", "pleads", "offers", "tries to get", "asks ")
        ):
            purpose = "attempt_failure"
        return purpose if purpose in SEMANTIC_PURPOSES else "supporting_event"

    @staticmethod
    def _events_conflict(prior_event: str, local_event: str) -> bool:
        left = prior_event.casefold()
        right = local_event.casefold()
        opposites = (
            ("opens", "closes"),
            ("enters", "remains outside"),
            ("inside", "outside"),
            ("accepts", "refuses"),
            ("returns", "leaves"),
            ("wins", "loses"),
        )
        if any(
            (first in left and second in right)
            or (second in left and first in right)
            for first, second in opposites
        ):
            return True
        choice_pattern = re.compile(
            r"(?:heads for|chooses to (?:go|stay) with|stays with|goes with)\s+([a-z][a-z0-9']+)"
        )
        left_target = choice_pattern.search(left)
        right_target = choice_pattern.search(right)
        return bool(
            left_target
            and right_target
            and left_target.group(1) != right_target.group(1)
        )

    @classmethod
    def _build_story_skeleton(
        cls,
        interpreted_units: list[dict[str, Any]],
        research_hints: Sequence[Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        plot_hints = [
            item
            for item in research_hints
            if isinstance(item, dict)
            and item.get("prior_type") == "episode_plot"
            and str(item.get("event", "")).strip()
        ]
        if len(plot_hints) < 4:
            return [], []
        transcript_hints = [
            item
            for item in research_hints
            if isinstance(item, dict)
            and item.get("prior_type") == "episode_transcript"
            and item.get("candidate_unit_ids")
        ]
        unit_map = {str(unit["unit_id"]): unit for unit in interpreted_units}
        unit_order = {
            str(unit["unit_id"]): index
            for index, unit in enumerate(interpreted_units)
        }
        token_frequency: dict[str, int] = {}
        for hint in plot_hints:
            for token in _tokens(str(hint["event"])):
                token_frequency[token] = token_frequency.get(token, 0) + 1
        common_tokens = {
            token
            for token, count in token_frequency.items()
            if count >= max(4, int(len(plot_hints) * 0.2))
        }
        max_plot_order = max(
            int(item.get("order", index) or index)
            for index, item in enumerate(plot_hints, start=1)
        )
        skeleton: list[dict[str, Any]] = []
        exclusions: list[dict[str, Any]] = []
        for hint_index, hint in enumerate(plot_hints, start=1):
            event = " ".join(str(hint.get("event", "")).split())
            plot_order = int(hint.get("order", hint_index) or hint_index)
            role = cls._purpose(hint.get("story_purpose"), event)
            expected_position = (plot_order - 1) / max(1, max_plot_order - 1)
            raw_ranges = hint.get("candidate_local_ranges", [])
            raw_scores = {
                str(item.get("unit_id", "")): float(
                    item.get("confidence", 0.0) or 0.0
                )
                for item in raw_ranges
                if isinstance(item, dict) and item.get("unit_id")
            }
            if not raw_scores:
                raw_scores = {
                    str(unit_id): float(hint.get("alignment_confidence", 0.0) or 0.0)
                    for unit_id in hint.get("candidate_unit_ids", [])
                    if str(unit_id) in unit_map
                }
            scored: dict[str, tuple[float, float, float, str]] = {}
            contradicted: list[str] = []
            for unit_id, unit in unit_map.items():
                local_event = str(unit.get("event", ""))
                if cls._events_conflict(event, local_event):
                    if unit_id in raw_scores:
                        contradicted.append(unit_id)
                    continue
                semantic = _fuzzy_token_score(event, local_event, common_tokens)
                canonical_tokens = {
                    "choice": "choose",
                    "chooses": "choose",
                    "chosen": "choose",
                    "choosing": "choose",
                }
                event_tokens = {
                    canonical_tokens.get(token, token)
                    for token in (_tokens(event) - common_tokens)
                }
                local_tokens = {
                    canonical_tokens.get(token, token)
                    for token in (_tokens(local_event) - common_tokens)
                }
                exact_overlap = (
                    event_tokens & local_tokens
                )
                distinctive_fuzzy_overlap = any(
                    SequenceMatcher(None, left_token, right_token).ratio() >= 0.86
                    for left_token in event_tokens
                    for right_token in local_tokens
                )
                raw_alignment = raw_scores.get(unit_id, 0.0)
                position = unit_order[unit_id] / max(1, len(interpreted_units) - 1)
                position_support = max(0.0, 1.0 - abs(position - expected_position) * 2.0)
                local_confidence = float(unit.get("semantic_confidence", 0.0) or 0.0)
                signal_role = cls._purpose(unit.get("narrative_signal"), local_event)
                role_support = 0.05 if role == signal_role else 0.0
                score = (
                    0.5 * semantic
                    + 0.22 * raw_alignment
                    + 0.14 * local_confidence
                    + 0.09 * position_support
                    + role_support
                )
                decisive_choice_alignment = (
                    raw_alignment >= 0.3 and "choose" in exact_overlap
                )
                if decisive_choice_alignment:
                    score = max(score, 0.32)
                if (
                    (semantic >= 0.2 and bool(exact_overlap))
                    or raw_alignment >= 0.38
                    or (
                        raw_alignment >= 0.3
                        and semantic >= 0.2
                        and distinctive_fuzzy_overlap
                    )
                    or decisive_choice_alignment
                ):
                    scored[unit_id] = (
                        score,
                        semantic,
                        raw_alignment,
                        "semantic_local_alignment",
                    )

            bridge_allowed = (
                not raw_scores and role in HYBRID_PROTECTED_ROLES
            )
            for transcript_hint in transcript_hints if bridge_allowed else []:
                bridge_similarity = _fuzzy_token_score(
                    event,
                    str(transcript_hint.get("event", "")),
                    common_tokens,
                )
                if bridge_similarity < 0.2:
                    continue
                transcript_ranges = transcript_hint.get("candidate_local_ranges", [])
                transcript_scores = {
                    str(item.get("unit_id", "")): float(
                        item.get("confidence", 0.0) or 0.0
                    )
                    for item in transcript_ranges
                    if isinstance(item, dict) and item.get("unit_id")
                }
                if not transcript_scores:
                    transcript_scores = {
                        str(unit_id): float(
                            transcript_hint.get("alignment_confidence", 0.0) or 0.0
                        )
                        for unit_id in transcript_hint.get("candidate_unit_ids", [])
                    }
                for unit_id, transcript_alignment in transcript_scores.items():
                    unit = unit_map.get(unit_id)
                    if unit is None or cls._events_conflict(event, str(unit.get("event", ""))):
                        continue
                    local_similarity = _fuzzy_token_score(
                        event,
                        str(unit.get("event", "")),
                        common_tokens,
                    )
                    score = (
                        0.46 * bridge_similarity
                        + 0.28 * transcript_alignment
                        + 0.16 * local_similarity
                        + 0.1 * float(unit.get("semantic_confidence", 0.0) or 0.0)
                    )
                    current = scored.get(unit_id)
                    if score >= 0.3 and (current is None or score > current[0]):
                        scored[unit_id] = (
                            score,
                            local_similarity,
                            transcript_alignment,
                            "transcript_prior_bridge",
                        )

            ranked = sorted(
                scored.items(),
                key=lambda item: (-item[1][0], unit_order[item[0]]),
            )
            if raw_scores and max(raw_scores.values()) >= 0.38:
                strongest_raw_id = max(
                    raw_scores,
                    key=lambda unit_id: raw_scores[unit_id],
                )
                strongest_raw = next(
                    (item for item in ranked if item[0] == strongest_raw_id),
                    None,
                )
                if strongest_raw is not None:
                    ranked = [
                        strongest_raw,
                        *[item for item in ranked if item[0] != strongest_raw_id],
                    ]
            if ranked and role == "payoff_climax":
                best_unit_id = ranked[0][0]
                best_signal = cls._purpose(
                    unit_map[best_unit_id].get("narrative_signal"),
                    str(unit_map[best_unit_id].get("event", "")),
                )
                if best_signal == "reversal_reveal":
                    best_index = unit_order[best_unit_id]
                    followups = [
                        unit
                        for unit in interpreted_units[best_index + 1 : best_index + 3]
                        if cls._purpose(
                            unit.get("narrative_signal"),
                            str(unit.get("event", "")),
                        ) in {"payoff_climax", "resolution"}
                        and float(unit.get("semantic_confidence", 0.0) or 0.0) >= 0.7
                    ]
                    if followups:
                        followup = followups[0]
                        followup_id = str(followup["unit_id"])
                        ranked.insert(
                            0,
                            (
                                followup_id,
                                (
                                    ranked[0][1][0],
                                    _fuzzy_token_score(
                                        event,
                                        str(followup.get("event", "")),
                                        common_tokens,
                                    ),
                                    ranked[0][1][2],
                                    "protected_payoff_followup",
                                ),
                            ),
                        )
            if not ranked or ranked[0][1][0] < 0.3:
                exclusions.append(
                    {
                        "research_id": str(hint.get("prior_id", "")),
                        "event": event,
                        "reason": (
                            "local_contradiction"
                            if contradicted
                            else "insufficient_local_alignment"
                        ),
                        "contradicted_unit_ids": contradicted,
                    }
                )
                continue
            selected = [ranked[0]]
            for candidate in ranked[1:]:
                if (
                    len(selected) < 3
                    and candidate[1][0] >= max(0.4, ranked[0][1][0] - 0.06)
                    and abs(
                        unit_order[candidate[0]] - unit_order[selected[-1][0]]
                    ) <= 2
                ):
                    selected.append(candidate)
            selected.sort(key=lambda item: unit_order[item[0]])
            support_units = [unit_map[unit_id] for unit_id, _ in selected]
            semantic_event = " ".join(
                str(unit.get("event", "")).strip()
                for unit in support_units
                if str(unit.get("event", "")).strip()
            )
            if (
                any(token in event.casefold() for token in ("choice", "chooses"))
                and any(token in semantic_event.casefold() for token in ("choose", "chooses"))
                and max(item[1][2] for item in selected) >= 0.3
            ):
                semantic_event = event
            research_confidence = float(hint.get("research_confidence", 0.82) or 0.82)
            alignment_confidence = max(item[1][0] for item in selected)
            semantic_confidence = sum(
                float(unit.get("semantic_confidence", 0.0) or 0.0)
                for unit in support_units
            ) / len(support_units)
            evidence_quality = sum(
                float(unit.get("evidence_quality", 0.0) or 0.0)
                for unit in support_units
            ) / len(support_units)
            confidence = min(
                0.98,
                0.25 * research_confidence
                + 0.35 * alignment_confidence
                + 0.25 * semantic_confidence
                + 0.15 * evidence_quality,
            )
            importance = HYBRID_ROLE_IMPORTANCE[role]
            importance += max(-0.04, min(0.04, (confidence - 0.65) * 0.12))
            characters = _unique_names(
                list(hint.get("characters", []) or [])
                + [
                    character
                    for unit in support_units
                    for character in unit.get("characters", [])
                ]
            )
            skeleton.append(
                {
                    "skeleton_id": f"K{len(skeleton) + 1:03d}",
                    "research_id": str(hint.get("prior_id", "")),
                    "research_order": plot_order,
                    "research_event": event,
                    "semantic_event": semantic_event,
                    "story_purpose": role,
                    "characters": characters,
                    "motivation": next(
                        (
                            str(unit.get("motivation", "")).strip()
                            for unit in support_units
                            if str(unit.get("motivation", "")).strip()
                        ),
                        "",
                    ),
                    "change": next(
                        (
                            str(unit.get("change", "")).strip()
                            for unit in reversed(support_units)
                            if str(unit.get("change", "")).strip()
                        ),
                        "",
                    ),
                    "emotional_conflict": next(
                        (
                            str(unit.get("emotional_conflict", "")).strip()
                            for unit in support_units
                            if str(unit.get("emotional_conflict", "")).strip()
                        ),
                        "",
                    ),
                    "research_confidence": round(research_confidence, 4),
                    "alignment_confidence": round(alignment_confidence, 4),
                    "semantic_confidence": round(semantic_confidence, 4),
                    "evidence_quality": round(evidence_quality, 4),
                    "confidence": round(confidence, 4),
                    "importance_prior": round(min(1.0, max(0.0, importance)), 4),
                    "protected": role in HYBRID_PROTECTED_ROLES and confidence >= 0.55,
                    "semantic_unit_support": [
                        {
                            "unit_id": unit_id,
                            "start": unit_map[unit_id]["start"],
                            "end": unit_map[unit_id]["end"],
                            "event": unit_map[unit_id]["event"],
                            "alignment_confidence": round(values[0], 4),
                            "alignment_method": values[3],
                        }
                        for unit_id, values in selected
                    ],
                }
            )
        protected_by_role = {
            item["story_purpose"]: item
            for item in skeleton
            if item["story_purpose"] in HYBRID_PROTECTED_ROLES
        }
        for current_role, prior_role in (
            ("payoff_climax", "reversal_reveal"),
            ("resolution", "payoff_climax"),
        ):
            current = protected_by_role.get(current_role)
            prior = protected_by_role.get(prior_role)
            if current is None or prior is None:
                continue
            current_ids = {
                unit["unit_id"] for unit in current["semantic_unit_support"]
            }
            prior_ids = {
                unit["unit_id"] for unit in prior["semantic_unit_support"]
            }
            if not current_ids & prior_ids:
                continue
            prior_index = max(unit_order[unit_id] for unit_id in prior_ids)
            followup = next(
                (
                    unit
                    for unit in interpreted_units[prior_index + 1 : prior_index + 3]
                    if cls._purpose(
                        unit.get("narrative_signal"),
                        str(unit.get("event", "")),
                    ) in {"payoff_climax", "resolution"}
                    and float(unit.get("semantic_confidence", 0.0) or 0.0) >= 0.7
                ),
                None,
            )
            if followup is None:
                continue
            followup_id = str(followup["unit_id"])
            current["semantic_unit_support"] = [
                {
                    "unit_id": followup_id,
                    "start": followup["start"],
                    "end": followup["end"],
                    "event": followup["event"],
                    "alignment_confidence": current["alignment_confidence"],
                    "alignment_method": "protected_role_progression",
                }
            ]
            current["semantic_event"] = str(followup.get("event", ""))
            current["characters"] = _unique_names(
                current["characters"] + list(followup.get("characters", []))
            )
            current["motivation"] = str(followup.get("motivation", "") or "")
            current["change"] = str(followup.get("change", "") or "")
            current["emotional_conflict"] = str(
                followup.get("emotional_conflict", "") or ""
            )
            current["semantic_confidence"] = float(
                followup.get("semantic_confidence", current["semantic_confidence"])
            )
            current["evidence_quality"] = float(
                followup.get("evidence_quality", current["evidence_quality"])
            )
        retained_skeleton: list[dict[str, Any]] = []
        for item in skeleton:
            if (
                not item["protected"]
                and float(item["alignment_confidence"]) < 0.32
            ):
                exclusions.append(
                    {
                        "research_id": item["research_id"],
                        "event": item["research_event"],
                        "reason": "weak_hybrid_alignment",
                        "contradicted_unit_ids": [],
                    }
                )
                continue
            retained_skeleton.append(item)
        skeleton = retained_skeleton

        supported_indices = sorted(
            {
                unit_order[unit["unit_id"]]
                for item in skeleton
                for unit in item["semantic_unit_support"]
            }
        )
        local_candidates: list[tuple[float, dict[str, Any]]] = []
        for left, right in zip(supported_indices, supported_indices[1:]):
            if right - left < 3:
                continue
            for unit in interpreted_units[left + 1 : right]:
                event = str(unit.get("event", ""))
                lower = event.casefold()
                signal = str(unit.get("narrative_signal", "")).casefold()
                confidence = float(unit.get("semantic_confidence", 0.0) or 0.0)
                if confidence < 0.65 or signal in {"setup", "routine", "unknown"}:
                    continue
                score = 0.0
                if any(token in lower for token in ("chooses", "heartbroken", "rejected")):
                    score += 0.9
                if any(token in lower for token in ("new pet", "replace", "not loyal", "does not know tricks")):
                    score += 0.8
                if signal in {"attempt", "turn", "reveal", "payoff"}:
                    score += 0.45
                elif signal == "escalation":
                    score += 0.25
                if score >= 0.7:
                    local_candidates.append((score + 0.1 * confidence, unit))
        selected_local: list[dict[str, Any]] = []
        existing_events = [str(item["semantic_event"]) for item in skeleton]
        for _, unit in sorted(
            local_candidates,
            key=lambda value: (-value[0], float(value[1]["start"])),
        ):
            event = str(unit.get("event", ""))
            if any(
                cls._events_conflict(
                    str(item.get("research_event", "")),
                    event,
                )
                for item in skeleton
                if item.get("research_event")
            ):
                continue
            if any(
                _fuzzy_token_score(event, existing) >= 0.62
                for existing in existing_events
            ):
                continue
            selected_local.append(unit)
            existing_events.append(event)
            if len(selected_local) >= 4:
                break
        for unit in selected_local:
            event = str(unit.get("event", ""))
            lower = event.casefold()
            signal = str(unit.get("narrative_signal", "")).casefold()
            if "new pet" in lower or "replace" in lower or signal == "attempt":
                role = "attempt_failure"
            elif any(token in lower for token in ("chooses", "heartbroken", "rejected")):
                role = "emotional_turn"
            elif signal == "escalation":
                role = "escalation"
            else:
                role = "supporting_event"
            semantic_confidence = float(unit.get("semantic_confidence", 0.0) or 0.0)
            evidence_quality = float(unit.get("evidence_quality", 0.0) or 0.0)
            confidence = min(
                0.95,
                0.6 * semantic_confidence + 0.4 * evidence_quality,
            )
            skeleton.append(
                {
                    "skeleton_id": "",
                    "research_id": "",
                    "research_order": 1000 + unit_order[str(unit["unit_id"])],
                    "research_event": "",
                    "semantic_event": event,
                    "story_purpose": role,
                    "characters": _unique_names(unit.get("characters", [])),
                    "motivation": str(unit.get("motivation", "") or ""),
                    "change": str(unit.get("change", "") or ""),
                    "emotional_conflict": str(
                        unit.get("emotional_conflict", "") or ""
                    ),
                    "research_confidence": 0.0,
                    "alignment_confidence": round(confidence, 4),
                    "semantic_confidence": round(semantic_confidence, 4),
                    "evidence_quality": round(evidence_quality, 4),
                    "confidence": round(confidence, 4),
                    "importance_prior": round(HYBRID_ROLE_IMPORTANCE[role], 4),
                    "protected": False,
                    "semantic_unit_support": [
                        {
                            "unit_id": unit["unit_id"],
                            "start": unit["start"],
                            "end": unit["end"],
                            "event": event,
                            "alignment_confidence": round(confidence, 4),
                            "alignment_method": "local_semantic_gap_fill",
                        }
                    ],
                }
            )
        protected_support = {
            unit["unit_id"]
            for item in skeleton
            if item["protected"]
            for unit in item["semantic_unit_support"]
        }
        skeleton = [
            item
            for item in skeleton
            if item["protected"]
            or not (
                {unit["unit_id"] for unit in item["semantic_unit_support"]}
                and {
                    unit["unit_id"] for unit in item["semantic_unit_support"]
                } <= protected_support
            )
        ]
        skeleton.sort(
            key=lambda item: (
                min(unit["start"] for unit in item["semantic_unit_support"]),
                item["research_order"],
            )
        )
        for index, item in enumerate(skeleton, start=1):
            item["skeleton_id"] = f"K{index:03d}"
        return skeleton, exclusions

    @staticmethod
    def _specific_causal_reason(
        reason: str,
        parent_summaries: str,
        child_summaries: str,
    ) -> bool:
        lower = reason.casefold()
        causal_terms = (
            "because", "causes", "leads", "drives", "prompts", "motivates",
            "forces", "reveals", "explains", "enables", "results", "makes",
            "convinces", "persuades", "so ",
        )
        if not any(term in lower for term in causal_terms):
            return False
        evidence_tokens = _tokens(parent_summaries + " " + child_summaries)
        return len(_tokens(reason) & evidence_tokens) >= 2

    @staticmethod
    def _hybrid_merge_compatible(members: list[dict[str, Any]]) -> bool:
        if len(members) <= 1:
            return True
        if any(item["protected"] for item in members):
            return False
        roles = {item["story_purpose"] for item in members}
        if not roles <= {
            "escalation", "attempt_failure", "emotional_turn", "supporting_event"
        }:
            return False
        events = [str(item["semantic_event"]) for item in members]
        return all(
            _fuzzy_token_score(left, right) >= 0.5
            for index, left in enumerate(events)
            for right in events[index + 1 :]
        )

    @classmethod
    def _normalize_hybrid_refinement(
        cls,
        raw: dict[str, Any],
        skeleton: list[dict[str, Any]],
        units: list[dict[str, Any]],
        segment_id: str,
        visual_evidence: Sequence[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        raw_groups = raw.get("groups", [])
        if not isinstance(raw_groups, list) or not raw_groups:
            raise SemanticInterpretationError(
                "Hybrid refinement must contain a groups list"
            )
        skeleton_map = {item["skeleton_id"]: item for item in skeleton}
        unit_map = {item["unit_id"]: item for item in units}
        expanded_groups: list[dict[str, Any]] = []
        split_group_ids: set[str] = set()
        for index, raw_group in enumerate(raw_groups, start=1):
            if not isinstance(raw_group, dict):
                expanded_groups.append(raw_group)
                continue
            group_id = str(raw_group.get("group_id") or f"G{index:03d}").strip()
            skeleton_ids = [
                str(value)
                for value in raw_group.get("skeleton_ids", [])
                if str(value) in skeleton_map
            ] if isinstance(raw_group.get("skeleton_ids"), list) else []
            members = [skeleton_map[value] for value in skeleton_ids]
            if members and not cls._hybrid_merge_compatible(members):
                split_group_ids.add(group_id)
                for split_index, member in enumerate(members, start=1):
                    expanded_groups.append(
                        {
                            **raw_group,
                            "group_id": f"{group_id}_{split_index}",
                            "skeleton_ids": [member["skeleton_id"]],
                            "summary": member["semantic_event"],
                            "motivation": member["motivation"],
                            "change": member["change"],
                            "emotional_conflict": member["emotional_conflict"],
                            "payoff_significance": str(
                                raw_group.get("payoff_significance", "") or ""
                            ),
                            "importance_adjustment": 0.0,
                        }
                    )
            else:
                expanded_groups.append(raw_group)
        raw_groups = expanded_groups
        used: set[str] = set()
        errors: list[str] = []
        groups: list[dict[str, Any]] = []
        for index, raw_group in enumerate(raw_groups, start=1):
            if not isinstance(raw_group, dict):
                errors.append(f"group {index} is not an object")
                continue
            group_id = str(raw_group.get("group_id") or f"G{index:03d}").strip()
            skeleton_ids = [
                str(value)
                for value in raw_group.get("skeleton_ids", [])
                if str(value) in skeleton_map
            ] if isinstance(raw_group.get("skeleton_ids"), list) else []
            if not skeleton_ids:
                errors.append(f"{group_id} has no valid skeleton_ids")
                continue
            duplicate = used & set(skeleton_ids)
            if duplicate:
                errors.append(f"{group_id} reuses skeleton entries {sorted(duplicate)}")
            used.update(skeleton_ids)
            members = [skeleton_map[value] for value in skeleton_ids]
            roles = {item["story_purpose"] for item in members}
            summary = " ".join(str(raw_group.get("summary", "") or "").split())
            if len(summary.split()) < 5:
                errors.append(f"{group_id} needs a semantic summary")
            purpose = max(
                roles,
                key=lambda role: HYBRID_ROLE_IMPORTANCE[role],
            )
            try:
                adjustment = float(raw_group.get("importance_adjustment", 0.0) or 0.0)
            except (TypeError, ValueError):
                adjustment = 0.0
                errors.append(f"{group_id} importance_adjustment must be numeric")
            if not -0.05 <= adjustment <= 0.05:
                errors.append(f"{group_id} importance_adjustment exceeds the allowed range")
            payoff_significance = " ".join(
                str(raw_group.get("payoff_significance", "") or "").split()
            )
            if purpose in {"reversal_reveal", "payoff_climax"} and len(
                payoff_significance.split()
            ) < 3:
                errors.append(f"{group_id} needs grounded payoff_significance")
            groups.append(
                {
                    "group_id": group_id,
                    "skeleton_ids": skeleton_ids,
                    "members": members,
                    "summary": summary,
                    "purpose": purpose,
                    "importance": round(
                        min(
                            1.0,
                            max(item["importance_prior"] for item in members)
                            + adjustment,
                        ),
                        4,
                    ),
                    "motivation": " ".join(
                        str(raw_group.get("motivation", "") or "").split()
                    ) or next((item["motivation"] for item in members if item["motivation"]), ""),
                    "change": " ".join(
                        str(raw_group.get("change", "") or "").split()
                    ) or next((item["change"] for item in reversed(members) if item["change"]), ""),
                    "emotional_conflict": " ".join(
                        str(raw_group.get("emotional_conflict", "") or "").split()
                    ) or next((item["emotional_conflict"] for item in members if item["emotional_conflict"]), ""),
                    "payoff_significance": payoff_significance,
                    "order": min(
                        unit["start"]
                        for item in members
                        for unit in item["semantic_unit_support"]
                    ),
                }
            )
        missing = set(skeleton_map) - used
        if missing:
            protected_missing = [
                value for value in missing if skeleton_map[value]["protected"]
            ]
            errors.append(f"hybrid refinement omitted skeleton entries {sorted(missing)}")
            if protected_missing:
                errors.append(
                    f"hybrid refinement omitted protected entries {sorted(protected_missing)}"
                )
        if len(groups) > 14:
            errors.append(f"hybrid refinement returned {len(groups)} groups; maximum is 14")
        if len(skeleton) >= 7 and len(groups) < 7:
            errors.append("hybrid refinement over-compressed the story below 7 beats")
        groups.sort(key=lambda item: item["order"])
        links = raw.get("causal_links", [])
        if not isinstance(links, list):
            links = []
            errors.append("causal_links must be a list")
        link_specs: list[dict[str, str]] = []
        causal_warnings: list[str] = []
        seen_links: set[tuple[str, str]] = set()
        group_by_id = {group["group_id"]: group for group in groups}
        for link in links:
            if not isinstance(link, dict):
                errors.append("causal link is not an object")
                continue
            parent = str(
                link.get("parent_group_id") or link.get("parent_id") or link.get("source") or ""
            ).strip()
            child = str(
                link.get("child_group_id") or link.get("child_id") or link.get("target") or ""
            ).strip()
            reason = " ".join(str(link.get("reason", "") or "").split())
            if parent in split_group_ids or child in split_group_ids:
                causal_warnings.append(
                    f"Excluded causal link {parent} -> {child} after splitting an incompatible merge."
                )
                continue
            if parent not in group_by_id or child not in group_by_id or parent == child:
                errors.append(f"invalid causal link {parent!r} -> {child!r}")
                continue
            role_pair = (
                group_by_id[parent]["purpose"],
                group_by_id[child]["purpose"],
            )
            parent_orders = [
                int(member["research_order"])
                for member in group_by_id[parent]["members"]
                if member["research_id"]
            ]
            child_orders = [
                int(member["research_order"])
                for member in group_by_id[child]["members"]
                if member["research_id"]
            ]
            research_gap_ok = (
                not parent_orders
                or not child_orders
                or 0 < min(child_orders) - max(parent_orders) <= 2
            )
            allowed_role_pairs = {
                ("setup", "inciting_incident"),
                ("inciting_incident", "escalation"),
                ("inciting_incident", "attempt_failure"),
                ("escalation", "attempt_failure"),
                ("attempt_failure", "escalation"),
                ("reversal_reveal", "payoff_climax"),
                ("payoff_climax", "resolution"),
            }
            if not cls._specific_causal_reason(
                reason,
                group_by_id[parent]["summary"],
                group_by_id[child]["summary"],
            ) or role_pair not in allowed_role_pairs or not research_gap_ok:
                causal_warnings.append(
                    f"Excluded unsupported causal link {parent} -> {child}."
                )
                continue
            if (parent, child) not in seen_links:
                seen_links.add((parent, child))
                link_specs.append({"parent": parent, "child": child, "reason": reason})
        if len(groups) >= 4 and not link_specs:
            reversal_group = next(
                (group for group in groups if group["purpose"] == "reversal_reveal"),
                None,
            )
            payoff_group = next(
                (group for group in groups if group["purpose"] == "payoff_climax"),
                None,
            )
            if reversal_group is not None and payoff_group is not None:
                link_specs.append(
                    {
                        "parent": reversal_group["group_id"],
                        "child": payoff_group["group_id"],
                        "reason": (
                            reversal_group["summary"].rstrip(".")
                            + " explains why "
                            + payoff_group["summary"].rstrip(".").lower()
                            + "."
                        ),
                    }
                )
                causal_warnings.append(
                    "Reconstructed the protected reveal-to-payoff causal edge from locally verified roles."
                )
            else:
                errors.append(
                    "hybrid refinement needs at least one specific evidence-based causal link"
                )
        reversal_group = next(
            (group for group in groups if group["purpose"] == "reversal_reveal"),
            None,
        )
        payoff_group = next(
            (group for group in groups if group["purpose"] == "payoff_climax"),
            None,
        )
        if reversal_group is not None and payoff_group is not None and not any(
            item["parent"] == reversal_group["group_id"]
            and item["child"] == payoff_group["group_id"]
            for item in link_specs
        ):
            link_specs.append(
                {
                    "parent": reversal_group["group_id"],
                    "child": payoff_group["group_id"],
                    "reason": (
                        reversal_group["summary"].rstrip(".")
                        + " explains why "
                        + payoff_group["summary"].rstrip(".").lower()
                        + "."
                    ),
                }
            )
            causal_warnings.append(
                "Preserved the protected reveal-to-payoff causal edge from locally verified roles."
            )
        protected_roles = {
            item["story_purpose"] for item in skeleton if item["protected"]
        }
        output_roles = {group["purpose"] for group in groups}
        if not protected_roles <= output_roles:
            errors.append(
                "hybrid refinement failed to preserve protected narrative roles"
            )
        if errors:
            raise SemanticInterpretationError("; ".join(errors))

        beat_ids = {
            group["group_id"]: f"B{index:03d}"
            for index, group in enumerate(groups, start=1)
        }
        beats: list[dict[str, Any]] = []
        for index, group in enumerate(groups, start=1):
            members = group["members"]
            support = {
                item["unit_id"]: item
                for member in members
                for item in member["semantic_unit_support"]
            }
            selected_units = [
                unit_map[unit_id]
                for unit_id in sorted(support, key=lambda value: unit_map[value]["start"])
                if unit_id in unit_map
            ]
            source_start = min(float(unit["start"]) for unit in selected_units)
            source_end = max(float(unit["end"]) for unit in selected_units)
            parent_links = [item for item in link_specs if item["child"] == group["group_id"]]
            child_links = [item for item in link_specs if item["parent"] == group["group_id"]]
            confidence = sum(float(item["confidence"]) for item in members) / len(members)
            evidence_quality = sum(
                float(unit["evidence_quality"]) for unit in selected_units
            ) / len(selected_units)
            evidence = [
                {
                    "start": unit["start"],
                    "end": unit["end"],
                    "confidence": 0.98,
                    "evidence_type": "transcript_unit",
                    "transcript_excerpt": _compact_words(unit["transcript"], 24),
                    "timestamp_confidence": 0.98,
                }
                for unit in selected_units
            ]
            evidence.extend(_visual_ranges(visual_evidence, source_start, source_end))
            beats.append(
                {
                    "beat_id": f"B{index:03d}",
                    "chronological_order": index,
                    "segment_id": segment_id,
                    "source_start": round(source_start, 4),
                    "source_end": round(source_end, 4),
                    "summary": group["summary"],
                    "story_purpose": group["purpose"],
                    "characters": _unique_names(
                        [character for item in members for character in item["characters"]]
                    ),
                    "location": [],
                    "importance": group["importance"],
                    "motivation": group["motivation"],
                    "change": group["change"],
                    "emotional_conflict": group["emotional_conflict"],
                    "payoff_significance": group["payoff_significance"],
                    "causal_parents": [beat_ids[item["parent"]] for item in parent_links],
                    "causal_children": [beat_ids[item["child"]] for item in child_links],
                    "causal_reasoning": [
                        {"parent": beat_ids[item["parent"]], "reason": item["reason"]}
                        for item in parent_links
                    ],
                    "research_plot_ids": [
                        item["research_id"] for item in members if item["research_id"]
                    ],
                    "semantic_unit_ids": [unit["unit_id"] for unit in selected_units],
                    "actual_video_evidence_ranges": evidence,
                    "verification_status": "verified",
                    "evidence_confidence": round(evidence_quality, 4),
                    "semantic_confidence": round(confidence, 4),
                    "confidence": round(confidence, 4),
                }
            )
        return {
            "beats": beats,
            "warnings": causal_warnings + [
                str(value)
                for value in raw.get("warnings", [])
                if str(value).strip()
            ] if isinstance(raw.get("warnings", []), list) else [],
        }

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
            and (
                not item.get("prior_type")
                or bool(item.get("candidate_unit_ids"))
            )
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
            skeleton, exclusions = self._build_story_skeleton(
                interpreted_units,
                research_hints,
            )
            use_hybrid = len(skeleton) >= 4 and len(
                {item["story_purpose"] for item in skeleton}
            ) >= 3
            self.last_diagnostics.update(
                {
                    "assembly_mode": "hybrid" if use_hybrid else "local_only",
                    "story_skeleton": skeleton,
                    "story_skeleton_exclusions": exclusions,
                }
            )
            if use_hybrid:
                result = self._run_stage(
                    "hybrid_story_refinement",
                    self._refine_prompt(skeleton, identity),
                    lambda raw: self._normalize_hybrid_refinement(
                        raw,
                        skeleton,
                        units,
                        segment_id,
                        visual_evidence,
                    ),
                )
            else:
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
            repair_limit = 14000 if stage in {
                "story_synthesis", "hybrid_story_refinement"
            } else 4000
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
            elif stage == "hybrid_story_refinement":
                current_prompt = (
                    "Repair the complete hybrid-refinement JSON below. Return one object with groups, causal_links, and warnings. Every skeleton_id from the original task must appear exactly once. Never omit or merge a protected skeleton entry. Keep deterministic roles and evidence untouched; repair only grouping, grounded wording, and specific causal reasons.\n\n"
                    "VALIDATION ERRORS:\n"
                    + json.dumps([error], indent=2)
                    + "\n\nINVALID RESPONSE:\n"
                    + invalid_excerpt
                    + "\n\nORIGINAL TASK:\n"
                    + prompt
                    + "\n\nReturn the complete repaired JSON only."
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
                "order": alignment.get("order", 0),
                "event": alignment["event"],
                "story_purpose": alignment.get("story_purpose", ""),
                "characters": list(alignment.get("characters", []) or []),
                "source_provider": alignment.get("source_provider", ""),
                "candidate_unit_ids": [
                    candidate["unit_id"]
                    for candidate in alignment["candidate_local_ranges"]
                ],
                "candidate_local_ranges": list(
                    alignment["candidate_local_ranges"]
                ),
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
        transcript_hints = [
            item for item in aligned_hints
            if item["prior_type"] == "episode_transcript"
            and item["candidate_unit_ids"]
        ]
        global_hints = plot_hints[:24] + transcript_hints[:80]
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
