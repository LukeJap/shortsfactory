import copy
import json
from pathlib import Path

import pytest

from recap_media.loader import (
    RecapInputError,
    load_episode_identity,
    load_recap_inputs,
    load_recap_script,
    load_verified_story_map,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "recap_media" / "valid"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _valid_episode_identity() -> dict:
    return copy.deepcopy(_load_fixture("episode_identity.json"))


def _valid_story_map() -> dict:
    return copy.deepcopy(_load_fixture("verified_story_map.json"))


def _valid_recap_script() -> dict:
    return copy.deepcopy(_load_fixture("recap_script.json"))


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ============================================================
# Valid fixtures load cleanly
# ============================================================

def test_load_episode_identity_valid_fixture():
    data = load_episode_identity(FIXTURES_DIR / "episode_identity.json")
    assert data["title"] == "Example Show"
    assert data["season"] == 2
    assert data["episode"] == 9


def test_load_verified_story_map_valid_fixture():
    data = load_verified_story_map(FIXTURES_DIR / "verified_story_map.json")
    assert [beat["beat_id"] for beat in data["beats"]] == ["B001", "B002"]


def test_load_recap_script_valid_fixture():
    data = load_recap_script(FIXTURES_DIR / "recap_script.json")
    assert [segment["segment_id"] for segment in data["segments"]] == ["VO_001", "VO_002"]


def test_load_recap_inputs_combines_all_three():
    inputs = load_recap_inputs(
        FIXTURES_DIR / "episode_identity.json",
        FIXTURES_DIR / "verified_story_map.json",
        FIXTURES_DIR / "recap_script.json",
    )
    assert inputs.episode_identity["title"] == "Example Show"
    assert len(inputs.verified_story_map["beats"]) == 2
    assert len(inputs.recap_script["segments"]) == 2


# ============================================================
# Missing / malformed files
# ============================================================

def test_missing_file_raises(tmp_path):
    with pytest.raises(RecapInputError, match="not found"):
        load_recap_script(tmp_path / "does_not_exist.json")


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "recap_script.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RecapInputError, match="not valid JSON"):
        load_recap_script(path)


def test_non_object_top_level_raises(tmp_path):
    path = _write(tmp_path / "recap_script.json", [1, 2, 3])
    with pytest.raises(RecapInputError, match="JSON object"):
        load_recap_script(path)


def test_wrong_schema_version_raises(tmp_path):
    data = _valid_recap_script()
    data["schema_version"] = 2
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="schema_version"):
        load_recap_script(path)


# ============================================================
# episode_identity.json validation
# ============================================================

def test_episode_identity_invalid_media_type_raises(tmp_path):
    data = _valid_episode_identity()
    data["media_type"] = "podcast"
    path = _write(tmp_path / "episode_identity.json", data)
    with pytest.raises(RecapInputError, match="media_type"):
        load_episode_identity(path)


def test_episode_identity_tv_episode_missing_season_raises(tmp_path):
    data = _valid_episode_identity()
    del data["season"]
    path = _write(tmp_path / "episode_identity.json", data)
    with pytest.raises(RecapInputError, match="season"):
        load_episode_identity(path)


def test_episode_identity_movie_does_not_require_season(tmp_path):
    data = _valid_episode_identity()
    data["media_type"] = "movie"
    del data["season"]
    del data["episode"]
    path = _write(tmp_path / "episode_identity.json", data)
    result = load_episode_identity(path)
    assert result["media_type"] == "movie"


def test_episode_identity_confidence_out_of_range_raises(tmp_path):
    data = _valid_episode_identity()
    data["confidence"] = 1.5
    path = _write(tmp_path / "episode_identity.json", data)
    with pytest.raises(RecapInputError, match="confidence"):
        load_episode_identity(path)


# ============================================================
# verified_story_map.json validation
# ============================================================

def test_story_map_duplicate_beat_id_raises(tmp_path):
    data = _valid_story_map()
    data["beats"][1]["beat_id"] = data["beats"][0]["beat_id"]
    path = _write(tmp_path / "verified_story_map.json", data)
    with pytest.raises(RecapInputError, match="duplicate beat_id"):
        load_verified_story_map(path)


