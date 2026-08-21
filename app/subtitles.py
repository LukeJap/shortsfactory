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


ROOT = Path(__file__).resolve().parent.parent

DEFAULT_VIDEO_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_base.mp4"
)

OUTPUT_PATH = (
    ROOT
    / "output"
    / "subtitles.json"
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

    return parser.parse_args()


def source_fingerprint(
    video_path: Path,
    quality: str,
    model_name: str,
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

    model_candidates = model_candidates_for_quality(
        quality
    )

    print(
        f"Transcription quality: {quality}",
        flush=True,
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

    if not video_path.exists():

        print(
            f"ERROR: Video not found: {video_path}",
            flush=True,
        )

        return 1

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

            write_output(
                cached
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
                f"Words detected: {len(cached.get('words', []))}",
                flush=True,
            )

            print(
                f"Speech segments detected: {len(cached.get('segments', []))}",
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

    write_output(
        output
    )

    try:

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

    print(
        f"Subtitle data saved to: {OUTPUT_PATH}",
        flush=True,
    )

    print(
        f"Transcript cached at: {cache_path}",
        flush=True,
    )

    print(
        f"Words detected: {len(output['words'])}",
        flush=True,
    )

    print(
        f"Speech segments detected: {len(output['segments'])}",
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
