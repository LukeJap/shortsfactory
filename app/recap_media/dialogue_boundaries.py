"""Conservative transcript-aware boundaries for recap source-audio inserts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline_paths import OUTPUT_DIR


TRANSCRIPT_CACHE_DIR = OUTPUT_DIR / "transcript_cache"
START_BOUNDARY_ALIGNMENT_TOLERANCE_SECONDS = 0.15
SHORT_UTTERANCE_SECONDS = 3.0
SPEECH_PRE_ROLL_SECONDS = 0.12
SPEECH_POST_TAIL_SECONDS = 0.20
MIN_ADEQUATE_POST_SPEECH_TAIL_SECONDS = 0.15


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


def _last_relevant_word(
    words: list[dict[str, Any]],
    start: float,
    end: float,
) -> dict[str, Any] | None:
    """Return the latest spoken word touched by a candidate's end region."""

    candidates = [
        word
        for word in words
        if word["end"] > start and word["start"] <= end
    ]
    return max(candidates, key=lambda word: (word["end"], word["start"]), default=None)


def _containing_utterance_for_word(
    segments: list[dict[str, Any]],
    word: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if word is None:
        return None
    midpoint = (word["start"] + word["end"]) / 2.0
    return _containing_entry(segments, midpoint)


def _bounded_range(
    start: float,
    end: float,
    source_duration_seconds: float | None,
) -> tuple[float, float]:
    start = max(0.0, start)
    end = max(start, end)
    if source_duration_seconds is None:
        return start, end
    try:
        source_duration = max(0.0, float(source_duration_seconds))
    except (TypeError, ValueError):
        return start, end
    return min(start, source_duration), min(end, source_duration)


def resolve_source_audio_boundary(
    candidate: dict[str, Any],
    *,
    source_video: str | Path | None,
    transcript_cache_dir: Path | None = None,
    source_duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Resolve source-moment boundaries to complete speech safely.

    Word timing is preferred whenever it is available: starts recover the
    initial attack of a word with a small pre-roll, while ends complete the
    current utterance and preserve a short natural tail. The resolver never
    reaches into a following utterance. Missing or wrong-source timing keeps
    the requested candidate range, apart from deterministic source bounds.
    """

    requested_start = float(candidate["start"])
    requested_end = float(candidate["end"])
    candidate_start, candidate_end = _bounded_range(
        requested_start,
        requested_end,
        source_duration_seconds,
    )
    result = {
        "candidate_start": requested_start,
        "candidate_end": requested_end,
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

    # Preserve an intentional internal phrase start unless the request cuts
    # through a spoken word. In that case, recover its attack and a tiny
    # pre-roll without pulling in the preceding sentence.
    if start_utterance is not None and candidate_start > start_utterance["start"]:
        active_word = _containing_entry(words, candidate_start)
        if active_word is not None:
            result["resolved_start"] = max(
                start_utterance["start"],
                active_word["start"] - SPEECH_PRE_ROLL_SECONDS,
            )
            reasons.append("Candidate start intersects a timed word; restored its word attack and pre-roll.")
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

    active_end_word = _containing_entry(words, candidate_end)
    final_word = _last_relevant_word(words, candidate_start, candidate_end)
    final_word_utterance = _containing_utterance_for_word(segments, final_word)
    if end_utterance is None:
        end_utterance = final_word_utterance

    # A requested end with adequate silence after its final spoken word is
    # already natural. Otherwise finish the current utterance first, then add
    # a short tail. We intentionally do not consult a following utterance.
    post_speech_tail = (
        candidate_end - final_word["end"]
        if final_word is not None and candidate_end >= final_word["end"]
        else 0.0
    )
    needs_speech_tail = final_word is not None and (
        active_end_word is not None
        or post_speech_tail < MIN_ADEQUATE_POST_SPEECH_TAIL_SECONDS
    )
    if needs_speech_tail:
        complete_through = candidate_end
        if end_utterance is not None and candidate_end < end_utterance["end"]:
            complete_through = end_utterance["end"]
            reasons.append("Candidate end intersects an unfinished timed utterance; extended to its end.")
        complete_through = max(complete_through, final_word["end"])
        result["resolved_end"] = max(
            result["resolved_end"],
            complete_through + SPEECH_POST_TAIL_SECONDS,
        )
        reasons.append("Added a natural post-speech tail after the final timed word.")
        used_word_timing = True
    elif end_utterance is not None and candidate_end < end_utterance["end"] and not words:
        # Segment timing remains the conservative fallback for legacy/weak
        # transcripts. Without word timing, completing the known utterance is
        # safer than clipping its final syllable.
        result["resolved_end"] = end_utterance["end"]
        reasons.append("Candidate end intersects an unfinished timed utterance; extended to its end.")

    result["resolved_start"], result["resolved_end"] = _bounded_range(
        float(result["resolved_start"]),
        float(result["resolved_end"]),
        source_duration_seconds,
    )

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
