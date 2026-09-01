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
TITLE_DEFAULT_X = 0.5
TITLE_DEFAULT_Y = 0.20
TITLE_DEFAULT_SCALE = 1.0
TITLE_DEFAULT_WIDTH = 0.76
TITLE_MIN_SCALE = 0.5
TITLE_MAX_SCALE = 2.0


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


def _coerce_fraction(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def persistent_title_state_from_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return the persistent title's normalized editor/export state.

    Text-only plans remain valid.  The transform defaults deliberately live
    here so a plan generated before direct canvas editing needs no migration.
    """

    payload = plan.get(PERSISTENT_TITLE_KEY) if isinstance(plan, dict) else {}
    if not isinstance(payload, dict):
        payload = {"text": payload}
    return {
        "text": normalize_persistent_title(payload.get("text", "")),
        "x": _coerce_fraction(payload.get("x"), TITLE_DEFAULT_X, 0.08, 0.92),
        "y": _coerce_fraction(payload.get("y"), TITLE_DEFAULT_Y, 0.02, 0.82),
        "scale": _coerce_fraction(
            payload.get("scale"), TITLE_DEFAULT_SCALE, TITLE_MIN_SCALE, TITLE_MAX_SCALE
        ),
        "width": _coerce_fraction(payload.get("width"), TITLE_DEFAULT_WIDTH, 0.40, 0.92),
        "active": bool(payload.get("active", True)),
    }


def set_persistent_title_on_plan(plan: dict[str, Any], value: object) -> dict[str, Any]:
    """Store title state without treating it as a timed editor clip."""

    title = normalize_persistent_title(value)
    if title:
        payload = plan.get(PERSISTENT_TITLE_KEY)
        payload = dict(payload) if isinstance(payload, dict) else {}
        payload["text"] = title
        plan[PERSISTENT_TITLE_KEY] = payload
    else:
        plan.pop(PERSISTENT_TITLE_KEY, None)
    return plan


def set_persistent_title_transform_on_plan(
    plan: dict[str, Any],
    *,
    x: object | None = None,
    y: object | None = None,
    scale: object | None = None,
    width: object | None = None,
    active: object | None = None,
) -> dict[str, Any]:
    """Persist direct-manipulation title settings in normalized canvas units."""

    state = persistent_title_state_from_plan(plan)
    if not state["text"]:
        return plan
    if x is not None:
        state["x"] = _coerce_fraction(x, state["x"], 0.08, 0.92)
    if y is not None:
        state["y"] = _coerce_fraction(y, state["y"], 0.02, 0.82)
    if scale is not None:
        state["scale"] = _coerce_fraction(
            scale, state["scale"], TITLE_MIN_SCALE, TITLE_MAX_SCALE
        )
    if width is not None:
        state["width"] = _coerce_fraction(width, state["width"], 0.40, 0.92)
    if active is not None:
        state["active"] = bool(active)
    plan[PERSISTENT_TITLE_KEY] = state
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

    state = persistent_title_state_from_plan({PERSISTENT_TITLE_KEY: title})
    text = escape_ass_text(state["text"])
    duration = max(0.01, float(duration_seconds))
    margin = round((1.0 - state["width"]) * OUTPUT_WIDTH / 2)
    position_x = round(state["x"] * OUTPUT_WIDTH)
    position_y = round(state["y"] * OUTPUT_HEIGHT)
    scale_percent = round(state["scale"] * 100)
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
        "-1,0,0,0,100,100,0,0,1,6,3,8,"
        f"{margin},{margin},{position_y},1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        (
            "Dialogue: 0,0:00:00.00,"
            f"{ass_timestamp(duration)},PersistentTitle,,0,0,0,,"
            f"{{\\pos({position_x},{position_y})\\fscx{scale_percent}\\fscy{scale_percent}}}{text}"
        ),
    ]
    return "\n".join(header) + "\n"


def write_persistent_title_ass(
    title: object,
    duration_seconds: float,
    path: Path,
) -> Path | None:
    """Write a title layer only when there is text; remove stale layers otherwise."""

    state = persistent_title_state_from_plan({PERSISTENT_TITLE_KEY: title})
    if not state["text"] or not state["active"]:
        path.unlink(missing_ok=True)
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        persistent_title_ass(state, duration_seconds),
        encoding="utf-8",
    )
    return path
