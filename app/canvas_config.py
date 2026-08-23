"""
Single source of truth for the output canvas size every ShortsFactory
render targets: 1080x1920 (9:16, vertical "Shorts" format).

Previously redefined independently under different names in render.py,
visual_emphasis.py, emoji_overlay.py, and apply_ai_visuals.py (plus bare
1080/1920 literals scattered in a few ffmpeg filter strings) -- consolidated
here, mirroring the same small-leaf-module pattern already established by
ollama_config.py.
"""

from __future__ import annotations

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
