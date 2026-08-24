"""
Detects hard scene/shot changes in a video range using ffmpeg's scene
scoring filter. Shared helper used by smart_motion.py (to avoid panning
motion across a cut) and temporal_edit.py (to place whip-transition/
speed-ramp effects at real camera cuts rather than arbitrary points).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "output" / "scene_plan.json"
SCENE_THRESHOLD = 0.31
MIN_SCENE_SECONDS = 0.65

PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect hard scene/shot changes with FFmpeg scene scoring."
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=None)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--threshold", type=float, default=SCENE_THRESHOLD)
    return parser.parse_args()


def probe_duration(video: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return float(result.stdout.strip())


def detect_scene_cuts(
    video: Path,
    start: float,
    end: float,
    threshold: float,
) -> list[float]:
    duration = max(0.01, end - start)
    vf = f"select='gt(scene,{threshold:.3f})',showinfo"

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-ss", f"{start:.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(video),
            "-an", "-vf", vf,
            "-f", "null", "-",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # FFmpeg returns 0 for a normal null-output analysis pass.
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg scene detection failed:\n" + result.stderr[-2000:]
        )

    raw = []
    for match in PTS_RE.finditer(result.stderr):
        value = float(match.group(1))
        if 0.10 < value < duration - 0.10:
            raw.append(value)

    raw.sort()
    deduped: list[float] = []
    for value in raw:
        if not deduped or value - deduped[-1] >= MIN_SCENE_SECONDS:
            deduped.append(value)

    return deduped


def build_scenes(duration: float, cuts: list[float]) -> list[dict]:
    boundaries = [0.0] + cuts + [duration]
    scenes = []
    for index in range(len(boundaries) - 1):
        start = boundaries[index]
        end = boundaries[index + 1]
        if end <= start:
            continue
        scenes.append(
            {
                "index": index + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            }
        )
    return scenes


def main() -> int:
    args = parse_args()
    video = Path(args.video).expanduser().resolve()
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = (ROOT / output).resolve()

    if not video.exists():
        print(f"ERROR: Video not found: {video}", flush=True)
        return 1

    try:
        full_duration = probe_duration(video)
    except Exception as exc:
        print(f"ERROR: Could not inspect video: {exc}", flush=True)
        return 1

    start = max(0.0, float(args.start))
    requested_end = full_duration if args.end is None else float(args.end)
    end = min(full_duration, max(start, requested_end))
    duration = max(0.0, end - start)

    if duration <= 0:
        print("ERROR: Invalid scene-detection range.", flush=True)
        return 1

    try:
        cuts = detect_scene_cuts(
            video,
            start,
            end,
            max(0.05, min(0.95, float(args.threshold))),
        )
    except Exception as exc:
        print(f"WARNING: Scene detection failed: {exc}", flush=True)
        cuts = []

    scenes = build_scenes(duration, cuts)

    payload = {
        "source_video": str(video),
        "selection_start": round(start, 3),
        "selection_end": round(end, 3),
        "duration_seconds": round(duration, 3),
        "threshold": round(float(args.threshold), 3),
        "scene_cut_count": len(cuts),
        "scene_count": len(scenes),
        "cuts": [round(value, 3) for value in cuts],
        "scenes": scenes,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("ShortsFactory scene detector", flush=True)
    print(f"Scene cuts detected: {len(cuts)}", flush=True)
    if cuts:
        print("Cuts: " + ", ".join(f"{value:.2f}s" for value in cuts), flush=True)
    else:
        print("No strong camera cuts detected.", flush=True)
    print(f"Scene plan: {output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
