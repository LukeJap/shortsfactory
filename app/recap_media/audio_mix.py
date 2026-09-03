"""
B5 -- audio ducking/mixing plan. Consumes an assembled sequence
(recap_media.sequence.assemble_sequence()/interweave_original_dialogue())
and computes real piecewise-linear gain envelopes for the narration and
source-video audio tracks across the recap's OUTPUT timeline (cumulative
shot durations in cut order -- not each shot's original source-video
timestamp, since that's the timeline the two tracks actually get mixed
on): narration foreground + source ducked during narration-bearing
shots, narration silent + source restored during original-dialogue
shots. Explicitly real ramps (duck attack/release), not the hard
if(between(...)) step music_overlay.py's existing (simpler) music-under-
SFX ducking uses -- see keyframes_to_volume_expression().

This module stops at planning + a reusable two-track mix filter_complex
fragment (matching the amix/alimiter convention already established by
music_overlay.py and sfx_engine.py's SFX mixing). Assembling the actual
multi-file ffmpeg command (concatenating narration WAVs, syncing against
the assembled video, real input file indices) is final-render
integration (B9), not this step -- there's no cut video/audio timeline
to attach real input labels to yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline_paths import RECAP_AUDIO_DUCK_PLAN_PATH
from recap_media.loader import RecapInputError

AUDIO_DUCK_PLAN_SCHEMA_VERSION = 1

# Tunable ranges from SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR.md's B5.
# Legacy callers can still specify a linear voiceover gain in this range.
# The Recap render path now uses a dB-normalized narration gain instead so
# the final limiter, rather than a <= 1.0 planning clamp, protects the mix.
VOICEOVER_GAIN_RANGE = (0.85, 1.00)
NARRATION_GAIN_DB_RANGE = (-12.0, 12.0)
SOURCE_DUCKED_GAIN_RANGE = (0.0, 0.30)
DUCK_ATTACK_SECONDS_RANGE = (0.10, 0.20)
DUCK_RELEASE_SECONDS_RANGE = (0.10, 0.25)

DEFAULT_VOICEOVER_GAIN = 0.95
DEFAULT_NARRATION_GAIN_DB = 4.0
DEFAULT_SOURCE_DUCKED_GAIN = 0.0
DEFAULT_SOURCE_RESTORED_GAIN = 1.0
DEFAULT_DUCK_ATTACK_SECONDS = 0.15
DEFAULT_DUCK_RELEASE_SECONDS = 0.175

# Worst-case simultaneous gain (default 0.95 narration + 0.20 ducked
# source = 1.15) can exceed unity even though each track's own gain is
# individually <=1.0 -- matches sfx_engine.py's existing final-mix
# limiter exactly, the established fix for this same class of problem.
LIMITER_LIMIT = 0.92

# Which per-shot "treatment" values (recap_media.sequence) count as
# narration actively playing (source ducks) vs. original dialogue/no
# narration at all (source plays at its restored level). Exhaustive
# across the four known treatment values.
NARRATION_FOREGROUND_TREATMENTS = {"narration_over_source", "reaction_beat"}
SOURCE_RESTORED_TREATMENTS = {"original_dialogue", "visual_only"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def narration_gain_from_db(gain_db: float) -> float:
    """Return a limiter-safe linear narration gain for a user-facing dB value."""

    try:
        normalized_db = _clamp(float(gain_db), *NARRATION_GAIN_DB_RANGE)
    except (TypeError, ValueError):
        normalized_db = DEFAULT_NARRATION_GAIN_DB
    return 10 ** (normalized_db / 20.0)


def shot_output_windows(sequence: dict[str, Any]) -> list[tuple[float, float, str]]:
    """
    Walk every shot across all segments in order, returning
    (output_start, output_end, treatment) -- each shot's position on the
    OUTPUT recap timeline (cumulative timeline durations in cut order), not
    its source-video timestamp.
    """

    windows: list[tuple[float, float, str]] = []
    cursor = 0.0

    for segment in sorted(sequence["segments"], key=lambda s: s["order"]):
        for shot in segment["shots"]:
            duration = float(shot.get("timeline_duration_seconds", shot["duration"]))
            treatment = shot.get("treatment", segment["presentation_hint"])
            windows.append((cursor, cursor + duration, treatment))
            cursor += duration

    return windows


def build_gain_keyframes(
    windows: list[tuple[float, float, str]],
    high_treatments: set[str],
    high_gain: float,
    low_gain: float,
    attack_seconds: float,
    release_seconds: float,
) -> list[tuple[float, float]]:
    """
    Collapse consecutive shots into "high" (foreground-gain) and "low"
    (ducked-gain) runs, then return [(time, gain), ...] keyframes tracing
    a piecewise-linear envelope between them. Transitioning into a low
    run uses attack timing (the conventional "how fast a duck engages"),
    transitioning into a high run uses release timing ("how fast it
    recovers") -- this is symmetric for both the narration and source
    tracks; only which treatments count as "high" and which gain values
    are passed in differs between the two callers.
    """

    if not windows:
        return [(0.0, high_gain)]

    runs: list[list[Any]] = []
    for start, end, treatment in windows:
        is_high = treatment in high_treatments
        if runs and runs[-1][2] == is_high:
            runs[-1][1] = end
        else:
            runs.append([start, end, is_high])

    keyframes: list[tuple[float, float]] = [
        (0.0, high_gain if runs[0][2] else low_gain)
    ]

    for index in range(1, len(runs)):
        transition_time, run_end, curr_is_high = runs[index]
        prev_is_high = runs[index - 1][2]

        from_gain = high_gain if prev_is_high else low_gain
        to_gain = high_gain if curr_is_high else low_gain

        ramp_seconds = release_seconds if curr_is_high else attack_seconds
        ramp_end = min(transition_time + ramp_seconds, run_end)

        keyframes.append((transition_time, from_gain))
        keyframes.append((ramp_end, to_gain))

    total_duration = runs[-1][1]
    if total_duration > keyframes[-1][0]:
        keyframes.append((total_duration, keyframes[-1][1]))

    return keyframes


def build_duck_plan(
    sequence: dict[str, Any],
    voiceover_gain: float | None = None,
    narration_gain_db: float = DEFAULT_NARRATION_GAIN_DB,
    source_ducked_gain: float = DEFAULT_SOURCE_DUCKED_GAIN,
    source_restored_gain: float = DEFAULT_SOURCE_RESTORED_GAIN,
    attack_seconds: float = DEFAULT_DUCK_ATTACK_SECONDS,
    release_seconds: float = DEFAULT_DUCK_RELEASE_SECONDS,
) -> dict[str, Any]:
    """
    Build the full ducking plan: narration and source gain-keyframe
    envelopes across the sequence's output timeline, plus the gain/timing
    settings used (kept in the plan so a GUI can display/tune them and so
    a re-render can reproduce the exact same envelope).
    """

    # Explicit linear gain is retained for legacy integrations/tests. New
    # Recap renders use a +4 dB default that is protected by the final mix
    # limiter below, so narration can be meaningfully louder than unity.
    try:
        narration_gain_db = _clamp(float(narration_gain_db), *NARRATION_GAIN_DB_RANGE)
    except (TypeError, ValueError):
        narration_gain_db = DEFAULT_NARRATION_GAIN_DB
    if voiceover_gain is None:
        voiceover_gain = narration_gain_from_db(narration_gain_db)
    else:
        voiceover_gain = _clamp(float(voiceover_gain), 0.0, 1.0)
    voiceover_gain = round(voiceover_gain, 4)
    source_ducked_gain = _clamp(source_ducked_gain, 0.0, 1.0)
    source_restored_gain = _clamp(source_restored_gain, 0.0, 1.0)
    attack_seconds = max(0.0, attack_seconds)
    release_seconds = max(0.0, release_seconds)

    windows = shot_output_windows(sequence)

    narration_keyframes = build_gain_keyframes(
        windows,
        NARRATION_FOREGROUND_TREATMENTS,
        high_gain=voiceover_gain,
        low_gain=0.0,  # "narration stops" during original dialogue -- true silence, not just quiet
        # Source-audio inserts are a hard handoff, not a conventional duck:
        # even a 150ms fade would leave competing narration under dialogue.
        attack_seconds=0.0,
        release_seconds=0.0,
    )

    source_keyframes = build_gain_keyframes(
        windows,
        SOURCE_RESTORED_TREATMENTS,
        high_gain=source_restored_gain,
        low_gain=source_ducked_gain,
        # A recap's source track is intentionally silent under narration.
        # Do not let a release/attack ramp leak source dialogue beneath VO.
        attack_seconds=0.0,
        release_seconds=0.0,
    )

    total_duration = windows[-1][1] if windows else 0.0

    return {
        "schema_version": AUDIO_DUCK_PLAN_SCHEMA_VERSION,
        "total_duration_seconds": round(total_duration, 3),
        "settings": {
            "voiceover_gain": voiceover_gain,
            "narration_gain_db": narration_gain_db,
            "source_ducked_gain": source_ducked_gain,
            "source_restored_gain": source_restored_gain,
            "attack_seconds": attack_seconds,
            "release_seconds": release_seconds,
            "limiter_limit": LIMITER_LIMIT,
        },
        "narration_keyframes": [[round(t, 3), round(g, 4)] for t, g in narration_keyframes],
        "source_keyframes": [[round(t, 3), round(g, 4)] for t, g in source_keyframes],
    }


def keyframes_to_volume_expression(keyframes: list[tuple[float, float]] | list[list[float]]) -> str:
    """
    Convert [(time, gain), ...] keyframes into an ffmpeg `volume` filter
    time-varying expression (use with volume='<expr>':eval=frame) that
    linearly interpolates between consecutive keyframes -- a genuine
    ramp, not a hard step. Holds the first gain before the first
    keyframe and the last gain past the last keyframe.
    """

    if not keyframes:
        return "1.0"

    if len(keyframes) == 1:
        return f"{keyframes[0][1]:.4f}"

    expression = f"{keyframes[-1][1]:.4f}"

    for index in range(len(keyframes) - 2, -1, -1):
        t0, g0 = keyframes[index]
        t1, g1 = keyframes[index + 1]
        if t1 <= t0:
            continue  # degenerate/zero-length segment -- nothing to interpolate

        lerp = f"({g0:.4f}+({g1:.4f}-{g0:.4f})*(t-{t0:.3f})/{(t1 - t0):.3f})"
        expression = f"if(between(t,{t0:.3f},{t1:.3f}),{lerp},{expression})"

    first_time = keyframes[0][0]
    expression = f"if(lt(t,{first_time:.3f}),{keyframes[0][1]:.4f},{expression})"

    return expression


def build_duck_filter_complex(
    plan: dict[str, Any],
    narration_label: str = "[0:a]",
    source_label: str = "[1:a]",
    output_label: str = "[mixed]",
) -> str:
    """
    The reusable two-track mix fragment: narration and source each get
    their own time-varying volume envelope, then amix + alimiter (same
    normalize=0/alimiter=limit convention already used by
    music_overlay.py and sfx_engine.py) guards against the two tracks'
    gains summing past unity. Caller supplies real input labels once
    actual narration/source audio inputs exist (final render
    integration, B9) -- this only builds the filter graph fragment.
    """

    narration_expression = keyframes_to_volume_expression(plan["narration_keyframes"])
    source_expression = keyframes_to_volume_expression(plan["source_keyframes"])
    limiter_limit = plan.get("settings", {}).get("limiter_limit", LIMITER_LIMIT)

    return (
        f"{narration_label}volume='{narration_expression}':eval=frame[narration_gained];"
        f"{source_label}volume='{source_expression}':eval=frame[source_gained];"
        "[narration_gained][source_gained]"
        "amix="
        "inputs=2:"
        "duration=longest:"
        "dropout_transition=0:"
        "normalize=0,"
        f"alimiter=limit={limiter_limit}"
        f"{output_label}"
    )


def write_duck_plan(
    plan: dict[str, Any],
    path: Path = RECAP_AUDIO_DUCK_PLAN_PATH,
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def load_duck_plan(path: Path = RECAP_AUDIO_DUCK_PLAN_PATH) -> dict[str, Any]:
    """
    Read back a previously-written audio_duck_plan.json. Track B's own
    output (same reasoning as recap_media.sequence.load_recap_sequence())
    -- a light structural check, not full re-validation, but still raises
    RecapInputError so callers only need to catch one exception type
    across every recap file.
    """

    if not path.exists():
        raise RecapInputError(f"audio_duck_plan.json not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecapInputError(f"audio_duck_plan.json is not valid JSON ({path}): {exc}") from exc

    if not isinstance(data, dict) or "narration_keyframes" not in data:
        raise RecapInputError(
            f"audio_duck_plan.json is missing required field 'narration_keyframes' ({path})"
        )

    return data
