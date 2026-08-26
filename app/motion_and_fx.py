"""
Combines smart_motion.py's punch-in zoompan and visual_fx.py's semantic
momentary FX into a single ffmpeg pass. The always-on base picture polish
is applied earlier in render.py's STEP 1 so this pass does not add another
whole-clip baseline color grade before captions and overlays.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from .visual_emphasis import (
        DEFAULT_ENERGY,
        content_rect_from_settings,
        load_render_settings,
        normalize_energy,
    )
except ImportError:
    from visual_emphasis import (
        DEFAULT_ENERGY,
        content_rect_from_settings,
        load_render_settings,
        normalize_energy,
    )

try:
    from .canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
except ImportError:
    from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH

try:
    from .pipeline_paths import SUBTITLES_PATH as TRANSCRIPT_PATH
except ImportError:
    from pipeline_paths import SUBTITLES_PATH as TRANSCRIPT_PATH

try:
    from .smart_motion import (
        apply_shot_aware_zoom_strength,
        build_motion_events,
        classify_motion_shots,
        detect_motion_scene_cuts,
        enrich_motion_events,
        load_json,
        probe_video,
        write_motion_plan,
        x_expression,
        y_expression,
        zoom_expression,
    )
except ImportError:
    from smart_motion import (
        apply_shot_aware_zoom_strength,
        build_motion_events,
        classify_motion_shots,
        detect_motion_scene_cuts,
        enrich_motion_events,
        load_json,
        probe_video,
        write_motion_plan,
        x_expression,
        y_expression,
        zoom_expression,
    )

try:
    from .visual_fx import (
        build_semantic_filter_chain,
        build_semantic_moments,
        coerce_fx_intensity,
        expand_moments_to_events,
        merge_motion_events,
        motion_events_for_moments,
        write_plan as write_fx_plan,
    )
except ImportError:
    from visual_fx import (
        build_semantic_filter_chain,
        build_semantic_moments,
        coerce_fx_intensity,
        expand_moments_to_events,
        merge_motion_events,
        motion_events_for_moments,
        write_plan as write_fx_plan,
    )


ROOT = Path(__file__).resolve().parent.parent

VIDEO_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_tight.mp4"
)

TEMP_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_motion_fx_tmp.mp4"
)


def main() -> int:

    print(
        "ShortsFactory motion + visual FX pass starting...",
        flush=True,
    )

    if not VIDEO_PATH.exists():
        print(
            f"WARNING: Tight video does not exist: {VIDEO_PATH}",
            flush=True,
        )
        return 0

    settings = load_render_settings()
    edit_energy = normalize_energy(
        settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )
    intensity = coerce_fx_intensity(
        settings.get(
            "fx_intensity",
            1.0,
        )
    )
    filters_enabled = bool(
        settings.get(
            "filters_enabled",
            True,
        )
    )
    content_rect = content_rect_from_settings(
        settings
    )

    print(
        f"Edit energy: {edit_energy}",
        flush=True,
    )
    print(
        f"FX intensity: {intensity:.2f}",
        flush=True,
    )
    print(
        f"Filters: {'ON' if filters_enabled else 'OFF'}",
        flush=True,
    )

    transcript = load_json(
        TRANSCRIPT_PATH
    )
    words = transcript.get(
        "words",
        [],
    )
    if not isinstance(
        words,
        list,
    ):
        words = []

    # --------------------------------------------------------
    # FX analysis (mirrors visual_fx.py::main(), but production rendering
    # now applies only momentary semantic FX in this pass).
    # --------------------------------------------------------

    moments, intensity_curve = build_semantic_moments(
        words,
        edit_energy,
    )
    fx_events = expand_moments_to_events(
        moments,
        edit_energy,
    )

    write_fx_plan(
        edit_energy,
        fx_events,
        moments,
        intensity_curve,
    )

    print(
        f"Semantic moments selected: {len(moments)}",
        flush=True,
    )
    print(
        f"Dynamic FX events selected: {len(fx_events)}",
        flush=True,
    )

    for index, event in enumerate(
        fx_events,
        start=1,
    ):
        print(
            (
                f"FX {index}: "
                f"{event['start']:.2f}s -> "
                f"{event['end']:.2f}s, "
                f"{event['effect']}, "
                f"trigger={event.get('trigger_word', '')}, "
                f"recipe={event.get('recipe', '')}, "
                f"stack={event.get('stack_id', '')}"
            ),
            flush=True,
        )

    # --------------------------------------------------------
    # Motion analysis (mirrors smart_motion.py::main() -- skips
    # cleanly, same as it always has, when there's no transcript or
    # the video can't be probed; the FX pass above still applies
    # either way).
    # --------------------------------------------------------

    fps = 30.0
    motion_events: list = []
    zoompan_fragment = ""

    if not words:
        print(
            "No word timestamps available; skipping smart motion.",
            flush=True,
        )
    else:
        try:
            duration, fps = probe_video()
        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            print(
                f"WARNING: Could not inspect video for smart motion: {exc}",
                flush=True,
            )
            duration = 0.0

        if duration > 0:

            scene_cuts = detect_motion_scene_cuts(
                duration
            )

            print(
                f"Scene cuts available to motion editor: {len(scene_cuts)}",
                flush=True,
            )

            shot_types = classify_motion_shots(
                duration
            )

            if shot_types:
                summary = ", ".join(
                    f"{shot.get('shot', '?')}:{shot.get('type', 'unknown')}"
                    for shot in shot_types
                )
                print(
                    f"Shot types available to motion editor: {summary}",
                    flush=True,
                )

            fallback_motion_events = build_motion_events(
                words,
                duration,
                scene_cuts,
                edit_energy,
            )
            fallback_motion_events = apply_shot_aware_zoom_strength(
                fallback_motion_events,
                shot_types,
                edit_energy,
            )
            fallback_motion_events = enrich_motion_events(
                fallback_motion_events,
                duration,
                edit_energy,
            )
            recipe_motion_events = motion_events_for_moments(
                moments,
                duration,
                edit_energy,
            )
            recipe_motion_events = apply_shot_aware_zoom_strength(
                recipe_motion_events,
                shot_types,
                edit_energy,
            )
            motion_events = merge_motion_events(
                recipe_motion_events,
                fallback_motion_events,
                edit_energy,
            )

            write_motion_plan(
                duration,
                fps,
                edit_energy,
                scene_cuts,
                shot_types,
                motion_events,
            )

            print(
                (
                    "Motion events selected: "
                    f"{len(motion_events)} "
                    f"({len(recipe_motion_events)} recipe, "
                    f"{len(motion_events) - len(recipe_motion_events)} fallback)"
                ),
                flush=True,
            )

            for index, event in enumerate(
                motion_events,
                start=1,
            ):
                print(
                    (
                        f"Motion {index}: "
                        f"{event['start']:.2f}s -> "
                        f"{event['end']:.2f}s, "
                        f"{event['zoom']:.2f}x, "
                        f"{event.get('movement', 'punch_in')}, "
                        f"trigger={event['trigger_word']}, "
                        f"source={event.get('source', 'smart_motion_fallback')}"
                    ),
                    flush=True,
                )

            if not motion_events:
                print(
                    "No useful motion moments found; leaving video unchanged.",
                    flush=True,
                )
            else:
                zoom = zoom_expression(
                    motion_events,
                    fps,
                )
                x = x_expression(
                    motion_events,
                    fps,
                )
                y = y_expression(
                    motion_events,
                    fps,
                )
                zoompan_fragment = (
                    f"zoompan=z='{zoom}':"
                    f"x='{x}':"
                    f"y='{y}':"
                    "d=1:"
                    f"s={content_rect[2]}x{content_rect[3]}:"
                    f"fps={fps:.6f}"
                )

    # --------------------------------------------------------
    # One combined ffmpeg pass: crop -> [zoompan ->] fx chain -> pad.
    # --------------------------------------------------------

    (
        content_x,
        content_y,
        content_width,
        content_height,
    ) = content_rect

    # The FX plan above is always analyzed/written regardless of the
    # toggle -- same "hide the render, don't lose the plan" rule the
    # Filters toggle uses everywhere else -- but the filter chain itself
    # is only folded into this pass's ffmpeg command when filters are on.
    # Smart motion (zoompan_fragment) is a separate concern and is never
    # affected by this toggle.
    fx_chain = (
        build_semantic_filter_chain(
            fx_events,
            intensity,
            edit_energy,
        )
        if filters_enabled
        else ""
    )

    inner_chain = ",".join(
        part for part in (zoompan_fragment, fx_chain) if part
    )

    filter_string = (
        f"crop={content_width}:{content_height}:"
        f"{content_x}:{content_y}"
        + (f",{inner_chain}" if inner_chain else "")
        + f",pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
        f"{content_x}:{content_y}:black"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(
            VIDEO_PATH
        ),
        "-vf",
        filter_string,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        # Intermediate stage -- this output gets re-encoded again by
        # later pipeline stages before delivery.
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-shortest",
        str(
            TEMP_PATH
        ),
    ]

    print(
        "",
        flush=True,
    )
    print(
        "Applying combined motion + visual FX pass...",
        flush=True,
    )

    try:
        subprocess.run(
            command,
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:

        if TEMP_PATH.exists():
            try:
                TEMP_PATH.unlink()
            except OSError:
                pass

        print(
            (
                "WARNING: Motion + visual FX FFmpeg pass failed "
                f"with exit code {exc.returncode}. "
                "Continuing with current footage."
            ),
            flush=True,
        )

        return 0

    os.replace(
        TEMP_PATH,
        VIDEO_PATH,
    )

    print(
        "Motion + visual FX applied.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
