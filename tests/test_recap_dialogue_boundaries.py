import json

import pytest

from make_captions import ass_time
from recap_media.caption_alignment import build_narration_ass_dialogue_lines
from recap_media.dialogue_boundaries import resolve_source_audio_boundary
from recap_media.sequence import (
    assemble_sequence,
    interweave_original_dialogue,
    voiceover_timing_by_segment,
)


SOURCE_NAME = "accepted_episode.mkv"


def _write_cache(tmp_path, *, source=SOURCE_NAME, segments, words):
    cache_dir = tmp_path / "transcript_cache"
    cache_dir.mkdir()
    (cache_dir / "accepted.json").write_text(
        json.dumps(
            {
                "source_video_path": str(tmp_path / "input" / source),
                "segments": segments,
                "words": words,
            }
        ),
        encoding="utf-8",
    )
    return cache_dir


def _candidate(start, end):
    return {"start": start, "end": end, "score": 0.9, "reason": "Test dialogue."}


def _resolve(candidate, cache_dir):
    return resolve_source_audio_boundary(
        candidate,
        source_video=SOURCE_NAME,
        transcript_cache_dir=cache_dir,
    )


def test_end_inside_utterance_extends_to_utterance_end(tmp_path):
    cache_dir = _write_cache(
        tmp_path,
        segments=[{"start": 100.0, "end": 103.0, "text": "First."}, {"start": 103.5, "end": 108.0, "text": "Second sentence."}],
        words=[{"start": 103.5, "end": 105.0, "word": "Second"}, {"start": 105.0, "end": 108.0, "word": "sentence."}],
    )

    resolved = _resolve(_candidate(100.0, 105.25), cache_dir)

    assert resolved["resolved_start"] == 100.0
    assert resolved["resolved_end"] == 108.0
    assert resolved["boundary_source"] == "transcript_cache_word_timing"


def test_complete_end_is_preserved_and_does_not_absorb_next_utterance(tmp_path):
    cache_dir = _write_cache(
        tmp_path,
        segments=[{"start": 100.0, "end": 104.0, "text": "Complete."}, {"start": 104.1, "end": 108.0, "text": "Unrelated reply."}],
        words=[{"start": 100.0, "end": 104.0, "word": "Complete."}, {"start": 104.1, "end": 108.0, "word": "Reply."}],
    )

    resolved = _resolve(_candidate(100.0, 104.0), cache_dir)

    assert (resolved["resolved_start"], resolved["resolved_end"]) == (100.0, 104.0)
    assert "complete transcript boundary" in resolved["boundary_reason"].lower()


def test_valid_internal_phrase_start_is_preserved(tmp_path):
    cache_dir = _write_cache(
        tmp_path,
        segments=[{"start": 100.0, "end": 108.0, "text": "I do not know, but I do know."}],
        words=[{"start": 100.0, "end": 102.0, "word": "know,"}, {"start": 102.4, "end": 108.0, "word": "but"}],
    )

    resolved = _resolve(_candidate(102.1, 108.0), cache_dir)

    assert resolved["resolved_start"] == 102.1


def test_unaligned_short_utterance_start_moves_to_utterance_beginning(tmp_path):
    cache_dir = _write_cache(
        tmp_path,
        segments=[{"start": 100.0, "end": 102.0, "text": "Hello, Gary."}],
        words=[{"start": 100.0, "end": 100.4, "word": "Hello,"}, {"start": 101.2, "end": 102.0, "word": "Gary."}],
    )

    resolved = _resolve(_candidate(100.8, 102.0), cache_dir)

    assert resolved["resolved_start"] == 100.0
    assert "short timed utterance" in resolved["boundary_reason"].lower()


@pytest.mark.parametrize("source_video", [None, "other_episode.mkv"])
def test_missing_or_wrong_source_cache_preserves_candidate(tmp_path, source_video):
    cache_dir = _write_cache(
        tmp_path,
        segments=[{"start": 100.0, "end": 108.0, "text": "A line."}],
        words=[{"start": 100.0, "end": 108.0, "word": "line."}],
    )

    resolved = resolve_source_audio_boundary(
        _candidate(100.0, 104.0),
        source_video=source_video,
        transcript_cache_dir=cache_dir,
    )

    assert (resolved["resolved_start"], resolved["resolved_end"]) == (100.0, 104.0)
    assert resolved["boundary_source"] == "candidate"


@pytest.mark.parametrize("start,end", [(1376.68, 1389.04), (1416.8, 1420.58)])
def test_clean_real_style_candidates_remain_unchanged(tmp_path, start, end):
    cache_dir = _write_cache(
        tmp_path,
        segments=[{"start": start, "end": end, "text": "Complete line."}],
        words=[{"start": start, "end": end, "word": "Complete."}],
    )

    resolved = _resolve(_candidate(start, end), cache_dir)

    assert (resolved["resolved_start"], resolved["resolved_end"]) == (start, end)


def _script():
    return {
        "schema_version": 1,
        "target_duration_seconds": 10,
        "target_word_count": 20,
        "voice_style": "fast_story_recap",
        "segments": [
            {
                "segment_id": "VO_001",
                "order": 1,
                "text": "One two three four five six seven eight nine ten.",
                "beat_ids": ["B001"],
                "presentation_hint": "narration_over_source",
                "importance": 0.8,
                "candidate_visuals": [
                    {"start": 10.0, "end": 13.0, "score": 0.9, "reason": "Visual one."},
                    {"start": 20.0, "end": 23.0, "score": 0.9, "reason": "Visual two."},
                ],
                "original_dialogue_candidates": [_candidate(100.0, 104.0)],
            }
        ],
    }


def test_resolved_insert_duration_shifts_voiceover_and_caption_timing(tmp_path):
    cache_dir = _write_cache(
        tmp_path,
        segments=[{"start": 100.0, "end": 103.0, "text": "First."}, {"start": 103.5, "end": 108.0, "text": "Second sentence."}],
        words=[{"start": 103.5, "end": 105.0, "word": "Second"}, {"start": 105.0, "end": 108.0, "word": "sentence."}],
    )
    sequence = assemble_sequence(_script(), {"VO_001": 8.0})

    resolved = interweave_original_dialogue(
        sequence,
        _script(),
        source_video=SOURCE_NAME,
        transcript_cache_dir=cache_dir,
    )
    insert = next(shot for shot in resolved["segments"][0]["shots"] if shot.get("source_audio_insert"))
    timing = voiceover_timing_by_segment(resolved)["VO_001"]

    assert (insert["candidate_start"], insert["candidate_end"]) == (100.0, 104.0)
    assert (insert["resolved_start"], insert["resolved_end"]) == (100.0, 108.0)
    assert insert["duration"] == pytest.approx(8.0)
    assert timing["dialogue_pauses"][0]["duration_seconds"] == pytest.approx(8.0)

    pause_offset = timing["dialogue_pauses"][0]["narration_offset_seconds"]
    lines = build_narration_ass_dialogue_lines(
        {"words": [{"text": "After", "start": pause_offset + 0.1, "end": pause_offset + 0.6}]},
        dialogue_pauses=timing["dialogue_pauses"],
    )
    assert ass_time(pause_offset + 8.1) in lines[0]
