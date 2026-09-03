from standard_audio_pitch import (
    build_standard_audio_pitch_filter,
    coerce_standard_audio_pitch,
    format_standard_audio_pitch,
    standard_audio_pitch_ratio,
)


def test_standard_audio_pitch_is_symmetric_and_defaults_to_neutral():
    assert coerce_standard_audio_pitch("invalid") == 0.0
    assert coerce_standard_audio_pitch(-9.0) == -4.0
    assert coerce_standard_audio_pitch(9.0) == 4.0
    assert format_standard_audio_pitch(0.0) == "0.0 st"
    assert format_standard_audio_pitch(1.8) == "+1.8 st"


def test_standard_audio_pitch_filter_preserves_duration():
    assert build_standard_audio_pitch_filter(0.0) == ""
    assert standard_audio_pitch_ratio(1.8) == 1.109569472067845
    assert build_standard_audio_pitch_filter(1.8) == (
        "rubberband=pitch=1.109569:tempo=1.000:formant=preserved:pitchq=quality"
    )
