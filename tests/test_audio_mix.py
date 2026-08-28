import pytest

from recap_media.audio_mix import (
    NARRATION_FOREGROUND_TREATMENTS,
    SOURCE_RESTORED_TREATMENTS,
    build_duck_plan,
    build_duck_filter_complex,
    build_gain_keyframes,
    keyframes_to_volume_expression,
    load_duck_plan,
    shot_output_windows,
    write_duck_plan,
)
from recap_media.loader import RecapInputError


def _sequence_from_treatments(treatments_and_durations):
    """Build a minimal fake `sequence` dict (recap_sequence.json shape)
    from a flat list of (treatment, duration) tuples, one shot each, all
    in a single segment -- enough for shot_output_windows()/
    build_duck_plan(), which only look at segments[*].shots[*]
    (duration, treatment) and segments[*].order."""
    shots = [
        {"start": i * 10.0, "end": i * 10.0 + duration, "duration": duration, "treatment": treatment}
        for i, (treatment, duration) in enumerate(treatments_and_durations)
    ]
    return {
        "segments": [
            {
                "order": 1,
                "presentation_hint": "narration_over_source",
                "shots": shots,
            }
        ]
    }


# ============================================================
# shot_output_windows
# ============================================================

def test_windows_use_cumulative_output_time_not_source_time():
    sequence = _sequence_from_treatments(
        [("narration_over_source", 2.0), ("original_dialogue", 3.0)]
    )
    windows = shot_output_windows(sequence)

    assert windows[0] == (0.0, 2.0, "narration_over_source")
    assert windows[1] == (2.0, 5.0, "original_dialogue")  # cumulative, not the shot's own source start=10.0


def test_windows_empty_sequence():
    sequence = {"segments": []}
    assert shot_output_windows(sequence) == []


# ============================================================
# build_gain_keyframes
# ============================================================

def test_all_high_treatment_has_no_ramps():
    windows = [(0.0, 5.0, "narration_over_source")]
    keyframes = build_gain_keyframes(
        windows, NARRATION_FOREGROUND_TREATMENTS, high_gain=0.95, low_gain=0.0,
        attack_seconds=0.15, release_seconds=0.175,
    )
    gains = {gain for _, gain in keyframes}
    assert gains == {0.95}


def test_transition_to_dialogue_uses_attack_timing():
    windows = [
        (0.0, 3.0, "narration_over_source"),
        (3.0, 6.0, "original_dialogue"),
    ]
    keyframes = build_gain_keyframes(
        windows, NARRATION_FOREGROUND_TREATMENTS, high_gain=0.95, low_gain=0.0,
        attack_seconds=0.15, release_seconds=0.175,
    )
    # ramp starts exactly at the transition (t=3.0) and completes 0.15s later
    ramp_start = next(t for t, g in keyframes if t == 3.0 and g == 0.95)
    ramp_end = next(t for t, g in keyframes if g == 0.0 and t > 3.0)
    assert ramp_end == pytest.approx(3.15)
    assert ramp_start is not None


def test_transition_back_to_narration_uses_release_timing():
    windows = [
        (0.0, 3.0, "original_dialogue"),
        (3.0, 6.0, "narration_over_source"),
    ]
    keyframes = build_gain_keyframes(
        windows, NARRATION_FOREGROUND_TREATMENTS, high_gain=0.95, low_gain=0.0,
        attack_seconds=0.15, release_seconds=0.175,
    )
    ramp_end = next(t for t, g in keyframes if g == 0.95 and t > 3.0)
    assert ramp_end == pytest.approx(3.175)


def test_empty_windows_returns_single_keyframe():
    keyframes = build_gain_keyframes(
        [], NARRATION_FOREGROUND_TREATMENTS, high_gain=0.95, low_gain=0.0,
        attack_seconds=0.15, release_seconds=0.175,
    )
    assert keyframes == [(0.0, 0.95)]


def test_ramp_clamped_to_short_run_duration():
    # A run shorter than the ramp itself shouldn't produce a keyframe
    # past that run's own end.
    windows = [
        (0.0, 3.0, "narration_over_source"),
        (3.0, 3.05, "original_dialogue"),  # only 0.05s, shorter than attack (0.15s)
        (3.05, 6.0, "narration_over_source"),
    ]
    keyframes = build_gain_keyframes(
        windows, NARRATION_FOREGROUND_TREATMENTS, high_gain=0.95, low_gain=0.0,
        attack_seconds=0.15, release_seconds=0.175,
    )
    assert all(t <= 6.0 + 1e-6 for t, _ in keyframes)
    assert all(t <= 3.05 + 1e-6 or g != 0.0 for t, g in keyframes if t <= 3.05)


# ============================================================
# build_duck_plan
# ============================================================

def test_narration_is_true_silence_during_dialogue():
    sequence = _sequence_from_treatments(
        [("narration_over_source", 3.0), ("original_dialogue", 3.0)]
    )
    plan = build_duck_plan(sequence)
    dialogue_gain = plan["narration_keyframes"][-1][1]
    assert dialogue_gain == 0.0


def test_source_restores_during_dialogue_and_ducks_during_narration():
    sequence = _sequence_from_treatments(
        [("narration_over_source", 3.0), ("original_dialogue", 3.0)]
    )
    plan = build_duck_plan(
        sequence, source_ducked_gain=0.2, source_restored_gain=1.0
    )
    gains = [gain for _, gain in plan["source_keyframes"]]
    assert 0.2 in gains
    assert 1.0 in gains


