"""
Task 16 -- TTS expression tags (product rule 8: "`<gasp>`, `<chuckle>`... never
appear in captions"). recap_script.json's authored "text" field may carry
inline non-verbal cues like "<chuckle> The Krusty Krab is..." for Orpheus to
vocalize. Those tags must reach the TTS engine but never the screen.

Two views are derived from the one authored "text" field rather than requiring
every script to be rewritten:

    tts_text     = segment.get("tts_text")     or text
    display_text = segment.get("display_text") or strip_tags(text)

An explicit "tts_text"/"display_text" on a segment always wins -- this is
the hook a future authoring UI can write into, while every existing script
(no such fields) keeps working unchanged.

Known limitation: display_text_for_segment() is a whole-segment view, but
the caption pipeline (recap_media.caption_alignment) works word-by-word --
it aligns tts_text against the WAV, then drops/strips tag words one at a
time (see _drop_expression_tag_words()). An explicit display_text is not
consulted there, since a per-word aligner has no way to honor an arbitrary
whole-segment rewording; only strip_tags(tts_text) is applied per word.
display_text_for_segment() itself is only used for the word_count fallback
estimate today (recap_media.loader). A future caption path that wants to
honor an explicit display_text verbatim would need its own (non-aligned)
timing strategy, not a drop-in here.
"""

from __future__ import annotations

import re
from typing import Any

# The vocabulary Orpheus recognizes as inline expression cues.
EXPRESSION_TAGS = (
    "laugh",
    "sigh",
    "chuckle",
    "cough",
    "sniffle",
    "groan",
    "yawn",
    "gasp",
)

_TAG_RE = re.compile(
    r"<(?:" + "|".join(EXPRESSION_TAGS) + r")>",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def strip_tags(text: str) -> str:
    """Remove every expression tag from `text` and collapse the whitespace
    left behind. Safe to call on text with no tags at all (a no-op)."""

    without_tags = _TAG_RE.sub("", text or "")
    return _WHITESPACE_RE.sub(" ", without_tags).strip()


def tts_text_for_segment(segment: dict[str, Any]) -> str:
    """What Orpheus should actually speak for this segment."""

    return segment.get("tts_text") or segment.get("text", "")


def display_text_for_segment(segment: dict[str, Any]) -> str:
    """What viewers should actually read for this segment -- tags never
    reach the screen (product rule 8)."""

    return segment.get("display_text") or strip_tags(segment.get("text", ""))
