"""Track A: research, source grounding, and recap writing intelligence."""

from .cache import ArtifactCache, source_fingerprint
from .identity import (
    EpisodeIdentityResolver,
    IdentityConfirmationRequired,
    IdentityResolutionError,
    IdentityQuery,
    parse_compound_title,
    parse_source_filename,
)
from .models import (
    IdentitySegment,
    PRESENTATION_HINTS,
    RecapValidationError,
    validate_identity_artifact,
    validate_recap_script,
    validate_research_dossier,
    validate_story_map,
)
from .pipeline import run_recap_pipeline
from .research import ResearchService
from .source import SourceMismatchError, align_story_map, load_transcript
from .writer import RecapWriter, TemplateRecapWriter

__all__ = [
    "ArtifactCache",
    "EpisodeIdentityResolver",
    "IdentityConfirmationRequired",
    "IdentityQuery",
    "IdentitySegment",
    "IdentityResolutionError",
    "PRESENTATION_HINTS",
    "RecapValidationError",
    "RecapWriter",
    "ResearchService",
    "SourceMismatchError",
    "TemplateRecapWriter",
    "align_story_map",
    "load_transcript",
    "parse_compound_title",
    "parse_source_filename",
    "run_recap_pipeline",
    "source_fingerprint",
    "validate_identity_artifact",
    "validate_recap_script",
    "validate_research_dossier",
    "validate_story_map",
]
