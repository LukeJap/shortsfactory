from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .pipeline_paths import (
        COMBINED_EDIT_PLAN_PATH as COMBINED_PLAN_PATH,
        SUBTITLES_PATH,
        TRANSCRIPT_CORRECTIONS_PATH as CORRECTIONS_PATH,
    )
except ImportError:
    from pipeline_paths import (
        COMBINED_EDIT_PLAN_PATH as COMBINED_PLAN_PATH,
        SUBTITLES_PATH,
        TRANSCRIPT_CORRECTIONS_PATH as CORRECTIONS_PATH,
    )


ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict[str, Any]:

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


def corrected_tokens(text: str) -> list[str]:

    return [
        token
        for token in re.findall(
            r"\S+",
            text.strip(),
        )
        if token
    ]


def keep_segments(
    plan: dict[str, Any],
    duration: float,
) -> list[tuple[float, float]]:

    raw = plan.get(
        "keep_segments",
        []
    )

    segments: list[
        tuple[float, float]
    ] = []

    if isinstance(raw, list):

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
                segments.append(
                    (
                        start,
                        end,
                    )
                )

    if segments:
        return segments

    return [
        (
            0.0,
            duration,
        )
    ]


def map_base_interval_to_tight(
    base_start: float,
    base_end: float,
    keeps: list[tuple[float, float]],
) -> tuple[float, float] | None:

    if base_end <= base_start:
        return None

    accumulated = 0.0
    mapped_pieces: list[
        tuple[float, float]
    ] = []

    for keep_start, keep_end in keeps:

        keep_duration = (
            keep_end
            - keep_start
        )

        overlap_start = max(
            base_start,
            keep_start,
        )

        overlap_end = min(
            base_end,
            keep_end,
        )

        if overlap_end > overlap_start:

            tight_start = (
                accumulated
                + overlap_start
                - keep_start
            )

            tight_end = (
                accumulated
                + overlap_end
                - keep_start
            )

            mapped_pieces.append(
                (
                    tight_start,
                    tight_end,
                )
            )

        accumulated += keep_duration

    if not mapped_pieces:
        return None

    return (
        mapped_pieces[0][0],
        mapped_pieces[-1][1],
    )


