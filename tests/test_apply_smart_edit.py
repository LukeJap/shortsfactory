from apply_smart_edit import keep_segments_match_existing_tight_video


def test_matches_when_segments_are_numerically_identical():
    pause_plan = {
        "keep_ranges": [
            {"start": 0.0, "end": 2.85, "duration": 2.85},
            {"start": 4.15, "end": 26.91, "duration": 22.76},
            {"start": 27.87, "end": 40.624, "duration": 12.754},
        ]
    }
    keep_segments = [(0.0, 2.85), (4.15, 26.91), (27.87, 40.624)]

    assert keep_segments_match_existing_tight_video(keep_segments, pause_plan)


def test_tolerates_float_formatting_differences_within_rounding():
    pause_plan = {
        "keep_ranges": [
            {"start": 0.0, "end": 2.85},
        ]
    }
    # Same value, just carrying float noise past 3 decimals.
    keep_segments = [(0.00000001, 2.8500004)]

    assert keep_segments_match_existing_tight_video(keep_segments, pause_plan)


def test_does_not_match_when_a_semantic_or_manual_cut_changed_the_segments():
    pause_plan = {
        "keep_ranges": [
            {"start": 0.0, "end": 2.85},
            {"start": 4.15, "end": 26.91},
            {"start": 27.87, "end": 40.624},
        ]
    }
    # An extra cut in the middle -- a real edit, must not be treated as
    # equivalent to the pause-only render.
    keep_segments = [(0.0, 2.85), (4.15, 15.0), (16.0, 26.91), (27.87, 40.624)]

    assert not keep_segments_match_existing_tight_video(keep_segments, pause_plan)


def test_does_not_match_when_pause_plan_is_missing_keep_ranges():
    assert not keep_segments_match_existing_tight_video(
        [(0.0, 2.85)], {}
    )


def test_does_not_match_on_malformed_pause_plan_entries():
    pause_plan = {"keep_ranges": [{"start": "not-a-number", "end": 2.85}]}
    assert not keep_segments_match_existing_tight_video(
        [(0.0, 2.85)], pause_plan
    )


def test_empty_segments_on_both_sides_match():
    assert keep_segments_match_existing_tight_video([], {"keep_ranges": []})
