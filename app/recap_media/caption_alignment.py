"""
B6 -- narration caption timing. Narration text (recap_script.json's
segment "text", the same authoritative source Orpheus was given to
speak) is never overwritten by what a recognizer thinks it heard --
only word-level *timestamps* are recovered from the generated WAV, via
Whisper transcription + alignment against the known text.

align_words_to_timing() is the actual logic and is fully unit-testable
with faked recognized-word input (no Whisper/torch import, no model
download) -- transcribe_narration_wav() is the thin real-Whisper wrapper
around it, deliberately not exercised by the fast test suite the same
way B2's OrpheusProvider HTTP calls aren't (see
SHORTSFACTORY_AI_RECAP_TRACK_B_MEDIA_EDITOR.md's testing section: "No
test should require a live Orpheus server" -- the same spirit applies
here to not requiring a loaded Whisper model for every test run).
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
from pipeline_paths import RECAP_NARRATION_CAPTIONS_PATH
from recap_media.loader import RecapInputError
from recap_media.sequence import WORDS_PER_SECOND_ESTIMATE

# Reusing ShortsFactory's own ASS timing/escaping/color/size building
# blocks (pure functions/constants, no shared mutable state) rather than
# inventing a parallel caption format -- explicitly NOT importing or
# calling into make_captions.py's semantic-emphasis system (classify_word,
# per-energy-tier CAPTION_SIZES, etc.), which is a much larger subsystem
# tightly coupled to the normal (non-recap) editing-energy pipeline and
# out of scope for "caption timing." Normal subtitle generation is
# completely untouched either way -- this only reads from that module.
from make_captions import FONT_SIZE, WHITE, ass_time, escape_ass_text

NARRATION_CAPTIONS_SCHEMA_VERSION = 1

# Fallback per-word duration when there's no reliable pair of matched
# neighbors to interpolate between at all (e.g. Whisper recognized
# nothing usable for this segment) -- same speaking-rate assumption B3
# already uses for its own duration estimate, so segment-level and
# word-level fallbacks agree with each other.
FALLBACK_WORD_SECONDS = 1.0 / WORDS_PER_SECOND_ESTIMATE

_TOKEN_STRIP_RE = re.compile(r"[^a-z0-9']+")


def _normalize_token(word: str) -> str:
    return _TOKEN_STRIP_RE.sub("", word.lower())


def tokenize_narration_text(text: str) -> list[str]:
    """Authoritative-text word list, in display form (original spelling/
    punctuation/casing) -- what actually gets tokenized/normalized for
    matching happens separately in align_words_to_timing()."""

    return text.split()


def align_words_to_timing(
    authoritative_words: list[str],
    recognized_words: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    One output entry per authoritative word, in order: {"text": <the
    authoritative word, verbatim, never the recognizer's version>,
    "start", "end", "matched"}. "matched" is True when this word's
    timing came from an actual aligned Whisper word, False when it had
    to be interpolated (Whisper dropped/merged/misheard that word) --
    kept in the output so a caller can tell how much of a segment's
    captions are directly-measured vs. estimated.

    Alignment itself is a word-level diff (difflib.SequenceMatcher) over
    normalized tokens (lowercased, punctuation stripped) -- appropriate
    here specifically because the audio is TTS generated from this exact
    text, not independent unknown speech, so "roughly the same words in
    the same order, a few possibly misheard/merged" is the right prior
    (a general-purpose speech aligner would need to handle far more
    disagreement than this).
    """

    if not authoritative_words:
        return []

    auth_tokens = [_normalize_token(word) for word in authoritative_words]
    recognized_tokens = [_normalize_token(str(word.get("text", ""))) for word in recognized_words]

    matcher = difflib.SequenceMatcher(a=auth_tokens, b=recognized_tokens, autojunk=False)

    result: list[dict[str, Any] | None] = [None] * len(authoritative_words)

    for tag, auth_start, auth_end, rec_start, rec_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(auth_end - auth_start):
            auth_index = auth_start + offset
            recognized = recognized_words[rec_start + offset]
            result[auth_index] = {
                "text": authoritative_words[auth_index],
                "start": round(float(recognized["start"]), 3),
                "end": round(float(recognized["end"]), 3),
                "matched": True,
            }

    _interpolate_unmatched(result, authoritative_words)

    return result  # type: ignore[return-value]


def _interpolate_unmatched(
    result: list[dict[str, Any] | None],
    authoritative_words: list[str],
) -> None:

    total = len(result)
    index = 0

    while index < total:

        if result[index] is not None:
            index += 1
            continue

        run_start = index
        while index < total and result[index] is None:
            index += 1
        run_end = index  # exclusive

        prev_end_time = result[run_start - 1]["end"] if run_start > 0 else 0.0
        next_start_time = result[run_end]["start"] if run_end < total else None

        run_words = authoritative_words[run_start:run_end]
        weights = [max(1, len(word)) for word in run_words]
        total_weight = sum(weights)

        available = (
            (next_start_time - prev_end_time)
            if next_start_time is not None
            else None
        )

        cursor = prev_end_time

        for offset, (word, weight) in enumerate(zip(run_words, weights)):
            auth_index = run_start + offset

            if available is not None and available > 0 and total_weight > 0:
                duration = available * (weight / total_weight)
            else:
                duration = FALLBACK_WORD_SECONDS

            start = cursor
            end = start + duration

            result[auth_index] = {
                "text": word,
                "start": round(start, 3),
                "end": round(end, 3),
                "matched": False,
            }
            cursor = end


