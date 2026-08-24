"""
STEP 9 of the render pipeline: composites emoji reaction overlays
(output/emoji_events.json, written by make_captions.py's
choose_emoji_events() during the real render, or previewed early by
emoji_planner.py) onto the captioned video via an ffmpeg overlay filter.
Reads each event's stored position_x/position_y fraction if present
(set via drag or the double-click picker in gui_app/mixins/
emoji_preview.py), falling back to a fixed 4-slot round-robin table for
events that predate that feature. Downloads/caches Twemoji PNGs for
plain-unicode emoji; local "reaction" image assets (assets/emoji/) are
used directly.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .canvas_config import OUTPUT_HEIGHT as CANVAS_HEIGHT, OUTPUT_WIDTH as CANVAS_WIDTH
except ImportError:
    from canvas_config import OUTPUT_HEIGHT as CANVAS_HEIGHT, OUTPUT_WIDTH as CANVAS_WIDTH

try:
    from .pipeline_paths import EMOJI_EVENTS_PATH as EVENTS_PATH
except ImportError:
    from pipeline_paths import EMOJI_EVENTS_PATH as EVENTS_PATH


ROOT = Path(__file__).resolve().parent.parent

EMOJI_DIR = ROOT / "assets" / "emoji"

INPUT_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_captioned.mp4"
)

OUTPUT_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_final.mp4"
)

TWEMOJI_BASE = (
    "https://cdn.jsdelivr.net/gh/"
    "jdecked/twemoji@17.0.3/"
    "assets/72x72/"
)


# Simple emoji style.
EMOJI_DURATION = 1.50
EMOJI_SIZE = 175

# Legacy fixed round-robin corner positions (top-left of the EMOJI_SIZE box,
# in canvas pixels). Still used as the *default* position for any emoji
# event that has no stored position_x/position_y (new events get one of
# these converted to a fraction; events predating this feature have neither
# field and fall back to this table directly).
EMOJI_DEFAULT_POSITIONS_PX = [
    (760, 1300),
    (170, 1340),
    (750, 1430),
    (190, 1460),
]


def coerce_emoji_fraction(value) -> float:

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(1.0, number))


def coerce_emoji_scale(value) -> float:

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0

    return max(0.5, min(2.0, number))


def emoji_pixel_to_fraction(
    x: float,
    y: float,
    size: float = EMOJI_SIZE,
) -> tuple[float, float]:
    # (x, y) is the top-left corner of the emoji's on-screen box (EMOJI_SIZE
    # * that event's own scale, or plain EMOJI_SIZE for callers that don't
    # know about per-event scale), in canvas pixels. 0.0 = flush against the
    # left/top edge, 1.0 = flush against the right/bottom edge.
    x_span = max(1, CANVAS_WIDTH - size)
    y_span = max(1, CANVAS_HEIGHT - size)

    return (
        coerce_emoji_fraction(x / x_span),
        coerce_emoji_fraction(y / y_span),
    )


def emoji_fraction_to_pixel(
    position_x,
    position_y,
    size: float = EMOJI_SIZE,
) -> tuple[float, float]:

    x_span = max(1, CANVAS_WIDTH - size)
    y_span = max(1, CANVAS_HEIGHT - size)

    return (
        coerce_emoji_fraction(position_x) * x_span,
        coerce_emoji_fraction(position_y) * y_span,
    )


def event_default_position_px(index: int) -> tuple[int, int]:

    return EMOJI_DEFAULT_POSITIONS_PX[
        index % len(EMOJI_DEFAULT_POSITIONS_PX)
    ]


def normalize_emoji(emoji: str) -> str:

    emoji = str(emoji).strip()

    if "\\U" in emoji or "\\u" in emoji:

        try:
            emoji = (
                emoji
                .encode("utf-8")
                .decode("unicode_escape")
            )

        except UnicodeDecodeError:
            pass

    return emoji


def emoji_filename(emoji: str) -> str:

    emoji = normalize_emoji(emoji)

    codepoints = []

    for char in emoji:

        value = ord(char)

        if value in (0xFE0E, 0xFE0F):
            continue

        codepoints.append(
            f"{value:x}"
        )

    return "-".join(codepoints) + ".png"


def download_emoji(
    emoji: str,
) -> Path | None:

    EMOJI_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    emoji = normalize_emoji(emoji)

    filename = emoji_filename(emoji)

    output = EMOJI_DIR / filename

    if (
        output.exists()
        and output.stat().st_size > 0
    ):

        print(
            f"Using cached emoji: {emoji}"
        )

        return output

    url = TWEMOJI_BASE + filename

    print(
        f"Downloading emoji: {emoji}"
    )

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "ShortsFactory/1.0"
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=15,
        ) as response:

            data = response.read()

        with output.open(
            "wb"
        ) as f:

            f.write(data)

        return output

    except Exception as exc:

        print(
            f"WARNING: Could not download "
            f"{emoji}: {exc}"
        )

        output.unlink(
            missing_ok=True
        )

        return None


def resolve_event_asset(
    event: dict,
) -> Path | None:

    raw_asset = event.get(
        "asset_path",
        "",
    )

    if not raw_asset:
        raw_asset = event.get(
            "asset",
            "",
        )

    raw_asset = str(
        raw_asset
    ).strip()

    if not raw_asset:
        return None

    path = Path(
        raw_asset
    )

    if not path.is_absolute():
        path = ROOT / path

    try:
        resolved = path.resolve()
        emoji_root = EMOJI_DIR.resolve()

        if not resolved.is_relative_to(
            emoji_root
        ):
            print(
                "WARNING: Ignoring emoji asset outside "
                f"asset folder: {raw_asset}"
            )
            return None

    except OSError:
        # Path resolution genuinely failed (e.g. a symlink loop) -- reject
        # rather than silently skip the asset-folder containment check
        # above.
        print(
            f"WARNING: Could not resolve emoji asset path: {raw_asset}"
        )
        return None

    if not resolved.exists():
        print(
            f"WARNING: Emoji asset not found: "
            f"{raw_asset}"
        )
        return None

    if resolved.suffix.lower() not in {
        ".gif",
        ".png",
    }:
        print(
            "WARNING: Unsupported emoji asset type: "
            f"{resolved.name}"
        )
        return None

    return resolved


def run(
    command: list[str],
) -> None:

    print()
    print("Running:")
    print(" ".join(command))
    print()

    result = subprocess.run(
        command,
        cwd=ROOT,
    )

    if result.returncode != 0:

        raise RuntimeError(
            f"FFmpeg failed with exit code "
            f"{result.returncode}"
        )


def prepare_emoji_events(
    events: list[Any],
) -> list[dict[str, Any]]:
    """
    Resolve each emoji event to a usable local asset (a saved reaction
    image/GIF, or a freshly downloaded emoji glyph), skipping events with
    neither an emoji nor an asset and events whose asset can't be
    resolved/downloaded.
    """
    prepared = []

    for event in events:

        asset_path = resolve_event_asset(
            event
        )

        emoji = normalize_emoji(
            event.get(
                "emoji",
                "",
            )
        )

        if not emoji and not asset_path:
            continue

        if asset_path:
            path = asset_path
            emoji_label = str(
                event.get(
                    "asset_description",
                    "",
                )
                or event.get(
                    "emoji",
                    "",
                )
                or path.stem
            )

            print(
                f"Using local emoji asset: "
                f"{emoji_label}"
            )

        else:
            path = download_emoji(
                emoji
            )
            emoji_label = emoji

        if not path:
            continue

        start = float(
            event.get(
                "start",
                0,
            )
        )

        try:
            requested_end = float(
                event.get(
                    "end",
                    start + EMOJI_DURATION,
                )
            )
        except (TypeError, ValueError):
            requested_end = start + EMOJI_DURATION

        # Trust a stored end (e.g. lengthened/shortened by a manual drag
        # on the editor timeline) over the default duration -- only fall
        # back to it when the stored value is missing or invalid.
        end = (
            requested_end
            if requested_end > start
            else start + EMOJI_DURATION
        )

        prepared.append(
            {
                "emoji": emoji_label,
                "path": path,
                "start": start,
                "end": end,
                "position_x": event.get("position_x"),
                "position_y": event.get("position_y"),
            }
        )

    return prepared


def build_emoji_inputs(
    prepared: list[dict[str, Any]],
) -> list[str]:
    """
    Build the ffmpeg -i input arguments: one per emoji asset, looped
    (static images) or ignore-looped (animated GIFs) so each covers its
    whole overlay window.
    """
    inputs = [
        "-i",
        str(INPUT_PATH),
    ]

    for event in prepared:

        path = Path(
            event["path"]
        )

        if path.suffix.lower() == ".gif":

            inputs.extend(
                [
                    "-ignore_loop",
                    "0",

                    "-i",
                    str(
                        path
                    ),
                ]
            )

        else:

            inputs.extend(
                [
                    "-loop",
                    "1",

                    "-framerate",
                    "30",

                    "-i",
                    str(
                        path
                    ),
                ]
            )

    return inputs


def build_emoji_filter_complex(
    prepared: list[dict[str, Any]],
) -> tuple[str, str]:
    """
    Build the filter_complex chain overlaying every prepared emoji onto
    the base video with a gentle upward-float + horizontal-sway
    animation, chaining each overlay stage onto the previous stage's
    output. Returns (filter_complex, final_output_label) -- the label
    the caller should -map.
    """
    filters = []

    current = "[0:v]"

    for index, event in enumerate(
        prepared
    ):

        input_label = (
            f"[{index + 1}:v]"
        )

        emoji_label = (
            f"[emoji{index}]"
        )

        output_label = (
            f"[ov{index}]"
        )

        start = float(
            event["start"]
        )

        end = float(
            event["end"]
        )

        # The trimmed clip and its float-upward animation must span the
        # actual requested display window (which may have been lengthened
        # or shortened by a manual drag on the editor timeline), not a
        # fixed constant -- otherwise the overlay silently vanishes at
        # the old fixed duration regardless of what `end` says.
        duration = max(
            0.1,
            end - start,
        )

        event_scale = coerce_emoji_scale(
            event.get("scale", 1.0)
        )
        event_size = max(
            1,
            round(EMOJI_SIZE * event_scale),
        )

        stored_x = event.get("position_x")
        stored_y = event.get("position_y")

        if stored_x is not None and stored_y is not None:
            x, y = emoji_fraction_to_pixel(
                stored_x,
                stored_y,
                size=event_size,
            )
        else:
            x, y = event_default_position_px(
                index
            )

        # Prepare a normal static full-color emoji.
        #
        # It has its own (possibly manually resized) size and its own
        # finite duration.
        filters.append(
            f"{input_label}"
            f"format=rgba,"
            f"scale="
            f"{event_size}:"
            f"{event_size},"
            f"trim="
            f"duration={duration},"
            f"setpts="
            f"PTS-STARTPTS+{start}/TB"
            f"{emoji_label}"
        )

        # Gentle motion:
        #
        # - slowly floats upward
        # - tiny horizontal sway
        #
        # No bouncing or duplicate layers.
        local_time = (
            f"(t-{start})"
        )

        moving_x = (
            f"{x}"
            f"+8*sin("
            f"4*{local_time}"
            f")"
        )

        moving_y = (
            f"{y}"
            f"-22*"
            f"({local_time}/"
            f"{duration})"
        )

        filters.append(
            f"{current}"
            f"{emoji_label}"
            f"overlay="
            f"x='{moving_x}':"
            f"y='{moving_y}':"
            f"eof_action=pass:"
            f"repeatlast=0:"
            f"enable="
            f"'between(t,{start},{end})'"
            f"{output_label}"
        )

        current = output_label

    return ";".join(filters), current


def main() -> int:

    print()
    print(
        "=== STEP 9: Adding colorful emojis ==="
    )
    print()

    if not INPUT_PATH.exists():

        print(
            f"ERROR: Video not found: "
            f"{INPUT_PATH}"
        )

        return 1

    if not EVENTS_PATH.exists():

        print(
            "No emoji events found."
        )

        return 0

    with EVENTS_PATH.open(
        "r",
        encoding="utf-8",
    ) as f:

        data = json.load(f)

    events = data.get(
        "events",
        [],
    )

    if not events:

        print(
            "No emoji events to render."
        )

        return 0

    prepared = prepare_emoji_events(
        events
    )

    if not prepared:

        print(
            "No emoji assets available."
        )

        return 0

    print(
        f"Emoji events ready: "
        f"{len(prepared)}"
    )

    inputs = build_emoji_inputs(
        prepared
    )

    filter_complex, current = build_emoji_filter_complex(
        prepared
    )

    command = [
        "ffmpeg",
        "-y",

        *inputs,

        "-filter_complex",
        filter_complex,

        "-map",
        current,

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

        "-shortest",

        "-movflags",
        "+faststart",

        str(OUTPUT_PATH),
    ]

    run(command)

    # Replace working captioned video with final version.
    if OUTPUT_PATH.exists():

        INPUT_PATH.unlink(
            missing_ok=True
        )

        OUTPUT_PATH.replace(
            INPUT_PATH
        )

    print()
    print(
        "Emoji overlay complete."
    )

    print(
        f"Final video: "
        f"{INPUT_PATH}"
    )

    print()

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
