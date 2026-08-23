from pathlib import Path

from sfx_engine import (
    category_cap,
    choose_event_category,
    event_end,
    event_start,
    filename_words,
    infer_category_from_words,
    stable_hash,
)


def test_stable_hash_is_deterministic():
    assert stable_hash("whoosh") == stable_hash("whoosh")
    assert stable_hash("whoosh") != stable_hash("impact")


def test_filename_words_strips_numeric_prefix_and_tokenizes():
    assert filename_words(Path("03_whoosh_fast.wav")) == {"whoosh", "fast"}
    assert filename_words(Path("bass_drop.mp3")) == {"bass", "drop"}


def test_infer_category_from_words_matches_known_aliases():
    assert infer_category_from_words({"coins", "cash"}) == "money"
    assert infer_category_from_words({"siren", "alarm"}) == "alert"


def test_infer_category_from_words_uses_valid_fallback():
    # A fallback that isn't a real category name is ignored; only a
    # recognized category label in CATEGORY_LABELS is honored.
    assert infer_category_from_words({"zzz", "qqq"}, fallback="bell") == "bell"
    assert infer_category_from_words({"zzz", "qqq"}, fallback="not-a-real-category") == "pop"
    assert infer_category_from_words({"zzz", "qqq"}) == "pop"


def test_category_cap_scales_with_energy():
    low_cap = category_cap("whoosh", "LOW")
    max_cap = category_cap("whoosh", "MAXIMUM")

    assert low_cap == 1
    assert max_cap == 2
    assert max_cap >= low_cap


def test_category_cap_unknown_category_is_uncapped():
    assert category_cap("nonexistent_category", "LOW") is None


def test_choose_event_category_avoids_repeating_immediately_preceding():
    event = {"category": "bell", "text": "plain text with no special words"}

    assert choose_event_category(event, []) == "bell"
    # With "bell" as the most recently used category, and an alternative
    # ("ding") available, it should not repeat "bell".
    assert choose_event_category(event, ["bell"]) == "ding"


def test_event_start_falls_back_through_field_names():
    assert event_start({"start": 5.0}) == 5.0
    assert event_start({"output_start": 3.0}) == 3.0
    assert event_start({"source_start": 1.0}) == 1.0
    assert event_start({}) == 0.0


def test_event_end_defaults_to_start_and_is_clamped():
    assert event_end({"start": 1.0, "end": 3.0}) == 3.0
    # No end provided -- defaults to start.
    assert event_end({"start": 1.0}) == 1.0
    # end before start is clamped up to start, never negative duration.
    assert event_end({"start": 5.0, "end": 2.0}) == 5.0
