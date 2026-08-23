from make_captions import caption_position_override_tag


def test_no_override_returns_empty_string():
    assert caption_position_override_tag({}) == ""
    assert caption_position_override_tag({"caption_position_x": 0.5}) == ""
    assert caption_position_override_tag({"caption_position_y": 0.5}) == ""


def test_override_builds_ass_pos_tag_scaled_to_canvas():
    tag = caption_position_override_tag(
        {"caption_position_x": 0.5, "caption_position_y": 0.5}
    )
    assert tag == r"{\pos(540.0,960.0)}"


def test_override_clamps_out_of_range_fractions_to_the_safe_drag_zone():
    # Clamps all the way to the safe-zone bounds (not just 0/1080 and
    # 0/1920) -- this is the defensive render-side clamp, independent of
    # the GUI's own drag clamp, so a stale or hand-edited render_settings
    # value can never burn a caption into the platform-UI zone.
    tag = caption_position_override_tag(
        {"caption_position_x": 2.0, "caption_position_y": -1.0}
    )
    assert tag == r"{\pos(900.0,140.0)}"


def test_override_clamps_values_inside_0_1_but_outside_the_safe_zone():
    # 0.99 is a perfectly valid fraction of the canvas, but still inside
    # the unsafe bottom strip -- must still be pulled back to the floor.
    tag = caption_position_override_tag(
        {"caption_position_x": 0.5, "caption_position_y": 0.99}
    )
    assert tag == r"{\pos(540.0,1660.0)}"


def test_override_ignores_garbage_values():
    tag = caption_position_override_tag(
        {"caption_position_x": "nope", "caption_position_y": 0.5}
    )
    assert tag == ""
