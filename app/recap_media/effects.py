"""Shared visual-FX, SFX, and emoji adapter for AI Recap Mode.

Track B owns the recap's base timeline.  This module translates its narration
caption timing into the existing regular-Shorts effect schemas and helpers;
it does not define a parallel effects vocabulary or renderer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from editor_asset_plan import (
    clips_of_kind,
    editor_plan_context_matches,
    load_editor_asset_plan,
    replace_kind_clips,
    save_editor_asset_plan,
    set_editor_plan_context,
)
from emoji_overlay import prepare_emoji_events, resolve_event_asset
from emoji_planner import build_emoji_candidates
from make_captions import choose_emoji_events
from pipeline_paths import RECAP_EDITOR_ASSET_PLAN_PATH, RECAP_EFFECTS_PLAN_PATH
from recap_media.sequence import voiceover_timing_by_segment
from sfx_engine import (
    candidate_for_event,
    collapse_stacks,
    event_from_sfx_clip,
    index_local_assets,
    prepare_events,
    select_events,
    sfx_clip_from_event,
)
from visual_emphasis import DEFAULT_ENERGY, normalize_energy
from visual_fx import build_semantic_moments, expand_moments_to_events, motion_events_for_moments


RECAP_EFFECTS_SCHEMA_VERSION = 1
RECAP_TIME_BASIS = "recap_base_timeline"
AI_RECAP_ORIGIN = "ai_recap"


class RecapEffectsError(Exception):
    """The persisted recap effect plan is malformed or unavailable."""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def recap_timeline_blocks(
    sequence: dict[str, Any],
    recap_script: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Authoritative final-assembly block windows with story provenance."""

    script_by_id = {
        str(segment.get("segment_id", "") or ""): segment
        for segment in (recap_script or {}).get("segments", [])
        if isinstance(segment, dict) and str(segment.get("segment_id", "") or "")
    }
    blocks: list[dict[str, Any]] = []
    cursor = 0.0
    for segment in sorted(sequence.get("segments", []), key=lambda item: item.get("order", 0)):
        if not isinstance(segment, dict):
            continue
        block_id = str(segment.get("segment_id", "") or "")
        if not block_id:
            continue
        start = _as_float(segment.get("timeline_start_seconds"), cursor)
        duration = max(0.0, _as_float(segment.get("timeline_duration_seconds")))
        end = _as_float(segment.get("timeline_end_seconds"), start + duration)
        if end < start:
            end = start + duration
        source = script_by_id.get(block_id, {})
        raw_beat_ids = segment.get("beat_ids") or source.get("beat_ids") or []
        blocks.append(
            {
                "block_id": block_id,
                "block_type": str(segment.get("block_type", source.get("block_type", "")) or ""),
                "start": round(start, 3),
                "end": round(end, 3),
                "beat_ids": [str(beat_id) for beat_id in raw_beat_ids if str(beat_id)],
            }
        )
        cursor = max(cursor, end)
    return blocks


def _block_for_time(blocks: list[dict[str, Any]], timestamp: float) -> dict[str, Any] | None:
    if not blocks:
        return None
    for block in blocks:
        if _as_float(block.get("start")) <= timestamp < _as_float(block.get("end")):
            return block
    return min(
        blocks,
        key=lambda block: min(
            abs(timestamp - _as_float(block.get("start"))),
            abs(timestamp - _as_float(block.get("end"))),
        ),
    )


def _stable_effect_id(family: str, event: dict[str, Any], block_id: str) -> str:
    descriptor = {
        "family": family,
        "block_id": block_id,
        "beat_ids": event.get("beat_ids", []),
        "start": round(_as_float(event.get("start")), 3),
        "end": round(_as_float(event.get("end")), 3),
        "effect": event.get("effect", event.get("movement", event.get("category", event.get("emoji", "")))),
        "trigger": event.get("trigger_word", event.get("matched_word", "")),
        "stack_id": event.get("stack_id", ""),
    }
    digest = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return f"recap_{family}_{block_id.lower()}_{digest}"


