"""
LLM-based content-trimming planner (targets a 25-40s final duration by
proposing segment removals via a local Ollama call, then a second
verification pass). NOT currently wired into the active render pipeline
(render.py) or any GUI mixin -- semantic_edit.py is the live equivalent
(STEP 4). Kept as a standalone, independently-runnable script; verify it
is still unused before removing or before assuming it runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from .ollama_config import OLLAMA_HOST, OLLAMA_MODEL
except ImportError:
    from ollama_config import OLLAMA_HOST, OLLAMA_MODEL

try:
    from .pipeline_paths import SUBTITLES_PATH
except ImportError:
    from pipeline_paths import SUBTITLES_PATH


ROOT = Path(__file__).resolve().parent.parent

OUTPUT_PATH = ROOT / "output" / "content_edit_plan.json"


# ============================================================
# EDITING SETTINGS
# ============================================================

TARGET_MIN_SECONDS = 25.0
TARGET_MAX_SECONDS = 40.0

# Do not approve microscopic segment removals.
MIN_REMOVE_DURATION = 0.40

# Second-pass verifier must be this confident.
MIN_VERIFY_CONFIDENCE = 0.80


def load_json(path: Path) -> dict[str, Any]:

    if not path.exists():
        raise FileNotFoundError(
            f"File not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def call_ollama(
    prompt: str,
) -> dict[str, Any]:

    url = (
        f"{OLLAMA_HOST.rstrip('/')}"
        f"/api/generate"
    )

    response = requests.post(
        url,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
            },
        },
        timeout=180,
    )

    response.raise_for_status()

    data = response.json()

    text = str(
        data.get(
            "response",
            "",
        )
    ).strip()

    if not text:
        raise RuntimeError(
            "Ollama returned an empty response."
        )

    try:
        return json.loads(text)

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ollama returned invalid JSON:\n{text}"
        ) from exc


def build_segment_transcript(
    segments: list[dict],
) -> str:

    lines = []

    for segment in segments:

        index = int(
            segment.get(
                "segment_index",
                0,
            )
        )

        start = float(
            segment.get(
                "start",
                0,
            )
        )

        end = float(
            segment.get(
                "end",
                0,
            )
        )

        duration = end - start

        text = str(
            segment.get(
                "text",
                "",
            )
        ).strip()

        lines.append(
            f"SEGMENT {index}\n"
            f"[{start:.2f} - {end:.2f}] "
            f"({duration:.2f}s)\n"
            f"{text}\n"
        )

    return "\n".join(lines)


def total_duration(
    segments: list[dict],
) -> float:

    if not segments:
        return 0.0

    return max(
        float(
            segment.get(
                "end",
                0,
            )
        )
        for segment in segments
    )


def build_editor_prompt(
    segments: list[dict],
) -> str:

    transcript = build_segment_transcript(
        segments
    )

    duration = total_duration(
        segments
    )

    return f"""
You are the content editor for an automated YouTube Shorts system.

You are given a spoken-video transcript divided into COMPLETE
speech segments.

Unlike a word-level editor, you may ONLY recommend removing WHOLE
segments.

Your goal is to preserve the strongest, clearest, most entertaining
version of the conversation.

==================================================
CURRENT VIDEO
==================================================

Duration: {duration:.2f} seconds

Preferred final range:

{TARGET_MIN_SECONDS:.0f} to {TARGET_MAX_SECONDS:.0f} seconds

IMPORTANT:

This range is a preference, NOT a quota.

If the clip is already under {TARGET_MAX_SECONDS:.0f} seconds,
do NOT remove good content merely to make it shorter.

If the clip is longer, remove redundant or weak sections where
possible.

==================================================
WHAT TO REMOVE
==================================================

Strong removal candidates include:

- a complete thought that repeats information already said
- redundant setup before a stronger version of the same idea
- an abandoned conversational tangent
- a weak detour that is unnecessary to understand the payoff
- a repeated explanation
- a segment that contributes almost nothing unique
- a conversational aside that slows down the Short

==================================================
WHAT TO KEEP
==================================================

Keep:

- unique facts
- jokes
- punchlines
- strong reactions
- surprising details
- story progression
- context necessary for later statements
- important setup
- payoff
- interesting personality
- useful disagreement or contrast

Do NOT remove a segment just because speech is casual or imperfect.

Do NOT rewrite dialogue.

Do NOT invent new dialogue.

Do NOT reorder segments.

==================================================
CRITICAL COHERENCE RULE
==================================================

Imagine physically deleting the proposed segment from the video.

The segment immediately before it will jump directly into the
segment immediately after it.

Only propose a removal when that transition is likely to remain
understandable.

If uncertain, KEEP the segment.

==================================================
TRANSCRIPT
==================================================

{transcript}

==================================================
OUTPUT
==================================================

Return ONLY JSON:

{{
  "summary": "brief explanation of the editing opportunity",
  "proposed_removals": [
    {{
      "segment_index": 2,
      "reason": "repeats the idea from segment 1",
      "confidence": 0.94
    }}
  ]
}}

