import numpy as np
import pytest

import recap_media.portrait_framing as portrait_framing
from recap_media.loader import RecapInputError
from recap_media.portrait_framing import (
    DEFAULT_BLUR_SIGMA,
    build_portrait_filter_chain,
    build_portrait_framing_plan,
    build_portrait_framing_plan_for_video,
    detect_pillarbox_active_rect_from_frames,
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


def test_filter_chain_crops_active_picture_before_background_and_foreground_split():
    chain = build_portrait_filter_chain(
        0,
        555,
        1080,
        810,
        active_rect={"x": 240, "y": 0, "width": 1440, "height": 1080},
    )

    assert chain.startswith("[0:v]crop=1440:1080:240:0[recap_active_src];")
    assert chain.index("crop=1440:1080:240:0") < chain.index("split=2")
    assert "[recap_active_src]split=2" in chain


def test_filter_chain_applies_pre_split_filters_before_blurring_background():
    chain = build_portrait_filter_chain(
        0,
        555,
        1080,
        810,
        active_rect={"x": 240, "y": 0, "width": 1440, "height": 1080},
        pre_split_filters=["eq=contrast=1.1800", "unsharp=5:5:0.7000:3:3:0.0000"],
    )

    assert "[recap_active_src]eq=contrast=1.1800,unsharp=5:5:0.7000:3:3:0.0000[recap_prepared_src]" in chain
    assert chain.index("crop=1440:1080:240:0") < chain.index("eq=contrast=1.1800")
    assert chain.index("eq=contrast=1.1800") < chain.index("split=2")
    assert chain.index("split=2") < chain.index("gblur=sigma=")


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


def test_detects_consistent_baked_pillarboxes_from_multiple_frames():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[:, 240:1680] = (90, 150, 210)

    detection = detect_pillarbox_active_rect_from_frames([frame] * 6, 1920, 1080)

    assert detection["pillarbox_detected"] is True
    assert detection["active_rect"] == {"x": 240, "y": 0, "width": 1440, "height": 1080}
    assert detection["consensus_count"] == 6


def test_no_pillarbox_keeps_the_full_frame_active():
    frame = np.full((180, 320, 3), (80, 110, 140), dtype=np.uint8)

    detection = detect_pillarbox_active_rect_from_frames([frame] * 4, 320, 180)

    assert detection["pillarbox_detected"] is False
    assert detection["active_rect"] == {"x": 0, "y": 0, "width": 320, "height": 180}


def test_dark_full_frame_scene_does_not_be_mistaken_for_pillarboxing():
    frame = np.full((180, 320, 3), 10, dtype=np.uint8)
    frame[:, :8] = 30
    frame[:, -8:] = 30

    detection = detect_pillarbox_active_rect_from_frames([frame] * 4, 320, 180)

    assert detection["pillarbox_detected"] is False
    assert detection["active_rect"] == {"x": 0, "y": 0, "width": 320, "height": 180}


def test_inconsistent_side_boundaries_fall_back_to_the_full_frame():
    frame_a = np.zeros((180, 320, 3), dtype=np.uint8)
    frame_b = np.zeros((180, 320, 3), dtype=np.uint8)
    frame_a[:, 30:290] = (100, 160, 220)
    frame_b[:, 54:266] = (100, 160, 220)

    detection = detect_pillarbox_active_rect_from_frames(
        [frame_a, frame_a, frame_b, frame_b], 320, 180
    )

    assert detection["pillarbox_detected"] is False
    assert detection["active_rect"] == {"x": 0, "y": 0, "width": 320, "height": 180}


def test_active_picture_geometry_fills_width_without_aspect_distortion():
    plan = build_portrait_framing_plan(
        1920,
        1080,
        active_rect={"x": 240, "y": 0, "width": 1440, "height": 1080},
    )

    assert plan["content_width"] == 1080
    assert plan["content_height"] == 810
    assert plan["content_y"] == 555
    assert "crop=1440:1080:240:0" in plan["filter_chain"]


def test_source_bound_plan_cache_reuses_matching_source_analysis(monkeypatch, tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    cache_path = tmp_path / "portrait_framing_plan.json"
    detection = {
        "active_rect": {"x": 24, "y": 0, "width": 272, "height": 180},
        "pillarbox_detected": True,
        "confidence": 1.0,
        "method": "test",
        "sample_count": 4,
        "candidate_count": 4,
        "consensus_count": 4,
    }
    monkeypatch.setattr(portrait_framing, "ffprobe_source_dimensions", lambda _path: (320, 180))
    monkeypatch.setattr(
        portrait_framing, "detect_pillarbox_active_rect_for_video", lambda *_args: detection
    )

    first = build_portrait_framing_plan_for_video(source, cache_path=cache_path)
    write_portrait_framing_plan(first, cache_path)
    monkeypatch.setattr(
        portrait_framing,
        "detect_pillarbox_active_rect_for_video",
        lambda *_args: pytest.fail("matching source should reuse its cached plan"),
    )

    assert build_portrait_framing_plan_for_video(source, cache_path=cache_path) == first


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
