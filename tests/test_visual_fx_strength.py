from visual_fx import (
    build_semantic_moments,
    coerce_visual_fx_strength,
    event_strength,
    expand_moments_to_events,
    visual_fx_effect_strength_scale,
    visual_fx_planning_settings,
    visual_fx_strength_from_energy,
)


def _emphatic_words():
    return [
        {"word": "never!", "start": 1.0 + (index * 5.0), "end": 1.3 + (index * 5.0)}
        for index in range(10)
    ]


def test_visual_fx_strength_legacy_anchors_are_preserved():
    words = _emphatic_words()

    assert visual_fx_strength_from_energy("LOW") == 25
    assert visual_fx_strength_from_energy("PUNCHY") == 50
    assert visual_fx_strength_from_energy("MAXIMUM") == 75
    assert build_semantic_moments(words, "LOW") == build_semantic_moments(
        words, "PUNCHY", 25
    )
    assert build_semantic_moments(words, "PUNCHY") == build_semantic_moments(
        words, "LOW", 50
    )
    assert build_semantic_moments(words, "MAXIMUM") == build_semantic_moments(
        words, "LOW", 75
    )


def test_visual_fx_strength_zero_disables_semantic_fx_without_touching_other_settings():
    words = _emphatic_words()

    moments, curve = build_semantic_moments(words, "MAXIMUM", 0)

    assert moments == []
    assert curve == []
    assert expand_moments_to_events([{"start": 1.0}], "MAXIMUM", 0) == []


def test_visual_fx_strength_interpolates_density_and_exceeds_legacy_maximum_at_100():
    low = visual_fx_planning_settings(25)
    punchy = visual_fx_planning_settings(50)
    maximum = visual_fx_planning_settings(75)
    maximum_plus = visual_fx_planning_settings(100)

    assert low["max_moments"] == 2
    assert punchy["max_moments"] == 4
    assert maximum["max_moments"] == 7
    assert maximum_plus["max_moments"] > maximum["max_moments"]
    assert maximum_plus["spacing"] < maximum["spacing"]
    assert visual_fx_effect_strength_scale(75) == 1.0
    assert visual_fx_effect_strength_scale(100) > 1.0

    maximum_moments, _curve = build_semantic_moments(
        _emphatic_words(), "MAXIMUM", 75
    )
    maximum_plus_moments, _curve = build_semantic_moments(
        _emphatic_words(), "MAXIMUM", 100
    )
    assert len(maximum_plus_moments) > len(maximum_moments)


def test_visual_fx_strength_overdrive_scales_event_strength_but_stays_bounded():
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
        "intensity": 0.9,
    }

    baseline = expand_moments_to_events([moment], "MAXIMUM", 75)
    overdriven = expand_moments_to_events([moment], "MAXIMUM", 100)

    assert "visual_fx_strength_scale" not in baseline[0]
    assert overdriven[0]["visual_fx_strength_scale"] > 1.0
    assert event_strength(overdriven[0], 1.0) > event_strength(baseline[0], 1.0)
    assert event_strength(overdriven[0], 2.0) <= 2.0


def test_visual_fx_strength_clamps_invalid_slider_values():
    assert coerce_visual_fx_strength(-1) == 0
    assert coerce_visual_fx_strength(101) == 100
    assert coerce_visual_fx_strength("LOW") == 25
    assert coerce_visual_fx_strength("PUNCHY") == 50
    assert coerce_visual_fx_strength("MAXIMUM") == 75
    assert coerce_visual_fx_strength("50") == 50
    assert coerce_visual_fx_strength("invalid") == 50
    assert coerce_visual_fx_strength(float("inf")) == 50