def _annotate_recap_events(
    events: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    family: str,
) -> list[dict[str, Any]]:
    """Give shared event dictionaries stable Recap ownership/provenance."""

    annotated: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        item = dict(event)
        block = _block_for_time(blocks, _as_float(item.get("start")))
        block_id = str((block or {}).get("block_id", "timeline") or "timeline")
        item["block_id"] = block_id
        item["beat_ids"] = list((block or {}).get("beat_ids", []))
        item["id"] = _stable_effect_id(family, item, block_id)
        item["origin"] = AI_RECAP_ORIGIN
        item["time_basis"] = RECAP_TIME_BASIS
        item["active"] = item.get("active", True) is not False
        item["manual_override"] = False
        item.setdefault("reason", f"ai_recap_{family}")
        annotated.append(item)
    return annotated


def _output_intervals(
    start: float,
    end: float,
    pauses: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """Map one WAV-relative word interval onto the recap base timeline."""

    cursor = max(0.0, start)
    accumulated_pause = 0.0
    intervals: list[tuple[float, float]] = []

    for pause in pauses:
        pause_start = max(0.0, _as_float(pause.get("narration_offset_seconds")))
        pause_duration = max(0.0, _as_float(pause.get("duration_seconds")))
        if pause_duration <= 0:
            continue
        if pause_start <= cursor:
            accumulated_pause += pause_duration
            continue
        if pause_start >= end:
            break
        intervals.append((cursor + accumulated_pause, pause_start + accumulated_pause))
        cursor = pause_start
        accumulated_pause += pause_duration

    intervals.append((cursor + accumulated_pause, max(cursor, end) + accumulated_pause))
    return [interval for interval in intervals if interval[1] > interval[0]]


def recap_base_timeline_words(
    sequence: dict[str, Any],
    narration_captions: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return normal ``visual_fx``/emoji words in recap base-time units."""

    timings = voiceover_timing_by_segment(sequence)
    words: list[dict[str, Any]] = []

    for segment in narration_captions.get("segments", []):
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id", "") or "")
        timing = timings.get(segment_id)
        if timing is None:
            continue
        base_start = _as_float(timing.get("start"))
        pauses = timing.get("dialogue_pauses", [])
        if not isinstance(pauses, list):
            pauses = []

        for word in segment.get("words", []):
            if not isinstance(word, dict):
                continue
            text = str(word.get("text", "") or "").strip()
            start = _as_float(word.get("start"))
            end = _as_float(word.get("end"), start)
            if not text or end <= start:
                continue
            for output_start, output_end in _output_intervals(start, end, pauses):
                words.append(
                    {
                        "word": text,
                        "start": round(base_start + output_start, 3),
                        "end": round(base_start + output_end, 3),
                        "segment_id": segment_id,
                    }
                )

    return sorted(words, key=lambda item: (item["start"], item["end"]))


def source_audio_insert_windows(sequence: dict[str, Any]) -> list[tuple[float, float]]:
    """Output-timeline windows where automatic SFX must stay silent."""

    windows: list[tuple[float, float]] = []
    cursor = 0.0
    for segment in sorted(sequence.get("segments", []), key=lambda item: item.get("order", 0)):
        for shot in segment.get("shots", []):
            duration = _as_float(shot.get("timeline_duration_seconds", shot.get("duration", 0.0)))
            start = _as_float(shot.get("timeline_start_seconds"), cursor)
            end = _as_float(shot.get("timeline_end_seconds"), start + duration)
            if shot.get("source_audio_insert") and end > start:
                windows.append((start, end))
            cursor = max(cursor, end)
    return windows


def _overlaps_protected_window(event: dict[str, Any], windows: list[tuple[float, float]]) -> bool:
    start = _as_float(event.get("start"))
    end = max(start + 0.06, _as_float(event.get("end"), start))
    return any(start < window_end and end > window_start for window_start, window_end in windows)


def _caption_anchor_y(portrait_plan: dict[str, Any]) -> float:
    content_y = _as_float(portrait_plan.get("content_y"))
    content_height = _as_float(portrait_plan.get("content_height"), 1920.0)
    return max(200.0, min(1660.0, content_y + content_height - 40.0))


def _emoji_clips(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Phase 6A is planning-only. Keep it offline and only propose assets the
    # existing emoji system has already resolved locally; Phase 6B can decide
    # whether to acquire/render any missing glyphs.
    prepared = []
    for event in events:
        if not isinstance(event, dict):
            continue
        asset_path = resolve_event_asset(event)
        if asset_path is None:
            continue
        prepared.append(
            {
                "id": str(event.get("id", "") or ""),
                "emoji": str(event.get("asset_description", event.get("emoji", "")) or ""),
                "path": asset_path,
                "start": _as_float(event.get("start")),
                "end": _as_float(event.get("end")),
                "matched_word": str(event.get("matched_word", "") or ""),
                "position_x": event.get("position_x"),
                "position_y": event.get("position_y"),
                "scale": event.get("scale", 1.0),
            }
        )
    metadata_by_id = {
        str(event.get("id", "") or ""): event
        for event in events
        if isinstance(event, dict)
    }
    clips: list[dict[str, Any]] = []
    for index, event in enumerate(prepared, start=1):
        metadata = metadata_by_id.get(str(event.get("id", "") or ""), {})
        clips.append(
            {
                "id": str(event.get("id") or f"recap_emoji_auto_{index:02d}"),
                "kind": "EMOJI",
                "time_basis": RECAP_TIME_BASIS,
                "start": round(_as_float(event.get("start")), 3),
                "end": round(_as_float(event.get("end")), 3),
                "emoji": str(event.get("emoji", "") or ""),
                "matched_word": str(event.get("matched_word", "") or ""),
                "asset_path": str(event["path"]),
                "position_x": event.get("position_x"),
                "position_y": event.get("position_y"),
                "scale": event.get("scale", 1.0),
                "active": True,
                "origin": AI_RECAP_ORIGIN,
                "manual_override": False,
                "block_id": str(metadata.get("block_id", "") or ""),
                "beat_ids": list(metadata.get("beat_ids", [])),
                "reason": str(metadata.get("reason", "emoji reaction") or "emoji reaction"),
                "stack_id": str(metadata.get("stack_id", "") or ""),
            }
        )
    return clips


def _sfx_candidates(
    fx_events: list[dict[str, Any]],
    motion_events: list[dict[str, Any]],
    emoji_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for event in fx_events:
        candidate = candidate_for_event(event)
        if candidate is not None:
            candidates.append(candidate)

    for event in motion_events:
        candidate = candidate_for_event(
            {
                "type": "camera",
                "start": event.get("start", 0.0),
                "end": event.get("end", event.get("start", 0.0)),
                "treatment": event.get("movement", "motion"),
                "trigger": event.get("trigger_word", ""),
                "reason": "semantic camera movement",
                "recipe": event.get("fx_recipe", ""),
                "stack_id": event.get("stack_id", ""),
                "hero": event.get("hero", False),
            }
        )
        if candidate is not None:
            candidates.append(candidate)

    for event in emoji_events:
        candidate = candidate_for_event(
            {
                "type": "emoji",
                "start": event.get("start", 0.0),
                "end": event.get("end", event.get("start", 0.0)),
                "trigger": event.get("matched_word", ""),
                "reason": "emoji appearance",
                "recipe": str(event.get("emoji", "")),
            }
        )
        if candidate is not None:
            candidates.append(candidate)

    return collapse_stacks(candidates)


def build_recap_effects_plan(
    sequence: dict[str, Any],
    narration_captions: dict[str, Any],
    portrait_plan: dict[str, Any],
    recap_script: dict[str, Any] | None = None,
    *,
    energy: str = DEFAULT_ENERGY,
) -> dict[str, Any]:
    """Create automatic shared-effect proposals for a recap base timeline."""

    energy = normalize_energy(energy)
    words = recap_base_timeline_words(sequence, narration_captions)
    duration = max(0.0, _as_float(sequence.get("total_duration_seconds")))
    blocks = recap_timeline_blocks(sequence, recap_script)
    moments, intensity_curve = build_semantic_moments(words, energy)
    moments = _annotate_recap_events(moments, blocks, "moment")
    fx_events = _annotate_recap_events(expand_moments_to_events(moments, energy), blocks, "fx")
    protected_windows = source_audio_insert_windows(sequence)
    fx_events = [
        event for event in fx_events if not _overlaps_protected_window(event, protected_windows)
    ]
    motion_events = _annotate_recap_events(
        motion_events_for_moments(moments, duration, energy), blocks, "motion"
    )
    motion_events = [
        event for event in motion_events if not _overlaps_protected_window(event, protected_windows)
    ]
    emoji_events = choose_emoji_events(
        build_emoji_candidates(words),
        words,
        energy,
        caption_anchor_y=_caption_anchor_y(portrait_plan),
    )
    emoji_events = [
        event for event in emoji_events if not _overlaps_protected_window(event, protected_windows)
    ]
    emoji_events = _annotate_recap_events(emoji_events, blocks, "emoji")

    selected_sfx = select_events(
        _sfx_candidates(fx_events, motion_events, emoji_events),
        energy,
        selection_start=0.0,
        selection_end=duration,
    )
    selected_sfx = [
        event for event in selected_sfx if not _overlaps_protected_window(event, protected_windows)
    ]
    warnings: list[str] = []
    prepared_sfx, skipped_sfx = prepare_events(
        selected_sfx,
        energy,
        index_local_assets(),
        warnings,
    )
    prepared_sfx = _annotate_recap_events(prepared_sfx, blocks, "sfx")
    sfx_clips = []
    for event in prepared_sfx:
        clip = sfx_clip_from_event(event)
        clip.update(
            {
                "block_id": event["block_id"],
                "beat_ids": event["beat_ids"],
                "reason": event["reason"],
                "stack_id": event.get("stack_id", ""),
            }
        )
        sfx_clips.append(clip)

    return {
        "schema_version": RECAP_EFFECTS_SCHEMA_VERSION,
        "time_basis": RECAP_TIME_BASIS,
        "render_enabled": False,
        "edit_energy": energy,
        "base_timeline_duration_seconds": round(duration, 3),
        "timeline_blocks": blocks,
        "source_audio_insert_windows": [
            {"start": round(start, 3), "end": round(end, 3)}
            for start, end in protected_windows
        ],
        "visual_fx": {
            "moments": moments,
            "intensity_curve": intensity_curve,
            "events": fx_events,
            "motion_events": motion_events,
        },
        "automatic_editor_clips": {
            "SFX": sfx_clips,
            "EMOJI": _emoji_clips(emoji_events),
        },
        "sfx": {
            "events": prepared_sfx,
            "skipped": skipped_sfx,
            "warnings": warnings,
        },
    }


def _manual_visual_events(data: dict[str, Any], field: str) -> list[dict[str, Any]]:
    visual = data.get("visual_fx", {})
    events = visual.get(field, []) if isinstance(visual, dict) else []
    return [
        dict(event)
        for event in events
        if isinstance(event, dict)
        and (bool(event.get("manual_override", False)) or bool(event.get("locked", False)))
    ]


def _merge_manual_visual_events(
    generated: list[dict[str, Any]],
    manual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {str(event.get("id", "") or ""): event for event in generated}
    for event in manual:
        event_id = str(event.get("id", "") or "")
        merged[event_id or f"manual_{len(merged):04d}"] = event
    return sorted(merged.values(), key=lambda event: (_as_float(event.get("start")), str(event.get("id", ""))))


def write_recap_effects_plan(
    effects_plan: dict[str, Any],
    *,
    source_key: str = "recap",
    effects_path: Path = RECAP_EFFECTS_PLAN_PATH,
    editor_plan_path: Path = RECAP_EDITOR_ASSET_PLAN_PATH,
) -> dict[str, Any]:
    """Persist automatic clips while retaining authoritative manual recap edits."""

    duration = _as_float(effects_plan.get("base_timeline_duration_seconds"))
    try:
        existing_payload = json.loads(effects_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing_payload = {}
    payload = dict(effects_plan)
    visual = dict(payload.get("visual_fx", {}))
    for field in ("events", "motion_events"):
        visual[field] = _merge_manual_visual_events(
            list(visual.get(field, [])),
            _manual_visual_events(existing_payload, field),
        )
    payload["visual_fx"] = visual
    editor_plan = load_editor_asset_plan(editor_plan_path)
    context_matches = editor_plan_context_matches(editor_plan, source_key, 0.0, duration)
    editor_plan = set_editor_plan_context(
        editor_plan,
        source_key,
        0.0,
        duration,
        clear_clips_on_change=not context_matches,
    )
    automatic = payload.get("automatic_editor_clips", {})
    for kind in ("SFX", "EMOJI"):
        clips = automatic.get(kind, []) if isinstance(automatic, dict) else []
        editor_plan = replace_kind_clips(editor_plan, kind, clips, preserve_manual=True)
    save_editor_asset_plan(editor_plan, editor_plan_path)

    payload["editor_asset_plan_path"] = str(editor_plan_path)
    effects_path.parent.mkdir(parents=True, exist_ok=True)
    effects_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def create_recap_effects_plan(
    sequence: dict[str, Any],
    narration_captions: dict[str, Any],
    portrait_plan: dict[str, Any],
    recap_script: dict[str, Any] | None = None,
    *,
    energy: str = DEFAULT_ENERGY,
    source_key: str = "recap",
    effects_path: Path = RECAP_EFFECTS_PLAN_PATH,
    editor_plan_path: Path = RECAP_EDITOR_ASSET_PLAN_PATH,
) -> dict[str, Any]:
    return write_recap_effects_plan(
        build_recap_effects_plan(
            sequence,
            narration_captions,
            portrait_plan,
            recap_script,
            energy=energy,
        ),
        source_key=source_key,
        effects_path=effects_path,
        editor_plan_path=editor_plan_path,
    )


def load_recap_effects(
    *,
    effects_path: Path = RECAP_EFFECTS_PLAN_PATH,
    editor_plan_path: Path = RECAP_EDITOR_ASSET_PLAN_PATH,
) -> dict[str, Any]:
    """Load renderable effects, honoring disabled/deleted recap entities."""

    try:
        data = json.loads(effects_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecapEffectsError(f"recap effects plan is unavailable: {effects_path}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != RECAP_EFFECTS_SCHEMA_VERSION:
        raise RecapEffectsError("recap effects plan has an unsupported schema")
    if data.get("render_enabled") is False:
        return {
            "visual_fx_events": [],
            "motion_events": [],
            "sfx_events": [],
            "emoji_events": [],
            "time_basis": data.get("time_basis", RECAP_TIME_BASIS),
        }

    editor_plan = load_editor_asset_plan(editor_plan_path)
    sfx_events = []
    for clip in clips_of_kind(editor_plan, "SFX", active_only=True):
        if clip.get("deleted"):
            continue
        event = event_from_sfx_clip(clip)
        if event is not None:
            sfx_events.append(event)

    emoji_events = []
    for clip in clips_of_kind(editor_plan, "EMOJI", active_only=True):
        if clip.get("deleted") or not clip.get("asset_path"):
            continue
        emoji_events.append(
            {
                "id": clip.get("id", ""),
                "emoji": clip.get("emoji", ""),
                "asset_path": clip["asset_path"],
                "start": _as_float(clip.get("start")),
                "end": _as_float(clip.get("end")),
                "matched_word": clip.get("matched_word", clip.get("label", "")),
                "position_x": clip.get("position_x"),
                "position_y": clip.get("position_y"),
                "scale": clip.get("scale", 1.0),
            }
        )

    visual = data.get("visual_fx", {})
    raw_events = visual.get("events", []) if isinstance(visual, dict) else []
    return {
        "visual_fx_events": [
            event for event in raw_events
            if isinstance(event, dict) and event.get("active", True) and not event.get("deleted")
        ],
        "motion_events": [
            event for event in (visual.get("motion_events", []) if isinstance(visual, dict) else [])
            if isinstance(event, dict) and event.get("active", True) and not event.get("deleted")
        ],
        "sfx_events": sorted(sfx_events, key=lambda event: _as_float(event.get("start"))),
        "emoji_events": prepare_emoji_events(emoji_events),
        "time_basis": data.get("time_basis", RECAP_TIME_BASIS),
    }
