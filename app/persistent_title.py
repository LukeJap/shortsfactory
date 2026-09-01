"""Shared persistent-video-title contract for preview and export paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH


PERSISTENT_TITLE_KEY = "persistent_title"
MAX_TITLE_CHARACTERS = 180
TITLE_FONT_SIZE = 72
TITLE_MARGIN_LEFT = 120
TITLE_MARGIN_RIGHT = 120
TITLE_MARGIN_TOP = 320


def normalize_persistent_title(value: object) -> str:
    """Return the bounded, single-space title text stored in editor state."""

    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:MAX_TITLE_CHARACTERS]


def persistent_title_from_plan(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    payload = plan.get(PERSISTENT_TITLE_KEY)
    if isinstance(payload, dict):
        payload = payload.get("text", "")
    return normalize_persistent_title(payload)


def set_persistent_title_on_plan(plan: dict[str, Any], value: object) -> dict[str, Any]:
    """Store title state without treating it as a timed editor clip."""

    title = normalize_persistent_title(value)
    if title:
        plan[PERSISTENT_TITLE_KEY] = {"text": title}
    else:
        plan.pop(PERSISTENT_TITLE_KEY, None)
    return plan


def escape_ass_text(value: str) -> str:
    """Escape text for one ASS dialogue event while retaining natural wraps."""

    return (
        normalize_persistent_title(value)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(float(seconds) * 100)))
    hours, remaining = divmod(centiseconds, 360_000)
    minutes, remaining = divmod(remaining, 6_000)
    return f"{hours}:{minutes:02d}:{remaining / 100:05.2f}"


def persistent_title_ass(title: object, duration_seconds: float) -> str:
    """Build an export-only ASS layer that stays below captions."""

    text = escape_ass_text(str(title or ""))
    duration = max(0.01, float(duration_seconds))
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {OUTPUT_WIDTH}",
        f"PlayResY: {OUTPUT_HEIGHT}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: PersistentTitle,Arial,72,&H00FFFFFF,&H00000000,&H00000000,&H96000000,"
        "-1,0,0,0,100,100,0,0,1,6,3,8,120,120,320,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        (
            "Dialogue: 0,0:00:00.00,"
            f"{ass_timestamp(duration)},PersistentTitle,,0,0,0,,{text}"
        ),
    ]
    return "\n".join(header) + "\n"


def write_persistent_title_ass(
    title: object,
    duration_seconds: float,
    path: Path,
) -> Path | None:
    """Write a title layer only when there is text; remove stale layers otherwise."""

    text = normalize_persistent_title(title)
    if not text:
        path.unlink(missing_ok=True)
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        persistent_title_ass(text, duration_seconds),
        encoding="utf-8",
    )
    return path
