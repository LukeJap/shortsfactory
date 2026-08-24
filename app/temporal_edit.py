from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .visual_emphasis import (
        DEFAULT_ENERGY,
        classify_word,
        energy_profile,
        intensity_for_moment,
        load_render_settings,
        normalize_energy,
        semantic_recipe,
    )
except ImportError:
    from visual_emphasis import (
        DEFAULT_ENERGY,
        classify_word,
        energy_profile,
        intensity_for_moment,
        load_render_settings,
        normalize_energy,
        semantic_recipe,
    )

try:
    from .pipeline_paths import (
        SUBTITLES_PATH as TRANSCRIPT_PATH,
        TEMPORAL_EDIT_PLAN_PATH as PLAN_PATH,
        TEMPORAL_SCENE_PLAN_PATH as SCENE_PLAN_PATH,
    )
except ImportError:
    from pipeline_paths import (
        SUBTITLES_PATH as TRANSCRIPT_PATH,
        TEMPORAL_EDIT_PLAN_PATH as PLAN_PATH,
        TEMPORAL_SCENE_PLAN_PATH as SCENE_PLAN_PATH,
    )


ROOT = Path(__file__).resolve().parent.parent

VIDEO_PATH = ROOT / "output" / "rendered" / "short1_tight.mp4"
TEMP_PATH = ROOT / "output" / "rendered" / "short1_temporal_tmp.mp4"

MIN_SOURCE_SEGMENT_SECONDS = 0.045
MIN_EVENT_SPACING_SECONDS = 1.15


def log(message: str = "") -> None:
    print(message, flush=True)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback

    if not math.isfinite(number):
        return fallback

    return number


def clean_word(value: str) -> str:
    return "".join(
        character
        for character in value.lower()
        if character.isalpha() or character == "'"
    )


