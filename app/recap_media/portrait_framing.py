"""
B7 -- Recap Mode's portrait presentation: unlike normal ShortsFactory's
crop-to-fill (render.py's render_base_video(), which deliberately crops
overhanging edges to fill 1080x1920 edge-to-edge), Recap Mode contains the
full *active source picture* in the foreground and fills the remaining canvas
with a blurred, enlarged copy of that same picture. A conservative detector
removes only stable baked side pillars before the composition. This is useful
for 4:3 TV/cartoon material stored inside a wider encoded canvas.

This module is entirely additive and Recap-Mode-only: render.py's own
render_base_video()/crop_to_fill_filter() are never called or modified
by anything here, so normal (non-recap) rendering is completely
unaffected. Reuses render.py's ffprobe_source_dimensions()/
content_rect_for_source() (pure helpers already built for exactly this
"fit within the canvas, don't crop" math and deliberately kept in place
after normal rendering switched to crop-to-fill) rather than
reimplementing the same geometry.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
from pipeline_paths import RECAP_PORTRAIT_PLAN_PATH
from recap_media.loader import RecapInputError
from render import content_rect_for_source, ffprobe_source_dimensions

PORTRAIT_PLAN_SCHEMA_VERSION = 2
PILLARBOX_DETECTION_VERSION = 1

# Eight widely spaced samples make the decision about the source file rather
# than any one shot. The detector deliberately accepts only large, symmetric,
# persistently near-black side regions; weak evidence falls back to the full
# encoded frame.
PILLARBOX_SAMPLE_POSITIONS = (0.08, 0.20, 0.32, 0.44, 0.56, 0.68, 0.80, 0.92)
NEAR_BLACK_LUMA = 20
MIN_PILLARBOX_FRACTION = 0.06
MAX_PILLARBOX_FRACTION = 0.25
MIN_ACTIVE_WIDTH_FRACTION = 0.58
MIN_CENTER_LUMA_PERCENTILE = 48
MIN_CONSENSUS_SAMPLES = 3

# ffmpeg gblur's sigma -- higher = blurrier. 25 is a fairly strong blur,
# appropriate for a background element nobody is meant to look directly
# at (the point is filling space around the real content, not being
# legible itself).
DEFAULT_BLUR_SIGMA = 25.0

# 0.0-1.0 additional darkening of the blurred background (via eq=
# brightness=-X) so it recedes behind the foreground content a bit more.
# Default 0.0 (no darkening) -- the spec only asks for "blurred/
# enlarged", not dimmed; exposed as a knob rather than baked in as an
# opinionated default beyond what was actually requested.
DEFAULT_BACKGROUND_DIM = 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_portrait_filter_chain(
    content_x: int,
    content_y: int,
    content_width: int,
    content_height: int,
    canvas_width: int = OUTPUT_WIDTH,
    canvas_height: int = OUTPUT_HEIGHT,
    blur_sigma: float = DEFAULT_BLUR_SIGMA,
    background_dim: float = DEFAULT_BACKGROUND_DIM,
    input_label: str = "0:v",
    active_rect: dict[str, int] | None = None,
) -> str:
    """
    Build the ffmpeg filter_complex fragment: split the source into a
    background copy (scaled to cover the full canvas, cropped, blurred,
    optionally dimmed) and a foreground copy (scaled to exactly
    content_width x content_height -- the full active picture at the largest
    size that still fits the canvas), then overlay the
    foreground onto the background at (content_x, content_y). Reads
    input_label (default the raw first input "0:v"; B9's render pass
    points this at its concatenated shots track instead); outputs
    [recap_out].
    """

    background_dim = _clamp(background_dim, 0.0, 1.0)

    background_chain = (
        f"scale={canvas_width}:{canvas_height}:force_original_aspect_ratio=increase,"
        f"crop={canvas_width}:{canvas_height},"
        f"gblur=sigma={blur_sigma:.2f}"
    )
    if background_dim > 0:
        background_chain += f",eq=brightness={-background_dim:.3f}"

    source_label = input_label
    crop_prefix = ""
    if active_rect:
        crop_x = int(active_rect["x"])
        crop_y = int(active_rect["y"])
        crop_width = int(active_rect["width"])
        crop_height = int(active_rect["height"])
        if crop_width <= 0 or crop_height <= 0 or crop_x < 0 or crop_y < 0:
            raise ValueError("Portrait active_rect must be a non-negative, non-empty rectangle.")
        source_label = "recap_active_src"
        crop_prefix = (
            f"[{input_label}]crop={crop_width}:{crop_height}:{crop_x}:{crop_y}"
            f"[{source_label}];"
        )

    return (
        crop_prefix
        + f"[{source_label}]split=2[recap_bg_src][recap_fg_src];"
        f"[recap_bg_src]{background_chain}[recap_bg];"
        f"[recap_fg_src]scale={content_width}:{content_height}[recap_fg];"
        f"[recap_bg][recap_fg]overlay={content_x}:{content_y}[recap_out]"
    )


def _full_active_rect(source_width: int, source_height: int) -> dict[str, int]:
    return {"x": 0, "y": 0, "width": source_width, "height": source_height}


def _even_floor(value: int) -> int:
    return max(0, int(value) - (int(value) % 2))


def _normalized_active_rect(
    active_rect: dict[str, Any] | None,
    source_width: int,
    source_height: int,
) -> dict[str, int]:
    """Clamp and even-round a source crop without ever expanding it."""

    full_rect = _full_active_rect(source_width, source_height)
    if source_width <= 0 or source_height <= 0 or not isinstance(active_rect, dict):
        return full_rect

    try:
        x = max(0, int(active_rect["x"]))
        y = max(0, int(active_rect["y"]))
        width = int(active_rect["width"])
        height = int(active_rect["height"])
    except (KeyError, TypeError, ValueError):
        return full_rect

    x = _even_floor(min(x, source_width - 1))
    y = _even_floor(min(y, source_height - 1))
    width = _even_floor(min(width, source_width - x))
    height = _even_floor(min(height, source_height - y))
    if width < 2 or height < 2:
        return full_rect
    return {"x": x, "y": y, "width": width, "height": height}


def _black_side_run(column_luma: np.ndarray) -> int:
    run = 0
    for value in column_luma:
        if float(value) <= NEAR_BLACK_LUMA:
            run += 1
        else:
            break
    return run


def _candidate_active_rect_for_frame(frame: np.ndarray) -> dict[str, int] | None:
    """Return a conservative pillarbox candidate for one decoded frame."""

    if frame is None or frame.ndim not in (2, 3):
        return None
    height, width = frame.shape[:2]
    if width < 64 or height < 64:
        return None

    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame

    # Ignore likely fades and all-black frames. A real active picture must
    # show some meaningful luminance in its central region before side pixels
    # are allowed to imply a crop.
    vertical_margin = max(1, height // 20)
    sampled_rows = gray[vertical_margin : height - vertical_margin : max(1, height // 360), :]
    if sampled_rows.size == 0:
        return None
    column_luma = np.percentile(sampled_rows, 90, axis=0)
    left = _black_side_run(column_luma)
    right = _black_side_run(column_luma[::-1])

    minimum_bar = math.ceil(width * MIN_PILLARBOX_FRACTION)
    maximum_bar = math.floor(width * MAX_PILLARBOX_FRACTION)
    if not (minimum_bar <= left <= maximum_bar and minimum_bar <= right <= maximum_bar):
        return None
    if abs(left - right) > max(12, round(width * 0.02)):
        return None

    center = gray[:, left : width - right]
    if center.size == 0 or float(np.percentile(center, 90)) < MIN_CENTER_LUMA_PERCENTILE:
        return None

    active_width = width - left - right
    if active_width < round(width * MIN_ACTIVE_WIDTH_FRACTION):
        return None
    return _normalized_active_rect(
        {"x": left, "y": 0, "width": active_width, "height": height}, width, height
    )


def detect_pillarbox_active_rect_from_frames(
    frames: list[np.ndarray],
    source_width: int,
    source_height: int,
) -> dict[str, Any]:
    """Find a stable active rectangle from several decoded source frames.

    The return value always includes a full-frame fallback. This makes the
    caller fail safe when a source is dark, unevenly pillarboxed, or cannot be
    sampled reliably.
    """

    full_rect = _full_active_rect(source_width, source_height)
    candidates = [
        candidate
        for frame in frames
        if (candidate := _candidate_active_rect_for_frame(frame)) is not None
    ]
    required = max(MIN_CONSENSUS_SAMPLES, math.ceil(len(frames) / 2))
    if len(candidates) < required:
        return {
            "active_rect": full_rect,
            "pillarbox_detected": False,
            "detection_version": PILLARBOX_DETECTION_VERSION,
            "confidence": 0.0,
            "method": "sampled_luma_consensus",
            "sample_count": len(frames),
            "candidate_count": len(candidates),
            "consensus_count": 0,
        }

    median_x = int(round(float(np.median([candidate["x"] for candidate in candidates]))))
    median_width = int(round(float(np.median([candidate["width"] for candidate in candidates]))))
    tolerance = max(12, round(source_width * 0.02))
    consensus = [
        candidate
        for candidate in candidates
        if abs(candidate["x"] - median_x) <= tolerance
        and abs(candidate["width"] - median_width) <= tolerance
    ]
    if len(consensus) < required or len(consensus) < math.ceil(len(candidates) * 0.75):
        return {
            "active_rect": full_rect,
            "pillarbox_detected": False,
            "detection_version": PILLARBOX_DETECTION_VERSION,
            "confidence": 0.0,
            "method": "sampled_luma_consensus",
            "sample_count": len(frames),
            "candidate_count": len(candidates),
            "consensus_count": len(consensus),
        }

    active_rect = _normalized_active_rect(
        {
            "x": int(round(float(np.median([candidate["x"] for candidate in consensus])))),
            "y": 0,
            "width": int(round(float(np.median([candidate["width"] for candidate in consensus])))),
            "height": source_height,
        },
        source_width,
        source_height,
    )
    if active_rect == full_rect:
        return {
            "active_rect": full_rect,
            "pillarbox_detected": False,
            "detection_version": PILLARBOX_DETECTION_VERSION,
            "confidence": 0.0,
            "method": "sampled_luma_consensus",
            "sample_count": len(frames),
            "candidate_count": len(candidates),
            "consensus_count": len(consensus),
        }
    return {
        "active_rect": active_rect,
        "pillarbox_detected": True,
        "detection_version": PILLARBOX_DETECTION_VERSION,
        "confidence": round(len(consensus) / len(frames), 3),
        "method": "sampled_luma_consensus",
        "sample_count": len(frames),
        "candidate_count": len(candidates),
        "consensus_count": len(consensus),
    }


def _sample_video_frames(source_path: Path) -> list[np.ndarray]:
    """Decode a small, distributed sample once for one source file."""

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        capture.release()
        return []
    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count <= 1:
            return []
        frames: list[np.ndarray] = []
        for position in PILLARBOX_SAMPLE_POSITIONS:
            capture.set(cv2.CAP_PROP_POS_FRAMES, round((frame_count - 1) * position))
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
        return frames
    finally:
        capture.release()


def source_identity_for_video(source_path: Path) -> dict[str, Any]:
    """A cheap, source-bound identity for cached portrait analysis."""

    resolved = source_path.expanduser().resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def detect_pillarbox_active_rect_for_video(
    source_path: Path,
    source_width: int,
    source_height: int,
) -> dict[str, Any]:
    return detect_pillarbox_active_rect_from_frames(
        _sample_video_frames(source_path), source_width, source_height
    )


def build_portrait_framing_plan(
    source_width: int,
    source_height: int,
    canvas_width: int = OUTPUT_WIDTH,
    canvas_height: int = OUTPUT_HEIGHT,
    blur_sigma: float = DEFAULT_BLUR_SIGMA,
    background_dim: float = DEFAULT_BACKGROUND_DIM,
    active_rect: dict[str, Any] | None = None,
    pillarbox_detection: dict[str, Any] | None = None,
    source_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Pure geometry + filter-string computation, no ffprobe/file access --
    see build_portrait_framing_plan_for_video() for the real,
    file-based entry point. Kept separate so the actual math is fully
    unit-testable without a real video file.
    """

    active_rect = _normalized_active_rect(active_rect, source_width, source_height)
    content_x, content_y, content_width, content_height = content_rect_for_source(
        active_rect["width"], active_rect["height"], canvas_width, canvas_height
    )
    crop_active_picture = active_rect != _full_active_rect(source_width, source_height)

    return {
        "schema_version": PORTRAIT_PLAN_SCHEMA_VERSION,
        "source_width": source_width,
        "source_height": source_height,
        "source_identity": source_identity,
        "active_rect": active_rect,
        "pillarbox_detection": pillarbox_detection
        or {
            "pillarbox_detected": False,
            "detection_version": PILLARBOX_DETECTION_VERSION,
            "confidence": 0.0,
            "method": "none",
            "sample_count": 0,
            "candidate_count": 0,
            "consensus_count": 0,
        },
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "content_x": content_x,
        "content_y": content_y,
        "content_width": content_width,
        "content_height": content_height,
        "blur_sigma": blur_sigma,
        "background_dim": background_dim,
        "filter_chain": build_portrait_filter_chain(
            content_x,
            content_y,
            content_width,
            content_height,
            canvas_width,
            canvas_height,
            blur_sigma,
            background_dim,
            active_rect=active_rect if crop_active_picture else None,
        ),
    }


