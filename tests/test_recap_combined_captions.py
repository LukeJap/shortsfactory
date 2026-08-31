import json

from recap_media.combined_captions import build_combined_recap_caption_plan


def test_combined_recap_captions_map_narration_and_source_dialogue_to_final_time(tmp_path):
    cache = tmp_path / "source_words.json"
    cache.write_text(
        json.dumps(
            {"words": [
                {"text": "outside", "start": 99.0, "end": 99.2},
                {"text": "Hello", "start": 100.1, "end": 100.3},
                {"text": "Gary", "start": 100.3, "end": 100.5},
                {"text": "tail", "start": 101.1, "end": 101.3},
            ]}
        ),
        encoding="utf-8",
    )
    sequence = {
        "segments": [
            {"segment_id": "N_001", "timeline_start_seconds": 0.0},
            {
                "segment_id": "S_001", "block_type": "source_moment",
                "shots": [{
                    "source_audio_insert": True, "resolved_start": 100.0, "resolved_end": 101.0,
                    "timeline_start_seconds": 6.0, "transcript_cache_path": str(cache),
                }],
            },
        ]
    }
    narration = {"segments": [{"segment_id": "N_001", "words": [{"text": "Narrator", "start": 0.0, "end": 0.6}]}]}

    plan = build_combined_recap_caption_plan(sequence, narration, [])

    assert plan["time_basis"] == "recap_final_timeline"
    assert [cue["text"] for cue in plan["cues"]] == ["Narrator", "Hello", "Gary"]
    assert [cue["speaker_domain"] for cue in plan["cues"]] == ["narration", "source_dialogue", "source_dialogue"]
    assert all(0 <= cue["start"] < 10 for cue in plan["cues"])