def probe_video(video_path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,avg_frame_rate",
        "-of",
        "json",
        str(video_path),
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    data = json.loads(result.stdout)
    duration = as_float(
        data.get("format", {}).get("duration", 0.0),
        0.0,
    )
    fps = 30.0
    has_audio = False

    streams = data.get("streams", [])
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue

            codec_type = str(stream.get("codec_type", ""))
            if codec_type == "audio":
                has_audio = True
            elif codec_type == "video":
                rate = str(stream.get("avg_frame_rate", "30/1") or "30/1")
                try:
                    numerator, denominator = rate.split("/", 1)
                    parsed = float(numerator) / max(0.000001, float(denominator))
                    if math.isfinite(parsed) and parsed > 0:
                        fps = parsed
                except (ValueError, ZeroDivisionError):
                    pass

    return {
        "duration": duration,
        "fps": fps,
        "has_audio": has_audio,
    }


def detect_scene_cuts(duration: float) -> list[float]:
    scene_script = ROOT / "app" / "scene_detect.py"
    if not scene_script.exists() or duration <= 0:
        return []

    try:
        subprocess.run(
            [
                sys.executable,
                str(scene_script),
                "--video",
                str(VIDEO_PATH),
                "--start",
                "0",
                "--end",
                f"{duration:.3f}",
                "--output",
                str(SCENE_PLAN_PATH),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    data = load_json(SCENE_PLAN_PATH)
    cuts = data.get("cuts", [])
    if not isinstance(cuts, list):
        return []

    cleaned = []
    for value in cuts:
        numeric = as_float(value, -1.0)
        if 0.05 < numeric < duration - 0.05:
            cleaned.append(numeric)

    return sorted(cleaned)


def word_time(word: dict[str, Any]) -> tuple[float, float] | None:
    start = as_float(word.get("start"), -1.0)
    end = as_float(word.get("end"), start)

    if start < 0:
        return None

    if end <= start:
        end = start + 0.08

    return start, end


def overlaps_word(
    words: list[dict[str, Any]],
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    overlapping = []

    for word in words:
        timing = word_time(word)
        if timing is None:
            continue

        word_start, word_end = timing
        if word_end <= start or word_start >= end:
            continue

        overlapping.append(word)

    return overlapping


def crosses_scene_cut(
    scene_cuts: list[float],
    start: float,
    end: float,
    padding: float = 0.08,
) -> bool:
    return any(start + padding < cut < end - padding for cut in scene_cuts)


def nearby_event(
    events: list[dict[str, Any]],
    start: float,
    spacing: float = MIN_EVENT_SPACING_SECONDS,
) -> bool:
    return any(
        abs(start - as_float(event.get("source_start", event.get("anchor", 0.0))))
        < spacing
        for event in events
    )


def event_budget(duration: float, energy: str) -> dict[str, int]:
    energy = normalize_energy(energy)

    if energy == "MAXIMUM":
        scale = 0.72 if duration < 18 else 1.0
        return {
            "speed_up": max(1, int(round(3 * scale))),
            "speed_ramp": max(1, int(round(2 * scale))),
            "whip_transition": max(1, int(round(3 * scale))),
        }

    if energy == "PUNCHY":
        return {
            "speed_up": 1 if duration >= 18 else 0,
            "speed_ramp": 1 if duration >= 16 else 0,
            "whip_transition": 1 if duration >= 20 else 0,
        }

    return {
        "speed_up": 0,
        "speed_ramp": 0,
        "whip_transition": 0,
    }


def semantic_moments(
    words: list[dict[str, Any]],
    duration: float,
    energy: str,
) -> list[dict[str, Any]]:
    moments = []

    for index, word in enumerate(words):
        raw_word = str(word.get("word", "") or "").strip()
        if not raw_word:
            continue

        timing = word_time(word)
        if timing is None:
            continue

        start, end = timing
        classification = classify_word(word, energy)
        level = str(classification.get("level", "NORMAL"))
        if level == "NORMAL":
            continue

        recipe = semantic_recipe(word, classification)
        intensity = intensity_for_moment(
            start,
            duration,
            classification,
            recipe,
            energy,
        )
        intensity_value = as_float(intensity.get("intensity"), 0.0)
        score = as_float(classification.get("score"), 0.0)
        alpha = clean_word(raw_word)

        moments.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "word": raw_word,
                "alpha": alpha,
                "level": level,
                "recipe": recipe,
                "score": score,
                "intensity": intensity_value,
                "region": intensity.get("region", "unknown"),
                "classification": classification,
                "rank": (
                    score
                    + intensity_value * 5.0
                    + (2.0 if recipe in {"wtf_chaos", "reaction"} else 0.0)
                ),
            }
        )

    moments.sort(
        key=lambda moment: (
            -as_float(moment.get("rank"), 0.0),
            as_float(moment.get("start"), 0.0),
        )
    )
    return moments


def gap_candidates(words: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    gaps = []
    previous_end = 0.0

    for index, word in enumerate(words):
        timing = word_time(word)
        if timing is None:
            continue

        start, end = timing
        gap_start = previous_end
        gap_end = start
        gap_duration = gap_end - gap_start
        if gap_duration >= 0.34:
            gaps.append(
                {
                    "start": max(0.0, gap_start),
                    "end": min(duration, gap_end),
                    "duration": gap_duration,
                    "next_index": index,
                }
            )

        previous_end = max(previous_end, end)

    if duration - previous_end >= 0.34:
        gaps.append(
            {
                "start": previous_end,
                "end": duration,
                "duration": duration - previous_end,
                "next_index": len(words),
            }
        )

    gaps.sort(key=lambda gap: -as_float(gap.get("duration"), 0.0))
    return gaps


def add_event(
    events: list[dict[str, Any]],
    event: dict[str, Any],
    scene_cuts: list[float],
) -> bool:
    start = as_float(event.get("source_start", event.get("anchor")), 0.0)
    end = as_float(event.get("source_end", event.get("anchor")), start)

    if nearby_event(events, start):
        return False

    if end > start and crosses_scene_cut(scene_cuts, start, end):
        event["skipped_reason"] = "crosses_scene_cut"
        return False

    event["id"] = f"temporal_{len(events) + 1:02d}_{event['type']}"
    events.append(event)
    return True


def build_temporal_events(
    words: list[dict[str, Any]],
    duration: float,
    scene_cuts: list[float],
    energy: str,
) -> list[dict[str, Any]]:
    energy = normalize_energy(energy)
    budgets = event_budget(duration, energy)

    if energy == "LOW" or duration < 6:
        return []

    moments = semantic_moments(words, duration, energy)
    gaps = gap_candidates(words, duration)
    events: list[dict[str, Any]] = []

    selected_counts = {
        key: 0
        for key in budgets
    }

    # Fast connective beats: only use transcript gaps so dialogue stays clean.
    for gap in gaps:
        if selected_counts["speed_up"] >= budgets["speed_up"]:
            break

        gap_duration = as_float(gap.get("duration"), 0.0)
        if gap_duration < 0.42:
            continue

        start = as_float(gap.get("start"), 0.0) + 0.035
        end = min(
            as_float(gap.get("end"), duration) - 0.035,
            start + (0.82 if energy == "MAXIMUM" else 0.62),
        )

        if end - start < 0.28:
            continue

        speed = 1.30 if energy == "MAXIMUM" else 1.18
        if add_event(
            events,
            {
                "type": "speed_up",
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "speed": speed,
                "reason": "compress_non_dialogue_gap",
                "dialogue_protection": "selected_from_word_gap",
                "intensity": 0.58 if energy == "PUNCHY" else 0.76,
                "recipe": "connective_pacing",
            },
            scene_cuts,
        ):
            selected_counts["speed_up"] += 1

    # Speed ramps into payoff: approximate with a brief fast approach.
    for moment in moments:
        if selected_counts["speed_ramp"] >= budgets["speed_ramp"]:
            break

        start = max(0.0, as_float(moment.get("start"), 0.0) - 0.42)
        end = max(start, as_float(moment.get("start"), 0.0) - 0.04)
        if end - start < 0.22:
            continue

        if overlaps_word(words, start, end):
            continue

        if add_event(
            events,
            {
                "type": "speed_ramp",
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "speed": 1.34 if energy == "MAXIMUM" else 1.20,
                "reason": "fast_approach_into_payoff",
                "trigger_word": moment.get("word", ""),
                "recipe": moment.get("recipe", ""),
                "intensity": moment.get("intensity", 0.0),
                "dialogue_protection": "pre_word_gap_only",
            },
            scene_cuts,
        ):
            selected_counts["speed_ramp"] += 1

    # Whip transitions are brief accelerated gap beats with extra render
    # sharpening/flash treatment. They are timed around cuts or payoffs, but
    # still avoid speech so they do not disturb sentence intelligibility.
    for gap in gaps:
        if selected_counts["whip_transition"] >= budgets["whip_transition"]:
            break

        gap_duration = as_float(gap.get("duration"), 0.0)
        if gap_duration < 0.28:
            continue

        start = as_float(gap.get("start"), 0.0) + 0.025
        end = min(
            as_float(gap.get("end"), duration) - 0.025,
            start + (0.32 if energy == "MAXIMUM" else 0.24),
        )

        if end - start < 0.16:
            continue

        if add_event(
            events,
            {
                "type": "whip_transition",
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "speed": 1.46 if energy == "MAXIMUM" else 1.24,
                "reason": "fast_directional_transition_gap",
                "recipe": "transition_pacing",
                "intensity": 0.70 if energy == "PUNCHY" else 0.88,
                "dialogue_protection": "selected_from_word_gap",
            },
            scene_cuts,
        ):
            selected_counts["whip_transition"] += 1

    events.sort(
        key=lambda event: as_float(event.get("source_start"), 0.0)
    )
    return events


def speed_filter(speed: float) -> str:
    speed = max(0.5, min(2.0, speed))
    return f"atempo={speed:.6f}"


def add_source_segment(
    media_segments: list[dict[str, Any]],
    source_start: float,
    source_end: float,
    speed: float,
    segment_type: str,
    event_id: str = "",
) -> None:
    if source_end - source_start < MIN_SOURCE_SEGMENT_SECONDS:
        return

    output_start = (
        as_float(media_segments[-1].get("output_end"), 0.0)
        if media_segments
        else 0.0
    )
    output_duration = (source_end - source_start) / max(0.001, speed)

    media_segments.append(
        {
            "kind": "source",
            "type": segment_type,
            "source_start": round(source_start, 6),
            "source_end": round(source_end, 6),
            "output_start": round(output_start, 6),
            "output_end": round(output_start + output_duration, 6),
            "speed": round(speed, 4),
            "duration_before": round(source_end - source_start, 6),
            "duration_after": round(output_duration, 6),
            "event_id": event_id,
        }
    )


def build_media_segments(
    events: list[dict[str, Any]],
    duration: float,
) -> list[dict[str, Any]]:
    media_segments: list[dict[str, Any]] = []
    cursor = 0.0

    for event in events:
        event_type = str(event.get("type", ""))
        source_start = as_float(event.get("source_start"), 0.0)
        source_end = as_float(event.get("source_end"), source_start)
        speed = as_float(event.get("speed"), 1.0)

        if source_start < cursor - 0.001:
            event["skipped_reason"] = "overlaps_previous_temporal_segment"
            continue

        if source_start > cursor:
            add_source_segment(
                media_segments,
                cursor,
                source_start,
                1.0,
                "normal",
            )

        add_source_segment(
            media_segments,
            source_start,
            min(duration, source_end),
            speed,
            event_type,
            str(event.get("id", "")),
        )
        cursor = min(duration, source_end)

    if duration > cursor:
        add_source_segment(
            media_segments,
            cursor,
            duration,
            1.0,
            "normal",
        )

    return media_segments


def filter_for_video_segment(
    segment: dict[str, Any],
    index: int,
    fps: float,
) -> str:
    label = f"[v{index}]"
    segment_type = str(segment.get("type", "normal"))

    source_start = as_float(segment.get("source_start"), 0.0)
    source_end = as_float(segment.get("source_end"), source_start)
    speed = as_float(segment.get("speed"), 1.0)

    filters = [
        f"[0:v]trim=start={source_start:.6f}:end={source_end:.6f}",
    ]

    filters.append(f"setpts=(PTS-STARTPTS)/{max(0.001, speed):.6f}")

    if segment_type in {"speed_ramp", "speed_up", "whip_transition"}:
        filters.append("unsharp=5:5:0.36:3:3:0.12")

    if segment_type == "whip_transition":
        filters.append("eq=contrast=1.22:saturation=1.20:brightness=0.018")
        filters.append("drawbox=x=0:y=0:w=iw:h=ih:color=white@0.055:t=fill")

    filters.append("format=yuv420p")
    return ",".join(filters) + label


def filter_for_audio_segment(
    segment: dict[str, Any],
    index: int,
    audio_source: str,
) -> str:
    label = f"[a{index}]"
    segment_type = str(segment.get("type", "normal"))

    source_start = as_float(segment.get("source_start"), 0.0)
    source_end = as_float(segment.get("source_end"), source_start)
    speed = as_float(segment.get("speed"), 1.0)

    filters = [
        f"{audio_source}atrim=start={source_start:.6f}:end={source_end:.6f}",
        "asetpts=PTS-STARTPTS",
    ]

    if abs(speed - 1.0) > 0.001:
        filters.append(speed_filter(speed))

    return ",".join(filters) + label


def render_temporal_video(
    media_segments: list[dict[str, Any]],
    fps: float,
    has_audio: bool,
    expected_duration: float,
) -> None:
    filter_parts = []
    concat_inputs = []
    audio_source = "[0:a]" if has_audio else "[1:a]"

    for index, segment in enumerate(media_segments):
        filter_parts.append(
            filter_for_video_segment(segment, index, fps)
        )
        filter_parts.append(
            filter_for_audio_segment(segment, index, audio_source)
        )
        concat_inputs.append(f"[v{index}][a{index}]")

    filter_parts.append(
        "".join(concat_inputs)
        + f"concat=n={len(media_segments)}:v=1:a=1[vout][aout]"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(VIDEO_PATH),
    ]

    if not has_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )

    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(TEMP_PATH),
        ]
    )

    log()
    log("Applying temporal edit pass...")
    log(" ".join(command))
    log()

    subprocess.run(command, cwd=ROOT, check=True)

    # Never replace the real tight clip with a temporal render whose encoded
    # duration wildly disagrees with the planner. This keeps one malformed
    # filter from turning a ~1 minute Short into several minutes of bad media.
    rendered_probe = probe_video(TEMP_PATH)
    rendered_duration = as_float(
        rendered_probe.get("duration"),
        0.0,
    )
    tolerance = max(
        1.0,
        expected_duration * 0.08,
    )

    if (
        rendered_duration <= 0.0
        or abs(rendered_duration - expected_duration) > tolerance
    ):
        try:
            TEMP_PATH.unlink()
        except OSError:
            pass

        raise RuntimeError(
            "Temporal output duration safety check failed: "
            f"planned {expected_duration:.2f}s, "
            f"encoded {rendered_duration:.2f}s."
        )

    os.replace(TEMP_PATH, VIDEO_PATH)


