from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent

SAMPLE_INTERVAL_SECONDS = 0.22
MIN_FACE_SIZE = 44
MIN_TRACK_SAMPLES = 3
FOCUS_HOLD_SECONDS = 0.85
MIN_ADVANTAGE_RATIO = 1.22
MIN_MOUTH_ACTIVITY = 4.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate the visually active speaker in multi-person shots "
            "using local face detection and mouth-region motion."
        )
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--start", required=True, type=float)
    parser.add_argument("--end", required=True, type=float)
    parser.add_argument("--shot-plan", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def multi_person_shots(
    shot_plan: dict[str, Any],
) -> list[dict[str, float]]:

    shots = shot_plan.get("shots", [])
    result: list[dict[str, float]] = []

    if not isinstance(shots, list):
        return result

    for shot in shots:
        if not isinstance(shot, dict):
            continue

        if str(shot.get("type", "")).strip() != "multi_person":
            continue

        try:
            start = float(shot.get("start", 0.0))
            end = float(shot.get("end", start))
        except (TypeError, ValueError):
            continue

        if end > start:
            result.append(
                {
                    "start": start,
                    "end": end,
                    "shot": float(shot.get("shot", 0) or 0),
                }
            )

    return result


def normalize_face(
    face: Any,
    frame_w: int,
    frame_h: int,
) -> dict[str, float]:

    x, y, w, h = [
        float(value)
        for value in face
    ]

    return {
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "cx": (x + w / 2.0) / max(1.0, frame_w),
        "cy": (y + h / 2.0) / max(1.0, frame_h),
        "area": (w * h) / max(1.0, frame_w * frame_h),
    }


def mouth_roi(
    gray,
    face: dict[str, float],
):
    import cv2  # type: ignore

    x = int(round(face["x"]))
    y = int(round(face["y"]))
    w = int(round(face["w"]))
    h = int(round(face["h"]))

    x1 = max(0, x + int(w * 0.18))
    x2 = min(gray.shape[1], x + int(w * 0.82))
    y1 = max(0, y + int(h * 0.55))
    y2 = min(gray.shape[0], y + int(h * 0.92))

    if x2 <= x1 or y2 <= y1:
        return None

    roi = gray[y1:y2, x1:x2]

    if roi.size == 0:
        return None

    roi = cv2.resize(
        roi,
        (64, 32),
        interpolation=cv2.INTER_AREA,
    )

    roi = cv2.GaussianBlur(
        roi,
        (3, 3),
        0,
    )

    return roi


def nearest_previous_track(
    face: dict[str, float],
    tracks: dict[int, dict[str, Any]],
) -> int | None:

    best_id = None
    best_distance = 999.0

    for track_id, track in tracks.items():
        dx = abs(
            float(track.get("cx", 0.5))
            - face["cx"]
        )

        if dx < 0.18 and dx < best_distance:
            best_distance = dx
            best_id = track_id

    return best_id


def analyze_multi_person_shot(
    capture,
    detector,
    absolute_start: float,
    shot_start: float,
    shot_end: float,
) -> list[dict[str, Any]]:

    import cv2  # type: ignore

    tracks: dict[int, dict[str, Any]] = {}
    next_track_id = 1
    observations: list[dict[str, Any]] = []

    local_time = shot_start + min(
        0.10,
        max(0.0, shot_end - shot_start) * 0.08,
    )

    while local_time < shot_end - 0.06:

        absolute_time = (
            absolute_start
            + local_time
        )

        capture.set(
            cv2.CAP_PROP_POS_MSEC,
            absolute_time * 1000.0,
        )

        ok, frame = capture.read()

        if not ok or frame is None:
            local_time += SAMPLE_INTERVAL_SECONDS
            continue

        frame_h, frame_w = frame.shape[:2]

        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY,
        )

        detection_width = min(
            960,
            frame_w,
        )

        scale = (
            detection_width
            / max(1, frame_w)
        )

        if scale < 1.0:

            detection_gray = cv2.resize(
                gray,
                (
                    detection_width,
                    max(
                        1,
                        int(round(frame_h * scale)),
                    ),
                ),
            )

        else:
            detection_gray = gray

        faces_raw = detector.detectMultiScale(
            detection_gray,
            scaleFactor=1.12,
            minNeighbors=5,
            minSize=(MIN_FACE_SIZE, MIN_FACE_SIZE),
        )

        if len(faces_raw) < 2:
            local_time += SAMPLE_INTERVAL_SECONDS
            continue

        detection_h, detection_w = (
            detection_gray.shape[:2]
        )

        faces = [
            normalize_face(
                face,
                detection_w,
                detection_h,
            )
            for face in faces_raw
        ]

        # Keep the most visually important faces to reduce false positives.
        faces.sort(
            key=lambda item: item["area"],
            reverse=True,
        )
        faces = faces[:4]
        faces.sort(
            key=lambda item: item["cx"]
        )

        assigned_ids: set[int] = set()

        frame_activity: list[dict[str, float]] = []

        for face in faces:

            track_id = nearest_previous_track(
                face,
                {
                    key: value
                    for key, value in tracks.items()
                    if key not in assigned_ids
                },
            )

            if track_id is None:
                track_id = next_track_id
                next_track_id += 1

            assigned_ids.add(track_id)

            roi = mouth_roi(
                detection_gray,
                face,
            )

            activity = 0.0

            previous = tracks.get(track_id)

            if (
                roi is not None
                and previous is not None
                and previous.get("mouth") is not None
            ):

                difference = cv2.absdiff(
                    roi,
                    previous["mouth"],
                )

                activity = float(
                    difference.mean()
                )

            tracks[track_id] = {
                "cx": face["cx"],
                "mouth": roi,
                "last_time": local_time,
                "samples": int(
                    (
                        previous or {}
                    ).get(
                        "samples",
                        0,
                    )
                ) + 1,
            }

            frame_activity.append(
                {
                    "track_id": float(track_id),
                    "x": face["cx"],
                    "activity": activity,
                }
            )

        frame_activity.sort(
            key=lambda item: item["activity"],
            reverse=True,
        )

        if len(frame_activity) >= 2:

            best = frame_activity[0]
            second = frame_activity[1]

            second_activity = max(
                0.01,
                second["activity"],
            )

            advantage = (
                best["activity"]
                / second_activity
            )

            confident = (
                best["activity"] >= MIN_MOUTH_ACTIVITY
                and advantage >= MIN_ADVANTAGE_RATIO
            )

            observations.append(
                {
                    "time": local_time,
                    "speaker_x": best["x"],
                    "activity": round(
                        best["activity"],
                        3,
                    ),
                    "runner_up_activity": round(
                        second["activity"],
                        3,
                    ),
                    "advantage_ratio": round(
                        advantage,
                        3,
                    ),
                    "confident": confident,
                    "visible_faces": len(frame_activity),
                }
            )

        local_time += SAMPLE_INTERVAL_SECONDS

    return observations


