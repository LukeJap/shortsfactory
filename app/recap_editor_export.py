"""CLI boundary for exporting an already assembled AI Recap editor timeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from recap_media.combined_captions import (
    load_combined_recap_caption_plan,
    write_combined_recap_caption_ass,
)
from recap_media.effects import RecapEffectsError, load_recap_effects
from recap_media.render import RecapRenderError, render_recap_editor_export


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export an AI Recap from current editor state.")
    parser.add_argument("--editor-base", required=True)
    parser.add_argument("--effects-plan", required=True)
    parser.add_argument("--editor-plan", required=True)
    parser.add_argument("--caption-plan", required=True)
    parser.add_argument("--caption-ass", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--caption-position-x", type=float)
    parser.add_argument("--caption-position-y", type=float)
    parser.add_argument("--caption-scale", type=float)
    parser.add_argument("--music")
    parser.add_argument("--music-volume", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    editor_base = Path(args.editor_base).resolve()
    effects_path = Path(args.effects_plan).resolve()
    editor_plan = Path(args.editor_plan).resolve()
    caption_plan_path = Path(args.caption_plan).resolve()
    caption_ass_path = Path(args.caption_ass).resolve()
    output_path = Path(args.output).resolve()
    music_path = Path(args.music).resolve() if args.music else None

    try:
        captions = load_combined_recap_caption_plan(caption_plan_path)
        write_combined_recap_caption_ass(
            captions,
            caption_ass_path,
            {
                "caption_position_x": args.caption_position_x,
                "caption_position_y": args.caption_position_y,
                "caption_scale": args.caption_scale,
            },
        )
        effects = load_recap_effects(
            effects_path=effects_path,
            editor_plan_path=editor_plan,
            render_planned_effects=True,
        )
        print("=== AI RECAP EDITOR EXPORT ===")
        print(f"Editor base: {editor_base}")
        print(f"Combined Recap captions: {len(captions.get('cues', []))}")
        print(f"Active Recap SFX: {len(effects.get('sfx_events', []))}")
        print(f"Active Recap emoji: {len(effects.get('emoji_events', []))}")
        print(f"Active Recap motion: {len(effects.get('motion_events', []))}")
        print(f"Active Recap visual FX: {len(effects.get('visual_fx_events', []))}")
        if music_path is not None:
            print(f"Music: {music_path.name} at {max(0.0, min(1.0, args.music_volume)):.2f}")
        render_recap_editor_export(
            editor_base,
            effects,
            caption_ass_path,
            output_path,
            music_path=music_path,
            music_volume=args.music_volume,
        )
    except (OSError, ValueError, RecapEffectsError, RecapRenderError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Recap final export: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
