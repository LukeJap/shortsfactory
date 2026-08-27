from recap_intelligence.cache import ArtifactCache, cache_key, source_fingerprint


def test_cache_key_changes_with_source_and_prompt_version(tmp_path):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"episode-a")
    identity = {"canonical_id": "episode:1"}
    source_id = source_fingerprint(source)
    first = cache_key(
        identity=identity,
        source_identity=source_id,
        artifact="story_map",
        prompt_version="v1",
        model_version="model-a",
    )
    second = cache_key(
        identity=identity,
        source_identity=source_id,
        artifact="story_map",
        prompt_version="v2",
        model_version="model-a",
    )
    assert first != second


def test_artifact_cache_round_trips_json(tmp_path):
    cache = ArtifactCache(tmp_path / "cache")
    cache.put("abc", {"value": 1})
    assert cache.get("abc") == {"value": 1}
