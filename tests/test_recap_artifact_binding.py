from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from recap_media.artifacts import (
    resolve_recap_artifact_context,
    resolve_recap_artifact_context_for_script,
)
from recap_media.loader import RecapInputError, load_episode_identity, load_external_recap_script, load_verified_story_map


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "recap_media" / "valid"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _identity(source_filename: str) -> dict:
    identity = _fixture("episode_identity.json")
    identity["query"] = {"source_filename": source_filename}
    return identity


def _story_map(beat_count: int) -> dict:
    story_map = _fixture("verified_story_map.json")
    for order in range(3, beat_count + 1):
        story_map["beats"].append(
            {
                "beat_id": f"B{order:03d}",
                "order": order,
                "summary": f"Verified event {order} advances the story.",
                "importance": 0.8,
                "source_evidence": [
                    {
                        "start": float(order * 10),
                        "end": float(order * 10 + 4),
                        "type": "local_video",
                        "confidence": 0.9,
                    }
                ],
            }
        )
    return story_map


def _write_artifacts(root: Path, source_filename: str, beat_count: int) -> None:
    root.mkdir(parents=True)
    (root / "episode_identity.json").write_text(
        json.dumps(_identity(source_filename)), encoding="utf-8"
    )
    (root / "verified_story_map.json").write_text(
        json.dumps(_story_map(beat_count)), encoding="utf-8"
    )


def _external_b003_script() -> dict:
    script = copy.deepcopy(_fixture("external_recap_script_v2.json"))
    for segment in script["segments"][2:]:
        segment["beat_ids"] = ["B003"]
        for candidate in segment["candidate_visuals"]:
            candidate.update(start=30.0, end=34.0)
        for candidate in segment["original_dialogue_candidates"]:
            candidate.update(start=30.0, end=34.0)
    return script


def test_no_source_never_resolves_stale_artifacts(tmp_path):
    output_dir = tmp_path / "output"
    _write_artifacts(output_dir / "recap_stale", "old_episode.mkv", 3)

    with pytest.raises(RecapInputError, match="Load a source video"):
        resolve_recap_artifact_context(None, output_dir=output_dir)


def test_matching_source_prefers_more_complete_verified_evidence_and_validates_b003(tmp_path):
    output_dir = tmp_path / "output"
    source = tmp_path / "input" / "episode.mkv"
    source.parent.mkdir()
    source.touch()
    _write_artifacts(output_dir / "recap", source.name, 2)
    matching_root = output_dir / "recap_matching"
    _write_artifacts(matching_root, source.name, 3)

    context = resolve_recap_artifact_context(source, output_dir=output_dir)

    assert context.root == matching_root.resolve()
    script_path = tmp_path / "external.json"
    script_path.write_text(json.dumps(_external_b003_script()), encoding="utf-8")
    imported = load_external_recap_script(
        script_path,
        episode_identity=load_episode_identity(context.episode_identity_path),
        verified_story_map=load_verified_story_map(context.verified_story_map_path),
    )
    assert "B003" in {beat_id for segment in imported["segments"] for beat_id in segment["beat_ids"]}


def test_wrong_source_artifacts_are_ignored_even_when_they_have_more_beats(tmp_path):
    output_dir = tmp_path / "output"
    source = tmp_path / "input" / "episode.mkv"
    source.parent.mkdir()
    source.touch()
    _write_artifacts(output_dir / "recap_wrong", "other_episode.mkv", 6)
    matching_root = output_dir / "recap_matching"
    _write_artifacts(matching_root, source.name, 3)

    context = resolve_recap_artifact_context(source, output_dir=output_dir)

    assert context.root == matching_root.resolve()


def test_equally_complete_matching_artifacts_fail_instead_of_guessing(tmp_path):
    output_dir = tmp_path / "output"
    source = tmp_path / "input" / "episode.mkv"
    source.parent.mkdir()
    source.touch()
    _write_artifacts(output_dir / "recap_one", source.name, 3)
    _write_artifacts(output_dir / "recap_two", source.name, 3)

    with pytest.raises(RecapInputError, match="Multiple AI Recap artifact sets"):
        resolve_recap_artifact_context(source, output_dir=output_dir)


def test_explicit_script_anchors_to_its_matching_artifact_folder(tmp_path):
    output_dir = tmp_path / "output"
    source = tmp_path / "input" / "episode.mkv"
    source.parent.mkdir()
    source.touch()
    old_root = output_dir / "recap_episode"
    new_root = output_dir / "recap_episode_v2"
    _write_artifacts(old_root, source.name, 3)
    _write_artifacts(new_root, source.name, 3)
    old_script = old_root / "recap_script.json"
    new_script = new_root / "recap_script.json"
    old_script.write_text(json.dumps(_external_b003_script()), encoding="utf-8")
    new_script.write_text(json.dumps(_external_b003_script()), encoding="utf-8")

    new_context = resolve_recap_artifact_context_for_script(
        source, new_script, output_dir=output_dir
    )
    old_context = resolve_recap_artifact_context_for_script(
        source, old_script, output_dir=output_dir
    )

    assert new_context.root == new_root.resolve()
    assert old_context.root == old_root.resolve()


def test_explicit_script_with_incomplete_parent_falls_back_to_discovery(tmp_path):
    output_dir = tmp_path / "output"
    source = tmp_path / "input" / "episode.mkv"
    source.parent.mkdir()
    source.touch()
    matching_root = output_dir / "recap_episode"
    _write_artifacts(matching_root, source.name, 3)
    selected_script = tmp_path / "imports" / "recap_script.json"
    selected_script.parent.mkdir()
    selected_script.write_text(json.dumps(_external_b003_script()), encoding="utf-8")

    context = resolve_recap_artifact_context_for_script(
        source, selected_script, output_dir=output_dir
    )

    assert context.root == matching_root.resolve()


def test_complete_script_parent_for_another_source_fails_without_fallback(tmp_path):
    output_dir = tmp_path / "output"
    source = tmp_path / "input" / "episode.mkv"
    source.parent.mkdir()
    source.touch()
    matching_root = output_dir / "recap_matching"
    selected_root = output_dir / "recap_other"
    _write_artifacts(matching_root, source.name, 3)
    _write_artifacts(selected_root, "other_episode.mkv", 3)
    selected_script = selected_root / "recap_script.json"
    selected_script.write_text(json.dumps(_external_b003_script()), encoding="utf-8")

    with pytest.raises(RecapInputError, match="does not match the loaded source"):
        resolve_recap_artifact_context_for_script(
            source, selected_script, output_dir=output_dir
        )
