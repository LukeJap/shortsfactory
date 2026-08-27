"""Command-line entry point for the isolated Track A pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .identity import (
    IdentityConfirmationRequired,
    IdentityQuery,
    parse_compound_title,
)
from .llm import OllamaJsonModel
from .pipeline import DEFAULT_OUTPUT_DIR, run_recap_pipeline
from .source import SourceMismatchError, detect_scene_boundaries
from .research import ResearchUnavailableError
from .writer import RecapWriter


ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build grounded ShortsFactory AI Recap Track A artifacts."
    )
    parser.add_argument("--content-type", choices=("tv", "movie"), required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--season", type=int)
    parser.add_argument("--episode", type=int)
    parser.add_argument("--segment-title", default="")
    parser.add_argument("--episode-title", default="")
    parser.add_argument("--container-title", default="")
    parser.add_argument("--container-episode", type=int)
    parser.add_argument("--source-runtime", type=float)
    parser.add_argument("--source", required=True)
    parser.add_argument("--transcript", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--confirm-candidate",
        type=int,
        help="Zero-based candidate index from episode_identity.json.",
    )
    parser.add_argument(
        "--use-ollama",
        action="store_true",
        help="Use the configured local Ollama model for recap prose.",
    )
    parser.add_argument(
        "--detect-scenes",
        action="store_true",
        help="Run FFmpeg scene scoring for the source before alignment.",
    )
    parser.add_argument(
        "--visual-evidence",
        default="",
        help="Optional JSON list of visual observations with start/end fields.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    transcript = (
        Path(args.transcript).expanduser().resolve()
        if args.transcript
        else None
    )
    if transcript is None:
        candidate = ROOT / "output" / "subtitles.json"
        if candidate.exists():
            transcript = candidate

    visual_evidence: list[dict[str, Any]] | None = None
    if args.visual_evidence:
        try:
            visual_evidence = json.loads(
                Path(args.visual_evidence).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: Could not read visual evidence: {exc}")
            return 1
        if not isinstance(visual_evidence, list):
            print("ERROR: Visual evidence must be a JSON list.")
            return 1

    scene_boundaries: list[float] = []
    if args.detect_scenes:
        try:
            scene_boundaries = detect_scene_boundaries(source)
        except Exception as exc:
            print(f"WARNING: Scene detection unavailable: {exc}")

    ollama_model = OllamaJsonModel() if args.use_ollama else None
    writer = RecapWriter(ollama_model) if ollama_model is not None else None
    try:
        artifacts = run_recap_pipeline(
            query=IdentityQuery(
                content_type=args.content_type,
                title=args.title,
                season=args.season,
                container_episode=(
                    args.container_episode
                    if args.container_episode is not None
                    else args.episode
                ),
                container_title=(
                    args.container_title
                    or args.episode_title
                    or args.segment_title
                ),
                segment_titles=tuple(
                    parse_compound_title(
                        args.episode_title or args.segment_title
                    )
                ),
                source_filename=source.name,
                source_runtime_seconds=args.source_runtime,
            ),
            source_video=source,
            transcript_path=transcript,
            output_dir=Path(args.output_dir),
            confirm_index=args.confirm_candidate,
            writer=writer,
            visual_evidence=visual_evidence,
            scene_boundaries=scene_boundaries,
            semantic_model=ollama_model,
        )
    except IdentityConfirmationRequired as exc:
        print(f"IDENTITY CONFIRMATION REQUIRED: {exc}")
        return 2
    except (SourceMismatchError, ResearchUnavailableError) as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Track A recap artifacts written:")
    for name, path in artifacts.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
