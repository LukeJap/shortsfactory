"""
The "Find Best Clips" front door: transcribes/ingests a full source video
and uses a local Ollama LLM to identify and score candidate short-form
clip windows (hooks, self-contained moments, pacing). Largest standalone
script in the pipeline. Discovers whichever Ollama model is actually
installed rather than assuming a fixed model name, unlike the other
Ollama-calling script (semantic_edit.py) -- that's intentional, not an
inconsistency to fix.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import os
import random
import re
import shutil
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ollama_config import OLLAMA_HOST as DEFAULT_OLLAMA_HOST


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
TRANSCRIPT_EXTENSIONS = {".json", ".txt"}
REQUEST_TIMEOUT_SECONDS = 180
# Retired 2026-09-17: exact-60.0s windows on a fixed grid were the main
# cause of poor clip quality, because the good clip was usually not in the
# candidate set at all. Candidates are now variable-length, boundary-derived
# "beats" (see generate_valid_windows()) bounded to this range instead.
MIN_CLIP_SECONDS = 15.0
MAX_CLIP_SECONDS = 90.0
PREFERRED_MIN_CLIP_SECONDS = 25.0
PREFERRED_MAX_CLIP_SECONDS = 60.0
# Consecutive transcript segments merge into one beat when the silence gap
# between them is smaller than this; a gap this size or larger marks a beat
# boundary.
BEAT_GAP_SECONDS = 0.6
# Per candidate start beat, keep the qualifying end nearest each of these
# durations, so candidates spread evenly across the range and the count stays
# roughly linear in beat count instead of quadratic.
CANDIDATE_DURATION_TARGETS = (20.0, 35.0, 55.0, 80.0)
# A start beat needs at least this much silence before it (except the first
# beat of the source), which keeps the candidate set small and openings clean.
CANDIDATE_START_GAP_SECONDS = 1.5
# Two candidates whose start and end both land within this many seconds of
# each other are treated as the same moment; only the first (longest, since
# ends are generated longest-first per start) is kept.
CANDIDATE_DEDUPE_TOLERANCE_SECONDS = 3.0
# A mechanical boundary can still land mid-sentence in edge cases. Once a
# window is actually selected, nudge each edge to the nearest real
# transcript-segment boundary within this many seconds, so a clip starts/ends
# at a natural pause instead of cutting off a word -- an explicit, requested
# trade-off after live testing showed unsnapped cuts routinely landed
# mid-sentence.
CLIP_BOUNDARY_LEEWAY_SECONDS = 4.0
FIRST_STAGE_RANKING_BATCH_SIZE = 8
FIRST_STAGE_SHORTLIST_COUNT = 2
FIRST_STAGE_CONTEXT_MAX_CHARS = 2400
FINAL_RANKING_CONTEXT_MAX_CHARS = 6000
# Ollama defaults llama3.1:8b to a 4096-token context. A first-stage batch of
# 8 one-minute transcripts plus instructions plus FIRST_STAGE_CONTEXT_MAX_CHARS
# of source context can exceed that and get silently truncated. Set both
# explicitly rather than trust the server default.
RANKING_NUM_CTX = 8192
RANKING_NUM_PREDICT_BASE = 256
RANKING_NUM_PREDICT_PER_SELECTION = 120
# Detailed (final-pass) selections carry four sub-scores, a title and a hook.
RANKING_NUM_PREDICT_PER_DETAILED_SELECTION = 60
# Titling is a separate pass over only the chosen clips.
TITLING_NUM_PREDICT_PER_CLIP = 90
# The final ranking pass sees at most this many candidates (top by first-stage
# score), which keeps its prompt inside num_ctx=8192.
FINAL_POOL_MAX_CANDIDATES = 26
TITLING_CLIP_TEXT_MAX_CHARS = 700
# Ollama request timeout tracks the output budget at a conservative rate.
TIMEOUT_TOKENS_PER_SECOND = 5
TIMEOUT_SLACK_SECONDS = 60
# The final pass asks for this many times the requested clip count, so
# quota/overlap filtering has a real pool to backfill from.
FINAL_SELECTION_MULTIPLIER = 3
RUBRIC_FIELDS = ("hook_strength", "self_contained", "payoff", "peak")
# hook_strength is scored deterministically in Python (see hook_strength()
# below) rather than by the model, so the model's schema/rubric cover only
# these three -- RUBRIC_FIELDS itself stays four-long for persistence.
MODEL_RUBRIC_FIELDS = ("self_contained", "payoff", "peak")
RUBRIC_MAX_PER_FIELD = 25
RANKING_TITLE_MAX_CHARS = 60
RANKING_HOOK_MAX_CHARS = 80
# At most this fraction of returned clips may be longer than LONG_CLIP_SECONDS.
LONG_CLIP_SECONDS = 70.0
MAX_LONG_CLIP_FRACTION = 0.5
# Max selections whose start falls in the same equal-width source region.
MAX_SELECTIONS_PER_REGION = 2
# 8192 is a hard ceiling: a 16k context spills the KV cache past 8GB VRAM and
# generation drops from ~40 tok/s to ~7. Requests are sized to fit instead.
RANKING_NUM_CTX_MAX = RANKING_NUM_CTX
# One window per request: ~370 prompt tokens + ~30 output, so a small fixed
# context that never depends on pool size.
SINGLE_SCORE_NUM_CTX = 2048
SINGLE_SCORE_NUM_PREDICT = 64
SINGLE_SCORE_TEXT_MAX_CHARS = 1200
DEFAULT_CLIP_COUNT = 10
MAX_CLIP_COUNT = 10
# Length-variety quotas for select_distinct_ranked_windows.
MID_LONG_CLIP_SECONDS = 45.0
MIN_MID_LONG_CLIPS = 2
SHORT_CLIP_SECONDS = 30.0
TITLE_MIN_WORDS = 4
TIMESTAMP_MATCH_TOLERANCE_SECONDS = 0.05
# find_matching_window() re-identifies which mechanical valid_windows entry
# a candidate came from, purely to re-validate it in normalize_analysis()
# (the legacy non-clip-discovery-only path; the GUI always passes
# --clip-discovery-only and never exercises this). A candidate that went
# through the sentence-boundary leeway snap above can now be up to
# CLIP_BOUNDARY_LEEWAY_SECONDS away from that mechanical window on either
# edge -- the tight TIMESTAMP_MATCH_TOLERANCE_SECONDS (meant for comparing
# untouched mechanical values to each other) made every snapped candidate
# fail this lookup and get silently dropped.
WINDOW_MATCH_TOLERANCE_SECONDS = CLIP_BOUNDARY_LEEWAY_SECONDS + 0.5
RANKING_CONTEXT_MAX_CHARS = 12000

GENERIC_HOOK_PHRASES = (
    "you won't believe",
    "you wont believe",
    "this is crazy",
    "this is insane",
    "here's what happened",
    "heres what happened",
    "what happens next",
    "wait for it",
    "the dark truth about",
    "becomes the center of attention",
    "becomes the center of a short exchange",
    "clear setup and payoff",
    "interesting moment",
    "engaging conversation",
    "creates curiosity",
    "viewers will want to know",
    "something surprising happens",
    "in this episode",
    "in this clip",
)

EXPECTED_ANALYSIS: dict[str, Any] = {
    "main_topic": "",
    "people_subjects": [],
    "funniest_or_most_surprising_moment": {
    "timestamp": "ONE timestamp only in HH:MM:SS.mmm format, or empty string. Never include a window ID, brackets, ranges, duration, or extra text.",
    "description": "string",
    },
"strongest_emotional_moment": {
    "timestamp": "ONE timestamp only in HH:MM:SS.mmm format, or empty string. Never include a window ID, brackets, ranges, duration, or extra text.",
    "description": "string",
    },
    "strongest_curiosity_gap": "",
    "three_possible_shorts_hooks": [],
    "best_hook": "",
    "recommended_clip_start_timestamp": "",
    "recommended_clip_end_timestamp": "",
    "recommended_short_length_seconds": None,
    "why_selected_section_is_interesting": "",
    "proposed_original_narration_commentary_concept": "",
    "suggested_ending_payoff": "",
    "copyright_reused_content_risk": {
        "level": "",
        "copyrighted_source_footage_audio": "",
        "original_commentary": "",
        "transformative_editing": "",
        "reused_content_monetization_concerns": "",
        "notes": "",
    },
    "viral_potential_score": None,
    "candidate_clips": [],
    "selected_clip": {
        "start_timestamp": "",
        "end_timestamp": "",
        "duration_seconds": 0,
        "hook": "",
        "reason": "",
    },
    "no_viable_clip_reason": "",
}


def analysis_json_schema() -> dict[str, Any]:
    clip_schema = {
        "type": "object",
        "properties": {
            "start_timestamp": {"type": "string"},
            "end_timestamp": {"type": "string"},
            "duration_seconds": {"type": "number"},
            "hook": {"type": "string"},
            "description": {"type": "string"},
            "score": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": [
            "start_timestamp",
            "end_timestamp",
            "duration_seconds",
            "hook",
            "description",
            "score",
            "reason",
        ],
    }
    selected_clip_schema = {
        "type": "object",
        "properties": {
            "start_timestamp": {"type": "string"},
            "end_timestamp": {"type": "string"},
            "duration_seconds": {"type": "number"},
            "hook": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": [
            "start_timestamp",
            "end_timestamp",
            "duration_seconds",
            "hook",
            "reason",
        ],
    }

    return {
        "type": "object",
        "properties": {
            "main_topic": {"type": "string"},
            "people_subjects": {"type": "array", "items": {"type": "string"}},
            "funniest_or_most_surprising_moment": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["timestamp", "description"],
            },
            "strongest_emotional_moment": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["timestamp", "description"],
            },
            "strongest_curiosity_gap": {"type": "string"},
            "three_possible_shorts_hooks": {"type": "array", "items": {"type": "string"}},
            "best_hook": {"type": "string"},
            "recommended_clip_start_timestamp": {"type": "string"},
            "recommended_clip_end_timestamp": {"type": "string"},
            "recommended_short_length_seconds": {"type": "number"},
            "why_selected_section_is_interesting": {"type": "string"},
            "proposed_original_narration_commentary_concept": {"type": "string"},
            "suggested_ending_payoff": {"type": "string"},
            "copyright_reused_content_risk": {
                "type": "object",
                "properties": {
                    "level": {"type": "string"},
                    "copyrighted_source_footage_audio": {"type": "string"},
                    "original_commentary": {"type": "string"},
                    "transformative_editing": {"type": "string"},
                    "reused_content_monetization_concerns": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "level",
                    "copyrighted_source_footage_audio",
                    "original_commentary",
                    "transformative_editing",
                    "reused_content_monetization_concerns",
                    "notes",
                ],
            },
            "viral_potential_score": {"type": "integer"},
            "candidate_clips": {"type": "array", "items": clip_schema},
            "selected_clip": selected_clip_schema,
            "no_viable_clip_reason": {"type": "string"},
        },
        "required": list(EXPECTED_ANALYSIS.keys()),
    }


class TranscriptLoadError(Exception):
    """Raised when a transcript exists but cannot be used."""


class WindowRankingTimeout(RuntimeError):
    """A ranking request exceeded the shared Ollama request timeout."""


class WindowRankingShortfall(RuntimeError):
    """A ranking request returned fewer valid selections than the caller's minimum_count."""

    def __init__(self, request_label: str, requested: int, returned: int) -> None:
        self.request_label = request_label
        self.requested = requested
        self.returned = returned
        super().__init__(
            f"Ollama returned {returned} valid selections for {request_label}; "
            f"expected {requested}."
        )


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptData:
    text: str
    segments: list[TranscriptSegment]


@dataclass(frozen=True)
class CandidateWindow:
    start: float
    end: float
    text: str

    @property
    def duration_seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Beat:
    """One merged run of transcript segments with no internal silence gap
    large enough to count as a beat boundary (see BEAT_GAP_SECONDS)."""

    start: float
    end: float
    text: str
    ends_with_terminal_punctuation: bool


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def log(message: str) -> None:
    print(message, flush=True)


def normalize_ollama_host(value: str | None) -> str:
    host = (value or DEFAULT_OLLAMA_HOST).strip().rstrip("/")
    if not host:
        return DEFAULT_OLLAMA_HOST

    parsed = urlparse(host)
    if not parsed.scheme:
        host = f"http://{host}"

    return host.rstrip("/")


def request_json(url: str, payload: dict[str, Any] | None = None, timeout: int = 10) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")

    return json.loads(body)


def get_ollama_models(host: str) -> tuple[list[str], str | None]:
    try:
        response = request_json(f"{host}/api/tags", timeout=5)
    except HTTPError as exc:
        return [], f"Ollama responded with HTTP {exc.code} at {host}."
    except URLError as exc:
        return [], f"Could not reach Ollama at {host}: {exc.reason}"
    except TimeoutError:
        return [], f"Timed out while connecting to Ollama at {host}."
    except json.JSONDecodeError:
        return [], f"Ollama at {host} returned a response that was not valid JSON."
    except OSError as exc:
        return [], f"Could not connect to Ollama at {host}: {exc}"

    models = response.get("models", [])
    names = [str(model.get("name", "")).strip() for model in models if model.get("name")]
    return names, None


def select_model(models: list[str]) -> tuple[str | None, str | None]:
    configured = os.environ.get("OLLAMA_MODEL", "").strip()
    if configured:
        if configured in models:
            return configured, None
        return None, (
            f"OLLAMA_MODEL is set to '{configured}', but that model is not installed. "
            "Run 'ollama list' to see installed models, or pull the model first."
        )

    preferred_prefixes = (
        "llama3.1",
        "llama3.2",
        "llama3",
        "qwen2.5",
        "mistral",
        "gemma3",
        "gemma2",
        "phi4",
    )
    for prefix in preferred_prefixes:
        for name in models:
            if name == prefix or name.startswith(f"{prefix}:"):
                return name, None

    if models:
        return models[0], None

    return None, "Ollama is running, but no local models are installed."


