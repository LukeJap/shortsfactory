"""Pure source-to-render timeline remapping helpers shared by captions and SFX."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def keep_segments(
    plan: dict[str, Any], duration: float
) -> list[tuple[float, float]]:
    segments: list[tuple[float, float]] = []
    raw = plan.get("keep_segments", [])
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                start = float(item.get("start", 0.0))
                end = float(item.get("end", start))
            except (TypeError, ValueError):
                continue
            if end > start:
                segments.append((start, end))
    return segments or [(0.0, duration)]


def map_source_interval_to_tight(
    source_start: float,
    source_end: float,
    selection_start: float,
    keeps: list[tuple[float, float]],
) -> tuple[float, float] | None:
    base_start = max(0.0, source_start - selection_start)
    base_end = max(base_start, source_end - selection_start)
    accumulated = 0.0
    pieces: list[tuple[float, float]] = []
    for keep_start, keep_end in keeps:
        overlap_start = max(base_start, keep_start)
        overlap_end = min(base_end, keep_end)
        if overlap_end > overlap_start:
            pieces.append(
                (
                    accumulated + overlap_start - keep_start,
                    accumulated + overlap_end - keep_start,
                )
            )
        accumulated += keep_end - keep_start
    if not pieces:
        return None
    return pieces[0][0], pieces[-1][1]


def map_tight_time_to_final(tight_time: float, temporal_plan: dict[str, Any]) -> float:
    if not temporal_plan.get("applied", False):
        return tight_time
    mapping = temporal_plan.get("time_mapping", [])
    if not isinstance(mapping, list):
        return tight_time
    try:
        final_duration = float(
            temporal_plan.get("estimated_final_duration_seconds", tight_time)
            or tight_time
        )
    except (TypeError, ValueError):
        final_duration = tight_time
    previous_output = 0.0
    for segment in mapping:
        if not isinstance(segment, dict) or segment.get("kind") != "source":
            continue
        try:
            source_start = float(segment.get("source_start", 0.0) or 0.0)
            source_end = float(segment.get("source_end", source_start) or source_start)
            output_start = float(segment.get("output_start", previous_output) or previous_output)
            output_end = float(segment.get("output_end", output_start) or output_start)
            speed = float(segment.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            continue
        if source_start <= tight_time <= source_end:
            return max(
                0.0,
                min(
                    final_duration,
                    output_start + (tight_time - source_start) / max(0.001, speed),
                ),
            )
        previous_output = output_end
    return max(0.0, min(final_duration, previous_output))


def map_tight_interval_to_final(
    tight_start: float, tight_end: float, temporal_plan: dict[str, Any]
) -> tuple[float, float]:
    final_start = map_tight_time_to_final(tight_start, temporal_plan)
    final_end = map_tight_time_to_final(tight_end, temporal_plan)
    return final_start, max(final_start, final_end)
