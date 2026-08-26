"""
STEP 4 of the render pipeline: proposes and verifies safe semantic cuts
(e.g. repeated ideas, filler) via two local Ollama calls -- an initial
proposal pass and a stricter second-pass verifier -- writing
output/semantic_edit_plan.json. apply_smart_edit.py (STEP 5) merges
approved cuts here with pause cuts and manual cuts into the final edit.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from .ollama_config import OLLAMA_HOST, OLLAMA_MODEL
    from .visual_emphasis import (
        energy_profile,
        load_render_settings,
        normalize_energy,
    )
    from .pipeline_paths import (
        SEMANTIC_EDIT_PLAN_PATH as OUTPUT_PATH,
        SUBTITLES_PATH,
    )
except ImportError:
    from ollama_config import OLLAMA_HOST, OLLAMA_MODEL
    from visual_emphasis import (
        energy_profile,
        load_render_settings,
        normalize_energy,
    )
    from pipeline_paths import (
        SEMANTIC_EDIT_PLAN_PATH as OUTPUT_PATH,
        SUBTITLES_PATH,
    )


ROOT = Path(__file__).resolve().parent.parent
SEMANTIC_AI_TIMEOUT_ENV = "SHORTSFACTORY_SEMANTIC_AI_TIMEOUT_SECONDS"
DEFAULT_SEMANTIC_AI_TIMEOUT_SECONDS = 30.0
SEMANTIC_AI_PREFLIGHT_TIMEOUT_SECONDS = 1.0


def semantic_ai_timeout_seconds() -> float:
    try:
        timeout = float(
            os.getenv(
                SEMANTIC_AI_TIMEOUT_ENV,
                str(DEFAULT_SEMANTIC_AI_TIMEOUT_SECONDS),
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return DEFAULT_SEMANTIC_AI_TIMEOUT_SECONDS

    return max(
        1.0,
        timeout,
    )


def semantic_ai_preflight_warning() -> str | None:
    """Return quickly when the optional local AI service is unavailable."""
    url = f"{OLLAMA_HOST.rstrip('/')}/api/tags"

    try:
        response = requests.get(
            url,
            timeout=SEMANTIC_AI_PREFLIGHT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:
        return str(exc)

    return None


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
        timeout=semantic_ai_timeout_seconds(),
    )

    response.raise_for_status()

    data = response.json()

    text = data.get(
        "response",
        "",
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


def write_plan(
    *,
    summary: str,
    proposed_cuts: list[dict[str, Any]],
    approved_cuts: list[dict[str, Any]],
    verification_results: list[dict[str, Any]],
    warning: str = "",
) -> None:

    removed_seconds = sum(
        float(
            cut.get(
                "duration",
                0.0,
            )
        )
        for cut in approved_cuts
    )

    output = {
        "summary": summary,
        "initial_proposal_count": len(
            proposed_cuts
        ),
        "approved_cut_count": len(
            approved_cuts
        ),
        "removed_seconds": round(
            removed_seconds,
            3,
        ),
        "approved_cuts": approved_cuts,
        "all_verification_results": verification_results,
    }

    if warning:
        output["warning"] = warning

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


def build_timed_transcript(
    words: list[dict],
) -> str:

    lines = []

    for index, word in enumerate(words):

        text = str(
            word.get("word", "")
        ).strip()

        start = float(
            word.get("start", 0)
        )

        end = float(
            word.get("end", 0)
        )

        if not text:
            continue

        lines.append(
            f"{index}: "
            f"[{start:.2f}-{end:.2f}] "
            f"{text}"
        )

    return "\n".join(lines)


def build_prompt(
    words: list[dict],
    energy: str = "PUNCHY",
) -> str:

    transcript = build_timed_transcript(
        words
    )

    return f"""
You are the semantic jump-cut editor for a YouTube Shorts
production system.

You are analyzing a short spoken-video transcript.

Your job is NOT to rewrite the dialogue.