def associated_transcript(video: Path, transcript: Path) -> bool:
    video_stem = video.stem.lower()
    transcript_stem = transcript.stem.lower()
    return (
        transcript_stem == video_stem
        or transcript_stem.startswith(f"{video_stem}.")
        or transcript_stem.startswith(f"{video_stem}-")
        or transcript_stem.startswith(f"{video_stem}_")
    )


def transcript_search_dirs(root: Path) -> list[Path]:
    possible_dirs = [
        root / "input",
        root,
        root / "data",
        root / "output",
        root / "output" / "transcripts",
    ]
    return [path for path in possible_dirs if path.exists()]


def find_candidate_transcripts(root: Path) -> list[tuple[Path, Path]]:
    input_dir = root / "input"
    if not input_dir.exists():
        return []

    videos = [
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not videos:
        return []

    candidates: list[tuple[Path, Path]] = []
    seen: set[Path] = set()

    for search_dir in transcript_search_dirs(root):
        iterator = search_dir.glob("*") if search_dir == root else search_dir.rglob("*")
        for transcript in iterator:
            if not transcript.is_file() or transcript.suffix.lower() not in TRANSCRIPT_EXTENSIONS:
                continue

            resolved = transcript.resolve()
            if resolved in seen:
                continue

            for video in videos:
                if associated_transcript(video, transcript):
                    seen.add(resolved)
                    candidates.append((video, transcript))
                    break

    candidates.sort(key=lambda pair: pair[1].stat().st_mtime, reverse=True)
    return candidates


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any, prefer: str = "first") -> float | None:
    if isinstance(value, int | float):
        return max(0.0, float(value))

    text = str(value or "").strip()
    if not text:
        return None

    text = text.replace(",", ".")
    timestamp_matches = re.findall(
        r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d{1,3})?",
        text,
    )
    if timestamp_matches:
        text = timestamp_matches[-1] if prefer == "last" else timestamp_matches[0]

    parts = text.split(":")
    try:
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return max(0.0, hours * 3600 + minutes * 60 + seconds)
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return max(0.0, minutes * 60 + seconds)
        if len(parts) == 1:
            return max(0.0, float(parts[0]))
    except ValueError:
        return None

    return None


def format_timestamp(seconds: Any) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""

    if value < 0:
        value = 0

    whole_seconds = int(value)
    milliseconds = int(round((value - whole_seconds) * 1000))
    if milliseconds == 1000:
        whole_seconds += 1
        milliseconds = 0

    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    seconds_only = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds_only:02d}.{milliseconds:03d}"


TIMESTAMP_TOKEN = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[\.,]\d{1,3})?"
TEXT_SEGMENT_RE = re.compile(
    rf"^\s*\[?\s*({TIMESTAMP_TOKEN})\s*(?:-->|-|to)\s*({TIMESTAMP_TOKEN})\s*\]?\s*(.*)$",
    re.IGNORECASE,
)


def read_json_transcript(path: Path) -> TranscriptData:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise TranscriptLoadError(f"Malformed JSON transcript: {exc}") from exc
    except OSError as exc:
        raise TranscriptLoadError(f"Could not read transcript: {exc}") from exc

    if not isinstance(data, dict):
        raise TranscriptLoadError("JSON transcript must be an object.")

    segments = data.get("segments")
    if isinstance(segments, list) and segments:
        lines: list[str] = []
        parsed_segments: list[TranscriptSegment] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue

            text = str(segment.get("text", "")).strip()
            if not text:
                continue

            start_seconds = as_float(segment.get("start"))
            end_seconds = as_float(segment.get("end"))
            start = format_timestamp(start_seconds)
            end = format_timestamp(end_seconds)
            if start and end:
                lines.append(f"[{start} - {end}] {text}")
                if start_seconds is not None and end_seconds is not None and end_seconds > start_seconds:
                    parsed_segments.append(TranscriptSegment(start_seconds, end_seconds, text))
            elif start:
                lines.append(f"[{start}] {text}")
            else:
                lines.append(text)

        if lines:
            return TranscriptData("\n".join(lines), parsed_segments)

    text = str(data.get("text", "")).strip()
    if text:
        return TranscriptData(text, [])

    raise TranscriptLoadError("JSON transcript did not contain usable text or segments.")


