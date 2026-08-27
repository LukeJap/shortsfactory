import pytest

from recap_media.caption_alignment import (
    align_words_to_timing,
    build_narration_ass_dialogue_lines,
    build_narration_captions,
    build_narration_captions_ass_content,
    build_segment_narration_captions,
    load_narration_captions,
    tokenize_narration_text,
    write_narration_captions,
    write_narration_captions_ass_file,
)
from recap_media.loader import RecapInputError


def _recognized(text, start, end):
    return {"text": text, "start": start, "end": end}


# ============================================================
# tokenize_narration_text
# ============================================================

def test_tokenize_splits_on_whitespace_preserving_punctuation():
    words = tokenize_narration_text("It all starts when a letter shows up.")
    assert words == ["It", "all", "starts", "when", "a", "letter", "shows", "up."]


# ============================================================
# align_words_to_timing: happy path
# ============================================================

def test_perfect_match_borrows_recognized_timing():
    authoritative = ["Hello", "there", "friend"]
    recognized = [
        _recognized("Hello", 0.0, 0.4),
        _recognized("there", 0.4, 0.8),
        _recognized("friend", 0.8, 1.3),
    ]
    result = align_words_to_timing(authoritative, recognized)

    assert [w["text"] for w in result] == authoritative  # display text always authoritative
    assert all(w["matched"] for w in result)
    assert result[0]["start"] == 0.0
    assert result[2]["end"] == 1.3


def test_punctuation_and_casing_differences_still_match():
    authoritative = ["Hello,", "friend."]
    recognized = [_recognized("hello", 0.0, 0.4), _recognized("friend", 0.4, 0.9)]
    result = align_words_to_timing(authoritative, recognized)

    assert result[0]["text"] == "Hello,"  # authoritative punctuation preserved
    assert result[0]["matched"] is True
    assert result[1]["matched"] is True


def test_empty_authoritative_words_returns_empty():
    assert align_words_to_timing([], [_recognized("hello", 0.0, 0.4)]) == []


# ============================================================
# The core B6 requirement: recognition errors never overwrite text
# ============================================================

def test_misheard_word_keeps_authoritative_text_and_is_unmatched():
    authoritative = ["The", "cot", "was", "empty"]
    recognized = [
        _recognized("The", 0.0, 0.2),
        _recognized("cat", 0.2, 0.5),  # misheard "cot" as "cat"
        _recognized("was", 0.5, 0.7),
        _recognized("empty", 0.7, 1.2),
    ]
    result = align_words_to_timing(authoritative, recognized)

    assert result[1]["text"] == "cot"  # never "cat" -- authoritative wins
    assert result[1]["matched"] is False
    # still gets a sensible timestamp between its matched neighbors
    assert 0.2 <= result[1]["start"] <= 0.7
    assert 0.2 <= result[1]["end"] <= 0.7


def test_dropped_word_is_interpolated_between_neighbors():
    authoritative = ["one", "two", "three", "four"]
    recognized = [
        _recognized("one", 0.0, 0.3),
        # "two" never recognized at all
        _recognized("three", 0.9, 1.2),
        _recognized("four", 1.2, 1.5),
    ]
    result = align_words_to_timing(authoritative, recognized)

    assert result[1]["text"] == "two"
    assert result[1]["matched"] is False
    assert 0.3 <= result[1]["start"] <= 0.9
    assert 0.3 <= result[1]["end"] <= 0.9
    assert result[0]["matched"] and result[2]["matched"] and result[3]["matched"]


def test_extra_hallucinated_word_does_not_disrupt_surrounding_matches():
    authoritative = ["one", "two", "three"]
    recognized = [
        _recognized("one", 0.0, 0.3),
        _recognized("um", 0.3, 0.35),  # hallucinated filler word, not in authoritative text
        _recognized("two", 0.35, 0.6),
        _recognized("three", 0.6, 0.9),
    ]
    result = align_words_to_timing(authoritative, recognized)

    assert all(w["matched"] for w in result)
    assert result[1]["text"] == "two"
    assert result[1]["start"] == 0.35


def test_leading_unmatched_run_anchors_to_zero():
    authoritative = ["Well", "hello", "there"]
    recognized = [
        # "Well" never recognized
        _recognized("hello", 1.0, 1.4),
        _recognized("there", 1.4, 1.8),
    ]
    result = align_words_to_timing(authoritative, recognized)

    assert result[0]["text"] == "Well"
    assert result[0]["matched"] is False
    assert result[0]["start"] == 0.0
    assert result[0]["end"] <= 1.0


