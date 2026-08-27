"""Staged, grounded recap writing with repair and quality validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .llm import JsonModel, ModelGeneration
from .models import (
    PRESENTATION_HINTS,
    SCHEMA_VERSION,
    RecapValidationError,
    utc_now,
    validate_recap_script,
    write_json,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTLINE_PROMPT_PATH = ROOT / "prompts" / "recap_outline.md"
DEFAULT_PROMPT_PATH = ROOT / "prompts" / "recap_writer.md"
DEFAULT_CRITIC_PROMPT_PATH = ROOT / "prompts" / "recap_critic.md"
DEFAULT_REPAIR_PROMPT_PATH = ROOT / "prompts" / "recap_repair.md"
WRITER_PROMPT_VERSION = "recap-writer-high-retention-v2"


class RecapWritingError(RuntimeError):
    """Raised when a recap cannot be grounded into the shared handoff."""


@dataclass(frozen=True)
class RecapWritingConfig:
    target_duration_seconds: int = 120
    target_word_count: int = 310
    voice_style: str = "fast_story_recap"
    minimum_word_count: int = 180
    maximum_word_count: int = 360
    max_repair_attempts: int = 2
    max_revision_attempts: int = 2


def _word_count(text: str) -> int:
    return len(str(text or "").split())


def _beat_map(
    story_map: dict[str, Any],
    *,
    verified_only: bool = False,
) -> dict[str, dict[str, Any]]:
    return {
        str(beat.get("beat_id")): beat
        for beat in story_map.get("beats", [])
        if isinstance(beat, dict)
        and beat.get("beat_id")
        and (
            not verified_only
            or beat.get("verification_status") == "verified"
        )
    }


def _story_payload(story_map: dict[str, Any]) -> dict[str, Any]:
    """Keep prompts focused on verified evidence and avoid segment ambiguity."""
    fields = (
        "beat_id",
        "chronological_order",
        "segment_id",
        "summary",
        "story_purpose",
        "characters",
        "location",
        "importance",
        "causal_parents",
        "causal_children",
        "actual_video_evidence_ranges",
        "original_dialogue_candidates",
        "confidence",
    )
    beats = [
        {field: beat.get(field) for field in fields if field in beat}
        for beat in story_map.get("beats", [])
        if isinstance(beat, dict)
        and beat.get("verification_status") == "verified"
    ]
    return {
        "canonical_identity": story_map.get("canonical_identity", {}),
        "duration_seconds": story_map.get("duration_seconds"),
        "confidence": story_map.get("confidence"),
        "warnings": list(story_map.get("warnings", []) or []),
        "verified_beats": beats,
    }


def _ranges_for_beats(
    beat_ids: list[str],
    beats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    for beat_id in beat_ids:
        beat = beats.get(beat_id, {})
        for item in beat.get("actual_video_evidence_ranges", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                start = float(item.get("start"))
                end = float(item.get("end"))
            except (TypeError, ValueError):
                continue
            if end <= start or (start, end) in seen:
                continue
            seen.add((start, end))
            ranges.append(
                {
                    "start": round(start, 4),
                    "end": round(end, 4),
                    "score": round(float(item.get("confidence", 0.0) or 0.0), 4),
                    "reason": f"Verified source evidence for story beat {beat_id}.",
                }
            )
    return ranges


def _dialogue_for_beats(
    beat_ids: list[str],
    beats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for beat_id in beat_ids:
        for item in beats.get(beat_id, {}).get("original_dialogue_candidates", []) or []:
            if isinstance(item, dict):
                candidates.append(dict(item))
    return candidates


def _normalize_ranges(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RecapWritingError(f"{field} must be a list")
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise RecapWritingError(f"{field}[{index}] must be an object")
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError) as exc:
            raise RecapWritingError(
                f"{field}[{index}] needs numeric start/end"
            ) from exc
        if end <= start:
            raise RecapWritingError(f"{field}[{index}] must have end > start")
        try:
            score = float(raw.get("score", raw.get("confidence", 0.5)) or 0.5)
        except (TypeError, ValueError):
            score = 0.5
        output.append(
            {
                "start": round(start, 4),
                "end": round(end, 4),
                "score": round(max(0.0, min(1.0, score)), 4),
                "reason": str(raw.get("reason", "") or "").strip(),
            }
        )
    return output


def normalize_script(
    raw: dict[str, Any],
    story_map: dict[str, Any],
    config: RecapWritingConfig,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError("Recap writer response must be an object")
    raw_segments = raw.get("segments", [])
    if not isinstance(raw_segments, list) or not raw_segments:
        raise RecapWritingError("Recap writer returned no segments")

    all_beats = _beat_map(story_map)
    verified_beats = _beat_map(story_map, verified_only=True)
    segments: list[dict[str, Any]] = []
    seen_segment_ids: set[str] = set()
    for index, raw_segment in enumerate(raw_segments, start=1):
        if not isinstance(raw_segment, dict):
            raise RecapWritingError(f"Recap segment {index} is not an object")
        raw_beat_ids = raw_segment.get("beat_ids", [])
        if not isinstance(raw_beat_ids, list) or not raw_beat_ids:
            raise RecapWritingError(f"Recap segment {index} needs beat_ids")
        beat_ids = list(dict.fromkeys(str(value) for value in raw_beat_ids))
        unknown = [beat_id for beat_id in beat_ids if beat_id not in all_beats]
        unverified = [
            beat_id
            for beat_id in beat_ids
            if beat_id in all_beats and beat_id not in verified_beats
        ]
        if unknown:
            raise RecapWritingError(
                f"Recap segment {index} references unknown beat IDs: {unknown}"
            )
        if unverified:
            raise RecapWritingError(
                f"Recap segment {index} references unverified beat IDs: {unverified}"
            )

        text = " ".join(str(raw_segment.get("text", "") or "").split()).strip()
        if not text:
            raise RecapWritingError(f"Recap segment {index} has no narration text")
        hint = str(
            raw_segment.get("presentation_hint", "narration_over_source")
        ).strip()
        if hint not in PRESENTATION_HINTS:
            raise RecapWritingError(f"Invalid presentation hint: {hint}")

        visuals = raw_segment.get("candidate_visuals")
        if not isinstance(visuals, list) or not visuals:
            visuals = _ranges_for_beats(beat_ids, verified_beats)
        else:
            visuals = _normalize_ranges(visuals, f"segment {index}.candidate_visuals")
        dialogue = raw_segment.get("original_dialogue_candidates")
        if not isinstance(dialogue, list) or not dialogue:
            dialogue = _dialogue_for_beats(beat_ids, verified_beats)
        dialogue = _normalize_ranges(
            dialogue if isinstance(dialogue, list) else [],
            f"segment {index}.original_dialogue_candidates",
        )

        importance = raw_segment.get("importance")
        try:
            importance = float(importance)
        except (TypeError, ValueError):
            importance = max(
                [
                    float(verified_beats[beat_id].get("importance", 0.5) or 0.5)
                    for beat_id in beat_ids
                ]
                or [0.5]
            )
        segment_id = str(raw_segment.get("segment_id") or f"VO_{index:03d}").strip()
        if not segment_id or segment_id in seen_segment_ids:
            raise RecapWritingError("Recap segment IDs must be present and unique")
        seen_segment_ids.add(segment_id)
        segments.append(
            {
                "segment_id": segment_id,
                "order": index,
                "text": text,
                "word_count": _word_count(text),
                "beat_ids": beat_ids,
                "presentation_hint": hint,
                "importance": round(max(0.0, min(1.0, importance)), 4),
                "candidate_visuals": visuals,
                "original_dialogue_candidates": dialogue,
            }
        )

    script = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "target_duration_seconds": config.target_duration_seconds,
        "target_word_count": config.target_word_count,
        "voice_style": config.voice_style,
        "actual_word_count": sum(segment["word_count"] for segment in segments),
        "segments": segments,
        "warnings": list(raw.get("warnings", []) or []),
    }
    validate_recap_script(script, story_map)
    return script


def _choice(
    raw: Any,
    field: str,
    verified_beats: dict[str, dict[str, Any]],
    *,
    required: bool,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError(f"outline.{field} must be an object")
    values = raw.get("beat_ids", [])
    if not isinstance(values, list):
        raise RecapWritingError(f"outline.{field}.beat_ids must be a list")
    beat_ids = list(dict.fromkeys(str(value) for value in values))
    invalid = [beat_id for beat_id in beat_ids if beat_id not in verified_beats]
    if invalid:
        raise RecapWritingError(
            f"outline.{field} references invalid beat IDs: {invalid}"
        )
    if required and not beat_ids:
        raise RecapWritingError(f"outline.{field} must choose at least one beat")
    return {
        "beat_ids": beat_ids,
        "intent": " ".join(str(raw.get("intent", "") or "").split()).strip(),
    }


def normalize_outline(
    raw: dict[str, Any],
    story_map: dict[str, Any],
    config: RecapWritingConfig,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError("Narrative outline response must be an object")
    verified_beats = _beat_map(story_map, verified_only=True)
    if not verified_beats:
        raise RecapWritingError("No verified beats are available for recap writing")

    outline: dict[str, Any] = {
        "hook": _choice(raw.get("hook"), "hook", verified_beats, required=True),
        "minimum_setup": _choice(
            raw.get("minimum_setup"),
            "minimum_setup",
            verified_beats,
            required=False,
        ),
        "reversal": _choice(
            raw.get("reversal"), "reversal", verified_beats, required=False
        ),
        "payoff_climax": _choice(
            raw.get("payoff_climax"),
            "payoff_climax",
            verified_beats,
            required=False,
        ),
        "resolution_button": _choice(
            raw.get("resolution_button"),
            "resolution_button",
            verified_beats,
            required=False,
        ),
    }
    for field in ("essential_causal_chain", "escalation_beats"):
        values = raw.get(field)
        if not isinstance(values, list):
            raise RecapWritingError(f"outline.{field} must be a list")
        outline[field] = [
            _choice(item, f"{field}[{index}]", verified_beats, required=True)
            for index, item in enumerate(values)
        ]
    if not outline["essential_causal_chain"]:
        raise RecapWritingError("outline.essential_causal_chain cannot be empty")

    payoff_beats = {
        beat_id
        for beat_id, beat in verified_beats.items()
        if any(
            token in str(beat.get("story_purpose", "") or "").casefold()
            for token in ("payoff", "climax")
        )
    }
    if payoff_beats and not (
        payoff_beats & set(outline["payoff_climax"]["beat_ids"])
    ):
        raise RecapWritingError(
            "outline.payoff_climax must include a verified payoff/climax beat"
        )

    outline["omitted_beat_ids"] = [
        str(value)
        for value in raw.get("omitted_beat_ids", [])
        if str(value) in verified_beats
    ]
    return outline


def _supported_range(
    candidate: dict[str, Any],
    sources: list[dict[str, Any]],
) -> bool:
    try:
        start = float(candidate.get("start"))
        end = float(candidate.get("end"))
    except (TypeError, ValueError):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        try:
            source_start = float(source.get("start"))
            source_end = float(source.get("end"))
        except (TypeError, ValueError):
            continue
        if abs(start - source_start) <= 0.001 and abs(end - source_end) <= 0.001:
            return True
    return False


def validate_script_quality_invariants(
    script: dict[str, Any],
    story_map: dict[str, Any],
    outline: dict[str, Any],
    config: RecapWritingConfig,
) -> None:
    try:
        validate_recap_script(script, story_map)
    except RecapValidationError as exc:
        raise RecapWritingError(str(exc)) from exc
    segments = script.get("segments", [])
    if not segments:
        raise RecapWritingError("Recap writer returned no segments")

    verified_beats = _beat_map(story_map, verified_only=True)
    represented: set[str] = set()
    for segment in segments:
        beat_ids = [str(value) for value in segment.get("beat_ids", [])]
        represented.update(beat_ids)
        evidence = [
            item
            for beat_id in beat_ids
            for item in verified_beats.get(beat_id, {}).get(
                "actual_video_evidence_ranges", []
            )
            if isinstance(item, dict)
        ]
        visuals = segment.get("candidate_visuals", [])
        if not visuals:
            raise RecapWritingError(
                f"{segment.get('segment_id')} needs candidate visuals"
            )
        for visual in visuals:
            if not _supported_range(visual, evidence):
                raise RecapWritingError(
                    f"{segment.get('segment_id')} has a candidate visual range "
                    "that is not present in its referenced verified beats"
                )
            if not str(visual.get("reason", "") or "").strip():
                raise RecapWritingError(
                    f"{segment.get('segment_id')} has a candidate visual without a reason"
                )

        dialogue_sources = [
            item
            for beat_id in beat_ids
            for item in verified_beats.get(beat_id, {}).get(
                "original_dialogue_candidates", []
            )
            if isinstance(item, dict)
        ]
        for dialogue in segment.get("original_dialogue_candidates", []):
            if not _supported_range(dialogue, dialogue_sources):
                raise RecapWritingError(
                    f"{segment.get('segment_id')} has an unsupported dialogue range"
                )

    words = int(script.get("actual_word_count", 0) or 0)
    evidence_scaled_minimum = min(
        config.minimum_word_count,
        max(1, len(verified_beats) * 16),
    )
    if words < evidence_scaled_minimum:
        raise RecapWritingError(
            f"Recap has {words} words; minimum sensible budget is "
            f"{evidence_scaled_minimum} for the available verified story material"
        )
    if words > config.maximum_word_count:
        raise RecapWritingError(
            f"Recap has {words} words; maximum budget is {config.maximum_word_count}"
        )

    first_ids = set(str(value) for value in segments[0].get("beat_ids", []))
    if not first_ids.intersection(outline["hook"]["beat_ids"]):
        raise RecapWritingError("The first narration segment does not ground the chosen hook")
    chain_ids = {
        beat_id
        for item in outline["essential_causal_chain"]
        for beat_id in item["beat_ids"]
    }
    missing_chain = sorted(chain_ids - represented)
    if missing_chain:
        raise RecapWritingError(
            f"Recap omits essential causal-chain beats: {missing_chain}"
        )
    payoff_ids = set(outline["payoff_climax"]["beat_ids"])
    if payoff_ids and not payoff_ids.intersection(represented):
        raise RecapWritingError("Recap omits the selected payoff/climax")


def normalize_critique(
    raw: dict[str, Any],
    expected_segment_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecapWritingError("Quality critique response must be an object")
    if not isinstance(raw.get("passes"), bool):
        raise RecapWritingError("Quality critique needs a boolean passes field")
    raw_grounding = raw.get("segment_grounding")
    if not isinstance(raw_grounding, list):
        raise RecapWritingError("Quality critique needs segment_grounding")
    grounding: list[dict[str, Any]] = []
    seen_grounding: set[str] = set()
    for index, item in enumerate(raw_grounding):
        if not isinstance(item, dict):
            raise RecapWritingError(f"Segment grounding {index} must be an object")
        segment_id = str(item.get("segment_id", "") or "").strip()
        if not segment_id or segment_id in seen_grounding:
            raise RecapWritingError(
                "Segment grounding IDs must be present and unique"
            )
        if not isinstance(item.get("supported"), bool):
            raise RecapWritingError(
                f"Segment grounding {segment_id} needs a boolean supported field"
            )
        claims = item.get("unsupported_claims", [])
        if not isinstance(claims, list):
            raise RecapWritingError(
                f"Segment grounding {segment_id}.unsupported_claims must be a list"
            )
        normalized_claims = [
            " ".join(str(value).split()).strip()
            for value in claims
            if " ".join(str(value).split()).strip()
        ]
        if not item["supported"] and not normalized_claims:
            raise RecapWritingError(
                f"Unsupported segment {segment_id} must identify unsupported claims"
            )
        seen_grounding.add(segment_id)
        grounding.append(
            {
                "segment_id": segment_id,
                "supported": item["supported"],
                "unsupported_claims": normalized_claims,
            }
        )
    expected = set(expected_segment_ids or [])
    if expected and seen_grounding != expected:
        missing = sorted(expected - seen_grounding)
        extra = sorted(seen_grounding - expected)
        raise RecapWritingError(
            f"Segment grounding coverage mismatch; missing={missing}, extra={extra}"
        )
    raw_issues = raw.get("issues", [])
    if not isinstance(raw_issues, list):
        raise RecapWritingError("Quality critique issues must be a list")
    issues: list[dict[str, Any]] = []
    for index, issue in enumerate(raw_issues):
        if not isinstance(issue, dict):
            raise RecapWritingError(f"Quality issue {index} must be an object")
        message = " ".join(str(issue.get("message", "") or "").split()).strip()
        if not message:
            raise RecapWritingError(f"Quality issue {index} needs a message")
        severity = str(issue.get("severity", "major") or "major").casefold()
        if severity not in {"minor", "major"}:
            raise RecapWritingError(f"Quality issue {index} has invalid severity")
        issues.append(
            {
                "category": str(issue.get("category", "quality") or "quality"),
                "severity": severity,
                "message": message,
                "segment_ids": [
                    str(value) for value in issue.get("segment_ids", []) or []
                ],
            }
        )
    if not raw["passes"] and not issues:
        raise RecapWritingError("A failing quality critique must explain its issues")
    if raw["passes"] and any(issue["severity"] == "major" for issue in issues):
        raise RecapWritingError(
            "A passing quality critique cannot contain a major issue"
        )
    if raw["passes"] and any(not item["supported"] for item in grounding):
        raise RecapWritingError(
            "A passing quality critique cannot contain unsupported segments"
        )
    instructions = raw.get("revision_instructions", [])
    if not isinstance(instructions, list):
        raise RecapWritingError("revision_instructions must be a list")
    return {
        "passes": raw["passes"],
        "segment_grounding": grounding,
        "issues": issues,
        "revision_instructions": [
            " ".join(str(value).split()).strip()
            for value in instructions
            if " ".join(str(value).split()).strip()
        ],
    }


class RecapWriter:
    """Build a selected story spine, draft it, validate it, and revise it."""

    prompt_version = WRITER_PROMPT_VERSION

    def __init__(
        self,
        model: JsonModel,
        *,
        prompt_path: Path = DEFAULT_PROMPT_PATH,
        outline_prompt_path: Path = DEFAULT_OUTLINE_PROMPT_PATH,
        critic_prompt_path: Path = DEFAULT_CRITIC_PROMPT_PATH,
        repair_prompt_path: Path = DEFAULT_REPAIR_PROMPT_PATH,
        config: RecapWritingConfig = RecapWritingConfig(),
        debug_dir: Path | None = None,
    ):
        self.model = model
        self.prompt_path = prompt_path
        self.outline_prompt_path = outline_prompt_path
        self.critic_prompt_path = critic_prompt_path
        self.repair_prompt_path = repair_prompt_path
        self.config = config
        self.debug_dir = debug_dir
        self.last_diagnostics: dict[str, Any] = {}

    @property
    def model_version(self) -> str:
        name = str(getattr(self.model, "model", "") or "").strip()
        return f"ollama:{name}" if name else type(self.model).__name__

    def cache_identity(self) -> tuple[str, str]:
        digest = hashlib.sha256()
        for path in (
            self.outline_prompt_path,
            self.prompt_path,
            self.critic_prompt_path,
            self.repair_prompt_path,
        ):
            digest.update(path.read_bytes())
        prompt_identity = f"{self.prompt_version}:{digest.hexdigest()[:16]}"
        return prompt_identity, self.model_version

    def set_debug_dir(self, path: Path) -> None:
        self.debug_dir = path

    @staticmethod
    def _read_prompt(path: Path) -> str:
        return path.read_text(encoding="utf-8").strip()

    def build_prompt(self, story_map: dict[str, Any]) -> str:
        """Retain a useful public prompt builder for callers and diagnostics."""
        return (
            self._read_prompt(self.prompt_path)
            + "\n\nVERIFIED STORY MAP:\n"
            + json.dumps(_story_payload(story_map), indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    def _outline_prompt(self, story_map: dict[str, Any]) -> str:
        verified_beat_ids = list(_beat_map(story_map, verified_only=True))
        return (
            self._read_prompt(self.outline_prompt_path)
            + "\n\nTARGET:\n"
            + json.dumps(
                {
                    "duration_seconds": self.config.target_duration_seconds,
                    "target_word_count": self.config.target_word_count,
                },
                indent=2,
            )
            + "\n\nVALID VERIFIED BEAT IDS (the only allowed IDs):\n"
            + json.dumps(verified_beat_ids)
            + "\n\nVERIFIED STORY MAP:\n"
            + json.dumps(_story_payload(story_map), indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    def _draft_prompt(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
        *,
        prior_script: dict[str, Any] | None = None,
        critique: dict[str, Any] | None = None,
    ) -> str:
        verified_beat_ids = list(_beat_map(story_map, verified_only=True))
        prompt = (
            self._read_prompt(self.prompt_path)
            + "\n\nSELECTED NARRATIVE OUTLINE:\n"
            + json.dumps(outline, indent=2, ensure_ascii=False)
            + "\n\nVALID VERIFIED BEAT IDS (the only allowed IDs):\n"
            + json.dumps(verified_beat_ids)
            + "\n\nVERIFIED STORY MAP:\n"
            + json.dumps(_story_payload(story_map), indent=2, ensure_ascii=False)
        )
        if prior_script is not None and critique is not None:
            prompt += (
                "\n\nSCRIPT TO REVISE:\n"
                + json.dumps(prior_script, indent=2, ensure_ascii=False)
                + "\n\nQUALITY CRITIQUE:\n"
                + json.dumps(critique, indent=2, ensure_ascii=False)
            )
        return prompt + "\n\nReturn JSON only."

    def _critique_prompt(
        self,
        story_map: dict[str, Any],
        outline: dict[str, Any],
        script: dict[str, Any],
    ) -> str:
        critique_script = {
            "actual_word_count": script.get("actual_word_count"),
            "voice_style": script.get("voice_style"),
            "segments": script.get("segments", []),
        }
        return (
            self._read_prompt(self.critic_prompt_path)
            + "\n\nSELECTED NARRATIVE OUTLINE:\n"
            + json.dumps(outline, indent=2, ensure_ascii=False)
            + "\n\nVERIFIED STORY MAP:\n"
            + json.dumps(_story_payload(story_map), indent=2, ensure_ascii=False)
            + "\n\nRECAP SCRIPT:\n"
            + json.dumps(critique_script, indent=2, ensure_ascii=False)
            + "\n\nReturn JSON only."
        )

    def _repair_prompt(
        self,
        *,
        stage: str,
        original_prompt: str,
        generation: ModelGeneration,
        errors: list[str],
    ) -> str:
        return (
            self._read_prompt(self.repair_prompt_path)
            + f"\n\nSTAGE: {stage}\n"
            + "\nVALIDATION ERRORS:\n"
            + json.dumps(errors, indent=2, ensure_ascii=False)
            + "\n\nINVALID RESPONSE:\n"
            + generation.raw_text
            + "\n\nORIGINAL TASK AND SOURCE OF TRUTH:\n"
            + original_prompt
            + "\n\nReturn the repaired JSON object only."
        )

    def _generate(self, prompt: str) -> ModelGeneration:
        generate = getattr(self.model, "generate", None)
        if callable(generate):
            result = generate(prompt)
            if isinstance(result, ModelGeneration):
                return result
            if isinstance(result, dict):
                return ModelGeneration(
                    raw_text=json.dumps(result, ensure_ascii=False),
                    parsed=result,
                )
            raise RecapWritingError("Model generate() returned an unsupported result")
        result = self.model.generate_json(prompt)
        if not isinstance(result, dict):
            raise RecapWritingError("Model response must be a JSON object")
        return ModelGeneration(
            raw_text=json.dumps(result, ensure_ascii=False),
            parsed=result,
        )

    def _run_stage(
        self,
        stage: str,
        prompt: str,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        current_prompt = prompt
        last_errors: list[str] = []
        attempts = 1 + max(0, self.config.max_repair_attempts)
        for attempt in range(1, attempts + 1):
            try:
                generation = self._generate(current_prompt)
            except Exception as exc:
                self.last_diagnostics["attempts"].append(
                    {
                        "stage": stage,
                        "attempt": attempt,
                        "kind": "initial" if attempt == 1 else "repair",
                        "raw_response": "",
                        "parse_error": str(exc),
                        "validation_errors": [str(exc)],
                    }
                )
                raise RecapWritingError(f"{stage} model call failed: {exc}") from exc

            errors: list[str] = []
            normalized: dict[str, Any] | None = None
            if generation.parsed is None:
                errors.append(
                    "Response is not a JSON object: "
                    + (generation.parse_error or "unknown parse error")
                )
            else:
                try:
                    normalized = validator(generation.parsed)
                except (RecapWritingError, RecapValidationError, ValueError) as exc:
                    errors.append(str(exc))
            self.last_diagnostics["attempts"].append(
                {
                    "stage": stage,
                    "attempt": attempt,
                    "kind": "initial" if attempt == 1 else "repair",
                    "raw_response": generation.raw_text,
                    "parse_error": generation.parse_error,
                    "validation_errors": errors,
                }
            )
            if not errors and normalized is not None:
                return normalized

            last_errors = errors
            if attempt >= attempts:
                break
            self.last_diagnostics["repair_attempt_count"] += 1
            current_prompt = self._repair_prompt(
                stage=stage,
                original_prompt=prompt,
                generation=generation,
                errors=errors,
            )
        raise RecapWritingError(
            f"{stage} remained invalid after {attempts} attempts: "
            + "; ".join(last_errors)
        )

    def _persist_diagnostics(self) -> None:
        if self.debug_dir is None:
            return
        write_json(
            self.debug_dir / "recap_writer_diagnostics.json",
            self.last_diagnostics,
        )

    def write(self, story_map: dict[str, Any]) -> dict[str, Any]:
        self.last_diagnostics = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "prompt_version": self.prompt_version,
            "model_version": self.model_version,
            "status": "running",
            "repair_attempt_count": 0,
            "revision_attempt_count": 0,
            "attempts": [],
        }
        try:
            if not _beat_map(story_map, verified_only=True):
                raise RecapWritingError(
                    "No verified story beats are available for the Ollama writer"
                )
            outline = self._run_stage(
                "narrative_outline",
                self._outline_prompt(story_map),
                lambda raw: normalize_outline(raw, story_map, self.config),
            )
            script = self._run_stage(
                "narration_draft",
                self._draft_prompt(story_map, outline),
                lambda raw: self._validated_script(raw, story_map, outline),
            )
            critique = self._run_stage(
                "quality_critique",
                self._critique_prompt(story_map, outline, script),
                lambda raw: normalize_critique(
                    raw,
                    [segment["segment_id"] for segment in script["segments"]],
                ),
            )
            revision = 0
            while not critique["passes"] and revision < self.config.max_revision_attempts:
                revision += 1
                self.last_diagnostics["revision_attempt_count"] = revision
                script = self._run_stage(
                    f"narration_revision_{revision}",
                    self._draft_prompt(
                        story_map,
                        outline,
                        prior_script=script,
                        critique=critique,
                    ),
                    lambda raw: self._validated_script(raw, story_map, outline),
                )
                critique = self._run_stage(
                    f"quality_critique_{revision + 1}",
                    self._critique_prompt(story_map, outline, script),
                    lambda raw: normalize_critique(
                        raw,
                        [
                            segment["segment_id"]
                            for segment in script["segments"]
                        ],
                    ),
                )
            if not critique["passes"]:
                messages = [issue["message"] for issue in critique["issues"]]
                raise RecapWritingError(
                    "Recap failed the quality gate after bounded revision: "
                    + "; ".join(messages)
                )

            self.last_diagnostics.update(
                {
                    "status": "success",
                    "word_count": script["actual_word_count"],
                    "segment_count": len(script["segments"]),
                    "final_critique": critique,
                }
            )
            self._persist_diagnostics()
            return script
        except Exception as exc:
            error = exc if isinstance(exc, RecapWritingError) else RecapWritingError(str(exc))
            self.last_diagnostics.update(
                {
                    "status": "failed",
                    "error": str(error),
                }
            )
            self._persist_diagnostics()
            raise error

    def _validated_script(
        self,
        raw: dict[str, Any],
        story_map: dict[str, Any],
        outline: dict[str, Any],
    ) -> dict[str, Any]:
        script = normalize_script(raw, story_map, self.config)
        validate_script_quality_invariants(script, story_map, outline, self.config)
        return script


class TemplateRecapWriter:
    """Offline baseline that repeats only verified beat summaries."""

    prompt_version = "recap-template-v1"
    model_version = "deterministic-template-v1"

    def __init__(self, config: RecapWritingConfig = RecapWritingConfig()):
        self.config = config

    def cache_identity(self) -> tuple[str, str]:
        return self.prompt_version, self.model_version

    def write(self, story_map: dict[str, Any]) -> dict[str, Any]:
        raw_segments: list[dict[str, Any]] = []
        beats = _beat_map(story_map)
        for beat in story_map.get("beats", []):
            if not isinstance(beat, dict):
                continue
            if beat.get("verification_status") != "verified":
                continue
            summary = str(beat.get("summary", "") or "").strip()
            if not summary:
                continue
            beat_id = str(beat.get("beat_id"))
            raw_segments.append(
                {
                    "segment_id": f"VO_{len(raw_segments) + 1:03d}",
                    "text": summary,
                    "beat_ids": [beat_id],
                    "presentation_hint": "narration_over_source",
                    "candidate_visuals": _ranges_for_beats([beat_id], beats),
                    "original_dialogue_candidates": _dialogue_for_beats(
                        [beat_id], beats
                    ),
                }
            )
        if not raw_segments:
            raise RecapWritingError(
                "No verified story beats are available for a grounded recap script."
            )
        return normalize_script(
            raw_segments_payload(raw_segments), story_map, self.config
        )


def raw_segments_payload(segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {"segments": segments}
