"""Resolve one source-bound AI Recap artifact context for a GUI session."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline_paths import OUTPUT_DIR, RECAP_DIR

from .loader import RecapInputError, load_episode_identity, load_verified_story_map


@dataclass(frozen=True)
class RecapArtifactContext:
    """The only Track A/Track B artifact root active for one source video."""

    root: Path
    source_video: Path
    episode_identity_path: Path
    verified_story_map_path: Path
    recap_script_path: Path
    recap_sequence_path: Path
    voiceover_dir: Path
    voiceover_manifest_path: Path
    pasted_script_path: Path

    @property
    def narration_captions_path(self) -> Path:
        return self.root / "narration_captions.json"

    @property
    def narration_captions_ass_path(self) -> Path:
        return self.root / "narration.ass"

    @property
    def audio_duck_plan_path(self) -> Path:
        return self.root / "audio_duck_plan.json"

    @property
    def portrait_framing_plan_path(self) -> Path:
        return self.root / "portrait_framing_plan.json"

    @property
    def final_recap_path(self) -> Path:
        return self.root / "final_recap.mp4"


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.expanduser().resolve(strict=False))))


def _source_reference(identity: dict[str, Any]) -> str | None:
    query = identity.get("query")
    if isinstance(query, dict):
        for key in (
            "source_video_path",
            "source_path",
            "source_file",
            "source_filename",
        ):
            value = query.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("source_video_path", "source_path", "source_file", "source_filename"):
        value = identity.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _identity_matches_source(identity: dict[str, Any], source_video: Path) -> bool:
    reference = _source_reference(identity)
    if not reference:
        return False

    reference_path = Path(reference).expanduser()
    if reference_path.is_absolute():
        return _normalized_path(reference_path) == _normalized_path(source_video)
    return reference_path.name.casefold() == source_video.name.casefold()


def _artifact_roots(output_dir: Path) -> list[Path]:
    roots = [output_dir / RECAP_DIR.name]
    if output_dir.exists():
        roots.extend(
            path
            for path in output_dir.iterdir()
            if path.is_dir() and path.name.startswith(f"{RECAP_DIR.name}_")
        )
    return sorted({root.resolve(strict=False) for root in roots}, key=lambda path: path.name.casefold())


def _context_for_root(root: Path, source_video: Path) -> RecapArtifactContext:
    voiceover_dir = root / "voiceover"
    return RecapArtifactContext(
        root=root,
        source_video=source_video,
        episode_identity_path=root / "episode_identity.json",
        verified_story_map_path=root / "verified_story_map.json",
        recap_script_path=root / "recap_script.json",
        recap_sequence_path=root / "recap_sequence.json",
        voiceover_dir=voiceover_dir,
        voiceover_manifest_path=voiceover_dir / "voiceover_manifest.json",
        pasted_script_path=root / "external_recap_script_paste.json",
    )


def _candidate_rank(story_map: dict[str, Any], context: RecapArtifactContext) -> tuple[int, int, int]:
    beats = story_map.get("beats")
    if not isinstance(beats, list):
        return (0, 0, 0)
    evidence_count = sum(
        len(beat.get("source_evidence", []))
        for beat in beats
        if isinstance(beat, dict) and isinstance(beat.get("source_evidence"), list)
    )
    return (len(beats), evidence_count, int(context.recap_script_path.exists()))


def resolve_recap_artifact_context(
    source_video: Path | None,
    *,
    output_dir: Path = OUTPUT_DIR,
) -> RecapArtifactContext:
    """Find the uniquely best validated artifact set for ``source_video``.

    Track A's identity artifact currently records the input filename, not a
    durable full source path. We therefore compare normalized absolute paths
    when one is present and otherwise compare normalized filenames. Candidate
    roots are ranked only by validated story-evidence completeness, never by
    modification time; an unresolved tie is an explicit error.
    """

    if source_video is None:
        raise RecapInputError("Load a source video to begin AI Recap.")

    source = Path(source_video).expanduser().resolve(strict=False)
    if not source.name:
        raise RecapInputError("Load a source video to begin AI Recap.")

    candidates: list[tuple[tuple[int, int, int], RecapArtifactContext]] = []
    for root in _artifact_roots(Path(output_dir).expanduser().resolve(strict=False)):
        context = _context_for_root(root, source)
        if not (
            context.episode_identity_path.exists()
            and context.verified_story_map_path.exists()
        ):
            continue
        try:
            identity = load_episode_identity(context.episode_identity_path)
            if not _identity_matches_source(identity, source):
                continue
            story_map = load_verified_story_map(context.verified_story_map_path)
        except RecapInputError:
            continue
        candidates.append((_candidate_rank(story_map, context), context))

    if not candidates:
        raise RecapInputError(
            f"No AI Recap artifacts match the loaded source {source.name!r}. "
            "Run Track A for this source first."
        )

    best_rank = max(rank for rank, _context in candidates)
    best_contexts = [context for rank, context in candidates if rank == best_rank]
    if len(best_contexts) != 1:
        names = ", ".join(sorted(context.root.name for context in best_contexts))
        raise RecapInputError(
            "Multiple AI Recap artifact sets match the loaded source with the "
            f"same verified evidence completeness: {names}."
        )
    return best_contexts[0]
