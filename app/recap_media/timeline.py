"""Explicit conversions between Recap assembly and final-output time."""

from __future__ import annotations


RECAP_PLAYBACK_SPEED = 1.5


def validated_playback_speed(playback_speed: float = RECAP_PLAYBACK_SPEED) -> float:
    """Return the supported Recap speed without importing the renderer."""

    try:
        speed = float(playback_speed)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid recap playback speed: {playback_speed!r}") from exc
    if not 0.5 <= speed <= 2.0:
        raise ValueError("Recap playback speed must be between 0.5x and 2.0x.")
    return speed


def recap_final_duration_seconds(
    base_duration_seconds: float,
    playback_speed: float = RECAP_PLAYBACK_SPEED,
) -> float:
    """Final rendered duration for the pre-speed assembly timeline."""

    return round(max(0.0, float(base_duration_seconds)) / validated_playback_speed(playback_speed), 3)


def recap_base_to_final_time(
    base_time_seconds: float,
    playback_speed: float = RECAP_PLAYBACK_SPEED,
) -> float:
    """Map an assembly-timeline coordinate to final editor/output time."""

    return round(max(0.0, float(base_time_seconds)) / validated_playback_speed(playback_speed), 3)


def recap_final_to_base_time(
    final_time_seconds: float,
    playback_speed: float = RECAP_PLAYBACK_SPEED,
) -> float:
    """Map an authoritative editor/output coordinate back to render time."""

    return round(max(0.0, float(final_time_seconds)) * validated_playback_speed(playback_speed), 3)
