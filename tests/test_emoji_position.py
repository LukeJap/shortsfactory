from emoji_overlay import (
    console_safe_text,
    coerce_emoji_fraction,
    emoji_fraction_to_pixel,
    emoji_pixel_to_fraction,
    event_default_position_px,
)
from make_captions import apply_emoji_position_overrides, choose_emoji_events


def words_at(*starts_and_texts):
    words = []
    for start, text in starts_and_texts:
        words.append(
            {
                "word": text,
                "start": start,
                "end": start + 0.4,
            }
        )
    return words


def test_coerce_emoji_fraction_clamps():
    assert coerce_emoji_fraction(-0.5) == 0.0
    assert coerce_emoji_fraction(5.0) == 1.0
    assert coerce_emoji_fraction("garbage") == 0.0
    assert coerce_emoji_fraction(0.4) == 0.4


def test_pixel_fraction_round_trip_matches_legacy_positions():
    # The default round-robin table must still land on the exact same
    # pixel positions after being stored/retrieved as a fraction.
    for x, y in [(760, 1300), (170, 1340), (750, 1430), (190, 1460)]:
        fx, fy = emoji_pixel_to_fraction(x, y)
        px, py = emoji_fraction_to_pixel(fx, fy)
        assert round(px) == x
        assert round(py) == y


def test_event_default_position_cycles_through_table():
    assert event_default_position_px(0) == (760, 1300)
    assert event_default_position_px(4) == (760, 1300)
    assert event_default_position_px(1) == (170, 1340)


def test_console_safe_text_escapes_emoji_for_windows_codepage():
    assert console_safe_text("Using cached emoji: 👀", "cp1252") == (
        "Using cached emoji: \\U0001f440"
    )


def test_choose_emoji_events_assigns_default_position_in_valid_range():
    words = words_at((1.0, "wow"), (5.0, "amazing"), (10.0, "incredible"))
    candidates = [
        {"start": 1.0, "emoji": "\U0001f631", "matched_word": "wow"},
        {"start": 5.0, "emoji": "\U0001f929", "matched_word": "amazing"},
    ]

    events = choose_emoji_events(candidates, words, "PUNCHY")

    assert events
    for event in events:
        assert 0.0 <= event["position_x"] <= 1.0
        assert 0.0 <= event["position_y"] <= 1.0


def test_apply_emoji_position_overrides_carries_forward_manual_drag():
    fresh_events = [
        {
            "start": 5.02,
            "end": 6.52,
            "emoji": "\U0001f929",
            "matched_word": "amazing",
            "position_x": 0.84,
            "position_y": 0.74,
        }
    ]
    previous_events = [
        {
            "start": 5.00,
            "end": 6.50,
            "emoji": "\U0001f929",
            "matched_word": "amazing",
            "position_x": 0.10,
            "position_y": 0.10,
            "manual_override": True,
        }
    ]

    merged = apply_emoji_position_overrides(fresh_events, previous_events)

    assert merged[0]["position_x"] == 0.10
    assert merged[0]["position_y"] == 0.10
    assert merged[0]["manual_override"] is True


def test_apply_emoji_position_overrides_ignores_non_manual_and_far_matches():
    fresh_events = [
        {
            "start": 5.0,
            "matched_word": "amazing",
            "position_x": 0.84,
            "position_y": 0.74,
        }
    ]

    # Not manually overridden -- should not be applied.
    not_manual = [
        {
            "start": 5.0,
            "matched_word": "amazing",
            "position_x": 0.10,
            "position_y": 0.10,
        }
    ]
    assert apply_emoji_position_overrides(fresh_events, not_manual)[0]["position_x"] == 0.84

    # Manually overridden but far outside the merge tolerance -- should not
    # be applied.
    far_away = [
        {
            "start": 40.0,
            "matched_word": "amazing",
            "position_x": 0.10,
            "position_y": 0.10,
            "manual_override": True,
        }
    ]
    assert apply_emoji_position_overrides(fresh_events, far_away)[0]["position_x"] == 0.84