Your job is to identify portions of the EXISTING speech that could
be removed to make the clip tighter while preserving the meaning,
story, humor, context, and natural speech.

The video may contain podcast conversation, interruptions,
cross-talk, incomplete thoughts, or casual speech.

==================================================
IMPORTANT
==================================================

BE CONSERVATIVE.

Only suggest a cut when the viewer loses little or no meaningful
information by removing it.

Good removal candidates:

- a speaker immediately repeating the same idea
- restarting a sentence and then saying it again
- obvious verbal filler
- abandoned sentence fragments that add no meaning
- redundant setup
- unnecessary repeated wording
- conversational clutter that can be removed cleanly

Do NOT remove:

- unique facts
- jokes or punchlines
- reactions that matter
- important context
- changes of opinion
- details needed to understand later speech
- words merely because the grammar is informal
- cross-talk merely because two people are speaking
- anything you are uncertain about

A Short should feel FAST but still natural.

The selected edit style is {energy}. Even in PUNCHY or MAXIMUM mode,
do not cut simply because a word or pause could technically be removed.
Prefer complete, clearly redundant phrases over tiny micro-cuts.
For LOW and PUNCHY, avoid isolated one-word removals.
Preserve reactions, comedic timing, and natural conversational rhythm.

Do not try to remove a certain amount of time.

If nothing should be removed, return zero cuts.

==================================================
TIMESTAMP RULES
==================================================

Every word below has:

WORD_INDEX
[start-end]
word

All cuts MUST begin at the start timestamp of an existing word.

All cuts MUST end at the end timestamp of an existing word.

Never cut through the middle of a word.

Prefer cutting complete phrases.

Avoid creating sentence joins that would sound obviously broken.

==================================================
TRANSCRIPT
==================================================

{transcript}

==================================================
OUTPUT
==================================================

Return ONLY JSON using this structure:

{{
  "summary": "brief description of the editing opportunity",
  "cuts": [
    {{
      "start_word_index": 0,
      "end_word_index": 3,
      "start": 0.0,
      "end": 1.5,
      "reason": "repeated idea",
      "removed_text": "exact words being removed",
      "confidence": 0.95
    }}
  ]
}}

Confidence must be between 0 and 1.

Only suggest genuinely useful cuts.

If there are no safe semantic cuts:

{{
  "summary": "The clip is already tightly spoken.",
  "cuts": []
}}
""".strip()


def validate_cuts(
    result: dict[str, Any],
    words: list[dict],
    profile: dict[str, Any] | None = None,
) -> list[dict]:

    if profile is None:
        profile = energy_profile(
            "PUNCHY"
        )

    min_duration = float(
        profile.get(
            "semantic_min_duration",
            0.45,
        )
    )
    min_words = int(
        profile.get(
            "semantic_min_words",
            2,
        )
    )
    min_confidence = float(
        profile.get(
            "semantic_min_confidence",
            0.88,
        )
    )

    valid = []

    cuts = result.get(
        "cuts",
        [],
    )

    if not isinstance(cuts, list):
        return valid

    for cut in cuts:

        try:

            start_index = int(
                cut["start_word_index"]
            )

            end_index = int(
                cut["end_word_index"]
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            continue

        if start_index < 0:
            continue

        if end_index >= len(words):
            continue

        if end_index < start_index:
            continue

        start = float(
            words[start_index]["start"]
        )

        end = float(
            words[end_index]["end"]
        )

        duration = end - start
        word_count = (
            end_index
            - start_index
            + 1
        )

        if duration < min_duration:
            continue

        if word_count < min_words:
            continue

        confidence = float(
            cut.get(
                "confidence",
                0,
            )
        )

        if confidence < min_confidence:
            continue

        removed_words = words[
            start_index:end_index + 1
        ]

        removed_text = " ".join(
            str(
                word.get(
                    "word",
                    "",
                )
            ).strip()
            for word in removed_words
        )

        valid.append(
            {
                "start_word_index":
                    start_index,

                "end_word_index":
                    end_index,

                "start":
                    round(start, 3),

                "end":
                    round(end, 3),

                "duration":
                    round(duration, 3),

                "reason":
                    str(
                        cut.get(
                            "reason",
                            "semantic cleanup",
                        )
                    ),

                "removed_text":
                    removed_text,

                "confidence":
                    round(
                        confidence,
                        3,
                    ),
            }
        )

    valid.sort(
        key=lambda item: item["start"]
    )

    non_overlapping = []

    last_end = -1.0

    for cut in valid:

        if cut["start"] < last_end:
            continue

        non_overlapping.append(
            cut
        )

        last_end = cut["end"]

    return non_overlapping


def words_to_text(
    words: list[dict],
    start_index: int,
    end_index: int,
) -> str:

    start_index = max(
        0,
        start_index,
    )

    end_index = min(
        len(words) - 1,
        end_index,
    )

    return " ".join(
        str(
            words[index].get(
                "word",
                "",
            )
        ).strip()
        for index in range(
            start_index,
            end_index + 1,
        )
    )


def build_verification_prompt(
    cut: dict,
    words: list[dict],
    energy: str = "PUNCHY",
) -> str:

    start_index = int(
        cut["start_word_index"]
    )

    end_index = int(
        cut["end_word_index"]
    )

    context_words = 12

    before = words_to_text(
        words,
        start_index - context_words,
        start_index - 1,
    )

    removed = words_to_text(
        words,
        start_index,
        end_index,
    )

    after = words_to_text(
        words,
        end_index + 1,
        end_index + context_words,
    )

    resulting_join = (
        f"{before} {after}"
    ).strip()

    return f"""
