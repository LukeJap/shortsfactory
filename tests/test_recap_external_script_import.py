import copy
import json
from pathlib import Path

import pytest

from recap_media.loader import (
    RecapInputError,
    load_episode_identity,
    load_external_recap_script,
    load_recap_script,
    load_verified_story_map,
)
from recap_media.sequence import assemble_sequence


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "recap_media" / "valid"
SOURCE_NAME = "external_import_episode.mkv"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _identity() -> dict:
    return load_episode_identity(FIXTURES_DIR / "episode_identity.json")


def _story_map() -> dict:
    return load_verified_story_map(FIXTURES_DIR / "verified_story_map.json")


def _external_script() -> dict:
    return _fixture("external_recap_script_v2.json")


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _import(tmp_path: Path, data: dict | None = None, story_map: dict | None = None) -> dict:
    return load_external_recap_script(
        _write(tmp_path / "external_recap_script.json", data or _external_script()),
        episode_identity=_identity(),
        verified_story_map=story_map or _story_map(),
    )


def test_valid_external_v2_import_preserves_prose_and_safe_provenance(tmp_path):
    data = _external_script()
    imported = _import(tmp_path, data)

    assert imported["schema_version"] == 2
    assert imported["script_source"] == "external"
    assert imported["authoring_source"] == "external_chatgpt"
    assert imported["imported_from"] == "external_recap_script.json"
    assert imported["segments"][0]["text"] == data["segments"][0]["text"]
    assert imported["segments"][2]["text"] == data["segments"][2]["text"]
    assert imported["segments"][2]["candidate_visuals"][0]["start"] == 120.5


def test_external_import_uses_no_writer_or_ollama_and_legacy_loader_is_unchanged(tmp_path):
    imported = _import(tmp_path)
    legacy = load_recap_script(FIXTURES_DIR / "recap_script.json")

    assert imported["script_source"] == "external"
    assert legacy["schema_version"] == 1
    assert {segment["block_type"] for segment in legacy["segments"]} == {"narration"}


def test_external_import_requires_schema_v2(tmp_path):
    data = _external_script()
    data["schema_version"] = 1

    with pytest.raises(RecapInputError, match="requires schema v2"):
        _import(tmp_path, data)


def test_external_import_rejects_unknown_beat(tmp_path):
    data = _external_script()
    data["segments"][1]["beat_ids"] = ["B999"]

    with pytest.raises(RecapInputError, match="Unknown beat B999 in block S_001"):
        _import(tmp_path, data)


def test_external_import_rejects_unsupported_source_range(tmp_path):
    data = _external_script()
    data["segments"][1]["original_dialogue_candidates"][0].update(start=900.0, end=904.0)

    with pytest.raises(RecapInputError, match="Source moment S_001 range is not supported"):
        _import(tmp_path, data)


def test_external_import_accepts_evidence_contained_source_range(tmp_path):
    data = _external_script()
    data["segments"][1]["original_dialogue_candidates"][0].update(start=12.2, end=18.1)

    imported = _import(tmp_path, data)

    assert imported["segments"][1]["original_dialogue_candidates"][0]["start"] == 12.2


def test_external_import_rejects_source_override(tmp_path):
    data = _external_script()
    data["source_video_path"] = r"C:\\other-show.mkv"

    with pytest.raises(RecapInputError, match="cannot override accepted source identity"):
        _import(tmp_path, data)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["segments"][1].update(segment_id="N_001"), "duplicate segment_id"),
        (lambda data: data["segments"][1].update(order=1), "duplicate segment order"),
        (lambda data: data["segments"][2].update(order=0), "strictly increasing"),
        (lambda data: data["segments"][0].update(text=""), "text"),
        (lambda data: data["segments"][1].update(text="This must stay empty."), "empty text"),
    ],
)
def test_external_import_rejects_invalid_block_contract(tmp_path, mutate, message):
    data = _external_script()
    mutate(data)

    with pytest.raises(RecapInputError, match=message):
        _import(tmp_path, data)


