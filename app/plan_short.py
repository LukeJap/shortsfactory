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
    from .pipeline_paths import SHORT_PLAN_PATH as OUTPUT_PATH
except ImportError:
    from pipeline_paths import SHORT_PLAN_PATH as OUTPUT_PATH


ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_PATH = ROOT / "output" / "analysis.json"
TRANSCRIPT_PATH = ROOT / "short1.json"


def log(message: str) -> None:
    print(message)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def call_ollama(prompt: str) -> dict[str, Any]:
    url = f"{OLLAMA_HOST.rstrip('/')}/api/generate"

    response = requests.post(
        url,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.4,
            },
        },
        timeout=180,
    )

    response.raise_for_status()

    data = response.json()
    text = data.get("response", "").strip()

    if not text:
        raise RuntimeError("Ollama returned an empty response.")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ollama returned invalid JSON:\n{text}"
        ) from exc


def build_prompt(
    analysis: dict[str, Any],
    transcript: dict[str, Any],
) -> str:
    selected = analysis.get("selected_clip", {})

    start = selected.get("start_timestamp", "")
    end = selected.get("end_timestamp", "")
    duration = selected.get("duration_seconds", 0)

    transcript_segments = transcript.get("segments", [])

    relevant_segments = []

    def timestamp_to_seconds(value: Any) -> float:
        if value is None:
            return 0.0

        text = str(value).strip()

        try:
            parts = text.split(":")

            if len(parts) == 3:
                return (
                    int(parts[0]) * 3600
                    + int(parts[1]) * 60
                    + float(parts[2])
                )

            if len(parts) == 2:
                return (
                    int(parts[0]) * 60
                    + float(parts[1])
                )

            return float(text)

        except (ValueError, TypeError):
            return 0.0

    try:
        start_seconds = timestamp_to_seconds(start)
        end_seconds = timestamp_to_seconds(end)

        for segment in transcript_segments:
            segment_start = float(segment.get("start", 0))
            segment_end = float(segment.get("end", 0))

            if segment_end >= start_seconds and segment_start <= end_seconds:
                relevant_segments.append(segment)

    except (ValueError, TypeError):
        relevant_segments = transcript_segments

    clip_transcript = "\n".join(
        f"[{segment.get('start', 0):.2f}-{segment.get('end', 0):.2f}] "
        f"{segment.get('text', '').strip()}"
        for segment in relevant_segments
    )

    # Only pass trustworthy analysis fields to the planner.
    #
    # Do NOT pass previous LLM-generated hooks, narration, concepts,
    # copyright assessments, or other creative interpretations.
    # Those fields can contaminate the next generation.
    trusted_analysis = {
        "main_topic": analysis.get("main_topic", ""),
        "people_subjects": analysis.get("people_subjects", []),
        "selected_clip": {
            "start_timestamp": start,
            "end_timestamp": end,
            "duration_seconds": duration,
        },
        "viral_potential_score": analysis.get("viral_potential_score", 0),
    }

    schema = {
        "title": "short title",
        "concept": "one sentence describing the actual idea of the Short",
        "hook": "short natural spoken hook",
        "hook_style": "curiosity, surprise, humor, story, observation, etc.",
        "narration": {
            "script": "original commentary/narration script",
            "estimated_seconds": "number",
        },
        "structure": [
            {
                "order": 1,
                "purpose": "hook/setup/source_clip/narration/payoff/etc.",
                "start_seconds": "number",
                "end_seconds": "number",
                "source_audio": "use_source_audio or narration_only or mixed",
                "visual_type": "source_clip, ai_video, ai_image, text, or mixed",
                "visual_prompt": "specific visual generation prompt or empty string",
                "on_screen_text": "short text or empty string",
            }
        ],
        "ai_visuals": [
            {
                "order": 1,
                "type": "video or image",
                "duration_seconds": "number",
                "prompt": "specific generation prompt",
                "purpose": "why this visual is needed",
            }
        ],
        "subtitle_style": {
            "style": "description",
            "words_per_line": "number",
            "emphasis_words": ["string"]
        },
        "ending": {
            "payoff": "specific ending payoff",
            "loop_back": "how the ending can encourage replay or continuation",
        },
        "editing_notes": [
            "specific editing instruction"
        ],
        "source_clip": {
            "start_timestamp": start,
            "end_timestamp": end,
            "duration_seconds": duration,
        },
    }

    return f"""
You are the creative director for an automated YouTube Shorts production system.

You are planning a SHORT based on a real source-video excerpt.

Your most important rule:

THE TRANSCRIPT IS THE SOURCE OF TRUTH.

The selected clip transcript below contains what was actually said.
Your job is to turn that material into an engaging, original,
commentary-driven Short.

==================================================
STRICT FACTUALITY RULES
==================================================

You MUST NOT invent facts.

Do not invent:
- events
- relationships
- scandals
- crimes
- historical claims
- dates
- prices
- sales
- ownership
- motivations
- outcomes
- locations
- quotes
- people
- actions that aren't in the transcript

If the transcript does not establish something, do not state it as fact.

You may make observations and commentary, but clearly keep them as
commentary rather than fabricated facts.

For example:

BAD:
"The Playboy Mansion had a dark and sinister history."

This is an unsupported factual characterization.

BAD:
"The mansion was recently sold."

The transcript does not establish this.

BAD:
"Hugh Hefner was involved in shocking events."

Do not infer this from the existence of the Playboy Mansion.

GOOD:
"The weird thing about the Playboy Mansion wasn't just who lived there.
It was how surprisingly dated the place looked."

This is commentary directly connected to the selected conversation.

==================================================
IMPORTANT: IGNORE PREVIOUS AI CREATIVE WRITING
==================================================

Any previous AI-generated hooks, narration, concepts, or interpretations
are NOT authoritative.

Do not copy or continue previous AI language.

Use ONLY:
1. The trusted source analysis below for basic topic identification.
2. The selected clip transcript for what actually happened.
3. Your own original commentary based directly on those materials.

==================================================
THE ACTUAL CLIP
==================================================

The selected source footage runs from:

{start} -> {end}

Duration:

{duration} seconds

The transcript of that exact section is:

{clip_transcript}

==================================================
TRUSTED SOURCE INFORMATION
==================================================

{json.dumps(trusted_analysis, indent=2)}

==================================================
CREATE THE SHORT
==================================================

The Short should feel like a creator discovered one genuinely interesting
idea inside this conversation and built a concise story around it.

Do NOT try to make every Short dark, shocking, controversial, or dramatic.

Instead identify the actual strongest angle.

Possible angles include:
- surprising observation
- weird detail
- funny moment
- unexpected contrast
- unusual historical detail IF supported
- curiosity
- story
- cultural observation
- absurdity
- nostalgia

For this particular clip, pay special attention to concrete details
actually present in the transcript.

==================================================
HOOK
==================================================

Write a short spoken hook.

The hook must:
- sound natural when spoken aloud
- immediately identify the subject
- create curiosity
- be understandable without prior context
- connect directly to the selected clip

Do NOT use generic clickbait.

Never write:
"You won't believe..."
"This is crazy..."
"Here's what happened..."
"The dark truth..."
"What's turns this..."
"Find out..."
"Shocking events happened here..."

Do not copy malformed transcript language into the hook.

==================================================
ORIGINAL NARRATION
==================================================

The narration must be original commentary.

It should add something to the source footage rather than simply repeat it.

The narration can:
- explain why the observation is interesting
- provide framing
- point out an unusual contrast
- connect ideas
- create a setup/payoff
- add humor
- guide the viewer through the clip

But it cannot invent factual information.

==================================================
SOURCE FOOTAGE
==================================================

Use the source clip strategically.

The source footage does NOT need to play continuously.

A good structure may look like:

HOOK
↓
AI VISUAL
↓
SOURCE CLIP
↓
ORIGINAL COMMENTARY
↓
AI VISUAL
↓
SOURCE CLIP PAYOFF

However, choose whatever structure best fits the actual conversation.

==================================================
AI VISUALS
==================================================

AI visuals will later be generated automatically.

Each visual must illustrate a specific idea being discussed.

Do not use vague prompts such as:

"a cinematic mansion"

Instead use prompts that describe the actual visual idea.

Example:

"Vertical 9:16 cinematic recreation of an old-fashioned mansion interior,
featuring dated wood paneling, an antique telephone, worn furniture,
warm tungsten lighting, documentary-style realism."

AI visuals should be clearly labeled as recreations/interpretations
when appropriate.

==================================================
COPYRIGHT
==================================================

Do NOT claim that AI filters, captions, zooms, crops, or effects
automatically eliminate copyright.

Instead, design the Short to be meaningfully transformed through:

- original narration
- selective source footage
- AI-generated visuals
- commentary
- editing
- pacing
- captions
- reframing
- sound design

The goal is to create a genuinely new commentary-driven work.

==================================================
TIMING
==================================================

Target approximately 25-40 seconds total.

Keep narration concise enough to fit naturally.

==================================================
STRUCTURE
==================================================

IMPORTANT TIMING RULE:

The "structure" timestamps describe the FINAL SHORT timeline.

They do NOT describe timestamps inside the source video.

The final Short should normally be approximately 25-40 seconds.

The structure must begin at 0.

The final structure end time should equal the intended Short duration.

Do not create a 50+ second Short unless explicitly requested.

The selected source clip is 28.28 seconds long, but you do NOT need
to use all 28.28 seconds.

Use only the portions needed to create the best Short.

A typical structure might be:

0-3 seconds: original hook
3-10 seconds: source footage
10-18 seconds: original narration + AI visual
18-25 seconds: source footage / payoff

Do not let source_clip sections accidentally imply that their timestamps
are timestamps into the original source video.

Use "source_clip" to mean "play an excerpt from the selected source clip."

==================================================
QUALITY CONTROL
==================================================

Before returning the JSON, silently verify:

1. Is the hook natural?
2. Is the hook based on the actual clip?
3. Did I invent any facts?
4. Did I accidentally claim something was sold?
5. Did I invent a scandal or dark history?
6. Does the narration add original commentary?
7. Do the AI visuals correspond to actual ideas?
8. Is there a clear setup and payoff?
9. Does the Short make sense to someone who has never seen the source?
10. Would an automated video renderer know what to do with this plan?

If uncertain, choose the simpler and more factual interpretation.

Create a production-ready plan using EXACTLY this JSON schema:

{json.dumps(schema, indent=2)}

Return ONLY the JSON object.
""".strip()

