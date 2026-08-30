import copy

import pytest

from recap_media.loader import RecapInputError
from recap_media.sequence import (
    CADENCE_DETAIL,
    CADENCE_ILLUSTRATIVE,
    CADENCE_IMPORTANT,
    CADENCE_ORIGINAL_DIALOGUE,
    CADENCE_PAYOFF,
    CADENCE_REACTION,
    DEFAULT_VISUAL_FUNCTION,
    MAX_MOVING_COVERAGE_SHOT_SECONDS,
    assemble_sequence,
    infer_visual_function,
    interweave_original_dialogue,
    load_recap_sequence,
    write_recap_sequence,
)


def _candidate(
    start,
    end,
    score=0.9,
    reason="test",
    visual_function=None,
    beat_id=None,
):
    candidate = {"start": start, "end": end, "score": score, "reason": reason}
    if visual_function is not None:
        candidate["visual_function"] = visual_function
    if beat_id is not None:
        candidate["beat_id"] = beat_id
    return candidate


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


def _story_map(*beats):
    return {"schema_version": 1, "beats": list(beats)}


def _beat(beat_id, order, *evidence):
    return {
        "beat_id": beat_id,
        "order": order,
        "summary": f"Verified event for {beat_id}",
        "importance": 0.7,
        "source_evidence": list(evidence),
    }