def test_external_import_rejects_backwards_story_chronology(tmp_path):
    data = _external_script()
    story_map = _story_map()
    story_map["beats"].append(
        {
            "beat_id": "B003",
            "order": 3,
            "summary": "The conflict reaches a new turning point.",
            "importance": 0.8,
            "source_evidence": [
                {"start": 180.0, "end": 184.0, "type": "dialogue", "confidence": 0.9}
            ],
        }
    )
    data["segments"][2]["beat_ids"] = ["B003"]
    data["segments"][3]["beat_ids"] = ["B003"]
    data["segments"][3]["candidate_visuals"] = [
        {"start": 180.0, "end": 184.0, "score": 0.85, "reason": "Accepted turning point footage."}
    ]
    data["segments"][3]["original_dialogue_candidates"] = [
        {"start": 180.0, "end": 184.0, "score": 0.88, "reason": "Accepted turning point dialogue."}
    ]
    data["segments"].append(
        {
            "segment_id": "N_003",
            "order": 5,
            "block_type": "narration",
            "text": "The recap abruptly returns to the opening event.",
            "beat_ids": ["B002"],
            "presentation_hint": "narration_over_source",
            "importance": 0.8,
            "candidate_visuals": [
                {"start": 120.5, "end": 128.2, "score": 0.9, "reason": "Earlier confrontation footage."}
            ],
            "original_dialogue_candidates": [],
        }
    )

    with pytest.raises(RecapInputError, match="moves backwards"):
        _import(tmp_path, data, story_map)


def test_external_import_allows_one_opening_hook_to_setup_backfill(tmp_path):
    data = _external_script()
    data["segments"][0]["beat_ids"] = ["B002"]
    data["segments"][1]["beat_ids"] = ["B002"]
    data["segments"][1]["candidate_visuals"] = [
        {"start": 120.5, "end": 128.2, "score": 0.85, "reason": "Accepted hook footage."}
    ]
    data["segments"][1]["original_dialogue_candidates"] = [
        {"start": 120.5, "end": 128.2, "score": 0.88, "reason": "Accepted hook dialogue."}
    ]
    data["segments"][2]["beat_ids"] = ["B001"]
    data["segments"][3]["beat_ids"] = ["B001"]
    data["segments"][3]["candidate_visuals"] = [
        {"start": 12.0, "end": 18.4, "score": 0.85, "reason": "Accepted setup footage."}
    ]
    data["segments"][3]["original_dialogue_candidates"] = [
        {"start": 12.0, "end": 18.4, "score": 0.88, "reason": "Accepted setup dialogue."}
    ]

    imported = _import(tmp_path, data)

    assert [segment["beat_ids"] for segment in imported["segments"]] == [
        ["B002"], ["B002"], ["B001"], ["B001"]
    ]


def test_imported_source_moment_still_uses_phase_1b_boundary_resolution(tmp_path):
    data = _external_script()
    story_map = _story_map()
    story_map["beats"][0]["source_evidence"] = [
        {"start": 100.0, "end": 104.0, "type": "dialogue", "confidence": 0.9}
    ]
    data["segments"][1]["original_dialogue_candidates"] = [
        {"start": 100.0, "end": 103.0, "score": 0.9, "reason": "Accepted dialogue moment."}
    ]
    imported = _import(tmp_path, data, story_map)
    cache_dir = tmp_path / "transcript_cache"
    cache_dir.mkdir()
    (cache_dir / "episode.json").write_text(
        json.dumps(
            {
                "source_video_path": str(tmp_path / "input" / SOURCE_NAME),
                "segments": [{"start": 100.0, "end": 104.0, "text": "A complete line."}],
                "words": [],
            }
        ),
        encoding="utf-8",
    )

    sequence = assemble_sequence(
        imported,
        {"N_001": 2.0, "N_002": 2.0},
        verified_story_map=story_map,
        source_video=SOURCE_NAME,
        transcript_cache_dir=cache_dir,
    )
    source_shot = next(segment for segment in sequence["segments"] if segment["segment_id"] == "S_001")["shots"][0]

    assert (source_shot["candidate_start"], source_shot["candidate_end"]) == (100.0, 103.0)
    assert (source_shot["start"], source_shot["end"]) == (100.0, 104.0)
    assert source_shot["boundary_source"] == "transcript_cache_segment_timing"


def test_external_provenance_never_uses_imported_absolute_path(tmp_path):
    data = _external_script()
    data["imported_from"] = r"C:\\untrusted\\path\\script.json"

    imported = _import(tmp_path, data)

    assert imported["imported_from"] == "external_recap_script.json"