def build_portrait_framing_plan_for_video(
    source_path: Path,
    canvas_width: int = OUTPUT_WIDTH,
    canvas_height: int = OUTPUT_HEIGHT,
    blur_sigma: float = DEFAULT_BLUR_SIGMA,
    background_dim: float = DEFAULT_BACKGROUND_DIM,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Probes source_path's real dimensions (ffprobe) and builds its
    portrait framing plan. Not unit-tested against a real video file --
    build_portrait_framing_plan() above is where the actual, fully
    tested geometry/filter logic lives; this is just the ffprobe glue."""

    source_identity = source_identity_for_video(source_path)
    if cache_path and cache_path.exists():
        try:
            cached_plan = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_plan = None
        if (
            isinstance(cached_plan, dict)
            and cached_plan.get("schema_version") == PORTRAIT_PLAN_SCHEMA_VERSION
            and cached_plan.get("source_identity") == source_identity
        ):
            return cached_plan

    source_width, source_height = ffprobe_source_dimensions(source_path)
    detection = detect_pillarbox_active_rect_for_video(
        source_path, source_width, source_height
    )
    return build_portrait_framing_plan(
        source_width,
        source_height,
        canvas_width,
        canvas_height,
        blur_sigma,
        background_dim,
        active_rect=detection["active_rect"],
        pillarbox_detection=detection,
        source_identity=source_identity,
    )


def write_portrait_framing_plan(
    plan: dict[str, Any],
    path: Path = RECAP_PORTRAIT_PLAN_PATH,
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def load_portrait_framing_plan(path: Path = RECAP_PORTRAIT_PLAN_PATH) -> dict[str, Any]:

    if not path.exists():
        raise RecapInputError(f"portrait_framing_plan.json not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecapInputError(
            f"portrait_framing_plan.json is not valid JSON ({path}): {exc}"
        ) from exc

    if not isinstance(data, dict) or "filter_chain" not in data:
        raise RecapInputError(
            f"portrait_framing_plan.json is missing required field 'filter_chain' ({path})"
        )

    return data
