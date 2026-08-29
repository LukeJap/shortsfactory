"""Conservative transcript-aware boundaries for recap source-audio inserts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline_paths import OUTPUT_DIR


TRANSCRIPT_CACHE_DIR = OUTPUT_DIR / "transcript_cache"
END_BOUNDARY_TOLERANCE_SECONDS = 0.15
START_BOUNDARY_ALIGNMENT_TOLERANCE_SECONDS = 0.15
SHORT_UTTERANCE_SECONDS = 3.0


def _timed_entries(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in data.get(key, []):
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        entries.append({**raw, "start": start, "end": end})
    return sorted(entries, key=lambda entry: (entry["start"], entry["end"]))


def _source_matches(cache_source: str, source_video: str | Path) -> bool:
    requested = Path(str(source_video).strip())
    cached = Path(cache_source)
    if not requested.name or not cached.name:
        return False

    # A caller with a path expects an exact normalized path match. The recap
    # identity carries only a trusted filename, which is still sufficient to
    # select the matching cache under ShortsFactory's single input source.
    if requested.is_absolute() or requested.parent != Path("."):
        try:
            return requested.resolve().as_posix().casefold() == cached.resolve().as_posix().casefold()
        except OSError:
            return False
    return requested.name.casefold() == cached.name.casefold()


def _load_transcript_timing(
    source_video: str | Path | None,
    transcript_cache_dir: Path | None,
) -> tuple[dict[str, Any] | None, Path | None]:
    if source_video is None or not str(source_video).strip():
        return None, None

    cache_dir = transcript_cache_dir or TRANSCRIPT_CACHE_DIR
    if not cache_dir.is_dir():
        return None, None

    matches: list[tuple[int, int, str, dict[str, Any], Path]] = []
    for cache_path in cache_dir.glob("*.json"):
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        cache_source = str(
            data.get("source_video_path") or data.get("source_video") or ""
        ).strip()
        if not cache_source or not _source_matches(cache_source, source_video):
            continue

        segments = _timed_entries(data, "segments")
        if not segments:
            continue
        words = _timed_entries(data, "words")
        # Prefer the most detailed cache when the same trusted source has
        # multiple valid transcript runs. The final path tie-break is stable.
        matches.append((len(words), len(segments), cache_path.name, data, cache_path))

    if not matches:
        return None, None
    _, _, _, data, cache_path = max(matches, key=lambda item: item[:3])
    return data, cache_path


def _containing_entry(entries: list[dict[str, Any]], time: float) -> dict[str, Any] | None:
    for entry in entries:
        if entry["start"] <= time < entry["end"]:
            return entry
    return None


def _neighboring_words(
    words: list[dict[str, Any]],
    time: float,
    utterance: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    utterance_words = [
        word
        for word in words
        if utterance["start"] <= word["start"] and word["end"] <= utterance["end"]
    ]
    previous = next((word for word in reversed(utterance_words) if word["end"] <= time), None)
    following = next((word for word in utterance_words if word["start"] >= time), None)
    return previous, following


def resolve_source_audio_boundary(
    candidate: dict[str, Any],
    *,
    source_video: str | Path | None,
    transcript_cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Resolve only clearly clipped source-audio candidate boundaries.

    A missing, malformed, or wrong-source cache deliberately leaves the
    candidate untouched. The resolver never enters a following utterance: it
    can only complete the utterance already intersected by a candidate edge.
    """

    candidate_start = float(candidate["start"])
    candidate_end = float(candidate["end"])
    result = {
        "candidate_start": candidate_start,
        "candidate_end": candidate_end,
        "resolved_start": candidate_start,
        "resolved_end": candidate_end,
        "boundary_source": "candidate",
        "boundary_reason": "Original candidate preserved; no matching transcript timing.",
    }

    transcript, cache_path = _load_transcript_timing(source_video, transcript_cache_dir)
    if transcript is None or cache_path is None:
        return result

    segments = _timed_entries(transcript, "segments")
    words = _timed_entries(transcript, "words")
    start_utterance = _containing_entry(segments, candidate_start)
    end_utterance = _containing_entry(segments, candidate_end)
    reasons: list[str] = []
    used_word_timing = False

    # A start is intentionally conservative. Preserve starts that are close
    # to a word boundary, even inside a longer Whisper segment. Only recover
    # a preceding short utterance when the candidate is stranded in its
    # interior silence or begins inside a timed word.
    if start_utterance is not None and candidate_start > start_utterance["start"]:
        active_word = _containing_entry(words, candidate_start)
        if active_word is not None:
            result["resolved_start"] = start_utterance["start"]
            reasons.append("Candidate start intersects a timed word; restored its utterance start.")
            used_word_timing = True
        elif start_utterance["end"] - start_utterance["start"] <= SHORT_UTTERANCE_SECONDS:
            previous, following = _neighboring_words(words, candidate_start, start_utterance)
            previous_gap = candidate_start - previous["end"] if previous else float("inf")
            following_gap = following["start"] - candidate_start if following else float("inf")
            aligned_to_phrase_boundary = (
                0.0 <= previous_gap <= START_BOUNDARY_ALIGNMENT_TOLERANCE_SECONDS
                or 0.0 <= following_gap <= START_BOUNDARY_ALIGNMENT_TOLERANCE_SECONDS
            )
            if not aligned_to_phrase_boundary:
                result["resolved_start"] = start_utterance["start"]
                reasons.append(
                    "Candidate start falls unaligned inside a short timed utterance; restored its start."
                )
                used_word_timing = bool(previous or following)

    # An end inside a word is an audible cut even when Whisper's word timing
    # is close to the candidate end. Otherwise, use a small tolerance around
    # an utterance end to avoid turning harmless cache jitter into a rewrite.
    if end_utterance is not None and candidate_end < end_utterance["end"]:
        active_word = _containing_entry(words, candidate_end)
        if active_word is not None or (
            end_utterance["end"] - candidate_end > END_BOUNDARY_TOLERANCE_SECONDS
        ):
            result["resolved_end"] = end_utterance["end"]
            reasons.append("Candidate end intersects an unfinished timed utterance; extended to its end.")
            used_word_timing = used_word_timing or active_word is not None

    result["transcript_cache_path"] = str(cache_path)
    if reasons:
        result["boundary_source"] = (
            "transcript_cache_word_timing" if used_word_timing else "transcript_cache_segment_timing"
        )
        result["boundary_reason"] = " ".join(reasons)
    else:
        result["boundary_source"] = "transcript_cache"
        result["boundary_reason"] = "Candidate already aligns to a complete transcript boundary."
    return result
