from standard_audio_pitch import build_standard_audio_preview_filter
from standard_video_speed import (
    build_standard_audio_tempo_filter,
    coerce_standard_video_speed,
    format_standard_video_speed,
)


def test_standard_video_speed_is_clamped_and_formatted():
    assert coerce_standard_video_speed("invalid") == 1.0
    assert coerce_standard_video_speed(0.25) == 0.5
    assert coerce_standard_video_speed(3.0) == 2.0
    assert format_standard_video_speed(1.5) == "1.50x"


def test_standard_speed_audio_transform_preserves_pitch_as_a_separate_control():
    assert build_standard_audio_tempo_filter(1.0) == ""
    assert build_standard_audio_tempo_filter(1.5) == "atempo=1.500000"
    assert build_standard_audio_preview_filter(0.0, 1.5) == "atempo=1.500000"
    assert build_standard_audio_preview_filter(1.8, 1.5) == (
        "rubberband=pitch=1.109569:tempo=1.000:formant=preserved:pitchq=quality,"
        "atempo=1.500000"
    )