def test_apply_emoji_position_overrides_no_previous_events_is_a_no_op():
    fresh_events = [{"start": 1.0, "matched_word": "wow", "position_x": 0.5, "position_y": 0.5}]
    assert apply_emoji_position_overrides(fresh_events, []) == fresh_events


def test_apply_emoji_position_overrides_matches_by_time_even_if_identity_changed():
    # The whole point of dropping identity matching: a previously-picked
    # emoji (different matched_word/asset than what a fresh replan would
    # choose for the same moment) must still be found and carried forward.
    fresh_events = [
        {
            "start": 5.0,
            "matched_word": "amazing",
            "emoji": "\U0001f929",
            "position_x": 0.5,
            "position_y": 0.5,
        }
    ]
    previous_events = [
        {
            "start": 5.05,
            "matched_word": "different_word_entirely",
            "emoji": "\U0001f440",
            "position_x": 0.2,
            "position_y": 0.2,
            "manual_override": True,
        }
    ]

    merged = apply_emoji_position_overrides(fresh_events, previous_events)
    assert merged[0]["position_x"] == 0.2
    assert merged[0]["position_y"] == 0.2
    # No content_override on the previous event -- emoji content is left
    # as whatever the fresh replan chose, only position carries forward.
    assert merged[0]["emoji"] == "\U0001f929"


def test_apply_emoji_position_overrides_carries_forward_picked_emoji_content():
    fresh_events = [
        {
            "start": 5.0,
            "matched_word": "amazing",
            "emoji": "\U0001f929",
            "position_x": 0.5,
            "position_y": 0.5,
        }
    ]
    previous_events = [
        {
            "start": 5.0,
            "matched_word": "amazing",
            "emoji": "custom reaction",
            "asset_path": "assets/emoji/12345-custom.png",
            "asset_description": "custom reaction",
            "asset_type": "local",
            "position_x": 0.5,
            "position_y": 0.5,
            "manual_override": True,
            "content_override": True,
        }
    ]

    merged = apply_emoji_position_overrides(fresh_events, previous_events)
    assert merged[0]["emoji"] == "custom reaction"
    assert merged[0]["asset_path"] == "assets/emoji/12345-custom.png"
    assert merged[0]["content_override"] is True


def test_apply_emoji_position_overrides_clears_stale_asset_when_switched_to_plain_emoji():
    fresh_events = [
        {
            "start": 5.0,
            "matched_word": "amazing",
            "emoji": "\U0001f929",
            "asset_path": "assets/emoji/old-local-asset.png",
            "position_x": 0.5,
            "position_y": 0.5,
        }
    ]
    previous_events = [
        {
            "start": 5.0,
            "matched_word": "amazing",
            "emoji": "\U0001f440",
            "position_x": 0.5,
            "position_y": 0.5,
            "manual_override": True,
            "content_override": True,
        }
    ]

    merged = apply_emoji_position_overrides(fresh_events, previous_events)
    assert merged[0]["emoji"] == "\U0001f440"
    assert "asset_path" not in merged[0]


def test_apply_emoji_position_overrides_does_not_double_match_one_previous_event():
    fresh_events = [
        {"start": 5.0, "position_x": 0.1, "position_y": 0.1},
        {"start": 5.1, "position_x": 0.2, "position_y": 0.2},
    ]
    previous_events = [
        {
            "start": 5.0,
            "position_x": 0.9,
            "position_y": 0.9,
            "manual_override": True,
        }
    ]

    merged = apply_emoji_position_overrides(fresh_events, previous_events)
    # Only the closer fresh event should consume the single previous
    # manual override -- the other keeps its own freshly-chosen position.
    matched = [event for event in merged if event["position_x"] == 0.9]
    assert len(matched) == 1