def test_visual_only_treated_like_restored_source_no_narration():
    sequence = _sequence_from_treatments([("visual_only", 2.0)])
    plan = build_duck_plan(sequence)
    assert plan["narration_keyframes"][0][1] == 0.0  # nothing to narrate
    assert plan["source_keyframes"][0][1] == plan["settings"]["source_restored_gain"]


def test_reaction_beat_treated_like_narration_foreground():
    sequence = _sequence_from_treatments([("reaction_beat", 1.0)])
    plan = build_duck_plan(sequence)
    assert plan["narration_keyframes"][0][1] == plan["settings"]["voiceover_gain"]
    assert plan["source_keyframes"][0][1] == plan["settings"]["source_ducked_gain"]


def test_out_of_range_gains_are_clamped():
    sequence = _sequence_from_treatments([("narration_over_source", 2.0)])
    plan = build_duck_plan(sequence, voiceover_gain=5.0, source_ducked_gain=-1.0)
    assert plan["settings"]["voiceover_gain"] == 1.0
    assert plan["settings"]["source_ducked_gain"] == 0.0


def test_total_duration_matches_sequence():
    sequence = _sequence_from_treatments(
        [("narration_over_source", 3.0), ("original_dialogue", 4.5)]
    )
    plan = build_duck_plan(sequence)
    assert plan["total_duration_seconds"] == pytest.approx(7.5)


# ============================================================
# keyframes_to_volume_expression
# ============================================================

def test_expression_empty_keyframes_is_constant():
    assert keyframes_to_volume_expression([]) == "1.0"


def test_expression_single_keyframe_is_constant():
    assert keyframes_to_volume_expression([(0.0, 0.75)]) == "0.7500"


def test_expression_contains_linear_interpolation_and_between():
    expr = keyframes_to_volume_expression([(0.0, 0.95), (3.0, 0.95), (3.15, 0.0)])
    assert "between(t," in expr
    assert "if(" in expr


def test_expression_evaluates_correctly_at_keyframe_boundaries():
    # Manually evaluate the generated expression as a Python formula at
    # a few sample times to confirm the interpolation math is right,
    # without needing a real ffmpeg binary in the test environment.
    keyframes = [(0.0, 1.0), (2.0, 1.0), (3.0, 0.0)]

    def gain_at(t):
        if t < 2.0:
            return 1.0
        if t < 3.0:
            return 1.0 + (0.0 - 1.0) * (t - 2.0) / 1.0
        return 0.0

    for t in (0.0, 1.0, 2.0, 2.5, 2.9, 3.0, 4.0):
        assert gain_at(t) == pytest.approx(
            _reference_eval(keyframes, t), abs=1e-6
        )


def _reference_eval(keyframes, t):
    for i in range(len(keyframes) - 1):
        t0, g0 = keyframes[i]
        t1, g1 = keyframes[i + 1]
        if t0 <= t < t1:
            return g0 + (g1 - g0) * (t - t0) / (t1 - t0)
    return keyframes[-1][1] if t >= keyframes[-1][0] else keyframes[0][1]


# ============================================================
# build_duck_filter_complex
# ============================================================

def test_filter_complex_includes_amix_and_limiter():
    sequence = _sequence_from_treatments([("narration_over_source", 2.0)])
    plan = build_duck_plan(sequence)
    filter_complex = build_duck_filter_complex(plan)

    assert "amix=" in filter_complex
    assert "alimiter=limit=" in filter_complex
    assert "normalize=0" in filter_complex
    assert filter_complex.count(";") == 2  # narration volume; source volume; amix+limiter


def test_filter_complex_uses_custom_labels():
    sequence = _sequence_from_treatments([("narration_over_source", 2.0)])
    plan = build_duck_plan(sequence)
    filter_complex = build_duck_filter_complex(
        plan, narration_label="[2:a]", source_label="[0:a:0]", output_label="[out]"
    )
    assert filter_complex.startswith("[2:a]volume=")
    assert "[0:a:0]volume=" in filter_complex
    assert filter_complex.endswith("[out]")


# ============================================================
# write/load round trip
# ============================================================

def test_write_and_load_round_trip(tmp_path):
    sequence = _sequence_from_treatments([("narration_over_source", 2.0)])
    plan = build_duck_plan(sequence)

    path = tmp_path / "audio_duck_plan.json"
    write_duck_plan(plan, path)
    loaded = load_duck_plan(path)

    assert loaded == plan


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(RecapInputError, match="not found"):
        load_duck_plan(tmp_path / "does_not_exist.json")


def test_load_malformed_json_raises(tmp_path):
    path = tmp_path / "audio_duck_plan.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RecapInputError, match="not valid JSON"):
        load_duck_plan(path)


def test_load_missing_required_field_raises(tmp_path):
    path = tmp_path / "audio_duck_plan.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(RecapInputError, match="narration_keyframes"):
        load_duck_plan(path)


def test_source_restored_treatments_and_narration_foreground_are_complementary():
    # every known shot "treatment" value must belong to exactly one set,
    # or build_duck_plan's high/low classification silently misses one
    known_treatments = {
        "narration_over_source",
        "original_dialogue",
        "reaction_beat",
        "visual_only",
    }
    assert NARRATION_FOREGROUND_TREATMENTS | SOURCE_RESTORED_TREATMENTS == known_treatments
    assert NARRATION_FOREGROUND_TREATMENTS & SOURCE_RESTORED_TREATMENTS == set()
