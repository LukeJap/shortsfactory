from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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


ROOT = Path(__file__).resolve().parent.parent

VIDEO_PATH = ROOT / "output" / "rendered" / "short1_tight.mp4"
TRANSCRIPT_PATH = ROOT / "output" / "subtitles.json"
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

    alpha = clean_alpha(
        raw_word
    )
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
    level = str(
        classification.get(
            "level",
            "NORMAL",
        )
    )

    if category in {
        "money",
        "number",
    }:
        return "green_money"

    if alpha in {
        "never",
        "nothing",
        "dead",
        "died",
        "dying",
        "sad",
        "failure",
    }:
        return "desat_hit"

    if alpha in {
        "weird",
        "strange",
        "confused",
        "what",
        "why",
        "crazy",
        "insane",
    }:
        return (
            "glitch_hit"
            if level in {
                "IMPACT",
                "EXTREME",
            }
            else "magenta_hype"
        )

    if accent == "warm":
        return "warm_gold"

    if accent == "danger":
        return "red_danger"

    if accent == "cold":
        return "cold_blue"

    if level in {
        "IMPACT",
        "EXTREME",
    }:
        return "contrast_flash"

    return "contrast_hit"


def event_duration(
    effect: str,
    level: str,
    energy: str,
) -> float:

    if effect == "contrast_flash":
        return 0.34 if energy != "LOW" else 0.24

    if effect == "overdrive_flash":
        return 0.18

    if effect == "rgb_split":
        return 0.22 if energy == "MAXIMUM" else 0.16

    if effect == "glitch_hit":
        return 0.30 if energy == "MAXIMUM" else 0.22

    if effect in {
        "posterize_hit",
        "bloom_flash",
        "spotlight",
        "detail_hit",
    }:
        return 0.62 if energy == "MAXIMUM" else 0.42

    if effect in {
        "desat_hit",
        "cold_blue",
        "green_money",
        "red_danger",
        "magenta_hype",
        "warm_gold",
    }:
        return 1.15 if energy == "MAXIMUM" else 0.86

    if level == "EXTREME":
        return 1.20

    if level == "IMPACT":
        return 0.75

    return 0.48


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
    max_moments = int(
        energy_profile(
            energy
        ).get(
            "max_filter_events",
            4,
        )
    )
    max_hero_moments = int(
        energy_profile(
            energy
        ).get(
            "max_hero_moments",
            1,
        )
    )
    spacing = {
        "LOW": 5.5,
        "PUNCHY": 1.85,
        "MAXIMUM": 0.78,
    }.get(
        energy,
        1.85,
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
        recipe = semantic_recipe(
            word,
            classification,
        )
        intensity = intensity_for_moment(
            start,
            duration,
            classification,
            recipe,
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
                    "wtf_chaos",
                    "reaction",
                    "money",
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

    if energy != "MAXIMUM":
        return effect_for_word(
            raw_word,
            classification,
        )

    return {
        "wtf_chaos": "rgb_split",
        "money": "green_money",
        "doom_negative": "desat_hit",
        "hype_win": "magenta_hype",
        "fail_awkward": "posterize_hit",
        "creepy_cold": "cold_blue",
        "nostalgia_memory": "warm_gold",
        "reaction": "contrast_flash",
        "speech_emphasis": "detail_hit",
    }.get(
        recipe,
        effect_for_word(
            raw_word,
            classification,
        ),
    )


def max_stack_effects_for_moment(
    moment: dict[str, Any],
) -> list[str]:

    recipe = str(
        moment.get(
            "recipe",
            "speech_emphasis",
        )
    )
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
    )

    effects = {
        "wtf_chaos": [
            "rgb_split",
            "contrast_flash",
        ],
        "money": [
            "green_money",
            "bloom_flash",
        ],
        "doom_negative": [
            "desat_hit",
            "spotlight",
        ],
        "hype_win": [
            "magenta_hype",
            "bloom_flash",
        ],
        "fail_awkward": [
            "desat_hit",
            "posterize_hit",
        ],
        "creepy_cold": [
            "cold_blue",
            "spotlight",
        ],
        "nostalgia_memory": [
            "warm_gold",
            "bloom_flash",
        ],
        "reaction": [
            "contrast_flash",
            "posterize_hit",
        ],
        "speech_emphasis": [
            "detail_hit",
        ],
    }.get(
        recipe,
        [
            "contrast_hit",
        ],
    )

    if hero and recipe in {
        "wtf_chaos",
        "reaction",
        "fail_awkward",
    }:
        effects.append(
            "rgb_split"
        )

    if hero and intensity >= 0.82:
        effects.append(
            "overdrive_flash"
        )

    return list(
        dict.fromkeys(
            effects
        )
    )


def moment_should_have_graphic(
    moment: dict[str, Any],
    energy: str,
) -> bool:

    recipe = str(
        moment.get(
            "recipe",
            "",
        )
    )
    level = str(
        moment.get(
            "level",
            "",
        )
    )

    if recipe in {
        "money",
        "wtf_chaos",
        "reaction",
    }:
        return energy == "MAXIMUM" or level in {
            "IMPACT",
            "EXTREME",
        }

    return bool(
        moment.get(
            "hero",
            False,
        )
        or level == "EXTREME"
    )


def event_from_moment(
    moment: dict[str, Any],
    effect: str,
    event_type: str,
    stack_id: str,
    start_offset: float = 0.0,
    duration: float | None = None,
) -> dict[str, Any]:

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
            "MAXIMUM",
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
        "recipe": moment.get(
            "recipe",
            "speech_emphasis",
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


def filters_for_event(
    event: dict[str, Any],
    index: int,
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
    effect = str(
        event.get(
            "effect",
            "",
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

    if effect == "desat_hit":
        return [
            f"hue=s=0.16:enable='{enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.10:t=fill:enable='{enable}'",
        ]

    if effect == "cold_blue":
        return [
            f"drawbox=x=0:y=0:w=iw:h=ih:color=cyan@0.11:t=fill:enable='{enable}'",
            f"eq=contrast=1.08:saturation=0.92:enable='{enable}'",
        ]

    if effect == "warm_gold":
        return [
            f"drawbox=x=0:y=0:w=iw:h=ih:color=orange@0.11:t=fill:enable='{enable}'",
            f"eq=contrast=1.10:saturation=1.12:enable='{enable}'",
        ]

    if effect == "green_money":
        return [
            f"drawbox=x=0:y=0:w=iw:h=ih:color=green@0.14:t=fill:enable='{enable}'",
            f"eq=contrast=1.16:saturation=1.18:enable='{enable}'",
        ]

    if effect == "red_danger":
        return [
            f"drawbox=x=0:y=0:w=iw:h=ih:color=red@0.15:t=fill:enable='{enable}'",
            f"eq=contrast=1.18:saturation=1.12:enable='{enable}'",
        ]

    if effect == "magenta_hype":
        return [
            f"drawbox=x=0:y=0:w=iw:h=ih:color=magenta@0.16:t=fill:enable='{enable}'",
            f"eq=contrast=1.22:saturation=1.34:brightness=0.010:enable='{enable}'",
        ]

    if effect == "rgb_split":
        stripe_enable = enable_between(
            start,
            min(
                end,
                start + 0.20,
            ),
        )
        return [
            f"drawbox=x=0:y=h*0.16:w=iw:h=11:color=red@0.28:t=fill:enable='{stripe_enable}'",
            f"drawbox=x=0:y=h*0.22:w=iw:h=8:color=cyan@0.24:t=fill:enable='{stripe_enable}'",
            f"drawbox=x=0:y=h*0.67:w=iw:h=12:color=magenta@0.26:t=fill:enable='{stripe_enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=cyan@0.055:t=fill:enable='{enable}'",
            f"eq=contrast=1.34:saturation=1.42:enable='{enable}'",
        ]

    if effect == "glitch_hit":
        stripe_enable = enable_between(
            start,
            min(
                end,
                start + 0.22,
            ),
        )
        return [
            f"drawbox=x=0:y=h*0.18:w=iw:h=18:color=cyan@0.28:t=fill:enable='{stripe_enable}'",
            f"drawbox=x=0:y=h*0.62:w=iw:h=14:color=magenta@0.26:t=fill:enable='{stripe_enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=magenta@0.10:t=fill:enable='{enable}'",
            f"eq=contrast=1.22:saturation=1.28:enable='{enable}'",
        ]

    if effect == "posterize_hit":
        return [
            f"eq=contrast=1.48:saturation=1.34:gamma=0.86:enable='{enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.08:t=fill:enable='{enable}'",
            f"unsharp=5:5:0.72:3:3:0.18:enable='{enable}'",
        ]

    if effect == "bloom_flash":
        bloom_end = min(
            end,
            start + 0.22,
        )
        return [
            f"eq=contrast=1.10:saturation=1.36:brightness=0.028:enable='{enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.12:t=fill:enable='{enable_between(start, bloom_end)}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=yellow@0.045:t=fill:enable='{enable}'",
        ]

    if effect == "spotlight":
        return [
            f"vignette=PI/3.2:enable='{enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.065:t=fill:enable='{enable}'",
        ]

    if effect == "detail_hit":
        return [
            f"eq=contrast=1.18:saturation=1.16:brightness=0.008:enable='{enable}'",
            f"unsharp=5:5:0.82:3:3:0.20:enable='{enable}'",
        ]

    if effect == "overdrive_flash":
        flash_end = min(
            end,
            start + 0.14,
        )
        return [
            f"eq=contrast=1.42:saturation=1.36:brightness=0.045:enable='{enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.18:t=fill:enable='{enable_between(start, flash_end)}'",
        ]

    if effect == "contrast_flash":
        flash_end = min(
            end,
            start + 0.11,
        )
        return [
            f"eq=contrast=1.24:saturation=1.22:brightness=0.025:enable='{enable}'",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.20:t=fill:enable='{enable_between(start, flash_end)}'",
        ]

    return [
        f"eq=contrast=1.16:saturation=1.15:brightness=0.012:enable='{enable}'",
    ]


def build_filter_chain(
    energy: str,
    events: list[dict[str, Any]],
    intensity: float = 1.0,
) -> str:

    filters = baseline_filters(
        energy,
        intensity,
    )

    for index, event in enumerate(
        events,
        start=1,
    ):
        filters.extend(
            filters_for_event(
                event,
                index,
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
    # content_rect if one is ever passed in again. Every filter above
    # (vignette, color grade washes, RGB-split stripes, drawtext slam-text
    # positioning) is computed using iw/ih/w/h -- frame-relative ffmpeg
    # symbols with no hardcoded absolute pixels -- so cropping to the real
    # content rect before this chain runs and padding back out afterward
    # would make every one of those effects operate on the actual visible
    # video instead of the full canvas even in that letterboxed case (e.g.
    # a vignette's falloff calibrated to the full 1920px-tall canvas would
    # otherwise be barely visible within a much smaller letterboxed
    # content area). No changes needed to any individual filter string.
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
        "Applying baseline visual grade and dynamic FX...",
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
