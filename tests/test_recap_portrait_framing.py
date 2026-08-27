import pytest

from recap_media.loader import RecapInputError
from recap_media.portrait_framing import (
    DEFAULT_BLUR_SIGMA,
    build_portrait_filter_chain,
    build_portrait_framing_plan,
    load_portrait_framing_plan,
    write_portrait_framing_plan,
)


# ============================================================
# build_portrait_filter_chain
# ============================================================

def test_filter_chain_has_split_blur_scale_overlay():
    chain = build_portrait_filter_chain(0, 555, 1080, 810)
    assert "split=2" in chain
    assert "gblur=sigma=" in chain
    assert "overlay=0:555" in chain
    assert "scale=1080:810" in chain  # foreground: exact content size, no aspect flag needed


def test_filter_chain_background_scales_to_cover_and_crop_full_canvas():
    chain = build_portrait_filter_chain(0, 555, 1080, 810, canvas_width=1080, canvas_height=1920)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in chain
    assert "crop=1080:1920" in chain


def test_filter_chain_no_dim_by_default():
    chain = build_portrait_filter_chain(0, 0, 1080, 1920)
    assert "eq=brightness" not in chain


def test_filter_chain_applies_dim_when_requested():
    chain = build_portrait_filter_chain(0, 0, 1080, 1920, background_dim=0.3)
    assert "eq=brightness=-0.300" in chain


def test_filter_chain_dim_clamped_to_valid_range():
    chain = build_portrait_filter_chain(0, 0, 1080, 1920, background_dim=5.0)
    assert "eq=brightness=-1.000" in chain


def test_filter_chain_uses_custom_blur_sigma():
    chain = build_portrait_filter_chain(0, 0, 1080, 1920, blur_sigma=40.0)
    assert "gblur=sigma=40.00" in chain


def test_filter_chain_output_label_is_recap_out():
    chain = build_portrait_filter_chain(0, 0, 1080, 1920)
    assert chain.endswith("[recap_out]")
    assert chain.startswith("[0:v]split=2")


# ============================================================
# build_portrait_framing_plan: geometry correctness
# ============================================================

def test_4_3_source_letterboxes_top_and_bottom():
    # 640x480 (4:3) into 1080x1920 -- fills full width, leaves blurred
    # bands above/below (the "especially useful for 4:3" case).
    plan = build_portrait_framing_plan(640, 480)
    assert plan["content_width"] == 1080
    assert plan["content_height"] == 810
    assert plan["content_x"] == 0
    assert plan["content_y"] == 555  # (1920 - 810) // 2


def test_16_9_source_pillarboxes_left_and_right():
    # 1920x1080 (16:9) into 1080x1920 -- fills full height (or close),
    # leaves blurred bands on the sides since content is much narrower.
    plan = build_portrait_framing_plan(1920, 1080)
    assert plan["content_height"] == 608  # round(1080 * (1080/1920))
    assert plan["content_width"] == 1080
    assert plan["content_y"] == 656  # (1920 - 608) // 2


def test_content_rect_never_exceeds_canvas():
    for source_width, source_height in [(640, 480), (1920, 1080), (100, 2000), (2000, 100)]:
        plan = build_portrait_framing_plan(source_width, source_height)
        assert plan["content_width"] <= plan["canvas_width"]
        assert plan["content_height"] <= plan["canvas_height"]
        assert plan["content_x"] >= 0
        assert plan["content_y"] >= 0


def test_degenerate_source_dimensions_falls_back_to_full_canvas():
    plan = build_portrait_framing_plan(0, 0)
    assert plan["content_width"] == plan["canvas_width"]
    assert plan["content_height"] == plan["canvas_height"]
    assert plan["content_x"] == 0
    assert plan["content_y"] == 0


def test_plan_includes_filter_chain_matching_its_own_geometry():
    plan = build_portrait_framing_plan(640, 480)
    assert f"overlay={plan['content_x']}:{plan['content_y']}" in plan["filter_chain"]
    assert f"scale={plan['content_width']}:{plan['content_height']}" in plan["filter_chain"]


def test_plan_uses_default_blur_sigma_unless_overridden():
    plan = build_portrait_framing_plan(640, 480)
    assert plan["blur_sigma"] == DEFAULT_BLUR_SIGMA


# ============================================================
# write/load round trip
# ============================================================

def test_write_and_load_round_trip(tmp_path):
    plan = build_portrait_framing_plan(640, 480)
    path = tmp_path / "portrait_framing_plan.json"
    write_portrait_framing_plan(plan, path)
    loaded = load_portrait_framing_plan(path)
    assert loaded == plan


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(RecapInputError, match="not found"):
        load_portrait_framing_plan(tmp_path / "does_not_exist.json")


def test_load_malformed_json_raises(tmp_path):
    path = tmp_path / "portrait_framing_plan.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RecapInputError, match="not valid JSON"):
        load_portrait_framing_plan(path)


def test_load_missing_required_field_raises(tmp_path):
    path = tmp_path / "portrait_framing_plan.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(RecapInputError, match="filter_chain"):
        load_portrait_framing_plan(path)
