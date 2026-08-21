from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SUBTITLES_PATH = ROOT / "output" / "subtitles.json"

INPUT_VIDEO = ROOT / "output" / "rendered" / "short1_base.mp4"
OUTPUT_VIDEO = ROOT / "output" / "rendered" / "short1_tight.mp4"

EDIT_PLAN_PATH = ROOT / "output" / "edit_plan.json"


# ============================================================
# SETTINGS
# ============================================================

# Ignore very small conversational gaps.
MIN_GAP_TO_EDIT = 0.80

# If there is a long pause, leave this much breathing room.
KEEP_GAP_SECONDS = 0.22

# Never create a tiny retained video segment.
MIN_KEEP_SEGMENT = 0.15


def run(command: list[str]) -> None:
    print("Running:")
    print(" ".join(command))
    print()

    result = subprocess.run(
        command,
        cwd=ROOT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed with exit code {result.returncode}"
        )


def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"File not found: {path}"
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_video_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Could not determine video duration."
        )

    return float(result.stdout.strip())


def detect_pause_cuts(
    words: list[dict],
) -> list[dict]:

    cuts = []

    if len(words) < 2:
        return cuts

    for index in range(len(words) - 1):

        current_word = words[index]
        next_word = words[index + 1]

        current_end = float(
            current_word.get("end", 0)
        )

        next_start = float(
            next_word.get("start", 0)
        )

        gap = next_start - current_end

        if gap < MIN_GAP_TO_EDIT:
            continue

        # Leave a little natural breathing room around the cut.
        remove_start = (
            current_end
            + KEEP_GAP_SECONDS / 2
        )

        remove_end = (
            next_start
            - KEEP_GAP_SECONDS / 2
        )

        if remove_end <= remove_start:
            continue

        cuts.append(
            {
                "start": round(remove_start, 3),
                "end": round(remove_end, 3),
                "duration": round(
                    remove_end - remove_start,
                    3,
                ),
                "reason": "long_pause",
                "previous_word": str(
                    current_word.get("word", "")
                ),
                "next_word": str(
                    next_word.get("word", "")
                ),
                "original_gap": round(gap, 3),
            }
        )

    return cuts


def cuts_to_keep_ranges(
    cuts: list[dict],
    duration: float,
) -> list[tuple[float, float]]:

    if not cuts:
        return [(0.0, duration)]

    keep_ranges = []

    cursor = 0.0

    for cut in cuts:

        cut_start = float(cut["start"])
        cut_end = float(cut["end"])

        if cut_start - cursor >= MIN_KEEP_SEGMENT:

            keep_ranges.append(
                (
                    round(cursor, 3),
                    round(cut_start, 3),
                )
            )

        cursor = max(cursor, cut_end)

    if duration - cursor >= MIN_KEEP_SEGMENT:

        keep_ranges.append(
            (
                round(cursor, 3),
                round(duration, 3),
            )
        )

    return keep_ranges


def render_tight_edit(
    keep_ranges: list[tuple[float, float]],
) -> None:

    if not keep_ranges:
        raise RuntimeError(
            "Smart editor produced no usable video ranges."
        )

    filters = []

    video_outputs = []
    audio_outputs = []

    for index, (start, end) in enumerate(
        keep_ranges
    ):

        video_label = f"v{index}"
        audio_label = f"a{index}"

        filters.append(
            f"[0:v]"
            f"trim=start={start}:end={end},"
            f"setpts=PTS-STARTPTS"
            f"[{video_label}]"
        )

        filters.append(
            f"[0:a]"
            f"atrim=start={start}:end={end},"
            f"asetpts=PTS-STARTPTS"
            f"[{audio_label}]"
        )

        video_outputs.append(
            f"[{video_label}]"
        )

        audio_outputs.append(
            f"[{audio_label}]"
        )

    concat_inputs = ""

    for video, audio in zip(
        video_outputs,
        audio_outputs,
    ):
        concat_inputs += video + audio

    filters.append(
        f"{concat_inputs}"
        f"concat=n={len(keep_ranges)}:"
        f"v=1:a=1"
        f"[vout][aout]"
    )

    filter_complex = ";".join(filters)

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(INPUT_VIDEO),

        "-filter_complex",
        filter_complex,

        "-map",
        "[vout]",
        "-map",
        "[aout]",

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

        str(OUTPUT_VIDEO),
    ]

    run(command)


def main() -> int:

    print()
    print("========================================")
    print("        ShortsFactory Smart Edit")
    print("========================================")
    print()

    if not INPUT_VIDEO.exists():
        print(
            f"ERROR: Input video missing: "
            f"{INPUT_VIDEO}"
        )
        return 1

    data = load_json(SUBTITLES_PATH)

    words = data.get("words", [])

    if not isinstance(words, list) or not words:
        print(
            "ERROR: subtitles.json has no word "
            "timestamps."
        )
        return 1

    duration = get_video_duration(
        INPUT_VIDEO
    )

    cuts = detect_pause_cuts(
        words
    )

    keep_ranges = cuts_to_keep_ranges(
        cuts,
        duration,
    )

    removed_seconds = sum(
        float(cut["duration"])
        for cut in cuts
    )

    estimated_duration = (
        duration - removed_seconds
    )

    edit_plan = {
        "mode": "light",
        "original_duration_seconds": round(
            duration,
            3,
        ),
        "estimated_final_duration_seconds": round(
            estimated_duration,
            3,
        ),
        "removed_seconds": round(
            removed_seconds,
            3,
        ),
        "cut_count": len(cuts),
        "cuts": cuts,
        "keep_ranges": [
            {
                "start": start,
                "end": end,
                "duration": round(
                    end - start,
                    3,
                ),
            }
            for start, end in keep_ranges
        ],
    }

    with EDIT_PLAN_PATH.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            edit_plan,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"Original duration: "
        f"{duration:.2f}s"
    )

    print(
        f"Long pauses found: "
        f"{len(cuts)}"
    )

    print(
        f"Time removed: "
        f"{removed_seconds:.2f}s"
    )

    print(
        f"Estimated result: "
        f"{estimated_duration:.2f}s"
    )

    print(
        f"Edit plan: "
        f"{EDIT_PLAN_PATH}"
    )

    if not cuts:

        print()
        print(
            "No meaningful dead air detected."
        )

        print(
            "Creating passthrough tight-edit video..."
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(INPUT_VIDEO),
            "-c",
            "copy",
            str(OUTPUT_VIDEO),
        ]

        run(command)

    else:

        print()
        print(
            "Rendering tightened clip..."
        )

        render_tight_edit(
            keep_ranges
        )

    print()
    print(
        f"Tight edit created: "
        f"{OUTPUT_VIDEO}"
    )

    print("Done.")

    return 0


if __name__ == "__main__":
    sys.exit(main())