def map_time(
    source_time: float,
    media_segments: list[dict[str, Any]],
    final_duration: float,
) -> float:
    source_time = max(0.0, source_time)

    for segment in media_segments:
        if segment.get("kind") != "source":
            continue

        source_start = as_float(segment.get("source_start"), 0.0)
        source_end = as_float(segment.get("source_end"), source_start)
        if source_start - 0.0001 <= source_time <= source_end + 0.0001:
            speed = max(0.001, as_float(segment.get("speed"), 1.0))
            output_start = as_float(segment.get("output_start"), 0.0)
            mapped = output_start + (source_time - source_start) / speed
            return max(0.0, min(final_duration, mapped))

    previous = 0.0
    for segment in media_segments:
        if segment.get("kind") != "source":
            continue

        source_start = as_float(segment.get("source_start"), 0.0)
        if source_time < source_start:
            return previous

        previous = as_float(segment.get("output_end"), previous)

    return final_duration


def remap_timed_item(
    item: dict[str, Any],
    media_segments: list[dict[str, Any]],
    final_duration: float,
) -> dict[str, Any]:
    remapped = copy.deepcopy(item)
    start = map_time(
        as_float(remapped.get("start"), 0.0),
        media_segments,
        final_duration,
    )
    end = map_time(
        as_float(remapped.get("end"), start),
        media_segments,
        final_duration,
    )

    if end <= start:
        end = min(final_duration, start + 0.04)

    remapped["start"] = round(start, 4)
    remapped["end"] = round(end, 4)
    return remapped


