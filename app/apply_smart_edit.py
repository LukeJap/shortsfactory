from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .visual_emphasis import (
        energy_profile,
        load_render_settings,
        normalize_energy,
    )
except ImportError:
    from visual_emphasis import (
        energy_profile,
        load_render_settings,
        normalize_energy,
    )


ROOT = Path(__file__).resolve().parent.parent

BASE_VIDEO = (
    ROOT
    / "output"
    / "rendered"
    / "short1_base.mp4"
)

TIGHT_VIDEO = (
    ROOT
    / "output"
    / "rendered"
    / "short1_tight.mp4"
)

PAUSE_PLAN = (
    ROOT
    / "output"
    / "edit_plan.json"
)

SEMANTIC_PLAN = (
    ROOT
    / "output"
    / "semantic_edit_plan.json"
)

MANUAL_PLAN = (
    ROOT
    / "output"
    / "manual_edit_plan.json"
)

COMBINED_PLAN = (
    ROOT
    / "output"
    / "combined_edit_plan.json"
)

MIN_CUT_SECONDS = 0.025
MERGE_TOLERANCE_SECONDS = 0.035


def log(message: str = "") -> None:
    print(message, flush=True)


def as_float(value: Any) -> float | None:

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_json(path: Path) -> dict[str, Any]:

    if not path.exists():
        return {}

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}

    return (
        data
        if isinstance(data, dict)
        else {}
    )


def ffprobe_duration(
    video_path: Path,
) -> float:

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
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

    return float(
        result.stdout.strip()
    )


def candidate_cut_from_dict(
    item: dict[str, Any],
) -> tuple[float, float] | None:
    """
    Accept several plan formats so this file remains compatible with
    the pause and semantic editors as those evolve.
    """

    start_keys = (
        "start",
        "start_seconds",
        "cut_start",
        "remove_start",
    )

    end_keys = (
        "end",
        "end_seconds",
        "cut_end",
        "remove_end",
    )

    start = None
    end = None

    for key in start_keys:

        if key in item:
            start = as_float(
                item.get(key)
            )

            if start is not None:
                break

    for key in end_keys:

        if key in item:
            end = as_float(
                item.get(key)
            )

            if end is not None:
                break

    if (
        start is None
        or end is None
        or end <= start
    ):
        return None

    return (
        start,
        end,
    )


def cut_is_approved(
    item: dict[str, Any],
) -> bool:

    # Pause plans usually contain only actual cuts, so absent approval
    # fields default to True.

    boolean_fields = (
        "approved",
        "verified",
        "keep_cut",
        "accepted",
        "apply",
    )

    for field in boolean_fields:

        if field in item:

            return bool(
                item.get(field)
            )

    decision = str(
        item.get(
            "decision",
            item.get(
                "verifier_decision",
                "",
            ),
        )
        or ""
    ).strip().lower()

    if decision:

        if decision in {
            "reject",
            "rejected",
            "no",
            "false",
        }:
            return False

        if decision in {
            "approve",
            "approved",
            "yes",
            "true",
            "accept",
            "accepted",
        }:
            return True

    status = str(
        item.get(
            "status",
            "",
        )
        or ""
    ).strip().lower()

    if status in {
        "rejected",
        "reject",
        "disabled",
        "skip",
        "skipped",
    }:
        return False

    return True


