from __future__ import annotations

from pathlib import Path


# gui_app/constants.py -> gui_app/ -> app/ -> repo root. Every other file in
# this package imports ROOT from here instead of recomputing it, since files
# at different depths (gui_app/ vs gui_app/mixins/) would need a different
# number of .parent hops to reach the same repo root.
ROOT = Path(__file__).resolve().parent.parent.parent

SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
}

VISUAL_EVENT_PREFIX = "SF_VISUAL_EVENT "

GENERIC_EDITOR_PHRASES = (
    "becomes the center of attention",
    "becomes the center of a short exchange",
    "clear setup and payoff",
    "interesting moment",
    "engaging conversation",
    "creates curiosity",
    "viewers will want to know",
    "something surprising happens",
)
