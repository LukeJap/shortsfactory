from visual_emphasis import (
    build_intensity_curve,
    classify_word,
    mark_collisions,
    normalize_energy,
    normalize_sfx_mode,
    word_time,
)


def test_normalize_energy_valid_and_fallback():
    assert normalize_energy("low") == "LOW"
    assert normalize_energy("Maximum") == "MAXIMUM"
    assert normalize_energy(None) == "PUNCHY"
    assert normalize_energy("bogus") == "PUNCHY"


def test_normalize_sfx_mode_valid_and_fallback():
    assert normalize_sfx_mode("off") == "OFF"
    assert normalize_sfx_mode(None) == "AUTO"
    assert normalize_sfx_mode("bogus") == "AUTO"


def test_classify_word_plain_word_is_normal():
    result = classify_word("the")
    assert result["level"] == "NORMAL"
    assert result["score"] == 0.0


def test_classify_word_negation_is_impact():
    # "never" is a NEGATION_WORDS entry (score 7.0), which crosses the
    # default PUNCHY energy's caption_impact_threshold (7.0). This matches
    # the documented example in SHORTSFACTORY_CURRENT_STATUS.md.
    result = classify_word("never")
    assert result["level"] == "IMPACT"
    assert result["score"] == 7.0
    assert result["category"] == "negation"


def test_classify_word_impact_list_word_is_emphasis():
    # "huge" is an IMPACT_WORDS entry (score 5.0), which crosses the
    # emphasis threshold (3.0) but not the impact threshold (7.0).
    result = classify_word("huge")
    assert result["level"] == "EMPHASIS"
    assert result["score"] == 5.0


def test_classify_word_money_marker_is_extreme():
    result = classify_word("$12,000")
    assert result["level"] == "EXTREME"
    assert result["category"] == "money"


def test_word_time_valid_pair():
    assert word_time({"start": 1.0, "end": 2.0}) == (1.0, 2.0)


def test_word_time_forces_minimum_duration():
    start, end = word_time({"start": 2.0, "end": 2.0})
    assert start == 2.0
    assert end > start


def test_word_time_returns_none_for_garbage_input():
    assert word_time({"start": "not-a-number", "end": 2.0}) is None


def test_mark_collisions_flags_lower_priority_event_within_window():
    events = [
        {"type": "scene_cut", "start": 5.0},
        {"type": "caption_emphasis", "start": 5.2},
    ]

    result = mark_collisions(events)

    scene_cut, caption = result
    assert "collision_note" not in scene_cut
    assert caption["collision_note"] == "coexists_with_higher_priority_event"


def test_mark_collisions_ignores_events_far_apart():
    events = [
        {"type": "scene_cut", "start": 5.0},
        {"type": "caption_emphasis", "start": 10.0},
    ]

    result = mark_collisions(events)

    assert all("collision_note" not in event for event in result)


def test_mark_collisions_tags_coordinated_stack_instead_of_colliding():
    events = [
        {"type": "scene_cut", "start": 5.0, "stack_id": "A"},
        {"type": "caption_emphasis", "start": 5.2, "stack_id": "A"},
    ]

    result = mark_collisions(events)

    # Same-stack events near each other are deliberate coordination, not an
    # accidental collision -- both get the coordinated-stack note, not the
    # lower-priority-loses note.
    assert all(
        event["collision_note"] == "coordinated_semantic_stack"
        for event in result
    )


def test_build_intensity_curve_covers_full_duration_in_five_regions():
    curve = build_intensity_curve(100.0, [])

    assert [region["region"] for region in curve] == [
        "hook",
        "setup",
        "build",
        "payoff",
        "ending",
    ]
    assert curve[0]["start"] == 0.0
    assert curve[-1]["end"] == 100.0
    # Each region should start exactly where the previous one ended.
    for previous, current in zip(curve, curve[1:]):
        assert previous["end"] == current["start"]


def test_build_intensity_curve_boosts_regions_with_more_moments():
    baseline = build_intensity_curve(100.0, [])
    with_moments = build_intensity_curve(
        100.0,
        [{"start": 5.0}, {"start": 6.0}, {"start": 7.0}],
    )

    baseline_hook = baseline[0]
    boosted_hook = with_moments[0]

    assert boosted_hook["moment_count"] == 3
    assert boosted_hook["intensity"] >= baseline_hook["intensity"]