def parse_cut_list(
    value: Any,
    source_name: str,
    require_explicit_approval: bool = False,
) -> list[dict[str, Any]]:

    if not isinstance(
        value,
        list,
    ):
        return []

    cuts: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()

    for item in value:

        if not isinstance(
            item,
            dict,
        ):
            continue

        candidate = candidate_cut_from_dict(
            item
        )

        if candidate is None:
            continue

        if require_explicit_approval:

            explicitly_approved = False

            for field in (
                "approved",
                "verified",
                "keep_cut",
                "accepted",
                "apply",
            ):

                if field in item:

                    explicitly_approved = bool(
                        item.get(field)
                    )

                    break

            if not explicitly_approved:

                decision = str(
                    item.get(
                        "decision",
                        item.get(
                            "verifier_decision",
                            "",
                        ),
                    )
                    or ""
                ).strip().lower()

                explicitly_approved = (
                    decision
                    in {
                        "approve",
                        "approved",
                        "yes",
                        "true",
                        "accept",
                        "accepted",
                    }
                )

            if not explicitly_approved:
                continue

        start, end = candidate

        key = (
            round(
                start,
                4,
            ),
            round(
                end,
                4,
            ),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        cuts.append(
            {
                "start": start,
                "end": end,
                "source": source_name,
                "text": str(
                    item.get(
                        "text",
                        item.get(
                            "remove",
                            "",
                        ),
                    )
                    or ""
                ).strip(),
            }
        )

    return cuts


def extract_pause_cuts(
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Pause editor output contains actual cut ranges, not AI proposals.
    Read only known top-level arrays. Never recurse through metadata.
    """

    for key in (
        "cuts",
        "pause_cuts",
        "edits",
        "approved_cuts",
    ):

        value = plan.get(
            key
        )

        if isinstance(
            value,
            list,
        ):

            return parse_cut_list(
                value,
                "pause",
                require_explicit_approval=False,
            )

    return []


def extract_semantic_cuts(
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Semantic plan may contain both proposals and verifier results.
    Prefer an explicit approved list. If only a generic `cuts` array
    exists, require each entry to explicitly say it was approved.
    """

    for key in (
        "approved_cuts",
        "approved_edits",
        "verified_cuts",
    ):

        value = plan.get(
            key
        )

        if isinstance(
            value,
            list,
        ):

            return parse_cut_list(
                value,
                "semantic",
                require_explicit_approval=False,
            )

    for key in (
        "cuts",
        "edits",
        "proposals",
    ):

        value = plan.get(
            key
        )

        if isinstance(
            value,
            list,
        ):

            return parse_cut_list(
                value,
                "semantic",
                require_explicit_approval=True,
            )

    return []


def merged_duration(
    cuts: list[dict[str, Any]],
    duration: float,
) -> float:

    merged = merge_cuts(
        cuts,
        duration,
    )

    return sum(
        float(
            cut.get(
                "duration",
                0.0,
            )
        )
        for cut in merged
    )


def apply_automatic_cut_safety(
    pause_cuts: list[dict[str, Any]],
    semantic_cuts: list[dict[str, Any]],
    duration: float,
    *,
    profile: dict[str, Any] | None = None,
    energy: str = "PUNCHY",
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
]:
    """
    Keep automatic editing natural and bounded.

    Manual transcript cuts remain authoritative. Automatic cuts must
    leave enough retained footage between jump cuts and must stay
    inside the selected Edit Style's removal budget.
    """

    if profile is None:
        profile = energy_profile(
            energy
        )

    automatic_cuts = []

    for cut in (
        pause_cuts
        + semantic_cuts
    ):
        normalized = normalize_cut(
            cut,
            duration,
        )
        if normalized is not None:
            automatic_cuts.append(
                normalized
            )

    if not automatic_cuts:
        return (
            [],
            [],
            None,
        )

    automatic_cuts.sort(
        key=lambda cut: (
            float(
                cut["start"]
            ),
            float(
                cut["end"]
            ),
        )
    )

    minimum_spacing = float(
        profile.get(
            "auto_cut_min_spacing",
            1.50,
        )
    )
    max_removal_ratio = float(
        profile.get(
            "auto_cut_max_removal_ratio",
            0.22,
        )
    )
    max_removed = max(
        0.0,
        duration
        * max(
            0.0,
            min(
                1.0,
                max_removal_ratio,
            ),
        ),
    )

    spaced: list[dict[str, Any]] = []
    crowded_rejected = 0

    for cut in automatic_cuts:
        if spaced:
            previous = spaced[-1]
            retained_between = (
                float(
                    cut["start"]
                )
                - float(
                    previous["end"]
                )
            )

            if retained_between < minimum_spacing:
                # Pause cuts are less likely than semantic cuts to create
                # an awkward spoken join. Prefer the pause when two
                # automatic edits compete for the same moment.
                if (
                    cut.get(
                        "source"
                    ) == "pause"
                    and previous.get(
                        "source"
                    ) == "semantic"
                ):
                    if (
                        len(
                            spaced
                        ) == 1
                        or float(
                            cut["start"]
                        )
                        - float(
                            spaced[-2]["end"]
                        )
                        >= minimum_spacing
                    ):
                        spaced[-1] = cut

                crowded_rejected += 1
                continue

        spaced.append(
            cut
        )

    # Use the removal budget on the safest edits first: pauses, then
    # already-verified semantic cuts. Sort chronologically again before
    # returning so downstream rendering behavior stays deterministic.
    budget_order = sorted(
        spaced,
        key=lambda cut: (
            0
            if cut.get(
                "source"
            ) == "pause"
            else 1,
            float(
                cut["start"]
            ),
        ),
    )

    selected: list[dict[str, Any]] = []
    selected_removed = 0.0
    budget_rejected = 0

    for cut in budget_order:
        cut_duration = (
            float(
                cut["end"]
            )
            - float(
                cut["start"]
            )
        )

        if (
            selected_removed
            + cut_duration
            > max_removed
            + 1e-6
        ):
            budget_rejected += 1
            continue

        selected.append(
            cut
        )
        selected_removed += cut_duration

    selected.sort(
        key=lambda cut: float(
            cut["start"]
        )
    )

    selected_pause = [
        cut
        for cut in selected
        if cut.get(
            "source"
        ) == "pause"
    ]
    selected_semantic = [
        cut
        for cut in selected
        if cut.get(
            "source"
        ) == "semantic"
    ]

    if (
        crowded_rejected == 0
        and budget_rejected == 0
    ):
        return (
            selected_pause,
            selected_semantic,
            None,
        )

    proposed_removed = merged_duration(
        automatic_cuts,
        duration,
    )
    warning = (
        f"Natural pacing guard ({energy}): "
        f"automatic edits proposed removing {proposed_removed:.2f}s. "
        f"Kept {len(selected)} cuts removing {selected_removed:.2f}s; "
        f"minimum retained spacing is {minimum_spacing:.2f}s and "
        f"automatic removal is capped at "
        f"{max_removal_ratio * 100:.0f}% ({max_removed:.2f}s). "
        f"Skipped {crowded_rejected} crowded cuts and "
        f"{budget_rejected} cuts over the removal budget."
    )

    return (
        selected_pause,
        selected_semantic,
        warning,
    )


def manual_cuts_relative_to_base(
    plan: dict[str, Any],
    base_duration: float,
) -> list[dict[str, Any]]:
    """
    manual_edit_plan.json uses absolute source-video timestamps.
    short1_base.mp4 starts at t=0 for the selected clip.

    Convert source timestamps into base-video-relative timestamps.
    """

    selection_start = (
        as_float(
            plan.get(
                "selection_start"
            )
        )
        or 0.0
    )

    selection_end = as_float(
        plan.get(
            "selection_end"
        )
    )

    raw_cuts = plan.get(
        "cuts",
        [],
    )

    if not isinstance(
        raw_cuts,
        list,
    ):
        return []

    cuts: list[dict[str, Any]] = []

    for item in raw_cuts:

        if not isinstance(
            item,
            dict,
        ):
            continue

        candidate = candidate_cut_from_dict(
            item
        )

        if candidate is None:
            continue

        absolute_start, absolute_end = (
            candidate
        )

        # Only the overlap with the currently selected source clip can
        # exist inside short1_base.mp4.
        clip_start = selection_start

        clip_end = (
            selection_end
            if selection_end is not None
            else selection_start
            + base_duration
        )

        absolute_start = max(
            absolute_start,
            clip_start,
        )

        absolute_end = min(
            absolute_end,
            clip_end,
        )

        if (
            absolute_end
            <= absolute_start
        ):
            continue

        relative_start = (
            absolute_start
            - selection_start
        )

        relative_end = (
            absolute_end
            - selection_start
        )

        relative_start = max(
            0.0,
            min(
                base_duration,
                relative_start,
            ),
        )

        relative_end = max(
            0.0,
            min(
                base_duration,
                relative_end,
            ),
        )

        if (
            relative_end
            - relative_start
            < MIN_CUT_SECONDS
        ):
            continue

        cuts.append(
            {
                "start": relative_start,
                "end": relative_end,
                "source": "manual_transcript_cut",
                "text": str(
                    item.get(
                        "text",
                        "",
                    )
                    or ""
                ).strip(),
                "source_start": candidate[0],
                "source_end": candidate[1],
            }
        )

    return cuts


def normalize_cut(
    cut: dict[str, Any],
    duration: float,
) -> dict[str, Any] | None:

    start = as_float(
        cut.get(
            "start"
        )
    )

    end = as_float(
        cut.get(
            "end"
        )
    )

    if (
        start is None
        or end is None
    ):
        return None

    start = max(
        0.0,
        min(
            duration,
            start,
        ),
    )

    end = max(
        0.0,
        min(
            duration,
            end,
        ),
    )

    if (
        end - start
        < MIN_CUT_SECONDS
    ):
        return None

    normalized = dict(
        cut
    )

    normalized["start"] = round(
        start,
        4,
    )

    normalized["end"] = round(
        end,
        4,
    )

    return normalized


def merge_cuts(
    cuts: list[dict[str, Any]],
    duration: float,
) -> list[dict[str, Any]]:

    normalized = []

    for cut in cuts:

        item = normalize_cut(
            cut,
            duration,
        )

        if item is not None:
            normalized.append(
                item
            )

    normalized.sort(
        key=lambda item: (
            item["start"],
            item["end"],
        )
    )

    merged: list[dict[str, Any]] = []

    for cut in normalized:

        if not merged:

            merged.append(
                {
                    **cut,
                    "sources": [
                        cut.get(
                            "source",
                            "unknown",
                        )
                    ],
                }
            )

            continue

        previous = merged[-1]

        if (
            cut["start"]
            <= previous["end"]
            + MERGE_TOLERANCE_SECONDS
        ):

            previous["end"] = max(
                previous["end"],
                cut["end"],
            )

            source = cut.get(
                "source",
                "unknown",
            )

            if source not in previous[
                "sources"
            ]:
                previous[
                    "sources"
                ].append(
                    source
                )

            previous_text = str(
                previous.get(
                    "text",
                    "",
                )
                or ""
            ).strip()

            cut_text = str(
                cut.get(
                    "text",
                    "",
                )
                or ""
            ).strip()

            if (
                cut_text
                and cut_text
                not in previous_text
            ):

                previous["text"] = (
                    (
                        previous_text
                        + " | "
                        + cut_text
                    )
                    if previous_text
                    else cut_text
                )

        else:

            merged.append(
                {
                    **cut,
                    "sources": [
                        cut.get(
                            "source",
                            "unknown",
                        )
                    ],
                }
            )

    for cut in merged:

        cut["duration"] = round(
            cut["end"]
            - cut["start"],
            4,
        )

    return merged


def keep_segments_from_cuts(
    duration: float,
    cuts: list[dict[str, Any]],
) -> list[tuple[float, float]]:

    keeps: list[
        tuple[float, float]
    ] = []

    cursor = 0.0

    for cut in cuts:

        start = float(
            cut["start"]
        )

        end = float(
            cut["end"]
        )

        if (
            start
            > cursor
            + MIN_CUT_SECONDS
        ):
            keeps.append(
                (
                    cursor,
                    start,
                )
            )

        cursor = max(
            cursor,
            end,
        )

    if (
        duration
        > cursor
        + MIN_CUT_SECONDS
    ):
        keeps.append(
            (
                cursor,
                duration,
            )
        )

    return keeps


def run(
    command: list[str],
) -> None:

    log("")
    log("Running:")
    log(
        " ".join(
            command
        )
    )
    log("")

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def render_keep_segments(
    input_video: Path,
    output_video: Path,
    keep_segments: list[
        tuple[float, float]
    ],
) -> None:

    if not keep_segments:

        raise RuntimeError(
            "The selected cuts would remove the entire clip."
        )

    filter_parts: list[str] = []

    concat_inputs: list[str] = []

    for index, (
        start,
        end,
    ) in enumerate(
        keep_segments
    ):

        filter_parts.append(
            (
                f"[0:v]"
                f"trim=start={start:.6f}:end={end:.6f},"
                "setpts=PTS-STARTPTS"
                f"[v{index}]"
            )
        )

        filter_parts.append(
            (
                f"[0:a]"
                f"atrim=start={start:.6f}:end={end:.6f},"
                "asetpts=PTS-STARTPTS"
                f"[a{index}]"
            )
        )

        concat_inputs.append(
            f"[v{index}][a{index}]"
        )

    filter_parts.append(
        (
            "".join(
                concat_inputs
            )
            + f"concat=n={len(keep_segments)}:v=1:a=1"
            + "[vout][aout]"
        )
    )

    filter_complex = ";".join(
        filter_parts
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(
            input_video
        ),
        "-filter_complex",
        filter_complex,
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
        str(
            output_video
        ),
    ]

    run(
        command
    )


def write_combined_plan(
    duration: float,
    pause_cuts: list[dict[str, Any]],
    semantic_cuts: list[dict[str, Any]],
    manual_cuts: list[dict[str, Any]],
    merged_cuts: list[dict[str, Any]],
    keep_segments: list[
        tuple[float, float]
    ],
) -> None:

    removed = sum(
        cut["duration"]
        for cut in merged_cuts
    )

    payload = {
        "source_video": str(
            BASE_VIDEO
        ),
        "original_duration_seconds": round(
            duration,
            4,
        ),
        "source_cut_counts": {
            "pause": len(
                pause_cuts
            ),
            "semantic": len(
                semantic_cuts
            ),
            "manual_transcript": len(
                manual_cuts
            ),
        },
        "merged_cut_count": len(
            merged_cuts
        ),
        "time_removed_seconds": round(
            removed,
            4,
        ),
        "estimated_final_duration_seconds": round(
            max(
                0.0,
                duration - removed,
            ),
            4,
        ),
        "cuts": merged_cuts,
        "keep_segments": [
            {
                "start": round(
                    start,
                    4,
                ),
                "end": round(
                    end,
                    4,
                ),
                "duration": round(
                    end - start,
                    4,
                ),
            }
            for start, end
            in keep_segments
        ],
    }

    COMBINED_PLAN.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    COMBINED_PLAN.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:

    log(
        "========================================"
    )

    log(
        "     ShortsFactory Combined Smart Edit"
    )

    log(
        "========================================"
    )

    if not BASE_VIDEO.exists():

        log(
            f"ERROR: Missing base video: {BASE_VIDEO}"
        )

        return 1

    try:

        duration = ffprobe_duration(
            BASE_VIDEO
        )

    except (
        subprocess.CalledProcessError,
        ValueError,
    ) as exc:

        log(
            f"ERROR: Could not read base-video duration: {exc}"
        )

        return 1

    settings = load_render_settings()

    # Sanity-check the probed duration against the selection the user
    # actually made. BASE_VIDEO was just trimmed to exactly that range by
    # render_base_video(), so its real duration should closely match. A
    # wild mismatch here (seen in practice: probing the full ~24-minute
    # source instead of a ~50-second selection) means something else wrote
    # to this shared, fixed output path while this pipeline was still
    # running -- most likely another render overlapping this one. Rather
    # than silently build a trim/concat filter that reads far past the end
    # of the real (correct) video data -- which produces corrupted,
    # garbage output -- fail loudly here instead.
    try:
        expected_duration = (
            float(
                settings.get(
                    "selection_end",
                    0.0,
                )
            )
            - float(
                settings.get(
                    "selection_start",
                    0.0,
                )
            )
        )
    except (TypeError, ValueError):
        expected_duration = 0.0

    if (
        expected_duration > 0.0
        and duration
        > expected_duration + 5.0
    ):

        log(
            f"ERROR: {BASE_VIDEO.name} is {duration:.2f}s long, but the "
            f"selected clip is only {expected_duration:.2f}s. This "
            "usually means another render overwrote this file while this "
            "one was still running. Refusing to continue -- please "
            "re-run Generate Short once no other render is in progress."
        )

        return 1

    pause_plan = load_json(
        PAUSE_PLAN
    )

    semantic_plan = load_json(
        SEMANTIC_PLAN
    )

    manual_plan = load_json(
        MANUAL_PLAN
    )

    pause_cuts = extract_pause_cuts(
        pause_plan
    )

    semantic_cuts = extract_semantic_cuts(
        semantic_plan
    )

    energy = normalize_energy(
        settings.get(
            "edit_energy",
            "PUNCHY",
        )
    )
    profile = energy_profile(
        energy
    )

    pause_cuts, semantic_cuts, automatic_warning = (
        apply_automatic_cut_safety(
            pause_cuts,
            semantic_cuts,
            duration,
            profile=profile,
            energy=energy,
        )
    )

    if automatic_warning:

        log("")
        log(
            "WARNING:"
        )

        log(
            automatic_warning
        )

    manual_cuts = manual_cuts_relative_to_base(
        manual_plan,
        duration,
    )

    all_cuts = (
        pause_cuts
        + semantic_cuts
        + manual_cuts
    )

    merged_cuts = merge_cuts(
        all_cuts,
        duration,
    )

    keep_segments = keep_segments_from_cuts(
        duration,
        merged_cuts,
    )

    removed = sum(
        cut["duration"]
        for cut in merged_cuts
    )

    log("")
    log(
        f"Original duration: {duration:.2f}s"
    )

    log(
        f"Pause cuts: {len(pause_cuts)}"
    )

    log(
        f"Approved semantic cuts: {len(semantic_cuts)}"
    )

    log(
        f"Manual transcript cuts: {len(manual_cuts)}"
    )

    log(
        f"Final merged cuts: {len(merged_cuts)}"
    )

    log(
        f"Time removed: {removed:.2f}s"
    )

    log(
        (
            "Estimated final duration: "
            f"{max(0.0, duration - removed):.2f}s"
        )
    )

    write_combined_plan(
        duration,
        pause_cuts,
        semantic_cuts,
        manual_cuts,
        merged_cuts,
        keep_segments,
    )

    TIGHT_VIDEO.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not merged_cuts:

        log("")
        log(
            "No approved or manual cuts."
        )

        log(
            "Creating passthrough short1_tight.mp4..."
        )

        shutil.copyfile(
            BASE_VIDEO,
            TIGHT_VIDEO,
        )

        log("")
        log(
            f"Combined plan: {COMBINED_PLAN}"
        )

        log(
            f"Tight video: {TIGHT_VIDEO}"
        )

        log("")
        log(
            "Done."
        )

        return 0

    if not keep_segments:

        # Never let automatic planning destroy the whole Short.
        # If manual cuts alone remove everything, that is an explicit
        # user edit and should still stop with an error.
        if pause_cuts or semantic_cuts:

            log("")
            log(
                "WARNING: Combined automatic edits would remove the entire clip."
            )

            log(
                "Retrying this render using manual transcript cuts only."
            )

            pause_cuts = []
            semantic_cuts = []

            merged_cuts = merge_cuts(
                manual_cuts,
                duration,
            )

            keep_segments = keep_segments_from_cuts(
                duration,
                merged_cuts,
            )

            removed = sum(
                cut["duration"]
                for cut in merged_cuts
            )

            write_combined_plan(
                duration,
                pause_cuts,
                semantic_cuts,
                manual_cuts,
                merged_cuts,
                keep_segments,
            )

        if not keep_segments:

            log("")
            log(
                "ERROR: Your manual transcript cuts would remove the entire selected clip."
            )

            return 1

    log("")
    log(
        "Rendering approved pause + semantic + manual transcript edits..."
    )

    try:

        render_keep_segments(
            BASE_VIDEO,
            TIGHT_VIDEO,
            keep_segments,
        )

    except subprocess.CalledProcessError as exc:

        log("")
        log(
            f"ERROR: FFmpeg failed with exit code {exc.returncode}"
        )

        return exc.returncode or 1

    except RuntimeError as exc:

        log("")
        log(
            f"ERROR: {exc}"
        )

        return 1

    log("")
    log(
        f"Combined plan: {COMBINED_PLAN}"
    )

    log(
        f"Tight video: {TIGHT_VIDEO}"
    )

    log("")
    log(
        "Done."
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