You are verifying one proposed jump cut in spoken video.

The goal is to make a YouTube Short tighter WITHOUT making
the speaker sound broken, confusing, unnatural, or misleading.

The selected edit style is {energy}. Natural speech rhythm wins over
a marginal pacing improvement. Reject tiny or questionable edits.

A previous editing model proposed removing the words below.

==================================================
BEFORE THE CUT
==================================================

{before}

==================================================
PROPOSED REMOVAL
==================================================

{removed}

==================================================
AFTER THE CUT
==================================================

{after}

==================================================
RESULT IF REMOVED
==================================================

{resulting_join}

==================================================
ORIGINAL REASON
==================================================

{cut.get("reason", "")}

==================================================
YOUR TASK
==================================================

Decide whether this exact removal is SAFE.

Approve it ONLY if:

1. The resulting spoken sentence still sounds natural.
2. Grammar remains understandable.
3. No important information is lost.
4. No joke, reaction, detail, or context is lost.
5. The words before and after the cut connect naturally.
6. The cut genuinely improves pacing.
7. The cut is better than simply leaving the speech alone.

Reject it if:

- the join sounds awkward
- the sentence becomes broken
- the meaning changes
- it removes useful personality or context
- you are unsure
- the improvement is tiny

Be conservative.

Return ONLY JSON:

{{
  "approved": true,
  "confidence": 0.95,
  "reason": "brief explanation"
}}

or

