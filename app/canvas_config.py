"""
Single source of truth for the output canvas size every ShortsFactory
render targets: 1080x1920 (9:16, vertical "Shorts" format).

Previously redefined independently under different names in render.py,
visual_emphasis.py and emoji_overlay.py (plus bare
1080/1920 literals scattered in a few ffmpeg filter strings) -- consolidated
here, mirroring the same small-leaf-module pattern already established by
ollama_config.py.
"""

from __future__ import annotations

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


def crop_to_fill_filter() -> str:
    """Return a crop-before-scale filter for the shared vertical canvas."""
    target_aspect = OUTPUT_WIDTH / OUTPUT_HEIGHT
    inverse_aspect = OUTPUT_HEIGHT / OUTPUT_WIDTH
    aspect_test = f"gte(iw/ih\\,{target_aspect:.4f})"
    crop_width = f"if({aspect_test}\\,ih*{target_aspect:.4f}\\,iw)"
    crop_height = f"if({aspect_test}\\,ih\\,iw*{inverse_aspect:.4f})"

    # Cropping first avoids creating an oversized intermediate frame before
    # the center crop discards most of it (3413x1920 for a 16:9 source).
    return (
        f"crop={crop_width}:{crop_height}:(iw-ow)/2:(ih-oh)/2,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=bicubic"
    )
