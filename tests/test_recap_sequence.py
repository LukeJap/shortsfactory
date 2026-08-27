import copy

import pytest

from recap_media.loader import RecapInputError
from recap_media.sequence import (
    CADENCE_IMPORTANT,
    CADENCE_NORMAL_ILLUSTRATIVE,
    CADENCE_ORIGINAL_DIALOGUE,
    CADENCE_QUICK_REACTION,
    assemble_sequence,
    load_recap_sequence,
    write_recap_sequence,
)


def _candidate(start, end, score=0.9, reason="test"):
    return {"start": start, "end": end, "score": score, "reason": reason}


def _segment(
    segment_id="VO_001",
    order=1,
    text="Some narration text goes here for this segment.",
    beat_ids=None,
    presentation_hint="narration_over_source",
    importance=0.5,
    candidate_visuals=None,
    original_dialogue_candidates=None,
):
    return {
        "segment_id": segment_id,
        "order": order,
        "text": text,
        "beat_ids": beat_ids or ["B001"],
        "presentation_hint": presentation_hint,
        "importance": importance,
        "candidate_visuals": candidate_visuals if candidate_visuals is not None else [],
        "original_dialogue_candidates": (
            original_dialogue_candidates if original_dialogue_candidates is not None else []
        ),
    }


def _script(segments, target_duration_seconds=120):
    return {
        "schema_version": 1,
        "target_duration_seconds": target_duration_seconds,
        "target_word_count": 300,
        "voice_style": "fast_story_recap",
        "segments": segments,
    }


# ============================================================
# Basic shot coverage
# ============================================================

def test_single_candidate_covers_target_duration():
    segment = _segment(candidate_visuals=[_candidate(10.0, 20.0)])
    result = assemble_sequence(_script([segment]), {"VO_001": 2.0})

    shots = result["segments"][0]["shots"]
    assert len(shots) == 1
    assert shots[0]["start"] == 10.0
    assert shots[0]["reused"] is False
    assert result["segments"][0]["shots_total_duration_seconds"] >= 1.9


def test_multiple_shots_used_when_target_exceeds_cadence_high():
    segment = _segment(
        candidate_visuals=[
            _candidate(0.0, 10.0, score=0.9),
            _candidate(50.0, 60.0, score=0.85),
        ],
    )
    # target well beyond a single shot's cadence_high (3.0s for default importance)
    result = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    shots = result["segments"][0]["shots"]
    assert len(shots) >= 3
    for shot in shots:
        assert shot["duration"] <= CADENCE_NORMAL_ILLUSTRATIVE[1] + 0.01


def test_shots_never_exceed_their_own_candidate_bounds():
    segment = _segment(candidate_visuals=[_candidate(100.0, 101.0)])  # only 1s available
    result = assemble_sequence(_script([segment]), {"VO_001": 5.0})

    for shot in result["segments"][0]["shots"]:
        assert shot["start"] >= 100.0
        assert shot["end"] <= 101.0 + 1e-6


# ============================================================
# Reuse avoidance
# ============================================================

def test_prefers_distinct_candidates_over_reuse_when_available():
    segment = _segment(
        candidate_visuals=[
            _candidate(0.0, 3.0, score=0.9),
            _candidate(20.0, 23.0, score=0.89),
        ],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 5.5})

    shots = result["segments"][0]["shots"]
    starts = {shot["start"] for shot in shots}
    assert len(starts) == 2  # both distinct candidates used, not one repeated
    assert all(shot["reused"] is False for shot in shots)


def test_falls_back_to_reuse_when_candidates_exhausted():
    segment = _segment(candidate_visuals=[_candidate(0.0, 2.0, score=0.9)])
    result = assemble_sequence(_script([segment]), {"VO_001": 10.0})

    shots = result["segments"][0]["shots"]
    assert len(shots) <= 20  # MAX_SHOTS_PER_SEGMENT respected, no infinite loop
    assert any(shot["reused"] for shot in shots)


def test_reuse_avoidance_is_global_across_segments():
    shared_candidate = _candidate(0.0, 3.0, score=0.99)
    other_candidate = _candidate(50.0, 53.0, score=0.5)
    seg1 = _segment(
        segment_id="VO_001",
        order=1,
        candidate_visuals=[shared_candidate, other_candidate],
    )
    seg2 = _segment(
        segment_id="VO_002",
        order=2,
        candidate_visuals=[shared_candidate, other_candidate],
    )
    result = assemble_sequence(
        _script([seg1, seg2]), {"VO_001": 2.0, "VO_002": 2.0}
    )

    # seg1 takes the high-scoring shared candidate; seg2 should prefer the
    # other (unused) candidate over reusing seg1's, despite the lower score.
    seg2_shots = result["segments"][1]["shots"]
    assert seg2_shots[0]["start"] == 50.0
    assert seg2_shots[0]["reused"] is False


# ============================================================
# Presentation-hint-specific behavior
# ============================================================

def test_original_dialogue_uses_dialogue_candidates_and_wider_cadence():
    segment = _segment(
        presentation_hint="original_dialogue",
        candidate_visuals=[_candidate(0.0, 100.0, score=0.99)],
        original_dialogue_candidates=[_candidate(200.0, 206.0, score=0.5)],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 3.0})

    shots = result["segments"][0]["shots"]
    assert shots[0]["source_list"] == "original_dialogue_candidates"
    assert shots[0]["start"] == 200.0
    assert shots[0]["duration"] <= CADENCE_ORIGINAL_DIALOGUE[1] + 0.01


