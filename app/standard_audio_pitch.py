"""Duration-preserving audio pitch helpers for Standard Mode."""

from __future__ import annotations

import math


DEFAULT_STANDARD_AUDIO_PITCH_SEMITONES = 0.0
STANDARD_AUDIO_PITCH_SEMITONES_RANGE = (-4.0, 4.0)


def coerce_standard_audio_pitch(value: object) -> float:
    """Return a safely clamped Standard Mode pitch value in semitones."""

    try:
        semitones = float(value)
    except (TypeError, ValueError):
        return DEFAULT_STANDARD_AUDIO_PITCH_SEMITONES
    if not math.isfinite(semitones):
        return DEFAULT_STANDARD_AUDIO_PITCH_SEMITONES
    low, high = STANDARD_AUDIO_PITCH_SEMITONES_RANGE
    return max(low, min(high, semitones))


def standard_audio_pitch_ratio(semitones: object) -> float:
    return 2 ** (coerce_standard_audio_pitch(semitones) / 12.0)


def build_standard_audio_pitch_filter(semitones: object) -> str:
    """Build the shared FFmpeg filter without changing audio duration."""

    value = coerce_standard_audio_pitch(semitones)
    if math.isclose(value, 0.0, abs_tol=1e-9):
        return ""
    return (
        f"rubberband=pitch={standard_audio_pitch_ratio(value):.6f}:tempo=1.000:"
        "formant=preserved:pitchq=quality"
    )


def format_standard_audio_pitch(semitones: object) -> str:
    value = coerce_standard_audio_pitch(semitones)
    return f"{value:+.1f} st" if value else "0.0 st"
