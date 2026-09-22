from recap_media.expression_tags import (
    display_text_for_segment,
    strip_tags,
    tts_text_for_segment,
)


# ============================================================
# strip_tags
# ============================================================

def test_strip_tags_removes_a_tag_and_collapses_whitespace():
    assert strip_tags("<chuckle> The Krusty Krab is open.") == "The Krusty Krab is open."


def test_strip_tags_removes_every_known_tag():
    text = "<laugh> <sigh> <chuckle> <cough> <sniffle> <groan> <yawn> <gasp> done."
    assert strip_tags(text) == "done."


def test_strip_tags_is_case_insensitive():
    assert strip_tags("<GASP> Oh no.") == "Oh no."


def test_strip_tags_noop_on_plain_text():
    assert strip_tags("Nothing unusual here.") == "Nothing unusual here."


def test_strip_tags_on_text_that_is_only_a_tag_returns_empty():
    assert strip_tags("<gasp>") == ""


def test_strip_tags_handles_empty_string():
    assert strip_tags("") == ""


# ============================================================
# tts_text_for_segment / display_text_for_segment -- the derivation model
# ============================================================

def test_tts_text_falls_back_to_text_when_no_override():
    segment = {"text": "<chuckle> Hello there."}
    assert tts_text_for_segment(segment) == "<chuckle> Hello there."


def test_tts_text_prefers_explicit_override():
    segment = {
        "text": "<chuckle> Hello there.",
        "tts_text": "<chuckle> Hello there, spoken differently.",
    }
    assert tts_text_for_segment(segment) == "<chuckle> Hello there, spoken differently."


def test_display_text_strips_tags_from_text_when_no_override():
    segment = {"text": "<chuckle> The Krusty Krab is really pleasant."}
    assert display_text_for_segment(segment) == "The Krusty Krab is really pleasant."


def test_display_text_prefers_explicit_override():
    # An explicit display_text overrides the derived one -- a future
    # authoring UI can write it without every script needing a rewrite.
    segment = {
        "text": "<chuckle> Hello there.",
        "display_text": "Hello there, friend.",
    }
    assert display_text_for_segment(segment) == "Hello there, friend."


def test_display_text_with_no_tags_matches_text():
    segment = {"text": "Nothing unusual here."}
    assert display_text_for_segment(segment) == "Nothing unusual here."