Confidence must be between 0 and 1.

If the conversation is already concise:

{{
  "summary": "The clip is already concise and coherent.",
  "proposed_removals": []
}}
""".strip()


def validate_proposals(
    result: dict[str, Any],
    segments: list[dict],
) -> list[dict]:

    segment_lookup = {
        int(
            segment.get(
                "segment_index",
                index,
            )
        ): segment
        for index, segment
        in enumerate(segments)
    }

    proposals = result.get(
        "proposed_removals",
        [],
    )

    if not isinstance(
        proposals,
        list,
    ):
        return []

    valid = []

    seen = set()

    for proposal in proposals:

        try:
            index = int(
                proposal[
                    "segment_index"
                ]
            )

            confidence = float(
                proposal.get(
                    "confidence",
                    0,
                )
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        if index in seen:
            continue

        if index not in segment_lookup:
            continue

        segment = segment_lookup[
            index
        ]

        start = float(
            segment.get(
                "start",
                0,
            )
        )

        end = float(
            segment.get(
                "end",
                0,
            )
        )

        duration = end - start

        if duration < MIN_REMOVE_DURATION:
            continue

        # Initial proposal must already be reasonably confident.
        if confidence < 0.75:
            continue

        seen.add(index)

        valid.append(
            {
                "segment_index":
                    index,

                "start":
                    round(
                        start,
                        3,
                    ),

                "end":
                    round(
                        end,
                        3,
                    ),

                "duration":
                    round(
                        duration,
                        3,
                    ),

                "text":
                    str(
                        segment.get(
                            "text",
                            "",
                        )
                    ).strip(),

                "reason":
                    str(
                        proposal.get(
                            "reason",
                            "",
                        )
                    ).strip(),

                "proposal_confidence":
                    round(
                        confidence,
                        3,
                    ),
            }
        )

    valid.sort(
        key=lambda item:
            item["segment_index"]
    )

    return valid


def segment_text(
    segments: list[dict],
    index: int,
) -> str:

    if index < 0:
        return "[START OF VIDEO]"

    if index >= len(segments):
        return "[END OF VIDEO]"

    return str(
        segments[index].get(
            "text",
            "",
        )
    ).strip()


def build_verification_prompt(
    proposal: dict,
    segments: list[dict],
) -> str:

    index = int(
        proposal["segment_index"]
    )

    before = segment_text(
        segments,
        index - 1,
    )

    removed = segment_text(
        segments,
        index,
    )

    after = segment_text(
        segments,
        index + 1,
    )

    return f"""
You are the final safety verifier for a YouTube Shorts jump cut.

A content editor wants to REMOVE one entire speech segment.

Your job is to determine whether physically deleting this segment
will improve the Short WITHOUT making the conversation confusing,
misleading, or obviously broken.

==================================================
SEGMENT BEFORE
==================================================

{before}

==================================================
REMOVE THIS SEGMENT
==================================================

{removed}

==================================================
SEGMENT AFTER
==================================================

{after}

==================================================
EDITOR'S REASON
==================================================

{proposal.get("reason", "")}

==================================================
DECISION RULES
==================================================

APPROVE only if:

- the removed segment adds little unique value
- the surrounding conversation still makes sense
- no important context is lost
- the edit improves pacing
- the before/after transition is understandable
- the viewer still understands the story or topic

REJECT if:

- it removes unique information
- it damages a joke or payoff
- later dialogue depends on it
- the jump becomes confusing
- the transition feels obviously incomplete
- you are uncertain whether it is safe

Be conservative.

Return ONLY JSON:

{{
  "approved": true,
  "confidence": 0.93,
  "reason": "brief explanation"
}}

or