def rebuild_segments(
    words: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    if not words:
        return []

    segments: list[
        dict[str, Any]
    ] = []

    current: list[
        dict[str, Any]
    ] = []

    for word in words:

        if current:

            gap = (
                float(
                    word.get(
                        "start",
                        0.0,
                    )
                )
                - float(
                    current[-1].get(
                        "end",
                        0.0,
                    )
                )
            )

            last_text = str(
                current[-1].get(
                    "word",
                    "",
                )
            )

            if (
                gap > 0.70
                or last_text.endswith(
                    (
                        ".",
                        "?",
                        "!",
                    )
                )
                or len(current) >= 14
            ):

                segments.append(
                    {
                        "start": float(
                            current[0]["start"]
                        ),
                        "end": float(
                            current[-1]["end"]
                        ),
                        "text": " ".join(
                            str(
                                item["word"]
                            )
                            for item in current
                        ),
                        "words": [
                            dict(
                                item
                            )
                            for item in current
                        ],
                    }
                )

                current = []

        current.append(
            word
        )

    if current:

        segments.append(
            {
                "start": float(
                    current[0]["start"]
                ),
                "end": float(
                    current[-1]["end"]
                ),
                "text": " ".join(
                    str(
                        item["word"]
                    )
                    for item in current
                ),
                "words": [
                    dict(
                        item
                    )
                    for item in current
                ],
            }
        )

    return segments


def replace_interval_words(
    words: list[dict[str, Any]],
    interval_start: float,
    interval_end: float,
    replacement_text: str,
) -> bool:

    tokens = corrected_tokens(
        replacement_text
    )

    if not tokens:
        return False

    matching_indices = [
        index
        for index, word in enumerate(
            words
        )
        if (
            float(
                word.get(
                    "end",
                    0.0,
                )
            )
            > interval_start
            - 0.08
            and float(
                word.get(
                    "start",
                    0.0,
                )
            )
            < interval_end
            + 0.08
        )
    ]

    if not matching_indices:
        return False

    first_index = matching_indices[0]
    last_index = matching_indices[-1]

    actual_start = max(
        interval_start,
        float(
            words[first_index].get(
                "start",
                interval_start,
            )
        ),
    )

    actual_end = min(
        interval_end,
        float(
            words[last_index].get(
                "end",
                interval_end,
            )
        ),
    )

    if actual_end <= actual_start:
        actual_start = float(
            words[first_index].get(
                "start",
                interval_start,
            )
        )
        actual_end = float(
            words[last_index].get(
                "end",
                interval_end,
            )
        )

    duration = max(
        0.06,
        actual_end
        - actual_start,
    )

    slot = duration / len(
        tokens
    )

    new_words = []

    for index, token in enumerate(
        tokens
    ):

        start = (
            actual_start
            + slot * index
        )

        end = (
            actual_start
            + slot * (
                index + 1
            )
        )

        new_words.append(
            {
                "word": token,
                "start": round(
                    start,
                    4,
                ),
                "end": round(
                    end,
                    4,
                ),
                "probability": 1.0,
                "corrected": True,
            }
        )

    words[
        first_index:last_index + 1
    ] = new_words

    return True


def main() -> int:

    print(
        "ShortsFactory transcript correction pass starting...",
        flush=True,
    )

    corrections = load_json(
        CORRECTIONS_PATH
    )

    raw_corrections = corrections.get(
        "corrections",
        [],
    )

    if not isinstance(
        raw_corrections,
        list,
    ) or not raw_corrections:

        print(
            "No user transcript corrections to apply.",
            flush=True,
        )
        return 0

    subtitles = load_json(
        SUBTITLES_PATH
    )

    words = subtitles.get(
        "words",
        [],
    )

    if not isinstance(
        words,
        list,
    ) or not words:

        print(
            "WARNING: No word timestamps found for correction pass.",
            flush=True,
        )
        return 0

    selection_start = float(
        corrections.get(
            "selection_start",
            0.0,
        )
        or 0.0
    )

    combined_plan = load_json(
        COMBINED_PLAN_PATH
    )

    try:
        original_duration = float(
            combined_plan.get(
                "original_duration_seconds",
                words[-1].get(
                    "end",
                    0.0,
                ),
            )
            or 0.0
        )
    except (
        TypeError,
        ValueError,
    ):
        original_duration = float(
            words[-1].get(
                "end",
                0.0,
            )
            or 0.0
        )

    keeps = keep_segments(
        combined_plan,
        original_duration,
    )

    applied = 0
    skipped = 0

    for correction in raw_corrections:

        if not isinstance(
            correction,
            dict,
        ):
            continue

        corrected_text = str(
            correction.get(
                "corrected_text",
                "",
            )
            or ""
        ).strip()

        if not corrected_text:
            continue

        try:
            source_start = float(
                correction.get(
                    "start",
                    0.0,
                )
            )
            source_end = float(
                correction.get(
                    "end",
                    source_start,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            skipped += 1
            continue

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

        mapped = map_base_interval_to_tight(
            base_start,
            base_end,
            keeps,
        )

        if mapped is None:

            # The user also cut this line out entirely.
            skipped += 1
            continue

        tight_start, tight_end = mapped

        if replace_interval_words(
            words,
            tight_start,
            tight_end,
            corrected_text,
        ):

            applied += 1

            print(
                (
                    f'Applied correction {applied}: '
                    f'"{correction.get("original_text", "")}" '
                    f'-> "{corrected_text}"'
                ),
                flush=True,
            )

        else:
            skipped += 1

    words.sort(
        key=lambda item: float(
            item.get(
                "start",
                0.0,
            )
        )
    )

    subtitles["words"] = words
    subtitles["word_count"] = len(
        words
    )
    subtitles["segments"] = rebuild_segments(
        words
    )
    subtitles["segment_count"] = len(
        subtitles["segments"]
    )
    subtitles["text"] = " ".join(
        str(
            word.get(
                "word",
                "",
            )
        )
        for word in words
    ).strip()
    subtitles[
        "user_corrections_applied"
    ] = applied

    SUBTITLES_PATH.write_text(
        json.dumps(
            subtitles,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Transcript corrections applied: {applied}",
        flush=True,
    )

    if skipped:
        print(
            f"Corrections skipped: {skipped}",
            flush=True,
        )

    print(
        "Corrected transcript saved before caption generation.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