def consolidate_focus(
    observations: list[dict[str, Any]],
    shot_start: float,
    shot_end: float,
) -> list[dict[str, Any]]:

    confident = [
        item
        for item in observations
        if bool(item.get("confident"))
    ]

    if not confident:
        return []

    points: list[dict[str, Any]] = []

    current_x = None
    hold_until = shot_start

    for item in confident:

        time = float(item["time"])
        x = float(item["speaker_x"])
        activity = float(item["activity"])
        advantage = float(item["advantage_ratio"])

        if (
            current_x is None
            or time >= hold_until
            or abs(x - current_x) > 0.26
        ):

            current_x = x
            hold_until = (
                time
                + FOCUS_HOLD_SECONDS
            )

            confidence = min(
                0.98,
                0.48
                + min(0.25, activity / 40.0)
                + min(0.25, (advantage - 1.0) / 3.0),
            )

            points.append(
                {
                    "time": round(time, 3),
                    "x": round(x, 4),
                    "confidence": round(
                        confidence,
                        3,
                    ),
                    "activity": round(
                        activity,
                        3,
                    ),
                    "advantage_ratio": round(
                        advantage,
                        3,
                    ),
                }
            )

    if points:
        points.insert(
            0,
            {
                **points[0],
                "time": round(
                    shot_start,
                    3,
                ),
            },
        )

        points.append(
            {
                **points[-1],
                "time": round(
                    shot_end,
                    3,
                ),
            }
        )

    return points


