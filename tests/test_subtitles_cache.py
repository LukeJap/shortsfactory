from subtitles import (
    CACHE_DIR,
    cache_is_valid,
    cache_path_for_video,
    migrate_cached_transcript,
    normalize_quality,
    normalized_segments,
    source_fingerprint,
)


def test_normalize_quality_passes_through_known_values():
    assert normalize_quality("auto") == "AUTO"
    assert normalize_quality("FAST") == "FAST"
    assert normalize_quality("Accurate") == "ACCURATE"


def test_normalize_quality_falls_back_to_auto():
    assert normalize_quality(None) == "AUTO"
    assert normalize_quality("") == "AUTO"
    assert normalize_quality("bogus") == "AUTO"


def test_cache_is_valid_requires_matching_identity_and_list_fields():
    identity = {"a": 1, "b": 2}

    assert cache_is_valid(
        {"cache_identity": identity, "segments": [], "words": []},
        identity,
    ) is True

    assert cache_is_valid(
        {"cache_identity": {"a": 1}, "segments": [], "words": []},
        identity,
    ) is False

    assert cache_is_valid(
        {"cache_identity": identity, "segments": "not-a-list", "words": []},
        identity,
    ) is False

    assert cache_is_valid(
        {"segments": [], "words": []},
        identity,
    ) is False


def test_migrate_cached_transcript_upgrades_fields_without_mutating_input():
    original = {
        "engine": "old",
        "quality": "old",
        "model": "old",
        "segments": [1],
        "words": [2],
    }
    new_identity = {"new": True}

    migrated = migrate_cached_transcript(
        original,
        new_identity,
        "accurate",
        "medium",
    )

    assert migrated["engine"] == "openai-whisper"
    assert migrated["quality"] == "ACCURATE"
    assert migrated["model"] == "medium"
    assert migrated["cache_identity"] == new_identity
    # Segments/words carried over unchanged.
    assert migrated["segments"] == [1]
    assert migrated["words"] == [2]

    # Original dict must not be mutated -- migrate_cached_transcript should
    # shallow-copy before stamping in the new fields.
    assert original == {
        "engine": "old",
        "quality": "old",
        "model": "old",
        "segments": [1],
        "words": [2],
    }


def test_normalized_segments_drops_malformed_segments():
    raw_segments = [
        {"start": 1.0, "end": 2.0, "text": "  hello  ", "words": []},
        {"start": 5.0, "end": 3.0, "text": "bad range", "words": []},
        {"start": "garbage", "end": 2.0, "text": "bad float", "words": []},
    ]

    segments, words = normalized_segments(raw_segments)

    assert len(segments) == 1
    assert segments[0]["text"] == "hello"
    assert words == []


def test_normalized_segments_filters_blank_words_and_defaults_probability():
    raw_segments = [
        {
            "start": 1.0,
            "end": 2.0,
            "text": "hello world",
            "words": [
                {"word": " hello ", "start": 1.0, "end": 1.4, "probability": 0.9},
                {"word": "   ", "start": 1.4, "end": 1.5},
                {"word": "world", "start": 1.5, "end": 2.0},
            ],
        },
    ]

    segments, words = normalized_segments(raw_segments)

    assert [w["word"] for w in words] == ["hello", "world"]
    assert words[0]["probability"] == 0.9
    # Missing probability defaults to 0.0 rather than being omitted/None.
    assert words[1]["probability"] == 0.0
    assert segments[0]["words"] == words


def test_source_fingerprint_is_deterministic_and_quality_sensitive(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video data")

    digest_a, identity_a = source_fingerprint(video, "AUTO", "small")
    digest_b, identity_b = source_fingerprint(video, "AUTO", "small")
    digest_c, identity_c = source_fingerprint(video, "FAST", "base")

    assert digest_a == digest_b
    assert identity_a == identity_b
    assert digest_a != digest_c


def test_cache_path_for_video_lands_in_cache_dir(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video data")

    cache_path, identity = cache_path_for_video(video, "AUTO", "small")

    assert cache_path.parent == CACHE_DIR
    assert cache_path.suffix == ".json"
    assert identity["quality"] == "AUTO"
    assert identity["model"] == "small"
