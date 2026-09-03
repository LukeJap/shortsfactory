"""
STEP 3 ("Smart Edit"): detects dead-air/long pauses in the transcript and
renders a preview tight cut (short1_base.mp4 -> short1_tight.mp4),
writing output/edit_plan.json. This is a preview pass -- apply_smart_edit.py
(STEP 5) re-derives the authoritative cut list (merging in semantic +
manual cuts and a pacing safety budget) and re-encodes again, reusing
this stage's output only when the two cut lists end up identical.

That reuse only fires if this stage's own cut list already reflects
apply_smart_edit.py's "natural pacing guard" (its removal-budget/minimum-
spacing cap) -- otherwise a render with no approved semantic/manual cuts
still ends up computing two different cut lists (this stage's raw pause
cuts vs. STEP 5's capped ones) and pays for a fully redundant second
re-encode of the same clip. So this stage applies the exact same guard
(apply_smart_edit.apply_automatic_cut_safety(), with an empty semantic
list -- semantic_edit.py hasn't run yet at this point in the pipeline)
to its own pause cuts before rendering, so the common case (no approved
semantic/manual cuts) reliably matches and STEP 5 can skip its re-encode.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    from .visual_emphasis import (
        auto_cut_aggression_from_energy,
        auto_cut_profile,
        coerce_auto_cut_aggression,
        load_render_settings,
        normalize_energy,
    )
except ImportError:
    from visual_emphasis import (
        auto_cut_aggression_from_energy,
        auto_cut_profile,
        coerce_auto_cut_aggression,
        load_render_settings,
        normalize_energy,
    )

try:
    from .pipeline_paths import EDIT_PLAN_PATH, SUBTITLES_PATH
except ImportError:
    from pipeline_paths import EDIT_PLAN_PATH, SUBTITLES_PATH

try:
    from .apply_smart_edit import apply_automatic_cut_safety, extract_pause_cuts
except ImportError:
    from apply_smart_edit import apply_automatic_cut_safety, extract_pause_cuts


ROOT = Path(__file__).resolve().parent.parent

INPUT_VIDEO = ROOT / "output" / "rendered" / "short1_base.mp4"
OUTPUT_VIDEO = ROOT / "output" / "rendered" / "short1_tight.mp4"


# ============================================================
# SETTINGS
# ============================================================

# Defaults preserve backwards compatibility for direct function calls.
# Normal renders override these from the selected Edit Style profile.
DEFAULT_MIN_GAP_TO_EDIT = 1.15
DEFAULT_KEEP_GAP_SECONDS = 0.42

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
    *,
    min_gap_to_edit: float = DEFAULT_MIN_GAP_TO_EDIT,
    keep_gap_seconds: float = DEFAULT_KEEP_GAP_SECONDS,
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

        if gap < min_gap_to_edit:
            continue

        # Leave natural breathing room around the cut instead of
        # snapping directly from one spoken word to the next.
        remove_start = (
            current_end
            + keep_gap_seconds / 2
        )

        remove_end = (
            next_start
            - keep_gap_seconds / 2
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
        # Intermediate stage -- this output gets re-encoded again by
        # later pipeline stages before delivery, so "veryfast" trades away
        # rate-distortion optimization that would just be discarded.
        "-preset",
        "veryfast",
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

    if not isinstance(words, list):
        words = []

    if not words:
        print(
            "No word timestamps found; skipping pause-cut analysis "
            "and preserving the full selected clip."
        )

    settings = load_render_settings()
    energy = normalize_energy(
        settings.get(
            "edit_energy",
            "PUNCHY",
        )
    )
    raw_aggression = settings.get("auto_cut_aggression")
    aggression = (
        auto_cut_aggression_from_energy(energy)
        if raw_aggression is None
        else coerce_auto_cut_aggression(raw_aggression)
    )
    profile = auto_cut_profile(aggression)

    min_gap_to_edit = float(
        profile.get(
            "auto_cut_min_gap",
            DEFAULT_MIN_GAP_TO_EDIT,
        )
    )
    keep_gap_seconds = float(
        profile.get(
            "auto_cut_keep_gap",
            DEFAULT_KEEP_GAP_SECONDS,
        )
    )

    duration = get_video_duration(
        INPUT_VIDEO
    )

    print(
        f"Edit style: {energy}"
    )
    print(f"AutoCut aggression: {aggression}")
    print(
        "Pause rule: edit gaps >= "
        f"{min_gap_to_edit:.2f}s and retain "
        f"{keep_gap_seconds:.2f}s of breathing room"
    )
    print()

    cuts = (
        []
        if aggression <= 0
        else detect_pause_cuts(
            words,
            min_gap_to_edit=min_gap_to_edit,
            keep_gap_seconds=keep_gap_seconds,
        )
    )

    # Apply the same natural pacing guard STEP 5 (apply_smart_edit.py)
    # will apply -- see the module docstring. No semantic cuts exist yet
    # at this point in the pipeline, so this only ever trims pause cuts
    # for spacing/removal-budget reasons, the same as STEP 5 will when it
    # has no approved semantic/manual cuts to merge in either.
    guarded_pause_cuts, _, pacing_warning = apply_automatic_cut_safety(
        extract_pause_cuts({"cuts": cuts}),
        [],
        duration,
        profile=profile,
        energy=energy,
    )
    kept_ranges = {
        (round(float(cut["start"]), 3), round(float(cut["end"]), 3))
        for cut in guarded_pause_cuts
    }
    cuts = [
        cut
        for cut in cuts
        if (round(float(cut["start"]), 3), round(float(cut["end"]), 3)) in kept_ranges
    ]

    if pacing_warning:
        print(pacing_warning)
        print()

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
        "edit_energy": energy,
        "auto_cut_aggression": aggression,
        "pause_settings": {
            "minimum_gap_seconds": round(
                min_gap_to_edit,
                3,
            ),
            "retained_gap_seconds": round(
                keep_gap_seconds,
                3,
            ),
        },
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