def main() -> int:

    args = parse_args()

    video_path = Path(
        args.video
    ).resolve()

    shot_plan_path = Path(
        args.shot_plan
    ).resolve()

    output_path = Path(
        args.output
    ).resolve()

    start = max(
        0.0,
        float(args.start),
    )

    end = max(
        start,
        float(args.end),
    )

    print(
        "ShortsFactory speaker-focus analysis starting...",
        flush=True,
    )

    if not video_path.exists():

        print(
            f"WARNING: Video not found: {video_path}",
            flush=True,
        )
        return 0

    try:
        import cv2  # type: ignore
    except ImportError:

        print(
            "WARNING: opencv-python is required for speaker focus.",
            flush=True,
        )
        return 0

    shot_plan = load_json(
        shot_plan_path
    )

    shots = multi_person_shots(
        shot_plan
    )

    payload: dict[str, Any] = {
        "video": str(video_path),
        "selection_start": round(start, 3),
        "selection_end": round(end, 3),
        "multi_person_shot_count": len(shots),
        "focus_point_count": 0,
        "shots": [],
        "focus_points": [],
    }

    if not shots:

        print(
            "No multi-person shots need active-speaker analysis.",
            flush=True,
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        return 0

    cascade_path = (
        Path(cv2.data.haarcascades)
        / "haarcascade_frontalface_default.xml"
    )

    detector = cv2.CascadeClassifier(
        str(cascade_path)
    )

    if detector.empty():

        print(
            "WARNING: OpenCV face detector could not be loaded.",
            flush=True,
        )
        return 0

    capture = cv2.VideoCapture(
        str(video_path)
    )

    if not capture.isOpened():

        print(
            "WARNING: Could not open video for speaker-focus analysis.",
            flush=True,
        )
        return 0

    all_focus: list[
        dict[str, Any]
    ] = []

    shot_results: list[
        dict[str, Any]
    ] = []

    try:

        for shot in shots:

            shot_start = float(
                shot["start"]
            )

            shot_end = float(
                shot["end"]
            )

            observations = analyze_multi_person_shot(
                capture,
                detector,
                start,
                shot_start,
                shot_end,
            )

            focus_points = consolidate_focus(
                observations,
                shot_start,
                shot_end,
            )

            shot_number = int(
                shot.get(
                    "shot",
                    0,
                )
            )

            for point in focus_points:

                point["shot"] = shot_number

            all_focus.extend(
                focus_points
            )

            shot_results.append(
                {
                    "shot": shot_number,
                    "start": round(
                        shot_start,
                        3,
                    ),
                    "end": round(
                        shot_end,
                        3,
                    ),
                    "observation_count": len(
                        observations
                    ),
                    "confident_observation_count": sum(
                        1
                        for item in observations
                        if item.get(
                            "confident"
                        )
                    ),
                    "focus_points": focus_points,
                }
            )

    finally:
        capture.release()

    payload["shots"] = shot_results
    payload["focus_points"] = all_focus
    payload["focus_point_count"] = len(
        all_focus
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Speaker-focus points selected: {len(all_focus)}",
        flush=True,
    )

    for point in all_focus:

        print(
            (
                f"Speaker focus: "
                f"{point['time']:.2f}s, "
                f"x={point['x']:.3f}, "
                f"confidence={point['confidence']:.2f}"
            ),
            flush=True,
        )

    print(
        f"Speaker-focus plan: {output_path}",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