def transcribe_narration_wav(
    wav_path: Path,
    model_name: str = "base",
) -> list[dict[str, Any]]:
    """
    Run Whisper on a narration WAV, returning word-level {start, end,
    text} entries. This is a genuine *recognition* pass -- its
    transcribed text is never treated as authoritative (align_words_to_
    timing() only ever borrows timing from it); only used as input to
    that function. `whisper` is imported locally, not at module level,
    so the rest of this module (and its fast unit tests) don't pay for
    torch's slow import unless this specific function actually runs.
    """

    import whisper  # local import -- see docstring

    model = whisper.load_model(model_name)
    result = model.transcribe(
        str(wav_path),
        word_timestamps=True,
        verbose=False,
        fp16=False,
    )

    words: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            try:
                start = float(word.get("start", 0.0))
                end = float(word.get("end", start))
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            words.append({"start": start, "end": end, "text": text})

    return words


def build_segment_narration_captions(
    segment_id: str,
    text: str,
    recognized_words: list[dict[str, Any]] | None = None,
    wav_path: Path | None = None,
    model_name: str = "base",
) -> dict[str, Any]:
    """
    Build one segment's word-level narration captions. Pass
    recognized_words directly (e.g. from a test double, or a
    already-computed Whisper pass) to skip transcription; otherwise
    wav_path is transcribed via transcribe_narration_wav(). Exactly one
    of recognized_words/wav_path should be given.
    """

    if recognized_words is None:
        if wav_path is None:
            raise ValueError("build_segment_narration_captions needs recognized_words or wav_path")
        recognized_words = transcribe_narration_wav(wav_path, model_name=model_name)

    authoritative_words = tokenize_narration_text(text)
    words = align_words_to_timing(authoritative_words, recognized_words)

    return {
        "segment_id": segment_id,
        "words": words,
        "matched_word_count": sum(1 for word in words if word["matched"]),
        "word_count": len(words),
    }


def build_narration_captions(
    segments: list[dict[str, Any]],
    recognized_words_by_segment: dict[str, list[dict[str, Any]]] | None = None,
    wav_paths_by_segment: dict[str, Path] | None = None,
    model_name: str = "base",
) -> dict[str, Any]:
    """
    Build narration captions for every narration-bearing segment from a
    loaded recap_script.json's "segments" list. Segments with
    presentation_hint "visual_only" carry no narration and are skipped,
    same as recap_media.voiceover.synthesize_segments().
    """

    recognized_words_by_segment = recognized_words_by_segment or {}
    wav_paths_by_segment = wav_paths_by_segment or {}

    segments_out = []
    for segment in segments:
        if segment.get("presentation_hint") == "visual_only":
            continue

        segment_id = segment["segment_id"]
        segments_out.append(
            build_segment_narration_captions(
                segment_id,
                segment["text"],
                recognized_words=recognized_words_by_segment.get(segment_id),
                wav_path=wav_paths_by_segment.get(segment_id),
                model_name=model_name,
            )
        )

    return {
        "schema_version": NARRATION_CAPTIONS_SCHEMA_VERSION,
        "segments": segments_out,
    }


