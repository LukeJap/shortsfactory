from smart_motion import x_expression, y_expression, zoom_expression
from visual_fx import (
    PROFESSIONAL_FX_RECIPES,
    build_semantic_filter_chain,
    build_semantic_moments,
    expand_moments_to_events,
    merge_motion_events,
    motion_events_for_moments,
    normalize_fx_recipe,
    semantic_event_filters,
)


def test_legacy_chaos_effect_names_alias_to_professional_recipes():
    legacy_names = [
        "contrast_flash",
        "overdrive_flash",
        "rgb_split",
        "glitch_hit",
        "posterize_hit",
        "magenta_hype",
        "green_money",
        "warm_gold",
        "cold_blue",
        "red_danger",
        "desat_hit",
        "slam_text",
    ]

    for name in legacy_names:
        assert normalize_fx_recipe(name) in PROFESSIONAL_FX_RECIPES


def test_semantic_chain_does_not_emit_chaotic_filters():
    events = [
        {
            "start": 1.0 + index,
            "end": 1.28 + index,
            "effect": effect,
            "intensity": 1.0,
        }
        for index, effect in enumerate(
            [
                "contrast_flash",
                "overdrive_flash",
                "rgb_split",
                "glitch_hit",
                "posterize_hit",
                "red_danger",
            ]
        )
    ]

    chain = build_semantic_filter_chain(
        events,
        1.0,
        "MAXIMUM",
    )

    assert "eval=frame" in chain
    for forbidden in (
        "drawbox=",
        "hue=",
        "posterize",
        "rgbashift",
        "colorchannelmixer",
        "white@",
        "red@",
        "cyan@",
        "magenta@",
        "green@",
    ):
        assert forbidden not in chain


def test_semantic_moments_select_professional_recipes_with_sparse_density():
    words = [
        {"word": "what?", "start": 1.0, "end": 1.2},
        {"word": "huge", "start": 3.6, "end": 3.9},
        {"word": "$12,000", "start": 6.4, "end": 6.8},
        {"word": "awkward", "start": 9.2, "end": 9.5},
        {"word": "remember", "start": 12.0, "end": 12.3},
        {"word": "insane!", "start": 14.8, "end": 15.1},
    ]

    moments, _curve = build_semantic_moments(
        words,
        "MAXIMUM",
    )

    assert 1 <= len(moments) <= 2
    assert {
        moment["recipe"]
        for moment in moments
    } <= set(PROFESSIONAL_FX_RECIPES)
    assert all(
        moment["recipe"]
        not in {
            "wtf_chaos",
            "rgb_split",
            "glitch_hit",
            "overdrive_flash",
        }
        for moment in moments
    )


def test_maximum_expansion_uses_polished_stack_not_graphics():
    moment = {
        "start": 2.0,
        "end": 2.2,
        "trigger_word": "insane",
        "level": "IMPACT",
        "score": 8.0,
        "recipe": "impact_punch",
        "legacy_recipe": "wtf_chaos",
        "moment_type": "SHOCK",
        "region": "hook",
        "intensity": 0.90,
        "hero": True,
    }

    events = expand_moments_to_events(
        [
            moment,
        ],
        "MAXIMUM",
    )

    assert [
        event["effect"]
        for event in events
    ] == [
        "impact_punch",
        "micro_camera_hit",
        "whip_blur",
    ]
    assert all(
        event["type"] == "filter"
        for event in events
    )


def test_recipe_motion_events_coordinate_with_selected_fx_moments():
    moments = [
        {
            "start": 2.0,
            "end": 2.2,
            "trigger_word": "insane",
            "recipe": "impact_punch",
            "moment_type": "SHOCK",
            "stack_id": "stack_01",
        },
        {
            "start": 5.0,
            "end": 5.3,
            "trigger_word": "$12,000",
            "recipe": "bloom_glow",
            "moment_type": "TRIUMPH",
            "stack_id": "stack_02",
        },
    ]

    events = motion_events_for_moments(
        moments,
        12.0,
        "PUNCHY",
    )

    assert [
        event["movement"]
        for event in events
    ] == [
        "impact_punch",
        "punch_in",
    ]
    assert events[0]["zoom"] == 1.08
    assert events[0]["source"] == "visual_fx_recipe"
    assert "x_bias" in events[0]
    assert "y_bias" in events[0]


def test_recipe_motion_wins_nearby_and_fallback_fills_gaps():
    recipe_events = [
        {
            "start": 2.0,
            "end": 2.5,
            "movement": "impact_punch",
            "zoom": 1.08,
            "source": "visual_fx_recipe",
        }
    ]
    fallback_events = [
        {
            "start": 2.4,
            "end": 3.0,
            "movement": "punch_in",
            "zoom": 1.1,
        },
        {
            "start": 6.0,
            "end": 7.0,
            "movement": "slow_push",
            "zoom": 1.05,
        },
    ]

    merged = merge_motion_events(
        recipe_events,
        fallback_events,
        "PUNCHY",
    )

    assert [
        event["start"]
        for event in merged
    ] == [
        2.0,
        6.0,
    ]
    assert merged[0]["source"] == "visual_fx_recipe"
    assert merged[1]["source"] == "smart_motion_fallback"


def test_impact_punch_camera_expression_includes_tiny_xy_hit():
    events = [
        {
            "start": 1.0,
            "end": 1.5,
            "zoom": 1.08,
            "movement": "impact_punch",
            "x_bias": 0.018,
            "y_bias": -0.012,
        }
    ]

    zoom = zoom_expression(
        events,
        30.0,
    )
    assert "between(on,30,34)" in zoom
    assert "between(on,34,37)" in zoom
    assert "between(on,41,45)" in zoom
    assert "(iw-iw/zoom)*0.018" in x_expression(
        events,
        30.0,
    )
    assert "(ih-ih/zoom)*-0.012" in y_expression(
        events,
        30.0,
    )


def test_semantic_event_filters_respect_zero_intensity():
    assert semantic_event_filters(
        [
            {
                "start": 1.0,
                "end": 1.5,
                "effect": "impact_punch",
            }
        ],
        intensity=0.0,
        energy="PUNCHY",
    ) == []
