from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent

RENDER_SETTINGS_PATH = ROOT / "output" / "render_settings.json"
VISUAL_EDIT_PLAN_PATH = ROOT / "output" / "visual_edit_plan.json"
SMART_MOTION_PLAN_PATH = ROOT / "output" / "smart_motion_plan.json"
AI_VISUAL_PLAN_PATH = ROOT / "output" / "ai_visual_plan.json"
AI_VISUAL_MAPPED_PLAN_PATH = ROOT / "output" / "ai_visual_mapped_plan.json"
VISUAL_FX_PLAN_PATH = ROOT / "output" / "visual_fx_plan.json"
TEMPORAL_EDIT_PLAN_PATH = ROOT / "output" / "temporal_edit_plan.json"

ENERGY_LEVELS = {
    "LOW",
    "PUNCHY",
    "MAXIMUM",
}

DEFAULT_ENERGY = "PUNCHY"

SFX_MODES = {
    "AUTO",
    "OFF",
}

DEFAULT_SFX_MODE = "AUTO"

ENERGY_PROFILES: dict[str, dict[str, Any]] = {
    "LOW": {
        "caption_emphasis_threshold": 6.5,
        "caption_impact_threshold": 9.0,
        "caption_extreme_threshold": 999.0,
        "max_motion_events": 2,
        "motion_spacing": 7.0,
        "motion_duration": 1.0,
        "motion_zoom_levels": [1.045, 1.06, 1.04],
        "emoji_max_events": 1,
        "max_filter_events": 1,
        "auto_cut_min_gap": 1.35,
        "auto_cut_keep_gap": 0.55,
        "auto_cut_min_spacing": 2.00,
        "auto_cut_max_removal_ratio": 0.15,
        "semantic_min_duration": 0.60,
        "semantic_min_words": 2,
        "semantic_min_confidence": 0.93,
        "semantic_verify_confidence": 0.93,
        "base_look": "polished",
    },
    "PUNCHY": {
        "caption_emphasis_threshold": 3.0,
        "caption_impact_threshold": 7.0,
        "caption_extreme_threshold": 11.5,
        "max_motion_events": 4,
        "motion_spacing": 4.75,
        "motion_duration": 1.2,
        "motion_zoom_levels": [1.10, 1.14, 1.09, 1.12],
        "emoji_max_events": 3,
        "max_filter_events": 4,
        "auto_cut_min_gap": 1.15,
        "auto_cut_keep_gap": 0.42,
        "auto_cut_min_spacing": 1.50,
        "auto_cut_max_removal_ratio": 0.22,
        "semantic_min_duration": 0.45,
        "semantic_min_words": 2,
        "semantic_min_confidence": 0.88,
        "semantic_verify_confidence": 0.90,
        "base_look": "viral_pop",
    },
    "MAXIMUM": {
        "caption_emphasis_threshold": 2.5,
        "caption_impact_threshold": 6.0,
        "caption_extreme_threshold": 8.5,
        "max_motion_events": 8,
        "motion_spacing": 2.35,
        "motion_duration": 1.35,
        "motion_zoom_levels": [1.12, 1.16, 1.10, 1.14, 1.18, 1.20],
        "emoji_max_events": 4,
        "max_filter_events": 12,
        "max_hero_moments": 3,
        "auto_cut_min_gap": 0.90,
        "auto_cut_keep_gap": 0.30,
        "auto_cut_min_spacing": 0.90,
        "auto_cut_max_removal_ratio": 0.35,
        "semantic_min_duration": 0.30,
        "semantic_min_words": 1,
        "semantic_min_confidence": 0.84,
        "semantic_verify_confidence": 0.86,
        "base_look": "maximum_overdrive",
    },
}

NEGATION_WORDS = {
    "never",
    "no",
    "nobody",
    "none",
    "nothing",
    "without",
}

IMPACT_WORDS = {
    "always",
    "best",
    "crazy",
    "dead",
    "died",
    "dying",
    "exactly",
    "huge",
    "insane",
    "million",
    "millions",
    "omg",
    "really",
    "worst",
}