def test_story_map_bad_time_range_raises(tmp_path):
    data = _valid_story_map()
    data["beats"][0]["source_evidence"][0]["start"] = 20.0
    data["beats"][0]["source_evidence"][0]["end"] = 10.0
    path = _write(tmp_path / "verified_story_map.json", data)
    with pytest.raises(RecapInputError, match="before"):
        load_verified_story_map(path)


def test_story_map_empty_beats_raises(tmp_path):
    data = _valid_story_map()
    data["beats"] = []
    path = _write(tmp_path / "verified_story_map.json", data)
    with pytest.raises(RecapInputError, match="beats"):
        load_verified_story_map(path)


def test_story_map_allows_empty_source_evidence(tmp_path):
    data = _valid_story_map()
    data["beats"][0]["source_evidence"] = []
    path = _write(tmp_path / "verified_story_map.json", data)
    result = load_verified_story_map(path)
    assert result["beats"][0]["source_evidence"] == []


# ============================================================
# recap_script.json validation
# ============================================================

def test_recap_script_invalid_presentation_hint_raises(tmp_path):
    data = _valid_recap_script()
    data["segments"][0]["presentation_hint"] = "dramatic_zoom"
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="presentation_hint"):
        load_recap_script(path)


def test_recap_script_duplicate_segment_id_raises(tmp_path):
    data = _valid_recap_script()
    data["segments"][1]["segment_id"] = data["segments"][0]["segment_id"]
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="duplicate segment_id"):
        load_recap_script(path)


def test_recap_script_duplicate_order_raises(tmp_path):
    data = _valid_recap_script()
    data["segments"][1]["order"] = data["segments"][0]["order"]
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="duplicate segment order"):
        load_recap_script(path)


def test_recap_script_segment_with_no_source_material_raises(tmp_path):
    data = _valid_recap_script()
    data["segments"][1]["candidate_visuals"] = []
    data["segments"][1]["original_dialogue_candidates"] = []
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="nothing to show"):
        load_recap_script(path)


def test_recap_script_bad_candidate_time_range_raises(tmp_path):
    data = _valid_recap_script()
    data["segments"][0]["candidate_visuals"][0]["start"] = 20.0
    data["segments"][0]["candidate_visuals"][0]["end"] = 10.0
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="before"):
        load_recap_script(path)


def test_recap_script_candidate_score_out_of_range_raises(tmp_path):
    data = _valid_recap_script()
    data["segments"][0]["candidate_visuals"][0]["score"] = 1.2
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="score"):
        load_recap_script(path)


def test_recap_script_empty_beat_ids_raises(tmp_path):
    data = _valid_recap_script()
    data["segments"][0]["beat_ids"] = []
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="beat_ids"):
        load_recap_script(path)


def test_recap_script_visual_only_allows_empty_text(tmp_path):
    data = _valid_recap_script()
    data["segments"][0]["presentation_hint"] = "visual_only"
    data["segments"][0]["text"] = ""
    path = _write(tmp_path / "recap_script.json", data)
    result = load_recap_script(path)
    assert result["segments"][0]["text"] == ""


def test_recap_script_non_visual_only_requires_text(tmp_path):
    data = _valid_recap_script()
    data["segments"][0]["text"] = ""
    path = _write(tmp_path / "recap_script.json", data)
    with pytest.raises(RecapInputError, match="text"):
        load_recap_script(path)


# ============================================================
# Cross-file validation (shared contract rule 4)
# ============================================================

def test_load_recap_inputs_dangling_beat_id_raises(tmp_path):
    story_map = _valid_story_map()
    del story_map["beats"][1]  # drop B002, which VO_002 references

    episode_path = _write(tmp_path / "episode_identity.json", _valid_episode_identity())
    story_map_path = _write(tmp_path / "verified_story_map.json", story_map)
    script_path = _write(tmp_path / "recap_script.json", _valid_recap_script())

    with pytest.raises(RecapInputError, match="not found in verified_story_map"):
        load_recap_inputs(episode_path, story_map_path, script_path)
