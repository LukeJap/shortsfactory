import copy
import json
import wave
from io import BytesIO
from pathlib import Path

import pytest

from recap_intelligence.models import RecapValidationError, validate_recap_script
from recap_media.audio_mix import build_duck_plan, shot_output_windows
from recap_media.caption_alignment import build_narration_captions
from recap_media.loader import RecapInputError, load_recap_script
from recap_media.orpheus_provider import DEFAULT_VOICE
from recap_media.sequence import (
    assemble_sequence,
    interweave_original_dialogue,
    voiceover_timing_by_segment,
)
from recap_media.voiceover import synthesize_segments


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "recap_media" / "valid"
SOURCE_NAME = "explicit_blocks_episode.mkv"


def _v2_script() -> dict:
    return json.loads((FIXTURES_DIR / "recap_script_v2_blocks.json").read_text(encoding="utf-8"))


def _write_transcript_cache(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "transcript_cache"
    cache_dir.mkdir()
    (cache_dir / "episode.json").write_text(
        json.dumps(
            {
                "source_video_path": str(tmp_path / "input" / SOURCE_NAME),
                "segments": [
                    {"start": 100.0, "end": 105.0, "text": "First source moment."},
                    {"start": 200.0, "end": 206.0, "text": "Second source moment."},
                ],
                "words": [],
            }
        ),
        encoding="utf-8",
    )
    return cache_dir


def _wav_bytes() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize_speech(self, text, voice=DEFAULT_VOICE, speed=1.0, timeout=60.0):
        self.calls.append(text)
        return _wav_bytes()


def test_v1_loader_keeps_existing_script_and_normalizes_narration_semantics():
    loaded = load_recap_script(FIXTURES_DIR / "recap_script.json")

    assert loaded["schema_version"] == 1
    assert {segment["block_type"] for segment in loaded["segments"]} == {"narration"}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda script: script["segments"][0].update(text=""), "narration text"),
        (lambda script: script["segments"][1].update(block_type="unknown"), "block_type"),
        (lambda script: script["segments"][1].update(original_dialogue_candidates=[]), "source range"),
    ],
)
def test_v2_validation_rejects_invalid_block_contract(mutate, message):
    script = _v2_script()
    mutate(script)

    with pytest.raises(RecapValidationError, match=message):
        validate_recap_script(script)


def test_v2_source_block_allows_empty_text_and_loader_preserves_block_types(tmp_path):
    script = _v2_script()
    validate_recap_script(script)
    path = tmp_path / "recap_script.json"
    path.write_text(json.dumps(script), encoding="utf-8")

    loaded = load_recap_script(path)

    assert [segment["block_type"] for segment in loaded["segments"]] == [
        "narration",
        "source_moment",
        "narration",
        "source_moment",
        "narration",
    ]
    assert loaded["segments"][1]["text"] == ""


def test_v2_loader_rejects_source_block_without_dialogue_candidate(tmp_path):
    script = _v2_script()
    script["segments"][1]["original_dialogue_candidates"] = []
    path = tmp_path / "recap_script.json"
    path.write_text(json.dumps(script), encoding="utf-8")

    with pytest.raises(RecapInputError, match="source_moment needs"):
        load_recap_script(path)


def test_v2_explicit_blocks_keep_tts_captions_and_timeline_separate(tmp_path):
    script = _v2_script()
    provider = _FakeProvider()
    results = synthesize_segments(
        provider,
        script["segments"],
        output_dir=tmp_path / "voiceover",
        manifest_path=tmp_path / "voiceover" / "manifest.json",
    )

    assert [result.segment_id for result in results] == ["N_001", "N_002", "N_003"]
    assert len(provider.calls) == 3

    captions = build_narration_captions(
        script["segments"],
        recognized_words_by_segment={segment_id: [] for segment_id in ("N_001", "N_002", "N_003")},
    )
    assert [segment["segment_id"] for segment in captions["segments"]] == [
        "N_001",
        "N_002",
        "N_003",
    ]

    sequence = assemble_sequence(
        script,
        {"N_001": 3.0, "N_002": 4.0, "N_003": 2.0},
        source_video=SOURCE_NAME,
        transcript_cache_dir=_write_transcript_cache(tmp_path),
    )
    source_blocks = [
        segment for segment in sequence["segments"] if segment["block_type"] == "source_moment"
    ]

    assert [segment["segment_id"] for segment in sequence["segments"]] == [
        "N_001",
        "S_001",
        "N_002",
        "S_002",
        "N_003",
    ]
    assert sequence["total_duration_seconds"] == pytest.approx(20.0)
    assert sequence["source_audio_insert_count"] == 2
    assert all(segment["narration_duration_seconds"] == 0.0 for segment in source_blocks)
    assert [(block["shots"][0]["start"], block["shots"][0]["end"]) for block in source_blocks] == [
        (100.0, 105.0),
        (200.0, 206.0),
    ]
    assert all(
        block["shots"][0]["treatment"] == "original_dialogue"
        and block["shots"][0]["source_audio_insert"]
        and "narration_pause_offset_seconds" not in block["shots"][0]
        for block in source_blocks
    )
    assert all(block["shots"][0]["boundary_source"] == "transcript_cache_segment_timing" for block in source_blocks)

    interwoven = interweave_original_dialogue(sequence, script)
    assert interwoven == sequence
    voiceover_timing = voiceover_timing_by_segment(sequence)
    assert set(voiceover_timing) == {"N_001", "N_002", "N_003"}
    assert all(not timing["dialogue_pauses"] for timing in voiceover_timing.values())

    windows = shot_output_windows(sequence)
    treatments = [window[2] for window in windows]
    treatment_transitions = [
        treatment
        for index, treatment in enumerate(treatments)
        if index == 0 or treatment != treatments[index - 1]
    ]
    assert treatment_transitions == [
        "narration_over_source",
        "original_dialogue",
        "narration_over_source",
        "original_dialogue",
        "narration_over_source",
    ]
    assert [
        (start, end)
        for start, end, treatment in windows
        if treatment == "original_dialogue"
    ] == [(3.0, 8.0), (12.0, 18.0)]
    assert build_duck_plan(sequence)["total_duration_seconds"] == pytest.approx(20.0)


def test_v2_source_moment_is_not_reinterpreted_as_a_legacy_pause(tmp_path):
    script = _v2_script()
    sequence = assemble_sequence(
        script,
        {"N_001": 3.0, "N_002": 4.0, "N_003": 2.0},
        source_video=SOURCE_NAME,
        transcript_cache_dir=_write_transcript_cache(tmp_path),
    )
    before = copy.deepcopy(sequence)

    assert interweave_original_dialogue(sequence, script) == before
