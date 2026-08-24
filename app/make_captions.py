"""
STEP 7 of the render pipeline: builds the karaoke-style ASS caption file
(word-by-word highlighting, semantic emphasis sizing/color/pop) and picks
the sparse set of emoji reaction events for the clip (choose_emoji_events()),
writing output/captions.ass and output/emoji_events.json. Also owns the
caption block position override (\\pos() tag, clamped to the safe drag
range shared with the GUI preview -- see caption_position_override_tag()
and render.py's clamp_caption_drag_position()) and the merge logic that
carries a manually-dragged emoji position/content forward across a
re-render (apply_emoji_position_overrides()). Note: the actual default
caption position at burn time comes from render.py's force_style, not
this file's own MARGIN_V -- see the comment on that constant below.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    from .visual_emphasis import (
        DEFAULT_ENERGY,
        VISUAL_EDIT_PLAN_PATH,
        build_visual_edit_plan,
        classify_word,
        energy_profile,
        load_render_settings,
        normalize_energy,
        write_visual_edit_plan,
    )
except ImportError:
    from visual_emphasis import (
        DEFAULT_ENERGY,
        VISUAL_EDIT_PLAN_PATH,
        build_visual_edit_plan,
        classify_word,
        energy_profile,
        load_render_settings,
        normalize_energy,
        write_visual_edit_plan,
    )

try:
    from .emoji_overlay import (
        emoji_pixel_to_fraction,
        event_default_position_px,
    )
except ImportError:
    from emoji_overlay import (
        emoji_pixel_to_fraction,
        event_default_position_px,
    )

try:
    from .render import OUTPUT_HEIGHT, OUTPUT_WIDTH, clamp_caption_drag_position
except ImportError:
    from render import OUTPUT_HEIGHT, OUTPUT_WIDTH, clamp_caption_drag_position

try:
    from .pipeline_paths import (
        CAPTIONS_PATH as OUTPUT_PATH,
        EMOJI_EVENTS_PATH as EMOJI_OUTPUT_PATH,
        SUBTITLES_PATH as INPUT_PATH,
    )
except ImportError:
    from pipeline_paths import (
        CAPTIONS_PATH as OUTPUT_PATH,
        EMOJI_EVENTS_PATH as EMOJI_OUTPUT_PATH,
        SUBTITLES_PATH as INPUT_PATH,
    )


ROOT = Path(__file__).resolve().parent.parent

EMOJI_DIR = ROOT / "assets" / "emoji"


# ============================================================
# SETTINGS
# ============================================================

MIN_WORDS = 2
MAX_WORDS = 3

FONT_SIZE = 78

# Written into each Dialogue line's own MarginV field below, but this is
# NOT what actually determines the default caption position in a real
# render: render.py's burn_captions() (STEP 8) burns with its own
# force_style='...MarginV=980...' (CAPTION_SAFE_MARGIN_BOTTOM), which
# completely overrides a Dialogue line's per-line MarginV. This constant
# only matters if captions.ass is ever rendered directly without going
# through that burn step (e.g. previewed in a standalone media player).
# A caption_position_x/y override (caption_position_override_tag() below)
# is unaffected either way -- ASS \pos() bypasses margin-based placement
# entirely, regardless of which MarginV would otherwise apply. See
# gui_app/mixins/caption_preview.py for the GUI-side placement-editor
# preview, which correctly derives its default from render.py's real
# CAPTION_SAFE_MARGIN_* values, not this constant.
MARGIN_V = 250

# Only two caption colors.
WHITE = "&H00FFFFFF"
YELLOW = "&H0000FFFF"

INLINE_WHITE = r"\c&HFFFFFF&"
INLINE_YELLOW = r"\c&H00FFFF&"

INLINE_ACCENTS = {
    "bone": r"\c&HD8E8F3&",
    "cold": r"\c&HFFD56D&",
    "danger": r"\c&H5050E8&",
    "green": r"\c&H63D978&",
    "magenta": r"\c&HD65CFF&",
    "warm": r"\c&H49B8FF&",
}

CAPTION_SIZES = {
    "LOW": {
        "NORMAL": FONT_SIZE,
        "EMPHASIS": 86,
        "IMPACT": 98,
        "EXTREME": 110,
    },
    "PUNCHY": {
        "NORMAL": FONT_SIZE,
        "EMPHASIS": 96,
        "IMPACT": 116,
        "EXTREME": 142,
    },
    "MAXIMUM": {
        "NORMAL": 82,
        "EMPHASIS": 112,
        "IMPACT": 146,
        "EXTREME": 178,
    },
}

CAPTION_POP_TAGS = {
    "NORMAL": "",
    "EMPHASIS": (
        r"\fscx92\fscy92"
        r"\t(0,110,\fscx108\fscy108)"
        r"\t(110,230,\fscx100\fscy100)"
    ),
    "IMPACT": (
        r"\fscx82\fscy82"
        r"\t(0,95,\fscx122\fscy122)"
        r"\t(95,260,\fscx100\fscy100)"
    ),
    "EXTREME": (
        r"\fscx70\fscy70"
        r"\frz-3"
        r"\t(0,85,\fscx142\fscy142\frz2)"
        r"\t(85,290,\fscx100\fscy100\frz0)"
    ),
}



# ============================================================
# EMOJI ASSOCIATIONS
# ============================================================
#
# These are Unicode codepoints written as Python escapes so
# Windows encoding cannot corrupt them.
#
# We intentionally keep this list relatively small.
# Emojis themselves are rendered separately as PNG images.
#

EMOJI_MAP = {
    "crazy": r"\U0001f92f",
    "insane": r"\U0001f92f",
    "insanity": r"\U0001f92f",

    "weird": r"\U0001f928",
    "strange": r"\U0001f928",
    "odd": r"\U0001f928",

    "shocking": r"\U0001f632",
    "shocked": r"\U0001f632",
    "surprising": r"\U0001f632",
    "surprise": r"\U0001f632",

    "wow": r"\U0001f62e",
    "omg": r"\U0001f62d",

    "funny": r"\U0001f602",
    "laugh": r"\U0001f602",
    "laughing": r"\U0001f602",

    "love": r"\u2764\ufe0f",
    "loved": r"\u2764\ufe0f",

    "fire": r"\U0001f525",
    "hot": r"\U0001f525",

    "dead": r"\U0001f480",
    "died": r"\U0001f480",
    "dying": r"\U0001f480",

    "money": r"\U0001f4b0",
    "rich": r"\U0001f4b0",

    "phone": r"\U0001f4f1",
    "computer": r"\U0001f4bb",
    "internet": r"\U0001f310",
    "online": r"\U0001f310",
    "video": r"\U0001f3a5",
    "music": r"\U0001f3b5",
    "game": r"\U0001f3ae",

    "danger": r"\u26a0\ufe0f",
    "dangerous": r"\u26a0\ufe0f",
    "warning": r"\u26a0\ufe0f",

    "problem": r"\U0001f6a8",
    "trouble": r"\U0001f6a8",

    "fight": r"\U0001f4a5",
    "attack": r"\U0001f4a5",
    "explode": r"\U0001f4a5",
    "explosion": r"\U0001f4a5",

    "secret": r"\U0001f92b",

    "think": r"\U0001f914",
    "thought": r"\U0001f4ad",

    "happy": r"\U0001f60a",
    "sad": r"\U0001f622",
    "angry": r"\U0001f621",
    "mad": r"\U0001f621",
    "scared": r"\U0001f628",
    "afraid": r"\U0001f628",

    "confused": r"\U0001f635",
    "confusing": r"\U0001f635",

    "awkward": r"\U0001f633",
    "embarrassed": r"\U0001f633",

    "look": r"\U0001f440",
    "looked": r"\U0001f440",
    "see": r"\U0001f440",
    "saw": r"\U0001f440",

    "listen": r"\U0001f442",
    "heard": r"\U0001f442",
    "hear": r"\U0001f442",

    "stop": r"\U0001f6d1",
    "wait": r"\u270b",

    "fast": r"\u26a1",
    "slow": r"\U0001f422",

    "true": r"\u2705",
    "right": r"\u2705",
    "wrong": r"\u274c",
    "never": r"\U0001f6ab",

    # Common story / reaction vocabulary.
    "old": r"\U0001f4fc",
    "older": r"\U0001f4fc",
    "ancient": r"\U0001f3db\ufe0f",
    "house": r"\U0001f3e0",
    "home": r"\U0001f3e0",
    "car": r"\U0001f697",
    "drive": r"\U0001f697",
    "door": r"\U0001f6aa",
    "run": r"\U0001f3c3",
    "running": r"\U0001f3c3",
    "big": r"\U0001f4a5",
    "huge": r"\U0001f4a5",
    "tiny": r"\U0001f90f",
    "small": r"\U0001f90f",
    "time": r"\u23f0",
    "night": r"\U0001f319",
    "dark": r"\U0001f311",
    "light": r"\U0001f4a1",
    "idea": r"\U0001f4a1",
    "remember": r"\U0001f9e0",
    "forgot": r"\U0001f9e0",
    "forget": r"\U0001f9e0",
    "yes": r"\u2705",
    "no": r"\u274c",
}


# Intentional sparse placement.
#
# We choose emoji moments after scanning the full transcript rather
# than randomly deciding caption-by-caption.
MIN_EMOJI_EVENTS = 1
MAX_EMOJI_EVENTS = 3
MIN_EMOJI_SPACING_SECONDS = 4.5
EMOJI_DURATION_SECONDS = 1.50

# Neutral fallback used only when the dialogue contains no mapped
# semantic emoji opportunity at all.
FALLBACK_EMOJI = r"\U0001f440"

LOCAL_ASSET_EXTENSIONS = {
    ".gif",
    ".png",
}

CODEPOINT_ASSET_RE = re.compile(
    r"^[0-9a-f]+(?:-[0-9a-f]+)*$",
    re.IGNORECASE,
)

ASSET_DESCRIPTION_STOPWORDS = {
    "joe",
}

ASSET_TRIGGER_ALIASES = {
    "ai": {
        "ai",
        "bot",
        "computer",
        "internet",
        "online",
        "robot",
    },
    "baby": {
        "baby",
        "child",
        "kid",
    },
    "boo": {
        "afraid",
        "boo",
        "scare",
        "scared",
        "spooky",
    },
    "cathink": {
        "confused",
        "think",
        "thinking",
        "thought",
        "wonder",
    },
    "dead": {
        "dead",
        "died",
        "dying",
        "rough",
    },
    "discombobulater": {
        "confused",
        "confusing",
        "discombobulated",
        "weird",
    },
    "ermwhatthesigma": {
        "confused",
        "erm",
        "sigma",
        "what",
        "weird",
    },
    "hahah": {
        "funny",
        "haha",
        "laugh",
        "laughing",
        "lol",
    },
    "joesad": {
        "cry",
        "sad",
        "sorry",
        "upset",
    },
    "joewaiting": {
        "patience",
        "slow",
        "wait",
        "waiting",
    },
    "larp": {
        "fake",
        "larp",
        "pretend",
    },
    "lmfao": {
        "funny",
        "haha",
        "laugh",
        "laughing",
        "lmfao",
        "lol",
    },
    "mischievous": {
        "crazy",
        "mischievous",
        "scheme",
        "sneaky",
        "weird",
    },
    "sad": {
        "cry",
        "sad",
        "sorry",
        "upset",
    },
    "shhh": {
        "quiet",
        "secret",
        "shh",
        "silent",
    },
    "shrug": {
        "confused",
        "maybe",
        "perhaps",
        "shrug",
        "unsure",
    },
    "skull": {
        "dead",
        "died",
        "dying",
        "funny",
        "laugh",
        "lol",
        "rough",
    },
    "spam": {
        "spam",
        "steve",
    },
    "talktowall": {
        "ignore",
        "ignored",
        "listen",
        "nobody",
        "talk",
        "wall",
    },
    "thinking": {
        "confused",
        "think",
        "thinking",
        "thought",
        "wonder",
    },
    "thumbs": {
        "agree",
        "good",
        "ok",
        "okay",
        "right",
        "true",
        "yes",
    },
    "timeisticking": {
        "clock",
        "countdown",
        "hurry",
        "late",
        "time",
        "today",
    },
    "waiting": {
        "patience",
        "slow",
        "wait",
        "waiting",
    },
    "whoknows": {
        "confused",
        "know",
        "knows",
        "maybe",
        "perhaps",
        "unsure",
        "what",
        "who",
        "why",
    },
    "wow": {
        "omg",
        "shock",
        "shocked",
        "shocking",
        "surprise",
        "surprising",
        "wow",
    },
}

LOCAL_ASSET_CACHE: list[dict] | None = None


# ============================================================
# HELPERS
# ============================================================

def ass_time(seconds: float) -> str:
    total_centiseconds = max(0, round(seconds * 100))

    hours = total_centiseconds // 360000
    minutes = (total_centiseconds % 360000) // 6000
    remaining = total_centiseconds % 6000

    seconds_part = remaining // 100
    centiseconds = remaining % 100

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{seconds_part:02d}."
        f"{centiseconds:02d}"
    )


def caption_position_override_tag(render_settings: dict) -> str:
    """
    ASS \\pos(x,y) override tag for a manually-dragged caption block
    position, or "" to use the style's default MarginV-based placement.

    (x, y) is the anchor point the style's own Alignment value (2 =
    bottom-center, unchanged by this) is measured from, so this only needs
    to override position, not alignment.
    """

    position_x = render_settings.get("caption_position_x")
    position_y = render_settings.get("caption_position_y")

    if position_x is None or position_y is None:
        return ""

    try:
        raw_x = max(0.0, min(1.0, float(position_x)))
        raw_y = max(0.0, min(1.0, float(position_y)))
    except (TypeError, ValueError):
        return ""

    # Defensive clamp, independent of the GUI's own drag clamp -- a value
    # saved before the drag range was tightened, or edited directly in
    # render_settings.json, must not be able to burn a caption into the
    # zone a platform's own UI would cover.
    fraction_x, fraction_y = clamp_caption_drag_position(raw_x, raw_y)
    x = fraction_x * OUTPUT_WIDTH
    y = fraction_y * OUTPUT_HEIGHT

    return f"{{\\pos({x:.1f},{y:.1f})}}"


def escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def clean_word(word: str) -> str:
    return re.sub(r"[^a-zA-Z']", "", word).lower()


def clean_asset_word(word: str) -> str:
    return re.sub(r"[^a-zA-Z0-9']", "", word).lower()


def is_codepoint_asset(path: Path) -> bool:
    return bool(
        CODEPOINT_ASSET_RE.fullmatch(
            path.stem
        )
    )


def asset_description(path: Path) -> str:
    description = re.sub(
        r"^\d+[-_\s]+",
        "",
        path.stem.lower(),
    )

    description = re.sub(
        r"[-_]+",
        " ",
        description,
    )

    return re.sub(
        r"\s+",
        " ",
        description,
    ).strip()


def asset_words_from_description(
    description: str,
) -> tuple[set[str], set[str]]:
    raw_words = {
        clean_asset_word(word)
        for word in re.split(
            r"[^a-zA-Z0-9']+",
            description,
        )
    }

    description_words = {
        word
        for word in raw_words
        if (
            word
            and not word.isdigit()
            and word not in ASSET_DESCRIPTION_STOPWORDS
        )
    }

    trigger_words = set(
        description_words
    )

    compact_description = re.sub(
        r"[^a-zA-Z0-9']+",
        "",
        description.lower(),
    )

    for needle, aliases in ASSET_TRIGGER_ALIASES.items():

        if (
            needle in description_words
            or needle in compact_description
        ):
            trigger_words.update(
                aliases
            )

    return (
        description_words,
        trigger_words,
    )


def load_local_reaction_assets() -> list[dict]:
    global LOCAL_ASSET_CACHE

    if LOCAL_ASSET_CACHE is not None:
        return LOCAL_ASSET_CACHE

    assets: list[dict] = []

    if not EMOJI_DIR.exists():
        LOCAL_ASSET_CACHE = assets
        return assets

    for path in sorted(
        EMOJI_DIR.iterdir(),
        key=lambda item: item.name.lower(),
    ):

        if (
            not path.is_file()
            or path.suffix.lower()
            not in LOCAL_ASSET_EXTENSIONS
            or is_codepoint_asset(path)
        ):
            continue

        description = asset_description(
            path
        )

        if not description:
            continue

        description_words, trigger_words = (
            asset_words_from_description(
                description
            )
        )

        if not trigger_words:
            continue

        assets.append(
            {
                "path": path,
                "description": description,
                "description_words": description_words,
                "trigger_words": trigger_words,
            }
        )

    LOCAL_ASSET_CACHE = assets
    return assets


def asset_match_score(
    asset: dict,
    word: str,
) -> tuple[int, int, int, str]:
    description_words = asset.get(
        "description_words",
        set(),
    )

    trigger_words = asset.get(
        "trigger_words",
        set(),
    )

    if word in description_words:
        strength = 3
    elif word in trigger_words:
        strength = 2
    elif word in str(
        asset.get(
            "description",
            "",
        )
    ):
        strength = 1
    else:
        strength = 0

    animated_bonus = (
        1
        if Path(
            asset["path"]
        ).suffix.lower()
        == ".gif"
        else 0
    )

    specificity = -len(
        trigger_words
    )

    return (
        strength,
        animated_bonus,
        specificity,
        str(
            asset["path"].name
        ).lower(),
    )


def find_local_reaction_asset(
    word: str,
) -> dict | None:
    matches = [
        asset
        for asset in load_local_reaction_assets()
        if word in asset.get(
            "trigger_words",
            set(),
        )
    ]

    if not matches:
        return None

    return max(
        matches,
        key=lambda asset: asset_match_score(
            asset,
            word,
        ),
    )


def relative_asset_path(path: Path) -> str:
    return str(
        path.relative_to(
            ROOT
        )
    ).replace(
        "\\",
        "/",
    )


def find_emoji(words: list[dict]) -> dict:
    """
    Return emoji event fields. Empty dict means no semantic match.
    """
    for word in words:
        cleaned = clean_word(
            str(
                word.get(
                    "word",
                    "",
                )
            )
        )

        if not cleaned:
            continue

        local_asset = find_local_reaction_asset(
            cleaned
        )

        if local_asset:
            return {
                "emoji": local_asset["description"],
                "matched_word": cleaned,
                "asset_path": relative_asset_path(
                    local_asset["path"]
                ),
                "asset_description": local_asset["description"],
                "asset_type": "local",
            }

        if cleaned in EMOJI_MAP:
            return {
                "emoji": EMOJI_MAP[cleaned],
                "matched_word": cleaned,
            }

    return {}


def choose_emoji_events(
    candidates: list[dict],
    words: list[dict],
    energy: str = DEFAULT_ENERGY,
) -> list[dict]:
    """
    Pick a sparse, deterministic set of emoji moments.

    Priorities:
    - semantic keyword matches
    - avoid repeated emoji
    - spread moments across the Short
    - target 1-3 total events
    """

    if not words:
        return []

    clip_start = float(
        words[0].get(
            "start",
            0.0,
        )
        or 0.0
    )

    clip_end = float(
        words[-1].get(
            "end",
            clip_start,
        )
        or clip_start
    )

    clip_duration = max(
        0.0,
        clip_end - clip_start,
    )

    max_emoji_events = int(
        energy_profile(
            energy
        ).get(
            "emoji_max_events",
            MAX_EMOJI_EVENTS,
        )
    )

    if clip_duration < 8:
        target_count = 1
    elif clip_duration < 22:
        target_count = 2
    else:
        target_count = max_emoji_events

    target_count = max(
        MIN_EMOJI_EVENTS,
        min(
            max_emoji_events,
            target_count,
        ),
    )

    selected: list[dict] = []
    used_emojis: set[str] = set()

    # Candidates are already chronological. Give a slight advantage
    # to unique emoji types and then enforce visual spacing.
    for candidate in candidates:

        emoji = candidate["emoji"]
        emoji_identity = str(
            candidate.get(
                "asset_path",
                emoji,
            )
        )
        start = float(
            candidate["start"]
        )

        if emoji_identity in used_emojis:
            continue

        if any(
            abs(
                start
                - float(
                    event["start"]
                )
            )
            < MIN_EMOJI_SPACING_SECONDS
            for event in selected
        ):
            continue

        event = {
            "start": start,
            "end": min(
                clip_end,
                start
                + EMOJI_DURATION_SECONDS,
            ),
            "emoji": emoji,
            "matched_word": candidate.get(
                "matched_word",
                "",
            ),
        }

        for optional_key in (
            "asset_path",
            "asset_description",
            "asset_type",
        ):

            if candidate.get(
                optional_key
            ):
                event[optional_key] = candidate[
                    optional_key
                ]

        default_x, default_y = event_default_position_px(
            len(selected)
        )
        (
            event["position_x"],
            event["position_y"],
        ) = emoji_pixel_to_fraction(
            default_x,
            default_y,
        )

        selected.append(
            event
        )

        used_emojis.add(
            emoji_identity
        )

        if len(selected) >= target_count:
            break

    # A Short should not silently have zero visual reactions just
    # because none of its nouns happened to be in the map.
    if not selected and clip_duration > 0:

        middle_time = (
            clip_start
            + clip_duration * 0.45
        )

        nearest_word = min(
            words,
            key=lambda word: abs(
                float(
                    word.get(
                        "start",
                        middle_time,
                    )
                    or middle_time
                )
                - middle_time
            ),
        )

        fallback_start = float(
            nearest_word.get(
                "start",
                middle_time,
            )
            or middle_time
        )

        fallback_x, fallback_y = event_default_position_px(0)
        fallback_position_x, fallback_position_y = emoji_pixel_to_fraction(
            fallback_x,
            fallback_y,
        )

        selected.append(
            {
                "start": fallback_start,
                "end": min(
                    clip_end,
                    fallback_start
                    + EMOJI_DURATION_SECONDS,
                ),
                "emoji": FALLBACK_EMOJI,
                "matched_word": "fallback_reaction",
                "position_x": fallback_position_x,
                "position_y": fallback_position_y,
            }
        )

    return selected[:max_emoji_events]


EMOJI_POSITION_MERGE_TOLERANCE_SECONDS = 1.2


def apply_emoji_position_overrides(
    events: list[dict],
    previous_events: list[dict],
) -> list[dict]:
    """
    Carry forward a manual edit (dragged position and/or a picked emoji/
    asset) from a previous emoji_events.json onto the freshly-chosen
    events, matched purely by close start time -- not by matched_word/asset
    identity, since changing which emoji represents a moment deliberately
    changes that identity. Events are compared/stored using the same
    clip-relative time convention on both sides (subtitles.json is always
    clip-relative to whichever video was most recently transcribed, so no
    rebasing is needed here -- the caller is responsible for feeding in two
    lists on the same time base).
    """

    manual_previous = [
        previous
        for previous in previous_events
        if isinstance(previous, dict)
        and previous.get("manual_override")
    ]

    if not manual_previous:
        return events

    used_previous_ids = set()
    updated = []

    for event in events:
        event = dict(event)
        try:
            event_start = float(event.get("start", 0.0))
        except (TypeError, ValueError):
            event_start = 0.0

        best_match = None
        best_match_id = None
        best_distance = EMOJI_POSITION_MERGE_TOLERANCE_SECONDS

        for previous_id, previous in enumerate(manual_previous):
            if previous_id in used_previous_ids:
                continue
            try:
                previous_start = float(previous.get("start", 0.0))
            except (TypeError, ValueError):
                continue
            distance = abs(previous_start - event_start)
            if distance <= best_distance:
                best_distance = distance
                best_match = previous
                best_match_id = previous_id

        if best_match is not None:
            used_previous_ids.add(best_match_id)
            event["position_x"] = best_match.get("position_x", event.get("position_x"))
            event["position_y"] = best_match.get("position_y", event.get("position_y"))
            event["manual_override"] = True

            if best_match.get("content_override"):
                event["emoji"] = best_match.get("emoji", event.get("emoji"))
                event["content_override"] = True
                for asset_key in ("asset_path", "asset_description", "asset_type"):
                    if best_match.get(asset_key):
                        event[asset_key] = best_match[asset_key]
                    else:
                        event.pop(asset_key, None)

        updated.append(event)

    return updated


# ============================================================
# KARAOKE CAPTION
# ============================================================

def caption_size(
    level: str,
    energy: str,
) -> int:

    return int(
        CAPTION_SIZES.get(
            energy,
            CAPTION_SIZES[DEFAULT_ENERGY],
        ).get(
            level,
            FONT_SIZE,
        )
    )


def caption_color_tag(
    classification: dict,
    highlighted: bool,
) -> str:

    if highlighted:
        return INLINE_YELLOW

    accent = str(
        classification.get(
            "accent",
            "none",
        )
    )

    return INLINE_ACCENTS.get(
        accent,
        INLINE_WHITE,
    )


def caption_word_text(
    raw_word: str,
    classification: dict,
    highlighted: bool,
    energy: str,
) -> str:

    safe_word = escape_ass_text(
        raw_word
    )

    level = str(
        classification.get(
            "level",
            "NORMAL",
        )
    )

    size = caption_size(
        level,
        energy,
    )

    color = caption_color_tag(
        classification,
        highlighted,
    )

    pop = (
        CAPTION_POP_TAGS.get(
            level,
            "",
        )
        if highlighted
        else ""
    )

    if level == "EXTREME":
        border = r"\bord12\shad5"
    elif level == "IMPACT":
        border = r"\bord10\shad4"
    else:
        border = r"\bord8\shad4"

    tags = (
        f"\\fs{size}"
        f"{color}"
        f"{border}"
        f"{pop}"
    )

    return (
        "{"
        + tags
        + "}"
        + safe_word
        + r"{\rShorts}"
    )


def collect_caption_emphasis_event(
    word: dict,
    classification: dict,
    energy: str,
) -> dict | None:

    level = str(
        classification.get(
            "level",
            "NORMAL",
        )
    )

    if level == "NORMAL":
        return None

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

    text = str(
        word.get(
            "word",
            "",
        )
        or ""
    ).strip()

    return {
        "type": "caption_emphasis",
        "start": round(
            start,
            3,
        ),
        "end": round(
            max(
                end,
                start + 0.08,
            ),
            3,
        ),
        "treatment": level.lower(),
        "text": text,
        "trigger": text,
        "energy": energy,
        "score": classification.get(
            "score",
            0.0,
        ),
        "category": classification.get(
            "category",
            "speech",
        ),
        "accent": classification.get(
            "accent",
            "none",
        ),
        "reason": classification.get(
            "reason",
            "",
        ),
    }


def build_caption(
    words: list[dict],
    highlight_index: int,
    energy: str,
) -> str:
    """
    Render one 2-3 word caption chunk while highlighting exactly
    one currently-spoken word in yellow.

    The main loop creates several ASS Dialogue events for the same
    chunk, advancing highlight_index as Whisper reaches each word.
    """
    raw_words = []

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

        raw_words.append(
            (
                raw_word,
                classify_word(
                    word,
                    energy,
                ),
            )
        )

    if not raw_words:
        return ""

    pieces = []

    for i, (
        raw_word,
        classification,
    ) in enumerate(raw_words):

        pieces.append(
            caption_word_text(
                raw_word,
                classification,
                i == highlight_index,
                energy,
            )
        )

    return " ".join(pieces)


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    print("ShortsFactory karaoke caption generator starting...")

    render_settings = load_render_settings()
    edit_energy = normalize_energy(
        render_settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )

    print(f"Edit energy: {edit_energy}")

    caption_position_tag = caption_position_override_tag(render_settings)
    if caption_position_tag:
        print(f"Caption position override: {caption_position_tag}")

    if not INPUT_PATH.exists():
        print(f"ERROR: Missing {INPUT_PATH}")
        return 1

    with INPUT_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    words = data.get("words", [])

    if not isinstance(words, list) or not words:
        print("ERROR: No word timestamps found.")
        return 1

    events = []
    emoji_candidates = []
    caption_emphasis_events = []
    caption_emphasis_keys = set()

    index = 0

    while index < len(words):

        # Alternate naturally between 2 and 3 words.
        group_size = 2 if len(events) % 3 != 2 else 3

        group = words[index:index + group_size]

        if not group:
            break

        start = float(group[0]["start"])
        end = float(group[-1]["end"])

        # --------------------------------------------------------
        # TRUE KARAOKE HIGHLIGHTING
        #
        # Keep the same 2-3 word chunk on screen, but create a new
        # ASS event whenever the spoken word changes.
        #
        # Example:
        #
        #   THIS is wild
        #   this IS wild
        #   this is WILD
        #
        # The words themselves do not move; only the yellow
        # highlight advances with Whisper's word timestamps.
        # --------------------------------------------------------

        for highlight_index, word in enumerate(group):

            event_start = float(word["start"])

            if highlight_index + 1 < len(group):
                event_end = float(
                    group[highlight_index + 1]["start"]
                )
            else:
                event_end = end

            # Whisper timestamps can occasionally touch or overlap.
            # Ensure every highlight has a tiny valid duration.
            if event_end <= event_start:
                event_end = max(
                    float(word.get("end", event_start)),
                    event_start + 0.01,
                )

            text = build_caption(
                group,
                highlight_index,
                edit_energy,
            )

            if text:
                events.append(
                    (
                        ass_time(event_start),
                        ass_time(event_end),
                        caption_position_tag + text,
                    )
                )

        for word in group:
            classification = classify_word(
                word,
                edit_energy,
            )
            emphasis_event = collect_caption_emphasis_event(
                word,
                classification,
                edit_energy,
            )

            if not emphasis_event:
                continue

            emphasis_key = (
                emphasis_event["start"],
                emphasis_event["end"],
                emphasis_event["text"],
            )

            if emphasis_key in caption_emphasis_keys:
                continue

            caption_emphasis_keys.add(
                emphasis_key
            )
            caption_emphasis_events.append(
                emphasis_event
            )

        # Collect semantic emoji opportunities now; choose a sparse
        # set only after the whole Short has been scanned.
        emoji_match = find_emoji(
            group
        )

        if emoji_match:

            emoji_event = {
                "start": start,
                "end": end,
            }

            emoji_event.update(
                emoji_match
            )

            emoji_candidates.append(
                emoji_event
            )

        index += len(group)

    emoji_events = choose_emoji_events(
        emoji_candidates,
        words,
        edit_energy,
    )

    previous_emoji_events = []
    if EMOJI_OUTPUT_PATH.exists():
        try:
            previous_emoji_data = json.loads(
                EMOJI_OUTPUT_PATH.read_text(encoding="utf-8")
            )
            previous_emoji_events = previous_emoji_data.get("events", [])
            if not isinstance(previous_emoji_events, list):
                previous_emoji_events = []

            # The preview-only planner (emoji_planner.py) writes events in
            # absolute source-video time, since it runs before the clip is
            # ever cropped. This real pass always works in clip-relative
            # time (this file's own `words` come from the already-cropped
            # clip's transcript) -- rebase before comparing so a manual drag
            # made in the pre-render preview still matches up correctly.
            if previous_emoji_data.get("time_base") == "absolute":
                previous_selection_start = float(
                    previous_emoji_data.get("selection_start", 0.0) or 0.0
                )
                rebased = []
                for previous_event in previous_emoji_events:
                    if not isinstance(previous_event, dict):
                        continue
                    previous_event = dict(previous_event)
                    try:
                        previous_event["start"] = (
                            float(previous_event.get("start", 0.0))
                            - previous_selection_start
                        )
                    except (TypeError, ValueError):
                        continue
                    rebased.append(previous_event)
                previous_emoji_events = rebased
        except (OSError, json.JSONDecodeError):
            previous_emoji_events = []

    emoji_events = apply_emoji_position_overrides(
        emoji_events,
        previous_emoji_events,
    )

    visual_plan = build_visual_edit_plan(
        render_settings,
        caption_emphasis_events,
        emoji_events,
    )

    # ========================================================
    # ASS HEADER
    # ========================================================

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUTPUT_WIDTH}
PlayResY: {OUTPUT_HEIGHT}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,Arial,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,8,4,2,70,70,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:

        f.write(header)

        for start, end, text in events:

            f.write(
                f"Dialogue: 0,{start},{end},Shorts,,0,0,{MARGIN_V},,{text}\n"
            )

    with EMOJI_OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "time_base": "clip_relative",
                "selection_start": render_settings.get("selection_start", 0.0),
                "selection_end": render_settings.get("selection_end", 0.0),
                "events": emoji_events,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    write_visual_edit_plan(
        visual_plan
    )

    print(f"Created: {OUTPUT_PATH}")
    print(f"Created: {EMOJI_OUTPUT_PATH}")
    print(f"Created: {VISUAL_EDIT_PLAN_PATH}")
    print(f"Caption events: {len(events)}")
    print(f"Caption emphasis events: {len(caption_emphasis_events)}")
    print(f"Visual edit plan events: {visual_plan.get('event_count', 0)}")
    print(f"Emoji candidates found: {len(emoji_candidates)}")
    print(f"Emoji events selected: {len(emoji_events)}")

    for emoji_index, emoji_event in enumerate(
        emoji_events,
        start=1,
    ):
        print(
            "Emoji "
            f"{emoji_index}: "
            f"{emoji_event.get('matched_word', '')} "
            f"at {emoji_event['start']:.2f}s"
        )
    print("Words per caption: 2–3")
    print("Karaoke highlighting: word-by-word enabled")
    print("Caption emphasis: semantic size/color/pop enabled")
    print(f"Edit energy: {edit_energy}")
    print("Emoji images: enabled")
    print("Done.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
