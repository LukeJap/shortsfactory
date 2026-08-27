"""Track A orchestration from confirmed identity to four recap artifacts."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Sequence

from .cache import ArtifactCache, cache_key, source_fingerprint
from .identity import (
    EpisodeIdentityResolver,
    IdentityConfirmationRequired,
    IdentityProvider,
    IdentityQuery,
    write_identity_artifact,
)
from .llm import JsonModel
from .models import (
    validate_research_dossier,
    validate_recap_script,
    validate_story_map,
    write_json,
)
from .providers import FandomProvider, MediaWikiProvider, TMDBProvider, TVMazeProvider
from .research import (
    ResearchProvider,
    ResearchService,
    classify_research_depth,
    validate_research_grounding,
)
from .source import (
    FANDOM_FAST_PATH_VERSION,
    SemanticStoryInterpreter,
    align_story_map,
    validate_story_grounding,
)
from .writer import RecapWriter, TemplateRecapWriter


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "output" / "recap"


def _default_identity_providers() -> list[IdentityProvider]:
    return [TVMazeProvider(), TMDBProvider(), MediaWikiProvider()]


def _default_research_providers() -> list[ResearchProvider]:
    return [TVMazeProvider(), TMDBProvider(), MediaWikiProvider(), FandomProvider()]


def _cached_valid(
    cache: ArtifactCache,
    key: str,
    validator,
) -> dict[str, Any] | None:
    payload = cache.get(key)
    if payload is None:
        return None
    try:
        validator(payload)
    except Exception:
        return None
    return payload


def run_recap_pipeline(
    *,
    query: IdentityQuery,
    source_video: Path,
    transcript_path: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    confirm_index: int | None = None,
    identity_providers: Sequence[IdentityProvider] | None = None,
    research_providers: Sequence[ResearchProvider] | None = None,
    writer: RecapWriter | TemplateRecapWriter | None = None,
    visual_evidence: Sequence[dict[str, Any]] | None = None,
    scene_boundaries: Sequence[float] | None = None,
    semantic_model: JsonModel | None = None,
    use_cache: bool = True,
) -> dict[str, Path]:
    intelligence_started = time.perf_counter()
    source_video = source_video.expanduser().resolve()
    if not source_video.exists():
        raise FileNotFoundError(f"Source video not found: {source_video}")
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    resolver = EpisodeIdentityResolver(
        identity_providers or _default_identity_providers()
    )
    resolution = resolver.resolve(query, confirm_index=confirm_index)
    identity_path = output_dir / "episode_identity.json"
    write_identity_artifact(identity_path, resolution)
    if resolution.selected is None:
        raise IdentityConfirmationRequired(
            f"Identity requires confirmation; inspect {identity_path}"
        )
    identity = resolution.selected.to_dict()

    source_identity: dict[str, Any] = {
        "video": source_fingerprint(source_video),
    }
    if transcript_path is not None and transcript_path.exists():
        source_identity["transcript"] = source_fingerprint(transcript_path)
    source_identity["alignment_inputs"] = {
        "visual_evidence": list(visual_evidence or []),
        "scene_boundaries": [float(value) for value in scene_boundaries or []],
    }
    cache = ArtifactCache(output_dir / ".cache")

    dossier_key = cache_key(
        identity=identity,
        source_identity=source_identity,
        artifact="episode_research_dossier",
        prompt_version="recap-research-v3-fandom-identity-locked",
        model_version="deterministic-provider-synthesis-v4",
    )
    research_started = time.perf_counter()
    dossier = (
        _cached_valid(cache, dossier_key, validate_research_dossier)
        if use_cache
        else None
    )
    dossier_cache_hit = dossier is not None
    if dossier is None:
        result = ResearchService(
            research_providers or _default_research_providers()
        ).collect(identity)
        dossier = result.dossier
        if use_cache:
            cache.put(dossier_key, dossier)
    dossier_path = output_dir / "episode_research_dossier.json"
    validate_research_dossier(dossier)
    validate_research_grounding(dossier)
    research_depth = classify_research_depth(dossier)
    dossier["research_depth"] = research_depth
    write_json(dossier_path, dossier)
    research_seconds = time.perf_counter() - research_started

    semantic_interpreter = None
    semantic_prompt_version = "no-semantic-model"
    semantic_model_version = "deterministic-research-alignment"
    if semantic_model is not None:
        semantic_interpreter = SemanticStoryInterpreter(semantic_model)
        semantic_interpreter.set_debug_dir(output_dir / ".semantic_debug")
        semantic_prompt_version, semantic_model_version = (
            semantic_interpreter.cache_identity()
        )

    story_key = cache_key(
        identity=identity,
        source_identity={
            **source_identity,
            "episode_research_dossier": dossier,
        },
        artifact="verified_story_map",
        prompt_version=(
            f"recap-source-align-v7:{FANDOM_FAST_PATH_VERSION}:"
            f"{research_depth['route']}:"
            f"{semantic_prompt_version}"
        ),
        model_version=(
            "transcript-evidence-v7:"
            f"{semantic_model_version}"
        ),
    )
    story_map = (
        _cached_valid(cache, story_key, validate_story_map)
        if use_cache
        else None
    )
    story_cache_hit = story_map is not None
    if story_map is None:
        story_map = align_story_map(
            identity=identity,
            dossier=dossier,
            source_video=source_video,
            transcript_path=transcript_path,
            visual_evidence=visual_evidence,
            scene_boundaries=scene_boundaries,
            semantic_interpreter=semantic_interpreter,
            research_depth=research_depth,
        )
        if use_cache:
            cache.put(story_key, story_map)
    story_path = output_dir / "verified_story_map.json"
    validate_story_map(story_map)
    validate_story_grounding(story_map, dossier)
    write_json(story_path, story_map)

    a1_a4_seconds = time.perf_counter() - intelligence_started
    runtime_path = output_dir / ".story_debug" / "intelligence_runtime.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        runtime_path,
        {
            "research_depth": research_depth,
            "selected_route": research_depth["route"],
            "cache": {
                "dossier_hit": dossier_cache_hit,
                "story_map_hit": story_cache_hit,
            },
            "runtime_seconds": {
                "research": round(research_seconds, 4),
                "alignment": story_map.get("fast_path_diagnostics", {})
                .get("runtime_seconds", {})
                .get("alignment", 0.0),
                "story_map_construction": story_map.get(
                    "fast_path_diagnostics", {}
                )
                .get("runtime_seconds", {})
                .get("story_map_construction", 0.0),
                "optional_llm_refinement": 0.0
                if research_depth["level"] == "RICH"
                else None,
                "total_a1_a4": round(a1_a4_seconds, 4),
            },
        },
    )

    recap_writer = writer or TemplateRecapWriter()
    set_debug_dir = getattr(recap_writer, "set_debug_dir", None)
    if callable(set_debug_dir):
        set_debug_dir(output_dir / ".writer_debug")
    writer_identity = getattr(recap_writer, "cache_identity", None)
    if callable(writer_identity):
        writer_prompt_version, writer_model_version = writer_identity()
    else:
        writer_prompt_version = type(recap_writer).__name__
        writer_model_version = "unknown-writer"
    script_key = cache_key(
        identity=identity,
        source_identity={"verified_story_map": story_map},
        artifact="recap_script",
        prompt_version=str(writer_prompt_version),
        model_version=str(writer_model_version),
    )
    script = (
        _cached_valid(
            cache,
            script_key,
            lambda payload: validate_recap_script(payload, story_map),
        )
        if use_cache
        else None
    )
    if script is None:
        script = recap_writer.write(story_map)
        if use_cache:
            cache.put(script_key, script)
    script_path = output_dir / "recap_script.json"
    validate_recap_script(script, story_map)
    write_json(script_path, script)

    return {
        "episode_identity": identity_path,
        "episode_research_dossier": dossier_path,
        "verified_story_map": story_path,
        "recap_script": script_path,
    }
