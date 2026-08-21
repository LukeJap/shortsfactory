from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parent.parent

OUTPUT_PLAN = (
    ROOT
    / "output"
    / "smart_reframe_plan.json"
)

SCENE_PLAN = (
    ROOT
    / "output"
    / "scene_plan.json"
)

SOURCE_SHOT_PLAN = (
    ROOT
    / "output"
    / "shot_type_source_plan.json"
)

SPEAKER_FOCUS_PLAN = (
    ROOT
    / "output"
    / "speaker_focus_plan.json"
)

OUTPUT_W = 1080
OUTPUT_H = 1920

SAMPLE_INTERVAL_SECONDS = 0.55
MAX_SAMPLE_FRAMES = 100
MIN_DETECTED_FRAMES = 3
MIN_DETECTION_RATIO = 0.16
KEYFRAME_INTERVAL_SECONDS = 1.8
SMOOTH_ALPHA = 0.28


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Render a selected source range to 9:16 using local "
            "face-aware horizontal reframing."
        )
    )

    parser.add_argument(
        "--source",
        required=True,
    )

    parser.add_argument(
        "--start",
        required=True,
        type=float,
    )

    parser.add_argument(
        "--end",
        required=True,
        type=float,
    )

    parser.add_argument(
        "--output",
        required=True,
    )

    return parser.parse_args()


