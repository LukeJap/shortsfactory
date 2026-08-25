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


def polish_filters(
    preset: str | None,
) -> list[str]:
    """
    Return deterministic FFmpeg filter fragments for a base polish preset.

    OFF returns no filters; every non-OFF preset uses eq(), colorbalance()
    warmth, and luma-only unsharp. No vignette, drawbox, denoise, or chroma
    sharpening is used.
    """

    key = normalize_polish_preset(
        preset
    )
    if key == "OFF":
        return []

    values = POLISH_PRESETS[
        key
    ]

    filters = [
        (
            f"eq=contrast={values.contrast:.4f}:"
            f"brightness={values.brightness:.4f}:"
            f"saturation={values.saturation:.4f}:"
            f"gamma={values.gamma:.4f}"
        ),
    ]

    if values.warmth != 0.0:
        filters.append(
            (
                f"colorbalance=rs={values.warmth:.4f}:"
                f"rm={values.warmth:.4f}:"
                f"rh={values.warmth * 0.5000:.4f}:"
                f"bs={-values.warmth:.4f}:"
                f"bm={-values.warmth:.4f}:"
                f"bh={-values.warmth * 0.5000:.4f}"
            )
        )

    filters.append(
        (
            f"unsharp=5:5:{values.luma_amount:.4f}:"
            f"3:3:{values.chroma_amount:.4f}"
        )
    )

    return filters


def polish_filter_chain(
    preset: str | None,
) -> str:
    """Return a comma-joined FFmpeg filter chain fragment for a preset."""

    return ",".join(
        polish_filters(
            preset
        )
    )