def validate_plan(plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    required = [
        "title",
        "concept",
        "hook",
        "narration",
        "structure",
        "ai_visuals",
        "subtitle_style",
        "ending",
        "editing_notes",
        "source_clip",
    ]

    for key in required:
        if key not in plan:
            issues.append(f"Missing required field: {key}")

    narration = plan.get("narration", {})

    if not isinstance(narration, dict):
        issues.append("narration must be an object.")
    elif not str(narration.get("script", "")).strip():
        issues.append("Narration script is empty.")

    if not isinstance(plan.get("structure"), list):
        issues.append("structure must be a list.")

    return issues


def main() -> int:
    log("ShortsFactory Short Planner starting...")
    log(f"Project folder: {ROOT}")

    try:
        analysis_file = load_json(ANALYSIS_PATH)
        transcript = load_json(TRANSCRIPT_PATH)

        analysis = analysis_file.get("analysis", analysis_file)

        selected = analysis.get("selected_clip", {})

        if not selected.get("start_timestamp"):
            raise RuntimeError(
                "analysis.json does not contain a valid selected clip."
            )

        log(
            "Selected clip: "
            f"{selected.get('start_timestamp')} -> "
            f"{selected.get('end_timestamp')}"
        )

        log(f"Using Ollama model: {OLLAMA_MODEL}")
        log("Building Short production plan...")

        prompt = build_prompt(
            analysis,
            transcript,
        )

        plan = call_ollama(prompt)

        issues = validate_plan(plan)

        if issues:
            log("Planner returned validation issues:")
            for issue in issues:
                log(f"- {issue}")

            raise RuntimeError(
                "Short plan validation failed."
            )

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

        with OUTPUT_PATH.open("w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        log(f"Short plan saved to: {OUTPUT_PATH}")
        log("Done.")

        return 0

    except Exception as exc:
        log("")
        log(f"Planning failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())