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


def test_combined_captions_follow_the_selected_recap_speed():
    sequence = {"segments": [{"segment_id": "N_001", "timeline_start_seconds": 3.0}]}
    narration = {
        "segments": [
            {"segment_id": "N_001", "words": [{"text": "Narrator", "start": 0.0, "end": 0.6}]}
        ]
    }

    normal = build_combined_recap_caption_plan(sequence, narration, [], playback_speed=1.0)
    accelerated = build_combined_recap_caption_plan(sequence, narration, [], playback_speed=1.5)

    assert normal["playback_speed"] == 1.0
    assert accelerated["playback_speed"] == 1.5
    assert normal["cues"][0]["start"] == 3.0
    assert accelerated["cues"][0]["start"] == 2.0


def test_source_dialogue_uses_shot_speed_and_stays_inside_shot(tmp_path):
    cache = tmp_path / "source_words.json"
    cache.write_text(
        json.dumps(
            {
                "words": [
                    {"text": "leading", "start": 99.8, "end": 100.2},
                    {"text": "middle", "start": 101.0, "end": 101.4},
                    {"text": "trailing", "start": 103.8, "end": 104.2},
                    {"text": "later", "start": 105.0, "end": 105.2},
                ]
            }
        ),
        encoding="utf-8",
    )
    sequence = {
        "segments": [
            {
                "segment_id": "S_001",
                "block_type": "source_moment",
                "shots": [
                    {
                        "source_audio_insert": True,
                        "resolved_start": 100.0,
                        "resolved_end": 104.0,
                        "source_playback_speed": 2.0,
                        "timeline_start_seconds": 10.0,
                        "timeline_end_seconds": 12.0,
                        "timeline_duration_seconds": 2.0,
                        "transcript_cache_path": str(cache),
                    }
                ],
            }
        ]
    }

    plan = build_combined_recap_caption_plan(
        sequence,
        {"segments": []},
        [],
        playback_speed=1.0,
    )

    assert [cue["text"] for cue in plan["cues"]] == ["leading", "middle", "trailing"]
    assert [(cue["start"], cue["end"]) for cue in plan["cues"]] == [
        (10.0, 10.1),
        (10.5, 10.7),
        (11.9, 12.0),
    ]
    assert all(10.0 <= cue["start"] < cue["end"] <= 12.0 for cue in plan["cues"])


def test_source_dialogue_insert_inside_narration_block_is_mapped(tmp_path):
    cache = tmp_path / "source_words.json"
    cache.write_text(
        json.dumps({"words": [{"text": "Plan L", "start": 501.2, "end": 501.8}]}),
        encoding="utf-8",
    )
    sequence = {
        "segments": [
            {
                "segment_id": "N_003",
                "block_type": "narration",
                "shots": [
                    {
                        "source_audio_insert": True,
                        "resolved_start": 501.0,
                        "resolved_end": 502.0,
                        "timeline_start_seconds": 30.0,
                        "timeline_end_seconds": 31.0,
                        "transcript_cache_path": str(cache),
                    }
                ],
            }
        ]
    }

    plan = build_combined_recap_caption_plan(
        sequence,
        {"segments": []},
        [],
        playback_speed=1.0,
    )

    assert [(cue["text"], cue["start"], cue["end"]) for cue in plan["cues"]] == [
        ("Plan L", 30.2, 30.8)
    ]


def test_final_speed_is_applied_once_after_source_shot_mapping(tmp_path):
    cache = tmp_path / "source_words.json"
    cache.write_text(
        json.dumps({"words": [{"text": "Now", "start": 20.5, "end": 21.0}]}),
        encoding="utf-8",
    )
    shot = {
        "source_audio_insert": True,
        "resolved_start": 20.0,
        "resolved_end": 22.0,
        "source_playback_speed": 2.0,
        "timeline_start_seconds": 6.0,
        "timeline_end_seconds": 7.0,
        "transcript_cache_path": str(cache),
    }
    sequence = {
        "segments": [
            {"segment_id": "S_001", "block_type": "source_moment", "shots": [shot]}
        ]
    }

    plan = build_combined_recap_caption_plan(
        sequence,
        {"segments": []},
        [],
        playback_speed=1.5,
    )

    assert plan["cues"][0]["start"] == 4.167
    assert plan["cues"][0]["end"] == 4.333
