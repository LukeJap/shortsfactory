"""
Classifies video shots as close-up/medium/wide/multi-person using local
OpenCV face detection. smart_motion.py uses shot type to size and cap the
punch-in zoom effect (e.g. don't zoom too tight on an already-close shot).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parent.parent

SAMPLE_INTERVAL_SECONDS = 0.55
MAX_SAMPLES_PER_SHOT = 18
MIN_FACE_SIZE = 38


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="Classify video shots as close-up, medium, wide, or multi-person."
    )

    parser.add_argument("--video", required=True)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--scene-plan", default="")
    parser.add_argument("--output", required=True)

    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def scene_boundaries(
    start: float,
    end: float,
    scene_plan_path: Path | None,
) -> list[float]:

    duration = max(0.0, end - start)
    cuts: list[float] = []

    if scene_plan_path and scene_plan_path.exists():

        data = load_json(scene_plan_path)

        raw_cuts = data.get("cuts", [])

        if isinstance(raw_cuts, list):

            for value in raw_cuts:

                try:
                    cut = float(value)
                except (TypeError, ValueError):
                    continue

                if 0.05 < cut < duration - 0.05:
                    cuts.append(cut)

    cuts = sorted(set(round(value, 4) for value in cuts))

    return [0.0, *cuts, duration]


def classify_shot(
    face_counts: list[int],
    largest_face_height_ratios: list[float],
    largest_face_area_ratios: list[float],
    detected_samples: int,
    sample_count: int,
) -> tuple[str, float, dict[str, float]]:

    detection_ratio = (
        detected_samples / max(1, sample_count)
    )

    if not largest_face_height_ratios:

        return (
            "wide",
            min(0.62, 0.35 + 0.25 * (1.0 - detection_ratio)),
            {
                "median_face_height_ratio": 0.0,
                "median_face_area_ratio": 0.0,
                "median_face_count": 0.0,
                "detection_ratio": round(detection_ratio, 3),
            },
        )

    height_ratio = median(largest_face_height_ratios)
    area_ratio = median(largest_face_area_ratios)
    face_count = median(face_counts) if face_counts else 1.0

    multi_ratio = (
        sum(1 for count in face_counts if count >= 2)
        / max(1, len(face_counts))
    )

    if multi_ratio >= 0.34 or face_count >= 2:

        shot_type = "multi_person"
        confidence = min(
            0.96,
            0.55
            + 0.25 * multi_ratio
            + 0.15 * detection_ratio,
        )

    elif height_ratio >= 0.30 or area_ratio >= 0.085:

        shot_type = "close_up"
        confidence = min(
            0.96,
            0.58
            + min(0.22, height_ratio * 0.55)
            + 0.12 * detection_ratio,
        )

    elif height_ratio >= 0.145 or area_ratio >= 0.025:

        shot_type = "medium"
        confidence = min(
            0.93,
            0.52
            + min(0.18, height_ratio * 0.45)
            + 0.12 * detection_ratio,
        )

    else:

        shot_type = "wide"
        confidence = min(
            0.88,
            0.50
            + 0.20 * detection_ratio
            + min(0.12, max(0.0, 0.145 - height_ratio)),
        )

    return (
        shot_type,
        round(confidence, 3),
        {
            "median_face_height_ratio": round(height_ratio, 4),
            "median_face_area_ratio": round(area_ratio, 4),
            "median_face_count": round(float(face_count), 2),
            "multi_person_sample_ratio": round(multi_ratio, 3),
            "detection_ratio": round(detection_ratio, 3),
        },
    )


def analyze_shots(
    video_path: Path,
    absolute_start: float,
    absolute_end: float,
    boundaries: list[float],
) -> list[dict[str, Any]]:

    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "opencv-python is required for shot-type intelligence."
        ) from exc

    cascade_path = (
        Path(cv2.data.haarcascades)
        / "haarcascade_frontalface_default.xml"
    )

    detector = cv2.CascadeClassifier(str(cascade_path))

    if detector.empty():
        raise RuntimeError("OpenCV face detector could not be loaded.")

    capture = cv2.VideoCapture(str(video_path))

    if not capture.isOpened():
        raise RuntimeError("OpenCV could not open the video.")

    shots: list[dict[str, Any]] = []

    try:

        for shot_index in range(len(boundaries) - 1):

            local_start = boundaries[shot_index]
            local_end = boundaries[shot_index + 1]
            shot_duration = max(0.0, local_end - local_start)

            sample_interval = max(
                SAMPLE_INTERVAL_SECONDS,
                shot_duration / max(1, MAX_SAMPLES_PER_SHOT),
            )

            sample_times: list[float] = []
            t = local_start + min(0.16, shot_duration * 0.12)

            safe_end = max(
                local_start,
                local_end - min(0.12, shot_duration * 0.10),
            )

            while (
                t <= safe_end
                and len(sample_times) < MAX_SAMPLES_PER_SHOT
            ):
                sample_times.append(t)
                t += sample_interval

            if not sample_times:
                sample_times = [
                    local_start + shot_duration * 0.5
                ]

            face_counts: list[int] = []
            height_ratios: list[float] = []
            area_ratios: list[float] = []
            detected_samples = 0
            valid_samples = 0

            for local_time in sample_times:

                capture.set(
                    cv2.CAP_PROP_POS_MSEC,
                    (absolute_start + local_time) * 1000.0,
                )

                ok, frame = capture.read()

                if not ok or frame is None:
                    continue

                valid_samples += 1

                frame_h, frame_w = frame.shape[:2]

                gray = cv2.cvtColor(
                    frame,
                    cv2.COLOR_BGR2GRAY,
                )

                detection_w = min(960, frame_w)
                scale = detection_w / max(1, frame_w)

                if scale < 1.0:

                    detection_frame = cv2.resize(
                        gray,
                        (
                            detection_w,
                            max(1, int(round(frame_h * scale))),
                        ),
                    )

                else:
                    detection_frame = gray

                faces = detector.detectMultiScale(
                    detection_frame,
                    scaleFactor=1.12,
                    minNeighbors=5,
                    minSize=(MIN_FACE_SIZE, MIN_FACE_SIZE),
                )

                face_counts.append(int(len(faces)))

                if len(faces) == 0:
                    continue

                detected_samples += 1

                largest = max(
                    faces,
                    key=lambda face: int(face[2]) * int(face[3]),
                )

                _, _, face_w, face_h = [
                    int(value)
                    for value in largest
                ]

                detect_h, detect_w = detection_frame.shape[:2]

                height_ratios.append(
                    face_h / max(1, detect_h)
                )

                area_ratios.append(
                    (face_w * face_h)
                    / max(1, detect_w * detect_h)
                )

            shot_type, confidence, metrics = classify_shot(
                face_counts,
                height_ratios,
                area_ratios,
                detected_samples,
                valid_samples,
            )

            shots.append(
                {
                    "shot": shot_index + 1,
                    "start": round(local_start, 3),
                    "end": round(local_end, 3),
                    "duration": round(shot_duration, 3),
                    "type": shot_type,
                    "confidence": confidence,
                    "sample_count": valid_samples,
                    "detected_sample_count": detected_samples,
                    "metrics": metrics,
                }
            )

    finally:
        capture.release()

    return shots


def main() -> int:

    args = parse_args()

    video_path = Path(args.video).resolve()
    output_path = Path(args.output).resolve()

    start = max(0.0, float(args.start))
    end = max(start, float(args.end))

    scene_plan_path = (
        Path(args.scene_plan).resolve()
        if str(args.scene_plan).strip()
        else None
    )

    print("ShortsFactory shot-type intelligence starting...", flush=True)

    if not video_path.exists():
        print(f"ERROR: Video not found: {video_path}", flush=True)
        return 1

    if end <= start:
        print("ERROR: End must be after start.", flush=True)
        return 1

    boundaries = scene_boundaries(
        start,
        end,
        scene_plan_path,
    )

    try:
        shots = analyze_shots(
            video_path,
            start,
            end,
            boundaries,
        )
    except RuntimeError as exc:
        print(f"WARNING: {exc}", flush=True)
        return 0

    payload = {
        "video": str(video_path),
        "selection_start": round(start, 3),
        "selection_end": round(end, 3),
        "shot_count": len(shots),
        "shots": shots,
    }

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

    print(f"Shot types classified: {len(shots)}", flush=True)

    for shot in shots:

        print(
            (
                f"Shot {shot['shot']}: "
                f"{shot['start']:.2f}s -> {shot['end']:.2f}s, "
                f"{shot['type']}, "
                f"confidence={shot['confidence']:.2f}"
            ),
            flush=True,
        )

    print(f"Shot-type plan: {output_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
