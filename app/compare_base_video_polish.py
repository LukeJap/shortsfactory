"""
Developer utility for rendering A/B/C/D base video polish comparison clips.

This script is intentionally isolated from ShortsFactory's production render
pipeline. It renders the same source range four times with identical crop,
audio, and encode settings; only the optional base polish filters differ.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from .base_video_polish import polish_filters
    from .canvas_config import crop_to_fill_filter
except ImportError:
    from base_video_polish import polish_filters
    from canvas_config import crop_to_fill_filter


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output" / "polish_comparison"
FRAMES_DIR = OUTPUT_DIR / "frames"

VARIANTS = (
    ("A", "OFF", "A_OFF.mp4"),
    ("B", "POP", "B_POP.mp4"),
    ("C", "WARM_POP", "C_WARM_POP.mp4"),
    ("D", "VIRAL_POP", "D_VIRAL_POP.mp4"),
)

SAMPLE_POINTS = (
    (25, 0.25),
    (50, 0.50),
    (75, 0.75),
)


def parse_seconds(
    value: str,
) -> float:
    """
    Parse numeric seconds or a timestamp shaped like HH:MM:SS.mmm / MM:SS.mmm.
    """

    text = str(
        value
    ).strip()
    if not text:
        raise argparse.ArgumentTypeError(
            "timestamp cannot be empty"
        )

    try:
        seconds = float(
            text
        )
    except ValueError:
        parts = text.split(
            ":"
        )
        if len(parts) not in {
            2,
            3,
        }:
            raise argparse.ArgumentTypeError(
                f"invalid timestamp: {value}"
            )

        try:
            numeric_parts = [
                float(
                    part
                )
                for part in parts
            ]
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"invalid timestamp: {value}"
            ) from exc

        if len(numeric_parts) == 2:
            minutes, seconds_part = numeric_parts
            seconds = minutes * 60 + seconds_part
        else:
            hours, minutes, seconds_part = numeric_parts
            seconds = hours * 3600 + minutes * 60 + seconds_part

    if seconds < 0:
        raise argparse.ArgumentTypeError(
            "timestamp must be non-negative"
        )

    return seconds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render isolated A/B/C/D base video polish comparison clips."
        )
    )

    parser.add_argument(
        "--source",
        required=True,
        help="Source video path.",
    )
    parser.add_argument(
        "--start",
        required=True,
        type=parse_seconds,
        help="Start time in seconds or timestamp form.",
    )
    parser.add_argument(
        "--end",
        required=True,
        type=parse_seconds,
        help="End time in seconds or timestamp form.",
    )

    return parser.parse_args()


def resolve_source(
    value: str,
) -> Path:
    path = Path(
        value
    ).expanduser()

    if not path.is_absolute():
        path = ROOT / path

    return path.resolve()


def ffmpeg_executable() -> str:
    configured = shutil.which(
        "ffmpeg"
    )
    if configured:
        return configured

    home_candidate = (
        Path.home()
        / "ffmpeg"
        / "bin"
        / "ffmpeg.exe"
    )
    if home_candidate.exists():
        return str(
            home_candidate
        )

    return "ffmpeg"


def base_filter_chain(
    preset: str,
) -> str:
    filters = [
        crop_to_fill_filter(),
    ]

    filters.extend(
        polish_filters(
            preset
        )
    )
    filters.extend(
        [
            "setsar=1",
            "format=yuv420p",
        ]
    )

    return ",".join(
        filters
    )


def run_command(
    command: list[str],
) -> None:
    print(
        "Running: "
        + " ".join(
            command
        ),
        flush=True,
    )
    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def render_variant(
    ffmpeg: str,
    source: Path,
    start: float,
    end: float,
    preset: str,
    output_path: Path,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(
            source
        ),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-map_chapters",
        "-1",
        "-vf",
        base_filter_chain(
            preset
        ),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(
            output_path
        ),
    ]

    run_command(
        command
    )


def extract_frame(
    ffmpeg: str,
    video_path: Path,
    sample_seconds: float,
    frame_path: Path,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{sample_seconds:.3f}",
        "-i",
        str(
            video_path
        ),
        "-frames:v",
        "1",
        "-update",
        "1",
        str(
            frame_path
        ),
    ]

    run_command(
        command
    )


def create_contact_sheet(
    ffmpeg: str,
    frame_paths: list[Path],
    output_path: Path,
) -> None:
    command = [
        ffmpeg,
        "-y",
    ]

    for frame_path in frame_paths:
        command.extend(
            [
                "-i",
                str(
                    frame_path
                ),
            ]
        )

    command.extend(
        [
            "-filter_complex",
            (
                "[0:v]scale=540:960[a];"
                "[1:v]scale=540:960[b];"
                "[2:v]scale=540:960[c];"
                "[3:v]scale=540:960[d];"
                "[a][b]hstack=inputs=2[top];"
                "[c][d]hstack=inputs=2[bottom];"
                "[top][bottom]vstack=inputs=2[out]"
            ),
            "-map",
            "[out]",
            "-update",
            "1",
            str(
                output_path
            ),
        ]
    )

    run_command(
        command
    )


def create_split_frame(
    ffmpeg: str,
    left_frame: Path,
    right_frame: Path,
    output_path: Path,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(
            left_frame
        ),
        "-i",
        str(
            right_frame
        ),
        "-filter_complex",
        (
            "[0:v]crop=iw/2:ih:0:0[left];"
            "[1:v]crop=iw/2:ih:iw/2:0[right];"
            "[left][right]hstack=inputs=2[out]"
        ),
        "-map",
        "[out]",
        "-update",
        "1",
        str(
            output_path
        ),
    ]

    run_command(
        command
    )


def main() -> int:
    args = parse_args()

    source = resolve_source(
        args.source
    )
    if not source.exists():
        print(
            f"ERROR: Source video does not exist: {source}",
            file=sys.stderr,
        )
        return 1

    start = float(
        args.start
    )
    end = float(
        args.end
    )
    if end <= start:
        print(
            "ERROR: --end must be greater than --start.",
            file=sys.stderr,
        )
        return 1

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    FRAMES_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    ffmpeg = ffmpeg_executable()
    duration = end - start
    rendered: list[tuple[str, str, Path]] = []

    print(
        "Rendering base video polish comparison clips...",
        flush=True,
    )
    print(
        f"Source: {source}",
        flush=True,
    )
    print(
        f"Range: {start:.3f}s -> {end:.3f}s",
        flush=True,
    )

    for label, preset, filename in VARIANTS:
        output_path = OUTPUT_DIR / filename
        print(
            f"\n{label}: {preset}",
            flush=True,
        )
        render_variant(
            ffmpeg,
            source,
            start,
            end,
            preset,
            output_path,
        )
        rendered.append(
            (
                label,
                preset,
                output_path,
            )
        )

    print(
        "\nExtracting matching frame samples...",
        flush=True,
    )
    midpoint_frames: dict[str, Path] = {}

    for percent, fraction in SAMPLE_POINTS:
        sample_seconds = duration * fraction
        frame_paths: list[Path] = []
        for label, preset, video_path in rendered:
            frame_path = FRAMES_DIR / f"{label}_{preset}_{percent}.png"
            extract_frame(
                ffmpeg,
                video_path,
                sample_seconds,
                frame_path,
            )
            frame_paths.append(
                frame_path
            )

            if percent == 50:
                full_frame_path = FRAMES_DIR / f"{label}_{preset}.png"
                extract_frame(
                    ffmpeg,
                    video_path,
                    sample_seconds,
                    full_frame_path,
                )
                midpoint_frames[preset] = full_frame_path

        create_contact_sheet(
            ffmpeg,
            frame_paths,
            FRAMES_DIR / f"comparison_{percent}.png",
        )

    if {
        "OFF",
        "WARM_POP",
    }.issubset(midpoint_frames):
        create_split_frame(
            ffmpeg,
            midpoint_frames["OFF"],
            midpoint_frames["WARM_POP"],
            FRAMES_DIR / "A_vs_C_split.png",
        )

    if {
        "OFF",
        "VIRAL_POP",
    }.issubset(midpoint_frames):
        create_split_frame(
            ffmpeg,
            midpoint_frames["OFF"],
            midpoint_frames["VIRAL_POP"],
            FRAMES_DIR / "A_vs_D_split.png",
        )

    print(
        "\nBase video polish comparison complete.",
        flush=True,
    )
    print(
        f"Clips: {OUTPUT_DIR}",
        flush=True,
    )
    print(
        f"Frames/contact sheets: {FRAMES_DIR}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
