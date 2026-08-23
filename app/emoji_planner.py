from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from .make_captions import choose_emoji_events, find_emoji
except ImportError:
    from make_captions import choose_emoji_events, find_emoji


ROOT = Path(__file__).resolve().parent.parent

DEFAULT_OUTPUT = ROOT / "output" / "emoji_events.json"


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
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))

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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "time_base": "absolute",
                    "selection_start": start,
                    "selection_end": end,
                    "events": [],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0

    candidates = build_emoji_candidates(words)
    events = choose_emoji_events(candidates, words, args.energy)

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
