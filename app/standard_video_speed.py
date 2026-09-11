"""Shared Standard Mode video-speed helpers."""

from __future__ import annotations

import math


DEFAULT_STANDARD_VIDEO_SPEED = 1.0
STANDARD_VIDEO_SPEED_RANGE = (0.5, 2.0)


def coerce_standard_video_speed(value: object) -> float:
    """Return a finite Standard Mode playback speed within the supported range."""

    try:
        speed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_STANDARD_VIDEO_SPEED
    if not math.isfinite(speed):
        return DEFAULT_STANDARD_VIDEO_SPEED
    low, high = STANDARD_VIDEO_SPEED_RANGE
    return max(low, min(high, speed))


def format_standard_video_speed(value: object) -> str:
    return f"{coerce_standard_video_speed(value):.2f}x"


def build_standard_audio_tempo_filter(playback_speed: object) -> str:
    """Return the audio tempo adjustment that keeps source audio in sync."""

    speed = coerce_standard_video_speed(playback_speed)
    if math.isclose(speed, 1.0, abs_tol=1e-9):
        return ""
    return f"atempo={speed:.6f}"