def _evidence(start, end, confidence=0.9, evidence_type="local_video"):
    return {
        "start": start,
        "end": end,
        "confidence": confidence,
        "type": evidence_type,
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


def test_narration_timeline_uses_moving_source_coverage_before_any_hold():
    segment = _segment(candidate_visuals=[_candidate(10.0, 20.0)])

    result = assemble_sequence(_script([segment]), {"VO_001": 8.0})

    out_segment = result["segments"][0]
    assert out_segment["shots_total_duration_seconds"] == pytest.approx(8.0)
    assert out_segment["timeline_duration_seconds"] == pytest.approx(8.0)
    assert all(shot["hold_duration_seconds"] == 0.0 for shot in out_segment["shots"])
    assert any(shot.get("coverage_mode") == "verified_range_extension" for shot in out_segment["shots"])
    assert result["raw_source_duration_seconds"] == pytest.approx(8.0)
    assert result["total_duration_seconds"] == pytest.approx(8.0)
    assert result["visual_coverage_shortfall_seconds"] == 0.0


def test_measured_narration_duration_extends_visual_coverage_over_the_old_plan():
    segment = _segment(candidate_visuals=[_candidate(10.0, 20.0)])

    old_plan = assemble_sequence(_script([segment]), {"VO_001": 5.6})
    measured = assemble_sequence(_script([segment]), {"VO_001": 6.0})

    assert old_plan["segments"][0]["timeline_duration_seconds"] == pytest.approx(5.6)
    assert measured["segments"][0]["narration_duration_seconds"] == pytest.approx(6.0)
    assert measured["segments"][0]["timeline_duration_seconds"] >= 6.0
    assert measured["segments"][0]["shots_total_duration_seconds"] >= 6.0
    assert measured["segments"][0]["visual_coverage_shortfall_seconds"] == 0.0


def test_moving_coverage_extends_only_within_candidate_bounds():
    segment = _segment(
        candidate_visuals=[
            _candidate(10.0, 18.0),
            _candidate(20.0, 28.0),
        ]
    )

    result = assemble_sequence(_script([segment]), {"VO_001": 10.0})

    shots = result["segments"][0]["shots"]
    assert all(10.0 <= shot["start"] <= 28.0 for shot in shots)
    assert all(shot["end"] <= 18.0 or shot["start"] >= 20.0 for shot in shots)
    assert all(shot["hold_duration_seconds"] == 0.0 for shot in shots)
    assert sum(shot["duration"] for shot in shots) == pytest.approx(10.0)
    assert all(shot["duration"] <= MAX_MOVING_COVERAGE_SHOT_SECONDS for shot in shots)


def test_dialogue_insert_keeps_unused_verified_context_as_moving_coverage():
    segment = _segment(
        beat_ids=["B018"],
        candidate_visuals=[_candidate(120.0, 123.78, score=0.95)],
        original_dialogue_candidates=[_candidate(120.0, 123.78, score=0.99)],
    )
    story_map = _story_map(
        _beat("B017", 17, _evidence(100.0, 110.0)),
        _beat("B018", 18, _evidence(120.0, 123.78)),
    )

    assembled = assemble_sequence(
        _script([segment]), {"VO_001": 13.739}, verified_story_map=story_map
    )
    result = interweave_original_dialogue(assembled, _script([segment]))
    out_segment = result["segments"][0]
    shots = out_segment["shots"]

    assert out_segment["shots_total_duration_seconds"] == pytest.approx(17.519)
    assert out_segment["timeline_duration_seconds"] == pytest.approx(17.519)
    assert out_segment["visual_hold_duration_seconds"] == 0.0
    assert out_segment["visual_coverage_shortfall_seconds"] == 0.0
    assert all(shot["duration"] <= MAX_MOVING_COVERAGE_SHOT_SECONDS for shot in shots)
    assert all(100.0 <= shot["start"] and shot["end"] <= 123.78 for shot in shots)
    assert len({(shot["start"], shot["end"]) for shot in shots}) == len(shots)
    assert any(
        shot.get("beat_id") == "B018"
        and shot.get("coverage_mode") == "contiguous_local_context"
        for shot in shots
    )


def test_multiple_distinct_candidates_are_all_used_for_a_long_target():
    segment = _segment(
        candidate_visuals=[
            _candidate(0.0, 10.0, score=0.90, reason="one"),
            _candidate(30.0, 40.0, score=0.89, reason="two"),
            _candidate(60.0, 70.0, score=0.88, reason="three"),
        ],
    )
    # target well beyond a single shot's cadence_high (2.5s for default importance)
    result = assemble_sequence(_script([segment]), {"VO_001": 7.0})

    shots = result["segments"][0]["shots"]
    assert len(shots) == 3
    starts = {shot["start"] for shot in shots}
    assert starts == {0.0, 30.0, 60.0}
    assert all(shot["reused"] is False for shot in shots)
    for shot in shots:
        assert shot["duration"] <= MAX_MOVING_COVERAGE_SHOT_SECONDS + 0.01


def test_shots_never_exceed_their_own_candidate_bounds():
    segment = _segment(candidate_visuals=[_candidate(100.0, 101.0)])  # only 1s available
    result = assemble_sequence(_script([segment]), {"VO_001": 5.0})

    for shot in result["segments"][0]["shots"]:
        assert shot["start"] >= 100.0
        assert shot["end"] <= 101.0 + 1e-6


def test_multi_beat_segment_supplements_script_candidates_from_assigned_beats():
    segment = _segment(
        beat_ids=["B001", "B002", "B003"],
        candidate_visuals=[_candidate(10.0, 13.0, score=0.95)],
    )
    story_map = _story_map(
        _beat("B001", 1, _evidence(10.0, 13.0)),
        _beat("B002", 2, _evidence(30.0, 33.0)),
        _beat("B003", 3, _evidence(50.0, 53.0)),
    )

    result = assemble_sequence(
        _script([segment]), {"VO_001": 7.0}, verified_story_map=story_map
    )

    shots = result["segments"][0]["shots"]
    assert len(shots) == 3
    assert {shot["start"] for shot in shots} == {10.0, 30.0, 50.0}
    assert {shot["beat_id"] for shot in shots if shot["candidate_origin"] == "verified_story_map"} == {
        "B002",
        "B003",
    }


def test_multi_beat_moving_coverage_keeps_distinct_verified_beats_chronological():
    segment = _segment(
        beat_ids=["B006", "B008", "B009", "B012"],
        candidate_visuals=[
            _candidate(100.0, 106.0, score=0.95, beat_id="B006"),
            _candidate(130.0, 136.0, score=0.92, beat_id="B012"),
        ],
    )
    story_map = _story_map(
        _beat("B006", 6, _evidence(100.0, 106.0)),
        _beat("B008", 8, _evidence(110.0, 116.0)),
        _beat("B009", 9, _evidence(120.0, 126.0)),
        _beat("B012", 12, _evidence(130.0, 136.0)),
    )

    result = assemble_sequence(
        _script([segment]), {"VO_001": 20.0}, verified_story_map=story_map
    )

    shots = result["segments"][0]["shots"]
    assert [shot["start"] for shot in shots] == sorted(shot["start"] for shot in shots)
    assert {shot["beat_id"] for shot in shots} == {"B006", "B008", "B009", "B012"}
    assert result["segments"][0]["visual_coverage_shortfall_seconds"] == 0.0


def test_multi_beat_sequence_trades_redundant_coverage_for_unused_evidence_moment():
    segment = _segment(
        beat_ids=["B001", "B002"],
        candidate_visuals=[
            _candidate(10.0, 20.0, score=0.95, beat_id="B001"),
            _candidate(30.0, 40.0, score=0.90, beat_id="B002"),
        ],
    )
    story_map = _story_map(
        _beat("B001", 1, _evidence(10.0, 20.0)),
        _beat("B002", 2, _evidence(30.0, 40.0)),
    )

    result = assemble_sequence(
        _script([segment]), {"VO_001": 12.0}, verified_story_map=story_map
    )
    out_segment = result["segments"][0]
    shots = out_segment["shots"]
    trailing_moments = [
        shot for shot in shots if shot.get("coverage_mode") == "trailing_evidence_moment"
    ]

    assert len(trailing_moments) == 1
    assert trailing_moments[0]["beat_id"] == "B002"
    assert trailing_moments[0]["start"] >= 37.5
    assert out_segment["shots_total_duration_seconds"] == pytest.approx(12.0)
    assert out_segment["visual_hold_duration_seconds"] == 0.0
    assert out_segment["visual_coverage_shortfall_seconds"] == 0.0
    assert [shot["start"] for shot in shots] == sorted(shot["start"] for shot in shots)
    assert len({(shot["start"], shot["end"]) for shot in shots}) == len(shots)
    assert all(shot["duration"] <= MAX_MOVING_COVERAGE_SHOT_SECONDS for shot in shots)


def test_contiguous_context_never_leaves_verified_story_window():
    segment = _segment(beat_ids=["B001"], candidate_visuals=[_candidate(100.0, 102.0)])
    story_map = _story_map(_beat("B001", 1, _evidence(100.0, 102.0)))

    result = assemble_sequence(
        _script([segment]), {"VO_001": 4.0}, verified_story_map=story_map
    )

    shots = result["segments"][0]["shots"]
    assert all(100.0 <= shot["start"] and shot["end"] <= 102.0 for shot in shots)
    assert result["segments"][0]["visual_coverage_shortfall_seconds"] == pytest.approx(2.0)


def test_supplemental_evidence_is_limited_to_the_segment_assigned_beats():
    segment = _segment(beat_ids=["B001"], candidate_visuals=[])
    story_map = _story_map(
        _beat("B001", 1, _evidence(10.0, 13.0)),
        _beat("B999", 2, _evidence(90.0, 93.0, confidence=1.0)),
    )

    result = assemble_sequence(
        _script([segment]), {"VO_001": 5.0}, verified_story_map=story_map
    )

    shots = result["segments"][0]["shots"]
    assert shots
    assert all(shot["beat_id"] == "B001" for shot in shots)
    assert all(shot["start"] < 90.0 for shot in shots)
    assert all(shot["end"] < 90.0 for shot in shots)
    assert all(shot["candidate_origin"] in {"verified_story_map", "recap_script"} for shot in shots)


def test_verified_evidence_deduplicates_script_ranges_and_uses_bounded_moving_coverage():
    segment = _segment(
        beat_ids=["B001", "B002"],
        candidate_visuals=[_candidate(10.0, 30.0, score=0.95)],
    )
    story_map = _story_map(
        _beat("B001", 1, _evidence(10.0, 30.0)),
        _beat("B002", 2, _evidence(40.0, 60.0)),
    )

    result = assemble_sequence(
        _script([segment]), {"VO_001": 20.0}, verified_story_map=story_map
    )

    shots = result["segments"][0]["shots"]
    assert len(shots) > 2
    assert len({(shot["start"], shot["end"]) for shot in shots}) == len(shots)
    assert all(shot["reused"] is False for shot in shots)
    assert all(shot["duration"] <= MAX_MOVING_COVERAGE_SHOT_SECONDS for shot in shots)
    assert {shot["beat_id"] for shot in shots} == {"B001", "B002"}


def test_legacy_sequence_assembly_is_unchanged_without_a_story_map():
    segment = _segment(candidate_visuals=[_candidate(10.0, 13.0)])

    result = assemble_sequence(_script([segment]), {"VO_001": 2.0})

    shot = result["segments"][0]["shots"][0]
    assert shot["start"] == 10.0
    assert shot["candidate_origin"] == "recap_script"


def test_multi_beat_default_candidates_cover_distinct_beats():
    segment = _segment(beat_ids=["B001", "B002", "B003"])
    candidates = [
        _candidate(10.0, 13.0, score=0.5, beat_id="B001"),
        _candidate(20.0, 23.0, score=0.5, beat_id="B002"),
        _candidate(30.0, 33.0, score=0.5, beat_id="B003"),
    ]

    result = assemble_sequence(
        _script([_segment(beat_ids=["B001", "B002", "B003"], candidate_visuals=candidates)]),
        {"VO_001": 7.0},
    )

    assert {shot["beat_id"] for shot in result["segments"][0]["shots"]} == {
        "B001",
        "B002",
        "B003",
    }


def test_new_beat_coverage_beats_redundant_same_beat_when_quality_is_comparable():
    segment = _segment(
        beat_ids=["B001", "B002"],
        candidate_visuals=[
            _candidate(10.0, 13.0, score=0.8, beat_id="B001"),
            _candidate(20.0, 23.0, score=0.75, beat_id="B001"),
            _candidate(30.0, 33.0, score=0.7, beat_id="B002"),
        ],
    )

    result = assemble_sequence(_script([segment]), {"VO_001": 4.0})

    shots = result["segments"][0]["shots"]
    assert shots[0]["beat_id"] == "B001"
    assert shots[1]["beat_id"] == "B002"


def test_weak_new_beat_evidence_does_not_flood_a_sequence():
    segment = _segment(
        beat_ids=["B001", "B002", "B003"],
        candidate_visuals=[
            _candidate(10.0, 13.0, score=0.1, beat_id="B001"),
            _candidate(20.0, 23.0, score=0.1, beat_id="B002"),
            _candidate(30.0, 33.0, score=0.1, beat_id="B003"),
        ],
    )

    result = assemble_sequence(_script([segment]), {"VO_001": 7.0})

    assert len(result["segments"][0]["shots"]) == 1


def test_multi_beat_shots_are_chronological_after_score_selection():
    segment = _segment(
        beat_ids=["B001", "B002", "B003"],
        candidate_visuals=[
            _candidate(20.0, 23.0, score=0.9, beat_id="B001"),
            _candidate(10.0, 13.0, score=0.7, beat_id="B002"),
            _candidate(30.0, 33.0, score=0.6, beat_id="B003"),
        ],
    )

    result = assemble_sequence(_script([segment]), {"VO_001": 7.0})

    assert [shot["start"] for shot in result["segments"][0]["shots"]] == [10.0, 20.0, 30.0]


def test_sequence_keeps_segment_order_when_source_times_are_nonchronological():
    first = _segment(
        segment_id="VO_001",
        order=1,
        candidate_visuals=[_candidate(50.0, 53.0)],
    )
    second = _segment(
        segment_id="VO_002",
        order=2,
        candidate_visuals=[_candidate(10.0, 13.0)],
    )

    result = assemble_sequence(
        _script([first, second]), {"VO_001": 2.0, "VO_002": 2.0}
    )

    assert [segment["segment_id"] for segment in result["segments"]] == ["VO_001", "VO_002"]
    assert [segment["shots"][0]["start"] for segment in result["segments"]] == [50.0, 10.0]


# ============================================================
# Reuse avoidance / no-padding
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


def test_single_candidate_segment_stops_short_rather_than_pad_with_reuse():
    # Only one candidate available; target duration is far larger than
    # what that one candidate can usefully cover. The new "don't pad"
    # rule means selection stops after the first shot instead of
    # re-selecting the same reused range over and over to fill time.
    segment = _segment(candidate_visuals=[_candidate(0.0, 2.0, score=0.9)])
    result = assemble_sequence(_script([segment]), {"VO_001": 10.0})

    shots = result["segments"][0]["shots"]
    assert len(shots) == 1
    assert shots[0]["reused"] is False
    assert result["segments"][0]["shots_total_duration_seconds"] < 3.0
    assert result["segments"][0]["timeline_duration_seconds"] == pytest.approx(10.0)
    assert result["segments"][0]["visual_coverage_shortfall_seconds"] > 7.0


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
# Visual-function inference
# ============================================================

def test_infer_visual_function_matches_reason_keywords():
    assert infer_visual_function(_candidate(0, 1, reason="he reacts with shock")) == "reaction"
    assert infer_visual_function(_candidate(0, 1, reason="the reveal moment finally lands")) == "payoff"
    assert infer_visual_function(_candidate(0, 1, reason="a close-up of the object")) == "detail"
    assert infer_visual_function(_candidate(0, 1, reason="nothing special here")) == DEFAULT_VISUAL_FUNCTION


def test_infer_visual_function_prefers_explicit_field_over_reason_text():
    candidate = _candidate(0, 1, reason="he reacts with shock", visual_function="detail")
    assert infer_visual_function(candidate) == "detail"


# ============================================================
# Presentation-hint / visual-function-specific cadence
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


def test_reaction_beat_hint_uses_reaction_cadence_band():
    segment = _segment(
        presentation_hint="reaction_beat",
        candidate_visuals=[_candidate(0.0, 10.0, score=0.9)],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 5.0})

    for shot in result["segments"][0]["shots"]:
        assert shot["duration"] <= CADENCE_REACTION[1] + 0.01


