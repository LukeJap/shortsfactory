from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent.parent

OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    "http://127.0.0.1:11434",
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "llama3.1:8b",
)

DEFAULT_OUTPUT = (
    ROOT
    / "output"
    / "ai_visual_plan.json"
)

MIN_SLOT_SECONDS = 1.4
MAX_SLOT_SECONDS = 3.8
MAX_SLOTS = 2


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Plan sparse AI visual / B-roll cutaway slots "
            "for a selected ShortsFactory clip."
        )
    )

    parser.add_argument(
        "--video",
        required=True,
    )

    parser.add_argument(
        "--transcript",
        required=True,
    )

    parser.add_argument(
        "--start",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--end",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--corrections",
        default=str(
            ROOT
            / "output"
            / "transcript_corrections.json"
        ),
    )

    parser.add_argument(
        "--manual-cuts",
        default=str(
            ROOT
            / "output"
            / "manual_edit_plan.json"
        ),
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT
        ),
    )

    return parser.parse_args()


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

    return (
        data
        if isinstance(
            data,
            dict,
        )
        else {}
    )


def correction_map(
    path: Path,
) -> dict[
    tuple[int, int],
    str
]:

    data = load_json(
        path
    )

    result: dict[
        tuple[int, int],
        str
    ] = {}

    raw = data.get(
        "corrections",
        [],
    )

    if not isinstance(
        raw,
        list,
    ):
        return result

    for item in raw:

        if not isinstance(
            item,
            dict,
        ):
            continue

        try:

            start_ms = int(
                round(
                    float(
                        item.get(
                            "start",
                            0.0,
                        )
                    )
                    * 1000
                )
            )

            end_ms = int(
                round(
                    float(
                        item.get(
                            "end",
                            0.0,
                        )
                    )
                    * 1000
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        text = str(
            item.get(
                "corrected_text",
                "",
            )
            or ""
        ).strip()

        if (
            end_ms > start_ms
            and text
        ):

            result[
                (
                    start_ms,
                    end_ms,
                )
            ] = text

    return result


def manual_cut_ranges(
    path: Path,
) -> list[
    tuple[float, float]
]:

    data = load_json(
        path
    )

    raw = data.get(
        "cuts",
        [],
    )

    result: list[
        tuple[float, float]
    ] = []

    if not isinstance(
        raw,
        list,
    ):
        return result

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

    return result


def overlaps_cut(
    start: float,
    end: float,
    cuts: list[
        tuple[float, float]
    ],
) -> bool:

    return any(
        (
            cut_end > start
            and cut_start < end
        )
        for cut_start, cut_end in cuts
    )


def selected_segments(
    transcript_path: Path,
    selection_start: float,
    selection_end: float,
    corrections: dict[
        tuple[int, int],
        str
    ],
    cuts: list[
        tuple[float, float]
    ],
) -> list[
    dict[str, Any]
]:

    transcript = load_json(
        transcript_path
    )

    raw = transcript.get(
        "segments",
        [],
    )

    segments: list[
        dict[str, Any]
    ] = []

    if not isinstance(
        raw,
        list,
    ):
        return segments

    for segment in raw:

        if not isinstance(
            segment,
            dict,
        ):
            continue

        try:

            start = float(
                segment.get(
                    "start",
                    0.0,
                )
            )

            end = float(
                segment.get(
                    "end",
                    start,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        if (
            end <= selection_start
            or start >= selection_end
        ):
            continue

        if overlaps_cut(
            start,
            end,
            cuts,
        ):
            continue

        key = (
            int(
                round(
                    start
                    * 1000
                )
            ),
            int(
                round(
                    end
                    * 1000
                )
            ),
        )

        text = corrections.get(
            key,
            str(
                segment.get(
                    "text",
                    "",
                )
                or ""
            ).strip(),
        )

        if not text:
            continue

        segments.append(
            {
                "start": start,
                "end": end,
                "text": text,
            }
        )

    return segments


def compact_transcript(
    segments: list[
        dict[str, Any]
    ],
) -> str:

    lines = []

    for segment in segments:

        lines.append(
            (
                f"[{segment['start']:.2f}"
                f"-{segment['end']:.2f}] "
                f"{segment['text']}"
            )
        )

    return "\n".join(
        lines
    )


def build_prompt(
    transcript_text: str,
    selection_start: float,
    selection_end: float,
) -> str:

    schema = {
        "slots": [
            {
                "start": "absolute source timestamp in seconds",
                "end": "absolute source timestamp in seconds",
                "label": "short 2-5 word label",
                "reason": "why a visual cutaway helps here",
                "visual_type": (
                    "ai_recreation | object_detail | environment | "
                    "graphic_explainer | archival_style"
                ),
                "prompt": "specific 9:16 generation prompt",
            }
        ]
    }

    return f"""
You are the visual-planning editor for ShortsFactory.

Your job is to propose sparse visual cutaways for ONE selected short-form clip.

The selected source range is:

{selection_start:.3f} -> {selection_end:.3f} seconds

TRANSCRIPT:

{transcript_text}

RULES:

1. Return 0, 1, or 2 visual slots maximum.
2. Do NOT add a visual simply because you can.
3. Add a visual only when it materially helps illustrate:
   - a concrete object
   - a place or environment
   - a historical/physical detail
   - a visual contrast
   - something difficult to understand from a talking head alone
4. Avoid covering the strongest facial reaction, punchline, or emotionally important source moment.
5. Prefer slots about 1.5-3.5 seconds long.
6. Every slot MUST stay entirely inside the selected source range.
7. Use only timestamps that overlap transcript content shown above.
8. Do not invent facts beyond the transcript.
9. The generation prompt must visually describe the exact idea being discussed.
10. Do not write generic prompts such as "cinematic scene" or "interesting visual."
11. If the source references a real copyrighted movie/TV scene or a real person,
    do not request a deceptive photorealistic duplicate of that exact copyrighted
    frame. Use an illustrative, documentary-style, graphic, object-detail,
    environment, or clearly recreated interpretation when appropriate.
12. The prompt should assume a vertical 9:16 image/video.
13. Do not put captions, logos, UI, or readable text in the generated visual unless
    the concept specifically requires a graphic explainer.
14. Return JSON only.

Return exactly this shape:

{json.dumps(schema, indent=2)}
""".strip()


def call_ollama(
    prompt: str,
) -> dict[str, Any]:

    response = requests.post(
        (
            OLLAMA_HOST.rstrip("/")
            + "/api/generate"
        ),
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.25,
            },
        },
        timeout=120,
    )

    response.raise_for_status()

    data = response.json()

    text = str(
        data.get(
            "response",
            "",
        )
        or ""
    ).strip()

    if not text:
        raise RuntimeError(
            "Ollama returned an empty visual plan."
        )

    result = json.loads(
        text
    )

    if not isinstance(
        result,
        dict,
    ):
        raise RuntimeError(
            "Visual planner response was not a JSON object."
        )

    return result


def normalize_slot(
    raw: Any,
    selection_start: float,
    selection_end: float,
) -> dict[str, Any] | None:

    if not isinstance(
        raw,
        dict,
    ):
        return None

    try:

        start = float(
            raw.get(
                "start",
                0.0,
            )
        )

        end = float(
            raw.get(
                "end",
                start,
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    start = max(
        selection_start,
        min(
            selection_end,
            start,
        ),
    )

    end = max(
        start,
        min(
            selection_end,
            end,
        ),
    )

    duration = (
        end
        - start
    )

    if duration < MIN_SLOT_SECONDS:

        center = (
            start
            + end
        ) / 2.0

        start = max(
            selection_start,
            center
            - MIN_SLOT_SECONDS
            / 2.0,
        )

        end = min(
            selection_end,
            start
            + MIN_SLOT_SECONDS,
        )

    if (
        end
        - start
        > MAX_SLOT_SECONDS
    ):

        end = (
            start
            + MAX_SLOT_SECONDS
        )

    if (
        end
        <= start
        or end
        - start
        < 0.8
    ):

        return None

    label = str(
        raw.get(
            "label",
            "AI Visual",
        )
        or "AI Visual"
    ).strip()

    reason = str(
        raw.get(
            "reason",
            "",
        )
        or ""
    ).strip()

    prompt = str(
        raw.get(
            "prompt",
            "",
        )
        or ""
    ).strip()

    visual_type = str(
        raw.get(
            "visual_type",
            "ai_recreation",
        )
        or "ai_recreation"
    ).strip()

    if not prompt:
        return None

    return {
        "start": round(
            start,
            3,
        ),
        "end": round(
            end,
            3,
        ),
        "duration": round(
            end
            - start,
            3,
        ),
        "label": label[:60],
        "reason": reason,
        "visual_type": visual_type,
        "prompt": prompt,
    }


def remove_overlaps(
    slots: list[
        dict[str, Any]
    ],
) -> list[
    dict[str, Any]
]:

    selected: list[
        dict[str, Any]
    ] = []

    for slot in sorted(
        slots,
        key=lambda item: (
            item["start"],
            item["end"],
        ),
    ):

        if any(
            (
                existing["end"]
                > slot["start"]
                and existing["start"]
                < slot["end"]
            )
            for existing in selected
        ):
            continue

        selected.append(
            slot
        )

        if len(
            selected
        ) >= MAX_SLOTS:
            break

    return selected


def main() -> int:

    args = parse_args()

    video_path = Path(
        args.video
    ).resolve()

    transcript_path = Path(
        args.transcript
    ).resolve()

    corrections_path = Path(
        args.corrections
    ).resolve()

    manual_cuts_path = Path(
        args.manual_cuts
    ).resolve()

    output_path = Path(
        args.output
    ).resolve()

    start = max(
        0.0,
        float(
            args.start
        ),
    )

    end = max(
        start,
        float(
            args.end
        ),
    )

    print(
        "ShortsFactory AI visual planner starting...",
        flush=True,
    )

    if not video_path.exists():

        print(
            f"ERROR: Source not found: {video_path}",
            flush=True,
        )

        return 1

    if not transcript_path.exists():

        print(
            "ERROR: Source transcript is not loaded. Run Find Best Clips first.",
            flush=True,
        )

        return 1

    corrections = correction_map(
        corrections_path
    )

    cuts = manual_cut_ranges(
        manual_cuts_path
    )

    segments = selected_segments(
        transcript_path,
        start,
        end,
        corrections,
        cuts,
    )

    if not segments:

        print(
            "No usable transcript segments remain in this selection.",
            flush=True,
        )

        plan = {
            "source_video": str(
                video_path
            ),
            "selection_start": start,
            "selection_end": end,
            "slot_count": 0,
            "slots": [],
        }

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path.write_text(
            json.dumps(
                plan,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        return 0

    transcript_text = compact_transcript(
        segments
    )

    print(
        f"Transcript lines considered: {len(segments)}",
        flush=True,
    )

    print(
        f"Using Ollama model: {OLLAMA_MODEL}",
        flush=True,
    )

    try:

        raw_plan = call_ollama(
            build_prompt(
                transcript_text,
                start,
                end,
            )
        )

    except Exception as exc:

        print(
            f"ERROR: AI visual planning failed: {exc}",
            flush=True,
        )

        return 1

    raw_slots = raw_plan.get(
        "slots",
        [],
    )

    if not isinstance(
        raw_slots,
        list,
    ):

        raw_slots = []

    slots = []

    for raw_slot in raw_slots:

        normalized = normalize_slot(
            raw_slot,
            start,
            end,
        )

        if normalized is not None:

            slots.append(
                normalized
            )

    slots = remove_overlaps(
        slots
    )

    plan = {
        "source_video": str(
            video_path
        ),
        "selection_start": round(
            start,
            3,
        ),
        "selection_end": round(
            end,
            3,
        ),
        "slot_count": len(
            slots
        ),
        "slots": slots,
    }

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Visual cutaway slots planned: {len(slots)}",
        flush=True,
    )

    for index, slot in enumerate(
        slots,
        start=1,
    ):

        print(
            (
                f"Visual {index}: "
                f"{slot['start']:.2f}s -> "
                f"{slot['end']:.2f}s  //  "
                f"{slot['label']}"
            ),
            flush=True,
        )

    print(
        f"Visual plan saved: {output_path}",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
