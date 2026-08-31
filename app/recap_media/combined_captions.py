"""Build one editable final-timeline caption track for an AI Recap."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from recap_media.timeline import RECAP_PLAYBACK_SPEED, recap_base_to_final_time
from make_captions import (
    FONT_SIZE,
    ass_time,
    caption_position_override_tag,
    coerce_caption_scale,
    escape_ass_text,
)
from recap_media.caption_alignment import build_narration_captions_ass_content


RECAP_CAPTION_PLAN_SCHEMA_VERSION = 1
RECAP_CAPTION_TIME_BASIS = "recap_final_timeline"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _source_words(cache_path: str | Path | None) -> list[dict[str, Any]]:
    if not cache_path:
        return []
    try:
        data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    words = data.get("words", []) if isinstance(data, dict) else []
    return [word for word in words if isinstance(word, dict) and _number(word.get("end")) > _number(word.get("start"))]


def _caption(start: float, end: float, text: str, block_id: str, domain: str) -> dict[str, Any] | None:
    text = str(text or "").strip()
    if not text or end <= start:
        return None
    return {
        "id": f"recap_caption_{block_id}_{start:.3f}",
        "start": round(start, 3),
        "end": round(end, 3),
        "text": text,
        "block_id": block_id,
        "speaker_domain": domain,
        "origin": "ai_recap",
        "time_basis": RECAP_CAPTION_TIME_BASIS,
        "manual_override": False,
        "active": True,
    }


def build_combined_recap_caption_plan(
    sequence: dict[str, Any],
    narration_captions: dict[str, Any],
    voiceover_clips: list[dict[str, Any]],
) -> dict[str, Any]:
    """Combine measured narration timing and cached source-dialogue words.

    Sequence time is the pre-speed assembly timeline. Every public cue is
    converted exactly once into the corrected final editor timeline.
    """

    clips = {str(clip.get("id", "")): clip for clip in voiceover_clips if isinstance(clip, dict)}
    sequence_starts = {}
    for segment in sequence.get("segments", []):
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id", ""))
        first_shot = next(
            (shot for shot in segment.get("shots", []) if isinstance(shot, dict)), {}
        )
        sequence_starts[segment_id] = _number(
            segment.get("timeline_start_seconds", first_shot.get("timeline_start_seconds", 0.0))
        )
    cues: list[dict[str, Any]] = []
    for segment in narration_captions.get("segments", []):
        if not isinstance(segment, dict):
            continue
        block_id = str(segment.get("segment_id", ""))
        clip = clips.get(block_id)
        if clip and (clip.get("active", True) is False or clip.get("deleted")):
            continue
        if clip:
            offset = _number(clip.get("start"))
        elif block_id in sequence_starts:
            offset = sequence_starts[block_id]
        else:
            continue
        for word in segment.get("words", []):
            if not isinstance(word, dict):
                continue
            cue = _caption(
                recap_base_to_final_time(offset + _number(word.get("start"))),
                recap_base_to_final_time(offset + _number(word.get("end"))),
                str(word.get("text", "")), block_id, "narration",
            )
            if cue:
                cues.append(cue)

    for segment in sequence.get("segments", []):
        if not isinstance(segment, dict) or str(segment.get("block_type", "")) != "source_moment":
            continue
        block_id = str(segment.get("segment_id", ""))
        for shot in segment.get("shots", []):
            if not isinstance(shot, dict) or not shot.get("source_audio_insert"):
                continue
            source_start = _number(shot.get("resolved_start", shot.get("start")))
            source_end = _number(shot.get("resolved_end", shot.get("end")))
            timeline_start = _number(shot.get("timeline_start_seconds"))
            for word in _source_words(shot.get("transcript_cache_path")):
                word_start, word_end = _number(word.get("start")), _number(word.get("end"))
                if word_end <= source_start or word_start >= source_end:
                    continue
                cue = _caption(
                    recap_base_to_final_time(timeline_start + max(0.0, word_start - source_start)),
                    recap_base_to_final_time(timeline_start + min(source_end - source_start, word_end - source_start)),
                    str(word.get("text") or word.get("word") or ""), block_id, "source_dialogue",
                )
                if cue:
                    cues.append(cue)

    return {
        "schema_version": RECAP_CAPTION_PLAN_SCHEMA_VERSION,
        "time_basis": RECAP_CAPTION_TIME_BASIS,
        "playback_speed": RECAP_PLAYBACK_SPEED,
        "cues": sorted(cues, key=lambda cue: (cue["start"], cue["end"], cue["id"])),
    }


def write_combined_recap_caption_plan(plan: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_combined_recap_caption_plan(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("time_basis") != RECAP_CAPTION_TIME_BASIS:
        raise ValueError("invalid combined Recap caption plan")
    if not isinstance(data.get("cues"), list):
        raise ValueError("combined Recap caption plan has no cues")
    return data


def write_combined_recap_caption_ass(
    plan: dict[str, Any],
    path: Path,
    render_settings: dict[str, Any] | None = None,
) -> None:
    """Derive the legacy ASS cache from the authoritative editable cues."""

    header = build_narration_captions_ass_content({"segments": []}, [])
    settings = render_settings or {}
    scale = coerce_caption_scale(settings.get("caption_scale"))
    header = re.sub(
        r"Style: Recap,Arial,\d+,",
        f"Style: Recap,Arial,{max(1, round(FONT_SIZE * scale))},",
        header,
        count=1,
    )
    position_tag = caption_position_override_tag(settings)
    lines = []
    for cue in plan.get("cues", []):
        if not isinstance(cue, dict) or cue.get("active", True) is False:
            continue
        start, end = _number(cue.get("start")), _number(cue.get("end"))
        if end <= start:
            continue
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Recap,,0,0,0,,"
            f"{position_tag}{escape_ass_text(str(cue.get('text', '')))}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
