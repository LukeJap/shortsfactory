"""
End-to-end check that B1 (loader) -> B2 (voiceover synthesis) -> B3
(sequence assembly) -> B4 (dialogue interweaving) actually compose
against the real fixture files, not just each other's own unit tests in
isolation.
"""

import wave
from io import BytesIO
from pathlib import Path

from recap_media.loader import load_recap_inputs
from recap_media.orpheus_provider import DEFAULT_VOICE
from recap_media.sequence import (
    assemble_sequence,
    interweave_original_dialogue,
    load_recap_sequence,
    write_recap_sequence,
)
from recap_media.voiceover import synthesize_segments

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "recap_media" / "valid"


def _make_wav_bytes(num_frames: int = 4000, framerate: int = 16000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


class FakeProvider:
    def synthesize_speech(self, text, voice=DEFAULT_VOICE, speed=1.0, timeout=60.0):
        return _make_wav_bytes()


def test_full_chain_against_real_fixtures(tmp_path):
    inputs = load_recap_inputs(
        FIXTURES_DIR / "episode_identity.json",
        FIXTURES_DIR / "verified_story_map.json",
        FIXTURES_DIR / "recap_script.json",
    )

    voiceover_results = synthesize_segments(
        FakeProvider(),
        inputs.recap_script["segments"],
        output_dir=tmp_path / "voiceover",
        manifest_path=tmp_path / "voiceover" / "manifest.json",
    )
    assert all(result.error is None for result in voiceover_results)

    narration_durations = {
        result.segment_id: result.duration_seconds for result in voiceover_results
    }

    sequence = assemble_sequence(inputs.recap_script, narration_durations)

    # Real fixture segments are short enough (1-2 shots each) that no
    # segment ends up eligible for an insert -- this still proves B4
    # composes cleanly against real B1/B3 output without crashing.
    sequence = interweave_original_dialogue(sequence, inputs.recap_script)

    sequence_path = tmp_path / "recap_sequence.json"
    write_recap_sequence(sequence, sequence_path)
    loaded_sequence = load_recap_sequence(sequence_path)

    assert loaded_sequence == sequence
    assert len(loaded_sequence["segments"]) == len(inputs.recap_script["segments"])
    for segment in loaded_sequence["segments"]:
        assert segment["shots"], f"{segment['segment_id']} got no shots"
        assert segment["narration_duration_source"] == "measured"
