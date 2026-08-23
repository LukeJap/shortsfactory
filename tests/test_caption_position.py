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


def test_override_clamps_out_of_range_fractions():
    tag = caption_position_override_tag(
        {"caption_position_x": 2.0, "caption_position_y": -1.0}
    )
    assert tag == r"{\pos(1080.0,0.0)}"


def test_override_ignores_garbage_values():
    tag = caption_position_override_tag(
        {"caption_position_x": "nope", "caption_position_y": 0.5}
    )
    assert tag == ""
