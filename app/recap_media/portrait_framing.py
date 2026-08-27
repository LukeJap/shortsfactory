"""
B7 -- Recap Mode's portrait presentation: unlike normal ShortsFactory's
crop-to-fill (render.py's render_base_video(), which deliberately crops
overhanging edges to fill 1080x1920 edge-to-edge), Recap Mode contains
the *entire* source frame in the foreground -- nothing is ever cropped
out of what the viewer sees -- and fills the remaining canvas with a
blurred, enlarged copy of the same source as a background. Especially
useful for 4:3 TV/cartoon material, where crop-to-fill would otherwise
lose a lot of picture on the left/right edges.

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
from pathlib import Path
from typing import Any

from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
from pipeline_paths import RECAP_PORTRAIT_PLAN_PATH
from recap_media.loader import RecapInputError
from render import content_rect_for_source, ffprobe_source_dimensions

PORTRAIT_PLAN_SCHEMA_VERSION = 1

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
) -> str:
    """
    Build the ffmpeg filter_complex fragment: split the source into a
    background copy (scaled to cover the full canvas, cropped, blurred,
    optionally dimmed) and a foreground copy (scaled to exactly
    content_width x content_height -- the full, uncropped source at
    the largest size that still fits the canvas), then overlay the
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

    return (
        f"[{input_label}]split=2[recap_bg_src][recap_fg_src];"
        f"[recap_bg_src]{background_chain}[recap_bg];"
        f"[recap_fg_src]scale={content_width}:{content_height}[recap_fg];"
        f"[recap_bg][recap_fg]overlay={content_x}:{content_y}[recap_out]"
    )


def build_portrait_framing_plan(
    source_width: int,
    source_height: int,
    canvas_width: int = OUTPUT_WIDTH,
    canvas_height: int = OUTPUT_HEIGHT,
    blur_sigma: float = DEFAULT_BLUR_SIGMA,
    background_dim: float = DEFAULT_BACKGROUND_DIM,
) -> dict[str, Any]:
    """
    Pure geometry + filter-string computation, no ffprobe/file access --
    see build_portrait_framing_plan_for_video() for the real,
    file-based entry point. Kept separate so the actual math is fully
    unit-testable without a real video file.
    """

    content_x, content_y, content_width, content_height = content_rect_for_source(
        source_width, source_height, canvas_width, canvas_height
    )

    return {
        "schema_version": PORTRAIT_PLAN_SCHEMA_VERSION,
        "source_width": source_width,
        "source_height": source_height,
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
        ),
    }


def build_portrait_framing_plan_for_video(
    source_path: Path,
    canvas_width: int = OUTPUT_WIDTH,
    canvas_height: int = OUTPUT_HEIGHT,
    blur_sigma: float = DEFAULT_BLUR_SIGMA,
    background_dim: float = DEFAULT_BACKGROUND_DIM,
) -> dict[str, Any]:
    """Probes source_path's real dimensions (ffprobe) and builds its
    portrait framing plan. Not unit-tested against a real video file --
    build_portrait_framing_plan() above is where the actual, fully
    tested geometry/filter logic lives; this is just the ffprobe glue."""

    source_width, source_height = ffprobe_source_dimensions(source_path)
    return build_portrait_framing_plan(
        source_width, source_height, canvas_width, canvas_height, blur_sigma, background_dim
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