EMPHASIS_WORDS = {
    "actually",
    "afraid",
    "angry",
    "awkward",
    "bad",
    "big",
    "but",
    "confused",
    "danger",
    "dangerous",
    "different",
    "embarrassed",
    "fake",
    "fast",
    "finally",
    "first",
    "funny",
    "good",
    "how",
    "happy",
    "important",
    "literally",
    "love",
    "money",
    "okay",
    "old",
    "only",
    "ow",
    "problem",
    "rich",
    "right",
    "sad",
    "scared",
    "secret",
    "strange",
    "surprise",
    "surprising",
    "true",
    "wait",
    "what",
    "when",
    "where",
    "who",
    "weird",
    "why",
    "wild",
    "wow",
    "wrong",
}

MONEY_WORDS = {
    "dollar",
    "dollars",
    "billion",
    "billions",
    "million",
    "millions",
    "thousand",
    "thousands",
}

COLD_WORDS = {
    "afraid",
    "cold",
    "dark",
    "dead",
    "died",
    "dying",
    "sad",
    "scared",
    "strange",
    "weird",
}

WARM_WORDS = {
    "best",
    "excited",
    "finally",
    "funny",
    "good",
    "happy",
    "love",
    "right",
    "true",
    "yes",
}

NEGATIVE_WORDS = NEGATION_WORDS | {
    "bad",
    "danger",
    "dangerous",
    "problem",
    "trouble",
    "wrong",
    "worst",
}

CHAOS_WORDS = {
    "chaos",
    "confused",
    "crazy",
    "insane",
    "omg",
    "strange",
    "surprise",
    "what",
    "why",
    "wild",
    "wow",
}

FAIL_WORDS = {
    "awkward",
    "bad",
    "embarrassed",
    "failure",
    "problem",
    "wrong",
    "worst",
}

CREEPY_WORDS = {
    "afraid",
    "cold",
    "creepy",
    "dark",
    "eerie",
    "scared",
    "strange",
    "weird",
}

HYPE_WORDS = {
    "always",
    "best",
    "excited",
    "finally",
    "good",
    "happy",
    "huge",
    "love",
    "really",
    "right",
    "true",
    "wild",
    "wow",
}

NOSTALGIA_WORDS = {
    "memory",
    "nostalgia",
    "old",
    "remember",
    "retro",
    "years",
}

REACTION_WORDS = {
    "no",
    "oh",
    "ow",
    "really",
    "wait",
    "what",
    "who",
    "wow",
    "yes",
}

NUMBER_RE = re.compile(
    r"^\$?\d[\d,]*(?:\.\d+)?(?:%|k|m|b)?$",
    re.IGNORECASE,
)

ORDINAL_RE = re.compile(
    r"^\d+(?:st|nd|rd|th)$",
    re.IGNORECASE,
)

YEAR_RE = re.compile(
    r"^(?:18|19|20)\d{2}$"
)


def normalize_energy(
    value: Any,
) -> str:

    energy = str(
        value
        or DEFAULT_ENERGY
    ).upper()

    if energy not in ENERGY_LEVELS:
        return DEFAULT_ENERGY

    return energy


def normalize_sfx_mode(
    value: Any,
) -> str:

    mode = str(
        value
        or DEFAULT_SFX_MODE
    ).upper()

    if mode not in SFX_MODES:
        return DEFAULT_SFX_MODE

    return mode


def energy_profile(
    energy: Any,
) -> dict[str, Any]:

    return ENERGY_PROFILES[
        normalize_energy(
            energy
        )
    ]