def test_reaction_visual_function_inferred_from_reason_gets_reaction_cadence():
    # No reaction_beat presentation_hint at all -- the candidate's own
    # inferred visual function should still be enough to pick the tighter
    # reaction cadence band over the segment's default illustrative one.
    segment = _segment(
        presentation_hint="narration_over_source",
        candidate_visuals=[_candidate(0.0, 10.0, score=0.9, reason="the crowd gasps in shock")],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 5.0})

    shots = result["segments"][0]["shots"]
    assert shots[0]["visual_function"] == "reaction"
    assert shots[0]["duration"] <= CADENCE_REACTION[1] + 0.01
    assert all(shot["duration"] <= MAX_MOVING_COVERAGE_SHOT_SECONDS for shot in shots)


def test_detail_visual_function_uses_detail_cadence_regardless_of_importance():
    segment = _segment(
        importance=0.95,  # would otherwise push toward the payoff band
        candidate_visuals=[_candidate(0.0, 10.0, score=0.9, visual_function="detail")],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 5.0})

    shots = result["segments"][0]["shots"]
    assert shots[0]["visual_function"] == "detail"
    assert shots[0]["duration"] <= CADENCE_DETAIL[1] + 0.01
    assert all(shot["duration"] <= MAX_MOVING_COVERAGE_SHOT_SECONDS for shot in shots)


