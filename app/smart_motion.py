from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .visual_emphasis import (
        DEFAULT_ENERGY,
        classify_word,
        content_rect_from_settings,
        energy_profile,
        load_render_settings,
        normalize_energy,
    )
except ImportError:
    from visual_emphasis import (
        DEFAULT_ENERGY,
        classify_word,
        content_rect_from_settings,
        energy_profile,
        load_render_settings,
        normalize_energy,
    )

try:
    from .canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
except ImportError:
    from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH


ROOT = Path(__file__).resolve().parent.parent

VIDEO_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_tight.mp4"
)

TRANSCRIPT_PATH = (
    ROOT
    / "output"
    / "subtitles.json"
)

PLAN_PATH = (
    ROOT
    / "output"
    / "smart_motion_plan.json"
)

MOTION_SCENE_PLAN = (
    ROOT
    / "output"
    / "motion_scene_plan.json"
)

MOTION_SHOT_PLAN = (
    ROOT
    / "output"
    / "shot_type_motion_plan.json"
)

TEMP_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_motion_tmp.mp4"
)

MIN_EVENT_SPACING = 4.75
EVENT_DURATION = 1.20
RAMP_SECONDS = 0.12
MAX_EVENTS = 4

EMPHASIS_WORDS = {
    "actually",
    "always",
    "but",
    "crazy",
    "different",
    "exactly",
    "first",
    "huge",
    "interesting",
    "literally",
    "never",
    "nothing",
    "old",
    "only",
    "really",
    "remember",
    "strange",
    "surprising",
    "totally",
    "weird",
    "why",
    "wild",
    "worst",
}


def clean_word(value: str) -> str:

    return (
        "".join(
            character
            for character in value.lower()
            if character.isalpha()
            or character == "'"
        )
    )


def load_json(
    path: Path,
) -> dict[str, Any]:

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

        return {}

    return (
        data
        if isinstance(
            data,
            dict,
        )
        else {}
    )


def probe_video() -> tuple[
    float,
    float,
]:

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate:format=duration",
        "-of",
        "json",
        str(
            VIDEO_PATH
        ),
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    data = json.loads(
        result.stdout
    )

    duration = float(
        data.get(
            "format",
            {},
        ).get(
            "duration",
            0.0,
        )
        or 0.0
    )

    streams = data.get(
        "streams",
        [],
    )

    fps = 30.0

    if streams:

        rate = str(
            streams[0].get(
                "avg_frame_rate",
                "30/1",
            )
            or "30/1"
        )

        try:

            numerator, denominator = (
                rate.split(
                    "/",
                    1,
                )
            )

            fps = (
                float(
                    numerator
                )
                / max(
                    0.000001,
                    float(
                        denominator
                    ),
                )
            )

        except (
            ValueError,
            ZeroDivisionError,
        ):

            fps = 30.0

    if (
        not math.isfinite(
            fps
        )
        or fps <= 0
    ):

        fps = 30.0

    return (
        duration,
        fps,
    )



