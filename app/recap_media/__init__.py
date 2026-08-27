"""
Track B (Media + Editor) of AI Recap Mode -- see
SHORTSFACTORY_AI_RECAP_SHARED_CONTRACT.md and
SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR.md. Consumes Track A's frozen
semantic JSON (episode identity, verified story map, recap script) and
turns it into an editable, voiced, mixed, captioned, rendered recap.
Nothing here performs research, plot reasoning, or story-map generation.
"""

from __future__ import annotations