def test_important_segment_uses_wider_cadence_than_normal():
    high_importance = _segment(
        segment_id="VO_HIGH",
        importance=0.8,  # important but below the payoff threshold
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
    assert low_shot_duration == pytest.approx(CADENCE_ILLUSTRATIVE[1], abs=0.01)


def test_very_high_importance_uses_payoff_cadence():
    segment = _segment(
        segment_id="VO_PAYOFF",
        importance=0.95,
        candidate_visuals=[_candidate(0.0, 100.0, score=0.9)],
    )
    result = assemble_sequence(_script([segment]), {"VO_PAYOFF": 10.0})

    shot_duration = result["segments"][0]["shots"][0]["duration"]
    assert shot_duration == pytest.approx(CADENCE_PAYOFF[1], abs=0.01)


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
# Diversity scoring and progression reordering
# ============================================================

def test_diversity_bonus_can_outweigh_a_small_score_gap():
    # B picks up a diversity bonus (a visual function not yet used in
    # this segment) plus avoids the same-function-as-previous penalty
    # that a repeat of A would incur -- enough to beat A's higher raw
    # score once A has already been selected once.
    segment = _segment(
        candidate_visuals=[
            _candidate(0.0, 3.0, score=0.80, reason="plain shot one"),
            _candidate(30.0, 33.0, score=0.72, reason="the crowd reacts in shock"),
            _candidate(60.0, 63.0, score=0.79, reason="plain shot two"),
        ],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 4.0})

    shots = result["segments"][0]["shots"]
    assert len(shots) == 2
    visual_functions = [shot["visual_function"] for shot in shots]
    assert "reaction" in visual_functions


def test_progression_reordering_moves_context_before_reaction():
    segment = _segment(
        candidate_visuals=[
            _candidate(0.0, 3.0, score=0.95, reason="a shocked reaction"),
            _candidate(30.0, 33.0, score=0.5, reason="establishing wide shot of the location"),
        ],
    )
    result = assemble_sequence(_script([segment]), {"VO_001": 4.0})

    shots = result["segments"][0]["shots"]
    assert len(shots) == 2
    assert shots[0]["visual_function"] == "context"
    assert shots[1]["visual_function"] == "reaction"


def test_shots_carry_provenance_fields():
    segment = _segment(candidate_visuals=[_candidate(0.0, 5.0)])
    result = assemble_sequence(_script([segment]), {"VO_001": 2.0})

    shot = result["segments"][0]["shots"][0]
    assert "visual_function" in shot
    assert "selection_score" in shot


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