def build_narration_ass_dialogue_lines(
    segment_captions: dict[str, Any],
    time_offset_seconds: float = 0.0,
    dialogue_pauses: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Convert one segment's word-level captions (build_segment_narration_
    captions()'s output) into simple ASS Dialogue lines -- one word per
    line, reusing ShortsFactory's own ass_time()/escape_ass_text()/WHITE/
    FONT_SIZE building blocks rather than inventing a parallel caption
    format (see this module's import site for why make_captions.py's
    much larger semantic-emphasis sizing/color system isn't pulled in
    too): plain white text at the standard caption font size.

    time_offset_seconds shifts every word's timing -- narration captions
    are computed relative to each segment's own WAV (starting at 0), but
    the assembled recap places each segment at wherever
    recap_media.audio_mix.shot_output_windows() says it starts on the
    final output timeline.
    """

    pauses: list[tuple[float, float]] = []
    for pause in dialogue_pauses or []:
        if not isinstance(pause, dict):
            continue
        try:
            offset = max(0.0, float(pause["narration_offset_seconds"]))
            duration = float(pause["duration_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if duration > 0:
            pauses.append((offset, duration))
    pauses.sort()

    def output_intervals(start: float, end: float) -> list[tuple[float, float]]:
        """Split a caption at a source-audio pause so it never spans one."""

        intervals: list[tuple[float, float]] = []
        cursor = start
        accumulated_pause = 0.0
        for pause_offset, pause_duration in pauses:
            if pause_offset <= cursor:
                accumulated_pause += pause_duration
                continue
            if pause_offset >= end:
                break
            intervals.append((cursor + accumulated_pause, pause_offset + accumulated_pause))
            cursor = pause_offset
            accumulated_pause += pause_duration
        intervals.append((cursor + accumulated_pause, end + accumulated_pause))
        return [interval for interval in intervals if interval[1] > interval[0]]

    lines = []
    for word in segment_captions["words"]:
        text = escape_ass_text(word["text"])
        for start_seconds, end_seconds in output_intervals(
            float(word["start"]), float(word["end"])
        ):
            start = ass_time(start_seconds + time_offset_seconds)
            end = ass_time(end_seconds + time_offset_seconds)
            lines.append(
                f"Dialogue: 0,{start},{end},Recap,,0,0,0,,{{\\c{WHITE}\\fs{FONT_SIZE}}}{text}"
            )

    return lines


ASS_STYLE_NAME = "Recap"


DEFAULT_ASS_MARGIN_V = 250

# How far above the visible content's own bottom edge (not the full
# canvas) captions sit, when a portrait_plan is available.
CONTENT_CAPTION_INSET_PX = 40


def _margin_v_for_portrait_plan(portrait_plan: dict[str, Any] | None) -> int:
    """
    Alignment=2's MarginV is measured from the *canvas* bottom edge, not
    the visible content's -- fine for normal crop-to-fill (content fills
    the whole canvas) but wrong for Recap Mode's blurred-background
    portrait framing (B7), where a fixed canvas-relative margin can land
    captions in the blurred background band instead of on the actual
    video for anything other than a 16:9 source. Ties the margin to the
    real content rect instead when a portrait_plan is given.
    """

    if not portrait_plan:
        return DEFAULT_ASS_MARGIN_V

    canvas_height = int(portrait_plan.get("canvas_height", OUTPUT_HEIGHT))
    content_y = int(portrait_plan.get("content_y", 0))
    content_height = int(portrait_plan.get("content_height", canvas_height))
    content_bottom = content_y + content_height

    return max(
        CONTENT_CAPTION_INSET_PX,
        canvas_height - content_bottom + CONTENT_CAPTION_INSET_PX,
    )


def build_narration_captions_ass_content(
    narration_captions: dict[str, Any],
    voiceover_clips: list[dict[str, Any]],
    portrait_plan: dict[str, Any] | None = None,
) -> str:
    """
    A complete, standalone .ass file's content covering every segment's
    word-level captions, each offset to its *actual* position on the
    render's output timeline.

    Deliberately keyed off voiceover_clips (editor_asset_plan.json's
    VOICEOVER clips, exactly as last edited in the GUI -- B9's audio
    track is built from these same clips) rather than recap_sequence.
    json's separately-computed shot timeline, so captions stay in sync
    with the narration audio actually heard even if "manual edits are
    authoritative" (shared contract) has moved a segment's real position
    away from whatever recap_sequence.json assumed when it was last
    generated. Segments with no matching active VOICEOVER clip (disabled,
    deleted, or not yet synthesized) are skipped -- no caption for
    narration that isn't playing.

    portrait_plan (recap_media.portrait_framing's plan dict) keeps
    captions anchored to the real content rect instead of the full
    canvas -- see _margin_v_for_portrait_plan().
    """

    clips_by_segment_id = {
        clip["id"]: clip
        for clip in voiceover_clips
        if clip.get("active", True) and not clip.get("deleted")
    }

    margin_v = _margin_v_for_portrait_plan(portrait_plan)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {OUTPUT_WIDTH}\n"
        f"PlayResY: {OUTPUT_HEIGHT}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: {ASS_STYLE_NAME},Arial,{FONT_SIZE},{WHITE},{WHITE},"
        f"&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,8,4,2,70,70,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    lines: list[str] = []
    for segment_captions in narration_captions["segments"]:
        segment_id = segment_captions["segment_id"]
        clip = clips_by_segment_id.get(segment_id)
        if clip is None:
            continue
        lines.extend(
            build_narration_ass_dialogue_lines(
                segment_captions,
                float(clip.get("start", 0.0) or 0.0),
                clip.get("dialogue_pauses"),
            )
        )

    body = "\n".join(lines)
    return header + body + ("\n" if body else "")


def write_narration_captions_ass_file(
    narration_captions: dict[str, Any],
    voiceover_clips: list[dict[str, Any]],
    path: Path,
    portrait_plan: dict[str, Any] | None = None,
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_narration_captions_ass_content(
            narration_captions, voiceover_clips, portrait_plan
        ),
        encoding="utf-8",
    )


def write_narration_captions(
    captions: dict[str, Any],
    path: Path = RECAP_NARRATION_CAPTIONS_PATH,
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(captions, indent=2), encoding="utf-8")


def load_narration_captions(path: Path = RECAP_NARRATION_CAPTIONS_PATH) -> dict[str, Any]:

    if not path.exists():
        raise RecapInputError(f"narration_captions.json not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecapInputError(f"narration_captions.json is not valid JSON ({path}): {exc}") from exc

    if not isinstance(data, dict) or "segments" not in data:
        raise RecapInputError(
            f"narration_captions.json is missing required field 'segments' ({path})"
        )

    return data
