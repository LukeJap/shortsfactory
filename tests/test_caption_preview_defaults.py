from gui_app.mixins.caption_preview import (
    DEFAULT_CAPTION_POSITION_X,
    DEFAULT_CAPTION_POSITION_Y,
)
from render import (
    CAPTION_SAFE_MARGIN_BOTTOM,
    CAPTION_SAFE_MARGIN_LEFT,
    CAPTION_SAFE_MARGIN_RIGHT,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
)


def test_default_caption_position_matches_render_py_force_style_margins():
    # render.py's burn_captions() burns captions with a force_style that
    # completely overrides captions.ass's own Style block -- the preview's
    # idle/default position must be derived from those real margins
    # (CAPTION_SAFE_MARGIN_*), not from make_captions.py's own MARGIN_V
    # constant, which never actually reaches the final render.
    expected_x = (
        CAPTION_SAFE_MARGIN_LEFT
        + (OUTPUT_WIDTH - CAPTION_SAFE_MARGIN_RIGHT)
    ) / 2 / OUTPUT_WIDTH
    expected_y = 1.0 - (CAPTION_SAFE_MARGIN_BOTTOM / OUTPUT_HEIGHT)

    assert DEFAULT_CAPTION_POSITION_X == expected_x
    assert DEFAULT_CAPTION_POSITION_Y == expected_y


def test_default_caption_position_is_within_the_canvas():
    assert 0.0 <= DEFAULT_CAPTION_POSITION_X <= 1.0
    assert 0.0 <= DEFAULT_CAPTION_POSITION_Y <= 1.0