{{
  "approved": false,
  "confidence": 0.93,
  "reason": "brief explanation"
}}
""".strip()


def verify_proposal(
    proposal: dict,
    segments: list[dict],
) -> dict:

    result = call_ollama(
        build_verification_prompt(
            proposal,
            segments,
        )
    )

    return {
        "approved": bool(
            result.get(
                "approved",
                False,
            )
        ),

        "confidence": round(
            float(
                result.get(
                    "confidence",
                    0,
                )
            ),
            3,
        ),

        "reason": str(
            result.get(
                "reason",
                "",
            )
        ).strip(),
    }


def main() -> int:

    print()
    print(
        "========================================"
    )
    print(
        "      ShortsFactory Content Editor"
    )
    print(
        "========================================"
    )
    print()

    data = load_json(
        SUBTITLES_PATH
    )

    segments = data.get(
        "segments",
        [],
    )

    if not isinstance(
        segments,
        list,
    ) or not segments:

        print(
            "ERROR: No speech segments found."
        )

        print()
        print(
            "Make sure subtitles.py is saving "
            "the new segments field."
        )

        return 1

    duration = total_duration(
        segments
    )

    print(
        f"Speech segments: "
        f"{len(segments)}"
    )

    # ========================================================
    # HARD SAFETY RULE
    #
    # If the clip is already within our desired Shorts range,
    # do NOT perform phrase-level content removal.
    #
    # Pause removal / tiny cleanup may still happen elsewhere,
    # but this editor exists primarily to compress LONG clips.
    # ========================================================

    if duration <= TARGET_MAX_SECONDS:

        print(
            f"Speech segments: "
            f"{len(segments)}"
        )

        print(
            f"Current duration: "
            f"{duration:.2f}s"
        )

        print()
        print(
            "Clip is already within the target "
            "Shorts duration."
        )

        print(
            "Skipping phrase-level content removal."
        )

        output = {
            "summary":
                "Clip is already within the target duration; "
                "no phrase-level removals were allowed.",

            "original_duration_seconds":
                round(duration, 3),

            "target_min_seconds":
                TARGET_MIN_SECONDS,

            "target_max_seconds":
                TARGET_MAX_SECONDS,

            "proposal_count":
                0,

            "approved_removal_count":
                0,

            "removed_seconds":
                0.0,

            "estimated_final_duration_seconds":
                round(duration, 3),

            "approved_removals":
                [],

            "all_verification_results":
                [],
        }

        OUTPUT_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with OUTPUT_PATH.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                output,
                f,
                indent=2,
                ensure_ascii=False,
            )

        print()
        print(
            f"Plan saved to:"
        )

        print(
            OUTPUT_PATH
        )

        return 0

    print(
        f"Current duration: "
        f"{duration:.2f}s"
    )

    print(
        f"Using Ollama model: "
        f"{OLLAMA_MODEL}"
    )

    print()
    print(
        "Looking for whole thoughts that "
        "can safely be removed..."
    )

    initial_result = call_ollama(
        build_editor_prompt(
            segments
        )
    )

    proposals = validate_proposals(
        initial_result,
        segments,
    )

    print()
    print(
        f"Initial removal proposals: "
        f"{len(proposals)}"
    )

    approved = []

    verification_results = []

    for number, proposal in enumerate(
        proposals,
        start=1,
    ):

        print()
        print(
            f"Verifying segment "
            f"{proposal['segment_index']} "
            f"({number}/{len(proposals)})..."
        )

        verification = verify_proposal(
            proposal,
            segments,
        )

        record = {
            **proposal,
            "verification":
                verification,
        }

        verification_results.append(
            record
        )

        print()
        print(
            f"SEGMENT "
            f"{proposal['segment_index']}"
        )

        print(
            f"Text: "
            f"\"{proposal['text']}\""
        )

        print(
            f"Duration: "
            f"{proposal['duration']:.2f}s"
        )

        print(
            f"Proposal reason: "
            f"{proposal['reason']}"
        )

        print(
            f"Verifier: "
            f"{'APPROVED' if verification['approved'] else 'REJECTED'}"
        )

        print(
            f"Verifier confidence: "
            f"{verification['confidence']}"
        )

        print(
            f"Verifier reason: "
            f"{verification['reason']}"
        )

        if (
            verification["approved"]
            and
            verification["confidence"]
            >= MIN_VERIFY_CONFIDENCE
        ):

            approved.append(
                {
                    **proposal,

                    "verification_confidence":
                        verification[
                            "confidence"
                        ],

                    "verification_reason":
                        verification[
                            "reason"
                        ],
                }
            )

    removed_seconds = sum(
        float(
            item["duration"]
        )
        for item in approved
    )

    estimated_final = (
        duration
        - removed_seconds
    )

    output = {
        "summary":
            initial_result.get(
                "summary",
                "",
            ),

        "original_duration_seconds":
            round(
                duration,
                3,
            ),

        "target_min_seconds":
            TARGET_MIN_SECONDS,

        "target_max_seconds":
            TARGET_MAX_SECONDS,

        "proposal_count":
            len(proposals),

        "approved_removal_count":
            len(approved),

        "removed_seconds":
            round(
                removed_seconds,
                3,
            ),

        "estimated_final_duration_seconds":
            round(
                estimated_final,
                3,
            ),

        "approved_removals":
            approved,

        "all_verification_results":
            verification_results,
    }

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print(
        "========================================"
    )
    print(
        "        CONTENT EDIT COMPLETE"
    )
    print(
        "========================================"
    )
    print()

    print(
        f"Original duration: "
        f"{duration:.2f}s"
    )

    print(
        f"Approved segment removals: "
        f"{len(approved)}"
    )

    print(
        f"Potential time removed: "
        f"{removed_seconds:.2f}s"
    )

    print(
        f"Estimated final duration: "
        f"{estimated_final:.2f}s"
    )

    print()
    print(
        f"Plan saved to:"
    )
    print(
        OUTPUT_PATH
    )

    print()
    print(
        "NOTE: No video has been changed yet."
    )

    return 0


if __name__ == "__main__":

    try:
        sys.exit(
            main()
        )

    except Exception as exc:

        print()
        print(
            f"Content editing failed: "
            f"{exc}"
        )

        sys.exit(1)