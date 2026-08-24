"""
Small, pure formatting/text utility functions shared across gui_app
mixins: time-code formatting (format_time/format_precise_time family) and
transcript-text helpers (e.g. detecting generic AI-editor phrasing via
GENERIC_EDITOR_PHRASES). No Qt/widget dependencies -- safe to unit test
directly.
"""

from __future__ import annotations

import re

from .constants import GENERIC_EDITOR_PHRASES


def format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"

    return f"{minutes:02d}:{remaining_seconds:02d}"


def format_precise_time(milliseconds: int) -> str:
    milliseconds = max(
        0,
        int(
            milliseconds
        ),
    )
    total_seconds = milliseconds // 1000
    millis = milliseconds % 1000
    hours = total_seconds // 3600
    minutes = (
        total_seconds
        % 3600
    ) // 60
    seconds = total_seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_card_time(milliseconds: int) -> str:
    seconds = max(
        0.0,
        milliseconds / 1000,
    )
    hours = int(
        seconds
        // 3600
    )
    minutes = int(
        (
            seconds
            % 3600
        )
        // 60
    )
    remaining = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:04.1f}"
    return f"{minutes:02d}:{remaining:04.1f}"


def is_generic_editor_text(text: str) -> bool:
    normalized = " ".join(
        str(
            text
            or ""
        ).lower().split()
    )
    if not normalized:
        return True
    return any(
        phrase in normalized
        for phrase in GENERIC_EDITOR_PHRASES
    )


def transcript_excerpt(
    text: str,
    max_words: int = 12,
) -> str:
    clean = " ".join(
        str(
            text
            or ""
        ).split()
    )
    if not clean:
        return ""
    pieces = re.split(
        r"(?<=[.!?])\s+",
        clean,
    )
    source = pieces[0].strip(
        " \"'"
    )
    words = source.split()
    if len(
        words
    ) > max_words:
        source = " ".join(
            words[:max_words]
        ).rstrip(
            ".,;:"
        )
    return source.strip()


def timestamp_to_seconds(value: str) -> float | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parts = text.replace(",", ".").split(":")

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

    except (TypeError, ValueError):
        return None


