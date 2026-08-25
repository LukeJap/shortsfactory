"""
Builds semantic per-moment polish accents driven by visual_emphasis.py's
intensity curve. The old energy-tier baseline color grade remains
available through baseline_filters() / build_filter_chain() for standalone
compatibility, but the production render path applies the always-on base
look in render.py's STEP 1 and uses semantic-only filters here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .visual_emphasis import (
        DEFAULT_ENERGY,
        VISUAL_FX_PLAN_PATH,
        build_intensity_curve,
        classify_word,
        content_rect_from_settings,
        energy_profile,
        intensity_for_moment,
        load_render_settings,
        normalize_energy,
        semantic_recipe,
        word_time,
    )
except ImportError:
    from visual_emphasis import (
        DEFAULT_ENERGY,
        VISUAL_FX_PLAN_PATH,
        build_intensity_curve,
        classify_word,
        content_rect_from_settings,
        energy_profile,
        intensity_for_moment,
        load_render_settings,
        normalize_energy,
        semantic_recipe,
        word_time,
    )

try:
    from .canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
except ImportError:
    from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH

try:
    from .pipeline_paths import SUBTITLES_PATH as TRANSCRIPT_PATH
except ImportError:
    from pipeline_paths import SUBTITLES_PATH as TRANSCRIPT_PATH


ROOT = Path(__file__).resolve().parent.parent

VIDEO_PATH = ROOT / "output" / "rendered" / "short1_tight.mp4"
TEMP_PATH = ROOT / "output" / "rendered" / "short1_fx_tmp.mp4"

def _default_font_candidates() -> list[Path]:
    """
    Bold/impact-style drawtext fonts, in priority order, for whichever OS
    this happens to be running on. Each candidate is checked for existence
    at call time in drawtext_font_option(), so a platform with none of these
    installed falls back to FFmpeg's default fontconfig lookup rather than
    failing.
    """

    if sys.platform.startswith("win"):
        return [
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path(r"C:\Windows\Fonts\impact.ttf"),
            Path(r"C:\Windows\Fonts\arial.ttf"),
        ]

    if sys.platform == "darwin":
        return [
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
            Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/Helvetica.ttc"),
        ]

    return [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]


FONT_CANDIDATES = _default_font_candidates()


@dataclass(frozen=True)
class ProfessionalFxRecipe:
    durations: dict[str, float]
    motion: str
    zoom: dict[str, float]
    contrast_peak: float = 0.0
    saturation_peak: float = 0.0
    brightness_peak: float = 0.0
    sharpness: float = 0.0
    blur_radius: dict[str, float] | None = None
    blur_angle: float = 0.0
    glow_warmth: float = 0.0


PROFESSIONAL_FX_RECIPES: dict[str, ProfessionalFxRecipe] = {
    "impact_punch": ProfessionalFxRecipe(
        durations={
            "LOW": 0.38,
            "PUNCHY": 0.48,
            "MAXIMUM": 0.56,
        },
        motion="impact_punch",
        zoom={
            "LOW": 1.045,
            "PUNCHY": 1.080,
            "MAXIMUM": 1.105,
        },
        contrast_peak=0.060,
        saturation_peak=0.045,
        brightness_peak=0.010,
        sharpness=0.18,
    ),
    "reaction_push_in": ProfessionalFxRecipe(
        durations={
            "LOW": 0.62,
            "PUNCHY": 0.76,
            "MAXIMUM": 0.88,
        },
        motion="punch_in",
        zoom={
            "LOW": 1.035,
            "PUNCHY": 1.065,
            "MAXIMUM": 1.090,
        },
        contrast_peak=0.045,
        saturation_peak=0.040,
        brightness_peak=0.004,
        sharpness=0.12,
    ),
    "micro_camera_hit": ProfessionalFxRecipe(
        durations={
            "LOW": 0.14,
            "PUNCHY": 0.20,
            "MAXIMUM": 0.24,
        },
        motion="impact_jolt",
        zoom={
            "LOW": 1.020,
            "PUNCHY": 1.045,
            "MAXIMUM": 1.065,
        },
        contrast_peak=0.040,
        saturation_peak=0.030,
        brightness_peak=0.006,
        sharpness=0.08,
        blur_radius={
            "LOW": 0.0,
            "PUNCHY": 1.2,
            "MAXIMUM": 1.8,
        },
        blur_angle=12.0,
    ),
    "whip_blur": ProfessionalFxRecipe(
        durations={
            "LOW": 0.16,
            "PUNCHY": 0.22,
            "MAXIMUM": 0.28,
        },
        motion="directional_push",
        zoom={
            "LOW": 1.020,
            "PUNCHY": 1.045,
            "MAXIMUM": 1.065,
        },
        contrast_peak=0.030,
        saturation_peak=0.025,
        brightness_peak=0.003,
        blur_radius={
            "LOW": 1.0,
            "PUNCHY": 2.0,
            "MAXIMUM": 3.0,
        },
        blur_angle=0.0,
    ),
    "cinematic_push": ProfessionalFxRecipe(
        durations={
            "LOW": 0.90,
            "PUNCHY": 1.15,
            "MAXIMUM": 1.35,
        },
        motion="slow_push",
        zoom={
            "LOW": 1.030,
            "PUNCHY": 1.050,
            "MAXIMUM": 1.070,
        },
        contrast_peak=0.050,
        saturation_peak=-0.020,
        brightness_peak=-0.004,
    ),
    "comedy_pull_out": ProfessionalFxRecipe(
        durations={
            "LOW": 0.48,
            "PUNCHY": 0.68,
            "MAXIMUM": 0.82,
        },
        motion="punch_out",
        zoom={
            "LOW": 1.025,
            "PUNCHY": 1.050,
            "MAXIMUM": 1.070,
        },
        contrast_peak=0.035,
        saturation_peak=0.025,
        brightness_peak=0.002,
    ),
    "bloom_glow": ProfessionalFxRecipe(
        durations={
            "LOW": 0.42,
            "PUNCHY": 0.58,
            "MAXIMUM": 0.72,
        },
        motion="punch_in",
        zoom={
            "LOW": 1.025,
            "PUNCHY": 1.045,
            "MAXIMUM": 1.065,
        },
        contrast_peak=0.040,
        saturation_peak=0.070,
        brightness_peak=0.016,
        sharpness=0.08,
        glow_warmth=0.010,
    ),
}


LEGACY_EFFECT_ALIASES = {
    "contrast_hit": "reaction_push_in",
    "detail_hit": "reaction_push_in",
    "contrast_flash": "impact_punch",
    "overdrive_flash": "impact_punch",
    "red_danger": "impact_punch",
    "rgb_split": "impact_punch",
    "glitch_hit": "impact_punch",
    "magenta_hype": "reaction_push_in",
    "posterize_hit": "comedy_pull_out",
    "bloom_flash": "bloom_glow",
    "green_money": "bloom_glow",
    "warm_gold": "bloom_glow",
    "desat_hit": "cinematic_push",
    "cold_blue": "cinematic_push",
    "spotlight": "cinematic_push",
    "slam_text": "impact_punch",
}


def load_json(
    path: Path,
) -> dict[str, Any]:

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}

    return (
        data
        if isinstance(
            data,
            dict,
        )
        else {}
    )


def clean_alpha(
    value: str,
) -> str:

    return re.sub(
        r"[^a-zA-Z']",
        "",
        value,
    ).lower()


def parse_word_time(
    word: dict[str, Any],
) -> tuple[float, float] | None:

    try:
        start = float(
            word.get(
                "start",
                0.0,
            )
            or 0.0
        )
        end = float(
            word.get(
                "end",
                start,
            )
            or start
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if end <= start:
        end = start + 0.16

    return (
        start,
        end,
    )


def effect_for_word(
    raw_word: str,
    classification: dict[str, Any],
) -> str:
    _, recipe = professional_moment_recipe(
        raw_word,
        classification,
    )

    return recipe


def normalize_fx_recipe(
    value: str | None,
) -> str:
    normalized = (
        str(
            value
            or ""
        )
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )

    if normalized in PROFESSIONAL_FX_RECIPES:
        return normalized

    return LEGACY_EFFECT_ALIASES.get(
        normalized,
        "reaction_push_in",
    )


def recipe_value(
    values: dict[str, float],
    energy: str,
) -> float:
    return float(
        values.get(
            normalize_energy(
                energy
            ),
            values.get(
                DEFAULT_ENERGY,
                next(
                    iter(
                        values.values()
                    )
                ),
            ),
        )
    )


def event_duration(
    effect: str,
    level: str,
    energy: str,
) -> float:
    recipe = PROFESSIONAL_FX_RECIPES[
        normalize_fx_recipe(
            effect
        )
    ]

    duration = recipe_value(
        recipe.durations,
        energy,
    )

    if str(
        level
    ).upper() == "EXTREME":
        duration *= 1.12

    return round(
        duration,
        3,
    )


def professional_moment_recipe(
    raw_word: str,
    classification: dict[str, Any],
) -> tuple[str, str]:
    legacy_recipe = semantic_recipe(
        raw_word,
        classification,
    )
    level = str(
        classification.get(
            "level",
            "NORMAL",
        )
    ).upper()
    category = str(
        classification.get(
            "category",
            "speech",
        )
    )
    accent = str(
        classification.get(
            "accent",
            "bone",
        )
    )
    alpha = clean_alpha(
        raw_word
    )

    if legacy_recipe == "fail_awkward":
        return (
            "AWKWARD",
            "comedy_pull_out",
        )

    if category in {
        "money",
        "number",
    } or legacy_recipe in {
        "money",
        "hype_win",
    }:
        return (
            "TRIUMPH",
            "bloom_glow",
        )

    if legacy_recipe in {
        "doom_negative",
        "creepy_cold",
        "nostalgia_memory",
    }:
        return (
            "EMOTIONAL",
            "cinematic_push",
        )

    if alpha in {
        "what",
        "who",
        "why",
        "wait",
        "wow",
        "oh",
        "really",
    } or legacy_recipe == "reaction":
        return (
            "REACTION",
            (
                "impact_punch"
                if level == "EXTREME"
                else "reaction_push_in"
            ),
        )

    if accent == "danger" or alpha in {
        "angry",
        "danger",
        "dangerous",
        "ow",
    }:
        return (
            "ANGER",
            "impact_punch",
        )

    if legacy_recipe == "wtf_chaos":
        return (
            "SHOCK",
            (
                "impact_punch"
                if level in {
                    "IMPACT",
                    "EXTREME",
                }
                else "reaction_push_in"
            ),
        )

    if level in {
        "IMPACT",
        "EXTREME",
    }:
        return (
            "PUNCHLINE",
            "impact_punch",
        )

    return (
        "REACTION",
        "reaction_push_in",
    )


def clip_duration_from_words(
    words: list[dict[str, Any]],
) -> float:

    duration = 0.0

    for word in words:
        timing = word_time(
            word
        )

        if timing is None:
            continue

        _, end = timing
        duration = max(
            duration,
            end,
        )

    return duration


def stack_id_for_moment(
    index: int,
    start: float,
) -> str:

    return (
        f"stack_{index:02d}_{int(round(start * 1000)):06d}"
    )


def build_semantic_moments(
    words: list[dict[str, Any]],
    energy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

    energy = normalize_energy(
        energy
    )
    duration = clip_duration_from_words(
        words
    )
    profile = energy_profile(
        energy
    )
    max_moments = int(
        profile.get(
            "max_filter_events",
            4,
        )
    )
    if duration < 9:
        max_moments = min(
            1,
            max_moments,
        )
    elif duration < 18:
        max_moments = min(
            2,
            max_moments,
        )
    elif duration < 30:
        max_moments = min(
            3
            if energy != "MAXIMUM"
            else 4,
            max_moments,
        )

    max_hero_moments = int(
        profile.get(
            "max_hero_moments",
            1,
        )
    )
    spacing = {
        "LOW": 8.0,
        "PUNCHY": 4.0,
        "MAXIMUM": 2.25,
    }.get(
        energy,
        4.0,
    )

    candidates: list[dict[str, Any]] = []

    for word in words:
        raw_word = str(
            word.get(
                "word",
                "",
            )
            or ""
        ).strip()

        if not raw_word:
            continue

        timing = word_time(
            word
        )

        if timing is None:
            continue

        classification = classify_word(
            word,
            energy,
        )
        level = str(
            classification.get(
                "level",
                "NORMAL",
            )
        )

        if level == "NORMAL":
            continue

        start, end = timing
        legacy_recipe = semantic_recipe(
            word,
            classification,
        )
        moment_type, recipe = professional_moment_recipe(
            raw_word,
            classification,
        )
        intensity = intensity_for_moment(
            start,
            duration,
            classification,
            legacy_recipe,
            energy,
        )
        intensity_value = float(
            intensity.get(
                "intensity",
                0.0,
            )
        )
        score = float(
            classification.get(
                "score",
                0.0,
            )
            or 0.0
        )

        hero_score = (
            score
            + intensity_value
            * 4.0
            + (
                2.0
                if recipe
                in {
                    "impact_punch",
                    "reaction_push_in",
                    "bloom_glow",
                }
                else 0.0
            )
        )

        candidates.append(
            {
                "start": round(
                    start,
                    3,
                ),
                "end": round(
                    end,
                    3,
                ),
                "trigger_word": raw_word,
                "level": level,
                "score": round(
                    score,
                    3,
                ),
                "recipe": recipe,
                "legacy_recipe": legacy_recipe,
                "moment_type": moment_type,
                "region": intensity.get(
                    "region",
                    "unknown",
                ),
                "intensity": intensity_value,
                "hero_score": round(
                    hero_score,
                    3,
                ),
                "reason": classification.get(
                    "reason",
                    "",
                ),
                "category": classification.get(
                    "category",
                    "speech",
                ),
                "accent": classification.get(
                    "accent",
                    "none",
                ),
            }
        )

    candidates.sort(
        key=lambda moment: (
            -float(
                moment.get(
                    "hero_score",
                    0.0,
                )
            ),
            float(
                moment.get(
                    "start",
                    0.0,
                )
            ),
        )
    )

    selected: list[dict[str, Any]] = []

    for moment in candidates:
        start = float(
            moment["start"]
        )

        if any(
            abs(
                start
                - float(
                    selected_moment["start"]
                )
            )
            < spacing
            for selected_moment in selected
        ):
            continue

        selected.append(
            moment
        )

        if len(
            selected
        ) >= max_moments:
            break

    if energy == "MAXIMUM":
        hero_candidates = sorted(
            selected,
            key=lambda moment: (
                -float(
                    moment.get(
                        "hero_score",
                        0.0,
                    )
                ),
                float(
                    moment.get(
                        "start",
                        0.0,
                    )
                ),
            ),
        )
        for moment in hero_candidates[
            :max_hero_moments
        ]:
            if (
                float(
                    moment.get(
                        "intensity",
                        0.0,
                    )
                )
                >= 0.70
                or str(
                    moment.get(
                        "level",
                        "",
                    )
                )
                in {
                    "IMPACT",
                    "EXTREME",
                }
            ):
                moment["hero"] = True

    selected.sort(
        key=lambda moment: float(
            moment["start"]
        )
    )

    curve = build_intensity_curve(
        duration,
        selected,
    )

    return (
        selected,
        curve,
    )


def base_filter_for_recipe(
    recipe: str,
    raw_word: str,
    classification: dict[str, Any],
    energy: str,
) -> str:

    normalized = normalize_fx_recipe(
        recipe
    )

    if normalized in PROFESSIONAL_FX_RECIPES:
        return normalized

    return effect_for_word(
        raw_word,
        classification,
    )


def supporting_effects_for_moment(
    moment: dict[str, Any],
    energy: str,
) -> list[str]:

    recipe = normalize_fx_recipe(
        str(
            moment.get(
                "recipe",
                "reaction_push_in",
            )
        )
    )
    level = str(
        moment.get(
            "level",
            "",
        )
    ).upper()
    hero = bool(
        moment.get(
            "hero",
            False,
        )
    )
    intensity = float(
        moment.get(
            "intensity",
            0.0,
        )
        or 0.0
    )

    effects = [
        recipe
    ]

    if normalize_energy(
        energy
    ) == "LOW":
        return effects

    if (
        recipe == "impact_punch"
        and (
            hero
            or level in {
                "IMPACT",
                "EXTREME",
            }
            or intensity >= 0.68
        )
    ):
        effects.append(
            "micro_camera_hit"
        )

    if (
        normalize_energy(
            energy
        )
        == "MAXIMUM"
        and recipe in {
            "impact_punch",
            "comedy_pull_out",
        }
        and (
            hero
            or intensity >= 0.78
        )
    ):
        effects.append(
            "whip_blur"
        )

    return list(
        dict.fromkeys(
            effects
        )
    )


def max_stack_effects_for_moment(
    moment: dict[str, Any],
) -> list[str]:

    return supporting_effects_for_moment(
        moment,
        "MAXIMUM",
    )


def motion_events_for_moments(
    moments: list[dict[str, Any]],
    duration: float,
    energy: str,
) -> list[dict[str, Any]]:

    if (
        not moments
        or duration <= 0
    ):
        return []

    energy = normalize_energy(
        energy
    )
    events: list[dict[str, Any]] = []

    for index, moment in enumerate(
        moments
    ):
        recipe_key = normalize_fx_recipe(
            str(
                moment.get(
                    "recipe",
                    "reaction_push_in",
                )
            )
        )
        recipe = PROFESSIONAL_FX_RECIPES[
            recipe_key
        ]
        start = max(
            0.0,
            coerce_float(
                moment.get(
                    "start",
                    0.0,
                )
                or 0.0,
                0.0,
            )
            - (
                0.04
                if recipe_key
                in {
                    "impact_punch",
                    "micro_camera_hit",
                    "whip_blur",
                }
                else 0.08
            ),
        )
        event_end = min(
            duration,
            start
            + recipe_value(
                recipe.durations,
                energy,
            ),
        )

        if event_end <= start:
            continue

        movement = recipe.motion
        event: dict[str, Any] = {
            "start": round(
                start,
                3,
            ),
            "end": round(
                event_end,
                3,
            ),
            "zoom": round(
                recipe_value(
                    recipe.zoom,
                    energy,
                ),
                3,
            ),
            "trigger_word": str(
                moment.get(
                    "trigger_word",
                    "speech_beat",
                )
                or "speech_beat"
            ),
            "energy": energy,
            "movement": movement,
            "reason": "semantic visual-fx recipe",
            "source": "visual_fx_recipe",
            "fx_recipe": recipe_key,
            "moment_type": moment.get(
                "moment_type",
                "",
            ),
            "stack_id": moment.get(
                "stack_id",
                "",
            ),
        }

        if movement in {
            "impact_punch",
            "impact_jolt",
        }:
            direction = (
                -1.0
                if index % 2
                else 1.0
            )
            event["x_bias"] = round(
                0.018
                * direction,
                3,
            )
            event["y_bias"] = round(
                (
                    -0.012
                    if index % 3 == 0
                    else 0.012
                ),
                3,
            )
        elif movement == "directional_push":
            event["x_bias"] = (
                -0.026
                if index % 2
                else 0.026
            )

        events.append(
            event
        )

    events.sort(
        key=lambda event: float(
            event.get(
                "start",
                0.0,
            )
            or 0.0
        )
    )

    return events


def motion_event_spacing(
    energy: str,
) -> float:

    return {
        "LOW": 1.20,
        "PUNCHY": 0.82,
        "MAXIMUM": 0.58,
    }.get(
        normalize_energy(
            energy
        ),
        0.82,
    )


def merge_motion_events(
    recipe_events: list[dict[str, Any]],
    fallback_events: list[dict[str, Any]],
    energy: str,
) -> list[dict[str, Any]]:

    spacing = motion_event_spacing(
        energy
    )
    merged = [
        dict(
            event
        )
        for event in recipe_events
    ]

    for fallback in fallback_events:
        fallback_start = float(
            fallback.get(
                "start",
                0.0,
            )
            or 0.0
        )
        if any(
            abs(
                fallback_start
                - float(
                    event.get(
                        "start",
                        0.0,
                    )
                    or 0.0
                )
            )
            < spacing
            for event in merged
        ):
            continue

        enriched = dict(
            fallback
        )
        enriched.setdefault(
            "source",
            "smart_motion_fallback",
        )
        merged.append(
            enriched
        )

    merged.sort(
        key=lambda event: float(
            event.get(
                "start",
                0.0,
            )
            or 0.0
        )
    )

    return merged


def moment_should_have_graphic(
    moment: dict[str, Any],
    energy: str,
) -> bool:

    return False


def event_from_moment(
    moment: dict[str, Any],
    effect: str,
    event_type: str,
    stack_id: str,
    start_offset: float = 0.0,
    duration: float | None = None,
) -> dict[str, Any]:

    effect = normalize_fx_recipe(
        effect
    )

    start = float(
        moment.get(
            "start",
            0.0,
        )
    ) + start_offset

    if duration is None:
        duration = event_duration(
            effect,
            str(
                moment.get(
                    "level",
                    "NORMAL",
                )
            ),
            str(
                moment.get(
                    "energy",
                    DEFAULT_ENERGY,
                )
            ),
        )

    end = max(
        float(
            moment.get(
                "end",
                start,
            )
        ),
        start
        + duration,
    )

    event: dict[str, Any] = {
        "type": event_type,
        "start": round(
            max(
                0.0,
                start,
            ),
            3,
        ),
        "end": round(
            end,
            3,
        ),
        "effect": effect,
        "trigger_word": moment.get(
            "trigger_word",
            "",
        ),
        "level": moment.get(
            "level",
            "NORMAL",
        ),
        "score": moment.get(
            "score",
            0.0,
        ),
        "reason": moment.get(
            "reason",
            "",
        ),
        "recipe": effect,
        "legacy_recipe": moment.get(
            "legacy_recipe",
            "",
        ),
        "moment_type": moment.get(
            "moment_type",
            "",
        ),
        "region": moment.get(
            "region",
            "unknown",
        ),
        "intensity": moment.get(
            "intensity",
            0.0,
        ),
        "stack_id": stack_id,
        "coordinated_stack": True,
    }

    if moment.get(
        "hero",
        False,
    ):
        event["hero"] = True

    return event


def expand_moments_to_events(
    moments: list[dict[str, Any]],
    energy: str,
) -> list[dict[str, Any]]:

    energy = normalize_energy(
        energy
    )
    events: list[dict[str, Any]] = []

    for index, moment in enumerate(
        moments,
        start=1,
    ):
        stack_id = stack_id_for_moment(
            index,
            float(
                moment.get(
                    "start",
                    0.0,
                )
            ),
        )
        raw_word = str(
            moment.get(
                "trigger_word",
                "",
            )
        )
        classification = {
            "level": moment.get(
                "level",
                "NORMAL",
            ),
            "score": moment.get(
                "score",
                0.0,
            ),
            "category": moment.get(
                "category",
                "speech",
            ),
            "accent": moment.get(
                "accent",
                "none",
            ),
            "reason": moment.get(
                "reason",
                "",
            ),
        }

        if energy == "MAXIMUM":
            effects = max_stack_effects_for_moment(
                moment
            )
        else:
            effects = [
                base_filter_for_recipe(
                    str(
                        moment.get(
                            "recipe",
                            "speech_emphasis",
                        )
                    ),
                    raw_word,
                    classification,
                    energy,
                )
            ]

        for effect in effects:
            events.append(
                event_from_moment(
                    moment,
                    effect,
                    "filter",
                    stack_id,
                    duration=event_duration(
                        effect,
                        str(
                            moment.get(
                                "level",
                                "NORMAL",
                            )
                        ),
                        energy,
                    ),
                )
            )

        if moment_should_have_graphic(
            moment,
            energy,
        ):
            graphic = event_from_moment(
                moment,
                "slam_text",
                "graphic",
                stack_id,
                duration=(
                    1.35
                    if energy == "MAXIMUM"
                    else 1.05
                ),
            )
            graphic["text"] = raw_word.upper()
            events.append(
                graphic
            )

    events.sort(
        key=lambda event: (
            float(
                event.get(
                    "start",
                    0.0,
                )
            ),
            0
            if event.get(
                "type"
            )
            == "graphic"
            else 1,
        )
    )

    return events


def build_fx_events(
    words: list[dict[str, Any]],
    energy: str,
) -> list[dict[str, Any]]:

    moments, _curve = build_semantic_moments(
        words,
        energy
    )
    return expand_moments_to_events(
        moments,
        energy,
    )


def coerce_fx_intensity(
    value: Any,
) -> float:

    try:
        intensity = float(
            value
        )
    except (TypeError, ValueError):
        return 1.0

    if intensity != intensity:  # NaN
        return 1.0

    return min(
        2.0,
        max(
            0.0,
            intensity,
        ),
    )


def _scaled_eq(
    contrast: float,
    saturation: float,
    brightness: float,
    intensity: float,
    gamma: float | None = None,
) -> str:

    # Scales each value's *deviation* from the neutral eq() baseline
    # (contrast=1.0, saturation=1.0, brightness=0.0, gamma=1.0) by
    # intensity, so intensity=1.0 reproduces the tier's original values
    # exactly and intensity=0.0 fully neutralizes the color grade.
    parts = (
        f"contrast={1.0 + (contrast - 1.0) * intensity:.4f}:"
        f"saturation={1.0 + (saturation - 1.0) * intensity:.4f}:"
        f"brightness={brightness * intensity:.4f}"
    )

    if gamma is not None:
        parts += f":gamma={1.0 + (gamma - 1.0) * intensity:.4f}"

    return f"eq={parts}"


def _scaled_unsharp(
    amount: float,
    camount: float,
    intensity: float,
) -> str:

    return (
        f"unsharp=5:5:{amount * intensity:.4f}:"
        f"3:3:{camount * intensity:.4f}"
    )


def _scaled_vignette(
    denominator: float,
    intensity: float,
) -> str | None:

    if intensity <= 0.0:
        return None

    return f"vignette=PI/{denominator / intensity:.4f}"


def baseline_filters(
    energy: str,
    intensity: float = 1.0,
) -> list[str]:

    energy = normalize_energy(
        energy
    )
    intensity = coerce_fx_intensity(
        intensity
    )

    if energy == "LOW":
        return [
            _scaled_eq(1.08, 1.12, 0.004, intensity),
            _scaled_unsharp(0.32, 0.12, intensity),
        ]

    if energy == "MAXIMUM":
        filters = [
            _scaled_eq(1.42, 1.62, 0.010, intensity, gamma=0.96),
            _scaled_unsharp(0.86, 0.26, intensity),
        ]

        darken_alpha = min(
            1.0,
            max(
                0.0,
                0.035 * intensity,
            ),
        )
        if darken_alpha > 0.0:
            filters.append(
                f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{darken_alpha:.4f}:t=fill"
            )

        vignette = _scaled_vignette(4.2, intensity)
        if vignette:
            filters.append(vignette)

        return filters

    filters = [
        _scaled_eq(1.18, 1.28, 0.008, intensity),
        _scaled_unsharp(0.52, 0.18, intensity),
    ]

    vignette = _scaled_vignette(7, intensity)
    if vignette:
        filters.append(vignette)

    return filters


def enable_between(
    start: float,
    end: float,
) -> str:

    return (
        f"between(t,{start:.3f},{end:.3f})"
    )


def escape_drawtext(
    value: str,
) -> str:

    return (
        value.replace(
            "\\",
            "\\\\",
        )
        .replace(
            ":",
            "\\:",
        )
        .replace(
            "'",
            "\\'",
        )
        .replace(
            "%",
            "\\%",
        )
    )


def drawtext_font_option() -> str:

    for path in FONT_CANDIDATES:
        if path.exists():
            escaped = (
                path.as_posix()
                .replace(
                    ":",
                    "\\:",
                    1,
                )
                .replace(
                    "'",
                    "\\'",
                )
            )
            return f"fontfile='{escaped}':"

    return ""


def coerce_float(
    value: Any,
    default: float,
) -> float:
    try:
        return float(
            value
        )
    except (
        TypeError,
        ValueError,
    ):
        return default


def event_strength(
    event: dict[str, Any],
    global_intensity: float,
) -> float:
    raw = event.get(
        "intensity",
        None,
    )
    local = (
        0.68
        if raw is None
        or raw == ""
        else coerce_float(
            raw,
            0.68,
        )
    )

    return max(
        0.0,
        min(
            1.35,
            local
            * coerce_fx_intensity(
                global_intensity
            ),
        ),
    )


def event_times(
    event: dict[str, Any],
    recipe: ProfessionalFxRecipe,
    energy: str,
) -> tuple[float, float]:
    start = max(
        0.0,
        coerce_float(
            event.get(
                "start",
                0.0,
            ),
            0.0,
        ),
    )
    end = coerce_float(
        event.get(
            "end",
            0.0,
        ),
        0.0,
    )

    if end <= start:
        end = start + recipe_value(
            recipe.durations,
            energy,
        )

    return (
        start,
        max(
            start + 0.08,
            end,
        ),
    )


def smoothstep(
    phase: str,
) -> str:
    return (
        f"({phase})*({phase})*(3-2*({phase}))"
    )


def envelope_expression(
    start: float,
    end: float,
    peak_fraction: float = 0.34,
) -> str:
    span = max(
        0.08,
        end
        - start,
    )
    peak = min(
        end
        - 0.03,
        max(
            start
            + 0.03,
            start
            + span
            * peak_fraction,
        ),
    )
    attack_denominator = max(
        0.001,
        peak
        - start,
    )
    release_denominator = max(
        0.001,
        end
        - peak,
    )
    attack_phase = (
        f"((t-{start:.3f})/{attack_denominator:.3f})"
    )
    release_phase = (
        f"(({end:.3f}-t)/{release_denominator:.3f})"
    )

    return (
        f"if(between(t,{start:.3f},{peak:.3f}),"
        f"{smoothstep(attack_phase)},"
        f"if(between(t,{peak:.3f},{end:.3f}),"
        f"{smoothstep(release_phase)},0))"
    )


def plus_delta_expression(
    base: float,
    delta: float,
    envelope: str,
) -> str:
    if delta < 0:
        return (
            f"{base:.4f}{delta:.4f}*{envelope}"
        )

    return (
        f"{base:.4f}+{delta:.4f}*{envelope}"
    )


def recipe_filters(
    event: dict[str, Any],
    recipe_key: str,
    energy: str,
    global_intensity: float,
) -> list[str]:
    recipe = PROFESSIONAL_FX_RECIPES[
        normalize_fx_recipe(
            recipe_key
        )
    ]
    start, end = event_times(
        event,
        recipe,
        energy,
    )
    enable = enable_between(
        start,
        end,
    )
    strength = event_strength(
        event,
        global_intensity,
    )
    envelope = envelope_expression(
        start,
        end,
    )

    filters: list[str] = []

    if recipe.blur_radius:
        radius = (
            recipe_value(
                recipe.blur_radius,
                energy,
            )
            * strength
        )
        if radius > 0.01:
            filters.append(
                (
                    f"dblur=angle={recipe.blur_angle:.1f}:"
                    f"radius={radius:.3f}:"
                    "planes=1:"
                    f"enable='{enable}'"
                )
            )

    filters.append(
        (
            "eq="
            f"contrast='{plus_delta_expression(1.0, recipe.contrast_peak * strength, envelope)}':"
            f"saturation='{plus_delta_expression(1.0, recipe.saturation_peak * strength, envelope)}':"
            f"brightness='{recipe.brightness_peak * strength:.4f}*{envelope}':"
            "eval=frame"
        )
    )

    if recipe.glow_warmth:
        warmth = recipe.glow_warmth * strength
        filters.append(
            (
                f"colorbalance=rs={warmth:.4f}:"
                f"rm={warmth:.4f}:"
                f"rh={warmth * 0.5:.4f}:"
                f"bs={-warmth:.4f}:"
                f"bm={-warmth:.4f}:"
                f"bh={-warmth * 0.5:.4f}:"
                f"enable='{enable}'"
            )
        )

    sharpness = recipe.sharpness * strength
    if sharpness > 0.01:
        filters.append(
            (
                f"unsharp=5:5:{sharpness:.4f}:"
                "3:3:0.0000:"
                f"enable='{enable}'"
            )
        )

    return filters


def filters_for_event(
    event: dict[str, Any],
    index: int,
    intensity: float = 1.0,
    energy: str = DEFAULT_ENERGY,
) -> list[str]:

    start = float(
        event.get(
            "start",
            0.0,
        )
    )
    end = float(
        event.get(
            "end",
            start,
        )
    )
    enable = enable_between(
        start,
        end,
    )
    effect = normalize_fx_recipe(
        str(
            event.get(
                "effect",
                event.get(
                    "recipe",
                    "",
                ),
            )
        )
    )

    if event.get(
        "type"
    ) == "graphic":
        text = escape_drawtext(
            str(
                event.get(
                    "text",
                    event.get(
                        "trigger_word",
                        "",
                    ),
                )
            )
        )
        return [
            (
                "drawtext="
                f"{drawtext_font_option()}"
                f"text='{text}':"
                "x=(w-text_w)/2:"
                "y=h*0.28:"
                "fontsize=118:"
                "fontcolor=white:"
                "borderw=8:"
                "bordercolor=black@0.95:"
                "box=1:"
                "boxcolor=black@0.34:"
                "boxborderw=24:"
                f"enable='{enable}'"
            )
        ]

    return recipe_filters(
        event,
        effect,
        energy,
        intensity,
    )


def semantic_event_filters(
    events: list[dict[str, Any]],
    intensity: float = 1.0,
    energy: str = DEFAULT_ENERGY,
) -> list[str]:
    """Return only the momentary semantic visual-FX filters."""

    if coerce_fx_intensity(
        intensity
    ) <= 0.0:
        return []

    filters: list[str] = []

    for index, event in enumerate(
        events,
        start=1,
    ):
        filters.extend(
            filters_for_event(
                event,
                index,
                intensity,
                energy,
            )
        )

    return filters


def build_semantic_filter_chain(
    events: list[dict[str, Any]],
    intensity: float = 1.0,
    energy: str = DEFAULT_ENERGY,
) -> str:
    """Return the production semantic-FX chain without a baseline grade."""

    filters: list[str] = []

    if coerce_fx_intensity(
        intensity
    ) > 0.0:
        filters.extend(
            semantic_event_filters(
                events,
                intensity,
                energy,
            )
        )

    filters.append(
        "format=yuv420p"
    )

    return ",".join(
        filters
    )


def build_filter_chain(
    energy: str,
    events: list[dict[str, Any]],
    intensity: float = 1.0,
) -> str:

    filters = baseline_filters(
        energy,
        intensity,
    )

    filters.extend(
        semantic_event_filters(
            events,
            intensity,
            energy,
        )
    )

    filters.append(
        "format=yuv420p"
    )

    return ",".join(
        filters
    )


def write_plan(
    energy: str,
    events: list[dict[str, Any]],
    moments: list[dict[str, Any]],
    intensity_curve: list[dict[str, Any]],
) -> None:

    recipes: dict[str, int] = {}
    for moment in moments:
        recipe = str(
            moment.get(
                "recipe",
                "speech_emphasis",
            )
        )
        recipes[recipe] = recipes.get(
            recipe,
            0,
        ) + 1

    plan = {
        "version": 1,
        "edit_energy": normalize_energy(
            energy
        ),
        "source_video": str(
            VIDEO_PATH
        ),
        "base_look": energy_profile(
            energy
        ).get(
            "base_look",
            "viral_pop",
        ),
        "intensity_model": (
            "semantic_recipes_with_time_curve"
            if normalize_energy(
                energy
            )
            == "MAXIMUM"
            else "clean_single_accent_moments"
        ),
        "intensity_curve": intensity_curve,
        "semantic_recipe_counts": recipes,
        "hero_moments": [
            moment
            for moment in moments
            if moment.get(
                "hero",
                False,
            )
        ],
        "moment_count": len(
            moments
        ),
        "event_count": len(
            events
        ),
        "events": events,
    }

    VISUAL_FX_PLAN_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    VISUAL_FX_PLAN_PATH.write_text(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def apply_visual_fx(
    energy: str,
    events: list[dict[str, Any]],
    content_rect: tuple[int, int, int, int] = (0, 0, OUTPUT_WIDTH, OUTPUT_HEIGHT),
    intensity: float = 1.0,
) -> None:

    filter_chain = build_filter_chain(
        energy,
        events,
        intensity,
    )

    (
        content_x,
        content_y,
        content_width,
        content_height,
    ) = content_rect

    # render_base_video() (app/render.py) crops to fill the 1080x1920
    # canvas by default (content_rect is the full canvas, making the
    # crop/pad below a no-op), but this still supports a letterboxed
    # content_rect if one is ever passed in again. The filters above use
    # iw/ih/w/h -- frame-relative ffmpeg symbols with no hardcoded absolute
    # pixels -- so cropping to the real content rect before this chain runs
    # and padding back out afterward keeps them aligned to actual visible
    # video instead of any surrounding canvas.
    filter_chain = (
        f"crop={content_width}:{content_height}:"
        f"{content_x}:{content_y},"
        f"{filter_chain},"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
        f"{content_x}:{content_y}:black"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(
            VIDEO_PATH
        ),
        "-vf",
        filter_chain,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-shortest",
        str(
            TEMP_PATH
        ),
    ]

    print(
        "",
        flush=True,
    )
    print(
        "Applying standalone visual polish and dynamic FX...",
        flush=True,
    )

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )

    os.replace(
        TEMP_PATH,
        VIDEO_PATH,
    )


def main() -> int:

    print(
        "ShortsFactory visual FX pass starting...",
        flush=True,
    )

    if not VIDEO_PATH.exists():
        print(
            f"WARNING: Tight video does not exist: {VIDEO_PATH}",
            flush=True,
        )
        return 0

    settings = load_render_settings()
    energy = normalize_energy(
        settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )
    intensity = coerce_fx_intensity(
        settings.get(
            "fx_intensity",
            1.0,
        )
    )

    transcript = load_json(
        TRANSCRIPT_PATH
    )
    words = transcript.get(
        "words",
        [],
    )

    if not isinstance(
        words,
        list,
    ):
        words = []

    moments, intensity_curve = build_semantic_moments(
        words,
        energy,
    )
    events = expand_moments_to_events(
        moments,
        energy,
    )

    write_plan(
        energy,
        events,
        moments,
        intensity_curve,
    )

    print(
        f"Edit energy: {energy}",
        flush=True,
    )
    print(
        f"FX intensity: {intensity:.2f}",
        flush=True,
    )
    print(
        (
            "Base look: "
            + str(
                energy_profile(
                    energy
                ).get(
                    "base_look",
                    "viral_pop",
                )
            )
        ),
        flush=True,
    )
    print(
        f"Semantic moments selected: {len(moments)}",
        flush=True,
    )
    print(
        f"Dynamic FX events selected: {len(events)}",
        flush=True,
    )

    for index, event in enumerate(
        events,
        start=1,
    ):
        print(
            (
                f"FX {index}: "
                f"{event['start']:.2f}s -> "
                f"{event['end']:.2f}s, "
                f"{event['effect']}, "
                f"trigger={event.get('trigger_word', '')}, "
                f"recipe={event.get('recipe', '')}, "
                f"stack={event.get('stack_id', '')}"
            ),
            flush=True,
        )

    try:
        apply_visual_fx(
            energy,
            events,
            content_rect_from_settings(
                settings
            ),
            intensity,
        )
    except subprocess.CalledProcessError as exc:
        if TEMP_PATH.exists():
            try:
                TEMP_PATH.unlink()
            except OSError:
                pass

        print(
            (
                "WARNING: Visual FX FFmpeg pass failed "
                f"with exit code {exc.returncode}. "
                "Continuing without visual FX."
            ),
            flush=True,
        )
        return 0

    print(
        f"Visual FX plan: {VISUAL_FX_PLAN_PATH}",
        flush=True,
    )
    print(
        "Visual FX applied.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
