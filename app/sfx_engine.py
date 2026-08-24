from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import argparse
from pathlib import Path
from typing import Any

try:
    from .editor_asset_plan import (
        clips_of_kind,
        editor_plan_context_matches,
        load_editor_asset_plan,
        save_editor_asset_plan,
        set_editor_plan_context,
    )
    from .apply_ai_visuals import (
        keep_segments,
        map_source_interval_to_tight,
        map_tight_interval_to_final,
    )
    from .visual_emphasis import (
        DEFAULT_ENERGY,
        load_render_settings,
        normalize_energy,
        normalize_sfx_mode,
    )
except ImportError:
    from editor_asset_plan import (
        clips_of_kind,
        editor_plan_context_matches,
        load_editor_asset_plan,
        save_editor_asset_plan,
        set_editor_plan_context,
    )
    from apply_ai_visuals import (
        keep_segments,
        map_source_interval_to_tight,
        map_tight_interval_to_final,
    )
    from visual_emphasis import (
        DEFAULT_ENERGY,
        load_render_settings,
        normalize_energy,
        normalize_sfx_mode,
    )

try:
    from .pipeline_paths import (
        COMBINED_EDIT_PLAN_PATH,
        EDITOR_ASSET_PLAN_PATH,
        SFX_PLAN_PATH,
        SUBTITLES_PATH,
        TEMPORAL_EDIT_PLAN_PATH,
        VISUAL_EDIT_PLAN_PATH,
    )
except ImportError:
    from pipeline_paths import (
        COMBINED_EDIT_PLAN_PATH,
        EDITOR_ASSET_PLAN_PATH,
        SFX_PLAN_PATH,
        SUBTITLES_PATH,
        TEMPORAL_EDIT_PLAN_PATH,
        VISUAL_EDIT_PLAN_PATH,
    )


ROOT = Path(__file__).resolve().parent.parent

OUTPUT_DIR = ROOT / "output"
RENDERED_DIR = OUTPUT_DIR / "rendered"
VIDEO_PATH = RENDERED_DIR / "short1_captioned.mp4"
TEMP_VIDEO_PATH = RENDERED_DIR / "short1_sfx_tmp.mp4"

SFX_DIR = ROOT / "assets" / "sfx"
GENERATED_SFX_DIR = SFX_DIR / "generated"

SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".ogg",
    ".m4a",
    ".aac",
    ".flac",
}

ENERGY_LIMITS = {
    "LOW": {
        "max_events": 2,
        "min_spacing": 0.65,
        "coverage_window": 15.0,
    },
    "PUNCHY": {
        "max_events": 6,
        "min_spacing": 0.42,
        "coverage_window": 8.0,
    },
    "MAXIMUM": {
        "max_events": 12,
        "min_spacing": 0.24,
        "coverage_window": 5.0,
    },
}

CATEGORY_DURATIONS = {
    "impact": 0.28,
    "bass": 0.42,
    "whoosh": 0.38,
    "transition": 0.34,
    "pop": 0.16,
    "ding": 0.20,
    "bell": 0.32,
    "celebration": 0.55,
    "reaction": 0.45,
    "alert": 0.55,
    "glitch": 0.20,
    "rewind": 0.26,
    "replay": 0.24,
    "beep": 0.18,
    "money": 0.24,
    "sparkle": 0.32,
    "doom": 0.46,
}

# Local sound files should play for their useful audible length instead of
# being chopped to the tiny planning-marker duration. Very long ambience or
# siren/reaction files are capped so one automatic accent cannot cover a huge
# section of the Short.
CATEGORY_MAX_PLAY_SECONDS = {
    "impact": 2.2,
    "bass": 2.8,
    "whoosh": 2.5,
    "transition": 2.5,
    "pop": 1.8,
    "ding": 2.5,
    "bell": 3.5,
    "celebration": 5.5,
    "reaction": 4.5,
    "alert": 4.5,
    "glitch": 2.2,
    "rewind": 3.0,
    "replay": 3.0,
    "beep": 2.0,
    "money": 3.5,
    "sparkle": 3.5,
    "doom": 4.0,
}


CATEGORY_LABELS = {
    "impact": "IMPACT",
    "bass": "BASS HIT",
    "whoosh": "WHOOSH",
    "transition": "TRANSITION",
    "pop": "POP",
    "ding": "DING",
    "bell": "BELL / CHIME",
    "celebration": "CELEBRATION",
    "reaction": "REACTION",
    "alert": "ALERT",
    "glitch": "GLITCH",
    "rewind": "REWIND",
    "replay": "REPLAY CUE",
    "beep": "ERROR/BEEP",
    "money": "MONEY",
    "sparkle": "SPARKLE/WIN",
    "doom": "DOOM/LOW HIT",
}

# Category caps are intentionally stricter for the two sounds that were
# dominating automatic plans. True motion can still earn a whoosh and a real
# slam can still earn an impact, but generic emphasis is routed toward
# dings, bells, pops, reactions, and other semantic choices.
CATEGORY_EVENT_CAPS = {
    "LOW": {
        "whoosh": 1,
        "impact": 1,
        "ding": 2,
        "bell": 1,
        "reaction": 1,
        "celebration": 1,
        "alert": 1,
    },
    "PUNCHY": {
        "whoosh": 1,
        "impact": 2,
        "ding": 2,
        "bell": 2,
        "reaction": 2,
        "celebration": 1,
        "alert": 1,
    },
    "MAXIMUM": {
        "whoosh": 2,
        "impact": 2,
        "ding": 3,
        "bell": 2,
        "reaction": 2,
        "celebration": 2,
        "alert": 1,
    },
}

VOLUME_BY_ENERGY = {
    "LOW": {
        "impact": 0.24,
        "bass": 0.22,
        "whoosh": 0.18,
        "transition": 0.18,
        "pop": 0.13,
        "ding": 0.15,
        "bell": 0.15,
        "celebration": 0.16,
        "reaction": 0.15,
        "alert": 0.15,
        "glitch": 0.16,
        "rewind": 0.17,
        "replay": 0.17,
        "beep": 0.13,
        "money": 0.17,
        "sparkle": 0.17,
        "doom": 0.20,
    },
    "PUNCHY": {
        "impact": 0.38,
        "bass": 0.34,
        "whoosh": 0.28,
        "transition": 0.27,
        "pop": 0.22,
        "ding": 0.23,
        "bell": 0.23,
        "celebration": 0.24,
        "reaction": 0.23,
        "alert": 0.22,
        "glitch": 0.25,
        "rewind": 0.27,
        "replay": 0.25,
        "beep": 0.20,
        "money": 0.27,
        "sparkle": 0.26,
        "doom": 0.30,
    },
    "MAXIMUM": {
        "impact": 0.50,
        "bass": 0.45,
        "whoosh": 0.38,
        "transition": 0.36,
        "pop": 0.31,
        "ding": 0.31,
        "bell": 0.31,
        "celebration": 0.33,
        "reaction": 0.31,
        "alert": 0.30,
        "glitch": 0.34,
        "rewind": 0.36,
        "replay": 0.34,
        "beep": 0.29,
        "money": 0.36,
        "sparkle": 0.35,
        "doom": 0.42,
    },
}

FILENAME_ALIASES = {
    "money": {
        "cash",
        "coin",
        "coins",
        "gold",
        "money",
        "prize",
        "register",
    },
    "reaction": {
        "anime",
        "funny",
        "meme",
        "reaction",
        "scream",
        "screaming",
        "wow",
        "yeah",
    },
    "alert": {
        "alarm",
        "horn",
        "police",
        "siren",
    },
    "ding": {
        "ding",
        "notification",
    },
    "bell": {
        "bell",
        "bells",
        "chime",
        "doorbell",
    },
    "celebration": {
        "achievement",
        "cheer",
        "cheering",
        "crowd",
        "victory",
    },
    "sparkle": {
        "fairy",
        "magic",
        "magical",
        "sparkle",
        "success",
        "win",
    },
    "beep": {
        "beep",
        "buzzer",
        "error",
        "wrong",
    },
    "rewind": {
        "reverse",
        "rewind",
    },
    "replay": {
        "repeat",
        "replay",
    },
    "glitch": {
        "digital",
        "glitch",
        "static",
    },
    "impact": {
        "hit",
        "impact",
        "punch",
        "slam",
        "smack",
        "stomp",
        "thud",
    },
    "bass": {
        "bass",
        "boom",
        "drop",
        "low",
        "sub",
    },
    "doom": {
        "dark",
        "doom",
        "fail",
        "ominous",
    },
    "whoosh": {
        "swoosh",
        "swish",
        "whip",
        "whoosh",
        "woosh",
    },
    "transition": {
        "rise",
        "transition",
        "wipe",
    },
    "pop": {
        "bubble",
        "click",
        "pop",
        "tap",
    },
}

CATEGORY_INFERENCE_ORDER = (
    "money",
    "reaction",
    "alert",
    "ding",
    "bell",
    "celebration",
    "rewind",
    "replay",
    "glitch",
    "sparkle",
    "beep",
    "impact",
    "bass",
    "doom",
    "whoosh",
    "transition",
    "pop",
)

GENERATED_CATEGORY_FALLBACKS = {
    "reaction": "pop",
    "alert": "beep",
    "celebration": "bell",
}