def remap_transcript(
    transcript: dict[str, Any],
    media_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    final_duration = (
        as_float(media_segments[-1].get("output_end"), 0.0)
        if media_segments
        else 0.0
    )
    remapped = copy.deepcopy(transcript)

    words = remapped.get("words", [])
    if isinstance(words, list):
        remapped["words"] = [
            remap_timed_item(word, media_segments, final_duration)
            for word in words
            if isinstance(word, dict)
        ]

    segments = remapped.get("segments", [])
    if isinstance(segments, list):
        new_segments = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue

            item = remap_timed_item(segment, media_segments, final_duration)
            segment_words = item.get("words", [])
            if isinstance(segment_words, list):
                item["words"] = [
                    remap_timed_item(word, media_segments, final_duration)
                    for word in segment_words
                    if isinstance(word, dict)
                ]
            new_segments.append(item)

        remapped["segments"] = new_segments

    remapped["temporal_edit"] = {
        "applied": True,
        "plan_path": str(PLAN_PATH),
        "final_duration_seconds": round(final_duration, 4),
        "time_mapping_version": 1,
    }
    return remapped


def annotate_events_with_output_times(
    events: list[dict[str, Any]],
    media_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    final_duration = (
        as_float(media_segments[-1].get("output_end"), 0.0)
        if media_segments
        else 0.0
    )
    annotated = []

    for event in events:
        item = dict(event)
        source_start = as_float(item.get("source_start"), 0.0)
        source_end = as_float(item.get("source_end"), source_start)
        speed = max(0.001, as_float(item.get("speed"), 1.0))
        item["output_start"] = round(
            map_time(
                source_start,
                media_segments,
                final_duration,
            ),
            4,
        )
        item["output_end"] = round(
            map_time(
                source_end,
                media_segments,
                final_duration,
            ),
            4,
        )
        item["duration_before"] = round(
            max(
                0.0,
                source_end - source_start,
            ),
            4,
        )
        item["duration_after"] = round(
            max(
                0.0,
                source_end - source_start,
            )
            / speed,
            4,
        )

        annotated.append(item)

    return annotated


def main() -> int:
    log("ShortsFactory temporal edit pass starting...")

    settings = load_render_settings()
    edit_energy = normalize_energy(
        settings.get("edit_energy", DEFAULT_ENERGY)
    )

    log(f"Edit energy: {edit_energy}")

    if not VIDEO_PATH.exists():
        log(f"WARNING: Tight video does not exist: {VIDEO_PATH}")
        return 0

    if not TRANSCRIPT_PATH.exists():
        log(f"WARNING: Transcript does not exist: {TRANSCRIPT_PATH}")
        return 0

    transcript = load_json(TRANSCRIPT_PATH)
    words = transcript.get("words", [])
    if not isinstance(words, list) or not words:
        log("No word timestamps available; skipping temporal edit.")
        write_json(
            PLAN_PATH,
            {
                "version": 1,
                "edit_energy": edit_energy,
                "applied": False,
                "reason": "no_word_timestamps",
                "events": [],
                "time_mapping": [],
            },
        )
        return 0

    try:
        probe = probe_video(VIDEO_PATH)
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as exc:
        log(f"WARNING: Could not inspect video for temporal editing: {exc}")
        return 0

    duration = as_float(probe.get("duration"), 0.0)
    fps = as_float(probe.get("fps"), 30.0)
    has_audio = bool(probe.get("has_audio", False))

    if duration <= 0:
        log("WARNING: Video duration is unavailable; skipping temporal edit.")
        return 0

    scene_cuts = detect_scene_cuts(duration)
    events = build_temporal_events(
        words,
        duration,
        scene_cuts,
        edit_energy,
    )
    media_segments = build_media_segments(events, duration)
    renderable_events = [
        event
        for event in events
        if not event.get("skipped_reason")
    ]

    if not renderable_events:
        log("No safe temporal events selected; leaving video unchanged.")
        write_json(
            PLAN_PATH,
            {
                "version": 1,
                "edit_energy": edit_energy,
                "applied": False,
                "reason": "no_safe_events",
                "source_duration_seconds": round(duration, 4),
                "scene_cuts": [round(cut, 3) for cut in scene_cuts],
                "events": events,
                "time_mapping": media_segments,
            },
        )
        return 0

    annotated_events = annotate_events_with_output_times(
        renderable_events,
        media_segments,
    )
    final_duration = as_float(media_segments[-1].get("output_end"), duration)

    plan = {
        "version": 1,
        "edit_energy": edit_energy,
        "applied": True,
        "source_video": str(VIDEO_PATH),
        "source_duration_seconds": round(duration, 4),
        "estimated_final_duration_seconds": round(final_duration, 4),
        "duration_delta_seconds": round(final_duration - duration, 4),
        "fps": round(fps, 6),
        "has_audio": has_audio,
        "audio_strategy": "pitch_preserving_atempo_for_speed_segments",
        "dialogue_protection": [
            "speed-up, speed-ramp, and whip events are selected from word gaps",
            "events crossing scene cuts are rejected",
        ],
        "scene_cuts": [round(cut, 3) for cut in scene_cuts],
        "event_count": len(annotated_events),
        "events": annotated_events,
        "time_mapping": media_segments,
    }

    write_json(PLAN_PATH, plan)

    log(f"Temporal events selected: {len(annotated_events)}")
    for index, event in enumerate(annotated_events, start=1):
        log(
            "Temporal "
            f"{index}: {event.get('type')} "
            f"{event.get('output_start', 0):.2f}s -> "
            f"{event.get('output_end', 0):.2f}s, "
            f"trigger={event.get('trigger_word', '')}, "
            f"reason={event.get('reason', '')}"
        )

    try:
        render_temporal_video(
            media_segments,
            fps,
            has_audio,
            final_duration,
        )
    except subprocess.CalledProcessError as exc:
        if TEMP_PATH.exists():
            try:
                TEMP_PATH.unlink()
            except OSError:
                pass

        plan["applied"] = False
        plan["failed_reason"] = f"ffmpeg_exit_{exc.returncode}"
        write_json(PLAN_PATH, plan)
        log(
            "WARNING: Temporal edit FFmpeg pass failed "
            f"with exit code {exc.returncode}. Continuing without it."
        )
        return 0
    except RuntimeError as exc:
        if TEMP_PATH.exists():
            try:
                TEMP_PATH.unlink()
            except OSError:
                pass

        plan["applied"] = False
        plan["failed_reason"] = str(exc)
        write_json(PLAN_PATH, plan)
        log(
            f"WARNING: {exc} Continuing without temporal editing."
        )
        return 0

    remapped = remap_transcript(transcript, media_segments)
    write_json(TRANSCRIPT_PATH, remapped)

    log(f"Temporal edit plan: {PLAN_PATH}")
    log(f"Remapped transcript: {TRANSCRIPT_PATH}")
    log("Temporal edit applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