def test_reaction_beat_uses_quick_cadence_band():
    segment = _segment(
        presentation_hint="reaction_beat",
        candidate_visuals=[_candidate(0.0, 10.0, score=0.9)],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 5.0})

    for shot in result["segments"][0]["shots"]:
        assert shot["duration"] <= CADENCE_QUICK_REACTION[1] + 0.01


def test_important_segment_uses_wider_cadence_than_normal():
    high_importance = _segment(
        segment_id="VO_HIGH",
        importance=0.9,
        candidate_visuals=[_candidate(0.0, 100.0, score=0.9)],
    )
    low_importance = _segment(
        segment_id="VO_LOW",
        importance=0.2,
        candidate_visuals=[_candidate(0.0, 100.0, score=0.9)],
    )
    high_result = assemble_sequence(_script([high_importance]), {"VO_HIGH": 10.0})
    low_result = assemble_sequence(_script([low_importance]), {"VO_LOW": 10.0})

    high_shot_duration = high_result["segments"][0]["shots"][0]["duration"]
    low_shot_duration = low_result["segments"][0]["shots"][0]["duration"]

    assert high_shot_duration == pytest.approx(CADENCE_IMPORTANT[1], abs=0.01)
    assert low_shot_duration == pytest.approx(CADENCE_NORMAL_ILLUSTRATIVE[1], abs=0.01)


def test_visual_only_gets_default_duration_without_narration_durations():
    segment = _segment(
        presentation_hint="visual_only",
        text="",
        importance=0.5,
        candidate_visuals=[_candidate(0.0, 100.0, score=0.9)],
    )
    result = assemble_sequence(_script([segment]), narration_durations={})

    out_segment = result["segments"][0]
    assert out_segment["narration_duration_source"] == "visual_only_default"
    assert out_segment["shots"]


def test_falls_back_to_word_count_estimate_when_no_measured_duration():
    segment = _segment(text="one two three four five six seven eight")
    result = assemble_sequence(_script([segment]), narration_durations={})

    out_segment = result["segments"][0]
    assert out_segment["narration_duration_source"] == "estimated"
    assert out_segment["narration_duration_seconds"] > 0


def test_falls_back_to_other_candidate_list_when_preferred_is_empty():
    segment = _segment(
        presentation_hint="original_dialogue",
        candidate_visuals=[_candidate(5.0, 8.0, score=0.7)],
        original_dialogue_candidates=[],  # nothing in the preferred list
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 2.0})

    shots = result["segments"][0]["shots"]
    assert shots
    assert shots[0]["source_list"] == "candidate_visuals"


# ============================================================
# Warnings
# ============================================================

def test_no_candidates_produces_warning_not_crash():
    segment = _segment(candidate_visuals=[], original_dialogue_candidates=[])
    result = assemble_sequence(_script([segment]), {"VO_001": 2.0})

    out_segment = result["segments"][0]
    assert out_segment["shots"] == []
    assert "no usable source candidates" in out_segment["warnings"][0].lower()
    assert result["sequence_warnings"]


def test_backward_jump_between_segments_is_flagged():
    seg1 = _segment(
        segment_id="VO_001", order=1,
        candidate_visuals=[_candidate(500.0, 503.0, score=0.9)],
    )
    seg2 = _segment(
        segment_id="VO_002", order=2,
        candidate_visuals=[_candidate(10.0, 13.0, score=0.9)],
    )
    result = assemble_sequence(
        _script([seg1, seg2]), {"VO_001": 2.0, "VO_002": 2.0}
    )

    assert result["segments"][1]["warnings"]
    assert any("jumps backward" in w for w in result["sequence_warnings"])


def test_forward_progression_has_no_warnings():
    seg1 = _segment(
        segment_id="VO_001", order=1,
        candidate_visuals=[_candidate(10.0, 13.0, score=0.9)],
    )
    seg2 = _segment(
        segment_id="VO_002", order=2,
        candidate_visuals=[_candidate(20.0, 23.0, score=0.9)],
    )
    result = assemble_sequence(
        _script([seg1, seg2]), {"VO_001": 2.0, "VO_002": 2.0}
    )

    assert result["segments"][0]["warnings"] == []
    assert result["segments"][1]["warnings"] == []
    assert result["sequence_warnings"] == []


# ============================================================
# write/load round trip
# ============================================================

def test_write_and_load_round_trip(tmp_path):
    segment = _segment(candidate_visuals=[_candidate(0.0, 5.0)])
    sequence = assemble_sequence(_script([segment]), {"VO_001": 2.0})

    path = tmp_path / "recap_sequence.json"
    write_recap_sequence(sequence, path)
    loaded = load_recap_sequence(path)

    assert loaded == sequence


def test_load_recap_sequence_missing_file_raises(tmp_path):
    with pytest.raises(RecapInputError, match="not found"):
        load_recap_sequence(tmp_path / "does_not_exist.json")


def test_load_recap_sequence_malformed_raises(tmp_path):
    path = tmp_path / "recap_sequence.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RecapInputError, match="not valid JSON"):
        load_recap_sequence(path)


def test_load_recap_sequence_missing_segments_field_raises(tmp_path):
    path = tmp_path / "recap_sequence.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(RecapInputError, match="segments"):
        load_recap_sequence(path)


def test_assemble_sequence_does_not_mutate_input_script():
    segment = _segment(candidate_visuals=[_candidate(0.0, 5.0)])
    script = _script([segment])
    original = copy.deepcopy(script)

    assemble_sequence(script, {"VO_001": 2.0})

    assert script == original