GENERATED_RECIPES = {
    "impact": (
        "sine=frequency=115:sample_rate=48000:duration=0.28",
        "volume=0.75,afade=t=out:st=0.03:d=0.24",
    ),
    "bass": (
        "sine=frequency=72:sample_rate=48000:duration=0.42",
        "volume=0.85,afade=t=out:st=0.04:d=0.36",
    ),
    "whoosh": (
        "anoisesrc=color=pink:sample_rate=48000:duration=0.38:amplitude=0.26",
        "highpass=f=420,lowpass=f=5200,afade=t=in:st=0:d=0.08,afade=t=out:st=0.24:d=0.14",
    ),
    "transition": (
        "anoisesrc=color=pink:sample_rate=48000:duration=0.34:amplitude=0.22",
        "highpass=f=500,lowpass=f=4600,afade=t=in:st=0:d=0.06,afade=t=out:st=0.22:d=0.12",
    ),
    "pop": (
        "sine=frequency=920:sample_rate=48000:duration=0.16",
        "volume=0.48,afade=t=out:st=0.015:d=0.12",
    ),
    "ding": (
        "sine=frequency=1320:sample_rate=48000:duration=0.20",
        "volume=0.36,afade=t=out:st=0.04:d=0.14",
    ),
    "bell": (
        "sine=frequency=1046:sample_rate=48000:duration=0.32",
        "volume=0.34,afade=t=out:st=0.06:d=0.24",
    ),
    "glitch": (
        "anoisesrc=color=white:sample_rate=48000:duration=0.20:amplitude=0.16",
        "highpass=f=900,volume=0.62,afade=t=out:st=0.04:d=0.14",
    ),
    "rewind": (
        "sine=frequency=520:sample_rate=48000:duration=0.26",
        "volume=0.44,afade=t=in:st=0:d=0.04,afade=t=out:st=0.16:d=0.10",
    ),
    "replay": (
        "sine=frequency=700:sample_rate=48000:duration=0.24",
        "volume=0.40,afade=t=out:st=0.04:d=0.18",
    ),
    "beep": (
        "sine=frequency=880:sample_rate=48000:duration=0.18",
        "volume=0.38,afade=t=out:st=0.03:d=0.12",
    ),
    "money": (
        "sine=frequency=1320:sample_rate=48000:duration=0.24",
        "volume=0.40,afade=t=out:st=0.05:d=0.16",
    ),
    "sparkle": (
        "sine=frequency=1680:sample_rate=48000:duration=0.32",
        "volume=0.35,afade=t=out:st=0.08:d=0.22",
    ),
    "doom": (
        "sine=frequency=58:sample_rate=48000:duration=0.46",
        "volume=0.86,afade=t=out:st=0.05:d=0.38",
    ),
}

WORD_RE = re.compile(
    r"[a-z0-9]+",
    re.IGNORECASE,
)


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

    return data if isinstance(data, dict) else {}


