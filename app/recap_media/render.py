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
from pipeline_paths import RECAP_FINAL_OUTPUT_PATH
from recap_media.audio_mix import build_duck_filter_complex
from recap_media.portrait_framing import build_portrait_filter_chain
from recap_media.voiceover import wav_path_for_segment


class RecapRenderError(Exception):
    """The recap render command could not be built or failed to run."""


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
        normalized_path = str(captions_ass_path).replace("\\", "/").replace(":", r"\:")
        caption_filter = (
            f"[recap_out]subtitles={normalized_path}[recap_captioned]"
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
    final_audio_label = "mixed"

    fragments = [video_filter, source_audio_filter, narration_filter, portrait_filter]
    if caption_filter is not None:
        fragments.append(caption_filter)
    fragments.append(duck_filter)

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
    source_video: Path,
    sequence: dict[str, Any],
    voiceover_clips: list[dict[str, Any]],
    portrait_plan: dict[str, Any],
    duck_plan: dict[str, Any],
    captions_ass_path: Path | None = None,
    output_path: Path = RECAP_FINAL_OUTPUT_PATH,
) -> Path:
    """
    Top-level orchestration: build the command and actually run ffmpeg.
    Raises RecapRenderError on an empty shot list, zero active narration,
    a missing WAV file, or a nonzero ffmpeg exit code.
    """

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
    )

    command = build_recap_ffmpeg_command(
        source_video,
        active_clips,
        filter_complex,
        final_video_label,
        final_audio_label,
        output_path,
        total_duration_seconds=sequence.get("total_duration_seconds"),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        raise RecapRenderError(
            f"ffmpeg failed with exit code {result.returncode}:\n{result.stderr[-2000:]}"
        )

    return output_path
