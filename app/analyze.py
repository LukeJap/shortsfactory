"""
The "Find Best Clips" front door: transcribes/ingests a full source video
and uses a local Ollama LLM to identify and score candidate short-form
clip windows (hooks, self-contained moments, pacing). Largest standalone
script in the pipeline. Discovers whichever Ollama model is actually
installed rather than assuming a fixed model name, unlike the other
Ollama-calling scripts (plan_short.py, content_edit.py, and semantic_edit.py)
-- that's intentional, not an inconsistency to fix.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import os
import re
import shutil
import sys
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
# Find Best Clips is intentionally a one-minute clip finder.  Keep the
# validation limits equal so every downstream candidate remains exact too.
MIN_CLIP_SECONDS = 60
PREFERRED_MIN_CLIP_SECONDS = 60
PREFERRED_MAX_CLIP_SECONDS = 60
EXTENDED_CLIP_MIN_SECONDS = 60
MAX_CLIP_SECONDS = 60
# A mechanical minute-boundary can land mid-sentence. Once a window is
# actually selected (not during ranking -- see generate_valid_windows()'s
# own docstring on why scoring stays on the fixed grid), nudge each edge
# to the nearest real transcript-segment boundary within this many
# seconds, so a clip starts/ends at a natural pause instead of cutting
# off a word or sentence. Final clips end up 60s +/- this amount, not
# perfectly exact -- an explicit, requested trade-off after live testing
# showed exact cuts routinely landed mid-sentence.
CLIP_BOUNDARY_LEEWAY_SECONDS = 4.0
MAX_VALID_WINDOWS_FOR_PROMPT = 30
WINDOW_STRIDE_SECONDS = 15
TIMESTAMP_MATCH_TOLERANCE_SECONDS = 0.05
# find_matching_window() re-identifies which mechanical valid_windows entry
# a candidate came from, purely to re-validate it in normalize_analysis().
# A candidate that went through the sentence-boundary leeway snap above can
# now be up to CLIP_BOUNDARY_LEEWAY_SECONDS away from that mechanical grid
# on either edge -- the tight TIMESTAMP_MATCH_TOLERANCE_SECONDS (meant for
# comparing untouched mechanical values to each other) made every snapped
# candidate fail this lookup and get silently dropped. WINDOW_STRIDE_SECONDS
# (15s) is comfortably larger than any realistic leeway value, so a small
# safety margin over the leeway still can't cross-match an adjacent window.
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


def generate_valid_windows(segments: list[TranscriptSegment]) -> list[CandidateWindow]:
    """Create exact one-minute candidates over the complete source timeline.

    Candidate boundaries deliberately do not depend on Whisper segment
    boundaries: a strong sixty-second scene should not become 52 seconds just
    because a transcript line ends there.  Transcript lines are only the
    evidence supplied to the ranker for that fixed source range.
    """
    if not segments:
        return []

    source_end = max(segment.end for segment in segments)
    latest_start = source_end - MAX_CLIP_SECONDS
    if latest_start < 0:
        return []

    starts: list[float] = []
    start = 0.0
    while start <= latest_start + TIMESTAMP_MATCH_TOLERANCE_SECONDS:
        starts.append(round(start, 3))
        start += WINDOW_STRIDE_SECONDS

    # Always assess the final full minute, even where the stride does not land
    # exactly on it. This prevents the ending from being accidentally omitted.
    tail_start = round(latest_start, 3)
    if not starts or abs(starts[-1] - tail_start) > TIMESTAMP_MATCH_TOLERANCE_SECONDS:
        starts.append(tail_start)

    windows: list[CandidateWindow] = []
    for window_start in starts:
        window_end = round(window_start + MAX_CLIP_SECONDS, 3)
        text = " ".join(
            segment.text
            for segment in segments
            if segment.end > window_start and segment.start < window_end
        )
        windows.append(CandidateWindow(start=window_start, end=window_end, text=text))

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
            "No valid exact 60-second timestamp-aligned windows were found. "
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
) -> dict[str, Any]:
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
                        "reason": {"type": "string"},
                    },
                },
            }
        },
    }


def call_ollama_window_ranker(
    host: str,
    model: str,
    prompt: str,
    *,
    window_ids: list[str],
    selection_count: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": window_ranking_json_schema(window_ids, selection_count),
        "options": {
            "temperature": 0,
            "top_p": 0.9,
        },
    }

    try:
        response = request_json(
            f"{host}/api/generate",
            payload=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except HTTPError as exc:
        raise RuntimeError(
            f"Ollama window-ranking request failed with HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Ollama stopped responding: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            "Ollama took too long to rank the clip candidates. Please try again."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Ollama returned invalid JSON for the window-ranking request."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Ollama window-ranking request failed: {exc}"
        ) from exc

    response_text = str(response.get("response", "")).strip()

    if not response_text:
        raise RuntimeError(
            "Ollama returned an empty window-ranking response."
        )

    try:
        return extract_json_object(response_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"The window-ranking response was not valid JSON: {exc}"
        ) from exc
def build_window_ranking_prompt(
    transcript: TranscriptData,
    valid_windows: list[CandidateWindow],
    target_clip_count: int = 3,
    excluded_window_ids: set[str] | None = None,
    response_count_override: int | None = None,
) -> str:
    excluded = excluded_window_ids or set()
    windows_text = format_valid_windows_for_prompt(valid_windows, excluded)
    source_context = transcript_context_for_window_ranking(transcript)
    response_count = window_ranking_response_count(
        valid_windows,
        target_clip_count,
        excluded,
        response_count_override,
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

Choose the {response_count} strongest potential exact 60-second Shorts.

A strong Short should:
- make sense with minimal context
- contain a specific event or payoff, not merely recognizable dialogue
- contain a mini-arc where possible: setup -> escalation/conflict -> punchline, reveal, or reaction
- prioritize a funny payoff, conflict, surprise, embarrassment, escalation, strong reaction, absurd situation, memorable exchange, or clear mini-story
- create curiosity
- have a natural beginning and ending
- avoid long stretches of filler conversation
- avoid generic statements
- work as a standalone exact 60-second clip
- use the supplied sixty seconds as a complete mini-story, not merely a setup before a payoff elsewhere

Penalize a candidate that only establishes the episode, introduces people or a
location, mostly contains greetings, ends before its payoff, or needs lots of
outside context. Treat opening themes, title sequences, credits, recaps,
sponsor reads, and other boilerplate as structural material, not strong Shorts,
unless that material itself contains a unique story event.

Score calibration:
- 90-100: exceptional standout moment; an obvious Short
- 80-89: strong complete scene or moment
- 70-79: usable but not special
- below 70: weaker but still usable backup material

Do not award 90+ to ordinary setup, a title/opening, or recognizable dialogue
without a concrete event or payoff.

Return ONLY this JSON structure:

{{
  "selections": [
    {{
      "window_id": "W001",
      "score": 95,
      "reason": "Concrete transcript detail that makes this window work."
    }},
    {{
      "window_id": "W002",
      "score": 88,
      "reason": "Concrete transcript detail that makes this window work."
    }},
    {{
      "window_id": "W003",
      "score": 82,
      "reason": "Concrete transcript detail that makes this window work."
    }}
  ]
}}

Rules:
1. Return exactly {response_count} different window IDs whenever that many eligible non-structural windows exist.
2. The IDs MUST come from the supplied windows.
3. Never invent an ID.
4. Do not return timestamps.
5. Do not return the transcript.
6. Do not return any additional fields.
7. Rank the strongest candidate first.
8. Prefer distinct, non-overlapping moments from different parts of the source when quality is similar.
9. Avoid returning multiple windows that cover essentially the same scene or conversation beat.
10. Reasons must mention concrete words, people, objects, places, or actions from that window.
11. Do not write phrases such as "clear setup and payoff", "engaging conversation", or "becomes the center of attention".
12. Every supplied candidate is exactly 60 seconds. Do not prefer one based on duration.
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

        score = coerce_int(selection.get("score"), minimum=0, maximum=100)
        if score is None:
            score = 0

        reason = str(selection.get("reason", "")).strip()
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
) -> list[tuple[CandidateWindow, int, str]]:
    """Select distinct source moments without returning overlapping clips."""

    selected: list[tuple[CandidateWindow, int, str]] = []

    def overlaps(
        first: CandidateWindow,
        second: CandidateWindow,
    ) -> bool:
        overlap = max(0.0, min(first.end, second.end) - max(first.start, second.start))
        return overlap > TIMESTAMP_MATCH_TOLERANCE_SECONDS

    for candidate in ranked_windows:
        window = candidate[0]
        if any(overlaps(window, chosen[0]) for chosen in selected):
            continue
        selected.append(candidate)
        if len(selected) >= target_clip_count:
            return selected

    return selected


def chronological_window_batches(
    windows: list[CandidateWindow],
    batch_size: int = MAX_VALID_WINDOWS_FOR_PROMPT,
) -> list[list[CandidateWindow]]:
    """Split a complete candidate timeline into prompt-sized chronological batches."""
    return [
        windows[index : index + batch_size]
        for index in range(0, len(windows), batch_size)
    ]


def rank_exact_minute_windows(
    host: str,
    model: str,
    transcript: TranscriptData,
    valid_windows: list[CandidateWindow],
    target_clip_count: int,
) -> list[tuple[CandidateWindow, int, str]]:
    """Rank all source regions, batching first only when one prompt is too large."""
    batches = chronological_window_batches(valid_windows)
    if not batches:
        return []

    if len(batches) == 1:
        rankable_windows = valid_windows
    else:
        # Keep a bounded final prompt while ensuring every chronological region
        # contributes a locally ranked contender.
        per_batch = max(1, min(target_clip_count, MAX_VALID_WINDOWS_FOR_PROMPT // len(batches)))
        shortlist: list[tuple[CandidateWindow, int, str]] = []
        for batch_number, batch in enumerate(batches, start=1):
            log(f"Ranking chronological candidate batch {batch_number}/{len(batches)}...")
            selection_count = min(len(batch), per_batch)
            batch_result = call_ollama_window_ranker(
                host,
                model,
                build_window_ranking_prompt(
                    transcript,
                    batch,
                    target_clip_count=selection_count,
                ),
                window_ids=[f"W{index:03d}" for index in range(1, len(batch) + 1)],
                selection_count=selection_count,
            )
            shortlist.extend(ranked_windows_from_result(batch_result, batch))

        # Batch winners can still overlap at a boundary.  Strictly collapse
        # them before the final pass so the final shortlist describes separate
        # moments from the whole source.
        shortlist = select_distinct_ranked_windows(
            sorted(shortlist, key=lambda candidate: (-candidate[1], candidate[0].start)),
            MAX_VALID_WINDOWS_FOR_PROMPT,
        )
        rankable_windows = [window for window, _score, _reason in shortlist]

    selection_count = min(
        len(rankable_windows),
        max(target_clip_count, target_clip_count * 3),
    )
    final_result = call_ollama_window_ranker(
        host,
        model,
        build_window_ranking_prompt(
            transcript,
            rankable_windows,
            target_clip_count=selection_count,
        ),
        window_ids=[f"W{index:03d}" for index in range(1, len(rankable_windows) + 1)],
        selection_count=selection_count,
    )
    return select_distinct_ranked_windows(
        ranked_windows_from_result(final_result, rankable_windows),
        target_clip_count,
    )

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

        candidate = {
            "start_timestamp": format_timestamp(window.start),
            "end_timestamp": format_timestamp(window.end),
            "duration_seconds": round_duration(window.duration_seconds),
            "hook": "",
            "description": description,
            "score": score,
            "reason": grounded_reason
            or grounded_reason_from_text(description),
        }

        candidate["hook"] = make_specific_hook(analysis, candidate)
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
        "description": str(raw_candidate.get("description", "")).strip(),
        "score": coerce_int(raw_candidate.get("score"), minimum=0, maximum=100),
        "reason": str(raw_candidate.get("reason", "")).strip(),
    }
    if has_generic_editor_language(candidate["description"]):
        candidate["description"] = truncate_for_prompt(window.text, 180)
    if is_generic_hook(candidate["hook"]):
        candidate["hook"] = make_specific_hook(analysis, candidate)
    if candidate["score"] is None:
        candidate["score"] = 0
    if has_generic_editor_language(candidate["reason"]):
        candidate["reason"] = grounded_reason_from_text(
            candidate["description"]
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
                "selected_clip duration must be close to 60 seconds "
                f"(+/-{CLIP_BOUNDARY_LEEWAY_SECONDS:.0f}s for sentence-boundary leeway)."
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
        default=3,
        help="Number of ranked clip candidates to return (1-6).",
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
            6,
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
    log(f"Valid exact 60-second windows across source: {len(valid_windows)}")

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
                f"the requested {target_clip_count}."
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
                log(
                    "Clip: {start} -> {end} | {duration:.1f}s | score {score} | {reason}".format(
                        start=candidate["start_timestamp"],
                        end=candidate["end_timestamp"],
                        duration=float(candidate["duration_seconds"]),
                        score=candidate.get("score", 0),
                        reason=candidate.get("reason", ""),
                    )
                )

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
