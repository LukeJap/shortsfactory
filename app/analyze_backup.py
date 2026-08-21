from __future__ import annotations

from dataclasses import dataclass
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


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
TRANSCRIPT_EXTENSIONS = {".json", ".txt"}
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
REQUEST_TIMEOUT_SECONDS = 180
MIN_CLIP_SECONDS = 15
PREFERRED_MIN_CLIP_SECONDS = 20
PREFERRED_MAX_CLIP_SECONDS = 35
MAX_CLIP_SECONDS = 45
MAX_VALID_WINDOWS_FOR_PROMPT = 30
TIMESTAMP_MATCH_TOLERANCE_SECONDS = 0.05

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
)

EXPECTED_ANALYSIS: dict[str, Any] = {
    "main_topic": "",
    "people_subjects": [],
    "funniest_or_most_surprising_moment": {
        "timestamp": "",
        "description": "",
    },
    "strongest_emotional_moment": {
        "timestamp": "",
        "description": "",
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
    windows: list[CandidateWindow] = []
    for start_index, start_segment in enumerate(segments):
        text_parts: list[str] = []
        for end_index in range(start_index, len(segments)):
            end_segment = segments[end_index]
            text_parts.append(end_segment.text)
            duration = end_segment.end - start_segment.start
            if duration > MAX_CLIP_SECONDS:
                break
            if duration >= MIN_CLIP_SECONDS:
                windows.append(
                    CandidateWindow(
                        start=start_segment.start,
                        end=end_segment.end,
                        text=" ".join(text_parts),
                    )
                )

    if len(windows) <= MAX_VALID_WINDOWS_FOR_PROMPT:
        return windows

    def prompt_rank(window: CandidateWindow) -> tuple[float, float, float]:
        duration = window.duration_seconds
        if PREFERRED_MIN_CLIP_SECONDS <= duration <= PREFERRED_MAX_CLIP_SECONDS:
            duration_penalty = 0
        else:
            duration_penalty = min(
                abs(duration - PREFERRED_MIN_CLIP_SECONDS),
                abs(duration - PREFERRED_MAX_CLIP_SECONDS),
            )
        return (duration_penalty, abs(duration - 30), window.start)

    selected = sorted(windows, key=prompt_rank)[:MAX_VALID_WINDOWS_FOR_PROMPT]
    return sorted(selected, key=lambda window: (window.start, window.end))


def truncate_for_prompt(text: str, max_chars: int = 320) -> str:
    clean_text = " ".join(text.split())
    if len(clean_text) <= max_chars:
        return clean_text
    return clean_text[: max_chars - 3].rstrip() + "..."


def format_valid_windows_for_prompt(windows: list[CandidateWindow]) -> str:
    if not windows:
        return (
            "No valid 15-45 second timestamp-aligned windows were found. "
            "Return candidate_clips as an empty array and selected_clip with empty timestamps."
        )

    lines = []
    for index, window in enumerate(windows, start=1):
        lines.append(
            "W{index:03d} [{start} - {end}] {duration:.3f}s: {text}".format(
                index=index,
                start=format_timestamp(window.start),
                end=format_timestamp(window.end),
                duration=window.duration_seconds,
                text=truncate_for_prompt(window.text),
            )
        )

    return "\n".join(lines)


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
        "recommended_short_length_seconds": "number from 15 to 45, or 0 if no viable clip",
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
                "duration_seconds": "number from 15 to 45",
                "hook": "specific non-clickbait hook",
                "description": "string",
                "score": "integer from 0 to 100",
                "reason": "string",
            }
        ],
        "selected_clip": {
            "start_timestamp": "HH:MM:SS.mmm from one candidate clip, or empty string",
            "end_timestamp": "HH:MM:SS.mmm from the same candidate clip, or empty string",
            "duration_seconds": "number from 15 to 45, or 0",
            "hook": "specific non-clickbait hook, or empty string",
            "reason": "string",
        },
        "no_viable_clip_reason": "string, only populated if selected_clip is empty",
    }

    feedback_section = ""
    if validation_feedback:
        feedback_section = "\n".join(f"- {item}" for item in validation_feedback)

    prompt_sections = [
        instructions,
        "Return exactly one JSON object with this schema:",
        json.dumps(schema, indent=2),
        "Critical timing constraints:",
        (
            f"Every candidate and selected clip must be {MIN_CLIP_SECONDS}-{MAX_CLIP_SECONDS} seconds. "
            f"Strongly prefer {PREFERRED_MIN_CLIP_SECONDS}-{PREFERRED_MAX_CLIP_SECONDS} seconds. "
            "Never select the entire transcript unless the entire transcript is within the required duration."
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
            "'Here's what happened...', and 'The dark truth about...'. Hooks must name the actual subject "
            "and create a specific curiosity gap from the transcript."
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


def make_specific_hook(analysis: dict[str, Any], candidate: dict[str, Any] | None = None) -> str:
    candidate = candidate or {}
    curiosity_gap = str(analysis.get("strongest_curiosity_gap", "")).strip()
    description = str(candidate.get("description", "")).strip()
    topic = str(analysis.get("main_topic", "")).strip()
    subject = extract_hook_subject(description) if description else ""

    if subject and curiosity_gap:
        return f"{subject} turns this moment into a sharper question: {curiosity_gap}"
    if subject:
        return f"{subject} becomes the center of a short exchange with a clear setup and payoff"
    if curiosity_gap and topic:
        return f"{topic}: {curiosity_gap}"
    if curiosity_gap:
        return curiosity_gap
    if description and topic:
        return f"{topic}: {description}"
    if description:
        return "A tight segment-aligned exchange with a clearer payoff than the full transcript"
    if topic:
        return f"A focused short about {topic}"
    return "A specific transcript moment with a clear setup and payoff"


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
            abs(window.start - start) <= TIMESTAMP_MATCH_TOLERANCE_SECONDS
            and abs(window.end - end) <= TIMESTAMP_MATCH_TOLERANCE_SECONDS
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

    duration = window.duration_seconds
    if not MIN_CLIP_SECONDS <= duration <= MAX_CLIP_SECONDS:
        return None

    candidate = {
        "start_timestamp": format_timestamp(window.start),
        "end_timestamp": format_timestamp(window.end),
        "duration_seconds": round_duration(duration),
        "hook": str(raw_candidate.get("hook", "")).strip(),
        "description": str(raw_candidate.get("description", "")).strip(),
        "score": coerce_int(raw_candidate.get("score"), minimum=0, maximum=100),
        "reason": str(raw_candidate.get("reason", "")).strip(),
    }
    if is_generic_hook(candidate["hook"]):
        candidate["hook"] = make_specific_hook(analysis, candidate)
    if candidate["score"] is None:
        candidate["score"] = 0
    if not candidate["reason"]:
        candidate["reason"] = "Segment-aligned candidate that fits the required Shorts duration."
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
        "reason": (
            "Backup segment-aligned candidate added because the model did not return enough valid windows."
        ),
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
                abs(window.duration_seconds - 30),
                0
                if PREFERRED_MIN_CLIP_SECONDS
                <= window.duration_seconds
                <= PREFERRED_MAX_CLIP_SECONDS
                else 1,
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
    explicit_empty_selection = False

    if isinstance(raw_selected, dict):
        if not raw_selected.get("start_timestamp") or not raw_selected.get("end_timestamp"):
            explicit_empty_selection = True
        else:
            selected_candidate = find_candidate_by_window(
                raw_selected.get("start_timestamp"),
                raw_selected.get("end_timestamp"),
                candidates,
            )

    if selected_candidate is None and not explicit_empty_selection:
        selected_candidate = find_candidate_by_window(
            raw.get("recommended_clip_start_timestamp"),
            raw.get("recommended_clip_end_timestamp"),
            candidates,
        )

    if selected_candidate is None and candidates and not explicit_empty_selection:
        selected_candidate = max(candidates, key=lambda candidate: candidate.get("score") or 0)

    if selected_candidate is None:
        reason = str(raw.get("no_viable_clip_reason", "")).strip()
        if isinstance(raw_selected, dict):
            reason = reason or str(raw_selected.get("reason", "")).strip()
        reason = reason or "No strong 15-45 second segment-aligned clip was selected."
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
        if not MIN_CLIP_SECONDS <= duration_float <= MAX_CLIP_SECONDS:
            issues.append("selected_clip duration is outside the required 15-45 second range.")
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
            moment = {"timestamp": "", "description": str(moment or "")}
        analysis[moment_key] = {
            "timestamp": str(moment.get("timestamp", "")).strip(),
            "description": str(moment.get("description", "")).strip(),
        }

    analysis["copyright_reused_content_risk"] = normalize_copyright_risk(
        analysis.get("copyright_reused_content_risk")
    )

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


def main() -> int:
    root = project_root()
    log("ShortsFactory analyzer starting...")
    log(f"Project folder: {root}")

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
    valid_windows = generate_valid_windows(transcript.segments)
    log(f"Valid 15-45 second segment-aligned windows: {len(valid_windows)}")

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
        instructions = load_analyzer_instructions(root)
        prompt = build_prompt(instructions, video, transcript_path, transcript, valid_windows)
        log("Sending transcript to local analyzer...")
        raw_analysis = call_ollama(host, model, prompt)
        analysis = normalize_analysis(raw_analysis, video, transcript_path, model, valid_windows)
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
