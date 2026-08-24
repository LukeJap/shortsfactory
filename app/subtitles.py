"""
Local Whisper transcription (STEP 2 and STEP 6 of the render pipeline --
transcribes the base clip, then again after cuts are applied) with a
content-hash-based cache (source_fingerprint()/cache_path_for_video(),
keyed on actual file bytes rather than mtime, since STEP 2/6 both write
to fixed, repeatedly-overwritten paths where mtime alone can never
usefully cache-hit). Also runs the post-transcription pipeline stages in
sequence: transcript corrections, temporal edit, smart motion, AI
visuals, visual FX (see main()'s maybe_apply_* calls). Following the
feature/LukeV2 merge, can alternatively reuse/remap the full-source
transcript through the applied cuts (--remap-through-cuts) instead of
re-running Whisper at all -- see remap_transcript_through_edit_plan().
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import whisper

try:
    from .pipeline_paths import (
        COMBINED_EDIT_PLAN_PATH,
        SUBTITLES_PATH as OUTPUT_PATH,
    )
except ImportError:
    from pipeline_paths import (
        COMBINED_EDIT_PLAN_PATH,
        SUBTITLES_PATH as OUTPUT_PATH,
    )


ROOT = Path(__file__).resolve().parent.parent

DEFAULT_VIDEO_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_base.mp4"
)

CACHE_DIR = (
    ROOT
    / "output"
    / "transcript_cache"
)

DEFAULT_QUALITY = "AUTO"
LANGUAGE = "en"

QUALITY_MODEL_CANDIDATES = {
    "FAST": [
        "base",
    ],
    "AUTO": [
        "small",
        "base",
    ],
    "ACCURATE": [
        "medium",
        "small",
        "base",
    ],
}


def normalize_quality(
    quality: str | None,
) -> str:

    normalized = str(
        quality
        or DEFAULT_QUALITY
    ).strip().upper()

    if normalized not in QUALITY_MODEL_CANDIDATES:
        return DEFAULT_QUALITY

    return normalized


def model_candidates_for_quality(
    quality: str,
) -> list[str]:

    override = os.environ.get(
        "SHORTSFACTORY_WHISPER_MODEL",
        "",
    ).strip()

    if override:
        return [
            override,
            "base",
        ]

    return list(
        QUALITY_MODEL_CANDIDATES[
            normalize_quality(
                quality
            )
        ]
    )


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Transcribe a ShortsFactory video with Whisper "
            "word timestamps and reusable transcript caching."
        )
    )

    parser.add_argument(
        "video",
        nargs="?",
        default=str(DEFAULT_VIDEO_PATH),
        help=(
            "Video to transcribe. Defaults to "
            "output/rendered/short1_base.mp4."
        ),
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force Whisper to transcribe even if a cached transcript exists.",
    )

    parser.add_argument(
        "--quality",
        choices=[
            "AUTO",
            "FAST",
            "ACCURATE",
        ],
        default=os.environ.get(
            "SHORTSFACTORY_TRANSCRIPTION_QUALITY",
            DEFAULT_QUALITY,
        ),
        type=normalize_quality,
        help="Transcription quality preset for local Whisper.",
    )

    parser.add_argument(
        "--selection-start",
        type=float,
        default=None,
        help=(
            "Optional source-video selection start in seconds. "
            "When paired with --selection-end, reuse the full-source "
            "transcript/cache and write selection-relative timestamps."
        ),
    )

    parser.add_argument(
        "--selection-end",
        type=float,
        default=None,
        help="Optional source-video selection end in seconds.",
    )

    parser.add_argument(
        "--remap-through-cuts",
        action="store_true",
        help=(
            "Reuse the current selection-relative subtitles.json and remap "
            "its timestamps through output/combined_edit_plan.json instead "
            "of running Whisper on short1_tight.mp4."
        ),
    )

    args = parser.parse_args()

    if (
        (args.selection_start is None)
        != (args.selection_end is None)
    ):
        parser.error(
            "--selection-start and --selection-end must be supplied together."
        )

    if (
        args.selection_start is not None
        and args.selection_end is not None
        and args.selection_end <= args.selection_start
    ):
        parser.error(
            "--selection-end must be greater than --selection-start."
        )

    return args


CONTENT_HASH_CHUNK_BYTES = 1024 * 1024


def content_sha256(video_path: Path) -> str:
    """
    Hash the actual video bytes rather than relying on mtime/size. Several
    pipeline stages (render_base_video(), apply_smart_edit.py) always
    write to the same fixed path (short1_base.mp4, short1_tight.mp4),
    rewriting it fresh on every render -- an mtime-based fingerprint can
    never hit for those paths even when two renders produce byte-identical
    output, since the act of producing the file always changes its mtime
    moments before the cache lookup. A real content hash is unaffected by
    that and still correctly misses when the content actually differs.
    Cheap in practice: ~0.4s for a 182MB source video, negligible next to
    a multi-second-or-longer Whisper transcription pass.
    """

    hasher = hashlib.sha256()

    with video_path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(CONTENT_HASH_CHUNK_BYTES),
            b"",
        ):
            hasher.update(chunk)

    return hasher.hexdigest()


def source_fingerprint(
    video_path: Path,
    quality: str,
    model_name: str,
) -> tuple[str, dict[str, Any]]:

    resolved = str(
        video_path.resolve()
    )

    identity = {
        "resolved_path": resolved,
        "content_sha256": content_sha256(
            video_path
        ),
        "engine": "openai-whisper",
        "quality": normalize_quality(
            quality
        ),
        "model": model_name,
        "compute": "fp32",
        "language": LANGUAGE,
    }

    digest_source = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
    ).encode(
        "utf-8"
    )

    digest = hashlib.sha256(
        digest_source
    ).hexdigest()[:24]

    return (
        digest,
        identity,
    )


def legacy_source_fingerprint(
    video_path: Path,
) -> tuple[str, dict[str, Any]]:

    stat = video_path.stat()

    resolved = str(
        video_path.resolve()
    )

    identity = {
        "resolved_path": resolved,
        "size_bytes": int(
            stat.st_size
        ),
        "modified_ns": int(
            stat.st_mtime_ns
        ),
        "model": "base",
        "language": LANGUAGE,
    }

    digest_source = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
    ).encode(
        "utf-8"
    )

    digest = hashlib.sha256(
        digest_source
    ).hexdigest()[:24]

    return (
        digest,
        identity,
    )


def cache_path_for_video(
    video_path: Path,
    quality: str,
    model_name: str,
) -> tuple[Path, dict[str, Any]]:

    digest, identity = source_fingerprint(
        video_path,
        quality,
        model_name,
    )

    cache_path = (
        CACHE_DIR
        / f"{digest}.json"
    )

    return (
        cache_path,
        identity,
    )


def legacy_cache_path_for_video(
    video_path: Path,
) -> tuple[Path, dict[str, Any]]:

    digest, identity = legacy_source_fingerprint(
        video_path
    )

    return (
        CACHE_DIR
        / f"{digest}.json",
        identity,
    )


def migrate_cached_transcript(
    cache_data: dict[str, Any],
    identity: dict[str, Any],
    quality: str,
    model_name: str,
) -> dict[str, Any]:

    migrated = dict(
        cache_data
    )
    migrated["engine"] = "openai-whisper"
    migrated["quality"] = normalize_quality(
        quality
    )
    migrated["model"] = model_name
    migrated["compute"] = "fp32"
    migrated["language"] = LANGUAGE
    migrated["cache_identity"] = identity
    return migrated


def read_json(
    path: Path,
) -> dict[str, Any] | None:

    try:

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
    ):

        return None

    if not isinstance(
        data,
        dict,
    ):
        return None

    return data


def cache_is_valid(
    cache_data: dict[str, Any],
    identity: dict[str, Any],
) -> bool:

    cached_identity = cache_data.get(
        "cache_identity"
    )

    if not isinstance(
        cached_identity,
        dict,
    ):
        return False

    return (
        cached_identity
        == identity
        and isinstance(
            cache_data.get(
                "segments"
            ),
            list,
        )
        and isinstance(
            cache_data.get(
                "words"
            ),
            list,
        )
    )


def slice_transcript_to_selection(
    data: dict[str, Any],
    selection_start: float,
    selection_end: float,
) -> dict[str, Any]:
    """Return a selection-relative view of a full-source transcript."""

    start = max(0.0, float(selection_start))
    end = max(start, float(selection_end))
    if end <= start:
        raise ValueError(
            "Transcript selection end must be after selection start."
        )

    def clip_word(raw_word: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw_word, dict):
            return None

        try:
            word_start = float(raw_word.get("start", 0.0))
            word_end = float(raw_word.get("end", word_start))
        except (TypeError, ValueError):
            return None

        if word_end <= start or word_start >= end:
            return None

        mapped_start = max(start, word_start) - start
        mapped_end = min(end, word_end) - start
        if mapped_end <= mapped_start:
            return None

        item = dict(raw_word)
        item["start"] = round(mapped_start, 4)
        item["end"] = round(mapped_end, 4)
        return item

    words: list[dict[str, Any]] = []
    raw_words = data.get("words", [])
    if isinstance(raw_words, list):
        for raw_word in raw_words:
            item = clip_word(raw_word)
            if item is not None:
                words.append(item)

    segments: list[dict[str, Any]] = []
    raw_segments = data.get("segments", [])
    if isinstance(raw_segments, list):
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue

            try:
                segment_start = float(raw_segment.get("start", 0.0))
                segment_end = float(
                    raw_segment.get("end", segment_start)
                )
            except (TypeError, ValueError):
                continue

            if segment_end <= start or segment_start >= end:
                continue

            segment_words: list[dict[str, Any]] = []
            raw_segment_words = raw_segment.get("words", [])
            if isinstance(raw_segment_words, list):
                for raw_word in raw_segment_words:
                    item = clip_word(raw_word)
                    if item is not None:
                        segment_words.append(item)

            if segment_words:
                mapped_start = float(segment_words[0]["start"])
                mapped_end = float(segment_words[-1]["end"])
            else:
                mapped_start = max(start, segment_start) - start
                mapped_end = min(end, segment_end) - start

            if mapped_end <= mapped_start:
                continue

            item = dict(raw_segment)
            item["start"] = round(mapped_start, 4)
            item["end"] = round(mapped_end, 4)
            item["words"] = segment_words
            if segment_words:
                item["text"] = " ".join(
                    str(word.get("word", "") or "").strip()
                    for word in segment_words
                    if str(word.get("word", "") or "").strip()
                )
            segments.append(item)

    output = dict(data)
    output["selection_start"] = round(start, 4)
    output["selection_end"] = round(end, 4)
    output["selection_duration_seconds"] = round(end - start, 4)
    output["timeline"] = "selection_relative"
    output["words"] = words
    output["segments"] = segments
    output["word_count"] = len(words)
    output["segment_count"] = len(segments)
    output["text"] = " ".join(
        str(word.get("word", "") or "").strip()
        for word in words
        if str(word.get("word", "") or "").strip()
    )
    return output


def normalized_keep_segments(
    plan: dict[str, Any],
) -> list[tuple[float, float]]:
    raw_segments = plan.get("keep_segments", [])
    if not isinstance(raw_segments, list):
        return []

    keeps: list[tuple[float, float]] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
        except (TypeError, ValueError):
            continue
        if end > start:
            keeps.append((start, end))

    keeps.sort(key=lambda item: item[0])
    return keeps


def map_interval_through_keeps(
    start: float,
    end: float,
    keeps: list[tuple[float, float]],
) -> tuple[float, float] | None:
    """
    Map a [start, end) interval from the original (pre-cut) timeline into
    where it lands on the concatenated (post-cut) timeline, given the list
    of retained [keep_start, keep_end) ranges in original-timeline order.

    Walks the keeps in order, accumulating how much retained duration has
    already been "consumed" before each one -- that running total is
    exactly the retained segment's start position on the concatenated
    timeline, since cutting and concatenating removes the gaps between
    kept ranges. If the interval overlaps more than one kept range (e.g.
    it straddles a cut), keeps only the single largest-overlap match
    rather than splitting it, since a caller here (a caption/emoji/word
    timing) needs one contiguous mapped interval, not several.
    """

    if end <= start:
        return None

    accumulated = 0.0
    best: tuple[float, float, float] | None = None

    for keep_start, keep_end in keeps:
        overlap_start = max(start, keep_start)
        overlap_end = min(end, keep_end)
        overlap = overlap_end - overlap_start

        if overlap > 0:
            mapped_start = accumulated + overlap_start - keep_start
            mapped_end = accumulated + overlap_end - keep_start
            if best is None or overlap > best[0]:
                best = (overlap, mapped_start, mapped_end)

        accumulated += keep_end - keep_start

    if best is None:
        return None

    return best[1], best[2]


def remap_timed_item_through_keeps(
    raw_item: dict[str, Any],
    keeps: list[tuple[float, float]],
) -> dict[str, Any] | None:
    """
    Apply map_interval_through_keeps() to one word/segment dict's
    start/end fields, returning a shallow copy with the mapped values (or
    None if the item falls entirely within a cut region and has nothing
    left to map).
    """
    if not isinstance(raw_item, dict):
        return None

    try:
        start = float(raw_item.get("start", 0.0))
        end = float(raw_item.get("end", start))
    except (TypeError, ValueError):
        return None

    mapped = map_interval_through_keeps(start, end, keeps)
    if mapped is None:
        return None

    mapped_start, mapped_end = mapped
    if mapped_end <= mapped_start:
        return None

    item = dict(raw_item)
    item["start"] = round(mapped_start, 4)
    item["end"] = round(mapped_end, 4)
    return item


def remap_transcript_through_edit_plan(
    data: dict[str, Any],
    plan: dict[str, Any],
    tight_video: Path,
) -> dict[str, Any]:
    """
    Rebuild an entire transcript (all words + segments) against the
    post-cut timeline by remapping every timed item through
    combined_edit_plan.json's keep_segments -- the --remap-through-cuts
    alternative to re-running Whisper on the tightened clip. Items that
    fall inside a cut are dropped entirely (remap_timed_item_through_keeps
    returns None for them); words are re-sorted by their new start time
    since a straddled interval's mapped position can reorder items that
    were adjacent pre-cut.
    """
    keeps = normalized_keep_segments(plan)

    if not keeps:
        try:
            duration = float(
                plan.get("original_duration_seconds", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            duration = 0.0

        if duration > 0:
            keeps = [(0.0, duration)]

    if not keeps:
        raise RuntimeError(
            "combined_edit_plan.json has no usable keep segments."
        )

    words: list[dict[str, Any]] = []
    raw_words = data.get("words", [])
    if isinstance(raw_words, list):
        for raw_word in raw_words:
            item = remap_timed_item_through_keeps(
                raw_word,
                keeps,
            )
            if item is not None:
                words.append(item)

    words.sort(
        key=lambda item: float(item.get("start", 0.0))
    )

    segments: list[dict[str, Any]] = []
    raw_segments = data.get("segments", [])
    if isinstance(raw_segments, list):
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue

            segment_words: list[dict[str, Any]] = []
            raw_segment_words = raw_segment.get("words", [])
            if isinstance(raw_segment_words, list):
                for raw_word in raw_segment_words:
                    item = remap_timed_item_through_keeps(
                        raw_word,
                        keeps,
                    )
                    if item is not None:
                        segment_words.append(item)

            if segment_words:
                item = dict(raw_segment)
                item["start"] = round(
                    float(segment_words[0]["start"]),
                    4,
                )
                item["end"] = round(
                    float(segment_words[-1]["end"]),
                    4,
                )
                item["words"] = segment_words
                item["text"] = " ".join(
                    str(word.get("word", "") or "").strip()
                    for word in segment_words
                    if str(word.get("word", "") or "").strip()
                )
                segments.append(item)
                continue

            item = remap_timed_item_through_keeps(
                raw_segment,
                keeps,
            )
            if item is not None:
                item["words"] = []
                segments.append(item)

    output = dict(data)
    output["source_video"] = tight_video.name
    output["source_video_path"] = str(tight_video.resolve())
    output["timeline"] = "tight_edit_relative"
    output["remapped_from_source_transcript"] = True
    output["keep_segment_count"] = len(keeps)
    output["words"] = words
    output["segments"] = segments
    output["word_count"] = len(words)
    output["segment_count"] = len(segments)
    output["text"] = " ".join(
        str(word.get("word", "") or "").strip()
        for word in words
        if str(word.get("word", "") or "").strip()
    )
    return output


def remap_current_transcript_after_smart_edit(
    tight_video: Path,
) -> dict[str, Any]:
    transcript = read_json(OUTPUT_PATH)
    if transcript is None:
        raise RuntimeError(
            "No selected transcript is available to remap."
        )

    plan = read_json(COMBINED_EDIT_PLAN_PATH)
    if plan is None:
        raise RuntimeError(
            "combined_edit_plan.json is unavailable."
        )

    return remap_transcript_through_edit_plan(
        transcript,
        plan,
        tight_video,
    )


def write_output(
    data: dict[str, Any],
) -> None:

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PATH.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def normalized_segments(
    whisper_segments: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:

    segments: list[
        dict[str, Any]
    ] = []

    words: list[
        dict[str, Any]
    ] = []

    for raw_segment in whisper_segments:

        if not isinstance(
            raw_segment,
            dict,
        ):
            continue

        segment_text = str(
            raw_segment.get(
                "text",
                "",
            )
            or ""
        ).strip()

        try:

            segment_start = float(
                raw_segment.get(
                    "start",
                    0.0,
                )
            )

            segment_end = float(
                raw_segment.get(
                    "end",
                    segment_start,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        if (
            segment_end
            <= segment_start
        ):
            continue

        segment_words: list[
            dict[str, Any]
        ] = []

        for raw_word in (
            raw_segment.get(
                "words",
                []
            )
            or []
        ):

            if not isinstance(
                raw_word,
                dict,
            ):
                continue

            word_text = str(
                raw_word.get(
                    "word",
                    "",
                )
                or ""
            ).strip()

            if not word_text:
                continue

            try:

                word_start = float(
                    raw_word.get(
                        "start",
                        segment_start,
                    )
                )

                word_end = float(
                    raw_word.get(
                        "end",
                        word_start,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

            probability = raw_word.get(
                "probability",
                0.0,
            )

            try:

                probability = float(
                    probability
                )

            except (
                TypeError,
                ValueError,
            ):

                probability = 0.0

            word_data = {
                "word": word_text,
                "start": word_start,
                "end": word_end,
                "probability": probability,
            }

            words.append(
                word_data
            )

            segment_words.append(
                word_data
            )

        segments.append(
            {
                "start": segment_start,
                "end": segment_end,
                "text": segment_text,
                "words": segment_words,
            }
        )

    return (
        segments,
        words,
    )


def transcribe(
    video_path: Path,
    identity: dict[str, Any],
    quality: str,
    model_name: str,
) -> dict[str, Any]:

    print(
        f"Loading Whisper model: {model_name} ({quality})",
        flush=True,
    )

    model = whisper.load_model(
        model_name
    )

    print(
        "Transcribing video with word-level timestamps...",
        flush=True,
    )

    result = model.transcribe(
        str(
            video_path
        ),
        language=LANGUAGE,
        word_timestamps=True,
        verbose=False,
        fp16=False,
    )

    raw_segments = result.get(
        "segments",
        [],
    )

    if not isinstance(
        raw_segments,
        list,
    ):

        raw_segments = []

    segments, words = normalized_segments(
        raw_segments
    )

    output = {
        "source_video": video_path.name,
        "source_video_path": str(
            video_path.resolve()
        ),
        "engine": "openai-whisper",
        "quality": normalize_quality(
            quality
        ),
        "model": model_name,
        "language": str(
            result.get(
                "language",
                LANGUAGE,
            )
            or LANGUAGE
        ),
        "text": str(
            result.get(
                "text",
                "",
            )
            or ""
        ).strip(),
        "word_count": len(
            words
        ),
        "segment_count": len(
            segments
        ),
        "words": words,
        "segments": segments,
        "cache_identity": identity,
    }

    return output


def transcribe_with_fallback(
    video_path: Path,
    quality: str,
) -> tuple[dict[str, Any], Path]:

    errors: list[str] = []

    for model_name in model_candidates_for_quality(
        quality
    ):

        cache_path, identity = cache_path_for_video(
            video_path,
            quality,
            model_name,
        )

        try:

            output = transcribe(
                video_path,
                identity,
                quality,
                model_name,
            )

            if errors:
                output[
                    "transcription_fallbacks"
                ] = errors

            return (
                output,
                cache_path,
            )

        except Exception as exc:

            message = (
                f"{model_name}: {exc}"
            )

            errors.append(
                message
            )

            if model_name != "base":

                print(
                    (
                        "WARNING: Whisper model failed; "
                        "trying lower-memory fallback. "
                        f"{message}"
                    ),
                    flush=True,
                )

                continue

            raise RuntimeError(
                "; ".join(
                    errors
                )
            ) from exc

    raise RuntimeError(
        "No Whisper transcription model succeeded."
    )




def maybe_apply_transcript_corrections(
    video_path: Path,
) -> None:
    """
    Apply user-fixed source transcript text after the tight clip has been
    re-transcribed, but before captions and smart-motion word analysis.
    """

    if video_path.name.lower() != "short1_tight.mp4":
        return

    script_path = (
        ROOT
        / "app"
        / "apply_transcript_corrections.py"
    )

    if not script_path.exists():
        return

    print(
        "",
        flush=True,
    )

    print(
        "Applying user transcript corrections...",
        flush=True,
    )

    try:
        subprocess.run(
            [
                sys.executable,
                str(script_path),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            (
                "WARNING: Transcript correction pass returned "
                f"exit code {exc.returncode}; "
                "continuing with Whisper text."
            ),
            flush=True,
        )



def maybe_apply_smart_motion(
    video_path: Path,
) -> None:
    """
    render.py already re-transcribes short1_tight.mp4 immediately before
    captions are generated. Hook the visual motion pass into that exact
    moment so timing stays unchanged and render.py does not need rewriting.
    """

    if video_path.name.lower() != "short1_tight.mp4":
        return

    script_path = (
        ROOT
        / "app"
        / "smart_motion.py"
    )

    if not script_path.exists():

        print(
            "Smart motion script not installed; continuing without punch-ins.",
            flush=True,
        )

        return

    print(
        "",
        flush=True,
    )

    print(
        "Applying automatic punch-in editing before captions...",
        flush=True,
    )

    try:

        subprocess.run(
            [
                sys.executable,
                str(
                    script_path
                ),
            ],
            cwd=ROOT,
            check=True,
        )

    except subprocess.CalledProcessError as exc:

        print(
            (
                "WARNING: Smart motion returned "
                f"exit code {exc.returncode}; "
                "continuing without blocking captions."
            ),
            flush=True,
        )



def maybe_apply_temporal_edit(
    video_path: Path,
) -> None:
    """
    Apply output-time manipulation after the final tight transcript exists.

    The temporal pass rewrites subtitles.json through its time map before
    smart motion, AI visuals, visual FX, captions, and emoji timing are built.
    """

    if video_path.name.lower() != "short1_tight.mp4":
        return

    script_path = (
        ROOT
        / "app"
        / "temporal_edit.py"
    )

    if not script_path.exists():
        return

    print(
        "",
        flush=True,
    )

    print(
        "Applying temporal speed/hold/replay editing...",
        flush=True,
    )

    try:

        subprocess.run(
            [
                sys.executable,
                str(
                    script_path
                ),
            ],
            cwd=ROOT,
            check=True,
        )

    except subprocess.CalledProcessError as exc:

        print(
            (
                "WARNING: Temporal edit returned "
                f"exit code {exc.returncode}; "
                "continuing without blocking captions."
            ),
            flush=True,
        )



def maybe_apply_ai_visuals(
    video_path: Path,
) -> None:
    """
    Composite any pre-generated AI visual assets into the final tight clip
    after smart motion, but before captions/emojis are rendered.
    """

    if video_path.name.lower() != "short1_tight.mp4":
        return

    script_path = (
        ROOT
        / "app"
        / "apply_ai_visuals.py"
    )

    if not script_path.exists():
        return

    print(
        "",
        flush=True,
    )

    print(
        "Applying planned AI visual cutaways...",
        flush=True,
    )

    try:

        subprocess.run(
            [
                sys.executable,
                str(
                    script_path
                ),
            ],
            cwd=ROOT,
            check=True,
        )

    except subprocess.CalledProcessError as exc:

        print(
            (
                "WARNING: AI visual compositor returned "
                f"exit code {exc.returncode}; "
                "continuing with source footage."
            ),
            flush=True,
        )


def maybe_apply_visual_fx(
    video_path: Path,
) -> None:
    """
    Apply the always-on base look plus dynamic visual FX before captions.
    Captions are burned later, so readability stays protected.
    """

    if video_path.name.lower() != "short1_tight.mp4":
        return

    script_path = (
        ROOT
        / "app"
        / "visual_fx.py"
    )

    if not script_path.exists():
        return

    print(
        "",
        flush=True,
    )

    print(
        "Applying visual grade and brainrot FX pass...",
        flush=True,
    )

    try:

        subprocess.run(
            [
                sys.executable,
                str(
                    script_path
                ),
            ],
            cwd=ROOT,
            check=True,
        )

    except subprocess.CalledProcessError as exc:

        print(
            (
                "WARNING: Visual FX pass returned "
                f"exit code {exc.returncode}; "
                "continuing with current footage."
            ),
            flush=True,
        )



def main() -> int:

    args = parse_args()

    video_path = Path(
        args.video
    ).expanduser()

    if not video_path.is_absolute():

        video_path = (
            ROOT
            / video_path
        )

    video_path = video_path.resolve()

    print(
        "ShortsFactory subtitle generator starting...",
        flush=True,
    )

    print(
        f"Project folder: {ROOT}",
        flush=True,
    )

    print(
        f"Input video: {video_path}",
        flush=True,
    )

    quality = normalize_quality(
        args.quality
    )

    print(
        f"Transcription quality: {quality}",
        flush=True,
    )

    if not video_path.exists():

        print(
            f"ERROR: Video not found: {video_path}",
            flush=True,
        )

        return 1

    if args.remap_through_cuts:
        print(
            "",
            flush=True,
        )
        print(
            "Reusing selected transcript after smart jump cuts.",
            flush=True,
        )
        print(
            "Skipping Whisper transcription for short1_tight.mp4.",
            flush=True,
        )

        try:
            output_for_run = remap_current_transcript_after_smart_edit(
                video_path
            )
        except Exception as exc:
            print(
                f"ERROR: Could not remap transcript through smart edits: {exc}",
                flush=True,
            )
            return 1

        write_output(
            output_for_run
        )

        print(
            (
                "Tight transcript remapped from "
                f"{output_for_run.get('keep_segment_count', 0)} "
                "retained segment(s)."
            ),
            flush=True,
        )
        print(
            f"Words retained: {len(output_for_run.get('words', []))}",
            flush=True,
        )
        print(
            f"Speech segments retained: {len(output_for_run.get('segments', []))}",
            flush=True,
        )

        maybe_apply_transcript_corrections(
            video_path
        )
        maybe_apply_temporal_edit(
            video_path
        )
        maybe_apply_smart_motion(
            video_path
        )
        maybe_apply_ai_visuals(
            video_path
        )
        maybe_apply_visual_fx(
            video_path
        )

        print(
            "Done.",
            flush=True,
        )
        return 0

    model_candidates = model_candidates_for_quality(
        quality
    )

    print(
        (
            "Whisper model candidates: "
            + ", ".join(
                model_candidates
            )
        ),
        flush=True,
    )

    try:

        cache_candidates = []
        for model_name in model_candidates:
            cache_path, identity = cache_path_for_video(
                video_path,
                quality,
                model_name,
            )
            cache_candidates.append(
                {
                    "path": cache_path,
                    "identity": identity,
                    "model": model_name,
                    "legacy": False,
                    "modern_path": cache_path,
                    "modern_identity": identity,
                }
            )

            if model_name == "base":
                legacy_path, legacy_identity = legacy_cache_path_for_video(
                    video_path
                )
                cache_candidates.append(
                    {
                        "path": legacy_path,
                        "identity": legacy_identity,
                        "model": model_name,
                        "legacy": True,
                        "modern_path": cache_path,
                        "modern_identity": identity,
                    }
                )

    except OSError as exc:

        print(
            f"ERROR: Could not inspect source video: {exc}",
            flush=True,
        )

        return 1

    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    stale_cache_seen = False

    if not args.no_cache:

        for candidate in cache_candidates:

            cache_path = candidate["path"]
            identity = candidate["identity"]

            cached = read_json(
                cache_path
            )

            if cached is not None and not cache_is_valid(
                cached,
                identity,
            ):
                stale_cache_seen = True

            if not (
                cached is not None
                and cache_is_valid(
                    cached,
                    identity,
                )
            ):
                continue

            if candidate["legacy"]:
                cached = migrate_cached_transcript(
                    cached,
                    candidate["modern_identity"],
                    quality,
                    candidate["model"],
                )
                try:
                    candidate["modern_path"].write_text(
                        json.dumps(
                            cached,
                            indent=2,
                            ensure_ascii=False,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    print(
                        "Migrated legacy base transcript cache to the current quality-aware cache.",
                        flush=True,
                    )
                except OSError as exc:
                    print(
                        f"WARNING: Could not migrate transcript cache: {exc}",
                        flush=True,
                    )

            print(
                "",
                flush=True,
            )

            print(
                "Transcript cache HIT.",
                flush=True,
            )

            print(
                "Skipping Whisper transcription for this unchanged video.",
                flush=True,
            )

            output_for_run = (
                slice_transcript_to_selection(
                    cached,
                    args.selection_start,
                    args.selection_end,
                )
                if (
                    args.selection_start is not None
                    and args.selection_end is not None
                )
                else cached
            )

            write_output(
                output_for_run
            )

            if (
                args.selection_start is not None
                and args.selection_end is not None
            ):
                print(
                    (
                        "Prepared selected transcript from cached source: "
                        f"{args.selection_start:.3f}s -> "
                        f"{args.selection_end:.3f}s."
                    ),
                    flush=True,
                )

            print(
                f"Cached transcript: {cache_path}",
                flush=True,
            )

            print(
                f"Subtitle data saved to: {OUTPUT_PATH}",
                flush=True,
            )

            print(
                f"Words detected: {len(output_for_run.get('words', []))}",
                flush=True,
            )

            print(
                f"Speech segments detected: {len(output_for_run.get('segments', []))}",
                flush=True,
            )

            maybe_apply_transcript_corrections(
                video_path
            )

            maybe_apply_temporal_edit(
                video_path
            )

            maybe_apply_smart_motion(
                video_path
            )

            maybe_apply_ai_visuals(
                video_path
            )

            maybe_apply_visual_fx(
                video_path
            )

            print(
                "Done.",
                flush=True,
            )

            return 0

    print(
        "",
        flush=True,
    )

    print(
        "Transcript cache MISS.",
        flush=True,
    )

    if args.no_cache:

        print(
            "Cache bypass requested.",
            flush=True,
        )

    elif stale_cache_seen:

        print(
            (
                "A transcript cache file existed, but its source/model/quality "
                "identity did not match this run."
            ),
            flush=True,
        )
    else:

        print(
            (
                "No matching transcript cache file was found for this "
                "source/model/quality combination."
            ),
            flush=True,
        )

    try:

        output, cache_path = transcribe_with_fallback(
            video_path,
            quality,
        )

    except Exception as exc:

        print(
            f"ERROR: Whisper transcription failed: {exc}",
            flush=True,
        )

        return 1

    output_for_run = (
        slice_transcript_to_selection(
            output,
            args.selection_start,
            args.selection_end,
        )
        if (
            args.selection_start is not None
            and args.selection_end is not None
        )
        else output
    )

    write_output(
        output_for_run
    )

    try:

        # Cache the complete source transcript. Selection slicing is
        # output-only so future selections can reuse the same Whisper result.
        cache_path.write_text(
            json.dumps(
                output,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    except OSError as exc:

        # Caching is an optimization; never fail the transcription
        # merely because the cache directory could not be written.
        print(
            f"WARNING: Could not save transcript cache: {exc}",
            flush=True,
        )

    if (
        args.selection_start is not None
        and args.selection_end is not None
    ):
        print(
            (
                "Prepared selected transcript from source transcript: "
                f"{args.selection_start:.3f}s -> "
                f"{args.selection_end:.3f}s."
            ),
            flush=True,
        )

    print(
        f"Subtitle data saved to: {OUTPUT_PATH}",
        flush=True,
    )

    print(
        f"Transcript cached at: {cache_path}",
        flush=True,
    )

    print(
        f"Words detected: {len(output_for_run['words'])}",
        flush=True,
    )

    print(
        f"Speech segments detected: {len(output_for_run['segments'])}",
        flush=True,
    )

    maybe_apply_transcript_corrections(
        video_path
    )

    maybe_apply_temporal_edit(
        video_path
    )

    maybe_apply_smart_motion(
        video_path
    )

    maybe_apply_ai_visuals(
        video_path
    )

    maybe_apply_visual_fx(
        video_path
    )

    print(
        "Done.",
        flush=True,
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
