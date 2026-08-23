from render import (
    CAPTION_DRAG_MARGIN_BOTTOM,
    CAPTION_DRAG_MARGIN_TOP,
    CAPTION_SAFE_MARGIN_LEFT,
    CAPTION_SAFE_MARGIN_RIGHT,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    clamp_caption_drag_position,
)


def test_center_position_is_unaffected():
    assert clamp_caption_drag_position(0.5, 0.5) == (0.5, 0.5)


def test_clamps_to_the_floor_and_ceiling():
    min_x = CAPTION_SAFE_MARGIN_LEFT / OUTPUT_WIDTH
    max_x = (OUTPUT_WIDTH - CAPTION_SAFE_MARGIN_RIGHT) / OUTPUT_WIDTH
    min_y = CAPTION_DRAG_MARGIN_TOP / OUTPUT_HEIGHT
    max_y = (OUTPUT_HEIGHT - CAPTION_DRAG_MARGIN_BOTTOM) / OUTPUT_HEIGHT

    assert clamp_caption_drag_position(0.0, 0.0) == (min_x, min_y)
    assert clamp_caption_drag_position(1.0, 1.0) == (max_x, max_y)


def test_reproduces_the_real_bug_found_in_a_render_log():
    # A real render_log.txt showed {\pos(541.1,1920.0)} -- the caption
    # dragged flush against the true bottom edge, with the actual burned
    # frame confirming it was visibly cut off/unsafe. y=1.0 (fraction)
    # must no longer be able to reach the raw edge.
    _x, y = clamp_caption_drag_position(541.1 / OUTPUT_WIDTH, 1.0)
    assert y * OUTPUT_HEIGHT == OUTPUT_HEIGHT - CAPTION_DRAG_MARGIN_BOTTOM
    assert y * OUTPUT_HEIGHT < 1920


def test_only_clamps_the_axis_that_is_out_of_range():
    x, y = clamp_caption_drag_position(0.5, 1.0)
    assert x == 0.5
    assert y == (OUTPUT_HEIGHT - CAPTION_DRAG_MARGIN_BOTTOM) / OUTPUT_HEIGHT
