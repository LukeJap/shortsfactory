"""
B2 -- per-segment narration asset generation/caching on top of
OrpheusProvider. Writes one WAV per recap_script.json segment (VO_001.wav,
VO_002.wav, ... -- never one combined multi-minute file), skips
regenerating a segment whose text/voice/speed haven't changed since last
time, and supports forcing exactly one segment to regenerate.

"Manual script/voice edits must become authoritative" (shared contract)
falls out of the cache design rather than needing its own special case:
the cache key is a hash of the segment's own (text, voice, speed), so
editing any of those is a cache miss on its own, regardless of whether a
stale WAV is still sitting on disk from before the edit.

Failures here never raise past the public functions -- a segment that
fails to synthesize (Orpheus offline, network error, invalid audio, ...)
comes back as a SegmentSynthesisResult with .error set, the same
non-fatal "log it, don't block everything else" pattern the rest of
ShortsFactory's optional AI stages already use.
"""

from __future__ import annotations

import hashlib
import json
import wave
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pipeline_paths import RECAP_DIR
from recap_media.orpheus_provider import (
    DEFAULT_VOICE,
    OrpheusError,
    OrpheusProvider,
)

VOICEOVER_DIR = RECAP_DIR / "voiceover"
MANIFEST_PATH = VOICEOVER_DIR / "voiceover_manifest.json"


@dataclass(frozen=True)
class SegmentSynthesisResult:
    segment_id: str
    wav_path: Path | None
    duration_seconds: float
    cache_hit: bool
    error: str | None = None
    fatal: bool = False


def wav_path_for_segment(segment_id: str, output_dir: Path = VOICEOVER_DIR) -> Path:
    return output_dir / f"{segment_id}.wav"


def load_voiceover_durations(manifest_path: Path = MANIFEST_PATH) -> dict[str, float]:
    """
    {segment_id: duration_seconds} for every segment with a cached
    manifest entry (real Orpheus-measured durations, not estimates) --
    lets recap_media.sequence.assemble_sequence() use accurate timing
    for whatever's already been synthesized without requiring every
    segment to be generated first (falls back to its own word-count
    estimate for any segment missing here).
    """

    manifest = _load_manifest(manifest_path)
    durations: dict[str, float] = {}

    for segment_id, entry in manifest.items():
        if not isinstance(entry, dict) or "duration_seconds" not in entry:
            continue
        try:
            durations[segment_id] = float(entry["duration_seconds"])
        except (TypeError, ValueError):
            continue

    return durations


def _content_hash(text: str, voice: str, speed: float) -> str:

    payload = json.dumps(
        {"text": text, "voice": voice, "speed": speed},
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_manifest(manifest: dict[str, Any], path: Path) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _wav_duration_seconds(path: Path) -> float:

    with closing(wave.open(str(path), "rb")) as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate() or 1
        return frames / float(rate)


def _is_fatal_provider_error(message: str) -> bool:
    """Whether another local Orpheus request would only worsen the failure."""

    normalized = message.casefold()
    return any(
        marker in normalized
        for marker in (
            "timed out",
            "could not reach orpheus",
            "returned http",
            "did not return valid wav",
            "returned empty audio",
        )
    )


def synthesize_segment(
    provider: OrpheusProvider,
    segment_id: str,
    text: str,
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
    output_dir: Path = VOICEOVER_DIR,
    manifest_path: Path = MANIFEST_PATH,
    force: bool = False,
) -> SegmentSynthesisResult:
    """
    Generate (or reuse) the narration WAV for one segment.

    Cache hit requires all three: the manifest has an entry for
    segment_id, that entry's content hash matches (text, voice, speed)
    exactly, and the WAV file it points at still exists on disk. Any
    mismatch is a cache miss and regenerates, even if a stale WAV is
    still sitting there. force=True always regenerates this one segment
    regardless of cache state, without touching any other segment's
    cache entry -- this is what a "regenerate one segment" GUI action
    should call.

    A cache hit never calls Orpheus at all, so already-synthesized
    segments keep working even while Orpheus-FastAPI is offline; only a
    genuinely new/changed segment needs the server actually running.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = wav_path_for_segment(segment_id, output_dir)
    content_hash = _content_hash(text, voice, speed)

    manifest = _load_manifest(manifest_path)
    entry = manifest.get(segment_id)

    if (
        not force
        and isinstance(entry, dict)
        and entry.get("content_hash") == content_hash
        and wav_path.exists()
    ):
        return SegmentSynthesisResult(
            segment_id=segment_id,
            wav_path=wav_path,
            duration_seconds=float(entry.get("duration_seconds", 0.0)),
            cache_hit=True,
        )

    try:
        audio_bytes = provider.synthesize_speech(text, voice=voice, speed=speed)
    except OrpheusError as exc:
        message = str(exc)
        return SegmentSynthesisResult(
            segment_id=segment_id,
            wav_path=None,
            duration_seconds=0.0,
            cache_hit=False,
            error=message,
            fatal=_is_fatal_provider_error(message),
        )

    wav_path.write_bytes(audio_bytes)
    duration_seconds = _wav_duration_seconds(wav_path)

    manifest[segment_id] = {
        "content_hash": content_hash,
        "voice": voice,
        "speed": speed,
        "duration_seconds": duration_seconds,
        "wav_path": str(wav_path),
    }
    _save_manifest(manifest, manifest_path)

    return SegmentSynthesisResult(
        segment_id=segment_id,
        wav_path=wav_path,
        duration_seconds=duration_seconds,
        cache_hit=False,
    )


def synthesize_segments(
    provider: OrpheusProvider,
    segments: list[dict[str, Any]],
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
    output_dir: Path = VOICEOVER_DIR,
    manifest_path: Path = MANIFEST_PATH,
    force_segment_ids: frozenset[str] = frozenset(),
    on_segment_start: Callable[[str, int, int], None] | None = None,
    on_segment_complete: Callable[[SegmentSynthesisResult, int, int], None] | None = None,
) -> list[SegmentSynthesisResult]:
    """
    Synthesize every narration-bearing segment from a loaded
    recap_script.json's "segments" list (see
    recap_media.loader.load_recap_script()). Schema-v2 source_moment
    blocks are source audio, not narration, and are skipped. Schema-v1
    keeps its existing visual_only behavior.

    Segments are intentionally dispatched one at a time for the local
    single-worker Orpheus backend. Each success reaches the WAV and manifest
    before the next request begins. A fatal provider failure, including a
    timeout that can leave the backend busy, stops the batch so we do not add
    doomed requests to its queue. Cached segments remain reusable next time.
    """

    narration_segments = [
        segment
        for segment in segments
        if not (
            segment.get("block_type") == "source_moment"
            or segment.get("presentation_hint") == "visual_only"
        )
    ]
    results = []
    total = len(narration_segments)

    for index, segment in enumerate(narration_segments, start=1):

        segment_id = segment["segment_id"]
        if on_segment_start is not None:
            on_segment_start(segment_id, index, total)
        result = synthesize_segment(
            provider,
            segment_id,
            segment["text"],
            voice=voice,
            speed=speed,
            output_dir=output_dir,
            manifest_path=manifest_path,
            force=segment_id in force_segment_ids,
        )
        results.append(result)
        if on_segment_complete is not None:
            on_segment_complete(result, index, total)
        if result.fatal:
            break

    return results
