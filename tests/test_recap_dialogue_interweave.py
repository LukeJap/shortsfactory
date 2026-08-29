import copy

import pytest

from recap_media.sequence import (
    assemble_sequence,
    interweave_original_dialogue,
    voiceover_timing_by_segment,
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
    segment = _multi_shot_segment(dialogue_score=0.3)  # below relevance floor
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

def test_accepted_dialogue_candidate_keeps_its_complete_range():
    segment = _multi_shot_segment(dialogue_score=0.9)
    segment["original_dialogue_candidates"] = [
        _candidate(100.0, 112.36, score=0.9)  # complete payoff/exchange
    ]
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    result = interweave_original_dialogue(
        sequence, _script([segment]), max_fraction_of_segment=0.4
    )

    out_segment = result["segments"][0]
    insert_shot = next(
        shot for shot in out_segment["shots"] if shot["treatment"] == "original_dialogue"
    )
    assert (insert_shot["start"], insert_shot["end"]) == (100.0, 112.36)
    assert insert_shot["duration"] == pytest.approx(12.36)
    assert (insert_shot["candidate_start"], insert_shot["candidate_end"]) == (100.0, 112.36)


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
    assert result["total_duration_seconds"] == pytest.approx(12.0)


# ============================================================
# Source-audio recap policy
# ============================================================

def test_relevant_candidates_create_four_distinct_source_audio_inserts():
    segments = [
        _multi_shot_segment(
            dialogue_score=score,
            dialogue_candidates=[
                _candidate(start, start + 3.5, score=score, reason=reason)
            ],
        )
        for start, score, reason in (
            (100.0, 0.77, "Character choice reaction."),
            (200.0, 0.83, "Emotional plea."),
            (300.0, 0.48, "Payoff reversal."),
            (400.0, 0.91, "Final punchline reaction."),
        )
    ]
    for order, segment in enumerate(segments, start=1):
        segment["segment_id"] = f"VO_{order:03d}"
        segment["order"] = order
        segment["beat_ids"] = [f"B{order:03d}"]
        visual_offset = (order - 1) * 100.0
        for candidate in segment["candidate_visuals"]:
            candidate["start"] += visual_offset
            candidate["end"] += visual_offset

    script = _script(segments)
    sequence = assemble_sequence(
        script, {segment["segment_id"]: 8.0 for segment in segments}
    )
    result = interweave_original_dialogue(sequence, script)

    inserts = [
        shot
        for segment in result["segments"]
        for shot in segment["shots"]
        if shot.get("source_audio_insert")
    ]
    assert len(inserts) == 4
    assert result["source_audio_insert_count"] == 4
    assert all(shot["duration"] == pytest.approx(3.5) for shot in inserts)
    assert [(shot["start"], shot["end"]) for shot in inserts] == [
        (100.0, 103.5),
        (200.0, 203.5),
        (300.0, 303.5),
        (400.0, 403.5),
    ]
    assert [shot["timeline_start_seconds"] for shot in inserts] == sorted(
        shot["timeline_start_seconds"] for shot in inserts
    )
    assert result["total_duration_seconds"] == pytest.approx(46.0)
    assert result["visual_hold_duration_seconds"] == 0.0
    assert result["visual_coverage_shortfall_seconds"] == 0.0


def test_verified_evidence_fills_source_audio_slots_when_script_metadata_is_sparse():
    segments = []
    beats = []
    for order in range(1, 5):
        beat_id = f"B{order:03d}"
        segment = _multi_shot_segment(dialogue_candidates=[])
        segment.update(
            {
                "segment_id": f"VO_{order:03d}",
                "order": order,
                "beat_ids": [beat_id],
            }
        )
        segments.append(segment)
        start = 100.0 * order
        beats.append(
            {
                "beat_id": beat_id,
                "source_evidence": [
                    {"start": start, "end": start + 4.0, "confidence": 0.6}
                ],
            }
        )

    script = _script(segments)
    sequence = assemble_sequence(
        script,
        {segment["segment_id"]: 8.0 for segment in segments},
        verified_story_map={"beats": beats},
    )
    result = interweave_original_dialogue(
        sequence, script, verified_story_map={"beats": beats}
    )

    inserts = [
        shot
        for segment in result["segments"]
        for shot in segment["shots"]
        if shot.get("source_audio_insert")
    ]
    assert len(inserts) == 4
    assert {shot["candidate_origin"] for shot in inserts} == {"verified_story_map"}
    assert [shot["beat_id"] for shot in inserts] == ["B001", "B002", "B003", "B004"]


def test_source_audio_windows_pause_narration_and_shift_following_voiceover():
    first = _multi_shot_segment(dialogue_score=0.9)
    second = _multi_shot_segment(
        dialogue_score=0.9,
        dialogue_candidates=[_candidate(200.0, 204.0, score=0.9, reason="Later reaction.")],
    )
    first.update({"segment_id": "VO_001", "order": 1, "beat_ids": ["B001"]})
    second.update({"segment_id": "VO_002", "order": 2, "beat_ids": ["B002"]})
    for candidate in second["candidate_visuals"]:
        candidate["start"] += 100.0
        candidate["end"] += 100.0
    script = _script([first, second])
    sequence = assemble_sequence(script, {"VO_001": 8.0, "VO_002": 8.0})
    result = interweave_original_dialogue(sequence, script)

    timing = voiceover_timing_by_segment(result)
    pauses = timing["VO_001"]["dialogue_pauses"]
    assert len(pauses) == 1
    assert 0.0 < pauses[0]["narration_offset_seconds"] < 8.0
    assert pauses[0]["duration_seconds"] == 4.0
    assert timing["VO_002"]["start"] == pytest.approx(12.0)
    assert timing["VO_002"]["end"] == pytest.approx(24.0)


# ============================================================
# Purity / non-mutation
# ============================================================

def test_does_not_mutate_input_sequence():
    segment = _multi_shot_segment(dialogue_score=0.9)
    sequence = assemble_sequence(_script([segment]), {"VO_001": 8.0})
    original = copy.deepcopy(sequence)

    interweave_original_dialogue(sequence, _script([segment]))

    assert sequence == original
