"""
Preview-only emoji planner: computes default emoji reaction events for
the current clip selection before any render has happened, so they're
visible to drag/edit in the GUI placement editor immediately (triggered
from gui_app/mixins/ai_visual_pipeline.py's "Plan Visuals" action, via
gui_app/mixins/emoji_preview.py). Writes output/emoji_events.json in
absolute source-video time (time_base: "absolute") since the clip hasn't
been cropped yet -- unlike the real render pass's make_captions.py, which
overwrites this same file in clip-relative time once it has been. Does
no ffmpeg/video work, just JSON in, JSON out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from .make_captions import (
        apply_emoji_position_overrides,
        choose_emoji_events,
        find_emoji,
    )
except ImportError:
    from make_captions import (
        apply_emoji_position_overrides,
        choose_emoji_events,
        find_emoji,
    )

try:
    from .emoji_overlay import (
        download_emoji,
        normalize_emoji,
        resolve_event_asset,
    )
except ImportError:
    from emoji_overlay import (
        download_emoji,
        normalize_emoji,
        resolve_event_asset,
    )

try:
    from .editor_asset_plan import (
        load_editor_asset_plan,
        save_editor_asset_plan,
        set_editor_plan_context,
    )
except ImportError:
    from editor_asset_plan import (
        load_editor_asset_plan,
        save_editor_asset_plan,
        set_editor_plan_context,
    )

try:
    from .visual_emphasis import load_render_settings
except ImportError:
    from visual_emphasis import load_render_settings

try:
    from .render import caption_anchor_y_px
except ImportError:
    from render import caption_anchor_y_px

try:
    from .pipeline_paths import EMOJI_EVENTS_PATH as DEFAULT_OUTPUT
except ImportError:
    from pipeline_paths import EMOJI_EVENTS_PATH as DEFAULT_OUTPUT


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Preview the emoji reactions ShortsFactory would pick for the "
            "current selection, before a full render runs. Writes "
            "output/emoji_events.json in absolute source-video time (unlike "
            "the real render pass, which overwrites this file in "
            "clip-relative time once the clip is actually cropped)."
        )
    )

    parser.add_argument("--transcript", required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--energy", default="PUNCHY")
    parser.add_argument(
        "--min-events",
        type=int,
        default=0,
        help=(
            "Minimum number of emoji reactions to generate, even if "
            "fewer natural keyword matches were found (0 = no forced "
            "minimum)."
        ),
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--editor-plan",
        action="store_true",
        help=(
            "Resolve each event's real asset file now (download/cache it) "
            "and write the plan into output/editor_asset_plan.json as "
            "EMOJI clips, so the final render reuses this exact plan "
            "instead of recomputing one from scratch."
        ),
    )

    return parser.parse_args()


def words_in_selection(
    transcript_path: Path,
    selection_start: float,
    selection_end: float,
) -> list[dict]:

    transcript = json.loads(
        transcript_path.read_text(encoding="utf-8")
    )

    raw_words = transcript.get("words", [])
    if not isinstance(raw_words, list):
        return []

    selected = []
    for word in raw_words:
        if not isinstance(word, dict):
            continue
        try:
            start = float(word.get("start", 0.0))
            end = float(word.get("end", start))
        except (TypeError, ValueError):
            continue

        if end <= selection_start or start >= selection_end:
            continue

        selected.append(word)

    return selected


def build_emoji_candidates(words: list[dict]) -> list[dict]:

    candidates = []
    index = 0

    while index < len(words):
        group_size = 2 if len(candidates) % 3 != 2 else 3
        group = words[index:index + group_size]

        if not group:
            break

        start = float(group[0]["start"])
        end = float(group[-1]["end"])

        emoji_match = find_emoji(group)
        if emoji_match:
            candidate = {"start": start, "end": end}
            candidate.update(emoji_match)
            candidates.append(candidate)

        index += len(group)

    return candidates


def merge_with_previous_preview_overrides(
    events: list[dict],
    output_path: Path,
) -> list[dict]:
    """
    Carry forward any manual drag/emoji-swap already made in the current
    preview (output/emoji_events.json) onto a freshly recomputed event
    set, so locking in an editor plan doesn't discard edits the user
    already made. Only applies when the existing file is in this
    planner's own absolute-time convention -- a file left over from an
    actual render (clip-relative time) can't be safely compared here.
    """

    if not output_path.exists():
        return events

    try:
        previous_data = json.loads(
            output_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return events

    if previous_data.get("time_base") != "absolute":
        return events

    previous_events = previous_data.get("events", [])
    if not isinstance(previous_events, list):
        return events

    return apply_emoji_position_overrides(
        events,
        previous_events,
    )


def write_editor_emoji_plan(
    events: list[dict],
    selection_start: float,
    selection_end: float,
) -> dict:
    """
    Resolve each event's real asset file (download/cache it now rather
    than at final-render time) and write the result into
    output/editor_asset_plan.json as EMOJI clips, replacing any previous
    EMOJI clips. This is what "pre-generates the assets": once this plan
    exists and its stored context still matches the current source
    video/selection, make_captions.py's real render pass reuses it
    verbatim (mapped through any cuts) instead of recomputing one from
    scratch.
    """

    resolved_clips = []

    for index, event in enumerate(events, start=1):

        asset_path = resolve_event_asset(
            event
        )
        if asset_path is None:
            asset_path = download_emoji(
                normalize_emoji(
                    event.get("emoji", "")
                )
            )
        if asset_path is None:
            continue

        label = str(
            event.get("asset_description")
            or event.get("emoji")
            or asset_path.stem
        )

        resolved_clips.append(
            {
                "id": f"emoji_auto_{index:02d}",
                "kind": "EMOJI",
                "start": float(
                    event.get("start", 0.0)
                ),
                "end": float(
                    event.get(
                        "end",
                        event.get("start", 0.0),
                    )
                ),
                "time_basis": "source",
                "emoji": str(
                    event.get("emoji", "")
                ),
                "asset_path": str(
                    asset_path
                ),
                "label": label,
                "position_x": event.get("position_x"),
                "position_y": event.get("position_y"),
                "active": True,
                "origin": "automatic",
            }
        )

    settings = load_render_settings()
    source_video = str(
        settings.get(
            "source_video",
            "",
        )
        or ""
    )

    plan = load_editor_asset_plan()
    plan = set_editor_plan_context(
        plan,
        source_video,
        selection_start,
        selection_end,
    )

    retained = [
        clip
        for clip in plan.get(
            "clips",
            [],
        )
        if isinstance(clip, dict)
        and str(
            clip.get(
                "kind",
                "",
            )
            or ""
        ).upper() != "EMOJI"
    ]
    retained.extend(
        resolved_clips
    )
    plan["clips"] = retained
    save_editor_asset_plan(
        plan
    )

    return {
        "event_count": len(
            resolved_clips
        ),
    }


def main() -> int:

    args = parse_args()

    transcript_path = Path(args.transcript).resolve()
    output_path = Path(args.output).resolve()

    start = max(0.0, float(args.start))
    end = max(start, float(args.end))

    print("ShortsFactory emoji preview planner starting...", flush=True)

    if not transcript_path.exists():
        print(
            "ERROR: Source transcript is not loaded. Run Find Best Clips first.",
            flush=True,
        )
        return 1

    words = words_in_selection(transcript_path, start, end)

    if not words:
        print("No usable transcript words in this selection.", flush=True)
        events = []
    else:
        candidates = build_emoji_candidates(words)
        caption_anchor_y = caption_anchor_y_px(load_render_settings())
        events = choose_emoji_events(
            candidates,
            words,
            args.energy,
            args.min_events,
            caption_anchor_y,
        )

    if args.editor_plan:
        events = merge_with_previous_preview_overrides(
            events,
            output_path,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "version": 1,
                "time_base": "absolute",
                "selection_start": start,
                "selection_end": end,
                "events": events,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Emoji preview events: {len(events)}", flush=True)
    print(f"Wrote: {output_path}", flush=True)

    if args.editor_plan:
        result = write_editor_emoji_plan(
            events,
            start,
            end,
        )
        print(
            f"Editor emoji clips: {result['event_count']}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