def read_json(
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


def load_render_settings() -> dict[str, Any]:

    settings = read_json(
        RENDER_SETTINGS_PATH
    )

    settings["edit_energy"] = normalize_energy(
        settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )

    settings["sfx_mode"] = normalize_sfx_mode(
        settings.get(
            "sfx_mode",
            DEFAULT_SFX_MODE,
        )
    )

    return settings


def write_render_settings(
    settings: dict[str, Any],
) -> None:

    payload = dict(
        settings
    )

    payload["edit_energy"] = normalize_energy(
        payload.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )

    payload["sfx_mode"] = normalize_sfx_mode(
        payload.get(
            "sfx_mode",
            DEFAULT_SFX_MODE,
        )
    )

    RENDER_SETTINGS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    RENDER_SETTINGS_PATH.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def clean_word(
    value: str,
) -> str:

    return re.sub(
        r"[^a-zA-Z0-9'$%.,]",
        "",
        value,
    ).lower()


def word_text(
    word: dict[str, Any] | str,
) -> str:

    if isinstance(
        word,
        dict,
    ):
        return str(
            word.get(
                "word",
                "",
            )
            or ""
        ).strip()

    return str(
        word
        or ""
    ).strip()


def numberish(
    cleaned: str,
) -> bool:

    value = cleaned.replace(
        ",",
        "",
    )

    return bool(
        NUMBER_RE.fullmatch(
            value
        )
        or ORDINAL_RE.fullmatch(
            value
        )
        or YEAR_RE.fullmatch(
            value
        )
    )


def semantic_accent(
    cleaned_alpha: str,
    category: str,
) -> str:

    if category in {
        "money",
        "number",
    }:
        return "green"

    if cleaned_alpha in COLD_WORDS:
        return "cold"

    if cleaned_alpha in WARM_WORDS:
        return "warm"

    if cleaned_alpha in CHAOS_WORDS:
        return "magenta"

    if cleaned_alpha in NEGATIVE_WORDS:
        return "danger"

    return "bone"


def semantic_recipe(
    word: dict[str, Any] | str,
    classification: dict[str, Any] | None = None,
) -> str:

    raw = word_text(
        word
    )
    cleaned = clean_word(
        raw
    )
    cleaned_alpha = re.sub(
        r"[^a-zA-Z']",
        "",
        cleaned,
    )

    if classification is None:
        classification = classify_word(
            word
        )

    category = str(
        classification.get(
            "category",
            "speech",
        )
    )

    if category in {
        "money",
        "number",
    }:
        return "money"

    if cleaned_alpha in CHAOS_WORDS:
        return "wtf_chaos"

    if cleaned_alpha in FAIL_WORDS:
        return "fail_awkward"

    if cleaned_alpha in CREEPY_WORDS:
        return "creepy_cold"

    if cleaned_alpha in NEGATIVE_WORDS:
        return "doom_negative"

    if cleaned_alpha in HYPE_WORDS:
        return "hype_win"

    if cleaned_alpha in NOSTALGIA_WORDS:
        return "nostalgia_memory"

    if cleaned_alpha in REACTION_WORDS:
        return "reaction"

    return "speech_emphasis"


def recipe_base_intensity(
    recipe: str,
) -> float:

    return {
        "wtf_chaos": 0.98,
        "money": 0.88,
        "doom_negative": 0.76,
        "hype_win": 0.82,
        "fail_awkward": 0.78,
        "creepy_cold": 0.70,
        "nostalgia_memory": 0.62,
        "reaction": 0.86,
        "speech_emphasis": 0.58,
    }.get(
        recipe,
        0.55,
    )


def word_time(
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


def structural_intensity(
    time_seconds: float,
    duration: float,
) -> tuple[str, float]:

    if duration <= 0:
        return (
            "unknown",
            0.65,
        )

    progress = max(
        0.0,
        min(
            1.0,
            time_seconds
            / duration,
        ),
    )

    if progress < 0.13:
        return (
            "hook",
            0.94,
        )

    if progress < 0.38:
        return (
            "setup",
            0.62,
        )

    if progress < 0.68:
        return (
            "build",
            0.78,
        )

    if progress < 0.86:
        return (
            "payoff",
            0.96,
        )

    return (
        "ending",
        0.86,
    )


def intensity_for_moment(
    start: float,
    duration: float,
    classification: dict[str, Any],
    recipe: str,
    energy: Any = DEFAULT_ENERGY,
) -> dict[str, Any]:

    energy = normalize_energy(
        energy
    )
    region, region_intensity = structural_intensity(
        start,
        duration,
    )
    score = float(
        classification.get(
            "score",
            0.0,
        )
        or 0.0
    )
    score_intensity = min(
        1.0,
        score
        / 10.0,
    )
    recipe_intensity = recipe_base_intensity(
        recipe
    )

    blended = (
        region_intensity
        * 0.38
        + recipe_intensity
        * 0.34
        + score_intensity
        * 0.28
    )

    if energy == "LOW":
        blended *= 0.62
    elif energy == "PUNCHY":
        blended *= 0.82
    else:
        blended = min(
            1.0,
            blended
            + 0.08,
        )

    return {
        "region": region,
        "intensity": round(
            max(
                0.0,
                min(
                    1.0,
                    blended,
                ),
            ),
            3,
        ),
        "region_intensity": round(
            region_intensity,
            3,
        ),
        "recipe_intensity": round(
            recipe_intensity,
            3,
        ),
    }


def build_intensity_curve(
    duration: float,
    moments: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    if duration <= 0:
        return []

    raw_segments = [
        (
            "hook",
            0.0,
            min(
                duration,
                duration
                * 0.13,
            ),
            0.94,
        ),
        (
            "setup",
            duration
            * 0.13,
            duration
            * 0.38,
            0.62,
        ),
        (
            "build",
            duration
            * 0.38,
            duration
            * 0.68,
            0.78,
        ),
        (
            "payoff",
            duration
            * 0.68,
            duration
            * 0.86,
            0.96,
        ),
        (
            "ending",
            duration
            * 0.86,
            duration,
            0.86,
        ),
    ]

    curve = []
    for label, start, end, base in raw_segments:
        if end <= start:
            continue

        local_moments = [
            moment
            for moment in moments
            if start
            <= float(
                moment.get(
                    "start",
                    0.0,
                )
            )
            < end
        ]
        bump = 0.0
        if local_moments:
            bump = min(
                0.12,
                0.03
                * len(
                    local_moments
                ),
            )

        curve.append(
            {
                "region": label,
                "start": round(
                    start,
                    3,
                ),
                "end": round(
                    end,
                    3,
                ),
                "intensity": round(
                    min(
                        1.0,
                        base
                        + bump,
                    ),
                    3,
                ),
                "moment_count": len(
                    local_moments
                ),
            }
        )

    return curve


def classify_word(
    word: dict[str, Any] | str,
    energy: Any = DEFAULT_ENERGY,
) -> dict[str, Any]:

    raw = word_text(
        word
    )

    cleaned = clean_word(
        raw
    )

    cleaned_alpha = re.sub(
        r"[^a-zA-Z']",
        "",
        cleaned,
    )

    score = 0.0
    category = "speech"
    reasons: list[str] = []

    if not cleaned:
        return {
            "level": "NORMAL",
            "score": 0.0,
            "category": category,
            "accent": "none",
            "reason": "",
        }

    if numberish(
        cleaned
    ):
        score += 7.5
        category = "number"
        reasons.append(
            "number"
        )

    if cleaned.startswith(
        "$"
    ) or cleaned_alpha in MONEY_WORDS:
        score += 7.0
        category = "money"
        reasons.append(
            "money"
        )

    if cleaned_alpha in NEGATION_WORDS:
        score += 7.0
        category = "negation"
        reasons.append(
            "negation"
        )

    if cleaned_alpha in IMPACT_WORDS:
        score += 5.0
        reasons.append(
            "impact_word"
        )

    if cleaned_alpha in EMPHASIS_WORDS:
        score += 3.0
        reasons.append(
            "emphasis_word"
        )

    if raw.endswith(
        "!"
    ):
        score += 3.0
        reasons.append(
            "exclamation"
        )

    if raw.endswith(
        "?"
    ):
        score += 2.0
        reasons.append(
            "question"
        )

    if (
        len(
            cleaned_alpha
        )
        >= 8
        and cleaned_alpha
        not in EMPHASIS_WORDS
        and cleaned_alpha
        not in IMPACT_WORDS
    ):
        score += 1.0
        reasons.append(
            "long_word"
        )

    profile = energy_profile(
        energy
    )

    if score >= float(
        profile[
            "caption_extreme_threshold"
        ]
    ):
        level = "EXTREME"
    elif score >= float(
        profile[
            "caption_impact_threshold"
        ]
    ):
        level = "IMPACT"
    elif score >= float(
        profile[
            "caption_emphasis_threshold"
        ]
    ):
        level = "EMPHASIS"
    else:
        level = "NORMAL"

    return {
        "level": level,
        "score": round(
            score,
            3,
        ),
        "category": category,
        "accent": semantic_accent(
            cleaned_alpha,
            category,
        )
        if level != "NORMAL"
        else "none",
        "reason": "+".join(
            reasons
        ),
    }


def priority_for_event_type(
    event_type: str,
) -> int:

    return {
        "scene_cut": 100,
        "ai_visual": 90,
        "temporal": 82,
        "camera": 70,
        "graphic": 60,
        "caption_emphasis": 45,
        "filter": 35,
        "emoji": 25,
    }.get(
        event_type,
        10,
    )


def event_start(
    event: dict[str, Any],
) -> float:

    try:
        return float(
            event.get(
                "start",
                0.0,
            )
            or 0.0
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def mark_collisions(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    stack_counts: dict[str, int] = {}
    for event in events:
        stack_id = str(
            event.get(
                "stack_id",
                "",
            )
            or ""
        )

        if stack_id:
            stack_counts[stack_id] = stack_counts.get(
                stack_id,
                0,
            ) + 1

    for event in events:
        event["priority"] = priority_for_event_type(
            str(
                event.get(
                    "type",
                    "",
                )
            )
        )
        stack_id = str(
            event.get(
                "stack_id",
                "",
            )
            or ""
        )
        if stack_id and stack_counts.get(
            stack_id,
            0,
        ) > 1:
            event["stack_size"] = stack_counts[
                stack_id
            ]
            event["collision_note"] = (
                "coordinated_semantic_stack"
            )

    for index, event in enumerate(
        events
    ):
        start = event_start(
            event
        )
        priority = int(
            event.get(
                "priority",
                0,
            )
        )

        for other_index, other in enumerate(
            events
        ):
            if index == other_index:
                continue

            if (
                event.get(
                    "stack_id",
                    "",
                )
                and event.get(
                    "stack_id",
                    "",
                )
                == other.get(
                    "stack_id",
                    "",
                )
            ):
                continue

            if abs(
                start
                - event_start(
                    other
                )
            ) > 0.35:
                continue

            other_priority = int(
                other.get(
                    "priority",
                    0,
                )
            )

            if other_priority <= priority:
                continue

            if event.get(
                "collision_note",
                "",
            ) == "coordinated_semantic_stack":
                continue

            if event.get(
                "type"
            ) == "caption_emphasis":
                event["collision_note"] = (
                    "coexists_with_higher_priority_event"
                )
            else:
                event["collision_note"] = (
                    "lower_priority_near_"
                    + str(
                        other.get(
                            "type",
                            "event",
                        )
                    )
                )
            break

    return events


def load_temporal_events() -> list[dict[str, Any]]:

    data = read_json(
        TEMPORAL_EDIT_PLAN_PATH
    )

    raw_events = data.get(
        "events",
        [],
    )

    if not isinstance(
        raw_events,
        list,
    ):
        return []

    events = []

    for event in raw_events:
        if not isinstance(
            event,
            dict,
        ):
            continue

        start = event.get(
            "output_start",
            event.get(
                "start",
                0.0,
            ),
        )
        end = event.get(
            "output_end",
            event.get(
                "end",
                start,
            ),
        )

        events.append(
            {
                "type": "temporal",
                "start": start,
                "end": end,
                "treatment": event.get(
                    "type",
                    "temporal",
                ),
                "trigger": event.get(
                    "trigger_word",
                    "",
                ),
                "reason": event.get(
                    "reason",
                    "",
                ),
                "recipe": event.get(
                    "recipe",
                    "",
                ),
                "intensity": event.get(
                    "intensity",
                    0.0,
                ),
                "speed": event.get(
                    "speed",
                    0.0,
                ),
                "duration_before": event.get(
                    "duration_before",
                    0.0,
                ),
                "duration_after": event.get(
                    "duration_after",
                    0.0,
                ),
                "source_start": event.get(
                    "source_start",
                    event.get(
                        "anchor",
                        0.0,
                    ),
                ),
                "source_end": event.get(
                    "source_end",
                    event.get(
                        "anchor",
                        0.0,
                    ),
                ),
                "audio_behavior": event.get(
                    "audio_behavior",
                    "",
                ),
                "dialogue_protection": event.get(
                    "dialogue_protection",
                    "",
                ),
                "temporal_event_id": event.get(
                    "id",
                    "",
                ),
            }
        )

    return events


def load_motion_events() -> list[dict[str, Any]]:

    data = read_json(
        SMART_MOTION_PLAN_PATH
    )

    events = data.get(
        "events",
        [],
    )

    if not isinstance(
        events,
        list,
    ):
        return []

    return [
        {
            "type": "camera",
            "start": event.get(
                "start",
                0.0,
            ),
            "end": event.get(
                "end",
                event.get(
                    "start",
                    0.0,
                ),
            ),
            "treatment": "smart_motion",
            "trigger": event.get(
                "trigger_word",
                "",
            ),
            "zoom": event.get(
                "zoom",
                1.0,
            ),
            "reason": event.get(
                "reason",
                "",
            ),
        }
        for event in events
    ]


def load_ai_visual_events() -> list[dict[str, Any]]:

    mapped = read_json(
        AI_VISUAL_MAPPED_PLAN_PATH
    )
    mapped_assets = mapped.get(
        "assets",
        [],
    )

    if isinstance(
        mapped_assets,
        list,
    ) and mapped_assets:
        events = []

        for asset in mapped_assets:
            if not isinstance(
                asset,
                dict,
            ):
                continue

            events.append(
                {
                    "type": "ai_visual",
                    "start": asset.get(
                        "start",
                        0.0,
                    ),
                    "end": asset.get(
                        "end",
                        asset.get(
                            "start",
                            0.0,
                        ),
                    ),
                    "treatment": "mapped_cutaway",
                    "trigger": asset.get(
                        "label",
                        "",
                    ),
                    "reason": (
                        "final_time_after_cut_and_temporal_mapping"
                    ),
                    "asset_path": asset.get(
                        "path",
                        "",
                    ),
                }
            )

        return events

    data = read_json(
        AI_VISUAL_PLAN_PATH
    )

    slots = data.get(
        "slots",
        [],
    )

    if not isinstance(
        slots,
        list,
    ):
        return []

    events = []

    for slot in slots:
        try:
            start = float(
                slot.get(
                    "start",
                    slot.get(
                        "start_seconds",
                        0.0,
                    ),
                )
                or 0.0
            )
            end = float(
                slot.get(
                    "end",
                    slot.get(
                        "end_seconds",
                        start,
                    ),
                )
                or start
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        events.append(
            {
                "type": "ai_visual",
                "start": start,
                "end": end,
                "treatment": str(
                    slot.get(
                        "visual_type",
                        "cutaway",
                    )
                ),
                "trigger": str(
                    slot.get(
                        "label",
                        slot.get(
                            "title",
                            "",
                        ),
                    )
                ),
                "reason": str(
                    slot.get(
                        "reason",
                        "",
                    )
                ),
            }
        )

    return events


def load_visual_fx_events() -> list[dict[str, Any]]:

    data = read_json(
        VISUAL_FX_PLAN_PATH
    )

    raw_events = data.get(
        "events",
        [],
    )

    if not isinstance(
        raw_events,
        list,
    ):
        return []

    events = []

    for event in raw_events:
        if not isinstance(
            event,
            dict,
        ):
            continue

        event_type = str(
            event.get(
                "type",
                "filter",
            )
        )

        if event_type not in {
            "filter",
            "graphic",
        }:
            event_type = "filter"

        events.append(
            {
                "type": event_type,
                "start": event.get(
                    "start",
                    0.0,
                ),
                "end": event.get(
                    "end",
                    event.get(
                        "start",
                        0.0,
                    ),
                ),
                "treatment": event.get(
                    "effect",
                    event.get(
                        "treatment",
                        "",
                    ),
                ),
                "trigger": event.get(
                    "trigger_word",
                    event.get(
                        "trigger",
                        "",
                    ),
                ),
                "reason": event.get(
                    "reason",
                    "",
                ),
                "recipe": event.get(
                    "recipe",
                    "",
                ),
                "region": event.get(
                    "region",
                    "",
                ),
                "intensity": event.get(
                    "intensity",
                    0.0,
                ),
                "stack_id": event.get(
                    "stack_id",
                    "",
                ),
                "hero": event.get(
                    "hero",
                    False,
                ),
                "coordinated_stack": event.get(
                    "coordinated_stack",
                    False,
                ),
                "text": event.get(
                    "text",
                    "",
                ),
            }
        )

    return events


def build_visual_edit_plan(
    settings: dict[str, Any],
    caption_events: list[dict[str, Any]],
    emoji_events: list[dict[str, Any]],
) -> dict[str, Any]:

    energy = normalize_energy(
        settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )

    events: list[dict[str, Any]] = []
    events.extend(
        caption_events
    )
    events.extend(
        {
            "type": "emoji",
            "start": event.get(
                "start",
                0.0,
            ),
            "end": event.get(
                "end",
                event.get(
                    "start",
                    0.0,
                ),
            ),
            "treatment": event.get(
                "emoji",
                "",
            ),
            "trigger": event.get(
                "matched_word",
                "",
            ),
            "asset_path": event.get(
                "asset_path",
                "",
            ),
        }
        for event in emoji_events
    )
    events.extend(
        load_motion_events()
    )
    events.extend(
        load_temporal_events()
    )
    events.extend(
        load_ai_visual_events()
    )
    events.extend(
        load_visual_fx_events()
    )

    visual_fx_plan = read_json(
        VISUAL_FX_PLAN_PATH
    )
    temporal_plan = read_json(
        TEMPORAL_EDIT_PLAN_PATH
    )
    intensity_curve = visual_fx_plan.get(
        "intensity_curve",
        [],
    )
    hero_moments = visual_fx_plan.get(
        "hero_moments",
        [],
    )
    semantic_recipe_counts = visual_fx_plan.get(
        "semantic_recipe_counts",
        {},
    )

    events.sort(
        key=lambda event: (
            event_start(
                event
            ),
            -priority_for_event_type(
                str(
                    event.get(
                        "type",
                        "",
                    )
                )
            ),
        )
    )

    events = mark_collisions(
        events
    )

    return {
        "version": 1,
        "edit_energy": energy,
        "settings": {
            "edit_energy": energy,
        },
        "intensity_model": visual_fx_plan.get(
            "intensity_model",
            "",
        ),
        "intensity_curve": (
            intensity_curve
            if isinstance(
                intensity_curve,
                list,
            )
            else []
        ),
        "semantic_recipe_counts": (
            semantic_recipe_counts
            if isinstance(
                semantic_recipe_counts,
                dict,
            )
            else {}
        ),
        "hero_moments": (
            hero_moments
            if isinstance(
                hero_moments,
                list,
            )
            else []
        ),
        "temporal_edit": {
            "applied": bool(
                temporal_plan.get(
                    "applied",
                    False,
                )
            ),
            "source_duration_seconds": temporal_plan.get(
                "source_duration_seconds",
                0.0,
            ),
            "estimated_final_duration_seconds": temporal_plan.get(
                "estimated_final_duration_seconds",
                0.0,
            ),
            "duration_delta_seconds": temporal_plan.get(
                "duration_delta_seconds",
                0.0,
            ),
            "event_count": temporal_plan.get(
                "event_count",
                0,
            ),
            "time_mapping": temporal_plan.get(
                "time_mapping",
                [],
            ),
        },
        "event_count": len(
            events
        ),
        "events": events,
    }


def write_visual_edit_plan(
    plan: dict[str, Any],
) -> None:

    VISUAL_EDIT_PLAN_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    VISUAL_EDIT_PLAN_PATH.write_text(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
