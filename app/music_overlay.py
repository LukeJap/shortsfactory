"""
Last post-processing step: mixes background music underneath the
finished, already-captioned video's dialogue track, ducking the music
volume around SFX events (read from output/sfx_plan.json) so they stay
audible. Invoked as a subprocess from the GUI's music mix flow
(gui_app/mixins/music.py) after the main render/caption pipeline
completes; replaces the video file in place via a temp file swap.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from .pipeline_paths import SFX_PLAN_PATH
except ImportError:
    from pipeline_paths import SFX_PLAN_PATH


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Mix background music underneath a finished ShortsFactory video."
        )
    )

    parser.add_argument(
        "--video",
        required=True,
        help="Finished video file to update.",
    )

    parser.add_argument(
        "--music",
        required=True,
        help="Background music/audio file.",
    )

    parser.add_argument(
        "--volume",
        type=float,
        default=0.18,
        help="Background music gain from 0.0 to 1.0.",
    )

    return parser.parse_args()


def run(command: list[str]) -> None:

    print("")
    print("Running:")
    print(" ".join(command))
    print("")

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def read_json(
    path: Path,
) -> dict:

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}

    return data if isinstance(data, dict) else {}


def sfx_events_for_ducking() -> list[dict]:
    """
    Load up to 16 applied SFX events (earliest-first) from
    output/sfx_plan.json as simple {start, end} windows, padded to a
    minimum 0.08s duration -- the time ranges music_volume_expression()
    will duck the music under.
    """

    plan = read_json(
        SFX_PLAN_PATH
    )

    if not plan.get(
        "applied",
        False,
    ):
        return []

    events = plan.get(
        "final_events",
        plan.get(
            "events",
            [],
        ),
    )

    if not isinstance(
        events,
        list,
    ):
        return []

    useful = []
    for event in events:
        if not isinstance(
            event,
            dict,
        ):
            continue
        try:
            start = float(
                event.get(
                    "start",
                    0.0,
                )
            )
            duration = float(
                event.get(
                    "duration",
                    0.25,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            continue
        useful.append(
            {
                "start": max(
                    0.0,
                    start,
                ),
                "end": max(
                    start + 0.08,
                    start + duration,
                ),
            }
        )

    useful.sort(
        key=lambda item: item["start"]
    )
    return useful[:16]


def music_volume_expression(
    volume: float,
    events: list[dict],
) -> str:
    """
    Build an ffmpeg `volume` filter time-varying expression: full volume
    by default, ducked to 58% (plus a 0.06s pre-roll/0.18s post-roll
    around each window, so the dip doesn't feel abrupt) during any SFX
    event's padded time range.

    Built by wrapping `expression` in a nested if(between(...), ducked,
    expression) for each event, iterating events in reverse (latest
    first) so that after all wraps, the chronologically *earliest*
    event ends up as the outermost/first-checked condition -- purely an
    artifact of how the nested string has to be assembled outside-in from
    an earliest-first list; the resulting expression's actual behavior at
    playback time doesn't depend on this ordering, since at most one
    event's window can be active at a given timestamp.
    """

    base = f"{volume:.4f}"
    ducked = f"{max(0.0, volume * 0.58):.4f}"
    expression = base

    for event in reversed(
        events
    ):
        start = max(
            0.0,
            float(
                event["start"]
            )
            - 0.06,
        )
        end = float(
            event["end"]
        ) + 0.18
        expression = (
            f"if(between(t,{start:.3f},{end:.3f}),"
            f"{ducked},{expression})"
        )

    return expression


def main() -> int:

    args = parse_args()

    video = Path(args.video).resolve()
    music = Path(args.music).resolve()

    if not video.exists():

        print(
            f"ERROR: Video not found: {video}"
        )

        return 1

    if not music.exists():

        print(
            f"ERROR: Music file not found: {music}"
        )

        return 1

    volume = max(
        0.0,
        min(
            1.0,
            float(args.volume),
        ),
    )

    temp_output = video.with_name(
        f"{video.stem}_music_tmp{video.suffix}"
    )

    if temp_output.exists():

        temp_output.unlink()

    print(
        "ShortsFactory music mixer starting..."
    )

    print(
        f"Video: {video}"
    )

    print(
        f"Music: {music}"
    )

    print(
        f"Background music gain: {volume:.2f}"
    )

    duck_events = sfx_events_for_ducking()

    if duck_events:
        print(
            f"Music ducking around {len(duck_events)} SFX event(s)."
        )

    # The source dialogue stays at full volume.
    # Music repeats if necessary and is mixed quietly underneath it.
    #
    # normalize=0 prevents amix from automatically reducing the dialogue
    # simply because a second track is present.
    music_expression = music_volume_expression(
        volume,
        duck_events,
    )

    filter_complex = (
        f"[0:a:0]volume=1.0[dialogue];"
        f"[1:a:0]volume='{music_expression}':eval=frame[music];"
        "[dialogue][music]"
        "amix="
        "inputs=2:"
        "duration=first:"
        "dropout_transition=2:"
        "normalize=0"
        "[mixed]"
    )

    command = [
        "ffmpeg",
        "-y",

        "-i",
        str(video),

        "-stream_loop",
        "-1",

        "-i",
        str(music),

        "-filter_complex",
        filter_complex,

        "-map",
        "0:v:0",

        "-map",
        "[mixed]",

        "-c:v",
        "copy",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-movflags",
        "+faststart",

        str(temp_output),
    ]

    try:

        run(
            command
        )

    except subprocess.CalledProcessError as exc:

        print(
            f"ERROR: FFmpeg music mix failed with exit code {exc.returncode}"
        )

        if temp_output.exists():

            temp_output.unlink()

        return exc.returncode or 1

    os.replace(
        temp_output,
        video,
    )

    print("")
    print(
        "Background music mixed successfully."
    )

    print(
        f"Updated video: {video}"
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