def test_trailing_unmatched_run_uses_fallback_duration():
    authoritative = ["Hello", "there", "friend"]
    recognized = [
        _recognized("Hello", 0.0, 0.4),
        _recognized("there", 0.4, 0.8),
        # "friend" never recognized -- no next anchor at all
    ]
    result = align_words_to_timing(authoritative, recognized)

    assert result[2]["text"] == "friend"
    assert result[2]["matched"] is False
    assert result[2]["start"] == 0.8
    assert result[2]["end"] > result[2]["start"]


def test_nothing_recognized_still_produces_sequential_timing_for_every_word():
    authoritative = ["one", "two", "three"]
    result = align_words_to_timing(authoritative, [])

    assert [w["text"] for w in result] == authoritative
    assert all(not w["matched"] for w in result)
    # strictly increasing, no overlaps, no crash
    for i in range(len(result) - 1):
        assert result[i]["end"] <= result[i + 1]["start"] + 1e-9


def test_multi_word_unmatched_run_weighted_by_word_length():
    authoritative = ["a", "extraordinarily", "big"]
    recognized = [
        _recognized("a", 0.0, 0.1),
        # "extraordinarily" and "big" both dropped
        _recognized("XXXX", 2.1, 2.2),  # unrelated recognized word, won't match "big" or the other one -- just anchors the next real word after it
    ]
    # Use an authoritative word after the gap that *does* match, so there's a real next anchor.
    authoritative = ["a", "extraordinarily", "big", "dog"]
    recognized = [
        _recognized("a", 0.0, 0.1),
        _recognized("dog", 2.1, 2.4),
    ]
    result = align_words_to_timing(authoritative, recognized)

    long_word_duration = result[1]["end"] - result[1]["start"]
    short_word_duration = result[2]["end"] - result[2]["start"]
    assert long_word_duration > short_word_duration  # "extraordinarily" gets more time than "big"


# ============================================================
# build_segment_narration_captions / build_narration_captions
# ============================================================

def test_build_segment_narration_captions_with_recognized_words():
    result = build_segment_narration_captions(
        "VO_001",
        "Hello there friend",
        recognized_words=[
            _recognized("Hello", 0.0, 0.4),
            _recognized("there", 0.4, 0.8),
            _recognized("friend", 0.8, 1.3),
        ],
    )
    assert result["segment_id"] == "VO_001"
    assert result["word_count"] == 3
    assert result["matched_word_count"] == 3


def test_build_segment_narration_captions_requires_input():
    with pytest.raises(ValueError):
        build_segment_narration_captions("VO_001", "Hello there")


def test_build_narration_captions_skips_visual_only():
    segments = [
        {
            "segment_id": "VO_001",
            "text": "Hello there",
            "presentation_hint": "narration_over_source",
        },
        {
            "segment_id": "VO_002",
            "text": "",
            "presentation_hint": "visual_only",
        },
    ]
    result = build_narration_captions(
        segments,
        recognized_words_by_segment={
            "VO_001": [_recognized("Hello", 0.0, 0.4), _recognized("there", 0.4, 0.8)],
        },
    )
    assert [s["segment_id"] for s in result["segments"]] == ["VO_001"]


# ============================================================
# build_narration_ass_dialogue_lines
# ============================================================

def test_ass_dialogue_lines_one_per_word():
    captions = build_segment_narration_captions(
        "VO_001", "Hello there",
        recognized_words=[_recognized("Hello", 0.0, 0.4), _recognized("there", 0.4, 0.9)],
    )
    lines = build_narration_ass_dialogue_lines(captions)

    assert len(lines) == 2
    assert all(line.startswith("Dialogue: 0,") for line in lines)
    assert "Hello" in lines[0]
    assert "there" in lines[1]