{{
  "approved": false,
  "confidence": 0.95,
  "reason": "brief explanation"
}}
""".strip()


def verify_cut(
    cut: dict,
    words: list[dict],
    energy: str = "PUNCHY",
) -> dict:

    prompt = build_verification_prompt(
        cut,
        words,
        energy,
    )

    result = call_ollama(
        prompt
    )

    approved = bool(
        result.get(
            "approved",
            False,
        )
    )

    confidence = float(
        result.get(
            "confidence",
            0,
        )
    )

    reason = str(
        result.get(
            "reason",
            "",
        )
    ).strip()

    return {
        "approved": approved,
        "confidence": round(
            confidence,
            3,
        ),
        "reason": reason,
    }


def main() -> int:

    print()
    print("========================================")
    print("      ShortsFactory Semantic Edit")
    print("========================================")
    print()

    data = load_json(
        SUBTITLES_PATH
    )

    words = data.get(
        "words",
        [],
    )

    if not words:

        print(
            "ERROR: No word timestamps "
            "found."
        )

        return 1

    print(
        f"Words: {len(words)}"
    )

    settings = load_render_settings()
    energy = normalize_energy(
        settings.get(
            "edit_energy",
            "PUNCHY",
        )
    )
    profile = energy_profile(
        energy
    )
    verification_threshold = float(
        profile.get(
            "semantic_verify_confidence",
            0.90,
        )
    )

    print(
        f"Using Ollama model: "
        f"{OLLAMA_MODEL}"
    )
    print(
        f"Edit style: {energy}"
    )
    print(
        "Semantic cut thresholds: "
        f">={float(profile.get('semantic_min_duration', 0.45)):.2f}s, "
        f">={int(profile.get('semantic_min_words', 2))} words, "
        f"proposal confidence >= {float(profile.get('semantic_min_confidence', 0.88)):.2f}, "
        f"verification >= {verification_threshold:.2f}"
    )

    print()
    print(
        "Analyzing speech for safe "
        "semantic cuts..."
    )

    preflight_warning = semantic_ai_preflight_warning()
    if preflight_warning:
        print()
        print(
            "WARNING: Semantic AI is unavailable for this render."
        )
        print(
            preflight_warning
        )
        print(
            "Continuing with pause and manual edits only."
        )
        write_plan(
            summary="Semantic editing skipped for this render.",
            proposed_cuts=[],
            approved_cuts=[],
            verification_results=[],
            warning=preflight_warning,
        )
        return 0

    prompt = build_prompt(
        words,
        energy,
    )

    try:
        result = call_ollama(
            prompt
        )
    except Exception as exc:
        warning = str(
            exc
        )
        print()
        print(
            "WARNING: Semantic AI is unavailable for this render."
        )
        print(
            warning
        )
        print(
            "Continuing with pause and manual edits only."
        )
        write_plan(
            summary="Semantic editing skipped for this render.",
            proposed_cuts=[],
            approved_cuts=[],
            verification_results=[],
            warning=warning,
        )
        return 0

    proposed_cuts = validate_cuts(
        result,
        words,
        profile,
    )

    print()
    print(
        f"Initial proposals: "
        f"{len(proposed_cuts)}"
    )

    approved_cuts = []

    verification_results = []

    for index, cut in enumerate(
        proposed_cuts,
        start=1,
    ):

        print()
        print(
            f"Verifying cut "
            f"{index}/{len(proposed_cuts)}..."
        )

        try:
            verification = verify_cut(
                cut,
                words,
                energy,
            )
        except Exception as exc:
            verification = {
                "approved": False,
                "confidence": 0.0,
                "reason": (
                    "Verification unavailable: "
                    f"{exc}"
                ),
            }

        verification_results.append(
            {
                **cut,
                "verification":
                    verification,
            }
        )

        print()
        print(
            f"PROPOSED CUT {index}"
        )

        print(
            f"Remove: "
            f"\"{cut['removed_text']}\""
        )

        print(
            f"Original reason: "
            f"{cut['reason']}"
        )

        print(
            f"Verifier decision: "
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
            verification["confidence"] >= verification_threshold
        ):

            approved_cuts.append(
                {
                    **cut,
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
        cut["duration"]
        for cut in approved_cuts
    )

    write_plan(
        summary=str(
            result.get(
                "summary",
                "",
            )
            or ""
        ),
        proposed_cuts=proposed_cuts,
        approved_cuts=approved_cuts,
        verification_results=verification_results,
    )

    print()
    print("========================================")
    print("       VERIFICATION COMPLETE")
    print("========================================")
    print()

    print(
        f"Initial proposals: "
        f"{len(proposed_cuts)}"
    )

    print(
        f"Approved cuts: "
        f"{len(approved_cuts)}"
    )

    print(
        f"Potential time removed: "
        f"{removed_seconds:.2f}s"
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
        "NOTE: No video was changed."
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
            f"Semantic editing failed: "
            f"{exc}"
        )

        sys.exit(1)
