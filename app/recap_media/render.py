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

import subprocess
from pathlib import Path
from typing import Any

from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
from pipeline_paths import RECAP_FINAL_OUTPUT_PATH, ROOT
from recap_media.audio_mix import build_duck_filter_complex
from recap_media.portrait_framing import build_portrait_filter_chain
from recap_media.voiceover import wav_path_for_segment


class RecapRenderError(Exception):
    """The recap render command could not be built or failed to run."""


RECAP_PLAYBACK_SPEED = 1.75
MAX_RENDERABLE_VISUAL_SHORTFALL_SECONDS = 0.15


def _validated_playback_speed(playback_speed: float) -> float:
    try:
        speed = float(playback_speed)
    except (TypeError, ValueError) as exc:
        raise RecapRenderError(f"Invalid recap playback speed: {playback_speed!r}") from exc
    if not 0.5 <= speed <= 2.0:
        raise RecapRenderError("Recap playback speed must be between 0.5x and 2.0x.")
    return speed


def final_recap_duration_seconds(
    base_duration_seconds: float,
    playback_speed: float = RECAP_PLAYBACK_SPEED,
) -> float:
    """Output duration after the final composite speed transform."""

    return round(max(0.0, float(base_duration_seconds)) / _validated_playback_speed(playback_speed), 3)


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
) -> tuple[str, str]:
    """Same trim+concat reconstruction as build_video_track_filter(), for
    the source video's own audio track (atrim/asetpts/concat a=1)."""

    if not shots:
        raise RecapRenderError("Cannot build a source audio track from an empty shot list.")

    parts = []
    labels = []

    for index, shot in enumerate(shots):
        label = f"ashot{index}"
        parts.append(
            f"[{audio_label}]atrim=start={float(shot['start']):.3f}:"
            f"end={float(shot['end']):.3f},asetpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")

    parts.append("".join(labels) + f"concat=n={len(shots)}:v=0:a=1[{output_label}]")
    return ";".join(parts), output_label


def build_narration_track_filter(
    active_clips: list[dict[str, Any]],
    input_index_by_clip_id: dict[str, int],
    output_label: str = "anarration",
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

    parts = []
    labels = []

    for clip in active_clips:
        clip_id = clip["id"]
        if clip_id not in input_index_by_clip_id:
            raise RecapRenderError(f"No ffmpeg input registered for VOICEOVER clip {clip_id!r}.")

        input_index = input_index_by_clip_id[clip_id]
        delay_ms = max(0, round(float(clip.get("start", 0.0) or 0.0) * 1000))
        try:
            volume = max(0.0, min(1.0, float(clip.get("volume", 1.0) or 1.0)))
        except (TypeError, ValueError):
            volume = 1.0

        label = f"narr{input_index}"
        parts.append(
            f"[{input_index}:a]volume={volume:.3f},"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
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
) -> tuple[str, str, str]:
    """
    Assemble the complete filter_complex for one recap render pass.
    Returns (filter_complex, final_video_label, final_audio_label).
    """

    shots = [shot for segment in sequence["segments"] for shot in segment["shots"]]

    video_filter, video_label = build_video_track_filter(shots)
    source_audio_filter, source_audio_label = build_source_audio_track_filter(shots)
    narration_filter, narration_label = build_narration_track_filter(
        active_voiceover_clips, input_index_by_clip_id
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
    )
    final_video_label = "recap_out"

    if captions_ass_path is not None:
        caption_filter = (
            f"[recap_out]subtitles=filename={escape_ffmpeg_filter_path(captions_ass_path)}"
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
    speed = _validated_playback_speed(playback_speed)
    speed_video_label = "recap_playback_video"
    speed_audio_label = "recap_playback_audio"
    playback_filter = (
        f"[{final_video_label}]setpts=PTS/{speed:.3f}[{speed_video_label}];"
        f"[mixed]atempo={speed:.3f}[{speed_audio_label}]"
    )
    final_video_label = speed_video_label
    final_audio_label = speed_audio_label

    fragments = [video_filter, source_audio_filter, narration_filter, portrait_filter]
    if caption_filter is not None:
        fragments.append(caption_filter)
    fragments.append(duck_filter)
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
) -> list[str]:
    """
    Build the full ffmpeg CLI argument list: source video as input 0,
    each active VOICEOVER clip's WAV as one subsequent input (in the
    same order build_recap_filter_complex()'s input_index_by_clip_id
    used), the assembled filter_complex, and the final video+audio maps.
    total_duration_seconds (recap_sequence.json's own total, the
    authoritative output length) is applied via -t so a track that ran
    slightly long/short from the trim+concat/amix arithmetic never
    leaves a dangling silent tail or a truncated final frame.
    """

    command = ["ffmpeg", "-y", "-i", str(source_video)]

    for clip in active_voiceover_clips:
        command.extend(["-i", str(wav_path_for_segment(clip["id"]))])

    command.extend(["-filter_complex", filter_complex])
    command.extend(["-map", f"[{final_video_label}]", "-map", f"[{final_audio_label}]"])

    if total_duration_seconds is not None:
        command.extend(["-t", f"{total_duration_seconds:.3f}"])

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
) -> Path:
    """
    Top-level orchestration: resolve the accepted recap source, build the
    command, and run ffmpeg. The source is deliberately resolved from the
    recap identity rather than the global editor plan, which may refer to a
    different short or episode.
    """

    if captions_ass_path is None:
        raise RecapRenderError(
            "Narration captions are required for a recap render; build the recap ASS file first."
        )
    if not captions_ass_path.is_file():
        raise RecapRenderError(f"Narration caption ASS file not found: {captions_ass_path}")

    shortfall = float(sequence.get("visual_coverage_shortfall_seconds", 0.0) or 0.0)
    if shortfall > MAX_RENDERABLE_VISUAL_SHORTFALL_SECONDS:
        raise RecapRenderError(
            "Recap sequence lacks moving visual coverage for "
            f"{shortfall:.3f}s; regenerate sequence coverage instead of freezing frames."
        )

    source_video = resolve_recap_source_video(episode_identity)
    speed = _validated_playback_speed(playback_speed)
    active_clips = active_voiceover_clips_in_order(voiceover_clips)
    if not active_clips:
        raise RecapRenderError(
            "No active VOICEOVER clips -- generate/enable at least one "
            "narration segment before rendering."
        )

    for clip in active_clips:
        wav_path = wav_path_for_segment(clip["id"])
        if not wav_path.exists():
            raise RecapRenderError(f"Voiceover WAV not found for {clip['id']!r}: {wav_path}")

    input_index_by_clip_id = input_index_for_voiceover_clips(active_clips)

    filter_complex, final_video_label, final_audio_label = build_recap_filter_complex(
        sequence,
        active_clips,
        input_index_by_clip_id,
        portrait_plan,
        duck_plan,
        captions_ass_path,
        speed,
    )

    command = build_recap_ffmpeg_command(
        source_video,
        active_clips,
        filter_complex,
        final_video_label,
        final_audio_label,
        output_path,
        total_duration_seconds=final_recap_duration_seconds(
            sequence.get("total_duration_seconds", 0.0), speed
        ),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        raise RecapRenderError(
            f"ffmpeg failed with exit code {result.returncode}:\n{result.stderr[-2000:]}"
        )

    return output_path
