from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .editor_asset_plan import (
        clips_of_kind,
        editor_plan_context_matches,
        load_editor_asset_plan,
    )
except ImportError:
    from editor_asset_plan import (
        clips_of_kind,
        editor_plan_context_matches,
        load_editor_asset_plan,
    )

try:
    from .visual_emphasis import load_render_settings
except ImportError:
    from visual_emphasis import load_render_settings


ROOT = Path(__file__).resolve().parent.parent

VIDEO_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_tight.mp4"
)

PLAN_PATH = (
    ROOT
    / "output"
    / "ai_visual_plan.json"
)

MANIFEST_PATH = (
    ROOT
    / "output"
    / "ai_visual_assets"
    / "manifest.json"
)

COMBINED_PLAN_PATH = (
    ROOT
    / "output"
    / "combined_edit_plan.json"
)

TEMPORAL_PLAN_PATH = (
    ROOT
    / "output"
    / "temporal_edit_plan.json"
)

MAPPED_PLAN_PATH = (
    ROOT
    / "output"
    / "ai_visual_mapped_plan.json"
)

TEMP_PATH = (
    ROOT
    / "output"
    / "rendered"
    / "short1_visuals_tmp.mp4"
)

FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920
CARD_MAX_WIDTH = 842
CARD_MAX_HEIGHT = 882
CARD_BORDER = 12
CARD_Y_FACTOR = 0.22
CARD_DIM_ALPHA = 0.0
CONTAIN_DIM_ALPHA = 0.24


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

    return data if isinstance(
        data,
        dict,
    ) else {}


