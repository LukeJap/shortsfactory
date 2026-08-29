import copy

import pytest

from recap_media.sequence import (
    CADENCE_ORIGINAL_DIALOGUE,
    assemble_sequence,
    interweave_original_dialogue,
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


def _multi_shot_segment(dialogue_score=0.9, dialogue_candidates=None):
    # 3 distinct illustrative candidates, spread out, plus enough target
    # duration to force multiple shots (>= 2) so the segment is eligible.
    return _segment(
        candidate_visuals=[
            _candidate(0.0, 3.0, score=0.9),
            _candidate(20.0, 23.0, score=0.85),
            _candidate(40.0, 43.0, score=0.8),
        ],
        original_dialogue_candidates=(
            dialogue_candidates
            if dialogue_candidates is not None
            else [_candidate(100.0, 104.0, score=dialogue_score, reason="Strong original line.")]
        ),
    )


# ============================================================
# Eligibility / threshold
# ============================================================

def test_strong_candidate_gets_inserted():
    segment = _multi_shot_segment(dialogue_score=0.9)
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(sequence, _script([segment]))

    out_segment = result["segments"][0]
    assert out_segment["has_dialogue_insert"] is True
    treatments = [shot["treatment"] for shot in out_segment["shots"]]
    assert "original_dialogue" in treatments
    # narrator still resumes -- at least one illustrative shot survives
    assert "narration_over_source" in treatments


def test_weak_candidate_does_not_get_inserted():
    segment = _multi_shot_segment(dialogue_score=0.5)  # below default threshold
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(sequence, _script([segment]))

    out_segment = result["segments"][0]
    assert out_segment["has_dialogue_insert"] is False
    assert all(shot["treatment"] == "narration_over_source" for shot in out_segment["shots"])


def test_no_dialogue_candidates_no_insert():
    segment = _multi_shot_segment(dialogue_candidates=[])
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(sequence, _script([segment]))

    assert result["segments"][0]["has_dialogue_insert"] is False


def test_custom_threshold_can_reject_default_qualifying_score():
    segment = _multi_shot_segment(dialogue_score=0.9)
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(
        sequence, _script([segment]), score_threshold=0.95
    )

    assert result["segments"][0]["has_dialogue_insert"] is False


# ============================================================
# Eligibility guards
# ============================================================

def test_single_shot_segment_not_eligible():
    segment = _segment(
        candidate_visuals=[_candidate(0.0, 3.0, score=0.9)],
        original_dialogue_candidates=[_candidate(100.0, 104.0, score=0.99)],
    )
    sequence = assemble_sequence(_script([segment]), {"VO_001": 1.0})  # short -> 1 shot only

    assert len(sequence["segments"][0]["shots"]) == 1

    result = interweave_original_dialogue(sequence, _script([segment]))
    assert result["segments"][0]["has_dialogue_insert"] is False


def test_original_dialogue_hint_segment_untouched():
    segment = _segment(
        presentation_hint="original_dialogue",
        candidate_visuals=[_candidate(0.0, 3.0, score=0.9)],
        original_dialogue_candidates=[_candidate(100.0, 104.0, score=0.99)],
    )
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(sequence, _script([segment]))

    assert result["segments"][0]["has_dialogue_insert"] is False


def test_reaction_beat_hint_segment_untouched():
    segment = _segment(
        presentation_hint="reaction_beat",
        candidate_visuals=[_candidate(0.0, 3.0, score=0.9), _candidate(20.0, 23.0, score=0.9)],
        original_dialogue_candidates=[_candidate(100.0, 104.0, score=0.99)],
    )
    sequence = assemble_sequence(_script([segment]), {"VO_001": 3.0})

    result = interweave_original_dialogue(sequence, _script([segment]))

    assert result["segments"][0]["has_dialogue_insert"] is False


# ============================================================
# Duration/cadence discipline
# ============================================================

def test_insert_duration_capped_by_max_fraction():
    segment = _multi_shot_segment(dialogue_score=0.9)
    segment["original_dialogue_candidates"] = [
        _candidate(100.0, 200.0, score=0.9)  # a huge 100s span available
    ]
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(
        sequence, _script([segment]), max_fraction_of_segment=0.4
    )

    out_segment = result["segments"][0]
    insert_shot = next(
        shot for shot in out_segment["shots"] if shot["treatment"] == "original_dialogue"
    )
    # capped by max_fraction * original total duration, not the full 100s span
    assert insert_shot["duration"] <= 8.0 * 0.4 + 0.01
    assert insert_shot["duration"] <= CADENCE_ORIGINAL_DIALOGUE[1] + 0.01


def test_too_short_segment_skips_insert_rather_than_forcing_it():
    # total duration so small that even the minimum dialogue cadence
    # would exceed max_fraction_of_segment of it.
    segment = _segment(
        candidate_visuals=[_candidate(0.0, 1.0, score=0.9), _candidate(2.0, 3.0, score=0.9)],
        original_dialogue_candidates=[_candidate(100.0, 104.0, score=0.99)],
    )
    sequence = assemble_sequence(_script([segment]), {"VO_001": 2.0})

    result = interweave_original_dialogue(sequence, _script([segment]))

    assert result["segments"][0]["has_dialogue_insert"] is False


def test_total_duration_seconds_recomputed_after_insert():
    segment = _multi_shot_segment(dialogue_score=0.9)
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(sequence, _script([segment]))

    expected = round(sum(s["timeline_duration_seconds"] for s in result["segments"]), 3)
    assert result["total_duration_seconds"] == expected
    assert result["total_duration_seconds"] == pytest.approx(8.0)


# ============================================================
# Purity / non-mutation
# ============================================================

def test_does_not_mutate_input_sequence():
    segment = _multi_shot_segment(dialogue_score=0.9)
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})
    original = copy.deepcopy(sequence)

    interweave_original_dialogue(sequence, _script([segment]))

    assert sequence == original