def parse_text_segments(text: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for line in text.splitlines():
        match = TEXT_SEGMENT_RE.match(line)
        if not match:
            continue

        start = parse_timestamp(match.group(1))
        end = parse_timestamp(match.group(2))
        segment_text = match.group(3).strip()
        if start is None or end is None or end <= start or not segment_text:
            continue

        segments.append(TranscriptSegment(start, end, segment_text))

    return segments


def read_text_transcript(path: Path) -> TranscriptData:
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise TranscriptLoadError(f"Transcript is not valid UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise TranscriptLoadError(f"Could not read transcript: {exc}") from exc

    if not text:
        raise TranscriptLoadError("Transcript file is empty.")

    segments = parse_text_segments(text)
    if segments:
        normalized_lines = [
            f"[{format_timestamp(segment.start)} - {format_timestamp(segment.end)}] {segment.text}"
            for segment in segments
        ]
        return TranscriptData("\n".join(normalized_lines), segments)

    return TranscriptData(text, [])


def load_transcript(path: Path) -> TranscriptData:
    if path.suffix.lower() == ".json":
        return read_json_transcript(path)
    if path.suffix.lower() == ".txt":
        return read_text_transcript(path)
    raise TranscriptLoadError(f"Unsupported transcript type: {path.suffix}")


def load_newest_transcript(root: Path) -> tuple[Path, Path, TranscriptData] | None:
    candidates = find_candidate_transcripts(root)
    if not candidates:
        return None

    for video, transcript in candidates:
        log(f"Reading transcript: {transcript}")
        try:
            return video, transcript, load_transcript(transcript)
        except TranscriptLoadError as exc:
            log(f"Skipping unusable transcript '{transcript.name}': {exc}")

    return None


def ends_with_terminal_punctuation(text: str) -> bool:
    """True if text ends a sentence, ignoring trailing quote/bracket marks."""
    stripped = text.strip().rstrip("\"'”’)]")
    return bool(stripped) and stripped[-1] in ".!?"


def first_spoken_line(text: str) -> str:
    """The literal first sentence of a window's run-on transcript text.

    Window text is one concatenated paragraph with no line breaks, so
    "judge only the first line" is otherwise undefined for the model -- it
    reads ahead into the rest of the clip and scores the opening beat
    instead of the opening utterance. Isolating it here, rather than
    leaving the model to find it, is what makes single-word fragments
    ("Ah!", "Fuse!") stop scoring like real hooks.
    """
    match = re.search(r"[.!?]", text)
    return text[: match.end()].strip() if match else text.strip()


FILLER_OPENERS = {
    "oh", "ah", "uh", "um", "hey", "hi", "hello", "yeah", "yes", "no", "wow",
    "huh", "well", "so", "okay", "ok", "hmm", "aw", "ugh", "whoa",
    "good morning", "good afternoon", "good evening",
}


def hook_strength(first_line: str) -> int:
    """Deterministic 0-25 score for a clip's opening utterance.

    The model was asked to judge this three different ways and returned a
    constant 20 every time -- length, question form and filler openers are
    mechanical checks, not judgment calls, so Python scores them instead.
    """
    line = (first_line or "").strip()
    if not line:
        return 0
    words = re.findall(r"[A-Za-z']+", line)
    n = len(words)
    lowered = " ".join(w.lower() for w in words)
    first = words[0].lower() if words else ""

    if n <= 2:                                   # "Oh!", "Fuse!"
        return 2
    if first in FILLER_OPENERS and n <= 5:       # "Yeah, that checks out."
        return 4
    if lowered in FILLER_OPENERS:
        return 2
    if line.rstrip().endswith("?") and n >= 4:   # a question is an open loop
        return 22 if n >= 6 else 20
    if n <= 5:
        return 8
    if n <= 10:
        return 14
    return 17


def build_beats(
    segments: list[TranscriptSegment],
    gap_seconds: float = BEAT_GAP_SECONDS,
) -> list[Beat]:
    """Merge consecutive transcript segments into beats.

    A beat boundary falls where the silence gap between consecutive segments
    is >= gap_seconds; segments closer together than that merge into one
    beat. Beats are the atomic unit candidate clips are built from -- a real
    content boundary, not a mechanical timestamp.
    """
    if not segments:
        return []

    ordered = sorted(segments, key=lambda segment: segment.start)
    beats: list[Beat] = []
    current_start = ordered[0].start
    current_end = ordered[0].end
    current_texts = [ordered[0].text]

    def flush() -> None:
        text = " ".join(current_texts).strip()
        beats.append(
            Beat(
                start=current_start,
                end=current_end,
                text=text,
                ends_with_terminal_punctuation=ends_with_terminal_punctuation(text),
            )
        )

    for segment in ordered[1:]:
        # Subtract a small float-noise tolerance rather than compare the raw
        # gap directly -- real transcript timestamps can make an intended
        # exact-gap_seconds boundary land a hair under it (e.g. 4.6 - 4.0 ==
        # 0.5999999999999996 in floating point).
        if segment.start - current_end >= gap_seconds - TIMESTAMP_MATCH_TOLERANCE_SECONDS:
            flush()
            current_start = segment.start
            current_texts = [segment.text]
        else:
            current_texts.append(segment.text)
        current_end = max(current_end, segment.end)

    flush()
    return beats


def generate_valid_windows(segments: list[TranscriptSegment]) -> list[CandidateWindow]:
    """Build variable-length candidates anchored to real content boundaries.

    A candidate starts on a beat that opens a sentence (the previous beat
    ends on terminal punctuation, or there is no previous beat) and ends on
    a beat that itself ends on terminal punctuation, so a clip is never
    proposed starting or ending mid-clause. Duration is bounded to
    [MIN_CLIP_SECONDS, MAX_CLIP_SECONDS] rather than pinned to a fixed
    length -- a good Short starts on the setup line and ends on the payoff
    line, and that length varies scene to scene.
    """
    beats = build_beats(segments)
    if not beats:
        return []

    raw_candidates: list[CandidateWindow] = []
    for start_index, start_beat in enumerate(beats):
        if start_index > 0:
            previous_beat = beats[start_index - 1]
            if not previous_beat.ends_with_terminal_punctuation:
                continue
            # A clip opening after a real pause opens cleanly; one opening
            # right after the previous line is a mid-scene splice.
            if (
                start_beat.start - previous_beat.end
                < CANDIDATE_START_GAP_SECONDS - TIMESTAMP_MATCH_TOLERANCE_SECONDS
            ):
                continue

        qualifying_ends: list[tuple[float, int]] = []
        for end_index in range(start_index, len(beats)):
            end_beat = beats[end_index]
            duration = end_beat.end - start_beat.start
            if duration > MAX_CLIP_SECONDS:
                break
            if duration < MIN_CLIP_SECONDS:
                continue
            if not end_beat.ends_with_terminal_punctuation:
                continue
            qualifying_ends.append((duration, end_index))

        # Pick the qualifying end nearest each duration target so candidates
        # spread across the whole range instead of clustering at the maximum.
        chosen_end_indices: set[int] = set()
        if qualifying_ends:
            for target in CANDIDATE_DURATION_TARGETS:
                _nearest_duration, nearest_index = min(
                    qualifying_ends, key=lambda item: abs(item[0] - target)
                )
                chosen_end_indices.add(nearest_index)
        for end_index in sorted(chosen_end_indices, reverse=True):
            text = " ".join(beat.text for beat in beats[start_index : end_index + 1]).strip()
            raw_candidates.append(
                CandidateWindow(start=start_beat.start, end=beats[end_index].end, text=text)
            )

    windows: list[CandidateWindow] = []
    for candidate in raw_candidates:
        if any(
            abs(candidate.start - kept.start) <= CANDIDATE_DEDUPE_TOLERANCE_SECONDS
            and abs(candidate.end - kept.end) <= CANDIDATE_DEDUPE_TOLERANCE_SECONDS
            for kept in windows
        ):
            continue
        windows.append(candidate)

    windows.sort(key=lambda window: (window.start, window.end))
    return windows

def truncate_for_prompt(text: str, max_chars: int = 320) -> str:
    clean_text = " ".join(text.split())
    if len(clean_text) <= max_chars:
        return clean_text
    return clean_text[: max_chars - 3].rstrip() + "..."


def format_valid_windows_for_prompt(
    windows: list[CandidateWindow],
    excluded_window_ids: set[str] | None = None,
) -> str:
    if not windows:
        return (
            "No valid candidate clip windows were found. "
            "Return candidate_clips as an empty array and selected_clip with empty timestamps."
        )

    excluded = excluded_window_ids or set()
    lines = []
    for index, window in enumerate(windows, start=1):
        if f"W{index:03d}" in excluded:
            continue
        lines.append(
            "W{index:03d} [{start} - {end}] {duration:.3f}s: {text}".format(
                index=index,
                start=format_timestamp(window.start),
                end=format_timestamp(window.end),
                duration=window.duration_seconds,
                text=truncate_for_prompt(window.text),
            )
        )

    if lines:
        return "\n".join(lines)
    return "No eligible timestamp-aligned windows remain."


def transcript_context_for_window_ranking(
    transcript: TranscriptData,
    max_chars: int = RANKING_CONTEXT_MAX_CHARS,
) -> str:
    """Keep enough of a long source to identify its structural moments."""

    text = transcript.text.strip()
    if len(text) <= max_chars:
        return text

    section_size = max(1, max_chars // 3)
    middle_start = max(0, (len(text) - section_size) // 2)
    sections = (
        ("SOURCE OPENING", text[:section_size]),
        ("SOURCE MIDDLE", text[middle_start : middle_start + section_size]),
        ("SOURCE ENDING", text[-section_size:]),
    )
    return "\n\n".join(
        f"[{label}]\n{section.strip()}"
        for label, section in sections
    )


def load_analyzer_instructions(root: Path) -> str:
    prompt_path = root / "prompts" / "analyzer.md"
    try:
        return prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Could not read analyzer prompt at {prompt_path}: {exc}") from exc


def trim_transcript_for_prompt(transcript: str, max_chars: int = 30000) -> tuple[str, bool]:
    if len(transcript) <= max_chars:
        return transcript, False

    return transcript[:max_chars].rstrip(), True


def build_prompt(
    instructions: str,
    video: Path,
    transcript_path: Path,
    transcript: TranscriptData,
    valid_windows: list[CandidateWindow],
    ranked_windows: list[tuple[CandidateWindow, int, str]] | None = None,
    validation_feedback: list[str] | None = None,
) -> str:
    trimmed_transcript, was_trimmed = trim_transcript_for_prompt(transcript.text)
    trim_note = (
        "The transcript was trimmed for model context. Analyze only the provided excerpt."
        if was_trimmed
        else "The full transcript is provided."
    )

    schema = {
        "main_topic": "string",
        "people_subjects": ["string"],
        "funniest_or_most_surprising_moment": {
            "timestamp": "HH:MM:SS.mmm or empty string",
            "description": "string",
        },
        "strongest_emotional_moment": {
            "timestamp": "HH:MM:SS.mmm or empty string",
            "description": "string",
        },
        "strongest_curiosity_gap": "string",
        "three_possible_shorts_hooks": ["string", "string", "string"],
        "best_hook": "string",
        "recommended_clip_start_timestamp": "HH:MM:SS.mmm",
        "recommended_clip_end_timestamp": "HH:MM:SS.mmm",
        "recommended_short_length_seconds": "exactly 60, or 0 if no viable clip",
        "why_selected_section_is_interesting": "string",
        "proposed_original_narration_commentary_concept": "string",
        "suggested_ending_payoff": "string",
        "copyright_reused_content_risk": {
            "level": "low, medium, or high",
            "copyrighted_source_footage_audio": "string",
            "original_commentary": "string",
            "transformative_editing": "string",
            "reused_content_monetization_concerns": "string",
            "notes": "string",
        },
        "viral_potential_score": "integer from 0 to 100",
        "candidate_clips": [
            {
                "start_timestamp": "HH:MM:SS.mmm from a valid window",
                "end_timestamp": "HH:MM:SS.mmm from the same valid window",
                "duration_seconds": "exactly 60",
                "hook": "specific non-clickbait hook",
                "description": "string",
                "score": "integer from 0 to 100",
                "reason": "string",
            }
        ],
        "selected_clip": {
            "start_timestamp": "HH:MM:SS.mmm from one candidate clip, or empty string",
            "end_timestamp": "HH:MM:SS.mmm from the same candidate clip, or empty string",
            "duration_seconds": "exactly 60, or 0",
            "hook": "specific non-clickbait hook, or empty string",
            "reason": "string",
        },
        "no_viable_clip_reason": "string, only populated if selected_clip is empty",
    }

    feedback_section = ""
    if validation_feedback:
        feedback_section = "\n".join(f"- {item}" for item in validation_feedback)

    ranked_windows_section = ""
    if ranked_windows:
        ranked_sections = [
            "The Shorts ranker has already selected the strongest candidate windows.",
            "Treat these ranked windows as the authoritative candidates for clip selection.",
            "Do not invent different clip timestamps.",
            "",
        ]

        for rank, (window, score, reason) in enumerate(ranked_windows[:6], start=1):
            ranked_sections.extend(
                [
                    f"RANKED CANDIDATE {rank}: W{valid_windows.index(window) + 1:03d}",
                    f"Start: {format_timestamp(window.start)}",
                    f"End: {format_timestamp(window.end)}",
                    f"Duration: {round_duration(window.duration_seconds)} seconds",
                    f"Ranker score: {score}/100",
                    f"Ranker reason: {reason}",
                    "Transcript:",
                    window.text,
                    "",
                ]
            )

        ranked_windows_section = "\n".join(ranked_sections)

    prompt_sections = [
        instructions,
        ranked_windows_section,
        (
            "ABSOLUTE GROUNDING RULES:\n"
            "1. Analyze each ranked candidate window using ONLY the transcript text "
            "shown directly under that candidate.\n"
            "2. Do NOT use information from another part of the transcript to describe "
            "a candidate window.\n"
            "3. A hook, description, reason, or explanation MUST be directly supported "
            "by words or events contained in that candidate's transcript.\n"
            "4. Do NOT move a later event into an earlier candidate window.\n"
            "5. If a candidate transcript does not contain enough information to support "
            "a claim, do not make that claim.\n"
            "6. The selected clip must make sense as a standalone clip based only on "
            "the transcript inside that clip.\n"
            "7. Never invent relationships, identities, motivations, events, or context.\n"
            "8. The transcript may contain transcription errors. Correct obvious "
            "speech-to-text errors only when the surrounding words make the intended "
            "meaning clear; otherwise preserve uncertainty."
        ),
        "Return exactly one JSON object with this schema:",
        json.dumps(schema, indent=2),
                "Critical timing constraints:",
        (
            "Every candidate and selected clip must be exactly 60.0 seconds. "
            "Use only a valid one-minute window that contains a complete setup, key moment, and payoff."
        ),
        "Moment timestamp rules:",
        (
            "For funniest_or_most_surprising_moment.timestamp and "
            "strongest_emotional_moment.timestamp, return exactly ONE timestamp "
            "such as 00:00:54.320. "
            "Never return a window ID such as W021. "
            "Never return a timestamp range, brackets, duration, or explanatory text. "
            "If you cannot determine the exact timestamp, return an empty string."
        ),
        "Candidate grounding rules:",
        (
            "Every candidate_clip must be grounded ONLY in the transcript contained "
            "inside that candidate's own ranked window. "
            "Do not combine facts, people, events, jokes, or ideas from different windows "
            "when writing a candidate's hook, description, or reason. "
            "Do not invent relationships, events, motivations, or facts that are not stated "
            "or clearly implied by that window. "
            "The hook must describe the actual subject or moment contained in the window. "
            "The reason must explain why that specific window is interesting, surprising, "
            "funny, emotional, or curiosity-driven. "
            "If a window does not contain enough context to support a specific hook, use a "
            "more literal hook rather than inventing context."
        ),
        "Timestamp rules:",
        (
            "Candidate and selected clip timestamps must exactly match the start and end "
            "timestamps of one of the ranked candidate windows. "
            "Do not create new timestamps or combine portions of different windows."
        ),
        "You may only use start/end timestamps from these valid candidate windows:",
        format_valid_windows_for_prompt(valid_windows),
        "Window IDs are labels for comparison only. Do not put W001, W002, or any other window ID in timestamp fields.",
        (
            "candidate_clips must contain at least 3 unique windows when available. "
            "Do not duplicate the same start/end pair. start_timestamp and end_timestamp must each be one timestamp, "
            "not a range string."
        ),
        "If none of the valid windows form a good Short, leave selected_clip empty and explain why in no_viable_clip_reason.",
        (
            "Avoid generic hooks such as 'You won't believe...', 'This is crazy...', "
            "'Here's what happened...', 'The dark truth about...', 'becomes the center of attention', "
            "'clear setup and payoff', or 'engaging conversation'. Hooks must name the actual subject "
            "or quote a concrete transcript detail."
        ),
    ]
    if feedback_section:
        prompt_sections.extend(["Previous response validation issues:", feedback_section])
    prompt_sections.extend(
        [
            f"Source video filename: {video.name}",
            f"Transcript filename: {transcript_path.name}",
            trim_note,
            "Transcript:",
            trimmed_transcript,
        ]
    )
    return "\n\n".join(prompt_sections)


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object.")

    return parsed

def window_ranking_response_count(
    valid_windows: list[CandidateWindow],
    target_clip_count: int,
    excluded_window_ids: set[str] | None = None,
    response_count_override: int | None = None,
) -> int:
    excluded = excluded_window_ids or set()
    available_window_count = max(0, len(valid_windows) - len(excluded))
    requested_count = (
        response_count_override
        if response_count_override is not None
        else target_clip_count
    )
    return min(available_window_count, max(0, int(requested_count)))


def window_ranking_json_schema(
    window_ids: list[str],
    selection_count: int,
    detailed: bool = False,
) -> dict[str, Any]:
    if detailed:
        properties: dict[str, Any] = {
            "window_id": {"type": "string", "enum": window_ids},
        }
        for field in RUBRIC_FIELDS:
            properties[field] = {
                "type": "integer",
                "minimum": 0,
                "maximum": RUBRIC_MAX_PER_FIELD,
            }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["selections"],
            "properties": {
                "selections": {
                    "type": "array",
                    "minItems": selection_count,
                    "maxItems": selection_count,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(properties),
                        "properties": properties,
                    },
                }
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["selections"],
        "properties": {
            "selections": {
                "type": "array",
                "minItems": selection_count,
                "maxItems": selection_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["window_id", "score", "reason"],
                    "properties": {
                        "window_id": {"type": "string", "enum": window_ids},
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "reason": {"type": "string", "maxLength": 160},
                    },
                },
            }
        },
    }


class RankedReason(str):
    """A ranked window's reason text, plus the title/hook/sub-scores the
    detailed ranker returns. Subclassing str keeps the existing
    (window, score, reason) tuple shape working for every other caller."""

    title: str = ""
    hook: str = ""
    sub_scores: dict[str, int]


def _shares_content_word(candidate: str, source_text: str) -> bool:
    source_words = set(re.findall(r"[a-z0-9']{4,}", source_text.lower()))
    return any(word in source_words for word in re.findall(r"[a-z0-9']{4,}", candidate.lower()))


def grounded_ranker_text(candidate: str, window: CandidateWindow) -> str:
    """Return the model's title/hook only if it is specific and grounded in the
    window's own text; otherwise an empty string so the caller regenerates it."""
    cleaned = " ".join(str(candidate or "").split())
    if not cleaned or is_generic_hook(cleaned):
        return ""
    if not _shares_content_word(cleaned, window.text):
        return ""
    return cleaned


class WindowRankingResponse(dict):
    """Parsed ranker JSON, plus the raw model text for debugging.

    Subclassing dict keeps `isinstance(result, dict)` and
    `result == {"selections": [...]}` working everywhere the parsed object
    is already used; only callers that want the raw text for logging need
    to know about the extra `raw_text` attribute.
    """

    raw_text: str = ""


def ranking_num_predict(selection_count: int, detailed: bool = False) -> int:
    per_selection = (
        RANKING_NUM_PREDICT_PER_DETAILED_SELECTION
        if detailed
        else RANKING_NUM_PREDICT_PER_SELECTION
    )
    return RANKING_NUM_PREDICT_BASE + per_selection * max(0, selection_count)


def ranking_timeout(num_predict: int) -> int:
    """Timeout that scales with the output budget; never below the base."""
    return max(
        REQUEST_TIMEOUT_SECONDS,
        int(num_predict / TIMEOUT_TOKENS_PER_SECOND) + TIMEOUT_SLACK_SECONDS,
    )


def ranking_num_ctx(prompt_chars: int, num_predict: int) -> int:
    """Context must cover the prompt *and* the output, or Ollama silently
    truncates the prompt. Floor at RANKING_NUM_CTX, cap at RANKING_NUM_CTX_MAX."""
    needed = prompt_chars // 3 + num_predict + 512
    size = RANKING_NUM_CTX
    while size < needed and size < RANKING_NUM_CTX_MAX:
        size *= 2
    return min(size, RANKING_NUM_CTX_MAX)


def estimated_request_tokens(prompt_chars: int, num_predict: int) -> int:
    """Same estimate ranking_num_ctx() sizes the context from."""
    return prompt_chars // 3 + num_predict + 512


def shrink_pool_to_context(
    transcript: TranscriptData,
    shortlist: list[tuple[CandidateWindow, int, str]],
    target_clip_count: int,
) -> list[tuple[CandidateWindow, int, str]]:
    """Drop the weakest candidates until the final request fits RANKING_NUM_CTX.

    Shrinks the pool rather than ever stepping num_ctx past 8192.
    """
    pool = list(shortlist)
    while True:
        windows = [window for window, _score, _reason in sorted(pool, key=lambda c: c[0].start)]
        selection_count = min(len(windows), target_clip_count * FINAL_SELECTION_MULTIPLIER)
        prompt = build_window_ranking_prompt(
            transcript,
            windows,
            target_clip_count=selection_count,
            context_max_chars=FINAL_RANKING_CONTEXT_MAX_CHARS,
            detailed=True,
        )
        estimate = estimated_request_tokens(
            len(prompt), ranking_num_predict(selection_count, True)
        )
        if estimate <= RANKING_NUM_CTX or len(pool) <= 1:
            log(
                f"Final pool token estimate: {estimate} of {RANKING_NUM_CTX} "
                f"with {len(pool)} candidates."
            )
            return pool
        weakest = min(range(len(pool)), key=lambda index: pool[index][1])
        pool.pop(weakest)


def salvage_selections(text: str) -> dict[str, Any] | None:
    """Recover every complete object from a truncated "selections" array.

    Scans with brace depth, tracking string state and escapes. Returns None
    when no complete selection can be recovered.
    """
    key = text.find('"selections"')
    if key == -1:
        return None
    bracket = text.find("[", key)
    if bracket == -1:
        return None

    selections: list[Any] = []
    depth = 0
    in_string = False
    escaped = False
    object_start = -1
    for index in range(bracket + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                object_start = index
            depth += 1
        elif char == "}":
            if depth == 0:
                break
            depth -= 1
            if depth == 0:
                try:
                    selections.append(json.loads(text[object_start : index + 1]))
                except json.JSONDecodeError:
                    break
        elif char == "]" and depth == 0:
            break

    if not selections:
        return None
    return {"selections": selections}


def post_for_selections(
    host: str,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    num_predict: int,
    selection_count: int,
    purpose: str,
    timeout_message: str = "",
    num_ctx: int | None = None,
) -> "WindowRankingResponse":
    """POST one schema-constrained request whose answer is a "selections" array.

    Shared by the ranking and titling passes. Truncated arrays are salvaged
    rather than failing the run.
    """
    if num_ctx is None:
        num_ctx = ranking_num_ctx(len(prompt), num_predict)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "top_p": 0.9,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }

    try:
        response = request_json(
            f"{host}/api/generate",
            payload=payload,
            timeout=ranking_timeout(num_predict),
        )
    except HTTPError as exc:
        raise RuntimeError(
            f"Ollama {purpose} request failed with HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Ollama stopped responding: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise WindowRankingTimeout(
            timeout_message
            or f"Ollama took too long for the {purpose} request. Please try again."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ollama returned invalid JSON for the {purpose} request."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Ollama {purpose} request failed: {exc}"
        ) from exc

    response_text = str(response.get("response", "")).strip()

    if not response_text:
        raise RuntimeError(
            f"Ollama returned an empty {purpose} response."
        )

    try:
        parsed = extract_json_object(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        parsed = salvage_selections(response_text)
        if parsed is None:
            raise RuntimeError(
                f"The {purpose} response was not valid JSON: {exc} "
                f"Raw response (first 500 chars): {response_text[:500]!r}"
            ) from exc
        log(
            f"WARNING: {purpose} response was truncated or malformed ({exc}); "
            f"salvaged {len(parsed['selections'])} of {selection_count} requested selections."
        )

    result = WindowRankingResponse(parsed)
    result.raw_text = response_text
    return result



def call_ollama_window_ranker(
    host: str,
    model: str,
    prompt: str,
    *,
    window_ids: list[str],
    selection_count: int,
    detailed: bool = False,
) -> dict[str, Any]:
    return post_for_selections(
        host,
        model,
        prompt,
        window_ranking_json_schema(window_ids, selection_count, detailed),
        num_predict=ranking_num_predict(selection_count, detailed),
        selection_count=selection_count,
        purpose="window-ranking",
        timeout_message="Ollama took too long to rank the clip candidates. Please try again.",
    )


def titling_json_schema(window_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["selections"],
        "properties": {
            "selections": {
                "type": "array",
                "minItems": len(window_ids),
                "maxItems": len(window_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["window_id", "title", "hook"],
                    "properties": {
                        "window_id": {"type": "string", "enum": window_ids},
                        "title": {"type": "string", "maxLength": RANKING_TITLE_MAX_CHARS},
                        "hook": {"type": "string", "maxLength": RANKING_HOOK_MAX_CHARS},
                    },
                },
            }
        },
    }


def call_ollama_clip_titler(
    host: str,
    model: str,
    prompt: str,
    *,
    window_ids: list[str],
) -> dict[str, Any]:
    return post_for_selections(
        host,
        model,
        prompt,
        titling_json_schema(window_ids),
        num_predict=RANKING_NUM_PREDICT_BASE
        + TITLING_NUM_PREDICT_PER_CLIP * len(window_ids),
        selection_count=len(window_ids),
        purpose="clip-titling",
    )


def _normalize_for_match(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", str(text).lower()).split())


def is_quote_of_clip(hook: str, window: CandidateWindow) -> bool:
    """A hook lifted verbatim from the clip's transcript is a quote, not a hook."""
    normalized = _normalize_for_match(hook)
    return bool(normalized) and normalized in _normalize_for_match(window.text)


def is_valid_title(title: str) -> bool:
    cleaned = title.strip()
    return len(cleaned.split()) >= TITLE_MIN_WORDS and not cleaned.endswith(".")


def build_titling_prompt(windows: list[CandidateWindow], *, retry: bool = False) -> str:
    clips = "\n\n".join(
        f"W{index:03d} ({window.duration_seconds:.0f}s): "
        f"{truncate_for_prompt(window.text, TITLING_CLIP_TEXT_MAX_CHARS)}"
        for index, window in enumerate(windows, start=1)
    )
    retry_note = (
        "\nYour previous answer quoted the clip or was too short. Rewrite it: "
        "no words copied in a row from the clip.\n"
        if retry
        else ""
    )
    return f"""
You are naming short-form video clips. For each clip below write:
- title: {TITLE_MIN_WORDS}-9 words, a claim or a question, no trailing period. State what is at stake
  or what is strange; never just label the scene. At most {RANKING_TITLE_MAX_CHARS} characters.
- hook: one sentence, at most {RANKING_HOOK_MAX_CHARS} characters, saying why a viewer should stay.
  Never quote the clip: do not copy any line of dialogue.

Use names and objects from the clip, but write your own words. Never write
"In this episode..." or "Interesting moment". Two finished examples:

{{"selections": [
  {{"window_id": "W001", "title": "Their secret recipe was never a secret", "hook": "One reveal turns a family legend into a joke on everyone."}},
  {{"window_id": "W002", "title": "Who really pays for the mule's feed?", "hook": "A partnership deal hides a cost nobody agreed to."}}
]}}
{retry_note}
Return exactly {len(windows)} entries, one per clip, using only the IDs below.

CLIPS:

{clips}
""".strip()


def _titling_selections(
    host: str,
    model: str,
    windows: list[CandidateWindow],
    *,
    retry: bool,
) -> dict[str, dict[str, Any]] | None:
    window_ids = [f"W{index:03d}" for index in range(1, len(windows) + 1)]
    try:
        result = call_ollama_clip_titler(
            host,
            model,
            build_titling_prompt(windows, retry=retry),
            window_ids=window_ids,
        )
    except (RuntimeError, WindowRankingTimeout) as exc:
        log(f"WARNING: titling pass failed ({exc}); using local titles.")
        return None
    by_id: dict[str, dict[str, Any]] = {}
    for selection in result.get("selections", []) if isinstance(result, dict) else []:
        if isinstance(selection, dict):
            by_id[str(selection.get("window_id", "")).strip().upper()] = selection
    return by_id


def _accepted_title_and_hook(
    selection: dict[str, Any], window: CandidateWindow
) -> tuple[str, str]:
    title = grounded_ranker_text(selection.get("title", ""), window)
    if not is_valid_title(title):
        title = ""
    hook = grounded_ranker_text(selection.get("hook", ""), window)
    if is_quote_of_clip(hook, window):
        hook = ""
    return title, hook


def title_selected_clips(
    host: str,
    model: str,
    ranked: list[tuple[CandidateWindow, int, str]],
) -> list[tuple[CandidateWindow, int, str]]:
    """Add a title and hook to each chosen clip with one small extra call.

    Rejected titles/hooks (generic, ungrounded, a quote of the clip, a title
    under 4 words or ending in a period) get one retry call covering only those
    clips. Titling is cosmetic: on any failure the clips come back unchanged
    and candidate_clips_from_ranked_windows() falls back to the local
    grounded_title_from_text / make_specific_hook generators.
    """
    if not ranked:
        return ranked

    windows = [item[0] for item in ranked]
    started = time.monotonic()
    first = _titling_selections(host, model, windows, retry=False)
    if first is None:
        return ranked

    accepted: list[tuple[str, str]] = []
    for index, window in enumerate(windows, start=1):
        accepted.append(_accepted_title_and_hook(first.get(f"W{index:03d}", {}), window))

    rejected = [index for index, (title, hook) in enumerate(accepted) if not title or not hook]
    if rejected:
        log(f"Titling retry for {len(rejected)} clip(s) with a rejected title or hook.")
        second = _titling_selections(
            host, model, [windows[index] for index in rejected], retry=True
        )
        for position, index in enumerate(rejected, start=1):
            retried = _accepted_title_and_hook(
                (second or {}).get(f"W{position:03d}", {}), windows[index]
            )
            accepted[index] = (accepted[index][0] or retried[0], accepted[index][1] or retried[1])

    titled: list[tuple[CandidateWindow, int, str]] = []
    model_titles = 0
    for (window, score, reason), (title, hook) in zip(ranked, accepted):
        model_titles += 1 if title else 0
        new_reason = RankedReason(str(reason))
        new_reason.title = title
        new_reason.hook = hook
        new_reason.sub_scores = dict(
            reason.sub_scores if isinstance(reason, RankedReason) else {}
        )
        titled.append((window, score, new_reason))
    log(
        f"Titling pass completed in {time.monotonic() - started:.1f}s: "
        f"{model_titles}/{len(ranked)} model titles kept."
    )
    return titled


SIMPLE_CALIBRATION_BLOCK = """Score calibration:
- 90-100: exceptional standout moment; an obvious Short
- 80-89: strong complete scene or moment
- 70-79: usable but not special
- below 70: weaker but still usable backup material

Do not award 90+ to ordinary setup, a title/opening, or recognizable dialogue
without a concrete event or payoff."""

SIMPLE_EXAMPLE_BLOCK = """Return ONLY this JSON structure:

{
  "selections": [
    {
      "window_id": "W001",
      "score": 95,
      "reason": "Krabs refuses to pay, then bills the customers."
    },
    {
      "window_id": "W002",
      "score": 88,
      "reason": "Krabs refuses to pay, then bills the customers."
    },
    {
      "window_id": "W003",
      "score": 82,
      "reason": "Krabs refuses to pay, then bills the customers."
    }
  ]
}"""

DETAILED_CALIBRATION_BLOCK = """Score every selection on four independent 0-25 judgments:
- hook_strength: does the first line make a viewer stay?
- self_contained: does it make sense with no prior context?
- payoff: does it resolve, land a joke, or reveal something?
- peak: is there a genuine emotional or comedic high point?

Judge each one separately; ordinary setup, a title/opening, or plain
recognizable dialogue scores low on payoff and peak. Return only the window ID
and the four numbers -- no text."""

DETAILED_EXAMPLE_BLOCK = """Return ONLY this JSON structure. The two entries below are unrelated
to your windows; they only show the shape. Their numbers are arbitrary --
judge each of your windows on its own content:

{
  "selections": [
    {
      "window_id": "W001",
      "hook_strength": 12,
      "self_contained": 22,
      "payoff": 6,
      "peak": 9
    },
    {
      "window_id": "W002",
      "hook_strength": 8,
      "self_contained": 15,
      "payoff": 24,
      "peak": 23
    }
  ]
}"""


def build_window_ranking_prompt(
    transcript: TranscriptData,
    valid_windows: list[CandidateWindow],
    target_clip_count: int = 3,
    excluded_window_ids: set[str] | None = None,
    response_count_override: int | None = None,
    context_max_chars: int = RANKING_CONTEXT_MAX_CHARS,
    detailed: bool = False,
) -> str:
    excluded = excluded_window_ids or set()
    windows_text = format_valid_windows_for_prompt(valid_windows, excluded)
    source_context = transcript_context_for_window_ranking(
        transcript,
        max_chars=context_max_chars,
    )
    response_count = window_ranking_response_count(
        valid_windows,
        target_clip_count,
        excluded,
        response_count_override,
    )

    if detailed:
        calibration_block = DETAILED_CALIBRATION_BLOCK
        example_block = DETAILED_EXAMPLE_BLOCK
        detailed_field_rule = (
            " Score each candidate on the four 0-25 sub-scores only; "
            "never return a total."
        )
        duration_rule = (
            f"Candidates run {MIN_CLIP_SECONDS:g}-{MAX_CLIP_SECONDS:g} seconds and each "
            "window shows its own duration. A Short lives or dies in its first three "
            "seconds, so a tight 25-second moment beats a padded 85-second one. Choose "
            "a longer window only when its payoff genuinely needs the runtime. More "
            "words are not more quality."
        )
    else:
        calibration_block = SIMPLE_CALIBRATION_BLOCK
        example_block = SIMPLE_EXAMPLE_BLOCK
        detailed_field_rule = ""
        duration_rule = (
            f"Candidates vary in length between {MIN_CLIP_SECONDS:g} and "
            f"{MAX_CLIP_SECONDS:g} seconds. Judge the moment, not the duration -- do "
            "not prefer a candidate merely for being longer or shorter."
        )

    return f"""
You are an editor selecting clips for short-form video.

Your ONLY job is to rank the pre-approved transcript windows below. Use the
source context only to understand where a candidate sits in the overall source.
You may select ONLY the supplied window IDs.

Do NOT invent timestamps.
Do NOT create new windows.
Do NOT modify the windows.
Do NOT return timestamps.

Each window has an ID such as W001, W002, etc.

Choose the {response_count} strongest potential Shorts. Candidates vary in
length (see each window's own duration below); a good Short is exactly as
long as its setup-to-payoff arc needs, not a fixed length.

A strong Short should:
- make sense with minimal context
- contain a specific event or payoff, not merely recognizable dialogue
- contain a mini-arc where possible: setup -> escalation/conflict -> punchline, reveal, or reaction
- prioritize a funny payoff, conflict, surprise, embarrassment, escalation, strong reaction, absurd situation, memorable exchange, or clear mini-story
- create curiosity
- have a natural beginning and ending
- avoid long stretches of filler conversation
- avoid generic statements
- work as a standalone clip at its own supplied length
- use its full supplied span as a complete mini-story, not merely a setup before a payoff elsewhere

Penalize a candidate that only establishes the episode, introduces people or a
location, mostly contains greetings, ends before its payoff, or needs lots of
outside context. Treat opening themes, title sequences, credits, recaps,
sponsor reads, and other boilerplate as structural material, not strong Shorts,
unless that material itself contains a unique story event.

{calibration_block}
{example_block}

Rules:
1. Return exactly {response_count} different window IDs whenever that many eligible non-structural windows exist.
2. The IDs MUST come from the supplied windows.
3. Never invent an ID.
4. Do not return timestamps.
5. Do not return the transcript.
6. Do not return any additional fields.{detailed_field_rule}
7. Rank the strongest candidate first.
8. Prefer distinct, non-overlapping moments from different parts of the source when quality is similar.
9. Avoid returning multiple windows that cover essentially the same scene or conversation beat.
10. Reasons must mention concrete words, people, objects, places, or actions from that window.
11. Do not write phrases such as "clear setup and payoff", "engaging conversation", or "becomes the center of attention".
12. {duration_rule}
13. Do not select a structural theme, title, credits, recap, sponsor, or boilerplate window unless it itself contains a unique story event.
14. Default toward distinct scenes from different parts of the source. Do not cluster selections in one adjacent conversation when similarly strong moments exist elsewhere.
15. Do not omit weaker-but-usable real scenes because they score below 70. Rank them honestly after stronger candidates so the requested final count can still be filled.

SOURCE CONTEXT (context only; do not select timestamps or IDs from this section):

{source_context}

PRE-APPROVED WINDOWS:

{windows_text}
""".strip()



def ranked_windows_from_result(
    result: dict[str, Any],
    valid_windows: list[CandidateWindow],
    excluded_window_ids: set[str] | None = None,
) -> list[tuple[CandidateWindow, int, str]]:
    """Convert ranker window IDs into real CandidateWindow objects."""
    if not isinstance(result, dict):
        return []

    selections = result.get("selections", [])
    if not isinstance(selections, list):
        return []

    window_map = {
        f"W{index:03d}": window
        for index, window in enumerate(valid_windows, start=1)
    }

    ranked: list[tuple[CandidateWindow, int, str]] = []
    seen: set[str] = set()
    excluded = excluded_window_ids or set()

    for selection in selections:
        if not isinstance(selection, dict):
            continue

        window_id = str(selection.get("window_id", "")).strip().upper()
        if window_id in seen or window_id in excluded:
            continue

        window = window_map.get(window_id)
        if window is None:
            continue

        sub_scores: dict[str, int] = {}
        for field in RUBRIC_FIELDS:
            value = coerce_int(
                selection.get(field), minimum=0, maximum=RUBRIC_MAX_PER_FIELD
            )
            if value is not None:
                sub_scores[field] = value

        if sub_scores:
            # The model judges each dimension; Python does the adding.
            score = sum(sub_scores.values())
        else:
            score = coerce_int(selection.get("score"), minimum=0, maximum=100)
            if score is None:
                score = 0

        reason = RankedReason(str(selection.get("reason", "")).strip())
        reason.sub_scores = sub_scores
        ranked.append((window, score, reason))
        seen.add(window_id)

    return ranked


def ranked_window_ids(
    ranked_windows: list[tuple[CandidateWindow, int, str]],
    valid_windows: list[CandidateWindow],
) -> set[str]:
    """Resolve ranked objects back to the ranker's stable prompt IDs."""

    ids = set()
    for index, window in enumerate(valid_windows, start=1):
        if any(window == ranked[0] for ranked in ranked_windows):
            ids.add(f"W{index:03d}")
    return ids


def select_distinct_ranked_windows(
    ranked_windows: list[tuple[CandidateWindow, int, str]],
    target_clip_count: int,
    source_duration: float | None = None,
) -> list[tuple[CandidateWindow, int, str]]:
    """Select distinct source moments without returning overlapping clips.

    Pass 1 applies the overlap rule plus a duration quota (at most half the
    clips longer than LONG_CLIP_SECONDS) and, when source_duration is known,
    a region quota (at most MAX_SELECTIONS_PER_REGION per equal-width region
    by start time). If that leaves fewer than target_clip_count, pass 2
    backfills by score with the quotas relaxed. Overlap is never relaxed, so fewer than
    target_clip_count clips come back only when no non-overlapping ones remain.
    """

    def overlaps(first: CandidateWindow, second: CandidateWindow) -> bool:
        overlap = max(0.0, min(first.end, second.end) - max(first.start, second.start))
        return overlap > TIMESTAMP_MATCH_TOLERANCE_SECONDS

    ordered = list(ranked_windows)
    max_long = max(1, target_clip_count // 2) if target_clip_count > 0 else 0
    region_count = max(1, target_clip_count)

    def region_of(window: CandidateWindow) -> int:
        if not source_duration or source_duration <= 0:
            return 0
        return min(region_count - 1, int(window.start / source_duration * region_count))

    max_short = max(1, target_clip_count // 2)
    min_mid_long = min(MIN_MID_LONG_CLIPS, target_clip_count)
    selected: list[tuple[CandidateWindow, int, str]] = []

    def run_pass(*, enforce_quotas: bool) -> None:
        for candidate in ordered:
            if len(selected) >= target_clip_count:
                return
            if any(candidate is chosen for chosen in selected):
                continue
            window = candidate[0]
            if any(overlaps(window, chosen[0]) for chosen in selected):
                continue
            if enforce_quotas:
                long_count = sum(
                    1 for chosen in selected if chosen[0].duration_seconds > LONG_CLIP_SECONDS
                )
                if window.duration_seconds > LONG_CLIP_SECONDS and long_count >= max_long:
                    continue
                if source_duration and source_duration > 0:
                    in_region = sum(
                        1 for chosen in selected if region_of(chosen[0]) == region_of(window)
                    )
                    if in_region >= MAX_SELECTIONS_PER_REGION:
                        continue
                if window.duration_seconds < SHORT_CLIP_SECONDS and sum(
                    1 for chosen in selected if chosen[0].duration_seconds < SHORT_CLIP_SECONDS
                ) >= max_short and any(
                    other[0].duration_seconds >= SHORT_CLIP_SECONDS
                    and not any(other is chosen for chosen in selected)
                    and not any(overlaps(other[0], chosen[0]) for chosen in selected)
                    for other in ordered
                ):
                    continue
                if window.duration_seconds < MID_LONG_CLIP_SECONDS:
                    deficit = min_mid_long - sum(
                        1
                        for chosen in selected
                        if chosen[0].duration_seconds >= MID_LONG_CLIP_SECONDS
                    )
                    slots_after = target_clip_count - len(selected) - 1
                    if deficit > slots_after and any(
                        other[0].duration_seconds >= MID_LONG_CLIP_SECONDS
                        and not any(other is chosen for chosen in selected)
                        and not any(overlaps(other[0], chosen[0]) for chosen in selected)
                        for other in ordered
                    ):
                        continue
            selected.append(candidate)

    run_pass(enforce_quotas=True)
    if len(selected) < target_clip_count:
        run_pass(enforce_quotas=False)
    return selected


def chronological_window_batches(
    windows: list[CandidateWindow],
    batch_size: int = FIRST_STAGE_RANKING_BATCH_SIZE,
) -> list[list[CandidateWindow]]:
    """Split a complete candidate timeline into prompt-sized chronological batches."""
    return [
        windows[index : index + batch_size]
        for index in range(0, len(windows), batch_size)
    ]


def rank_window_request(
    host: str,
    model: str,
    transcript: TranscriptData,
    windows: list[CandidateWindow],
    selection_count: int,
    *,
    request_label: str,
    context_max_chars: int,
    minimum_count: int = 0,
    detailed: bool = False,
) -> list[tuple[CandidateWindow, int, str]]:
    """Run one measured, schema-constrained ranking request."""

    selection_count = min(len(windows), max(0, int(selection_count)))
    if not windows or selection_count <= 0:
        return []
    prompt = build_window_ranking_prompt(
        transcript,
        windows,
        target_clip_count=selection_count,
        context_max_chars=context_max_chars,
        detailed=detailed,
    )
    predict_budget = ranking_num_predict(selection_count, detailed)
    log(
        f"Ranking {request_label}: {len(windows)} candidates, "
        f"{len(prompt)} prompt chars, timeout={ranking_timeout(predict_budget)}s, "
        f"num_predict={predict_budget}, "
        f"num_ctx={ranking_num_ctx(len(prompt), predict_budget)}"
    )
    started = time.monotonic()
    try:
        result = call_ollama_window_ranker(
            host,
            model,
            prompt,
            window_ids=[f"W{index:03d}" for index in range(1, len(windows) + 1)],
            selection_count=selection_count,
            **({"detailed": True} if detailed else {}),
        )
    except WindowRankingTimeout:
        elapsed = time.monotonic() - started
        log(f"{request_label} timed out after {elapsed:.1f}s.")
        raise

    ranked = ranked_windows_from_result(result, windows)
    elapsed = time.monotonic() - started
    log(f"{request_label} completed in {elapsed:.1f}s: {len(ranked)} valid selections")

    if detailed:
        missing = [
            window_item
            for window_item in ranked
            if not getattr(window_item[2], "sub_scores", None)
        ]
        if missing:
            log(
                f"WARNING: {request_label} requested detailed sub-scores but "
                f"{len(missing)}/{len(ranked)} selections came back without any."
            )

    if not ranked:
        raw_text = getattr(result, "raw_text", "")
        log(f"{request_label} raw model response (first 500 chars): {raw_text[:500]!r}")

    if len(ranked) >= selection_count:
        return ranked[:selection_count]

    if len(ranked) >= minimum_count:
        log(
            f"WARNING: {request_label} returned {len(ranked)} valid selections; "
            f"requested {selection_count}."
        )
        return ranked

    raise WindowRankingShortfall(request_label, selection_count, len(ranked))


def rank_first_stage_batch(
    host: str,
    model: str,
    transcript: TranscriptData,
    batch: list[CandidateWindow],
    selection_count: int,
    *,
    batch_number: int,
    batch_total: int,
) -> list[tuple[CandidateWindow, int, str]]:
    """Rank one chronological batch, splitting it once after a timeout or shortfall.

    Losing one batch's shortlist must never fail the whole analysis -- the
    other chronological batches still cover the source -- so every failure
    path here logs a warning and returns an empty list instead of raising.
    """

    label = f"batch {batch_number}/{batch_total}"
    try:
        return rank_window_request(
            host,
            model,
            transcript,
            batch,
            selection_count,
            request_label=label,
            context_max_chars=FIRST_STAGE_CONTEXT_MAX_CHARS,
            minimum_count=1,
        )
    except (WindowRankingTimeout, WindowRankingShortfall):
        if len(batch) <= 1:
            log(f"{label} produced no usable selections; skipping this batch.")
            return []

    midpoint = max(1, len(batch) // 2)
    sub_batches = [batch[:midpoint], batch[midpoint:]]
    sub_batches = [items for items in sub_batches if items]
    per_sub_batch = max(1, (selection_count + len(sub_batches) - 1) // len(sub_batches))
    log(
        f"Retrying {label} once as {len(sub_batches)} smaller chronological "
        f"sub-batches."
    )
    ranked: list[tuple[CandidateWindow, int, str]] = []
    for sub_number, sub_batch in enumerate(sub_batches, start=1):
        try:
            ranked.extend(
                rank_window_request(
                    host,
                    model,
                    transcript,
                    sub_batch,
                    min(len(sub_batch), per_sub_batch),
                    request_label=f"{label} retry {sub_number}/{len(sub_batches)}",
                    context_max_chars=FIRST_STAGE_CONTEXT_MAX_CHARS,
                    minimum_count=1,
                )
            )
        except (WindowRankingTimeout, WindowRankingShortfall):
            log(
                f"{label} retry {sub_number}/{len(sub_batches)} produced no "
                "usable selections; skipping this sub-batch."
            )
            continue

    if not ranked:
        log(f"{label} produced no usable selections after retry; skipping this batch.")
        return []

    return sorted(ranked, key=lambda item: (-item[1], item[0].start))[:selection_count]


def format_clip_log_lines(candidate: dict[str, Any], analysis: dict[str, Any]) -> list[str]:
    """CLI lines for one clip: title as the headline, hook beneath it."""
    title = candidate.get("title") or make_specific_hook(analysis, candidate)
    lines = [
        "Clip: {start} -> {end} | {duration:.1f}s | score {score} | {title}".format(
            start=candidate["start_timestamp"],
            end=candidate["end_timestamp"],
            duration=float(candidate["duration_seconds"]),
            score=candidate.get("score", 0),
            title=title,
        )
    ]
    if candidate.get("hook"):
        lines.append(f"       hook: {candidate['hook']}")
    return lines


def trim_shortlist_by_region(
    shortlist: list[tuple[CandidateWindow, int, str]],
    region_count: int,
    source_duration: float,
    max_candidates: int = FINAL_POOL_MAX_CANDIDATES,
) -> list[tuple[CandidateWindow, int, str]]:
    """Trim to max_candidates without starving any part of the source.

    The source is split into region_count equal regions by window start. Each
    region contributes its top ceil(max_candidates / region_count) by
    first-stage score, then leftover slots fill by global score. Returned in
    chronological order.
    """
    if len(shortlist) <= max_candidates:
        return sorted(shortlist, key=lambda candidate: candidate[0].start)

    region_count = max(1, region_count)
    per_region = -(-max_candidates // region_count)

    def region_of(window: CandidateWindow) -> int:
        if source_duration <= 0:
            return 0
        return min(region_count - 1, max(0, int(window.start / source_duration * region_count)))

    regions: list[list[tuple[CandidateWindow, int, str]]] = [[] for _ in range(region_count)]
    for candidate in shortlist:
        regions[region_of(candidate[0])].append(candidate)

    kept: list[tuple[CandidateWindow, int, str]] = []
    leftovers: list[tuple[CandidateWindow, int, str]] = []
    for members in regions:
        members.sort(key=lambda candidate: -candidate[1])
        kept.extend(members[:per_region])
        leftovers.extend(members[per_region:])

    # Per-region quotas can overshoot when max_candidates isn't a multiple of
    # region_count; drop the weakest so the cap always holds.
    if len(kept) > max_candidates:
        kept.sort(key=lambda candidate: -candidate[1])
        leftovers.extend(kept[max_candidates:])
        kept = kept[:max_candidates]
    if len(kept) < max_candidates:
        leftovers.sort(key=lambda candidate: -candidate[1])
        kept.extend(leftovers[: max_candidates - len(kept)])

    counts = [
        f"{sum(1 for candidate in kept if region_of(candidate[0]) == index)}/{len(members)}"
        for index, members in enumerate(regions)
    ]
    log(
        f"Trimmed final pool from {len(shortlist)} to {len(kept)} "
        f"(kept/available per region): {', '.join(counts)}"
    )
    return sorted(kept, key=lambda candidate: candidate[0].start)


def shuffled_for_final_pass(
    windows: list[CandidateWindow], seed: int | None = None
) -> tuple[list[CandidateWindow], int]:
    """Seeded shuffle so prompt position carries no chronological signal.

    W-IDs are assigned by prompt position and mapped straight back to the real
    window objects, so callers restore chronological order by sorting on start.
    The seed comes from SHORTS_RANK_SEED when set, else is derived from the
    windows themselves, so a run is reproducible.
    """
    if seed is None:
        env_seed = os.environ.get("SHORTS_RANK_SEED", "").strip()
        if env_seed.lstrip("-").isdigit():
            seed = int(env_seed)
        else:
            seed = zlib.crc32(
                ",".join(f"{window.start:.2f}" for window in windows).encode()
            )
    order = list(windows)
    random.Random(seed).shuffle(order)
    return order, seed


def single_window_json_schema() -> dict[str, Any]:
    properties = {
        field: {"type": "integer", "minimum": 0, "maximum": RUBRIC_MAX_PER_FIELD}
        for field in MODEL_RUBRIC_FIELDS
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(MODEL_RUBRIC_FIELDS),
        "properties": properties,
    }


SINGLE_SCORE_CALIBRATION_BLOCK = """Calibration -- two fixed reference clips, unrelated to the one you are
scoring. Judge your clip against these bands, not against their content.

STRONG example (total 64):
  "You didn't actually eat the last krabby patty, did you?" Squidward went
  pale. "I did more than eat it -- I microwaved the recipe and gave it to the
  seagulls." Mr. Krabs's eye twitched, and by the time he opened his mouth the
  whole restaurant had gone silent.
  self_contained=24 (no outside characters or backstory needed)
  payoff=21 (the reveal is the clip's climax and it lands)
  peak=19 (the twitch and the room going silent is the high point)

MID example (total 28) -- real content this pipeline scored 75 last run;
anchors the bottom of the real range, not an invented extreme:
  "It's so dry! Step right up and try our Jim Dandy Jams!" ...never
  resolves... "Oh, hey, that's good tail!"
  self_contained=14 (sales patter, but the gist comes through)
  payoff=6 (never resolves, just more hype lines)
  peak=8 (energetic, but not a standout beat)"""

SINGLE_SCORE_RUBRIC_BLOCK = """Score it on three independent 0-25 judgments, each judged only from the
transcript below:

self_contained (0-25): judge whether a viewer with zero prior context follows it.
  0-5   requires knowing who these people are or what happened earlier
  6-12  mostly followable but leans on an unexplained reference
  13-19 a new viewer follows the gist with only minor confusion
  20-25 fully self-contained -- nothing outside the clip is needed

payoff (0-25): judge the ending.
  0-5   ends before anything resolves, or nothing happens
  6-12  a mild resolution or trailing remark
  13-19 a clear joke lands, a question is answered, or a reveal occurs
  20-25 a sharp, specific payoff that recontextualizes what came before

peak (0-25): judge the single highest moment in the clip.
  0-5   no emotional or comedic high point at all
  6-12  a mild reaction or low-key moment
  13-19 raised voices or an argument, ordinary for this show -- not a
        standout
  20-25 extreme even by this show's own baseline -- worth replaying,
        not just another heated exchange like the rest

Do not return the same total for different clips unless they are genuinely
equivalent -- a midpoint score must match the midpoint band, not be chosen as
a hedge. Return only the three numbers."""


def build_single_window_prompt(window: CandidateWindow) -> str:
    return f"""
You are an editor judging one candidate clip for short-form video.

{SINGLE_SCORE_CALIBRATION_BLOCK}

{SINGLE_SCORE_RUBRIC_BLOCK}

FIRST LINE: {first_spoken_line(window.text)}

CLIP ({window.duration_seconds:.0f}s):
{truncate_for_prompt(window.text, SINGLE_SCORE_TEXT_MAX_CHARS)}
""".strip()


def call_ollama_single_scorer(host: str, model: str, prompt: str) -> dict[str, Any]:
    return post_for_selections(
        host,
        model,
        prompt,
        single_window_json_schema(),
        num_predict=SINGLE_SCORE_NUM_PREDICT,
        selection_count=1,
        purpose="clip-scoring",
        num_ctx=SINGLE_SCORE_NUM_CTX,
    )


def score_windows_individually(
    host: str,
    model: str,
    windows: list[CandidateWindow],
    seed: int | None = None,
) -> list[tuple[CandidateWindow, int, str]]:
    """Score every window in its own request so no judgment sees another.

    A failed request scores that window 0 with no sub-scores and is logged;
    one bad call costs one candidate. Requests run in a seeded shuffled order
    (presentation order only -- results are independent of it).
    """
    order, seed = shuffled_for_final_pass(windows, seed)
    log(f"Scoring {len(order)} candidates one per request (seed {seed}).")
    started = time.monotonic()
    results: list[tuple[CandidateWindow, int, str]] = []
    failures = 0
    for number, window in enumerate(order, start=1):
        sub_scores: dict[str, int] = {}
        try:
            result = call_ollama_single_scorer(
                host, model, build_single_window_prompt(window)
            )
            for field in MODEL_RUBRIC_FIELDS:
                value = coerce_int(
                    result.get(field), minimum=0, maximum=RUBRIC_MAX_PER_FIELD
                )
                if value is not None:
                    sub_scores[field] = value
            if len(sub_scores) == len(MODEL_RUBRIC_FIELDS):
                sub_scores["hook_strength"] = hook_strength(
                    first_spoken_line(window.text)
                )
        except (RuntimeError, WindowRankingTimeout) as exc:
            log(f"WARNING: scoring candidate {number}/{len(order)} failed: {exc}")
        if len(sub_scores) < len(RUBRIC_FIELDS):
            failures += 1
            log(
                f"WARNING: candidate at {format_timestamp(window.start)} got no usable "
                "sub-scores; scoring it 0."
            )
            sub_scores = {}
        reason = RankedReason("")
        reason.sub_scores = sub_scores
        results.append((window, sum(sub_scores.values()), reason))
    log(
        f"Scored {len(order)} candidates in {time.monotonic() - started:.1f}s "
        f"({failures} failed)."
    )
    return results


def rank_exact_minute_windows(
    host: str,
    model: str,
    transcript: TranscriptData,
    valid_windows: list[CandidateWindow],
    target_clip_count: int,
    seed: int | None = None,
) -> list[tuple[CandidateWindow, int, str]]:
    """Rank exact-minute windows through local shortlists and one global pass."""
    batches = chronological_window_batches(valid_windows)
    if not batches:
        return []

    # First-stage batch scores are otherwise discarded once the final pass
    # runs; keep them only to break ties in the final rubric total, since
    # they are the one comparative signal in the pipeline.
    first_stage_scores: dict[CandidateWindow, int] = {}

    if len(batches) == 1:
        rankable_windows = valid_windows
    else:
        per_batch_shortlist = min(
            3,
            max(
                FIRST_STAGE_SHORTLIST_COUNT,
                (target_clip_count * 2 + len(batches) - 1) // len(batches),
            ),
        )
        shortlist: list[tuple[CandidateWindow, int, str]] = []
        for batch_number, batch in enumerate(batches, start=1):
            shortlist.extend(
                rank_first_stage_batch(
                    host,
                    model,
                    transcript,
                    batch,
                    min(len(batch), per_batch_shortlist),
                    batch_number=batch_number,
                    batch_total=len(batches),
                )
            )
        if not shortlist:
            raise RuntimeError("No candidate windows survived first-stage ranking.")
        if len(shortlist) > FINAL_POOL_MAX_CANDIDATES:
            source_end = max((segment.end for segment in transcript.segments), default=0.0)
            shortlist = trim_shortlist_by_region(
                shortlist,
                target_clip_count,
                source_end or max(window.end for window, _s, _r in shortlist),
            )
        shortlist.sort(key=lambda candidate: candidate[0].start)
        rankable_windows = [window for window, _score, _reason in shortlist]
        first_stage_scores = {window: score for window, score, _reason in shortlist}

    final_ranked = score_windows_individually(host, model, rankable_windows, seed)
    # Rank by the Python-computed total, best first; ties break by the
    # first-stage batch score (free, already computed, order-independent)
    # before falling back to start time.
    final_ranked = sorted(
        final_ranked,
        key=lambda item: (-item[1], -first_stage_scores.get(item[0], 0), item[0].start),
    )
    source_end = max((segment.end for segment in transcript.segments), default=0.0)
    selected = select_distinct_ranked_windows(
        final_ranked,
        target_clip_count,
        source_duration=source_end,
    )
    return title_selected_clips(host, model, selected)

def snap_boundary_to_segment_edge(
    boundary: float,
    segments: list[TranscriptSegment],
    leeway_seconds: float = CLIP_BOUNDARY_LEEWAY_SECONDS,
) -> float:
    """Nudge one clip edge to the nearest segment start/end within leeway.

    Looks at both segment starts and ends as candidate landing points --
    either can mark a natural pause -- and returns the closest one within
    leeway_seconds. Returns the original boundary unchanged if nothing
    real transcript evidence falls within range.
    """

    best = boundary
    best_distance = leeway_seconds
    for segment in segments:
        for edge in (segment.start, segment.end):
            distance = abs(edge - boundary)
            if distance <= best_distance:
                best = edge
                best_distance = distance
    return best


def snap_window_to_sentence_boundaries(
    window: CandidateWindow,
    segments: list[TranscriptSegment] | None,
    leeway_seconds: float = CLIP_BOUNDARY_LEEWAY_SECONDS,
) -> CandidateWindow:
    """Return a copy of window with edges nudged to natural sentence pauses.

    Only ever called on an already-selected/final window, never on the
    fixed grid generate_valid_windows() hands to the ranker -- scoring
    stays on exact minutes so overlap/dedup logic keeps working, and only
    the clip a viewer will actually see gets the more natural edges.
    """

    if not segments:
        return window

    new_start = snap_boundary_to_segment_edge(window.start, segments, leeway_seconds)
    new_end = snap_boundary_to_segment_edge(window.end, segments, leeway_seconds)
    if new_end <= new_start:
        return window

    text = " ".join(
        segment.text
        for segment in segments
        if segment.end > new_start and segment.start < new_end
    )
    return CandidateWindow(start=new_start, end=new_end, text=text or window.text)


def candidate_clips_from_ranked_windows(
    ranked_windows: list[tuple[CandidateWindow, int, str]],
    raw_analysis: dict[str, Any],
    segments: list[TranscriptSegment] | None = None,
) -> list[dict[str, Any]]:
    """Convert ranked CandidateWindow objects into candidate clip dictionaries."""
    analysis = raw_analysis.get("analysis", raw_analysis)
    if not isinstance(analysis, dict):
        analysis = {}

    candidates: list[dict[str, Any]] = []

    for raw_window, score, reason in ranked_windows:
        window = snap_window_to_sentence_boundaries(raw_window, segments)
        description = truncate_for_prompt(window.text, 180)
        grounded_reason = (
            ""
            if has_generic_editor_language(reason)
            else reason
        )

        # A plain str has a .title() method, so only trust RankedReason attrs.
        ranker_title = reason.title if isinstance(reason, RankedReason) else ""
        ranker_hook = reason.hook if isinstance(reason, RankedReason) else ""

        candidate = {
            "start_timestamp": format_timestamp(window.start),
            "end_timestamp": format_timestamp(window.end),
            "duration_seconds": round_duration(window.duration_seconds),
            "hook": "",
            "title": ranker_title or grounded_title_from_text(window.text),
            "description": description,
            "score": score,
            # The title/hook pair replaces the "anchored by the line" template;
            # only a real, non-generic model reason is kept.
            "reason": grounded_reason,
        }

        # A rejected/missing hook stays empty: the local fallback generators
        # return transcript excerpts, which is exactly what the quote guard
        # forbids. The card shows the title alone instead.
        candidate["hook"] = ranker_hook

        if isinstance(reason, RankedReason):
            sub_scores = getattr(reason, "sub_scores", {})
            for field in RUBRIC_FIELDS:
                if field in sub_scores:
                    candidate[field] = sub_scores[field]
        candidates.append(candidate)

    return candidates

def call_ollama(host: str, model: str, prompt: str) -> dict[str, Any]:
    response = None
    last_http_error: HTTPError | None = None
    for response_format in (analysis_json_schema(), "json"):
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": response_format,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
            },
        }

        try:
            response = request_json(
                f"{host}/api/generate",
                payload=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            break
        except HTTPError as exc:
            last_http_error = exc
            if response_format == "json":
                raise RuntimeError(f"Ollama model request failed with HTTP {exc.code}.") from exc
            continue
        except URLError as exc:
            raise RuntimeError(f"Ollama stopped responding: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Ollama model request timed out.") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Ollama returned invalid JSON for the model request.") from exc
        except OSError as exc:
            raise RuntimeError(f"Ollama model request failed: {exc}") from exc

    if response is None:
        if last_http_error is not None:
            raise RuntimeError(f"Ollama model request failed with HTTP {last_http_error.code}.")
        raise RuntimeError("Ollama did not return a model response.")

    response_text = str(response.get("response", "")).strip()
    if not response_text:
        raise RuntimeError("Ollama returned an empty model response.")

    try:
        return extract_json_object(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"The model response was not valid JSON: {exc}") from exc


def coerce_int(value: Any, minimum: int | None = None, maximum: int | None = None) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not match:
            return None
        try:
            number = int(round(float(match.group(0))))
        except ValueError:
            return None

    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def normalize_copyright_risk(risk: Any) -> dict[str, str]:
    if not isinstance(risk, dict):
        risk = {"level": "", "notes": str(risk or "")}

    level = str(risk.get("level", "")).strip().lower()
    notes = str(risk.get("notes", "")).strip()
    copyrighted_source = str(risk.get("copyrighted_source_footage_audio", "")).strip()
    original_commentary = str(risk.get("original_commentary", "")).strip()
    transformative_editing = str(risk.get("transformative_editing", "")).strip()
    monetization_concerns = str(risk.get("reused_content_monetization_concerns", "")).strip()
    normalized_level = level if level in {"low", "medium", "high"} else "medium"
    combined_risk_text = " ".join(
        [
            notes,
            copyrighted_source,
            original_commentary,
            transformative_editing,
            monetization_concerns,
        ]
    )
    lower_notes = combined_risk_text.lower()

    unsafe_claims = (
        "not copyrighted",
        "no copyright",
        "not trademarked",
        "safe to publish",
        "free to use",
        "public domain",
        "automatically makes",
        "guarantees monetization",
    )
    ownership_signals = (
        "user-owned",
        "user owned",
        "original recording",
        "original user footage",
        "created by the uploader",
    )

    has_unsafe_claim = any(phrase in lower_notes for phrase in unsafe_claims)
    has_ownership_signal = any(phrase in lower_notes for phrase in ownership_signals)
    if has_unsafe_claim or (normalized_level == "low" and not has_ownership_signal):
        normalized_level = "medium"
        notes = (
            "Potential reused-content/copyright risk because the recommendation relies on source dialogue. "
            "Human review is required; keep excerpts limited and add original commentary, context, or critique."
        )
        copyrighted_source = copyrighted_source or "Source footage/audio may be copyrighted unless the user owns or licensed it."
        original_commentary = original_commentary or "Original narration can add context or critique, but it does not automatically clear rights."
        transformative_editing = transformative_editing or "Transformative edits may help the creative framing, but they do not guarantee legal safety."
        monetization_concerns = monetization_concerns or "YouTube reused-content monetization review may still be a concern."
    elif not notes:
        notes = "Human review is required before publishing."

    return {
        "level": normalized_level,
        "copyrighted_source_footage_audio": copyrighted_source
        or "Unknown ownership status; verify rights before publishing.",
        "original_commentary": original_commentary
        or "Original commentary should add context, interpretation, critique, humor, or explanation.",
        "transformative_editing": transformative_editing
        or "Editing should create a new presentation instead of reposting raw source material.",
        "reused_content_monetization_concerns": monetization_concerns
        or "Monetization may be affected if the Short relies heavily on reused footage or audio.",
        "notes": notes,
    }


def round_duration(value: float) -> float:
    rounded = round(value, 3)
    return int(rounded) if rounded.is_integer() else rounded


def is_generic_hook(hook: str) -> bool:
    normalized = " ".join(hook.lower().split())
    if not normalized:
        return True
    return any(phrase in normalized for phrase in GENERIC_HOOK_PHRASES)


def has_generic_editor_language(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if not normalized:
        return True
    return any(phrase in normalized for phrase in GENERIC_HOOK_PHRASES)


def sentence_fragments(text: str) -> list[str]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    pieces = re.split(r"(?<=[.!?])\s+", cleaned)
    return [piece.strip(" \"'") for piece in pieces if piece.strip(" \"'")]


def transcript_excerpt(text: str, max_words: int = 12) -> str:
    fragments = sentence_fragments(text)
    source = fragments[0] if fragments else " ".join(str(text or "").split())
    words = source.split()
    if len(words) > max_words:
        source = " ".join(words[:max_words]).rstrip(".,;:")
    return source.strip()


def grounded_title_from_text(text: str) -> str:
    excerpt = transcript_excerpt(text, max_words=7)
    if not excerpt:
        return "Grounded transcript moment"

    # Prefer a literal editor title over model-written hype. This may be
    # a short quote fragment, but it is always grounded in the transcript.
    return excerpt.strip(" .,!?:;\"'")


def grounded_reason_from_text(text: str) -> str:
    excerpt = transcript_excerpt(text, max_words=14)
    if not excerpt:
        return "Transcript-grounded candidate that fits the Shorts duration."
    return f'The candidate is anchored by the line "{excerpt}".'


def make_specific_hook(analysis: dict[str, Any], candidate: dict[str, Any] | None = None) -> str:
    candidate = candidate or {}
    curiosity_gap = str(analysis.get("strongest_curiosity_gap", "")).strip()
    description = str(candidate.get("description", "")).strip()
    topic = str(analysis.get("main_topic", "")).strip()

    if description:
        return grounded_title_from_text(description)
    if curiosity_gap and topic:
        return f"{topic}: {curiosity_gap}"
    if curiosity_gap:
        return curiosity_gap
    if description and topic:
        return f"{topic}: {description}"
    if topic:
        return f"A focused short about {topic}"
    return "Grounded transcript moment"


def extract_hook_subject(text: str) -> str:
    stopwords = {
        "One",
        "What",
        "Which",
        "Man",
        "Look",
        "Like",
        "Everything",
        "Imagine",
        "Wait",
        "Find",
        "They",
        "Yeah",
        "Wow",
    }
    matches = re.findall(r"\b[A-Z][A-Za-z0-9']+(?:\s+[A-Z][A-Za-z0-9']+){0,3}\b", text)
    candidates = [match.strip() for match in matches if match.split()[0] not in stopwords]
    if not candidates:
        return ""
    return max(candidates, key=lambda item: (len(item.split()), len(item)))


def find_matching_window(
    start_value: Any,
    end_value: Any,
    valid_windows: list[CandidateWindow],
) -> CandidateWindow | None:
    start = parse_timestamp(start_value, prefer="first")
    end = parse_timestamp(end_value, prefer="last")
    if start is None or end is None:
        return None

    for window in valid_windows:
        if (
            abs(window.start - start) <= WINDOW_MATCH_TOLERANCE_SECONDS
            and abs(window.end - end) <= WINDOW_MATCH_TOLERANCE_SECONDS
        ):
            return window

    return None


def normalize_candidate_clip(
    raw_candidate: Any,
    valid_windows: list[CandidateWindow],
    analysis: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(raw_candidate, dict):
        return None

    window = find_matching_window(
        raw_candidate.get("start_timestamp"),
        raw_candidate.get("end_timestamp"),
        valid_windows,
    )
    if window is None:
        return None

    # Trust the candidate's own precise boundaries once find_matching_window()
    # has confirmed they correspond to a real mechanical window -- window.start/
    # window.end are the unsnapped mechanical grid, and using them here would
    # silently discard the sentence-boundary leeway
    # candidate_clips_from_ranked_windows() already applied.
    candidate_start = parse_timestamp(raw_candidate.get("start_timestamp"), prefer="first")
    candidate_end = parse_timestamp(raw_candidate.get("end_timestamp"), prefer="last")
    if candidate_start is None or candidate_end is None or candidate_end <= candidate_start:
        candidate_start, candidate_end = window.start, window.end

    duration = candidate_end - candidate_start
    if not (
        MIN_CLIP_SECONDS - CLIP_BOUNDARY_LEEWAY_SECONDS
        <= duration
        <= MAX_CLIP_SECONDS + CLIP_BOUNDARY_LEEWAY_SECONDS
    ):
        return None

    candidate = {
        "start_timestamp": format_timestamp(candidate_start),
        "end_timestamp": format_timestamp(candidate_end),
        "duration_seconds": round_duration(duration),
        "hook": str(raw_candidate.get("hook", "")).strip(),
        "title": str(raw_candidate.get("title", "")).strip(),
        "description": str(raw_candidate.get("description", "")).strip(),
        "score": coerce_int(raw_candidate.get("score"), minimum=0, maximum=100),
        "reason": str(raw_candidate.get("reason", "")).strip(),
    }
    for field in RUBRIC_FIELDS:
        value = coerce_int(
            raw_candidate.get(field), minimum=0, maximum=RUBRIC_MAX_PER_FIELD
        )
        if value is not None:
            candidate[field] = value
    if has_generic_editor_language(candidate["description"]):
        candidate["description"] = truncate_for_prompt(window.text, 180)
    if is_generic_hook(candidate["hook"]):
        candidate["hook"] = make_specific_hook(analysis, candidate)
    if not candidate["title"] or is_generic_hook(candidate["title"]):
        candidate["title"] = grounded_title_from_text(window.text)
    if candidate["score"] is None:
        candidate["score"] = 0
    if has_generic_editor_language(candidate["reason"]):
        # With a real hook the reason carries nothing a user wants to read.
        candidate["reason"] = (
            ""
            if candidate["hook"]
            else grounded_reason_from_text(candidate["description"])
        )
    return candidate


def fallback_candidate_clip(
    window: CandidateWindow,
    analysis: dict[str, Any],
    score: int,
) -> dict[str, Any]:
    description = truncate_for_prompt(window.text, 180)
    candidate = {
        "start_timestamp": format_timestamp(window.start),
        "end_timestamp": format_timestamp(window.end),
        "duration_seconds": round_duration(window.duration_seconds),
        "hook": "",
        "description": description,
        "score": score,
        "reason": grounded_reason_from_text(description),
    }
    candidate["hook"] = make_specific_hook(analysis, candidate)
    return candidate


def normalize_candidate_clips(
    raw_candidates: Any,
    valid_windows: list[CandidateWindow],
    analysis: dict[str, Any],
    allow_fallback: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_candidate in raw_candidates:
        candidate = normalize_candidate_clip(raw_candidate, valid_windows, analysis)
        if candidate is None:
            continue
        key = (candidate["start_timestamp"], candidate["end_timestamp"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)

    if allow_fallback and len(normalized) < 3 and len(valid_windows) >= 3:
        used_keys = {(candidate["start_timestamp"], candidate["end_timestamp"]) for candidate in normalized}
        ranked_windows = sorted(
            valid_windows,
            key=lambda window: (
                0
                if PREFERRED_MIN_CLIP_SECONDS
                <= window.duration_seconds
                <= PREFERRED_MAX_CLIP_SECONDS
                else 1,
                min(
                    abs(
                        window.duration_seconds
                        - PREFERRED_MIN_CLIP_SECONDS
                    ),
                    abs(
                        window.duration_seconds
                        - PREFERRED_MAX_CLIP_SECONDS
                    ),
                ),
                window.start,
            ),
        )
        for window in ranked_windows:
            if len(normalized) >= 3:
                break
            key = (format_timestamp(window.start), format_timestamp(window.end))
            if key in used_keys:
                continue
            fallback_score = max(0, 55 - len(normalized) * 5)
            normalized.append(fallback_candidate_clip(window, analysis, fallback_score))
            used_keys.add(key)

    return normalized


def find_candidate_by_window(
    start_value: Any,
    end_value: Any,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    start = parse_timestamp(start_value, prefer="first")
    end = parse_timestamp(end_value, prefer="last")
    if start is None or end is None:
        return None

    for candidate in candidates:
        candidate_start = parse_timestamp(candidate.get("start_timestamp"), prefer="first")
        candidate_end = parse_timestamp(candidate.get("end_timestamp"), prefer="last")
        if candidate_start is None or candidate_end is None:
            continue
        if (
            abs(candidate_start - start) <= TIMESTAMP_MATCH_TOLERANCE_SECONDS
            and abs(candidate_end - end) <= TIMESTAMP_MATCH_TOLERANCE_SECONDS
        ):
            return candidate

    return None


def empty_selected_clip(reason: str) -> dict[str, Any]:
    return {
        "start_timestamp": "",
        "end_timestamp": "",
        "duration_seconds": 0,
        "hook": "",
        "reason": reason,
    }


def normalize_selected_clip(
    raw: dict[str, Any],
    candidates: list[dict[str, Any]],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    raw_selected = raw.get("selected_clip")
    selected_candidate = None

    # First, honor an explicit valid selection from the model.
    if isinstance(raw_selected, dict):
        if raw_selected.get("start_timestamp") and raw_selected.get("end_timestamp"):
            selected_candidate = find_candidate_by_window(
                raw_selected.get("start_timestamp"),
                raw_selected.get("end_timestamp"),
                candidates,
            )

    # If the model's selected_clip was invalid or empty, try its
    # recommended timestamps.
    if selected_candidate is None:
        selected_candidate = find_candidate_by_window(
            raw.get("recommended_clip_start_timestamp"),
            raw.get("recommended_clip_end_timestamp"),
            candidates,
        )

    # Final fallback: use the highest-scoring validated candidate.
    # The ranker has already established these as the strongest windows.
    if selected_candidate is None and candidates:
        selected_candidate = max(
            candidates,
            key=lambda candidate: candidate.get("score") or 0,
        )

    if selected_candidate is None:
        reason = str(raw.get("no_viable_clip_reason", "")).strip()

        if isinstance(raw_selected, dict):
            reason = reason or str(
                raw_selected.get("reason", "")
            ).strip()

        reason = (
            reason
            or "No strong exact 60-second clip was selected."
        )

        return empty_selected_clip(reason)

    hook = str(selected_candidate.get("hook", "")).strip()

    if is_generic_hook(hook):
        hook = make_specific_hook(analysis, selected_candidate)

    return {
        "start_timestamp": selected_candidate["start_timestamp"],
        "end_timestamp": selected_candidate["end_timestamp"],
        "duration_seconds": selected_candidate["duration_seconds"],
        "hook": hook,
        "reason": str(selected_candidate.get("reason", "")).strip()
        or "Selected as the strongest validated candidate clip.",
    }


def validate_normalized_analysis(analysis: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    candidates = analysis.get("candidate_clips", [])
    if not isinstance(candidates, list) or len(candidates) < 3:
        issues.append("Fewer than 3 valid candidate_clips were returned.")

    selected = analysis.get("selected_clip", {})
    if not isinstance(selected, dict):
        issues.append("selected_clip is missing or malformed.")
        return issues

    duration = selected.get("duration_seconds")
    try:
        duration_float = float(duration)
    except (TypeError, ValueError):
        duration_float = 0

    if duration_float:
        if not (
            MIN_CLIP_SECONDS - CLIP_BOUNDARY_LEEWAY_SECONDS
            <= duration_float
            <= MAX_CLIP_SECONDS + CLIP_BOUNDARY_LEEWAY_SECONDS
        ):
            issues.append(
                f"selected_clip duration must be within {MIN_CLIP_SECONDS:g}-"
                f"{MAX_CLIP_SECONDS:g}s (+/-{CLIP_BOUNDARY_LEEWAY_SECONDS:.0f}s "
                "for sentence-boundary leeway)."
            )
        if is_generic_hook(str(selected.get("hook", ""))):
            issues.append("selected_clip hook is generic instead of specific.")
    elif not str(analysis.get("no_viable_clip_reason", "")).strip() and not str(
        selected.get("reason", "")
    ).strip():
        issues.append("No selected clip was provided and no no_viable_clip_reason was given.")

    return issues


def unwrap_model_response(raw: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw.get("analysis"), dict):
        nested = raw["analysis"]
        if any(key in nested for key in EXPECTED_ANALYSIS):
            return nested
    return raw


def normalize_analysis(
    raw: dict[str, Any],
    video: Path,
    transcript: Path,
    model: str,
    valid_windows: list[CandidateWindow],
    allow_fallback: bool = False,
) -> dict[str, Any]:
    raw = unwrap_model_response(raw)
    analysis = json.loads(json.dumps(EXPECTED_ANALYSIS))
    for key in EXPECTED_ANALYSIS:
        if key in raw:
            analysis[key] = raw[key]

    hooks = analysis.get("three_possible_shorts_hooks")
    if not isinstance(hooks, list):
        hooks = [str(hooks)] if hooks else []
    hooks = [str(hook).strip() for hook in hooks if str(hook).strip()]
    analysis["three_possible_shorts_hooks"] = hooks[:3]

    people = analysis.get("people_subjects")
    if not isinstance(people, list):
        people = [str(people)] if people else []
    analysis["people_subjects"] = [str(person).strip() for person in people if str(person).strip()]

    for moment_key in ("funniest_or_most_surprising_moment", "strongest_emotional_moment"):
        moment = analysis.get(moment_key)

        if not isinstance(moment, dict):
            moment = {
                "timestamp": "",
                "description": str(moment or "").strip(),
            }

        raw_timestamp = str(moment.get("timestamp", "")).strip()
        parsed_timestamp = parse_timestamp(raw_timestamp, prefer="first")

        if parsed_timestamp is not None:
            normalized_timestamp = format_timestamp(parsed_timestamp)
        else:
            normalized_timestamp = ""

        analysis[moment_key] = {
            "timestamp": normalized_timestamp,
            "description": str(moment.get("description", "")).strip(),
        }

    candidate_clips = normalize_candidate_clips(
        raw.get("candidate_clips"),
        valid_windows,
        analysis,
        allow_fallback=allow_fallback,
    )
    selected_clip = normalize_selected_clip(raw, candidate_clips, analysis)
    analysis["candidate_clips"] = candidate_clips
    analysis["selected_clip"] = selected_clip

    if selected_clip["duration_seconds"]:
        analysis["recommended_clip_start_timestamp"] = selected_clip["start_timestamp"]
        analysis["recommended_clip_end_timestamp"] = selected_clip["end_timestamp"]
        analysis["recommended_short_length_seconds"] = selected_clip["duration_seconds"]
        analysis["best_hook"] = selected_clip["hook"]
        analysis["no_viable_clip_reason"] = ""
    else:
        reason = str(raw.get("no_viable_clip_reason", "")).strip() or selected_clip["reason"]
        analysis["recommended_clip_start_timestamp"] = ""
        analysis["recommended_clip_end_timestamp"] = ""
        analysis["recommended_short_length_seconds"] = 0
        analysis["no_viable_clip_reason"] = reason

    hooks = [hook for hook in analysis["three_possible_shorts_hooks"] if not is_generic_hook(hook)]
    if selected_clip["hook"] and not is_generic_hook(selected_clip["hook"]):
        hooks.insert(0, selected_clip["hook"])
    analysis["three_possible_shorts_hooks"] = list(dict.fromkeys(hooks))[:3]
    if not analysis["three_possible_shorts_hooks"] and selected_clip["hook"]:
        analysis["three_possible_shorts_hooks"] = [selected_clip["hook"]]

    score = coerce_int(analysis.get("viral_potential_score"), minimum=0, maximum=100)
    analysis["viral_potential_score"] = score

    metadata = {
        "source_video": video.name,
        "source_video_path": str(video),
        "source_transcript": transcript.name,
        "source_transcript_path": str(transcript),
        "analyzer_backend": "ollama",
        "ollama_model": model,
        "valid_segment_aligned_windows_found": len(valid_windows),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return {"metadata": metadata, "analysis": analysis}


def write_analysis(root: Path, analysis: dict[str, Any]) -> Path:
    output_path = root / "output" / "analysis.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def print_ollama_setup_help(host: str, detail: str) -> None:
    log("")
    log("Ollama is not ready, so no clip analysis was generated.")
    log(detail)

    if shutil.which("ollama") is None:
        log("Install Ollama for Windows from https://ollama.com/download/windows")
    else:
        log("The Ollama command is installed, but the local server is not responding.")
        log("Start Ollama from the Windows app, or run: ollama serve")

    log("Then install a local model, for example: ollama pull llama3.1:8b")
    log(f"The analyzer is checking this Ollama host: {host}")


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ShortsFactory clip analyzer"
    )

    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="Explicit source video path supplied by the desktop app.",
    )

    parser.add_argument(
        "--transcript",
        type=str,
        default=None,
        help="Explicit Whisper transcript path supplied by the desktop app.",
    )

    parser.add_argument(
        "--clip-discovery-only",
        action="store_true",
        help=(
            "Run only the fast Shorts window-ranking pass and write the "
            "top candidates without the slower full creative analysis pass."
        ),
    )

    parser.add_argument(
        "--max-clips",
        type=int,
        default=DEFAULT_CLIP_COUNT,
        help=f"Number of ranked clip candidates to return (1-{MAX_CLIP_COUNT}).",
    )

    args = parser.parse_args()

    if bool(args.video) != bool(args.transcript):
        parser.error(
            "--video and --transcript must be supplied together."
        )

    return args


def resolve_cli_path(
    root: Path,
    value: str,
) -> Path:
    path = Path(value)

    if not path.is_absolute():
        path = root / path

    return path.resolve()


def load_explicit_input(
    root: Path,
    video_value: str,
    transcript_value: str,
) -> tuple[Path, Path, TranscriptData]:

    video = resolve_cli_path(
        root,
        video_value,
    )

    transcript_path = resolve_cli_path(
        root,
        transcript_value,
    )

    if not video.exists():
        raise TranscriptLoadError(
            f"Explicit source video does not exist: {video}"
        )

    if not transcript_path.exists():
        raise TranscriptLoadError(
            f"Explicit transcript does not exist: {transcript_path}"
        )

    transcript = load_transcript(
        transcript_path
    )

    return (
        video,
        transcript_path,
        transcript,
    )


def main() -> int:
    args = parse_cli_args()

    target_clip_count = max(
        1,
        min(
            MAX_CLIP_COUNT,
            int(args.max_clips),
        ),
    )

    root = project_root()
    log("ShortsFactory analyzer starting...")
    log(f"Project folder: {root}")

    if args.video and args.transcript:

        try:
            video, transcript_path, transcript = load_explicit_input(
                root,
                args.video,
                args.transcript,
            )
        except TranscriptLoadError as exc:
            log("")
            log(f"Could not load app-selected video/transcript: {exc}")
            return 1

        log("Using video and transcript supplied by the desktop app.")

    else:

        transcript_result = load_newest_transcript(root)

        if transcript_result is None:
            log("")
            log("No usable transcript was found.")
            log("Put a .txt or .json Whisper transcript next to the project, in input, data, or output.")
            log("The transcript filename should match a video in input, for example short1.json for input/short1.mp4.")
            return 1

        video, transcript_path, transcript = transcript_result
    log(f"Matched video: {video}")
    log(f"Transcript characters: {len(transcript.text)}")
    log(f"Timestamped transcript segments: {len(transcript.segments)}")
    log(f"Requested clip candidates: {target_clip_count}")
    valid_windows = generate_valid_windows(transcript.segments)
    log(f"Candidate clip windows across source: {len(valid_windows)}")

    host = normalize_ollama_host(os.environ.get("OLLAMA_HOST"))
    log(f"Checking Ollama at {host}...")
    models, ollama_error = get_ollama_models(host)
    if ollama_error:
        print_ollama_setup_help(host, ollama_error)
        return 1

    model, model_error = select_model(models)
    if model_error:
        print_ollama_setup_help(host, model_error)
        return 1

    assert model is not None
    log(f"Using Ollama model: {model}")

    try:
        log("Ranking exact one-minute transcript windows for Shorts...")
        ranked_windows = rank_exact_minute_windows(
            host,
            model,
            transcript,
            valid_windows,
            target_clip_count,
        )

        if len(ranked_windows) < target_clip_count:
            log(
                f"WARNING: Found {len(ranked_windows)} valid clip candidate(s) instead of "
                f"the requested {target_clip_count} (overlap filtering is never relaxed)."
            )

        log(f"Ranker selected {len(ranked_windows)} candidate windows.")

        if args.clip_discovery_only:
            log("Fast clip-discovery mode: skipping the slower second Ollama analysis pass.")

            # The dedicated ranker already selected the exact three windows the
            # desktop editor needs. Build a complete, normalized analysis file
            # directly from those authoritative windows instead of sending the
            # entire transcript through Ollama a second time.
            raw_analysis = json.loads(
                json.dumps(EXPECTED_ANALYSIS)
            )

            ranked_candidate_clips = candidate_clips_from_ranked_windows(
                ranked_windows,
                raw_analysis,
                segments=transcript.segments,
            )

            ranked_candidate_clips = ranked_candidate_clips[
                :target_clip_count
            ]

            raw_analysis["candidate_clips"] = ranked_candidate_clips

            for candidate in ranked_candidate_clips:
                for line in format_clip_log_lines(candidate, raw_analysis):
                    log(line)

            if ranked_candidate_clips:
                best_ranked_clip = ranked_candidate_clips[0]

                raw_analysis["selected_clip"] = {
                    "start_timestamp": best_ranked_clip["start_timestamp"],
                    "end_timestamp": best_ranked_clip["end_timestamp"],
                    "duration_seconds": best_ranked_clip["duration_seconds"],
                    "hook": best_ranked_clip.get("hook", ""),
                    "reason": best_ranked_clip.get("reason", ""),
                }

                raw_analysis["recommended_clip_start_timestamp"] = (
                    best_ranked_clip["start_timestamp"]
                )
                raw_analysis["recommended_clip_end_timestamp"] = (
                    best_ranked_clip["end_timestamp"]
                )
                raw_analysis["recommended_short_length_seconds"] = (
                    best_ranked_clip["duration_seconds"]
                )
                raw_analysis["best_hook"] = best_ranked_clip.get("hook", "")
                raw_analysis["viral_potential_score"] = best_ranked_clip.get(
                    "score",
                    0,
                )

            analysis = normalize_analysis(
                raw_analysis,
                video,
                transcript_path,
                model,
                valid_windows,
            )

            analysis["metadata"]["analysis_mode"] = "clip_discovery_only"
            analysis["metadata"]["ranked_candidate_count"] = len(
                ranked_candidate_clips
            )

            output_path = write_analysis(root, analysis)

            if ranked_candidate_clips:
                log(
                    "Fast clip discovery complete: "
                    f"{best_ranked_clip['start_timestamp']} -> "
                    f"{best_ranked_clip['end_timestamp']} is currently ranked #1."
                )
            else:
                log("Fast clip discovery completed without a valid clip candidate.")
            log(f"Analysis saved to: {output_path}")
            log("Done.")
            return 0

        instructions = load_analyzer_instructions(root)

        prompt = build_prompt(
            instructions,
            video,
            transcript_path,
            transcript,
            valid_windows,
            ranked_windows=ranked_windows,
        )

        log("Sending transcript to local analyzer...")
        raw_analysis = call_ollama(host, model, prompt)

        ranked_candidate_clips = candidate_clips_from_ranked_windows(
            ranked_windows,
            raw_analysis,
            segments=transcript.segments,
        )

        if len(ranked_candidate_clips) >= 3:
            raw_analysis["candidate_clips"] = ranked_candidate_clips

            # The ranker is authoritative for clip selection.
            # The LLM may analyze the ranked windows, but it does not
            # get to override which window was selected.
            best_ranked_clip = ranked_candidate_clips[0]
            raw_analysis["selected_clip"] = {
                "start_timestamp": best_ranked_clip["start_timestamp"],
                "end_timestamp": best_ranked_clip["end_timestamp"],
                "duration_seconds": best_ranked_clip["duration_seconds"],
                "hook": best_ranked_clip.get("hook", ""),
                "reason": best_ranked_clip.get("reason", ""),
            }

            raw_analysis["recommended_clip_start_timestamp"] = (
                best_ranked_clip["start_timestamp"]
            )
            raw_analysis["recommended_clip_end_timestamp"] = (
                best_ranked_clip["end_timestamp"]
            )
            raw_analysis["recommended_short_length_seconds"] = (
                best_ranked_clip["duration_seconds"]
            )

            log(
                f"Ranker selected {len(ranked_candidate_clips)} candidate windows; "
                f"authoritative selection: "
                f"{best_ranked_clip['start_timestamp']} -> "
                f"{best_ranked_clip['end_timestamp']}"
            )
        else:
            log("Ranker did not return 3 valid windows; using analyzer candidates.")

        analysis = normalize_analysis(
            raw_analysis,
            video,
            transcript_path,
            model,
            valid_windows,
        )
        validation_issues = validate_normalized_analysis(analysis["analysis"])
        if validation_issues:
            log("Analyzer response needed a stricter retry:")
            for issue in validation_issues:
                log(f"- {issue}")
            retry_prompt = build_prompt(
                instructions,
                video,
                transcript_path,
                transcript,
                valid_windows,
                ranked_windows=ranked_windows,
                validation_feedback=validation_issues,
            )
            raw_analysis = call_ollama(host, model, retry_prompt)
            analysis = normalize_analysis(raw_analysis, video, transcript_path, model, valid_windows)
            validation_issues = validate_normalized_analysis(analysis["analysis"])
            if validation_issues:
                log("Final analyzer response still had validation notes; safe post-processing was applied.")
                for issue in validation_issues:
                    log(f"- {issue}")
                analysis = normalize_analysis(
                    raw_analysis,
                    video,
                    transcript_path,
                    model,
                    valid_windows,
                    allow_fallback=True,
                )
                validation_issues = validate_normalized_analysis(analysis["analysis"])
                if validation_issues:
                    log("Safe post-processing notes:")
                    for issue in validation_issues:
                        log(f"- {issue}")
        output_path = write_analysis(root, analysis)
    except RuntimeError as exc:
        log("")
        log(f"Analysis failed: {exc}")
        log("Try a different local model with: set OLLAMA_MODEL=model-name")
        return 1

    log(f"Analysis saved to: {output_path}")
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