def detect_motion_scene_cuts(
    duration: float,
) -> list[float]:

    scene_script = (
        ROOT
        / "app"
        / "scene_detect.py"
    )

    if not scene_script.exists():
        return []

    try:
        subprocess.run(
            [
                sys.executable,
                str(scene_script),
                "--video",
                str(VIDEO_PATH),
                "--start",
                "0",
                "--end",
                f"{duration:.3f}",
                "--output",
                str(MOTION_SCENE_PLAN),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    try:
        data = json.loads(
            MOTION_SCENE_PLAN.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return []

    cuts = data.get(
        "cuts",
        [],
    )

    if not isinstance(
        cuts,
        list,
    ):
        return []

    cleaned = []

    for value in cuts:

        try:
            numeric = float(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if 0.05 < numeric < duration - 0.05:
            cleaned.append(
                numeric
            )

    return sorted(
        cleaned
    )


def choose_event_count(
    duration: float,
    energy: str,
) -> int:

    max_events = int(
        energy_profile(
            energy
        ).get(
            "max_motion_events",
            MAX_EVENTS,
        )
    )

    if duration < 9:
        return 1

    if duration < 18:
        return min(
            2,
            max_events,
        )

    if duration < 30:
        return min(
            3,
            max_events,
        )

    return max_events


def candidate_score(
    words: list[dict[str, Any]],
    index: int,
    energy: str,
) -> float:

    item = words[index]

    raw_word = str(
        item.get(
            "word",
            "",
        )
        or ""
    )

    word = clean_word(
        raw_word
    )

    score = 0.0

    emphasis = classify_word(
        raw_word,
        energy,
    )

    if emphasis.get(
        "level"
    ) == "IMPACT":
        score += 6.0
    elif emphasis.get(
        "level"
    ) == "EMPHASIS":
        score += 3.0

    if word in EMPHASIS_WORDS:
        score += 5.0

    if raw_word.endswith(
        ("!", "?")
    ):
        score += 4.0

    if index == 0:
        score += 2.0

    if index > 0:

        try:

            gap = (
                float(
                    item.get(
                        "start",
                        0.0,
                    )
                )
                - float(
                    words[index - 1].get(
                        "end",
                        0.0,
                    )
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            gap = 0.0

        if gap >= 0.42:
            score += 2.75

    if len(
        word
    ) >= 8:
        score += 0.75

    return score


def classify_motion_shots(
    duration: float,
) -> list[dict[str, Any]]:

    script = ROOT / "app" / "shot_type.py"

    if not script.exists():
        return []

    try:
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--video",
                str(VIDEO_PATH),
                "--start",
                "0",
                "--end",
                f"{duration:.3f}",
                "--scene-plan",
                str(MOTION_SCENE_PLAN),
                "--output",
                str(MOTION_SHOT_PLAN),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    try:
        data = json.loads(
            MOTION_SHOT_PLAN.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return []

    shots = data.get("shots", [])
    return shots if isinstance(shots, list) else []


def apply_shot_aware_zoom_strength(
    events: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    energy: str,
) -> list[dict[str, Any]]:

    if normalize_energy(
        energy
    ) == "LOW":
        max_zoom = {
            "close_up": 1.035,
            "medium": 1.055,
            "wide": 1.075,
            "multi_person": 1.045,
        }
    elif normalize_energy(
        energy
    ) == "MAXIMUM":
        max_zoom = {
            "close_up": 1.06,
            "medium": 1.12,
            "wide": 1.17,
            "multi_person": 1.08,
        }
    else:
        max_zoom = {
            "close_up": 1.045,
            "medium": 1.085,
            "wide": 1.14,
            "multi_person": 1.055,
        }

    for event in events:
        t = float(event.get("start", 0.0))
        shot_type = "medium"

        for shot in shots:
            try:
                shot_start = float(shot.get("start", 0.0))
                shot_end = float(shot.get("end", shot_start))
            except (TypeError, ValueError):
                continue

            if shot_start <= t <= shot_end:
                shot_type = str(shot.get("type", "medium"))
                break

        original_zoom = float(event.get("zoom", 1.0))
        event["zoom"] = round(
            min(original_zoom, max_zoom.get(shot_type, 1.085)),
            3,
        )
        event["shot_type"] = shot_type

    return events


def build_motion_events(
    words: list[dict[str, Any]],
    duration: float,
    scene_cuts: list[float],
    energy: str,
) -> list[dict[str, Any]]:

    if (
        not words
        or duration <= 0
    ):
        return []

    profile = energy_profile(
        energy
    )

    target_count = choose_event_count(
        duration,
        energy,
    )

    min_event_spacing = float(
        profile.get(
            "motion_spacing",
            MIN_EVENT_SPACING,
        )
    )

    event_duration = float(
        profile.get(
            "motion_duration",
            EVENT_DURATION,
        )
    )

    zoom_levels = [
        float(
            value
        )
        for value in profile.get(
            "motion_zoom_levels",
            [
                1.10,
                1.14,
                1.09,
                1.12,
            ],
        )
    ]

    if not zoom_levels:
        zoom_levels = [
            1.10,
        ]

    target_count = min(
        target_count,
        len(words),
    )

    candidates = []

    for index, word in enumerate(
        words
    ):

        try:

            start = float(
                word.get(
                    "start",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        # Don't punch instantly at frame zero or during the final beat.
        if (
            start < 1.0
            or start
            > max(
                1.0,
                duration - 1.5,
            )
        ):
            continue

        # A real camera cut already supplies visual energy. Avoid stacking
        # a synthetic zoom directly on top of it.
        if any(
            abs(
                start
                - cut
            )
            < 0.80
            for cut in scene_cuts
        ):
            continue

        candidates.append(
            {
                "start": start,
                "score": candidate_score(
                    words,
                    index,
                    energy,
                ),
                "word": clean_word(
                    str(
                        word.get(
                            "word",
                            "",
                        )
                    )
                ),
            }
        )

    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["start"],
        )
    )

    selected: list[
        dict[str, Any]
    ] = []

    for candidate in candidates:

        if any(
            abs(
                candidate["start"]
                - event["start"]
            )
            < min_event_spacing
            for event in selected
        ):

            continue

        event_start = max(
            0.0,
            candidate["start"]
            - 0.10,
        )

        event_end = min(
            duration,
            event_start
            + event_duration,
        )

        selected.append(
            {
                "start": round(
                    event_start,
                    3,
                ),
                "end": round(
                    event_end,
                    3,
                ),
                "zoom": zoom_levels[
                    len(
                        selected
                    )
                    % len(
                        zoom_levels
                    )
                ],
                "trigger_word": (
                    candidate["word"]
                    or "speech_beat"
                ),
                "energy": normalize_energy(
                    energy
                ),
                "reason": (
                    "speech emphasis"
                    if candidate["score"] >= 4.0
                    else "pacing beat"
                ),
            }
        )

        if len(
            selected
        ) >= target_count:
            break

    # If keyword/gap scoring did not give us enough moments,
    # fill from evenly spaced speech positions.
    if (
        len(
            selected
        )
        < target_count
    ):

        for fraction in (
            0.22,
            0.44,
            0.66,
            0.82,
        ):

            desired = (
                duration
                * fraction
            )

            nearest = min(
                words,
                key=lambda item: abs(
                    float(
                        item.get(
                            "start",
                            desired,
                        )
                        or desired
                    )
                    - desired
                ),
            )

            start = float(
                nearest.get(
                    "start",
                    desired,
                )
                or desired
            )

            if any(
                abs(
                    start
                    - event["start"]
                )
                < min_event_spacing
                for event in selected
            ):

                continue

            if any(
                abs(
                    start
                    - cut
                )
                < 0.80
                for cut in scene_cuts
            ):

                continue

            event_start = max(
                0.0,
                start - 0.10,
            )

            selected.append(
                {
                    "start": round(
                        event_start,
                        3,
                    ),
                    "end": round(
                        min(
                            duration,
                            event_start
                            + event_duration,
                        ),
                        3,
                    ),
                    "zoom": zoom_levels[
                        len(
                            selected
                        )
                        % len(
                            zoom_levels
                        )
                    ],
                    "trigger_word": clean_word(
                        str(
                            nearest.get(
                                "word",
                                "",
                            )
                        )
                    )
                    or "speech_beat",
                    "energy": normalize_energy(
                        energy
                    ),
                    "reason": "pacing beat",
                }
            )

            if len(
                selected
            ) >= target_count:
                break

    selected.sort(
        key=lambda item: item[
            "start"
        ]
    )

    return selected


def motion_movement_for_event(
    event: dict[str, Any],
    index: int,
    energy: str,
) -> str:

    reason = str(
        event.get(
            "reason",
            "",
        )
    )
    trigger = str(
        event.get(
            "trigger_word",
            "",
        )
    )

    if normalize_energy(
        energy
    ) == "MAXIMUM":
        if trigger in {
            "crazy",
            "dead",
            "died",
            "insane",
            "ow",
            "what",
            "why",
        }:
            return "impact_jolt"

        if index % 4 == 1:
            return "hard_reframe_cut"

    if trigger in {
        "what",
        "who",
        "why",
        "reveal",
        "right",
    }:
        return "punch_out"

    if reason == "pacing beat" and index % 2 == 0:
        return "slow_push"

    if normalize_energy(
        energy
    ) == "PUNCHY" and index % 4 == 3:
        return "hard_reframe_cut"

    if index % 3 == 2:
        return "directional_push"

    return "punch_in"


def enrich_motion_events(
    events: list[dict[str, Any]],
    duration: float,
    energy: str,
) -> list[dict[str, Any]]:

    for index, event in enumerate(
        events
    ):
        movement = motion_movement_for_event(
            event,
            index,
            energy,
        )
        event["movement"] = movement

        if movement in {
            "impact_punch",
            "impact_jolt",
        }:
            event["zoom"] = round(
                min(
                    float(
                        event.get(
                            "zoom",
                            1.0,
                        )
                    )
                    + 0.035,
                    1.18
                    if normalize_energy(
                        energy
                    )
                    == "MAXIMUM"
                    else 1.145,
                ),
                3,
            )
            if movement == "impact_jolt":
                event["end"] = round(
                    min(
                        duration,
                        float(
                            event.get(
                                "start",
                                0.0,
                            )
                        )
                        + 0.34,
                    ),
                    3,
                )
                event["x_bias"] = (
                    -0.030
                    if index % 2 == 0
                    else 0.030
                )
                event["y_bias"] = (
                    -0.020
                    if index % 3 == 0
                    else 0.020
                )

        elif movement == "slow_push":
            event["end"] = round(
                min(
                    duration,
                    float(
                        event.get(
                            "start",
                            0.0,
                        )
                    )
                    + (
                        3.0
                        if normalize_energy(
                            energy
                        )
                        == "MAXIMUM"
                        else 2.35
                    ),
                ),
                3,
            )

        elif movement == "directional_push":
            event["x_bias"] = (
                -0.035
                if index % 2 == 0
                else 0.035
            )

        elif movement == "hard_reframe_cut":
            event["end"] = round(
                min(
                    duration,
                    float(
                        event.get(
                            "start",
                            0.0,
                        )
                    )
                    + (
                        0.72
                        if normalize_energy(
                            energy
                        )
                        == "MAXIMUM"
                        else 0.52
                    ),
                ),
                3,
            )
            event["zoom"] = round(
                min(
                    float(
                        event.get(
                            "zoom",
                            1.0,
                        )
                    )
                    + (
                        0.055
                        if normalize_energy(
                            energy
                        )
                        == "MAXIMUM"
                        else 0.025
                    ),
                    1.18
                    if normalize_energy(
                        energy
                    )
                    == "MAXIMUM"
                    else 1.12,
                ),
                3,
            )
            event["x_bias"] = (
                -0.045
                if index % 2 == 0
                else 0.045
            )

    return events


def zoom_expression(
    events: list[dict[str, Any]],
    fps: float,
) -> str:
    """
    Build a nested zoompan expression.

    Each event ramps in quickly, holds, then ramps out.
    Outside events, zoom = 1.0.
    """

    expression = "1"

    ramp_frames = max(
        2,
        int(
            round(
                RAMP_SECONDS
                * fps
            )
        ),
    )

    for event in reversed(
        events
    ):

        start_frame = int(
            round(
                event["start"]
                * fps
            )
        )

        end_frame = int(
            round(
                event["end"]
                * fps
            )
        )

        peak = float(
            event["zoom"]
        )
        movement = str(
            event.get(
                "movement",
                "punch_in",
            )
        )

        ramp_in_end = min(
            end_frame,
            start_frame
            + ramp_frames,
        )

        ramp_out_start = max(
            ramp_in_end,
            end_frame
            - ramp_frames,
        )

        ramp_in_denominator = max(
            1,
            ramp_in_end
            - start_frame,
        )

        ramp_out_denominator = max(
            1,
            end_frame
            - ramp_out_start,
        )

        if movement == "punch_out":
            event_expression = (
                f"if(between(on,{start_frame},{end_frame}),"
                f"1+({peak}-1)*({end_frame}-on)/max(1,{end_frame - start_frame}),"
                f"{expression})"
            )

        elif movement == "slow_push":
            event_expression = (
                f"if(between(on,{start_frame},{end_frame}),"
                f"1+({peak}-1)*(on-{start_frame})/max(1,{end_frame - start_frame}),"
                f"{expression})"
            )

        elif movement in {
            "impact_punch",
            "impact_jolt",
        }:
            settle = max(
                ramp_in_end,
                start_frame
                + int(
                    round(
                        0.22
                        if movement == "impact_punch"
                        else 0.10
                    )
                ),
            )
            settle_zoom = max(
                1.0,
                peak
                - (
                    0.035
                    if movement == "impact_punch"
                    else 0.060
                ),
            )
            event_expression = (
                f"if(between(on,{start_frame},{ramp_in_end}),"
                f"1+({peak}-1)*(on-{start_frame})/{ramp_in_denominator},"
                f"if(between(on,{ramp_in_end},{settle}),"
                f"{peak}-({peak}-{settle_zoom})*(on-{ramp_in_end})/max(1,{settle - ramp_in_end}),"
                f"if(between(on,{settle},{ramp_out_start}),"
                f"{settle_zoom},"
                f"if(between(on,{ramp_out_start},{end_frame}),"
                f"1+({settle_zoom}-1)*({end_frame}-on)/{ramp_out_denominator},"
                f"{expression}))))"
            )

        elif movement == "hard_reframe_cut":
            settle_zoom = max(
                1.0,
                peak - 0.030,
            )
            settle = min(
                end_frame,
                start_frame
                + int(
                    round(
                        0.16
                        * fps
                    )
                ),
            )
            event_expression = (
                f"if(between(on,{start_frame},{settle}),"
                f"{peak},"
                f"if(between(on,{settle},{ramp_out_start}),"
                f"{settle_zoom},"
                f"if(between(on,{ramp_out_start},{end_frame}),"
                f"1+({settle_zoom}-1)*({end_frame}-on)/{ramp_out_denominator},"
                f"{expression})))"
            )

        else:
            event_expression = (
                f"if(between(on,{start_frame},{ramp_in_end}),"
                f"1+({peak}-1)*(on-{start_frame})/{ramp_in_denominator},"
                f"if(between(on,{ramp_in_end},{ramp_out_start}),"
                f"{peak},"
                f"if(between(on,{ramp_out_start},{end_frame}),"
                f"1+({peak}-1)*({end_frame}-on)/{ramp_out_denominator},"
                f"{expression})))"
            )

        expression = event_expression

    return expression


def x_expression(
    events: list[dict[str, Any]],
    fps: float,
) -> str:

    expression = "iw/2-(iw/zoom/2)"

    for event in reversed(
        events
    ):
        if event.get(
            "movement"
        ) not in {
            "directional_push",
            "hard_reframe_cut",
            "impact_jolt",
        }:
            continue

        start_frame = int(
            round(
                event["start"]
                * fps
            )
        )

        end_frame = int(
            round(
                event["end"]
                * fps
            )
        )

        bias = float(
            event.get(
                "x_bias",
                0.0,
            )
        )

        event_expression = (
            f"if(between(on,{start_frame},{end_frame}),"
            f"iw/2-(iw/zoom/2)+(iw-iw/zoom)*{bias},"
            f"{expression})"
        )

        expression = event_expression

    return expression


def y_expression(
    events: list[dict[str, Any]],
    fps: float,
) -> str:

    expression = "ih/2-(ih/zoom/2)"

    for event in reversed(
        events
    ):
        if event.get(
            "movement"
        ) != "impact_jolt":
            continue

        start_frame = int(
            round(
                event["start"]
                * fps
            )
        )

        end_frame = int(
            round(
                event["end"]
                * fps
            )
        )

        bias = float(
            event.get(
                "y_bias",
                0.0,
            )
        )

        event_expression = (
            f"if(between(on,{start_frame},{end_frame}),"
            f"ih/2-(ih/zoom/2)+(ih-ih/zoom)*{bias},"
            f"{expression})"
        )

        expression = event_expression

    return expression


def apply_motion(
    events: list[dict[str, Any]],
    fps: float,
    content_rect: tuple[int, int, int, int] = (0, 0, OUTPUT_WIDTH, OUTPUT_HEIGHT),
) -> None:

    zoom = zoom_expression(
        events,
        fps,
    )
    x = x_expression(
        events,
        fps,
    )
    y = y_expression(
        events,
        fps,
    )

    (
        content_x,
        content_y,
        content_width,
        content_height,
    ) = content_rect

    # render_base_video() (app/render.py) crops to fill the 1080x1920
    # canvas by default (content_rect is the full canvas, making both
    # crop/pad below no-ops), but this still supports a letterboxed
    # content_rect if one is ever passed in again -- in that case the
    # zoompan filter above (whose x/y/zoom expressions center and pan
    # using iw/ih) would otherwise zoom into a mix of real content and
    # black bars. Cropping to the real content rect first means iw/ih
    # inside those expressions resolve to the actual content dimensions
    # with no changes needed to any of that zoom/pan logic; padding back
    # out afterward restores the original 1080x1920 canvas and centering.
    filter_string = (
        f"crop={content_width}:{content_height}:"
        f"{content_x}:{content_y},"
        "zoompan="
        f"z='{zoom}':"
        f"x='{x}':"
        f"y='{y}':"
        "d=1:"
        f"s={content_width}x{content_height}:"
        f"fps={fps:.6f},"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
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
        "-preset",
        "medium",
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
        "Applying smart punch-in motion...",
        flush=True,
    )

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )

    os.replace(
        TEMP_PATH,
        VIDEO_PATH,
    )


def main() -> int:

    print(
        "ShortsFactory smart motion starting...",
        flush=True,
    )

    render_settings = load_render_settings()
    edit_energy = normalize_energy(
        render_settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )

    print(
        f"Edit energy: {edit_energy}",
        flush=True,
    )

    if not VIDEO_PATH.exists():

        print(
            f"ERROR: Missing video: {VIDEO_PATH}",
            flush=True,
        )

        return 1

    transcript = load_json(
        TRANSCRIPT_PATH
    )

    words = transcript.get(
        "words",
        [],
    )

    if (
        not isinstance(
            words,
            list,
        )
        or not words
    ):

        print(
            "No word timestamps available; skipping smart motion.",
            flush=True,
        )

        return 0

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

        return 0

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

    events = build_motion_events(
        words,
        duration,
        scene_cuts,
        edit_energy,
    )

    events = apply_shot_aware_zoom_strength(
        events,
        shot_types,
        edit_energy,
    )

    events = enrich_motion_events(
        events,
        duration,
        edit_energy,
    )

    plan = {
        "source_video": str(
            VIDEO_PATH
        ),
        "duration_seconds": round(
            duration,
            3,
        ),
        "fps": round(
            fps,
            6,
        ),
        "edit_energy": edit_energy,
        "scene_cut_count": len(
            scene_cuts
        ),
        "scene_cuts": [
            round(
                cut,
                3,
            )
            for cut in scene_cuts
        ],
        "shot_types": shot_types,
        "event_count": len(
            events
        ),
        "events": events,
    }

    PLAN_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    PLAN_PATH.write_text(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Smart motion events selected: {len(events)}",
        flush=True,
    )

    for index, event in enumerate(
        events,
        start=1,
    ):

        print(
            (
                f"Motion {index}: "
                f"{event['start']:.2f}s -> "
                f"{event['end']:.2f}s, "
                f"{event['zoom']:.2f}x, "
                f"{event.get('movement', 'punch_in')}, "
                f"trigger={event['trigger_word']}"
            ),
            flush=True,
        )

    if not events:

        print(
            "No useful motion moments found; leaving video unchanged.",
            flush=True,
        )

        return 0

    try:

        apply_motion(
            events,
            fps,
            content_rect_from_settings(
                render_settings
            ),
        )

    except subprocess.CalledProcessError as exc:

        if TEMP_PATH.exists():

            try:
                TEMP_PATH.unlink()
            except OSError:
                pass

        print(
            (
                "WARNING: Smart motion FFmpeg pass failed "
                f"with exit code {exc.returncode}. "
                "Continuing without motion."
            ),
            flush=True,
        )

        return 0

    print(
        f"Smart motion plan: {PLAN_PATH}",
        flush=True,
    )

    print(
        "Smart motion applied.",
        flush=True,
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
