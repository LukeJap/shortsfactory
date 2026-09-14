"""
Pure base video polish preset definitions for ShortsFactory.

This module does not run FFmpeg. It only returns deterministic filter
fragments that can be composed into an FFmpeg -vf chain by production render
code or developer comparison utilities.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BasePolishPreset:
    contrast: float = 1.0
    brightness: float = 0.0
    saturation: float = 1.0
    gamma: float = 1.0
    warmth: float = 0.0
    luma_amount: float = 0.0
    chroma_amount: float = 0.0


POLISH_PRESETS: dict[str, BasePolishPreset] = {
    "OFF": BasePolishPreset(),
    "POP": BasePolishPreset(
        contrast=1.10,
        brightness=0.004,
        saturation=1.18,
        gamma=1.0,
        warmth=0.04,
        luma_amount=0.45,
        chroma_amount=0.0,
    ),
    "WARM_POP": BasePolishPreset(
        contrast=1.14,
        brightness=0.006,
        saturation=1.24,
        gamma=0.99,
        warmth=0.08,
        luma_amount=0.55,
        chroma_amount=0.0,
    ),
    "VIRAL_POP": BasePolishPreset(
        contrast=1.18,
        brightness=0.008,
        saturation=1.30,
        gamma=0.98,
        warmth=0.12,
        luma_amount=0.70,
        chroma_amount=0.0,
    ),
}


PRODUCTION_POLISH_PRESET = "VIRAL_POP"


PRESET_ALIASES = {
    "": "OFF",
    "A": "OFF",
    "CONTROL": "OFF",
    "OFF_CONTROL": "OFF",
    "B": "POP",
    "C": "WARM_POP",
    "D": "VIRAL_POP",
}


def normalize_polish_preset(
    value: str | None,
) -> str:
    """
    Return a known preset key. Unknown values fall back to OFF so polish is
    never applied accidentally.
    """

    normalized = (
        str(
            value
            or ""
        )
        .strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )

    normalized = PRESET_ALIASES.get(
        normalized,
        normalized,
    )

    if normalized in POLISH_PRESETS:
        return normalized

    return "OFF"


def _scale_from_neutral(
    value: float,
    neutral: float,
    strength: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """Scale an effect delta while keeping FFmpeg parameters in safe bounds."""

    return min(
        maximum,
        max(
            minimum,
            neutral + ((value - neutral) * strength),
        ),
    )


def _coerce_intensity(value: float | None) -> float:
    """Clamp the 0-200% Filter Intensity UI value to a safe 0.0-2.0 range."""

    try:
        intensity = float(value)
    except (TypeError, ValueError):
        return 1.0
    if intensity != intensity:  # NaN
        return 1.0
    return min(2.0, max(0.0, intensity))


def polish_filter_values(
    preset: str | None,
    intensity: float = 1.0,
) -> dict[str, float]:
    """Return the scaled contrast/saturation/brightness for a preset+intensity.

    This is the exact grade math `polish_filters()` bakes into the render.
    It's exposed separately so the GUI's lightweight per-frame monitor
    preview -- which only approximates contrast/saturation/brightness, not
    the full eq/colorbalance/unsharp chain -- can stay driven by the same
    numbers the real render uses instead of drifting out of sync with it.
    """

    key = normalize_polish_preset(preset)
    if key == "OFF":
        return {"contrast": 1.0, "saturation": 1.0, "brightness": 0.0}

    strength = _coerce_intensity(intensity)
    if strength <= 0.0:
        return {"contrast": 1.0, "saturation": 1.0, "brightness": 0.0}

    values = POLISH_PRESETS[key]
    return {
        "contrast": _scale_from_neutral(values.contrast, 1.0, strength, minimum=0.5, maximum=2.25),
        "saturation": _scale_from_neutral(values.saturation, 1.0, strength, minimum=0.0, maximum=3.0),
        "brightness": _scale_from_neutral(values.brightness, 0.0, strength, minimum=-0.25, maximum=0.25),
    }


def polish_filters(
    preset: str | None,
    intensity: float = 1.0,
) -> list[str]:
    """
    Return deterministic FFmpeg filter fragments for a base polish preset.

    `intensity` (0.0-2.0, matching the Filter Intensity slider) scales the
    preset's values away from a neutral/no-op grade: 0.0 applies no filters
    at all, 1.0 reproduces the preset's own values unchanged, and 2.0
    doubles the deviation from neutral. This is the single place that turns
    Filter Intensity into pixels, so the live monitor preview and the final
    render always agree on what the slider does.

    OFF returns no filters; every non-OFF preset uses eq(), colorbalance()
    warmth, and luma-only unsharp. No vignette, drawbox, denoise, or chroma
    sharpening is used.
    """

    key = normalize_polish_preset(
        preset
    )
    if key == "OFF":
        return []

    strength = _coerce_intensity(intensity)
    if strength <= 0.0:
        return []

    values = POLISH_PRESETS[
        key
    ]

    grade = polish_filter_values(preset, intensity)
    contrast = grade["contrast"]
    saturation = grade["saturation"]
    brightness = grade["brightness"]
    gamma = _scale_from_neutral(values.gamma, 1.0, strength, minimum=0.5, maximum=1.5)
    warmth = _scale_from_neutral(values.warmth, 0.0, strength, minimum=-0.5, maximum=0.5)
    luma_amount = min(1.5, values.luma_amount * strength)
    chroma_amount = min(0.75, values.chroma_amount * strength)

    filters = [
        (
            f"eq=contrast={contrast:.4f}:"
            f"brightness={brightness:.4f}:"
            f"saturation={saturation:.4f}:"
            f"gamma={gamma:.4f}"
        ),
    ]

    if warmth != 0.0:
        filters.append(
            (
                f"colorbalance=rs={warmth:.4f}:"
                f"rm={warmth:.4f}:"
                f"rh={warmth * 0.5000:.4f}:"
                f"bs={-warmth:.4f}:"
                f"bm={-warmth:.4f}:"
                f"bh={-warmth * 0.5000:.4f}"
            )
        )

    filters.append(
        (
            f"unsharp=5:5:{luma_amount:.4f}:"
            f"3:3:{chroma_amount:.4f}"
        )
    )

    return filters


def polish_filter_chain(
    preset: str | None,
    intensity: float = 1.0,
) -> str:
    """Return a comma-joined FFmpeg filter chain fragment for a preset."""

    return ",".join(
        polish_filters(
            preset,
            intensity,
        )
    )