def ffprobe_dimensions(
    source: Path,
) -> tuple[int, int, float]:

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:format=duration",
        "-of",
        "json",
        str(source),
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

    streams = data.get(
        "streams",
        [],
    )

    if not streams:
        raise RuntimeError(
            "No video stream found."
        )

    width = int(
        streams[0]["width"]
    )

    height = int(
        streams[0]["height"]
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

    return (
        width,
        height,
        duration,
    )


def scaled_dimensions(
    width: int,
    height: int,
) -> tuple[int, int]:

    scale = max(
        OUTPUT_W / width,
        OUTPUT_H / height,
    )

    scaled_w = int(
        math.ceil(
            width * scale
        )
    )

    scaled_h = int(
        math.ceil(
            height * scale
        )
    )

    return (
        scaled_w,
        scaled_h,
    )


def center_crop_plan(
    duration: float,
    reason: str,
) -> dict[str, Any]:

    return {
        "mode": "center_fallback",
        "reason": reason,
        "duration_seconds": round(
            duration,
            3,
        ),
        "detection_ratio": 0.0,
        "sample_count": 0,
        "detected_sample_count": 0,
        "keyframes": [],
    }



def detect_scene_boundaries(
    source: Path,
    start: float,
    end: float,
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
                str(source),
                "--start",
                f"{start:.3f}",
                "--end",
                f"{end:.3f}",
                "--output",
                str(SCENE_PLAN),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    try:
        data = json.loads(
            SCENE_PLAN.read_text(
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

        if 0.05 < numeric < (end - start) - 0.05:
            cleaned.append(
                numeric
            )

    return sorted(
        cleaned
    )


def fill_and_smooth_track_by_scenes(
    observations: list[dict[str, float]],
    duration: float,
    scene_cuts: list[float],
) -> list[dict[str, float]]:
    """
    Smooth tracking independently inside each detected shot.
    This prevents the crop from easing across an actual camera cut.
    """

    boundaries = (
        [0.0]
        + [
            cut
            for cut in scene_cuts
            if 0.0 < cut < duration
        ]
        + [duration]
    )

    combined: list[
        dict[str, float]
    ] = []

    for scene_index in range(
        len(boundaries) - 1
    ):

        scene_start = boundaries[
            scene_index
        ]

        scene_end = boundaries[
            scene_index + 1
        ]

        scene_duration = max(
            0.0,
            scene_end
            - scene_start,
        )

        if scene_duration <= 0:
            continue

        scene_observations = []

        for observation in observations:

            if (
                scene_start
                <= observation["time"]
                <= scene_end
            ):

                scene_observations.append(
                    {
                        **observation,
                        "time": observation["time"]
                        - scene_start,
                    }
                )

        if scene_observations:

            scene_track = fill_and_smooth_track(
                scene_observations,
                scene_duration,
            )

            for keyframe in scene_track:

                combined.append(
                    {
                        "time": keyframe["time"]
                        + scene_start,
                        "x": keyframe["x"],
                        "scene": scene_index + 1,
                    }
                )

        else:
            # No reliable face in this shot: reset to center for the shot
            # instead of dragging the previous speaker's position across it.
            combined.append(
                {
                    "time": scene_start,
                    "x": 0.5,
                    "scene": scene_index + 1,
                }
            )

            combined.append(
                {
                    "time": scene_end,
                    "x": 0.5,
                    "scene": scene_index + 1,
                }
            )

    # Keep exact duplicate scene-boundary timestamps, but nudge the earlier
    # keyframe back slightly so FFmpeg can make the shot change effectively
    # instantaneous rather than interpolating through it.
    adjusted: list[
        dict[str, float]
    ] = []

    for keyframe in combined:

        if (
            adjusted
            and abs(
                keyframe["time"]
                - adjusted[-1]["time"]
            ) < 0.001
            and keyframe.get("scene")
            != adjusted[-1].get("scene")
        ):

            adjusted[-1]["time"] = max(
                0.0,
                adjusted[-1]["time"]
                - 0.015,
            )

        adjusted.append(
            keyframe
        )

    adjusted.sort(
        key=lambda item: item[
            "time"
        ]
    )

    return adjusted


def classify_source_shots(
    source: Path,
    start: float,
    end: float,
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
                str(source),
                "--start",
                f"{start:.3f}",
                "--end",
                f"{end:.3f}",
                "--scene-plan",
                str(SCENE_PLAN),
                "--output",
                str(SOURCE_SHOT_PLAN),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    try:
        data = json.loads(
            SOURCE_SHOT_PLAN.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return []

    shots = data.get("shots", [])
    return shots if isinstance(shots, list) else []


def adapt_keyframes_to_shot_types(
    keyframes: list[dict[str, Any]],
    shots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reduce crop movement in already-tight or multi-person shots."""

    if not keyframes or not shots:
        return keyframes

    strength_by_type = {
        "close_up": 0.48,
        "medium": 0.78,
        "wide": 1.00,
        "multi_person": 0.62,
    }

    adapted = []

    for keyframe in keyframes:
        t = float(keyframe.get("time", 0.0))
        shot_type = "medium"

        for shot in shots:
            try:
                shot_start = float(shot.get("start", 0.0))
                shot_end = float(shot.get("end", shot_start))
            except (TypeError, ValueError):
                continue

            if shot_start - 0.001 <= t <= shot_end + 0.001:
                shot_type = str(shot.get("type", "medium"))
                break

        strength = strength_by_type.get(shot_type, 0.78)
        x = float(keyframe.get("x", 0.5))
        adjusted_x = 0.5 + (x - 0.5) * strength

        item = dict(keyframe)
        item["x"] = max(0.0, min(1.0, adjusted_x))
        item["shot_type"] = shot_type
        item["tracking_strength"] = strength
        adapted.append(item)

    return adapted



def detect_speaker_focus(
    source: Path,
    start: float,
    end: float,
    shot_types: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Estimate active speaker position only for shots already classified
    as multi-person. This remains optional and never blocks rendering.
    """

    if not any(
        str(
            shot.get(
                "type",
                "",
            )
        )
        == "multi_person"
        for shot in shot_types
    ):
        return []

    script = (
        ROOT
        / "app"
        / "speaker_focus.py"
    )

    if not script.exists():
        return []

    try:
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--video",
                str(source),
                "--start",
                f"{start:.3f}",
                "--end",
                f"{end:.3f}",
                "--shot-plan",
                str(SOURCE_SHOT_PLAN),
                "--output",
                str(SPEAKER_FOCUS_PLAN),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    try:
        data = json.loads(
            SPEAKER_FOCUS_PLAN.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return []

    points = data.get(
        "focus_points",
        [],
    )

    if not isinstance(
        points,
        list,
    ):
        return []

    cleaned: list[
        dict[str, Any]
    ] = []

    for point in points:

        if not isinstance(
            point,
            dict,
        ):
            continue

        try:
            time = float(
                point.get(
                    "time",
                    0.0,
                )
            )

            x = float(
                point.get(
                    "x",
                    0.5,
                )
            )

            confidence = float(
                point.get(
                    "confidence",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if (
            0.0
            <= time
            <= end - start + 0.05
        ):

            cleaned.append(
                {
                    "time": time,
                    "x": max(
                        0.0,
                        min(
                            1.0,
                            x,
                        ),
                    ),
                    "confidence": max(
                        0.0,
                        min(
                            1.0,
                            confidence,
                        ),
                    ),
                    "shot": point.get(
                        "shot"
                    ),
                }
            )

    return cleaned


def apply_speaker_focus_to_keyframes(
    keyframes: list[dict[str, Any]],
    focus_points: list[dict[str, Any]],
    shots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Blend visually confident mouth-motion speaker estimates into the
    multi-person crop path. We blend instead of hard-snapping so false
    positives do not create frantic camera movement.
    """

    if (
        not keyframes
        or not focus_points
        or not shots
    ):
        return keyframes

    adapted: list[
        dict[str, Any]
    ] = []

    for keyframe in keyframes:

        item = dict(
            keyframe
        )

        t = float(
            item.get(
                "time",
                0.0,
            )
        )

        shot = next(
            (
                shot
                for shot in shots
                if (
                    str(
                        shot.get(
                            "type",
                            "",
                        )
                    )
                    == "multi_person"
                    and float(
                        shot.get(
                            "start",
                            0.0,
                        )
                    )
                    - 0.001
                    <= t
                    <= float(
                        shot.get(
                            "end",
                            0.0,
                        )
                    )
                    + 0.001
                )
            ),
            None,
        )

        if shot is None:

            adapted.append(
                item
            )

            continue

        nearby = min(
            focus_points,
            key=lambda point: abs(
                float(
                    point.get(
                        "time",
                        0.0,
                    )
                )
                - t
            ),
        )

        distance = abs(
            float(
                nearby.get(
                    "time",
                    0.0,
                )
            )
            - t
        )

        if distance > 1.35:

            adapted.append(
                item
            )

            continue

        confidence = float(
            nearby.get(
                "confidence",
                0.0,
            )
        )

        if confidence < 0.52:

            adapted.append(
                item
            )

            continue

        base_x = float(
            item.get(
                "x",
                0.5,
            )
        )

        speaker_x = float(
            nearby.get(
                "x",
                base_x,
            )
        )

        # Deliberately conservative. A high-confidence active speaker
        # can pull the crop significantly, but not erase the stable
        # group framing entirely.
        blend = min(
            0.68,
            max(
                0.30,
                (
                    confidence
                    - 0.45
                )
                * 1.10,
            ),
        )

        focused_x = (
            base_x
            * (
                1.0
                - blend
            )
            + speaker_x
            * blend
        )

        item["x"] = max(
            0.0,
            min(
                1.0,
                focused_x,
            ),
        )

        item[
            "speaker_focus"
        ] = True

        item[
            "speaker_focus_x"
        ] = round(
            speaker_x,
            4,
        )

        item[
            "speaker_focus_confidence"
        ] = round(
            confidence,
            3,
        )

        item[
            "speaker_focus_blend"
        ] = round(
            blend,
            3,
        )

        adapted.append(
            item
        )

    return adapted


def detect_face_centers(
    source: Path,
    start: float,
    end: float,
) -> tuple[
    list[dict[str, float]],
    int,
    int,
    str | None,
]:

    try:
        import cv2  # type: ignore
    except ImportError:
        return (
            [],
            0,
            0,
            (
                "OpenCV is not installed. "
                "Install opencv-python to enable face-aware reframing."
            ),
        )

    cascade_path = (
        Path(
            cv2.data.haarcascades
        )
        / "haarcascade_frontalface_default.xml"
    )

    detector = cv2.CascadeClassifier(
        str(
            cascade_path
        )
    )

    if detector.empty():
        return (
            [],
            0,
            0,
            "OpenCV face detector could not be loaded.",
        )

    capture = cv2.VideoCapture(
        str(source)
    )

    if not capture.isOpened():
        return (
            [],
            0,
            0,
            "OpenCV could not open the source video.",
        )

    clip_duration = max(
        0.0,
        end - start,
    )

    sample_interval = max(
        SAMPLE_INTERVAL_SECONDS,
        clip_duration
        / max(
            1,
            MAX_SAMPLE_FRAMES,
        ),
    )

    sample_times = []

    t = 0.0

    while (
        t <= clip_duration
        and len(sample_times)
        < MAX_SAMPLE_FRAMES
    ):

        sample_times.append(
            t
        )

        t += sample_interval

    observations: list[
        dict[str, float]
    ] = []

    detected_count = 0

    for local_time in sample_times:

        absolute_ms = (
            start + local_time
        ) * 1000.0

        capture.set(
            cv2.CAP_PROP_POS_MSEC,
            absolute_ms,
        )

        ok, frame = capture.read()

        if not ok or frame is None:
            continue

        frame_h, frame_w = (
            frame.shape[:2]
        )

        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY,
        )

        # Downscale very large sources for faster CPU detection.
        detection_width = min(
            960,
            frame_w,
        )

        detection_scale = (
            detection_width
            / frame_w
        )

        if detection_scale < 1.0:

            detection_frame = cv2.resize(
                gray,
                (
                    detection_width,
                    int(
                        round(
                            frame_h
                            * detection_scale
                        )
                    ),
                ),
            )

        else:

            detection_frame = gray

        faces = detector.detectMultiScale(
            detection_frame,
            scaleFactor=1.12,
            minNeighbors=5,
            minSize=(42, 42),
        )

        if len(faces) == 0:
            continue

        detected_count += 1

        # Build one stable subject region per sampled frame.
        # Multiple visible people are framed together instead of
        # arbitrarily picking one and jumping between speakers.
        left = min(
            int(face[0])
            for face in faces
        )

        right = max(
            int(
                face[0]
                + face[2]
            )
            for face in faces
        )

        center_x_detection = (
            left + right
        ) / 2.0

        normalized_x = (
            center_x_detection
            / detection_frame.shape[1]
        )

        observations.append(
            {
                "time": local_time,
                "x": max(
                    0.0,
                    min(
                        1.0,
                        normalized_x,
                    ),
                ),
                "faces": float(
                    len(faces)
                ),
            }
        )

    capture.release()

    return (
        observations,
        len(sample_times),
        detected_count,
        None,
    )


def fill_and_smooth_track(
    observations: list[dict[str, float]],
    duration: float,
) -> list[dict[str, float]]:

    if not observations:
        return []

    # Reject wildly isolated detections using the median position.
    median_x = median(
        observation["x"]
        for observation in observations
    )

    filtered = [
        observation
        for observation in observations
        if abs(
            observation["x"]
            - median_x
        )
        <= 0.38
    ]

    if len(filtered) < MIN_DETECTED_FRAMES:
        filtered = observations

    filtered.sort(
        key=lambda item: item[
            "time"
        ]
    )

    key_times = [
        0.0
    ]

    t = KEYFRAME_INTERVAL_SECONDS

    while t < duration:

        key_times.append(
            t
        )

        t += KEYFRAME_INTERVAL_SECONDS

    if duration > 0:
        key_times.append(
            duration
        )

    raw_keyframes: list[
        dict[str, float]
    ] = []

    for key_time in key_times:

        nearby = sorted(
            filtered,
            key=lambda item: abs(
                item["time"]
                - key_time
            ),
        )[:4]

        if nearby:

            weights = [
                1.0
                / (
                    0.35
                    + abs(
                        item["time"]
                        - key_time
                    )
                )
                for item in nearby
            ]

            weighted_x = sum(
                item["x"] * weight
                for item, weight
                in zip(
                    nearby,
                    weights,
                )
            ) / sum(
                weights
            )

        else:
            weighted_x = 0.5

        raw_keyframes.append(
            {
                "time": key_time,
                "x": weighted_x,
            }
        )

    # Exponential smoothing prevents the crop from twitching.
    smoothed: list[
        dict[str, float]
    ] = []

    current_x = raw_keyframes[0][
        "x"
    ]

    for index, keyframe in enumerate(
        raw_keyframes
    ):

        if index == 0:

            current_x = keyframe[
                "x"
            ]

        else:

            current_x = (
                current_x
                * (
                    1.0
                    - SMOOTH_ALPHA
                )
                + keyframe["x"]
                * SMOOTH_ALPHA
            )

        smoothed.append(
            {
                "time": keyframe[
                    "time"
                ],
                "x": max(
                    0.0,
                    min(
                        1.0,
                        current_x,
                    ),
                ),
            }
        )

    return smoothed


def crop_x_from_normalized(
    normalized_x: float,
    scaled_w: int,
) -> float:

    if scaled_w <= OUTPUT_W:
        return 0.0

    subject_x = (
        normalized_x
        * scaled_w
    )

    target = (
        subject_x
        - OUTPUT_W / 2
    )

    return max(
        0.0,
        min(
            scaled_w
            - OUTPUT_W,
            target,
        ),
    )


def ffmpeg_x_expression(
    keyframes: list[dict[str, float]],
    scaled_w: int,
) -> str:

    if not keyframes:
        return (
            "(iw-1080)/2"
        )

    points = [
        {
            "time": float(
                item["time"]
            ),
            "crop_x": crop_x_from_normalized(
                float(
                    item["x"]
                ),
                scaled_w,
            ),
        }
        for item in keyframes
    ]

    if len(points) == 1:
        return (
            f"{points[0]['crop_x']:.3f}"
        )

    expression = (
        f"{points[-1]['crop_x']:.3f}"
    )

    # Piecewise linear interpolation through subject tracking keyframes.
    for index in range(
        len(points) - 2,
        -1,
        -1,
    ):

        left = points[
            index
        ]

        right = points[
            index + 1
        ]

        dt = max(
            0.001,
            right["time"]
            - left["time"],
        )

        interpolated = (
            f"({left['crop_x']:.3f}"
            f"+({right['crop_x']:.3f}"
            f"-{left['crop_x']:.3f})"
            f"*(t-{left['time']:.3f})"
            f"/{dt:.3f})"
        )

        expression = (
            f"if(lte(t,{right['time']:.3f}),"
            f"{interpolated},"
            f"{expression})"
        )

    return expression


def render_vertical(
    source: Path,
    start: float,
    end: float,
    output: Path,
    keyframes: list[dict[str, float]],
    source_w: int,
    source_h: int,
) -> None:

    duration = max(
        0.01,
        end - start,
    )

    scaled_w, _ = scaled_dimensions(
        source_w,
        source_h,
    )

    x_expression = ffmpeg_x_expression(
        keyframes,
        scaled_w,
    )

    video_filter = (
        "scale="
        f"{OUTPUT_W}:{OUTPUT_H}:"
        "force_original_aspect_ratio=increase,"
        "crop="
        f"{OUTPUT_W}:{OUTPUT_H}:"
        f"x='{x_expression}':"
        "y='(ih-1920)/2'"
    )

    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]

    print("")
    print("Running:")
    print(
        " ".join(
            command
        )
    )
    print("")

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def main() -> int:

    args = parse_args()

    source = Path(
        args.source
    ).resolve()

    output = Path(
        args.output
    ).resolve()

    start = max(
        0.0,
        float(
            args.start
        ),
    )

    end = max(
        start,
        float(
            args.end
        ),
    )

    print(
        "ShortsFactory subject-aware reframe starting...",
        flush=True,
    )

    print(
        f"Source: {source}",
        flush=True,
    )

    print(
        f"Range: {start:.3f} -> {end:.3f}",
        flush=True,
    )

    if not source.exists():

        print(
            f"ERROR: Source not found: {source}",
            flush=True,
        )

        return 1

    if end <= start:

        print(
            "ERROR: End timestamp must be after start timestamp.",
            flush=True,
        )

        return 1

    try:

        source_w, source_h, source_duration = (
            ffprobe_dimensions(
                source
            )
        )

    except Exception as exc:

        print(
            f"ERROR: Could not inspect source: {exc}",
            flush=True,
        )

        return 1

    end = min(
        end,
        source_duration
        if source_duration > 0
        else end,
    )

    clip_duration = max(
        0.0,
        end - start,
    )

    # Portrait footage already fits the target composition well; avoid
    # unnecessary subject tracking/cropping.
    if (
        source_w
        / max(
            1,
            source_h,
        )
        <= 0.70
    ):

        observations = []
        sample_count = 0
        detected_count = 0
        detector_error = (
            "Source is already portrait-oriented."
        )

    else:

        (
            observations,
            sample_count,
            detected_count,
            detector_error,
        ) = detect_face_centers(
            source,
            start,
            end,
        )

    scene_cuts = detect_scene_boundaries(
        source,
        start,
        end,
    )

    print(
        f"Scene cuts detected for reframing: {len(scene_cuts)}",
        flush=True,
    )

    shot_types = classify_source_shots(
        source,
        start,
        end,
    )

    if shot_types:
        summary = ", ".join(
            f"{shot.get('shot', '?')}:{shot.get('type', 'unknown')}"
            for shot in shot_types
        )
        print(
            f"Shot types for reframing: {summary}",
            flush=True,
        )

    speaker_focus_points = detect_speaker_focus(
        source,
        start,
        end,
        shot_types,
    )

    if speaker_focus_points:

        print(
            (
                "Active-speaker focus points available: "
                f"{len(speaker_focus_points)}"
            ),
            flush=True,
        )

    detection_ratio = (
        detected_count
        / max(
            1,
            sample_count,
        )
    )

    reliable = (
        detector_error is None
        and detected_count
        >= MIN_DETECTED_FRAMES
        and detection_ratio
        >= MIN_DETECTION_RATIO
    )

    if reliable:

        keyframes = fill_and_smooth_track_by_scenes(
            observations,
            clip_duration,
            scene_cuts,
        )

        keyframes = adapt_keyframes_to_shot_types(
            keyframes,
            shot_types,
        )

        keyframes = apply_speaker_focus_to_keyframes(
            keyframes,
            speaker_focus_points,
            shot_types,
        )

        mode = (
            "face_aware_dynamic"
        )

        reason = (
            "Reliable face detections found across the selected clip."
        )

        print(
            (
                "Subject tracking enabled: "
                f"{detected_count}/{sample_count} "
                f"sampled frames contained faces."
            ),
            flush=True,
        )

    else:

        keyframes = []

        mode = "center_fallback"

        reason = (
            detector_error
            or (
                "Face detections were too sparse for a reliable moving crop "
                f"({detected_count}/{sample_count} sampled frames)."
            )
        )

        print(
            f"Using safe center crop: {reason}",
            flush=True,
        )

    plan = {
        "source_video": str(
            source
        ),
        "selection_start": round(
            start,
            3,
        ),
        "selection_end": round(
            end,
            3,
        ),
        "duration_seconds": round(
            clip_duration,
            3,
        ),
        "source_width": source_w,
        "source_height": source_h,
        "mode": mode,
        "reason": reason,
        "sample_count": sample_count,
        "detected_sample_count": (
            detected_count
        ),
        "detection_ratio": round(
            detection_ratio,
            3,
        ),
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
        "speaker_focus_point_count": len(
            speaker_focus_points
        ),
        "speaker_focus_points": speaker_focus_points,
        "keyframes": [
            {
                "time": round(
                    item["time"],
                    3,
                ),
                "normalized_subject_x": round(
                    item["x"],
                    4,
                ),
                "shot_type": item.get("shot_type", "unknown"),
                "tracking_strength": item.get("tracking_strength", 1.0),
                "speaker_focus": item.get("speaker_focus", False),
                "speaker_focus_x": item.get("speaker_focus_x"),
                "speaker_focus_confidence": item.get(
                    "speaker_focus_confidence"
                ),
                "speaker_focus_blend": item.get(
                    "speaker_focus_blend"
                ),
            }
            for item in keyframes
        ],
    }

    OUTPUT_PLAN.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PLAN.write_text(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    try:

        render_vertical(
            source,
            start,
            end,
            output,
            keyframes,
            source_w,
            source_h,
        )

    except subprocess.CalledProcessError as exc:

        print(
            (
                "ERROR: FFmpeg reframe failed with "
                f"exit code {exc.returncode}"
            ),
            flush=True,
        )

        return exc.returncode or 1

    print(
        f"Reframe mode: {mode}",
        flush=True,
    )

    print(
        f"Plan saved: {OUTPUT_PLAN}",
        flush=True,
    )

    print(
        f"Vertical clip created: {output}",
        flush=True,
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