def write_plan(
    plan: dict[str, Any],
) -> None:

    SFX_PLAN_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    SFX_PLAN_PATH.write_text(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def ffmpeg_binary() -> str:

    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_binary() -> str:

    return shutil.which("ffprobe") or "ffprobe"


def run_quiet(
    command: list[str],
) -> subprocess.CompletedProcess[str]:

    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def probe_duration(
    path: Path,
) -> float:

    result = run_quiet(
        [
            ffprobe_binary(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )

    if result.returncode != 0:
        return 0.0

    try:
        return max(
            0.0,
            float(
                result.stdout.strip()
                or 0.0
            ),
        )
    except ValueError:
        return 0.0


def has_audio_stream(
    path: Path,
) -> bool:

    result = run_quiet(
        [
            ffprobe_binary(),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )

    return result.returncode == 0 and bool(
        result.stdout.strip()
    )


def asset_is_valid(
    path: Path,
) -> bool:

    if not path.exists():
        return False

    return probe_duration(
        path
    ) > 0.02


def filename_words(
    path: Path,
) -> set[str]:

    stem = path.stem.lower()
    stem = re.sub(
        r"^\d+[-_\s]*",
        "",
        stem,
    )
    return set(
        WORD_RE.findall(
            stem
        )
    )


def stable_hash(
    value: str,
) -> int:

    total = 0
    for char in str(
        value
    ).lower():
        total = (
            total * 131
            + ord(char)
        ) % 1000003
    return total


def infer_category_from_words(
    words: set[str],
    fallback: str = "",
) -> str:

    normalized_fallback = str(
        fallback
        or ""
    ).strip().lower()
    best_category = (
        normalized_fallback
        if normalized_fallback in CATEGORY_LABELS
        else ""
    )
    best_score = 1 if best_category else 0

    for category in CATEGORY_INFERENCE_ORDER:
        aliases = FILENAME_ALIASES.get(
            category,
            set(),
        )
        score = len(
            words & aliases
        ) * 2

        # Extra context lets descriptive downloaded filenames land in the
        # category people expect without requiring exact naming conventions.
        if category == "money" and words & {
            "cash",
            "coin",
            "coins",
            "gold",
            "register",
        }:
            score += 4
        elif category == "reaction" and words & {
            "boy",
            "what",
        }:
            score += 2
        elif category == "ding" and words & {
            "editing",
            "notification04",
            "tone",
        }:
            score += 2
        elif category == "bell" and words & {
            "doorbell",
        }:
            score += 3
        elif category == "celebration" and words & {
            "huge",
            "uplifting",
        }:
            score += 1
        elif category == "impact" and words & {
            "epic",
            "hammer",
            "heavy",
            "huge",
            "metal",
            "strong",
        }:
            score += 2
        elif category == "glitch" and words & {
            "robot",
            "roar",
            "sci",
            "fi",
        }:
            score += 1
        elif category == "rewind" and words & {
            "reel",
            "tape",
        }:
            score += 2
        elif category == "whoosh" and words & {
            "air",
        }:
            score += 1

        if score > best_score:
            best_category = category
            best_score = score

    if best_category:
        return best_category

    return (
        normalized_fallback
        if normalized_fallback in CATEGORY_LABELS
        else "pop"
    )


def label_for_asset_words(
    category: str,
    words: set[str],
) -> str:

    if category == "money":
        if "register" in words:
            return "CASH REGISTER"
        if "coin" in words or "coins" in words or "gold" in words:
            return "COIN"
        if "cash" in words:
            return "CASH"
        return "MONEY"

    if category == "reaction":
        if "wow" in words or "anime" in words:
            return "WOW REACTION"
        if "scream" in words or "screaming" in words:
            return "REACTION SCREAM"
        if "yeah" in words or "boy" in words:
            return "YEAH BOY"
        if "meme" in words:
            return "MEME REACTION"
        return "REACTION"

    if category == "alert":
        if "horn" in words:
            return "AIR HORN"
        if "siren" in words or "police" in words:
            return "SIREN"
        return "ALERT"

    if category == "ding":
        if "notification" in words or "notification04" in words:
            return "NOTIFICATION DING"
        return "DING"

    if category == "bell":
        if "doorbell" in words:
            return "DOORBELL"
        if "achievement" in words:
            return "ACHIEVEMENT BELL"
        if "chime" in words:
            return "CHIME"
        return "BELL"

    if category == "celebration":
        if "crowd" in words or "cheer" in words or "cheering" in words:
            return "CROWD CHEER"
        return "CELEBRATION"

    if category == "impact":
        if words & {
            "metal",
            "hammer",
        }:
            return "METAL IMPACT"
        if words & {
            "heavy",
            "epic",
            "huge",
            "strong",
        }:
            return "HEAVY IMPACT"
        if words & {
            "punch",
            "punches",
        }:
            return "PUNCH HIT"
        if "stomp" in words:
            return "STOMP"
        return "IMPACT"

    if category == "bass":
        if words & {
            "short",
            "low",
            "drop",
        }:
            return "LOW HIT"
        return "BASS HIT"

    if category == "whoosh":
        if "whip" in words:
            return "WHIP WHOOSH"
        if "air" in words:
            return "AIR WHOOSH"
        return "WHOOSH"

    if category == "transition":
        return "TRANSITION"

    if category == "pop":
        if "electric" in words:
            return "ELECTRIC POP"
        if "bubble" in words:
            return "BUBBLE POP"
        if "click" in words:
            return "CLICK"
        return "POP"

    if category == "glitch":
        if "static" in words:
            return "GLITCH STATIC"
        if "roar" in words:
            return "GLITCH ROAR"
        return "GLITCH"

    if category == "rewind":
        return "REWIND"

    if category == "replay":
        return "REPLAY"

    if category == "beep":
        if words & {
            "wrong",
            "error",
            "buzzer",
            "game",
            "over",
        }:
            return "ERROR BEEP"
        return "BEEP"

    if category == "sparkle":
        return "SPARKLE"

    if category == "doom":
        if "game" in words and "over" in words:
            return "GAME OVER"
        return "LOW HIT"

    return CATEGORY_LABELS.get(
        category,
        category.upper(),
    )


def asset_metadata_for_path(
    asset_path: str | Path,
    fallback_category: str = "",
) -> dict[str, str]:

    path = Path(
        str(
            asset_path
            or ""
        )
    )
    words = filename_words(
        path
    )
    category = infer_category_from_words(
        words,
        fallback=fallback_category,
    )
    description = " ".join(
        word.capitalize()
        for word in path.stem.replace(
            "_",
            " ",
        ).replace(
            "-",
            " ",
        ).split()
    ).strip()

    return {
        "category": category,
        "label": label_for_asset_words(
            category,
            words,
        ),
        "asset_filename": path.name,
        "description": description
        or CATEGORY_LABELS.get(
            category,
            category.upper(),
        ),
    }


def index_local_assets() -> dict[str, list[Path]]:

    indexed = {
        category: []
        for category in CATEGORY_LABELS
    }

    if not SFX_DIR.exists():
        return indexed

    for path in sorted(
        SFX_DIR.rglob("*"),
        key=lambda item: item.name.lower(),
    ):
        if not path.is_file():
            continue
        if GENERATED_SFX_DIR in path.parents:
            continue
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue

        # Legacy ShortsFactory fallback files sometimes live directly in the
        # user SFX folder (sf_whoosh.wav, sf_impact.wav, etc.). Do not let
        # those outrank the real library. The procedural fallback generator
        # remains available when a category truly has no local match.
        if path.stem.lower().startswith("sf_"):
            continue

        words = filename_words(
            path
        )
        category = infer_category_from_words(
            words
        )
        indexed[
            category
        ].append(
            path
        )

    return indexed


def local_asset_counts(
    local_assets: dict[str, list[Path]],
) -> dict[str, int]:

    return {
        category: len(
            paths
        )
        for category, paths in local_assets.items()
        if paths
    }


def category_options_for_event(
    event: dict[str, Any],
) -> list[str]:

    base = str(
        event.get(
            "category",
            "",
        )
        or ""
    ).lower()
    kind = str(
        event.get(
            "source_event_type",
            "",
        )
        or ""
    ).lower()
    treatment = str(
        event.get(
            "source_treatment",
            "",
        )
        or ""
    ).lower()
    blob = text_blob(
        event
    )
    options: list[str] = []

    def add(
        category: str,
    ):

        normalized = str(
            category
            or ""
        ).lower()
        if (
            normalized in CATEGORY_LABELS
            and normalized not in options
        ):
            options.append(
                normalized
            )

    if any(
        token in blob
        for token in (
            "money",
            "cash",
            "coin",
            "coins",
            "register",
            "price",
            "paid",
            "rich",
        )
    ):
        add("money")

    if any(
        token in blob
        for token in (
            "wow",
            "reaction",
            "shocked",
            "shock",
            "surprise",
            "surprised",
            "scream",
            "funny",
            "meme",
            "crazy",
            "unbelievable",
        )
    ):
        add("reaction")

    if any(
        token in blob
        for token in (
            "victory",
            "celebrate",
            "celebration",
            "cheer",
            "crowd",
            "applause",
            "yeah",
        )
    ):
        add("celebration")

    if any(
        token in blob
        for token in (
            "achievement",
            "bell",
            "chime",
            "unlock",
            "reward",
            "success",
        )
    ):
        add("bell")

    if any(
        token in blob
        for token in (
            "ding",
            "notification",
            "reveal",
            "realize",
            "realization",
            "discover",
            "discovery",
            "notice",
            "important",
            "key point",
            "answer",
            "finally",
            "actually",
        )
    ):
        add("ding")

    if any(
        token in blob
        for token in (
            "alarm",
            "siren",
            "warning",
            "attention",
            "air horn",
        )
    ):
        add("alert")

    if any(
        token in blob
        for token in (
            "glitch",
            "rgb",
            "static",
            "digital",
            "overdrive",
        )
    ):
        add("glitch")

    if any(
        token in blob
        for token in (
            "rewind",
            "reverse",
        )
    ):
        add("rewind")

    if any(
        token in blob
        for token in (
            "replay",
            "repeat",
        )
    ):
        add("replay")

    if any(
        token in blob
        for token in (
            "sparkle",
            "heart",
            "fairy",
            "magic",
        )
    ):
        add("sparkle")

    if any(
        token in blob
        for token in (
            "wrong",
            "error",
            "awkward",
            "fail",
            "buzzer",
        )
    ):
        add("beep")

    if any(
        token in blob
        for token in (
            "doom",
            "dark",
            "negative",
            "ominous",
            "game over",
        )
    ):
        add("doom")

    if treatment in {
        "whip_transition",
    } or any(
        token in blob
        for token in (
            "whip",
            "whoosh",
            "swish",
            "wipe",
        )
    ):
        add("whoosh")

    if treatment in {
        "speed_ramp",
        "speed_up",
    }:
        add("transition")

    if kind in {
        "camera",
        "ai_visual",
    } and any(
        token in blob
        for token in (
            "motion",
            "move",
            "moving",
            "pan",
            "zoom",
            "reframe",
            "entrance",
            "transition",
            "cutaway",
        )
    ):
        add("whoosh")

    add(base)

    # Generic emphasis should sound light first. Heavy impacts are fallbacks,
    # not the default replacement whenever a category repeats.
    if base in {
        "whoosh",
        "transition",
    }:
        add("ding")
        add("pop")
    elif base == "impact":
        add("ding")
        add("pop")
        add("bass")
    elif base == "pop":
        add("ding")
        add("bell")
    elif base == "ding":
        add("bell")
        add("pop")
    elif base == "bell":
        add("ding")
        add("pop")
    elif base == "reaction":
        add("pop")
        add("ding")
    elif base == "celebration":
        add("bell")
        add("ding")
    elif base == "alert":
        add("beep")
        add("ding")
    elif base == "doom":
        add("bass")
        add("impact")
    elif base == "sparkle":
        add("bell")
        add("ding")
    elif base == "money":
        add("ding")
        add("pop")
    elif base == "glitch":
        add("beep")
        add("pop")

    if not options:
        add("ding")
        add("pop")

    return options


def strong_motion_event(
    event: dict[str, Any],
) -> bool:

    treatment = str(
        event.get(
            "source_treatment",
            "",
        )
        or ""
    ).lower()
    blob = text_blob(
        event
    )
    return (
        treatment
        in {
            "whip_transition",
            "speed_ramp",
            "speed_up",
        }
        or any(
            token in blob
            for token in (
                "whip",
                "whoosh",
                "swish",
                "wipe",
                "pan",
                "zoom",
                "reframe",
                "motion",
                "moving",
                "transition",
            )
        )
    )


def strong_impact_event(
    event: dict[str, Any],
) -> bool:

    treatment = str(
        event.get(
            "source_treatment",
            "",
        )
        or ""
    ).lower()
    level = str(
        event.get(
            "level",
            "",
        )
        or ""
    ).upper()
    blob = text_blob(
        event
    )
    return (
        bool(
            event.get(
                "hero",
                False,
            )
        )
        or treatment
        in {
            "impact",
            "slam",
        }
        or level
        in {
            "IMPACT",
            "EXTREME",
        }
        or any(
            token in blob
            for token in (
                "slam",
                "punch",
                "smash",
                "stomp",
            )
        )
    )


def category_cap(
    category: str,
    energy: str,
) -> int | None:

    limits = CATEGORY_EVENT_CAPS.get(
        energy,
        CATEGORY_EVENT_CAPS.get(
            DEFAULT_ENERGY,
            {},
        ),
    )
    raw = limits.get(
        category
    )
    return (
        int(
            raw
        )
        if raw is not None
        else None
    )


def choose_event_category(
    event: dict[str, Any],
    recent_categories: list[str],
    category_counts: dict[str, int] | None = None,
    energy: str = DEFAULT_ENERGY,
) -> str:

    options = category_options_for_event(
        event
    )
    if not options:
        return "ding"

    category_counts = category_counts or {}

    filtered: list[str] = []
    for category in options:
        cap = category_cap(
            category,
            energy,
        )
        if (
            cap is not None
            and category_counts.get(
                category,
                0,
            )
            >= cap
        ):
            continue
        if (
            category == "whoosh"
            and not strong_motion_event(
                event
            )
        ):
            continue
        if (
            category == "impact"
            and not strong_impact_event(
                event
            )
            and len(
                options
            ) > 1
        ):
            continue
        filtered.append(
            category
        )

    if not filtered:
        filtered = [
            category
            for category in options
            if category not in {
                "whoosh",
                "impact",
            }
        ] or options

    if recent_categories:
        for category in filtered:
            if category != recent_categories[-1]:
                return category

    return filtered[0]


def semantic_asset_score(
    category: str,
    path: Path,
    event: dict[str, Any],
) -> float:

    words = filename_words(
        path
    )
    blob = text_blob(
        event
    )
    treatment = str(
        event.get(
            "source_treatment",
            "",
        )
        or ""
    ).lower()
    score = float(
        len(
            words
            & FILENAME_ALIASES.get(
                category,
                set(),
            )
        )
    ) * 4.0

    if category == "reaction":
        if words & {
            "wow",
            "anime",
            "scream",
            "screaming",
            "meme",
            "yeah",
            "funny",
        }:
            score += 5.0
        if any(
            token in blob
            for token in (
                "wow",
                "reaction",
                "shock",
                "surprise",
                "funny",
                "crazy",
            )
        ):
            score += 3.0
    elif category == "alert":
        if words & {
            "horn",
            "siren",
            "police",
            "alarm",
        }:
            score += 5.0
        if any(
            token in blob
            for token in (
                "warning",
                "attention",
                "alarm",
                "siren",
            )
        ):
            score += 3.0
    elif category == "ding":
        if words & {
            "ding",
            "notification",
        }:
            score += 5.0
        if any(
            token in blob
            for token in (
                "reveal",
                "realize",
                "discover",
                "notice",
                "important",
                "answer",
                "finally",
                "actually",
            )
        ):
            score += 3.0
    elif category == "bell":
        if words & {
            "bell",
            "bells",
            "doorbell",
            "chime",
            "achievement",
        }:
            score += 5.0
        if any(
            token in blob
            for token in (
                "achievement",
                "success",
                "unlock",
                "reward",
                "chime",
            )
        ):
            score += 3.0
    elif category == "celebration":
        if words & {
            "crowd",
            "cheer",
            "cheering",
            "victory",
            "achievement",
        }:
            score += 5.0
        if any(
            token in blob
            for token in (
                "victory",
                "celebrate",
                "cheer",
                "crowd",
                "yeah",
            )
        ):
            score += 3.0
    elif category == "impact":
        if words & {
            "heavy",
            "epic",
            "huge",
            "impact",
            "metal",
            "punch",
            "slam",
            "stomp",
        }:
            score += 4.0
        if any(
            token in blob
            for token in (
                "reaction",
                "chaos",
                "wtf",
                "slam",
            )
        ):
            score += 2.5
    elif category == "bass":
        if words & {
            "bass",
            "boom",
            "drop",
            "low",
            "sub",
        }:
            score += 4.0
        if any(
            token in blob
            for token in (
                "extreme",
                "doom",
                "negative",
            )
        ):
            score += 2.0
    elif category == "whoosh":
        if words & {
            "air",
            "whoosh",
            "whip",
            "woosh",
            "swish",
        }:
            score += 5.0
        if treatment == "whip_transition":
            score += 3.0
    elif category == "transition":
        if words & {
            "transition",
            "rise",
            "wipe",
        }:
            score += 4.0
    elif category == "pop":
        if words & {
            "click",
            "pop",
            "bubble",
            "electric",
        }:
            score += 4.0
    elif category == "glitch":
        if words & {
            "glitch",
            "static",
            "robot",
            "digital",
            "sci",
            "fi",
        }:
            score += 5.0
    elif category == "rewind":
        if words & {
            "rewind",
            "reverse",
            "reel",
            "tape",
        }:
            score += 5.0
    elif category == "replay":
        if words & {
            "replay",
            "repeat",
        }:
            score += 5.0
    elif category == "beep":
        if words & {
            "beep",
            "wrong",
            "error",
            "buzzer",
        }:
            score += 5.0
    elif category == "money":
        if words & {
            "cash",
            "coin",
            "coins",
            "money",
            "register",
            "bag",
            "gold",
            "prize",
        }:
            score += 5.0
    elif category == "sparkle":
        if words & {
            "sparkle",
            "win",
            "success",
            "fairy",
            "magic",
            "magical",
        }:
            score += 5.0
    elif category == "doom":
        if words & {
            "dark",
            "doom",
            "ominous",
            "game",
            "over",
            "epic",
        }:
            score += 5.0

    return score


def generated_asset_path(
    category: str,
) -> Path:

    return GENERATED_SFX_DIR / f"sf_{category}.wav"


def ensure_generated_asset(
    category: str,
    warnings: list[str],
) -> Path | None:

    recipe = GENERATED_RECIPES.get(
        category
    )
    if not recipe:
        warnings.append(
            f"No procedural fallback recipe for {category}."
        )
        return None

    output = generated_asset_path(
        category
    )

    if asset_is_valid(
        output
    ):
        return output

    GENERATED_SFX_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    source_filter, audio_filter = recipe
    command = [
        ffmpeg_binary(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        source_filter,
        "-filter:a",
        audio_filter,
        "-ac",
        "2",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]

    result = run_quiet(
        command
    )

    if result.returncode != 0 or not asset_is_valid(output):
        warnings.append(
            f"Could not generate fallback SFX for {category}: {result.stderr.strip()[:240]}"
        )
        try:
            output.unlink(
                missing_ok=True
            )
        except OSError:
            pass
        return None

    return output


def choose_asset(
    category: str,
    local_assets: dict[str, list[Path]],
    warnings: list[str],
    *,
    event: dict[str, Any] | None = None,
    recent_categories: list[str] | None = None,
    recent_assets: list[str] | None = None,
    asset_use_counts: dict[str, int] | None = None,
    selection_index: int = 0,
) -> tuple[Path | None, str]:

    event = event or {}
    recent_categories = recent_categories or []
    recent_assets = recent_assets or []
    asset_use_counts = asset_use_counts or {}
    valid_assets: list[Path] = []

    for path in local_assets.get(
        category,
        [],
    ):
        if asset_is_valid(path):
            valid_assets.append(
                path
            )
            continue

        warnings.append(
            f"Skipping corrupt/unreadable local SFX: {path}"
        )

    if valid_assets:
        selection_key = (
            f"{category}|"
            f"{selection_index}|"
            f"{event.get('start', 0.0)}|"
            f"{event.get('source_event_type', '')}|"
            f"{event.get('source_treatment', '')}|"
            f"{event.get('trigger', '')}"
        )
        ranked = []
        for path in valid_assets:
            path_text = str(
                path
            )
            score = semantic_asset_score(
                category,
                path,
                event,
            )

            # Strongly rotate through the user's actual library. Semantic fit
            # still matters, but an unused valid sound should usually beat a
            # clip that has already fired several times in the same plan.
            usage_count = int(
                asset_use_counts.get(
                    path_text,
                    0,
                )
            )
            score -= usage_count * 6.0
            if usage_count == 0:
                score += 2.0

            if recent_categories and recent_categories[-1] == category:
                score -= 1.8
            if (
                len(recent_categories) >= 2
                and recent_categories[-1] == category
                and recent_categories[-2] == category
            ):
                score -= 3.4
            if recent_assets and recent_assets[-1] == path_text:
                score -= 6.0
            if path_text in recent_assets[-3:]:
                score -= 2.5

            ranked.append(
                (
                    score,
                    stable_hash(
                        selection_key
                        + "|"
                        + path.name
                    ),
                    path,
                )
            )

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1],
            ),
            reverse=True,
        )
        return ranked[0][2], "local"

    generated_category = GENERATED_CATEGORY_FALLBACKS.get(
        category,
        category,
    )
    generated = ensure_generated_asset(
        generated_category,
        warnings,
    )
    if generated:
        return generated, "generated"

    return None, "skipped"


def as_float(
    value: Any,
    default: float = 0.0,
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


def event_start(
    event: dict[str, Any],
) -> float:

    return as_float(
        event.get(
            "start",
            event.get(
                "output_start",
                event.get(
                    "source_start",
                    0.0,
                ),
            ),
        )
    )


def event_end(
    event: dict[str, Any],
) -> float:

    start = event_start(
        event
    )
    end = as_float(
        event.get(
            "end",
            event.get(
                "output_end",
                start,
            ),
        ),
        start,
    )
    return max(
        start,
        end,
    )


def text_blob(
    event: dict[str, Any],
) -> str:

    parts = []
    for key in (
        "type",
        "treatment",
        "effect",
        "recipe",
        "level",
        "reason",
        "trigger",
        "trigger_word",
        "accent",
        "category",
    ):
        value = event.get(
            key
        )
        if value is not None:
            parts.append(
                str(value)
            )
    return " ".join(
        parts
    ).lower()


def candidate_for_event(
    event: dict[str, Any],
) -> dict[str, Any] | None:

    start = event_start(
        event
    )
    if start < 0:
        return None

    kind = str(
        event.get(
            "type",
            "",
        )
    ).lower()
    treatment = str(
        event.get(
            "treatment",
            event.get(
                "effect",
                "",
            ),
        )
    ).lower()
    recipe = str(
        event.get(
            "recipe",
            "",
        )
    ).lower()
    level = str(
        event.get(
            "level",
            "",
        )
    ).upper()
    blob = text_blob(
        event
    )

    category = ""
    score = 0.0
    reason = ""

    if treatment == "whip_transition":
        category = "whoosh"
        score = 92.0
        reason = "whip transition"
    elif treatment == "replay":
        category = "replay"
        score = 86.0
        reason = "replay cue"
    elif treatment == "rewind":
        category = "rewind"
        score = 86.0
        reason = "rewind cue"
    elif treatment in {
        "speed_ramp",
        "speed_up",
    }:
        category = "transition"
        score = 45.0
        reason = "pacing change"
    elif kind == "graphic" and "slam" in treatment:
        category = "doom" if "doom" in recipe else "impact"
        score = 90.0
        reason = "slam text stack"
    elif kind == "filter":
        if any(
            token in blob
            for token in (
                "glitch",
                "rgb",
                "overdrive",
            )
        ):
            category = "glitch"
            score = 78.0
            reason = "glitch/flash visual hit"
        elif "doom" in recipe or "desat" in treatment:
            category = "doom"
            score = 76.0
            reason = "doom visual hit"
        elif "detail_hit" in treatment:
            category = "ding"
            score = 52.0
            reason = "detail ding"
    elif kind == "camera":
        if any(
            token in blob
            for token in (
                "whip",
                "pan",
                "motion",
                "move",
                "zoom",
                "reframe",
            )
        ):
            category = "whoosh"
            score = 58.0
            reason = "camera movement"
        else:
            category = "ding"
            score = 48.0
            reason = "camera accent"
    elif kind == "ai_visual":
        if any(
            token in blob
            for token in (
                "money",
                "cash",
                "coin",
            )
        ):
            category = "money"
            score = 58.0
            reason = "money cutaway"
        elif any(
            token in blob
            for token in (
                "victory",
                "cheer",
                "crowd",
                "yeah",
            )
        ):
            category = "celebration"
            score = 56.0
            reason = "celebration cutaway"
        elif any(
            token in blob
            for token in (
                "achievement",
                "success",
                "unlock",
                "reward",
                "bell",
                "chime",
            )
        ):
            category = "bell"
            score = 54.0
            reason = "positive chime cutaway"
        elif any(
            token in blob
            for token in (
                "wow",
                "reaction",
                "shock",
                "surprise",
                "funny",
                "crazy",
            )
        ):
            category = "reaction"
            score = 54.0
            reason = "reaction cutaway"
        elif any(
            token in blob
            for token in (
                "sparkle",
                "heart",
                "magic",
            )
        ):
            category = "sparkle"
            score = 50.0
            reason = "positive cutaway"
        elif any(
            token in blob
            for token in (
                "glitch",
                "rgb",
                "digital",
                "overdrive",
            )
        ):
            category = "glitch"
            score = 56.0
            reason = "glitch cutaway"
        elif any(
            token in blob
            for token in (
                "doom",
                "dark",
                "negative",
                "ominous",
            )
        ):
            category = "doom"
            score = 54.0
            reason = "negative cutaway"
        elif any(
            token in blob
            for token in (
                "motion",
                "transition",
                "whip",
                "wipe",
                "entrance",
            )
        ):
            category = "whoosh"
            score = 48.0
            reason = "AI visual transition"
        else:
            category = "ding"
            score = 46.0
            reason = "AI visual accent"
    elif kind == "emoji":
        if any(
            token in blob
            for token in (
                "cash",
                "coin",
                "money",
                "1f4b0",
            )
        ):
            category = "money"
            score = 50.0
            reason = "money emoji cue"
        elif any(
            token in blob
            for token in (
                "victory",
                "cheer",
                "yeah",
            )
        ):
            category = "celebration"
            score = 46.0
            reason = "celebration emoji cue"
        elif any(
            token in blob
            for token in (
                "wow",
                "shock",
                "surprise",
                "funny",
            )
        ):
            category = "reaction"
            score = 45.0
            reason = "reaction emoji cue"
        elif any(
            token in blob
            for token in (
                "win",
                "sparkle",
                "heart",
            )
        ):
            category = "bell"
            score = 44.0
            reason = "positive emoji cue"
        else:
            category = "pop"
            score = 38.0
            reason = "emoji pop"
    elif kind == "caption_emphasis":
        if "money" in recipe or "money" in blob:
            category = "money"
            score = 72.0
            reason = "money word"
        elif "doom" in recipe or "negative" in blob:
            category = "doom"
            score = 76.0
            reason = "doom/negative word"
        elif any(
            token in blob
            for token in (
                "victory",
                "cheer",
                "celebrate",
                "yeah",
            )
        ):
            category = "celebration"
            score = 68.0
            reason = "celebration word"
        elif any(
            token in blob
            for token in (
                "achievement",
                "success",
                "unlock",
                "reward",
            )
        ):
            category = "bell"
            score = 66.0
            reason = "positive word"
        elif any(
            token in blob
            for token in (
                "wow",
                "shock",
                "surprise",
                "crazy",
                "funny",
            )
        ):
            category = "reaction"
            score = 64.0
            reason = "reaction word"
        elif level == "IMPACT" or treatment == "impact":
            category = "impact"
            score = 74.0
            reason = "impact word"
        elif level == "EXTREME":
            category = "bass"
            score = 84.0
            reason = "extreme caption hit"
        else:
            category = "ding"
            score = 40.0
            reason = "caption emphasis"

    if not category:
        return None

    if bool(
        event.get(
            "hero",
            False,
        )
    ):
        score += 20.0

    score += min(
        12.0,
        as_float(
            event.get(
                "intensity",
                0.0,
            )
        )
        * 10.0,
    )

    return {
        "start": round(
            start,
            3,
        ),
        "end": round(
            max(
                event_end(
                    event
                ),
                start + CATEGORY_DURATIONS.get(
                    category,
                    0.25,
                ),
            ),
            3,
        ),
        "category": category,
        "label": CATEGORY_LABELS.get(
            category,
            category.upper(),
        ),
        "score": round(
            score,
            3,
        ),
        "reason": reason,
        "source_event_type": kind,
        "source_treatment": treatment,
        "recipe": recipe,
        "trigger": str(
            event.get(
                "trigger",
                event.get(
                    "trigger_word",
                    "",
                ),
            )
        ),
        "stack_id": str(
            event.get(
                "stack_id",
                "",
            )
            or ""
        ),
        "hero": bool(
            event.get(
                "hero",
                False,
            )
        ),
    }


def candidates_from_plan(
    visual_plan: dict[str, Any],
    temporal_plan: dict[str, Any],
) -> list[dict[str, Any]]:

    candidates: list[dict[str, Any]] = []

    for event in visual_plan.get(
        "events",
        [],
    ):
        if not isinstance(
            event,
            dict,
        ):
            continue
        candidate = candidate_for_event(
            event
        )
        if candidate:
            candidates.append(
                candidate
            )

    visual_has_temporal = any(
        candidate.get(
            "source_event_type"
        )
        == "temporal"
        for candidate in candidates
    )

    if not visual_has_temporal:
        for event in temporal_plan.get(
            "events",
            [],
        ):
            if not isinstance(
                event,
                dict,
            ):
                continue
            temporal_event = dict(
                event
            )
            temporal_event["type"] = "temporal"
            temporal_event["treatment"] = event.get(
                "type",
                "",
            )
            candidate = candidate_for_event(
                temporal_event
            )
            if candidate:
                candidates.append(
                    candidate
                )

    for hero in visual_plan.get(
        "hero_moments",
        [],
    ):
        if not isinstance(
            hero,
            dict,
        ):
            continue
        hero_event = dict(
            hero
        )
        hero_event.setdefault(
            "type",
            "caption_emphasis",
        )
        hero_event.setdefault(
            "treatment",
            str(
                hero.get(
                    "level",
                    "",
                )
            ).lower(),
        )
        hero_event["hero"] = True
        candidate = candidate_for_event(
            hero_event
        )
        if candidate:
            candidate["source_event_type"] = "hero_moment"
            candidates.append(
                candidate
            )

    return candidates


def collapse_stacks(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    best_by_stack: dict[str, dict[str, Any]] = {}
    loose: list[dict[str, Any]] = []

    for candidate in candidates:
        stack_id = str(
            candidate.get(
                "stack_id",
                "",
            )
        )
        if not stack_id:
            loose.append(
                candidate
            )
            continue

        current = best_by_stack.get(
            stack_id
        )
        if current is None or float(
            candidate.get(
                "score",
                0.0,
            )
        ) > float(
            current.get(
                "score",
                0.0,
            )
        ):
            best_by_stack[stack_id] = candidate

    return loose + list(
        best_by_stack.values()
    )


def select_events(
    candidates: list[dict[str, Any]],
    energy: str,
    *,
    selection_start: float | None = None,
    selection_end: float | None = None,
) -> list[dict[str, Any]]:

    limits = ENERGY_LIMITS.get(
        energy,
        ENERGY_LIMITS[DEFAULT_ENERGY],
    )
    base_max_events = int(
        limits["max_events"]
    )
    min_spacing = float(
        limits["min_spacing"]
    )

    bounded_selection = (
        selection_start is not None
        and selection_end is not None
        and float(
            selection_end
        )
        > float(
            selection_start
        )
    )
    if bounded_selection:
        selection_start = float(
            selection_start
        )
        selection_end = float(
            selection_end
        )
        selection_duration = max(
            0.0,
            selection_end
            - selection_start,
        )
        # Preserve the existing 30-second density, but scale the event budget
        # with whatever range the editor selected. A 90-second MAXIMUM range,
        # for example, gets roughly three times the opportunities of a
        # 30-second range instead of hitting the old hard cap of 12.
        max_events = max(
            1,
            int(
                math.ceil(
                    base_max_events
                    * max(
                        30.0,
                        selection_duration,
                    )
                    / 30.0
                )
            ),
        )
    else:
        max_events = base_max_events

    ranked_candidates = sorted(
        candidates,
        key=lambda item: (
            -float(
                item.get(
                    "score",
                    0.0,
                )
            ),
            float(
                item.get(
                    "start",
                    0.0,
                )
            ),
        ),
    )

    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()

    def can_add(
        candidate: dict[str, Any],
    ) -> bool:
        start = float(
            candidate.get(
                "start",
                0.0,
            )
        )
        return not any(
            abs(
                start
                - float(
                    chosen.get(
                        "start",
                        0.0,
                    )
                )
            )
            < min_spacing
            for chosen in selected
        )

    def add_candidate(
        candidate: dict[str, Any],
    ) -> bool:
        candidate_id = id(
            candidate
        )
        if (
            candidate_id in selected_ids
            or not can_add(
                candidate
            )
        ):
            return False
        selected.append(
            candidate
        )
        selected_ids.add(
            candidate_id
        )
        return True

    if bounded_selection and ranked_candidates:
        # First guarantee coverage across the selected range. Pick the best
        # available event inside each successive time window before filling
        # remaining slots by score. This prevents every high-scoring SFX from
        # clustering in the opening seconds of a long manual selection.
        coverage_window = max(
            2.0,
            float(
                limits.get(
                    "coverage_window",
                    8.0,
                )
            ),
        )
        window_start = selection_start
        while (
            window_start < selection_end
            and len(
                selected
            )
            < max_events
        ):
            window_end = min(
                selection_end,
                window_start
                + coverage_window,
            )
            window_candidates = [
                candidate
                for candidate in ranked_candidates
                if (
                    float(
                        candidate.get(
                            "start",
                            0.0,
                        )
                    )
                    >= window_start
                    and float(
                        candidate.get(
                            "start",
                            0.0,
                        )
                    )
                    < window_end
                )
            ]
            for candidate in window_candidates:
                if add_candidate(
                    candidate
                ):
                    break
            window_start = window_end

    for candidate in ranked_candidates:
        if len(
            selected
        ) >= max_events:
            break
        add_candidate(
            candidate
        )

    selected.sort(
        key=lambda item: float(
            item.get(
                "start",
                0.0,
            )
        )
    )

    return selected


def playback_duration_for_asset(
    path: Path,
    category: str,
) -> float:

    fallback = CATEGORY_DURATIONS.get(
        category,
        0.25,
    )
    measured = probe_duration(
        path
    )
    if measured <= 0.02:
        return fallback

    maximum = CATEGORY_MAX_PLAY_SECONDS.get(
        category,
        3.0,
    )
    return max(
        0.06,
        min(
            measured,
            maximum,
        ),
    )


def prepare_events(
    selected: list[dict[str, Any]],
    energy: str,
    local_assets: dict[str, list[Path]],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    recent_categories: list[str] = []
    recent_assets: list[str] = []
    category_counts: dict[str, int] = {}
    asset_use_counts: dict[str, int] = {}

    for index, event in enumerate(
        selected,
        start=1,
    ):
        category = choose_event_category(
            event,
            recent_categories,
            category_counts=category_counts,
            energy=energy,
        )
        asset, asset_source = choose_asset(
            category,
            local_assets,
            warnings,
            event=event,
            recent_categories=recent_categories,
            recent_assets=recent_assets,
            asset_use_counts=asset_use_counts,
            selection_index=index,
        )
        if not asset:
            skipped.append(
                {
                    **event,
                    "category": category,
                    "asset_source": "skipped",
                    "skip_reason": "no usable local or generated asset",
                }
            )
            continue

        metadata = asset_metadata_for_path(
            asset,
            fallback_category=category,
        )
        resolved_category = str(
            metadata.get(
                "category",
                category,
            )
            or category
        )

        volume = VOLUME_BY_ENERGY.get(
            energy,
            VOLUME_BY_ENERGY[DEFAULT_ENERGY],
        ).get(
            resolved_category,
            0.25,
        )

        # Keep non-hero speech accents tucked under dialogue.
        if (
            not bool(
                event.get(
                    "hero",
                    False,
                )
            )
            and str(
                event.get(
                    "source_event_type",
                    "",
                )
            )
            in {
                "caption_emphasis",
                "emoji",
                "camera",
                "transcript_accent",
            }
        ):
            volume *= 0.82

        planned.append(
            {
                "id": f"sfx_{index:02d}",
                **event,
                "category": resolved_category,
                "label": str(
                    metadata.get(
                        "label",
                        CATEGORY_LABELS.get(
                            resolved_category,
                            resolved_category.upper(),
                        ),
                    )
                ),
                "description": str(
                    metadata.get(
                        "description",
                        "",
                    )
                    or ""
                ),
                "asset_filename": str(
                    metadata.get(
                        "asset_filename",
                        "",
                    )
                    or ""
                ),
                "duration": round(
                    playback_duration_for_asset(
                        asset,
                        resolved_category,
                    ),
                    3,
                ),
                "volume": round(
                    volume,
                    3,
                ),
                "asset_path": str(
                    asset
                ),
                "asset_source": asset_source,
            }
        )
        recent_categories.append(
            resolved_category
        )
        recent_assets.append(
            str(asset)
        )
        category_counts[
            resolved_category
        ] = (
            category_counts.get(
                resolved_category,
                0,
            )
            + 1
        )
        asset_use_counts[
            str(
                asset
            )
        ] = (
            asset_use_counts.get(
                str(
                    asset
                ),
                0,
            )
            + 1
        )

    return planned, skipped


def sfx_clip_from_event(
    event: dict[str, Any],
) -> dict[str, Any]:

    start = as_float(
        event.get(
            "start",
            0.0,
        )
    )
    duration = max(
        0.06,
        as_float(
            event.get(
                "duration",
                CATEGORY_DURATIONS.get(
                    str(
                        event.get(
                            "category",
                            "impact",
                        )
                    ),
                    0.25,
                ),
            ),
            0.25,
        ),
    )

    asset_path = str(
        event.get(
            "asset_path",
            "",
        )
        or ""
    )
    metadata = asset_metadata_for_path(
        asset_path,
        fallback_category=str(
            event.get(
                "category",
                "impact",
            )
            or "impact"
        ),
    ) if asset_path else {
        "category": str(
            event.get(
                "category",
                "impact",
            )
            or "impact"
        ),
        "label": str(
            event.get(
                "label",
                "SFX",
            )
            or "SFX"
        ),
        "asset_filename": "",
        "description": str(
            event.get(
                "description",
                "",
            )
            or ""
        ),
    }

    return {
        "id": str(
            event.get(
                "id",
                f"sfx_{int(start * 1000):06d}",
            )
        ),
        "kind": "SFX",
        "time_basis": str(
            event.get(
                "time_basis",
                "final_output",
            )
            or "final_output"
        ),
        "start": round(
            start,
            3,
        ),
        "end": round(
            start
            + duration,
            3,
        ),
        "duration": round(
            duration,
            3,
        ),
        "trim_in": float(
            event.get(
                "trim_in",
                0.0,
            )
            or 0.0
        ),
        "asset_path": str(
            asset_path
        ),
        "label": str(
            metadata.get(
                "label",
                "SFX",
            )
            or "SFX"
        ),
        "category": str(
            metadata.get(
                "category",
                event.get(
                    "category",
                    "",
                ),
            )
            or ""
        ),
        "asset_filename": str(
            metadata.get(
                "asset_filename",
                "",
            )
            or ""
        ),
        "description": str(
            metadata.get(
                "description",
                event.get(
                    "description",
                    "",
                ),
            )
            or ""
        ),
        "volume": float(
            event.get(
                "volume",
                0.25,
            )
            or 0.25
        ),
        "active": bool(
            event.get(
                "active",
                True,
            )
        ),
        "origin": str(
            event.get(
                "origin",
                "automatic",
            )
            or "automatic"
        ),
        "manual_override": bool(
            event.get(
                "manual_override",
                False,
            )
        ),
        "locked": bool(
            event.get(
                "locked",
                False,
            )
        ),
        "asset_source": str(
            event.get(
                "asset_source",
                "",
            )
            or ""
        ),
    }


def event_from_sfx_clip(
    clip: dict[str, Any],
) -> dict[str, Any] | None:

    if clip.get(
        "active",
        True,
    ) is False:
        return None

    asset_path = str(
        clip.get(
            "asset_path",
            "",
        )
        or ""
    )
    if not asset_path:
        return None

    asset_file = Path(
        asset_path
    )
    if not asset_is_valid(
        asset_file
    ):
        return None

    metadata = asset_metadata_for_path(
        asset_path,
        fallback_category=str(
            clip.get(
                "category",
                "",
            )
            or ""
        ),
    )

    start = as_float(
        clip.get(
            "start",
            0.0,
        )
    )
    end = as_float(
        clip.get(
            "end",
            start,
        ),
        start,
    )
    duration = max(
        0.06,
        end - start,
    )

    if str(
        clip.get(
            "time_basis",
            "final_output",
        )
        or "final_output"
    ) == "source":
        settings = load_render_settings()
        selection_start = as_float(
            settings.get(
                "selection_start",
                0.0,
            )
        )
        selection_end = as_float(
            settings.get(
                "selection_end",
                selection_start,
            ),
            selection_start,
        )

        if (
            end <= selection_start
            or start >= selection_end
        ):
            return None

        selection_duration = max(
            0.0,
            selection_end - selection_start,
        )
        combined_plan = read_json(
            COMBINED_EDIT_PLAN_PATH
        )
        keeps = keep_segments(
            combined_plan,
            selection_duration,
        )
        mapped = map_source_interval_to_tight(
            start,
            end,
            selection_start,
            keeps,
        )
        if mapped is None:
            return None

        tight_start, tight_end = mapped
        temporal_plan = read_json(
            TEMPORAL_EDIT_PLAN_PATH
        )
        start, end = map_tight_interval_to_final(
            tight_start,
            tight_end,
            temporal_plan,
        )
        duration = max(
            0.06,
            end - start,
        )

    return {
        "id": str(
            clip.get(
                "id",
                "",
            )
        ),
        "start": round(
            start,
            3,
        ),
        "end": round(
            start
            + duration,
            3,
        ),
        "duration": round(
            duration,
            3,
        ),
        "trim_in": max(
            0.0,
            as_float(
                clip.get(
                    "trim_in",
                    0.0,
                )
            ),
        ),
        "category": str(
            metadata.get(
                "category",
                clip.get(
                    "category",
                    "",
                ),
            )
            or ""
        ),
        "label": str(
            metadata.get(
                "label",
                clip.get(
                    "label",
                    "SFX",
                ),
            )
            or "SFX"
        ),
        "description": str(
            metadata.get(
                "description",
                clip.get(
                    "description",
                    "",
                ),
            )
            or ""
        ),
        "asset_filename": str(
            metadata.get(
                "asset_filename",
                clip.get(
                    "asset_filename",
                    "",
                ),
            )
            or ""
        ),
        "volume": max(
            0.0,
            min(
                0.8,
                as_float(
                    clip.get(
                        "volume",
                        0.25,
                    ),
                    0.25,
                ),
            ),
        ),
        "asset_path": asset_path,
        "asset_source": str(
            clip.get(
                "asset_source",
                "editor_asset_plan",
            )
            or "editor_asset_plan"
        ),
        "origin": str(
            clip.get(
                "origin",
                "editor",
            )
            or "editor"
        ),
        "manual_override": bool(
            clip.get(
                "manual_override",
                False,
            )
        ),
        "locked": bool(
            clip.get(
                "locked",
                False,
            )
        ),
    }


def merge_sfx_with_editor_plan(
    automatic_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:

    plan = load_editor_asset_plan()
    settings = load_render_settings()
    source_video = str(
        settings.get(
            "source_video",
            "",
        )
        or ""
    )
    selection_start = as_float(
        settings.get(
            "selection_start",
            0.0,
        )
    )
    selection_end = as_float(
        settings.get(
            "selection_end",
            selection_start,
        ),
        selection_start,
    )

    editor_sfx_clips = clips_of_kind(
        plan,
        "SFX",
        active_only=False,
    )
    use_editor_plan = bool(
        editor_sfx_clips
        and editor_plan_context_matches(
            plan,
            source_video,
            selection_start,
            selection_end,
        )
    )

    if not use_editor_plan:
        return list(automatic_events), plan

    final_events: list[dict[str, Any]] = []
    for clip in clips_of_kind(
        plan,
        "SFX",
        active_only=True,
    ):
        if bool(
            clip.get(
                "deleted",
                False,
            )
        ):
            continue
        event = event_from_sfx_clip(
            clip
        )
        if event is not None:
            final_events.append(
                event
            )

    final_events.sort(
        key=lambda event: float(
            event.get(
                "start",
                0.0,
            )
        )
    )

    return final_events, plan


def mix_sfx_into_video(
    video: Path,
    events: list[dict[str, Any]],
    warnings: list[str],
) -> bool:

    if not events:
        return False

    video_duration = probe_duration(
        video
    )
    if video_duration <= 0:
        warnings.append(
            "Could not probe rendered video duration; skipped SFX mix."
        )
        return False

    video_has_audio = has_audio_stream(
        video
    )

    command = [
        ffmpeg_binary(),
        "-y",
        "-i",
        str(video),
    ]

    if video_has_audio:
        base_label = "[0:a:0]"
        next_input_index = 1
    else:
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{video_duration:.3f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )
        base_label = "[1:a:0]"
        next_input_index = 2

    for event in events:
        command.extend(
            [
                "-i",
                str(
                    event.get(
                        "asset_path",
                        "",
                    )
                ),
            ]
        )

    filter_parts = [
        (
            f"{base_label}"
            "aformat=sample_rates=48000:channel_layouts=stereo,"
            "volume=1.0[base]"
        )
    ]
    mix_inputs = [
        "[base]"
    ]

    for offset, event in enumerate(
        events
    ):
        input_index = next_input_index + offset
        delay_ms = max(
            0,
            int(
                round(
                    float(
                        event.get(
                            "start",
                            0.0,
                        )
                    )
                    * 1000
                )
            ),
        )
        duration = max(
            0.06,
            float(
                event.get(
                    "duration",
                    0.25,
                )
            ),
        )
        trim_in = max(
            0.0,
            float(
                event.get(
                    "trim_in",
                    0.0,
                )
                or 0.0
            ),
        )
        volume = max(
            0.0,
            min(
                0.8,
                float(
                    event.get(
                        "volume",
                        0.25,
                    )
                ),
            ),
        )
        label = f"sfx{offset}"
        filter_parts.append(
            (
                f"[{input_index}:a:0]"
                f"atrim=start={trim_in:.3f}:duration={duration:.3f},"
                "asetpts=PTS-STARTPTS,"
                "aformat=sample_rates=48000:channel_layouts=stereo,"
                f"volume={volume:.4f},"
                f"adelay={delay_ms}|{delay_ms}"
                f"[{label}]"
            )
        )
        mix_inputs.append(
            f"[{label}]"
        )

    filter_parts.append(
        (
            "".join(
                mix_inputs
            )
            + "amix="
            + f"inputs={len(mix_inputs)}:"
            + "duration=first:"
            + "dropout_transition=0:"
            + "normalize=0,"
            + "alimiter=limit=0.92"
            + "[mixed]"
        )
    )

    filter_complex = ";".join(
        filter_parts
    )

    if TEMP_VIDEO_PATH.exists():
        TEMP_VIDEO_PATH.unlink()

    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[mixed]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(TEMP_VIDEO_PATH),
        ]
    )

    result = run_quiet(
        command
    )

    if result.returncode != 0 or not TEMP_VIDEO_PATH.exists():
        stderr_text = result.stderr.strip()
        stderr_tail = stderr_text[-1800:] if stderr_text else "unknown FFmpeg error"
        warnings.append(
            "FFmpeg SFX mix failed; video left unchanged. "
            f"Error tail: {stderr_tail}"
        )
        try:
            TEMP_VIDEO_PATH.unlink(
                missing_ok=True
            )
        except OSError:
            pass
        return False

    os.replace(
        TEMP_VIDEO_PATH,
        video,
    )
    return True


def base_plan(
    energy: str,
    mode: str,
) -> dict[str, Any]:

    return {
        "version": 1,
        "applied": False,
        "mode": mode,
        "edit_energy": energy,
        "output_time_basis": "final_rendered_timeline",
        "local_sfx_folder": str(
            SFX_DIR
        ),
        "generated_sfx_folder": str(
            GENERATED_SFX_DIR
        ),
        "event_count": 0,
        "events": [],
        "skipped": [],
        "warnings": [],
        "mix": {
            "applied": False,
            "video": str(
                VIDEO_PATH
            ),
        },
    }


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="Plan or mix ShortsFactory sound effects.",
    )
    parser.add_argument(
        "--editor-plan",
        action="store_true",
        help="Create editable SFX clips without mixing a rendered video.",
    )
    parser.add_argument(
        "--selection-start",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--selection-end",
        type=float,
        default=None,
    )
    return parser.parse_args()


def transcript_category_and_score(
    text: str,
    index: int,
) -> tuple[str, float, str]:

    normalized = " ".join(
        str(
            text
            or ""
        ).lower().split()
    )

    if any(
        token in normalized
        for token in (
            "money",
            "cash",
            "coin",
            "coins",
            "dollar",
            "prize",
            "rich",
        )
    ):
        return "money", 72.0, "money/reward dialogue beat"

    if any(
        token in normalized
        for token in (
            "wow",
            "whoa",
            "what?",
            "really",
            "seriously",
            "no way",
            "oh my",
            "crazy",
        )
    ) or "!" in text:
        return "reaction", 66.0, "reaction dialogue beat"

    if any(
        token in normalized
        for token in (
            "yes",
            "yeah",
            "finally",
            "got it",
            "got you",
            "love",
            "great",
            "good",
            "win",
            "won",
            "success",
        )
    ):
        return "bell", 62.0, "positive dialogue beat"

    if any(
        token in normalized
        for token in (
            "wrong",
            "fail",
            "failed",
            "never",
            "lost",
            "gone",
            "bad",
            "error",
        )
    ):
        return "beep", 58.0, "negative dialogue beat"

    if "?" in text:
        return "ding", 60.0, "question/reveal beat"

    # Ordinary dialogue beats intentionally alternate between small bright
    # accents rather than hammering every line with the same sound family.
    if index % 3 == 2:
        return "bell", 45.0, "dialogue beat"
    if index % 2:
        return "pop", 43.0, "dialogue beat"
    return "ding", 46.0, "dialogue beat"


def transcript_editor_candidates(
    selection_start: float,
    selection_end: float,
) -> list[dict[str, Any]]:

    transcript = read_json(
        SUBTITLES_PATH
    )
    raw_segments = transcript.get(
        "segments",
        [],
    )
    if not isinstance(
        raw_segments,
        list,
    ):
        return []

    candidates: list[dict[str, Any]] = []
    for index, segment in enumerate(
        raw_segments
    ):
        if not isinstance(
            segment,
            dict,
        ):
            continue

        start = as_float(
            segment.get(
                "start",
                0.0,
            )
        )
        end = as_float(
            segment.get(
                "end",
                start,
            ),
            start,
        )
        if (
            end <= selection_start
            or start >= selection_end
        ):
            continue

        text = str(
            segment.get(
                "text",
                "",
            )
            or ""
        ).strip()
        if not text:
            continue

        category, score, reason = transcript_category_and_score(
            text,
            index,
        )

        # Accent the end of the spoken thought rather than spraying effects
        # at arbitrary percentages of the timeline. Keep a small lead-in so
        # the sound lands with the last word instead of noticeably after it.
        accent_time = max(
            selection_start,
            min(
                selection_end - 0.05,
                end - 0.08,
            ),
        )
        if accent_time < selection_start or accent_time >= selection_end:
            continue

        candidates.append(
            {
                "start": round(
                    accent_time,
                    3,
                ),
                "end": round(
                    min(
                        selection_end,
                        accent_time
                        + CATEGORY_DURATIONS.get(
                            category,
                            0.25,
                        ),
                    ),
                    3,
                ),
                "category": category,
                "label": CATEGORY_LABELS.get(
                    category,
                    category.upper(),
                ),
                "score": score,
                "reason": reason,
                "source_event_type": "transcript_accent",
                "source_treatment": "accent",
                "recipe": "transcript_semantic",
                "trigger": text[:120],
                "stack_id": "",
                "hero": score >= 70.0,
                "time_basis": "source",
            }
        )

    return candidates


def fallback_editor_candidates(
    selection_start: float,
    selection_end: float,
    energy: str,
) -> list[dict[str, Any]]:

    duration = max(
        0.0,
        selection_end
        - selection_start,
    )
    if duration <= 0.5:
        return []

    # Fallback planning has no semantic edit events to work from, so avoid
    # pretending every clip entrance needs a whoosh and every ending needs an
    # impact. Use light accents instead; real motion/slam events from the
    # visual plan can still request those heavier categories explicitly.
    first_start = (
        selection_start
        + duration
        * 0.38
    )
    candidates = [
        {
            "start": round(
                first_start,
                3,
            ),
            "end": round(
                min(
                    selection_end,
                    first_start
                    + CATEGORY_DURATIONS["ding"],
                ),
                3,
            ),
            "category": "ding",
            "label": CATEGORY_LABELS["ding"],
            "score": 58.0,
            "reason": "editor fallback emphasis",
            "source_event_type": "editor_fallback",
            "source_treatment": "accent",
            "recipe": "editor_fallback",
            "trigger": "fallback_emphasis",
            "stack_id": "",
            "hero": False,
            "time_basis": "source",
        }
    ]

    if duration >= 3.0:
        second_start = (
            selection_start
            + duration
            * 0.68
        )
        candidates.append(
            {
                "start": round(
                    second_start,
                    3,
                ),
                "end": round(
                    min(
                        selection_end,
                        second_start
                        + CATEGORY_DURATIONS["pop"],
                    ),
                    3,
                ),
                "category": "pop",
                "label": CATEGORY_LABELS["pop"],
                "score": 46.0,
                "reason": "editor fallback light accent",
                "source_event_type": "editor_fallback",
                "source_treatment": "accent",
                "recipe": "editor_fallback",
                "trigger": "fallback_accent",
                "stack_id": "",
                "hero": False,
                "time_basis": "source",
            }
        )

    if duration >= 5.0 or energy == "MAXIMUM":
        bell_start = min(
            selection_end
            - 0.2,
            selection_start
            + duration
            * 0.88,
        )
        candidates.append(
            {
                "start": round(
                    max(
                        selection_start,
                        bell_start,
                    ),
                    3,
                ),
                "end": round(
                    min(
                        selection_end,
                        bell_start
                        + CATEGORY_DURATIONS["bell"],
                    ),
                    3,
                ),
                "category": "bell",
                "label": CATEGORY_LABELS["bell"],
                "score": 62.0,
                "reason": "editor fallback payoff chime",
                "source_event_type": "editor_fallback",
                "source_treatment": "accent",
                "recipe": "editor_fallback",
                "trigger": "fallback_payoff",
                "stack_id": "",
                "hero": False,
                "time_basis": "source",
            }
        )

    return candidates


def editor_candidates(
    selection_start: float,
    selection_end: float,
    energy: str,
) -> list[dict[str, Any]]:

    duration = max(
        0.0,
        selection_end
        - selection_start,
    )
    visual_plan = read_json(
        VISUAL_EDIT_PLAN_PATH
    )
    temporal_plan = read_json(
        TEMPORAL_EDIT_PLAN_PATH
    )

    candidates = collapse_stacks(
        candidates_from_plan(
            visual_plan,
            temporal_plan,
        )
    )

    source_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        start = as_float(
            candidate.get(
                "start",
                0.0,
            )
        )
        if start < -0.05 or start > duration + 0.05:
            continue
        item = dict(
            candidate
        )
        item["start"] = round(
            selection_start
            + start,
            3,
        )
        item["end"] = round(
            selection_start
            + as_float(
                candidate.get(
                    "end",
                    start,
                ),
                start,
            ),
            3,
        )
        item["time_basis"] = "source"
        source_candidates.append(
            item
        )

    transcript_candidates = transcript_editor_candidates(
        selection_start,
        selection_end,
    )

    # Visual/temporal events are often sparse and can cluster in the first few
    # seconds. Add dialogue-beat candidates from the already-loaded transcript
    # so automatic SFX can continue across the full selected clip. select_events
    # still enforces the LOW/PUNCHY/MAXIMUM event cap and spacing.
    combined = source_candidates + transcript_candidates
    if combined:
        return combined

    return fallback_editor_candidates(
        selection_start,
        selection_end,
        energy,
    )


def write_editor_sfx_plan(
    selection_start: float,
    selection_end: float,
    energy: str,
    mode: str,
) -> dict[str, Any]:

    plan = base_plan(
        energy,
        mode,
    )
    warnings: list[str] = plan["warnings"]

    if mode == "OFF":
        plan["reason"] = "sound effects disabled in render settings"
        write_plan(
            plan
        )
        return plan

    selected = select_events(
        editor_candidates(
            selection_start,
            selection_end,
            energy,
        ),
        energy,
        selection_start=selection_start,
        selection_end=selection_end,
    )
    local_assets = index_local_assets()
    plan["local_asset_counts"] = local_asset_counts(
        local_assets
    )
    prepared, skipped = prepare_events(
        selected,
        energy,
        local_assets,
        warnings,
    )

    editor_clips = [
        sfx_clip_from_event(
            {
                **event,
                "time_basis": "source",
                "origin": "automatic",
                "manual_override": False,
            }
        )
        for event in prepared
    ]
    settings = load_render_settings()
    source_video = str(
        settings.get(
            "source_video",
            "",
        )
        or ""
    )
    editor_plan = load_editor_asset_plan()
    context_matches = editor_plan_context_matches(
        editor_plan,
        source_video,
        selection_start,
        selection_end,
    )
    editor_plan = set_editor_plan_context(
        editor_plan,
        source_video,
        selection_start,
        selection_end,
        clear_clips_on_change=not context_matches,
    )

    retained = [
        clip
        for clip in editor_plan.get(
            "clips",
            [],
        )
        if isinstance(clip, dict)
        and str(
            clip.get(
                "kind",
                "",
            )
            or ""
        ).upper() != "SFX"
    ]
    retained.extend(
        editor_clips
    )
    editor_plan["clips"] = retained
    save_editor_asset_plan(
        editor_plan
    )

    plan.update(
        {
            "applied": False,
            "editor_plan_only": True,
            "selection_start": round(
                selection_start,
                3,
            ),
            "selection_end": round(
                selection_end,
                3,
            ),
            "candidate_count": len(
                selected
            ),
            "event_count": len(
                editor_clips
            ),
            "events": prepared,
            "final_events": [
                event
                for event in (
                    event_from_sfx_clip(
                        clip
                    )
                    for clip in clips_of_kind(
                        editor_plan,
                        "SFX",
                        active_only=True,
                    )
                )
                if event is not None
            ],
            "skipped": skipped,
            "editor_asset_plan": str(
                EDITOR_ASSET_PLAN_PATH
            ),
            "mix": {
                "applied": False,
                "video": str(
                    VIDEO_PATH
                ),
                "reason": "editor preview plan only",
            },
        }
    )
    write_plan(
        plan
    )
    return plan


def main() -> int:
    args = parse_args()

    settings = load_render_settings()
    energy = normalize_energy(
        settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )
    mode = normalize_sfx_mode(
        settings.get(
            "sfx_mode",
            "AUTO",
        )
    )
    plan = base_plan(
        energy,
        mode,
    )
    warnings: list[str] = plan["warnings"]

    print()
    print("ShortsFactory SFX engine starting...")
    print(f"Sound FX mode: {mode}")
    print(f"Edit energy: {energy}")

    if args.editor_plan:
        selection_start = (
            args.selection_start
            if args.selection_start is not None
            else as_float(
                settings.get(
                    "selection_start",
                    0.0,
                )
            )
        )
        selection_end = (
            args.selection_end
            if args.selection_end is not None
            else as_float(
                settings.get(
                    "selection_end",
                    selection_start,
                ),
                selection_start,
            )
        )
        editor_plan = write_editor_sfx_plan(
            float(
                selection_start
            ),
            float(
                selection_end
            ),
            energy,
            mode,
        )
        print(
            f"Editor SFX clips: {editor_plan.get('event_count', 0)}"
        )
        if editor_plan.get(
            "warnings"
        ):
            print("Warnings:")
            for warning in editor_plan.get(
                "warnings",
                [],
            ):
                print(f"- {warning}")
        return 0

    if mode == "OFF":
        plan["reason"] = "sound effects disabled in render settings"
        write_plan(
            plan
        )
        print("Sound FX disabled; wrote empty sfx_plan.json.")
        return 0

    if not VIDEO_PATH.exists():
        warnings.append(
            f"Rendered video not found: {VIDEO_PATH}"
        )
        write_plan(
            plan
        )
        print("Rendered video missing; skipped SFX.")
        return 0

    visual_plan = read_json(
        VISUAL_EDIT_PLAN_PATH
    )
    temporal_plan = read_json(
        TEMPORAL_EDIT_PLAN_PATH
    )

    candidates = collapse_stacks(
        candidates_from_plan(
            visual_plan,
            temporal_plan,
        )
    )
    selected = select_events(
        candidates,
        energy,
    )
    local_assets = index_local_assets()
    plan["local_asset_counts"] = local_asset_counts(
        local_assets
    )
    prepared, skipped = prepare_events(
        selected,
        energy,
        local_assets,
        warnings,
    )

    final_events, editor_asset_plan = merge_sfx_with_editor_plan(
        prepared
    )

    mixed = mix_sfx_into_video(
        VIDEO_PATH,
        final_events,
        warnings,
    )

    plan.update(
        {
            "applied": bool(
                mixed
            ),
            "candidate_count": len(
                candidates
            ),
            "event_count": len(
                final_events
            ),
            "events": prepared,
            "final_events": final_events,
            "skipped": skipped,
            "editor_asset_plan": str(
                EDITOR_ASSET_PLAN_PATH
            ),
            "mix": {
                "applied": bool(
                    mixed
                ),
                "video": str(
                    VIDEO_PATH
                ),
                "dialogue_protection": [
                    "SFX are selected after visual stack collapsing.",
                    "Events closer than the energy spacing window are skipped.",
                    "Non-hero dialogue-adjacent accents are gain-reduced.",
                    "Final audio mix uses a limiter at 0.92 peak.",
                ],
            },
        }
    )

    write_plan(
        plan
    )

    print(f"Planned SFX events: {len(prepared)}")
    if skipped:
        print(f"Skipped SFX events: {len(skipped)}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    if mixed:
        print(f"SFX mixed into: {VIDEO_PATH}")
    else:
        print("No SFX mix applied; video left unchanged.")

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