def keep_segments(
    plan: dict[str, Any],
    duration: float,
) -> list[
    tuple[float, float]
]:

    raw = plan.get(
        "keep_segments",
        [],
    )

    result: list[
        tuple[float, float]
    ] = []

    if isinstance(
        raw,
        list,
    ):

        for item in raw:

            if not isinstance(
                item,
                dict,
            ):
                continue

            try:
                start = float(
                    item.get(
                        "start",
                        0.0,
                    )
                )
                end = float(
                    item.get(
                        "end",
                        start,
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            if end > start:
                result.append(
                    (
                        start,
                        end,
                    )
                )

    if result:
        return result

    return [
        (
            0.0,
            duration,
        )
    ]


def map_source_interval_to_tight(
    source_start: float,
    source_end: float,
    selection_start: float,
    keeps: list[
        tuple[float, float]
    ],
) -> tuple[
    float,
    float,
] | None:

    base_start = max(
        0.0,
        source_start
        - selection_start,
    )

    base_end = max(
        base_start,
        source_end
        - selection_start,
    )

    accumulated = 0.0
    pieces: list[
        tuple[float, float]
    ] = []

    for keep_start, keep_end in keeps:

        overlap_start = max(
            base_start,
            keep_start,
        )

        overlap_end = min(
            base_end,
            keep_end,
        )

        if overlap_end > overlap_start:

            pieces.append(
                (
                    accumulated
                    + overlap_start
                    - keep_start,
                    accumulated
                    + overlap_end
                    - keep_start,
                )
            )

        accumulated += (
            keep_end
            - keep_start
        )

    if not pieces:
        return None

    return (
        pieces[0][0],
        pieces[-1][1],
    )


def map_tight_time_to_final(
    tight_time: float,
    temporal_plan: dict[str, Any],
) -> float:

    if not temporal_plan.get(
        "applied",
        False,
    ):
        return tight_time

    mapping = temporal_plan.get(
        "time_mapping",
        [],
    )

    if not isinstance(
        mapping,
        list,
    ):
        return tight_time

    final_duration = float(
        temporal_plan.get(
            "estimated_final_duration_seconds",
            tight_time,
        )
        or tight_time
    )

    previous_output = 0.0

    for segment in mapping:

        if not isinstance(
            segment,
            dict,
        ):
            continue

        if segment.get(
            "kind"
        ) != "source":
            continue

        source_start = float(
            segment.get(
                "source_start",
                0.0,
            )
            or 0.0
        )
        source_end = float(
            segment.get(
                "source_end",
                source_start,
            )
            or source_start
        )
        output_start = float(
            segment.get(
                "output_start",
                previous_output,
            )
            or previous_output
        )
        output_end = float(
            segment.get(
                "output_end",
                output_start,
            )
            or output_start
        )
        speed = float(
            segment.get(
                "speed",
                1.0,
            )
            or 1.0
        )

        if source_start <= tight_time <= source_end:
            return max(
                0.0,
                min(
                    final_duration,
                    output_start
                    + (
                        tight_time
                        - source_start
                    )
                    / max(
                        0.001,
                        speed,
                    ),
                ),
            )

        previous_output = output_end

    return max(
        0.0,
        min(
            final_duration,
            previous_output,
        ),
    )


def map_tight_interval_to_final(
    tight_start: float,
    tight_end: float,
    temporal_plan: dict[str, Any],
) -> tuple[float, float]:

    final_start = map_tight_time_to_final(
        tight_start,
        temporal_plan,
    )
    final_end = map_tight_time_to_final(
        tight_end,
        temporal_plan,
    )

    return (
        final_start,
        max(
            final_start,
            final_end,
        ),
    )


def probe_duration(
    video_path: Path,
) -> float:

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(
                video_path
            ),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    return float(
        result.stdout.strip()
    )


def normalize_display_mode(
    value: str,
) -> str:

    normalized = str(
        value
        or ""
    ).strip().upper()
    if normalized in {
        "OVERLAY_CARD",
        "FULL_FRAME_CONTAIN",
        "FULL_FRAME_COVER",
    }:
        return normalized
    return "OVERLAY_CARD"


def coerce_scale(
    value: Any,
) -> float:

    try:
        number = float(
            value
        )
    except (
        TypeError,
        ValueError,
    ):
        number = 1.0

    return max(
        0.6,
        min(
            1.4,
            number,
        ),
    )


def scale_opacity(
    mode: str,
) -> float:

    if mode == "OVERLAY_CARD":
        return 0.985
    return 1.0


def build_filter(
    mapped_assets: list[
        dict[str, Any]
    ],
) -> str:

    chains = [
        "[0:v]setpts=PTS-STARTPTS[base0]"
    ]

    previous = "base0"

    for index, asset in enumerate(
        mapped_assets,
        start=1,
    ):

        start = float(
            asset["tight_start"]
        )

        end = float(
            asset["tight_end"]
        )
        mode = normalize_display_mode(
            asset.get(
                "display_mode",
                "OVERLAY_CARD",
            )
        )
        scale = coerce_scale(
            asset.get(
                "scale",
                1.0,
            )
        )
        dim_alpha = 0.0

        if mode == "FULL_FRAME_COVER":
            chains.append(
                (
                    f"[{index}:v]"
                    f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:"
                    "force_original_aspect_ratio=increase,"
                    f"crop={FRAME_WIDTH}:{FRAME_HEIGHT},"
                    "setsar=1"
                    f"[vis{index}]"
                )
            )
        elif mode == "FULL_FRAME_CONTAIN":
            contain_width = max(
                320,
                int(
                    round(
                        FRAME_WIDTH
                        * scale
                    )
                ),
            )
            contain_height = max(
                320,
                int(
                    round(
                        FRAME_HEIGHT
                        * scale
                    )
                ),
            )
            chains.append(
                (
                    f"[{index}:v]"
                    f"scale={contain_width}:{contain_height}:"
                    "force_original_aspect_ratio=decrease,"
                    "setsar=1,"
                    "format=rgba,"
                    f"colorchannelmixer=aa={scale_opacity(mode):.3f}"
                    f"[vis{index}]"
                )
            )
            dim_alpha = CONTAIN_DIM_ALPHA
        else:
            overlay_width = max(
                280,
                int(
                    round(
                        CARD_MAX_WIDTH
                        * scale
                    )
                ),
            )
            overlay_height = max(
                280,
                int(
                    round(
                        CARD_MAX_HEIGHT
                        * scale
                    )
                ),
            )
            chains.append(
                (
                    f"[{index}:v]"
                    f"scale={overlay_width}:{overlay_height}:"
                    "force_original_aspect_ratio=increase,"
                    f"crop={overlay_width}:{overlay_height},"
                    "setsar=1,"
                    "format=rgba,"
                    f"colorchannelmixer=aa={scale_opacity(mode):.3f}"
                    f"[vis{index}]"
                )
            )
            dim_alpha = CARD_DIM_ALPHA

        pre_overlay = previous
        if dim_alpha > 0.0:
            dim_label = f"dim{index}"
            chains.append(
                (
                    f"[{previous}]"
                    "drawbox="
                    "x=0:y=0:w=iw:h=ih:"
                    f"color=black@{dim_alpha:.3f}:"
                    "t=fill:"
                    f"enable='between(t,{start:.3f},{end:.3f})'"
                    f"[{dim_label}]"
                )
            )
            pre_overlay = dim_label

        output_label = (
            f"base{index}"
        )

        if mode == "FULL_FRAME_COVER":
            x_expr = "0"
            y_expr = "0"
        elif mode == "FULL_FRAME_CONTAIN":
            x_expr = "(W-w)/2"
            y_expr = "(H-h)/2"
        else:
            x_expr = "(W-w)/2"
            y_expr = f"max(110,(H-h)*{CARD_Y_FACTOR:.3f})"

        chains.append(
            (
                f"[{pre_overlay}]"
                f"[vis{index}]"
                "overlay="
                f"x='{x_expr}':"
                f"y='{y_expr}':"
                f"enable='between(t,{start:.3f},{end:.3f})'"
                f"[{output_label}]"
            )
        )

        previous = output_label

    chains.append(
        f"[{previous}]format=yuv420p[outv]"
    )

    return ";".join(
        chains
    )


def main() -> int:

    print(
        "ShortsFactory AI visual compositor starting...",
        flush=True,
    )

    if not VIDEO_PATH.exists():

        print(
            f"WARNING: Tight video does not exist: {VIDEO_PATH}",
            flush=True,
        )
        return 0

    if not PLAN_PATH.exists():

        print(
            "No AI visual plan exists; skipping visual compositing.",
            flush=True,
        )
        return 0

    if not MANIFEST_PATH.exists():

        print(
            "No generated AI visual assets exist; skipping visual compositing.",
            flush=True,
        )
        return 0

    plan = load_json(
        PLAN_PATH
    )

    manifest = load_json(
        MANIFEST_PATH
    )

    slots = plan.get(
        "slots",
        [],
    )

    assets = manifest.get(
        "assets",
        [],
    )

    if (
        not isinstance(
            slots,
            list,
        )
        or not isinstance(
            assets,
            list,
        )
        or not slots
        or not assets
    ):

        print(
            "Visual plan/assets are empty; skipping.",
            flush=True,
        )
        return 0

    selection_start = float(
        plan.get(
            "selection_start",
            0.0,
        )
        or 0.0
    )

    combined_plan = load_json(
        COMBINED_PLAN_PATH
    )
    temporal_plan = load_json(
        TEMPORAL_PLAN_PATH
    )

    try:
        original_duration = float(
            combined_plan.get(
                "original_duration_seconds",
                probe_duration(
                    VIDEO_PATH
                ),
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        original_duration = probe_duration(
            VIDEO_PATH
        )

    keeps = keep_segments(
        combined_plan,
        original_duration,
    )

    asset_map_by_index = {
        int(
            item.get(
                "slot_index",
                0,
            )
        ): item
        for item in assets
        if isinstance(
            item,
            dict,
        )
    }

    asset_map_by_id = {
        str(
            item.get(
                "slot_id",
                "",
            )
            or ""
        ): item
        for item in assets
        if isinstance(
            item,
            dict,
        )
        and item.get(
            "slot_id"
        )
    }

    editor_asset_plan = load_editor_asset_plan()
    render_settings = load_render_settings()
    source_video = str(
        render_settings.get(
            "source_video",
            "",
        )
        or ""
    )
    try:
        selection_start = float(
            render_settings.get(
                "selection_start",
                -1.0,
            )
        )
        selection_end = float(
            render_settings.get(
                "selection_end",
                -1.0,
            )
        )
    except (TypeError, ValueError):
        selection_start = -1.0
        selection_end = -1.0

    if editor_plan_context_matches(
        editor_asset_plan,
        source_video,
        selection_start,
        selection_end,
    ):
        editor_visual_clips = clips_of_kind(
            editor_asset_plan,
            "AI_VISUAL",
            active_only=True,
        )
    else:
        editor_visual_clips = []

    mapped_assets: list[
        dict[str, Any]
    ] = []

    visual_items: list[tuple[int, dict[str, Any]]] = []

    if editor_visual_clips:
        for clip_index, clip in enumerate(
            editor_visual_clips,
            start=1,
        ):
            slot = {
                "slot_id": clip.get(
                    "slot_id",
                    clip.get(
                        "id",
                        "",
                    ),
                ),
                "label": clip.get(
                    "label",
                    f"Visual {clip_index}",
                ),
                "start": clip.get(
                    "start",
                    0.0,
                ),
                "end": clip.get(
                    "end",
                    clip.get(
                        "start",
                        0.0,
                    ),
                ),
                "asset_path": clip.get(
                    "asset_path",
                    clip.get(
                        "active_variant_path",
                        "",
                    ),
                ),
                "enabled": clip.get(
                    "active",
                    True,
                ),
                "state": "READY",
                "editor_clip_id": clip.get(
                    "id",
                    "",
                ),
                "display_mode": clip.get(
                    "display_mode",
                    "OVERLAY_CARD",
                ),
                "scale": clip.get(
                    "scale",
                    1.0,
                ),
                "source_type": clip.get(
                    "source_type",
                    "ai_generated",
                ),
            }
            visual_items.append(
                (
                    clip_index,
                    slot,
                )
            )
    else:
        visual_items = list(
            enumerate(
                slots,
                start=1,
            )
        )

    for slot_index, slot in visual_items:

        if not isinstance(
            slot,
            dict,
        ):
            continue

        if slot.get(
            "enabled",
            True,
        ) is False:
            continue

        slot_state = str(
            slot.get(
                "state",
                "",
            )
            or ""
        ).upper()

        if slot_state == "FAILED":
            continue

        slot_id = str(
            slot.get(
                "slot_id",
                "",
            )
            or ""
        )

        direct_asset_path = str(
            slot.get(
                "asset_path",
                "",
            )
            or ""
        )

        asset = None

        if direct_asset_path:
            direct_path = Path(
                direct_asset_path
            )
            if direct_path.exists():
                asset = {
                    "path": str(
                        direct_path
                    ),
                    "state": "READY",
                }

        if asset is None:
            asset = (
                asset_map_by_id.get(
                    slot_id
                )
                if slot_id
                else None
            )

        if asset is None:
            asset = asset_map_by_index.get(
                slot_index
            )

        if not asset:
            continue

        asset_state = str(
            asset.get(
                "state",
                "",
            )
            or ""
        ).upper()

        if asset_state == "FAILED":
            continue

        path = Path(
            str(
                asset.get(
                    "path",
                    "",
                )
            )
        )

        if not path.exists():
            continue

        try:
            source_start = float(
                slot.get(
                    "start",
                    0.0,
                )
            )
            source_end = float(
                slot.get(
                    "end",
                    source_start,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        mapped = map_source_interval_to_tight(
            source_start,
            source_end,
            selection_start,
            keeps,
        )

        if mapped is None:
            continue

        tight_start, tight_end = mapped
        tight_start, tight_end = map_tight_interval_to_final(
            tight_start,
            tight_end,
            temporal_plan,
        )

        if tight_end - tight_start < 0.5:
            continue

        mapped_assets.append(
            {
                "path": path,
                "tight_start": tight_start,
                "tight_end": tight_end,
                "label": slot.get(
                    "label",
                    f"Visual {slot_index}",
                ),
                "display_mode": normalize_display_mode(
                    slot.get(
                        "display_mode",
                        "OVERLAY_CARD",
                    )
                ),
                "scale": round(
                    coerce_scale(
                        slot.get(
                            "scale",
                            1.0,
                        )
                    ),
                    2,
                ),
                "source_type": str(
                    slot.get(
                        "source_type",
                        "ai_generated",
                    )
                    or "ai_generated"
                ),
            }
        )

    if not mapped_assets:

        print(
            "All planned visuals were removed by edits/cuts; skipping.",
            flush=True,
        )
        return 0

    MAPPED_PLAN_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "source_plan": str(
                    PLAN_PATH
                ),
                "temporal_plan": str(
                    TEMPORAL_PLAN_PATH
                ),
                "temporal_applied": bool(
                    temporal_plan.get(
                        "applied",
                        False,
                    )
                ),
                "asset_count": len(
                    mapped_assets
                ),
                "assets": [
                    {
                        "path": str(
                            asset["path"]
                        ),
                        "start": round(
                            float(
                                asset["tight_start"]
                            ),
                            4,
                        ),
                        "end": round(
                            float(
                                asset["tight_end"]
                            ),
                            4,
                        ),
                        "label": asset.get(
                            "label",
                            "",
                        ),
                        "display_mode": asset.get(
                            "display_mode",
                            "OVERLAY_CARD",
                        ),
                        "scale": asset.get(
                            "scale",
                            1.0,
                        ),
                        "source_type": asset.get(
                            "source_type",
                            "ai_generated",
                        ),
                    }
                    for asset in mapped_assets
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(
            VIDEO_PATH
        ),
    ]

    for asset in mapped_assets:

        command.extend(
            [
                "-loop",
                "1",
                "-i",
                str(
                    asset["path"]
                ),
            ]
        )

    filter_complex = build_filter(
        mapped_assets
    )

    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
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
    )

    print(
        f"Compositing AI visuals: {len(mapped_assets)}",
        flush=True,
    )

    for index, asset in enumerate(
        mapped_assets,
        start=1,
    ):

        print(
            (
                f"Visual {index}: "
                f"{asset['tight_start']:.2f}s -> "
                f"{asset['tight_end']:.2f}s  //  "
                f"{asset['label']}"
            ),
            flush=True,
        )

    try:

        subprocess.run(
            command,
            cwd=ROOT,
            check=True,
        )

    except subprocess.CalledProcessError as exc:

        if TEMP_PATH.exists():

            try:
                TEMP_PATH.unlink()
            except OSError:
                pass

        print(
            (
                "WARNING: AI visual compositing failed with "
                f"exit code {exc.returncode}. "
                "Continuing with the source footage."
            ),
            flush=True,
        )

        return 0

    os.replace(
        TEMP_PATH,
        VIDEO_PATH,
    )

    print(
        "AI visual cutaways composited into tight video.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
