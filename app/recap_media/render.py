"""
B9 -- final render integration. Combines everything B1-B8 produced
(recap_sequence.json's shot list, per-segment voiceover WAVs, the audio
duck plan, narration captions, portrait framing) into one real ffmpeg
render producing output/recap/final_recap.mp4.

"AI proposes -> user sees it -> user edits it -> manual edit is
authoritative -> render matches editor" (shared contract): this reads
editor_asset_plan.json's VOICEOVER clips exactly as last written by the
GUI (B8) for the audio track and captions -- a disabled/deleted clip is
silent and captionless here, not silently overridden back in. It never
recomputes shot selection itself; recap_sequence.json's shots (B3/B4)
are used exactly as last generated.

One combined ffmpeg pass, not several full re-encodes: video trim+concat,
portrait framing, and caption burn-in all happen in one filter_complex;
audio trim+concat (the original source audio), narration WAV placement,
and duck mixing happen in that same pass's audio side. Matches this
codebase's established "merge passes, don't add re-encode stages"
principle (see motion_and_fx.py's docstring for the same rationale
elsewhere in ShortsFactory) -- normal (non-recap) rendering (render.py)
is not touched by anything in this module.

Known limitation: the video track's timeline (recap_sequence.json's
shot durations) and the audio/caption track's timeline (editor_asset_
plan.json's VOICEOVER clip positions) are computed independently and can
drift apart if voiceover durations changed after the sequence was last
assembled -- re-run "Generate Sequence" after "Generate Voiceover" for
the tightest sync (see recap_media.sequence's docstring for the same
caveat from the other direction).
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

from base_video_polish import PRODUCTION_POLISH_PRESET, polish_filters
from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
from emoji_overlay import (
    build_emoji_filter_complex,
    emoji_input_arguments,
)
from pipeline_paths import RECAP_FINAL_OUTPUT_PATH, ROOT
from recap_media.audio_mix import build_duck_filter_complex
from recap_media.caption_alignment import load_narration_captions
from recap_media.effects import (
    RecapEffectsError,
    create_recap_effects_plan,
    load_recap_effects,
    recap_effects_for_render,
)
from recap_media.portrait_framing import build_portrait_filter_chain
from recap_media.timeline import RECAP_PLAYBACK_SPEED, recap_final_duration_seconds
from recap_media.voiceover import wav_path_for_segment
from smart_motion import x_expression, y_expression, zoom_expression
from sfx_engine import build_sfx_mix_filter_complex
from visual_fx import build_semantic_filter_chain
from music_overlay import music_volume_expression


class RecapRenderError(Exception):
    """The recap render command could not be built or failed to run."""


DEFAULT_RECAP_TIMELINE_FPS = 24000 / 1001
DEFAULT_NARRATION_PITCH_SEMITONES = 1.8
DEFAULT_SOURCE_PITCH_SEMITONES = 1.8
NARRATION_PITCH_SEMITONES_RANGE = (-3.0, 4.0)
MAX_RENDERABLE_VISUAL_SHORTFALL_SECONDS = 0.15
SOURCE_AUDIO_EDGE_FADE_SECONDS = 0.012


def _validated_playback_speed(playback_speed: float) -> float:
    try:
        speed = float(playback_speed)
    except (TypeError, ValueError) as exc:
        raise RecapRenderError(f"Invalid recap playback speed: {playback_speed!r}") from exc
    if not 0.5 <= speed <= 2.0:
        raise RecapRenderError("Recap playback speed must be between 0.5x and 2.0x.")
    return speed


def _validated_pitch_semitones(pitch_semitones: float, stream_name: str) -> float:
    try:
        semitones = float(pitch_semitones)
    except (TypeError, ValueError) as exc:
        raise RecapRenderError(
            f"Invalid {stream_name.lower()} pitch semitones: {pitch_semitones!r}"
        ) from exc
    low, high = NARRATION_PITCH_SEMITONES_RANGE
    if not low <= semitones <= high:
        raise RecapRenderError(
            f"{stream_name} pitch must be between {low:.1f} and {high:.1f} semitones."
        )
    return semitones


def _pitch_ratio(pitch_semitones: float, stream_name: str) -> float:
    """Frequency ratio for a semitone setting after range validation."""

    semitones = _validated_pitch_semitones(pitch_semitones, stream_name)
    return 2 ** (semitones / 12.0)


def narration_pitch_ratio(narration_pitch_semitones: float) -> float:
    return _pitch_ratio(narration_pitch_semitones, "Narration")


def source_pitch_ratio(source_pitch_semitones: float) -> float:
    return _pitch_ratio(source_pitch_semitones, "Source")


def _build_duration_preserving_pitch_filter(pitch_semitones: float, stream_name: str) -> str:
    """Build a duration-preserving pitch transform for one recap audio branch.

    The project's verified FFmpeg build provides ``rubberband``. An explicit
    tempo=1.000 keeps each branch's duration fixed, leaving the accepted
    shared playback-speed stage as the only timeline transform.
    """

    semitones = _validated_pitch_semitones(pitch_semitones, stream_name)
    if math.isclose(semitones, 0.0, abs_tol=1e-9):
        return ""
    return (
        f"rubberband=pitch={_pitch_ratio(semitones, stream_name):.6f}:tempo=1.000:"
        "formant=preserved:pitchq=quality"
    )


def build_narration_pitch_filter(narration_pitch_semitones: float) -> str:
    return _build_duration_preserving_pitch_filter(narration_pitch_semitones, "Narration")


def build_source_pitch_filter(source_pitch_semitones: float) -> str:
    return _build_duration_preserving_pitch_filter(source_pitch_semitones, "Source")


def final_recap_duration_seconds(
    base_duration_seconds: float,
    playback_speed: float = RECAP_PLAYBACK_SPEED,
) -> float:
    """Output duration after the final composite speed transform."""

    return recap_final_duration_seconds(
        base_duration_seconds,
        _validated_playback_speed(playback_speed),
    )


def recap_timeline_fps(source_video: Path) -> float:
    """Return the assembled source timeline's frame rate for smart motion.

    ``zoompan`` with ``d=1`` emits one output frame per assembled input
    frame. Its output FPS therefore must match the source timeline or video
    runs independently of the source-audio concat. A conservative broadcast
    default preserves the established behavior when probing is unavailable.
    """

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source_video),
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        numerator, denominator = result.stdout.strip().split("/", 1)
        fps = float(numerator) / float(denominator)
    except (OSError, subprocess.SubprocessError, ValueError, ZeroDivisionError):
        return DEFAULT_RECAP_TIMELINE_FPS
    return fps if math.isfinite(fps) and 1.0 <= fps <= 120.0 else DEFAULT_RECAP_TIMELINE_FPS


def build_recap_motion_filter(
    motion_events: list[dict[str, Any]],
    *,
    input_label: str = "recap_out",
    output_label: str = "recap_motion",
    timeline_fps: float = DEFAULT_RECAP_TIMELINE_FPS,
) -> str:
    """Adapt the shared smart-motion expressions to the assembled recap.

    This does not create a second motion implementation. It only gives the
    existing shared expressions the same frame rate as the N_/S_ concat they
    consume, which keeps source video and source audio on one timeline.
    """

    fps = float(timeline_fps)
    if not math.isfinite(fps) or not 1.0 <= fps <= 120.0:
        fps = DEFAULT_RECAP_TIMELINE_FPS
    return (
        f"[{input_label}]zoompan="
        f"z='{zoom_expression(motion_events, fps)}':"
        f"x='{x_expression(motion_events, fps)}':"
        f"y='{y_expression(motion_events, fps)}':"
        f"d=1:s=1080x1920:fps={fps:.6f}[{output_label}]"
    )


def escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a file path embedded in an FFmpeg filtergraph option value."""

    normalized = str(path).replace("\\", "/")

    # First escape the subtitles filter's option value. Then escape that
    # result for the surrounding filtergraph, which consumes one layer before
    # the subtitles filter parses its own colon-delimited options.
    option_value = (
        normalized.replace("\\", r"\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
    )
    return (
        option_value.replace("\\", r"\\")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace(";", r"\;")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def resolve_recap_source_video(
    episode_identity: dict[str, Any],
    input_dir: Path | None = None,
) -> Path:
    """Resolve the source named by accepted recap identity provenance.

    A recap must never borrow the global editor-plan source, which may point
    to another short or episode. Track A's identity query records the original
    source filename; resolving that exact filename below the project input
    directory is the narrow, auditable bridge to Track B's renderer.
    """

    query = episode_identity.get("query")
    if not isinstance(query, dict):
        raise RecapRenderError("Recap episode identity has no source query provenance.")
    source_filename = query.get("source_filename")
    if not isinstance(source_filename, str) or not source_filename.strip():
        raise RecapRenderError("Recap episode identity has no source_filename provenance.")

    source_name = Path(source_filename)
    if source_name.is_absolute() or source_name.name != source_filename:
        raise RecapRenderError("Recap source_filename must be a single input filename.")

    root = (input_dir or ROOT / "input").resolve()
    source_path = (root / source_name).resolve()
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise RecapRenderError("Recap source path escapes the project input directory.") from exc
    if not source_path.is_file():
        raise RecapRenderError(
            f"Recap source from episode identity was not found: {source_path}"
        )
    return source_path


def active_voiceover_clips_in_order(voiceover_clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enabled, non-deleted VOICEOVER clips, ordered by their position on
    the output timeline -- "render matches editor": whatever's disabled/
    deleted in the GUI is silent and captionless here, not just dimmed."""

    active = [
        clip
        for clip in voiceover_clips
        if clip.get("active", True) and not clip.get("deleted")
    ]
    return sorted(active, key=lambda clip: float(clip.get("start", 0.0) or 0.0))


def build_video_track_filter(
    shots: list[dict[str, Any]],
    video_label: str = "0:v",
    output_label: str = "vconcat",
) -> tuple[str, str]:
    """
    Reconstruct the recap's video track from recap_sequence.json's shots
    (each carrying its own source-video start/end) via trim+setpts+concat,
    in cut order. Returns (filter_fragment, output_label).
    """

    if not shots:
        raise RecapRenderError("Cannot build a video track from an empty shot list.")

    parts = []
    labels = []

    for index, shot in enumerate(shots):
        label = f"vshot{index}"
        parts.append(
            f"[{video_label}]trim=start={float(shot['start']):.3f}:"
            f"end={float(shot['end']):.3f},setpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")

    parts.append("".join(labels) + f"concat=n={len(shots)}:v=1:a=0[{output_label}]")
    return ";".join(parts), output_label


def build_source_audio_track_filter(
    shots: list[dict[str, Any]],
    audio_label: str = "0:a",
    output_label: str = "asource",
    source_pitch_semitones: float = DEFAULT_SOURCE_PITCH_SEMITONES,
) -> tuple[str, str]:
    """Same trim+concat reconstruction as build_video_track_filter(), for
    the source video's own audio track (atrim/asetpts/concat a=1)."""

    if not shots:
        raise RecapRenderError("Cannot build a source audio track from an empty shot list.")

    pitch_filter = build_source_pitch_filter(source_pitch_semitones)
    parts = []
    labels = []

    for index, shot in enumerate(shots):
        label = f"ashot{index}"
        filter_chain = (
            f"[{audio_label}]atrim=start={float(shot['start']):.3f}:"
            f"end={float(shot['end']):.3f},asetpts=PTS-STARTPTS"
        )
        if shot.get("treatment") == "original_dialogue":
            duration = max(0.0, float(shot["end"]) - float(shot["start"]))
            fade_seconds = min(SOURCE_AUDIO_EDGE_FADE_SECONDS, duration / 2.0)
            if fade_seconds > 0:
                filter_chain += (
                    f",afade=t=in:st=0:d={fade_seconds:.3f}"
                    f",afade=t=out:st={duration - fade_seconds:.3f}:d={fade_seconds:.3f}"
                )
        parts.append(f"{filter_chain}[{label}]")
        labels.append(f"[{label}]")

    parts.append("".join(labels) + f"concat=n={len(shots)}:v=0:a=1[{output_label}]")
    if not pitch_filter:
        return ";".join(parts), output_label

    pitched_output_label = f"{output_label}_pitched"
    parts.append(f"[{output_label}]{pitch_filter}[{pitched_output_label}]")
    return ";".join(parts), pitched_output_label


def build_narration_track_filter(
    active_clips: list[dict[str, Any]],
    input_index_by_clip_id: dict[str, int],
    output_label: str = "anarration",
    narration_pitch_semitones: float = DEFAULT_NARRATION_PITCH_SEMITONES,
) -> tuple[str, str]:
    """
    Position each active VOICEOVER clip's WAV (already a separate ffmpeg
    input -- see input_index_by_clip_id) at its cumulative output-
    timeline start via adelay, applying that clip's own volume, then
    combine with amix into one continuous narration track. Requires at
    least one active clip -- callers with none should skip the whole
    narration/duck-mix path rather than call this (see render_recap()).
    """

    if not active_clips:
        raise RecapRenderError(
            "Cannot build a narration track with zero active VOICEOVER clips."
        )

    pitch_filter = build_narration_pitch_filter(narration_pitch_semitones)
    parts = []
    labels = []

    def dialogue_pauses(clip: dict[str, Any]) -> list[tuple[float, float]]:
        pauses: list[tuple[float, float]] = []
        for pause in clip.get("dialogue_pauses", []) or []:
            if not isinstance(pause, dict):
                continue
            try:
                offset = max(0.0, float(pause["narration_offset_seconds"]))
                duration = float(pause["duration_seconds"])
            except (KeyError, TypeError, ValueError):
                continue
            if duration > 0:
                pauses.append((offset, duration))
        return sorted(pauses)

    for clip in active_clips:
        clip_id = clip["id"]
        if clip_id not in input_index_by_clip_id:
            raise RecapRenderError(f"No ffmpeg input registered for VOICEOVER clip {clip_id!r}.")

        input_index = input_index_by_clip_id[clip_id]
        input_audio_label = f"{input_index}:a"
        if pitch_filter:
            input_audio_label = f"narr_pitch_{input_index}"
            parts.append(f"[{input_index}:a]{pitch_filter}[{input_audio_label}]")
        delay_ms = max(0, round(float(clip.get("start", 0.0) or 0.0) * 1000))
        try:
            volume = max(0.0, min(1.0, float(clip.get("volume", 1.0) or 1.0)))
        except (TypeError, ValueError):
            volume = 1.0

        pauses = dialogue_pauses(clip)
        if not pauses:
            label = f"narr{input_index}"
            parts.append(
                f"[{input_audio_label}]volume={volume:.3f},"
                f"adelay={delay_ms}|{delay_ms}[{label}]"
            )
            labels.append(f"[{label}]")
            continue

        narration_cursor = 0.0
        added_pause_duration = 0.0
        for fragment_index, (pause_offset, pause_duration) in enumerate(pauses):
            if pause_offset > narration_cursor:
                label = f"narr{input_index}_{fragment_index}"
                fragment_delay_ms = max(
                    0,
                    round((float(clip.get("start", 0.0) or 0.0) + narration_cursor + added_pause_duration) * 1000),
                )
                parts.append(
                    f"[{input_audio_label}]atrim=start={narration_cursor:.3f}:end={pause_offset:.3f},"
                    f"asetpts=PTS-STARTPTS,volume={volume:.3f},"
                    f"adelay={fragment_delay_ms}|{fragment_delay_ms}[{label}]"
                )
                labels.append(f"[{label}]")
            narration_cursor = max(narration_cursor, pause_offset)
            added_pause_duration += pause_duration

        label = f"narr{input_index}_{len(pauses)}"
        fragment_delay_ms = max(
            0,
            round((float(clip.get("start", 0.0) or 0.0) + narration_cursor + added_pause_duration) * 1000),
        )
        parts.append(
            f"[{input_audio_label}]atrim=start={narration_cursor:.3f},asetpts=PTS-STARTPTS,"
            f"volume={volume:.3f},adelay={fragment_delay_ms}|{fragment_delay_ms}[{label}]"
        )
        labels.append(f"[{label}]")

    parts.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:"
        f"normalize=0[{output_label}]"
    )
    return ";".join(parts), output_label


def build_recap_filter_complex(
    sequence: dict[str, Any],
    active_voiceover_clips: list[dict[str, Any]],
    input_index_by_clip_id: dict[str, int],
    portrait_plan: dict[str, Any],
    duck_plan: dict[str, Any],
    captions_ass_path: Path | None = None,
    playback_speed: float = RECAP_PLAYBACK_SPEED,
    recap_effects: dict[str, Any] | None = None,
    narration_pitch_semitones: float = DEFAULT_NARRATION_PITCH_SEMITONES,
    source_pitch_semitones: float = DEFAULT_SOURCE_PITCH_SEMITONES,
    timeline_fps: float = DEFAULT_RECAP_TIMELINE_FPS,
) -> tuple[str, str, str]:
    """
    Assemble the complete filter_complex for one recap render pass.
    Returns (filter_complex, final_video_label, final_audio_label).
    """

    shots = [shot for segment in sequence["segments"] for shot in segment["shots"]]

    video_filter, video_label = build_video_track_filter(shots)
    source_audio_filter, source_audio_label = build_source_audio_track_filter(
        shots,
        source_pitch_semitones=source_pitch_semitones,
    )
    narration_filter, narration_label = build_narration_track_filter(
        active_voiceover_clips,
        input_index_by_clip_id,
        narration_pitch_semitones=narration_pitch_semitones,
    )

    pillarbox_detection = portrait_plan.get("pillarbox_detection")
    active_rect = (
        portrait_plan.get("active_rect")
        if isinstance(pillarbox_detection, dict)
        and pillarbox_detection.get("pillarbox_detected") is True
        else None
    )
    portrait_filter = build_portrait_filter_chain(
        portrait_plan["content_x"],
        portrait_plan["content_y"],
        portrait_plan["content_width"],
        portrait_plan["content_height"],
        canvas_width=portrait_plan.get("canvas_width", OUTPUT_WIDTH),
        canvas_height=portrait_plan.get("canvas_height", OUTPUT_HEIGHT),
        blur_sigma=portrait_plan.get("blur_sigma", 25.0),
        background_dim=portrait_plan.get("background_dim", 0.0),
        input_label=video_label,
        active_rect=active_rect,
        # Use the exact Standard Short base treatment on the active source
        # picture before portrait framing splits it into foreground/background.
        pre_split_filters=polish_filters(PRODUCTION_POLISH_PRESET),
    )
    final_video_label = "recap_out"
    recap_effects = recap_effects_for_render(
        recap_effects or {},
        playback_speed=playback_speed,
    )
    visual_fx_events = recap_effects.get("visual_fx_events", [])
    if not isinstance(visual_fx_events, list):
        visual_fx_events = []
    sfx_events = recap_effects.get("sfx_events", [])
    if not isinstance(sfx_events, list):
        sfx_events = []
    emoji_events = recap_effects.get("emoji_events", [])
    if not isinstance(emoji_events, list):
        emoji_events = []

    motion_events = recap_effects.get("motion_events", [])
    if not isinstance(motion_events, list):
        motion_events = []

    motion_filter = None
    if motion_events:
        # The standard smart-motion expressions are intentionally reused on
        # the already-framed 1080x1920 recap canvas. This retains Recap
        # Mode's portrait composition while giving its semantic moments the
        # same punch-in/pan language as a regular Short.
        motion_filter = build_recap_motion_filter(
            motion_events,
            timeline_fps=timeline_fps,
        )
        final_video_label = "recap_motion"

    fx_filter = None
    if visual_fx_events:
        fx_filter = (
            f"[{final_video_label}]"
            f"{build_semantic_filter_chain(visual_fx_events)}"
            "[recap_fx]"
        )
        final_video_label = "recap_fx"

    emoji_filter = None
    if emoji_events:
        first_sfx_input_index = 1 + len(active_voiceover_clips)
        first_emoji_input_index = first_sfx_input_index + len(sfx_events)
        emoji_filter, final_video_label = build_emoji_filter_complex(
            emoji_events,
            f"[{final_video_label}]",
            first_input_index=first_emoji_input_index,
        )
        # The shared emoji helper returns a bracketed FFmpeg label while this
        # builder carries labels without brackets between stages.
        final_video_label = final_video_label.strip("[]")

    if captions_ass_path is not None:
        caption_filter = (
            f"[{final_video_label}]subtitles=filename={escape_ffmpeg_filter_path(captions_ass_path)}"
            "[recap_captioned]"
        )
        final_video_label = "recap_captioned"
    else:
        caption_filter = None

    duck_filter = build_duck_filter_complex(
        duck_plan,
        narration_label=f"[{narration_label}]",
        source_label=f"[{source_audio_label}]",
        output_label="[mixed]",
    )
    sfx_filter = None
    mixed_audio_label = "mixed"
    if sfx_events:
        sfx_filter = build_sfx_mix_filter_complex(
            "[mixed]",
            sfx_events,
            first_input_index=1 + len(active_voiceover_clips),
            output_label="[recap_mixed]",
        )
        mixed_audio_label = "recap_mixed"
    speed = _validated_playback_speed(playback_speed)
    speed_video_label = "recap_playback_video"
    speed_audio_label = "recap_playback_audio"
    playback_filter = (
        f"[{final_video_label}]setpts=PTS/{speed:.3f}[{speed_video_label}];"
        f"[{mixed_audio_label}]atempo={speed:.3f}[{speed_audio_label}]"
    )
    final_video_label = speed_video_label
    final_audio_label = speed_audio_label

    fragments = [video_filter, source_audio_filter, narration_filter, portrait_filter]
    if motion_filter is not None:
        fragments.append(motion_filter)
    if fx_filter is not None:
        fragments.append(fx_filter)
    if emoji_filter is not None:
        fragments.append(emoji_filter)
    if caption_filter is not None:
        fragments.append(caption_filter)
    fragments.append(duck_filter)
    if sfx_filter is not None:
        fragments.append(sfx_filter)
    fragments.append(playback_filter)

    return ";".join(fragments), final_video_label, final_audio_label


def build_recap_ffmpeg_command(
    source_video: Path,
    active_voiceover_clips: list[dict[str, Any]],
    filter_complex: str,
    final_video_label: str,
    final_audio_label: str,
    output_path: Path,
    total_duration_seconds: float | None = None,
    sfx_events: list[dict[str, Any]] | None = None,
    emoji_events: list[dict[str, Any]] | None = None,
    voiceover_dir: Path | None = None,
) -> list[str]:
    """
    Build the full ffmpeg CLI argument list: source video as input 0,
    each active VOICEOVER clip's WAV as one subsequent input (in the
    same order build_recap_filter_complex()'s input_index_by_clip_id
    used), the assembled filter_complex, and the final video+audio maps.
    ``total_duration_seconds`` is retained for call compatibility only. The
    final render deliberately has no global ``-t`` cap: a valid narration
    WAV is authoritative and must not be cut because an older sequence or
    rounded metadata window is fractionally shorter.
    """

    command = ["ffmpeg", "-y", "-i", str(source_video)]

    for clip in active_voiceover_clips:
        wav_path = (
            wav_path_for_segment(clip["id"], voiceover_dir)
            if voiceover_dir is not None
            else wav_path_for_segment(clip["id"])
        )
        command.extend(["-i", str(wav_path)])

    for event in sfx_events or []:
        command.extend(["-i", str(event["asset_path"])])
    command.extend(emoji_input_arguments(emoji_events or []))

    command.extend(["-filter_complex", filter_complex])
    command.extend(["-map", f"[{final_video_label}]", "-map", f"[{final_audio_label}]"])
    # The source episode can carry full-length chapter metadata. In MP4,
    # FFmpeg serializes those chapters as a timed data track, which makes a
    # short recap appear to run for the entire source episode. The recap's
    # filtered video and mixed audio are the only intentional output streams.
    command.extend(["-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn"])

    command.extend(
        [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output_path),
        ]
    )

    return command


def build_recap_editor_export_filter_complex(
    recap_effects: dict[str, Any],
    *,
    captions_ass_path: Path,
    timeline_fps: float,
    emoji_first_input_index: int | None = None,
    music_input_index: int | None = None,
    music_volume: float = 0.0,
) -> tuple[str, str, str]:
    """Apply current editor entities to an already assembled Recap base.

    The base is final-timeline 1080x1920 media. This deliberately avoids
    source trimming, portrait framing, speed changes, and production polish.
    """

    visual_fx_events = list(recap_effects.get("visual_fx_events", []) or [])
    motion_events = list(recap_effects.get("motion_events", []) or [])
    sfx_events = list(recap_effects.get("sfx_events", []) or [])
    emoji_events = list(recap_effects.get("emoji_events", []) or [])

    fragments: list[str] = []
    video_label = "0:v"
    if motion_events:
        fragments.append(
            build_recap_motion_filter(
                motion_events,
                input_label=video_label,
                output_label="recap_editor_motion",
                timeline_fps=timeline_fps,
            )
        )
        video_label = "recap_editor_motion"
    if visual_fx_events:
        fragments.append(
            f"[{video_label}]{build_semantic_filter_chain(visual_fx_events)}"
            "[recap_editor_fx]"
        )
        video_label = "recap_editor_fx"
    if emoji_events and emoji_first_input_index is not None:
        emoji_filter, emoji_label = build_emoji_filter_complex(
            emoji_events,
            f"[{video_label}]",
            first_input_index=emoji_first_input_index,
        )
        fragments.append(emoji_filter)
        video_label = emoji_label.strip("[]")
    fragments.append(
        f"[{video_label}]subtitles=filename={escape_ffmpeg_filter_path(captions_ass_path)}"
        "[recap_editor_captioned]"
    )
    video_label = "recap_editor_captioned"

    audio_label = "0:a"
    if sfx_events:
        fragments.append(
            build_sfx_mix_filter_complex(
                "[0:a]",
                sfx_events,
                first_input_index=1,
                output_label="[recap_editor_sfx]",
            )
        )
        audio_label = "recap_editor_sfx"
    if music_input_index is not None:
        gain = max(0.0, min(1.0, float(music_volume)))
        music_expression = music_volume_expression(gain, sfx_events)
        fragments.append(
            f"[{audio_label}]volume=1.0[recap_editor_program];"
            f"[{music_input_index}:a]volume='{music_expression}':eval=frame[recap_editor_music];"
            "[recap_editor_program][recap_editor_music]"
            "amix=inputs=2:duration=first:dropout_transition=2:normalize=0"
            "[recap_editor_audio]"
        )
        audio_label = "recap_editor_audio"

    return ";".join(fragments), video_label, audio_label


def bind_recap_editor_export_inputs(
    editor_base_path: Path,
    recap_effects: dict[str, Any],
    *,
    music_path: Path | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Append only usable optional inputs and record their actual indexes."""

    command = ["ffmpeg", "-y", "-i", str(editor_base_path)]
    next_index = 1
    sfx_events: list[dict[str, Any]] = []
    for event in recap_effects.get("sfx_events", []) or []:
        if not isinstance(event, dict):
            continue
        path = Path(str(event.get("asset_path", "")))
        if not path.is_file():
            print(f"WARNING: Recap SFX asset not found; skipping: {path}")
            continue
        bound = dict(event)
        bound["asset_path"] = str(path)
        bound["input_index"] = next_index
        sfx_events.append(bound)
        command.extend(["-i", str(path)])
        next_index += 1

    emoji_events: list[dict[str, Any]] = []
    for event in recap_effects.get("emoji_events", []) or []:
        if not isinstance(event, dict):
            continue
        path = _recap_local_emoji_path(event)
        if path is None:
            print(f"WARNING: Recap emoji asset unavailable; skipping: {event.get('emoji', '')}")
            continue
        bound = dict(event)
        bound["path"] = path
        bound["input_index"] = next_index
        emoji_events.append(bound)
        command.extend(emoji_input_arguments([bound]))
        next_index += 1

    music_input_index = None
    if music_path is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music_path)])
        music_input_index = next_index
        next_index += 1
    return command, {
        "sfx_events": sfx_events,
        "emoji_events": emoji_events,
        "emoji_first_input_index": emoji_events[0]["input_index"] if emoji_events else None,
        "music_input_index": music_input_index,
        "input_count": next_index,
    }


def _recap_local_emoji_path(event: dict[str, Any]) -> Path | None:
    """Return an already-resolved local Recap emoji asset without network fallback."""

    for field in ("path", "asset_path", "asset"):
        value = event.get(field)
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            return path
    return None


def build_recap_editor_export_command(
    input_arguments: list[str],
    filter_complex: str,
    video_label: str,
    audio_label: str,
    output_path: Path,
) -> list[str]:
    command = list(input_arguments)
    command.extend(["-filter_complex", filter_complex, "-map", f"[{video_label}]"])
    command.extend(["-map", f"[{audio_label}]" if ":" not in audio_label else audio_label])
    command.extend(["-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn"])
    command.extend([
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-movflags", "+faststart", str(output_path),
    ])
    return command


def render_recap_editor_export(
    editor_base_path: Path,
    recap_effects: dict[str, Any],
    captions_ass_path: Path,
    output_path: Path,
    *,
    music_path: Path | None = None,
    music_volume: float = 0.0,
) -> Path:
    """Render one AI Recap export from editor state without any replanning."""

    if not editor_base_path.is_file() or editor_base_path.stat().st_size <= 0:
        raise RecapRenderError(f"Clean Recap editor base not found: {editor_base_path}")
    if not captions_ass_path.is_file():
        raise RecapRenderError(f"Combined Recap captions not found: {captions_ass_path}")
    if music_path is not None and not music_path.is_file():
        raise RecapRenderError(f"Background music not found: {music_path}")
    input_arguments, bindings = bind_recap_editor_export_inputs(
        editor_base_path,
        recap_effects,
        music_path=music_path,
    )
    effects = dict(recap_effects)
    effects["sfx_events"] = bindings["sfx_events"]
    effects["emoji_events"] = bindings["emoji_events"]
    fps = recap_timeline_fps(editor_base_path)
    filters, video_label, audio_label = build_recap_editor_export_filter_complex(
        effects,
        captions_ass_path=captions_ass_path,
        timeline_fps=fps,
        emoji_first_input_index=bindings["emoji_first_input_index"],
        music_input_index=bindings["music_input_index"],
        music_volume=music_volume,
    )
    command = build_recap_editor_export_command(
        input_arguments,
        filters,
        video_label,
        audio_label,
        output_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RecapRenderError(f"Recap editor export failed with exit code {result.returncode}:\n{result.stderr[-2000:]}")
    return output_path


def input_index_for_voiceover_clips(
    active_voiceover_clips: list[dict[str, Any]],
    first_index: int = 1,
) -> dict[str, int]:
    """{clip_id: ffmpeg input index}, in the same order those clips will
    be passed as -i arguments (build_recap_ffmpeg_command())."""

    return {
        clip["id"]: first_index + position
        for position, clip in enumerate(active_voiceover_clips)
    }


def render_recap(
    episode_identity: dict[str, Any],
    sequence: dict[str, Any],
    voiceover_clips: list[dict[str, Any]],
    portrait_plan: dict[str, Any],
    duck_plan: dict[str, Any],
    captions_ass_path: Path | None = None,
    output_path: Path = RECAP_FINAL_OUTPUT_PATH,
    playback_speed: float = RECAP_PLAYBACK_SPEED,
    recap_effects: dict[str, Any] | None = None,
    voiceover_dir: Path | None = None,
    narration_pitch_semitones: float = DEFAULT_NARRATION_PITCH_SEMITONES,
    source_pitch_semitones: float = DEFAULT_SOURCE_PITCH_SEMITONES,
    allow_captionless: bool = False,
) -> Path:
    """
    Top-level orchestration: resolve the accepted recap source, build the
    command, and run ffmpeg. The source is deliberately resolved from the
    recap identity rather than the global editor plan, which may refer to a
    different short or episode.
    """

    if captions_ass_path is None and not allow_captionless:
        raise RecapRenderError(
            "Narration captions are required for a recap render; build the recap ASS file first."
        )
    if captions_ass_path is not None and not captions_ass_path.is_file():
        raise RecapRenderError(f"Narration caption ASS file not found: {captions_ass_path}")

    shortfall = float(sequence.get("visual_coverage_shortfall_seconds", 0.0) or 0.0)
    if shortfall > MAX_RENDERABLE_VISUAL_SHORTFALL_SECONDS:
        raise RecapRenderError(
            "Recap sequence lacks moving visual coverage for "
            f"{shortfall:.3f}s; regenerate sequence coverage instead of freezing frames."
        )

    source_video = resolve_recap_source_video(episode_identity)
    timeline_fps = recap_timeline_fps(source_video)
    speed = _validated_playback_speed(playback_speed)
    active_clips = active_voiceover_clips_in_order(voiceover_clips)
    if not active_clips:
        raise RecapRenderError(
            "No active VOICEOVER clips -- generate/enable at least one "
            "narration segment before rendering."
        )

    for clip in active_clips:
        wav_path = (
            wav_path_for_segment(clip["id"], voiceover_dir)
            if voiceover_dir is not None
            else wav_path_for_segment(clip["id"])
        )
        if not wav_path.exists():
            raise RecapRenderError(f"Voiceover WAV not found for {clip['id']!r}: {wav_path}")

    if recap_effects is None:
        try:
            recap_effects = load_recap_effects()
        except RecapEffectsError:
            query = episode_identity.get("query", {})
            source_key = str(query.get("source_filename", "recap")) if isinstance(query, dict) else "recap"
            create_recap_effects_plan(
                sequence,
                load_narration_captions(),
                portrait_plan,
                source_key=source_key,
            )
            recap_effects = load_recap_effects()

    input_index_by_clip_id = input_index_for_voiceover_clips(active_clips)

    filter_complex, final_video_label, final_audio_label = build_recap_filter_complex(
        sequence,
        active_clips,
        input_index_by_clip_id,
        portrait_plan,
        duck_plan,
        captions_ass_path,
        speed,
        recap_effects,
        narration_pitch_semitones=narration_pitch_semitones,
        source_pitch_semitones=source_pitch_semitones,
        timeline_fps=timeline_fps,
    )

    command = build_recap_ffmpeg_command(
        source_video,
        active_clips,
        filter_complex,
        final_video_label,
        final_audio_label,
        output_path,
        sfx_events=recap_effects.get("sfx_events", []),
        emoji_events=recap_effects.get("emoji_events", []),
        voiceover_dir=voiceover_dir,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        raise RecapRenderError(
            f"ffmpeg failed with exit code {result.returncode}:\n{result.stderr[-2000:]}"
        )

    return output_path
