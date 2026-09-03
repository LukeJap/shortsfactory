import auto_cut
import apply_smart_edit
import semantic_edit
from visual_emphasis import (
    auto_cut_aggression_from_energy,
    auto_cut_aggression_settings,
    auto_cut_profile,
    coerce_auto_cut_aggression,
    energy_profile,
)


def _spaced_words():
    return [
        {"word": "one", "start": 0.0, "end": 0.2},
        {"word": "two", "start": 1.0, "end": 1.2},
        {"word": "three", "start": 2.0, "end": 2.2},
    ]


def test_auto_cut_aggression_preserves_legacy_profile_anchors():
    assert auto_cut_aggression_from_energy("LOW") == 25
    assert auto_cut_aggression_from_energy("PUNCHY") == 50
    assert auto_cut_aggression_from_energy("MAXIMUM") == 75
    assert auto_cut_profile(25) == energy_profile("LOW")
    assert auto_cut_profile(50) == energy_profile("PUNCHY")
    assert auto_cut_profile(75) == energy_profile("MAXIMUM")


def test_auto_cut_aggression_zero_preserves_timing_and_100_is_stronger_than_maximum():
    words = _spaced_words()
    maximum = auto_cut_aggression_settings(75)
    maximum_plus = auto_cut_aggression_settings(100)

    assert auto_cut_profile(0)["auto_cut_max_removal_ratio"] == 0.0
    assert auto_cut.detect_pause_cuts(
        words,
        min_gap_to_edit=maximum["auto_cut_min_gap"],
        keep_gap_seconds=maximum["auto_cut_keep_gap"],
    ) == []
    assert len(
        auto_cut.detect_pause_cuts(
            words,
            min_gap_to_edit=maximum_plus["auto_cut_min_gap"],
            keep_gap_seconds=maximum_plus["auto_cut_keep_gap"],
        )
    ) == 2
    assert maximum_plus["auto_cut_max_removal_ratio"] > maximum["auto_cut_max_removal_ratio"]
    assert maximum_plus["auto_cut_min_spacing"] < maximum["auto_cut_min_spacing"]


def test_auto_cut_aggression_zero_budget_rejects_automatic_cuts():
    cuts, _semantic, _warning = apply_smart_edit.apply_automatic_cut_safety(
        [{"start": 1.0, "end": 1.5, "source": "pause"}],
        [],
        10.0,
        profile=auto_cut_profile(0),
    )

    assert cuts == []


def test_auto_cut_aggression_100_relaxes_existing_semantic_validation_gates():
    words = [{"word": "um", "start": 0.0, "end": 0.25}]
    proposal = {
        "cuts": [
            {
                "start_word_index": 0,
                "end_word_index": 0,
                "reason": "weak connective material",
                "confidence": 0.80,
            }
        ]
    }

    assert semantic_edit.validate_cuts(proposal, words, auto_cut_profile(75)) == []
    assert len(semantic_edit.validate_cuts(proposal, words, auto_cut_profile(100))) == 1


def test_auto_cut_aggression_coerces_legacy_and_invalid_saved_values():
    assert coerce_auto_cut_aggression("LOW") == 25
    assert coerce_auto_cut_aggression("PUNCHY") == 50
    assert coerce_auto_cut_aggression("MAXIMUM") == 75
    assert coerce_auto_cut_aggression(-1) == 0
    assert coerce_auto_cut_aggression(101) == 100
    assert coerce_auto_cut_aggression("invalid") == 50