def test_ass_dialogue_lines_apply_time_offset():
    captions = build_segment_narration_captions(
        "VO_001", "Hi",
        recognized_words=[_recognized("Hi", 0.0, 0.5)],
    )
    lines_no_offset = build_narration_ass_dialogue_lines(captions, time_offset_seconds=0.0)
    lines_with_offset = build_narration_ass_dialogue_lines(captions, time_offset_seconds=10.0)

    assert lines_no_offset[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.50")
    assert lines_with_offset[0].startswith("Dialogue: 0,0:00:10.00,0:00:10.50")


def test_ass_dialogue_lines_escape_special_characters():
    captions = build_segment_narration_captions(
        "VO_001", "{weird}",
        recognized_words=[_recognized("weird", 0.0, 0.4)],
    )
    lines = build_narration_ass_dialogue_lines(captions)
    assert r"\{weird\}" in lines[0]


# ============================================================
# build_narration_captions_ass_content / write_narration_captions_ass_file
# ============================================================

def test_ass_content_has_valid_header_sections():
    narration_captions = build_narration_captions(
        [{"segment_id": "VO_001", "text": "Hi there", "presentation_hint": "narration_over_source"}],
        recognized_words_by_segment={
            "VO_001": [_recognized("Hi", 0.0, 0.3), _recognized("there", 0.3, 0.7)],
        },
    )
    voiceover_clips = [{"id": "VO_001", "start": 0.0, "active": True}]

    content = build_narration_captions_ass_content(narration_captions, voiceover_clips)

    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "Style: Recap," in content
    assert "Dialogue: 0," in content


def test_ass_content_offsets_by_voiceover_clip_start():
    narration_captions = build_narration_captions(
        [{"segment_id": "VO_001", "text": "Hi", "presentation_hint": "narration_over_source"}],
        recognized_words_by_segment={"VO_001": [_recognized("Hi", 0.0, 0.5)]},
    )
    voiceover_clips = [{"id": "VO_001", "start": 20.0, "active": True}]

    content = build_narration_captions_ass_content(narration_captions, voiceover_clips)

    assert "Dialogue: 0,0:00:20.00,0:00:20.50" in content


def test_ass_content_skips_disabled_or_deleted_segments():
    narration_captions = build_narration_captions(
        [
            {"segment_id": "VO_001", "text": "Hi", "presentation_hint": "narration_over_source"},
            {"segment_id": "VO_002", "text": "Bye", "presentation_hint": "narration_over_source"},
        ],
        recognized_words_by_segment={
            "VO_001": [_recognized("Hi", 0.0, 0.5)],
            "VO_002": [_recognized("Bye", 0.0, 0.5)],
        },
    )
    voiceover_clips = [
        {"id": "VO_001", "start": 0.0, "active": False},  # disabled
        {"id": "VO_002", "start": 5.0, "active": True, "deleted": False},
    ]

    content = build_narration_captions_ass_content(narration_captions, voiceover_clips)

    assert "Hi" not in content
    assert "Bye" in content


def test_ass_content_uses_default_margin_without_portrait_plan():
    narration_captions = build_narration_captions(
        [{"segment_id": "VO_001", "text": "Hi", "presentation_hint": "narration_over_source"}],
        recognized_words_by_segment={"VO_001": [_recognized("Hi", 0.0, 0.5)]},
    )
    content = build_narration_captions_ass_content(
        narration_captions, [{"id": "VO_001", "start": 0.0, "active": True}]
    )
    assert ",70,70,250,1" in content  # DEFAULT_ASS_MARGIN_V


def test_ass_content_anchors_to_content_rect_with_portrait_plan():
    narration_captions = build_narration_captions(
        [{"segment_id": "VO_001", "text": "Hi", "presentation_hint": "narration_over_source"}],
        recognized_words_by_segment={"VO_001": [_recognized("Hi", 0.0, 0.5)]},
    )
    # A 4:3 source in a 1920-tall canvas: content occupies y=555..1365,
    # so captions should sit just below content's bottom edge (1365),
    # not 250px up from the full canvas's own bottom edge (1920).
    portrait_plan = {
        "canvas_height": 1920,
        "content_y": 555,
        "content_height": 810,
    }
    content = build_narration_captions_ass_content(
        narration_captions,
        [{"id": "VO_001", "start": 0.0, "active": True}],
        portrait_plan=portrait_plan,
    )
    expected_margin = (1920 - (555 + 810)) + 40
    assert f",70,70,{expected_margin},1" in content
    assert expected_margin != 250


def test_write_narration_captions_ass_file(tmp_path):
    narration_captions = build_narration_captions(
        [{"segment_id": "VO_001", "text": "Hi", "presentation_hint": "narration_over_source"}],
        recognized_words_by_segment={"VO_001": [_recognized("Hi", 0.0, 0.5)]},
    )
    path = tmp_path / "narration.ass"
    write_narration_captions_ass_file(
        narration_captions, [{"id": "VO_001", "start": 0.0, "active": True}], path
    )
    assert path.exists()
    assert "[Events]" in path.read_text(encoding="utf-8")


# ============================================================
# write/load round trip
# ============================================================

def test_write_and_load_round_trip(tmp_path):
    captions = build_narration_captions(
        [{"segment_id": "VO_001", "text": "Hi", "presentation_hint": "narration_over_source"}],
        recognized_words_by_segment={"VO_001": [_recognized("Hi", 0.0, 0.3)]},
    )
    path = tmp_path / "narration_captions.json"
    write_narration_captions(captions, path)
    loaded = load_narration_captions(path)
    assert loaded == captions


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(RecapInputError, match="not found"):
        load_narration_captions(tmp_path / "does_not_exist.json")


def test_load_malformed_json_raises(tmp_path):
    path = tmp_path / "narration_captions.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RecapInputError, match="not valid JSON"):
        load_narration_captions(path)


def test_load_missing_segments_field_raises(tmp_path):
    path = tmp_path / "narration_captions.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(RecapInputError, match="segments"):
        load_narration_captions(path)
