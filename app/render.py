"""
The core render pipeline orchestrator (`python app/render.py --source ...
--start ... --end ...`, invoked by the GUI's "Generate Final Video"
button, gui_app/mixins/render_pipeline.py). Runs STEP 1 (crop-to-fill the
selection to 1080x1920) through STEP 11 (organize output) in order,
shelling out to the other app/*.py pipeline scripts as subprocesses --
see main() for the exact STEP sequence. Also owns the caption safe-margin
constants (CAPTION_SAFE_MARGIN_*, CAPTION_DRAG_MARGIN_*) and the shared
clamp_caption_drag_position() used by both the real caption burn-in and
the GUI's placement-editor preview.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .visual_emphasis import (
        auto_cut_aggression_from_energy,
        coerce_auto_cut_aggression,
        DEFAULT_ENERGY,
        load_render_settings,
        normalize_energy,
        normalize_sfx_mode,
        write_render_settings,
    )
except ImportError:
    from visual_emphasis import (
        auto_cut_aggression_from_energy,
        coerce_auto_cut_aggression,
        DEFAULT_ENERGY,
        load_render_settings,
        normalize_energy,
        normalize_sfx_mode,
        write_render_settings,
    )

try:
    from .standard_audio_pitch import build_standard_audio_pitch_filter
except ImportError:
    from standard_audio_pitch import build_standard_audio_pitch_filter

try:
    from .canvas_config import (
        OUTPUT_HEIGHT,
        OUTPUT_WIDTH,
    )
except ImportError:
    from canvas_config import (
        OUTPUT_HEIGHT,
        OUTPUT_WIDTH,
    )

try:
    from .base_video_polish import (
        PRODUCTION_POLISH_PRESET,
        polish_filters,
    )
except ImportError:
    from base_video_polish import (
        PRODUCTION_POLISH_PRESET,
        polish_filters,
    )

try:
    from .pipeline_paths import (
        CAPTIONS_PATH,
        EDIT_PLAN_PATH,
        SEMANTIC_EDIT_PLAN_PATH as SEMANTIC_PLAN_PATH,
        SHORT_PLAN_PATH as PLAN_PATH,
        SUBTITLES_PATH,
    )
except ImportError:
    from pipeline_paths import (
        CAPTIONS_PATH,
        EDIT_PLAN_PATH,
        SEMANTIC_EDIT_PLAN_PATH as SEMANTIC_PLAN_PATH,
        SHORT_PLAN_PATH as PLAN_PATH,
        SUBTITLES_PATH,
    )

try:
    from .render_archive import is_archived_clip_name
except ImportError:
    from render_archive import is_archived_clip_name

try:
    from .editor_asset_plan import load_editor_asset_plan
    from .persistent_title import write_persistent_title_ass
except ImportError:
    from editor_asset_plan import load_editor_asset_plan
    from persistent_title import write_persistent_title_ass


ROOT = Path(__file__).resolve().parent.parent

OUTPUT_DIR = ROOT / "output" / "rendered"

COMPONENTS_DIR = OUTPUT_DIR / "_components"

BASE_OUTPUT_PATH = (
    OUTPUT_DIR / "short1_base.mp4"
)

TIGHT_OUTPUT_PATH = (
    OUTPUT_DIR / "short1_tight.mp4"
)

CAPTION_OUTPUT_PATH = (
    OUTPUT_DIR / "short1_captioned.mp4"
)

PERSISTENT_TITLE_ASS_PATH = ROOT / "output" / "persistent_title.ass"

DEFAULT_SOURCE_VIDEO = (
    ROOT / "input" / "short1.mp4"
)

# Keep captions around the lower-center of the Shorts canvas rather
# than near the bottom UI controls.
CAPTION_SAFE_MARGIN_LEFT = 110
CAPTION_SAFE_MARGIN_RIGHT = 180
CAPTION_SAFE_MARGIN_BOTTOM = 980

# Hard floor/ceiling for a *manually dragged* caption position (both the
# placement-editor preview and the real \pos() override in
# make_captions.py's caption_position_override_tag() clamp to these). Looser
# than CAPTION_SAFE_MARGIN_BOTTOM above -- that constant sets a stylistic
# default placement (roughly screen-center), not the actual edge of usable
# space -- but still tight enough to keep a dragged caption's anchor out of
# where a platform's own UI (like/comment/share rail, caption/username
# strip, top status area) typically sits on a real vertical video post.
CAPTION_DRAG_MARGIN_TOP = 140
CAPTION_DRAG_MARGIN_BOTTOM = 260


def clamp_caption_drag_position(
    position_x: float,
    position_y: float,
) -> tuple[float, float]:
    """
    Clamp a caption anchor position (fraction of canvas, 0-1) into the
    safe drag range -- shared by the placement-editor preview
    (gui_app/mixins/caption_preview.py) and the real \\pos() override
    (make_captions.py's caption_position_override_tag()) so a manual drag
    can never land somewhere the render itself would refuse to honor.
    """

    min_x = CAPTION_SAFE_MARGIN_LEFT / OUTPUT_WIDTH
    max_x = (OUTPUT_WIDTH - CAPTION_SAFE_MARGIN_RIGHT) / OUTPUT_WIDTH
    min_y = CAPTION_DRAG_MARGIN_TOP / OUTPUT_HEIGHT
    max_y = (OUTPUT_HEIGHT - CAPTION_DRAG_MARGIN_BOTTOM) / OUTPUT_HEIGHT

    return (
        max(min_x, min(max_x, position_x)),
        max(min_y, min(max_y, position_y)),
    )


def caption_anchor_y_px(render_settings: dict) -> float:
    """
    Effective caption anchor y (pixels from top) for the current render
    settings: the manually-dragged position if one is set (see
    caption_position_override_tag() in make_captions.py), else the
    default MarginV-based placement this module's ffmpeg force_style
    applies (CAPTION_SAFE_MARGIN_BOTTOM from the bottom edge). Alignment=2
    (bottom-center) means caption text grows upward from this point, not
    downward -- used by emoji_overlay.py to keep auto-placed emoji clear
    of wherever the caption actually sits.
    """

    position_y = render_settings.get("caption_position_y")

    if position_y is None:
        return float(OUTPUT_HEIGHT - CAPTION_SAFE_MARGIN_BOTTOM)

    try:
        raw_y = max(0.0, min(1.0, float(position_y)))
    except (TypeError, ValueError):
        return float(OUTPUT_HEIGHT - CAPTION_SAFE_MARGIN_BOTTOM)

    _, fraction_y = clamp_caption_drag_position(0.5, raw_y)
    return fraction_y * OUTPUT_HEIGHT


def caption_filter_fragment(
    captions_path: Path = CAPTIONS_PATH,
) -> str:
    """Return the shared FFmpeg subtitles filter used by final rendering."""
    try:
        filter_path = captions_path.resolve().relative_to(ROOT.resolve())
    except (OSError, ValueError):
        filter_path = captions_path

    normalized_path = str(filter_path).replace("\\", "/")
    return (
        f"subtitles={normalized_path}:"
        f"force_style='Alignment=2,MarginL={CAPTION_SAFE_MARGIN_LEFT},"
        f"MarginR={CAPTION_SAFE_MARGIN_RIGHT},"
        f"MarginV={CAPTION_SAFE_MARGIN_BOTTOM}'"
    )


# ============================================================
# PIPELINE SCRIPTS
# ============================================================

SUBTITLES_SCRIPT = (
    ROOT / "app" / "subtitles.py"
)

AUTO_CUT_SCRIPT = (
    ROOT / "app" / "auto_cut.py"
)

SEMANTIC_EDIT_SCRIPT = (
    ROOT / "app" / "semantic_edit.py"
)

APPLY_SMART_EDIT_SCRIPT = (
    ROOT / "app" / "apply_smart_edit.py"
)

CAPTIONS_SCRIPT = (
    ROOT / "app" / "make_captions.py"
)

EMOJI_SCRIPT = (
    ROOT / "app" / "emoji_overlay.py"
)

SFX_SCRIPT = (
    ROOT / "app" / "sfx_engine.py"
)


def run_command(
    command: list[str],
) -> None:

    print()
    print("Running:")
    print(" ".join(command))
    print()

    result = subprocess.run(
        command,
        cwd=ROOT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code "
            f"{result.returncode}"
        )


def python_executable() -> str:

    current = str(
        sys.executable
        or ""
    ).strip()
    if current:
        current_path = Path(
            current
        )
        if current_path.exists():
            return current

        broken_root = f"{ROOT}.venv"
        if broken_root in current:
            repaired = str(
                ROOT / ".venv" / "Scripts" / "python.exe"
            )
            if Path(
                repaired
            ).exists():
                return repaired

    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(
                candidate
            )

    return current or sys.executable


def write_semantic_fallback_plan(
    reason: str,
) -> None:

    SEMANTIC_PLAN_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    SEMANTIC_PLAN_PATH.write_text(
        json.dumps(
            {
                "summary": "Semantic editing skipped for this render.",
                "initial_proposal_count": 0,
                "approved_cut_count": 0,
                "removed_seconds": 0.0,
                "approved_cuts": [],
                "all_verification_results": [],
                "warning": reason,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def load_json(
    path: Path,
) -> dict:

    if not path.exists():

        raise FileNotFoundError(
            f"File not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        return json.load(f)


def component_target_for(
    path: Path,
) -> Path:

    target = COMPONENTS_DIR / path.name

    if not target.exists():
        return target

    stem = path.stem
    suffix = path.suffix

    for index in range(
        2,
        1000,
    ):

        candidate = COMPONENTS_DIR / f"{stem}_{index}{suffix}"

        if not candidate.exists():
            return candidate

    return COMPONENTS_DIR / f"{stem}_old{suffix}"


def move_component_with_retry(
    path: Path,
    target: Path,
    attempts: int = 8,
    delay_seconds: float = 0.25,
) -> bool:

    last_error: OSError | None = None

    for attempt in range(
        max(
            1,
            int(attempts),
        )
    ):
        try:
            path.replace(
                target
            )
            return True
        except OSError as exc:
            last_error = exc

            if attempt + 1 < attempts:
                time.sleep(
                    max(
                        0.0,
                        float(delay_seconds),
                    )
                )

    print(
        "WARNING: Could not move rendered component "
        f"{path.name} into _components after {attempts} attempts: "
        f"{last_error}. Leaving it in place."
    )
    return False


def organize_rendered_output() -> None:

    if not CAPTION_OUTPUT_PATH.exists():
        return

    COMPONENTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print(
        "=== STEP 11: Organizing rendered output ==="
    )
    print()

    moved: list[str] = []
    skipped: list[str] = []

    for path in sorted(
        OUTPUT_DIR.iterdir(),
        key=lambda item: item.name.lower(),
    ):

        if path == COMPONENTS_DIR:
            continue

        if not path.is_file():
            continue

        if path.name == CAPTION_OUTPUT_PATH.name:
            continue

        if is_archived_clip_name(path.name):
            continue

        target = component_target_for(
            path
        )

        if move_component_with_retry(
            path,
            target,
        ):
            moved.append(
                path.name
            )
        else:
            # Render-folder organization is housekeeping only. A temporary
            # Windows lock must never turn a finished Short into a failed render.
            skipped.append(
                path.name
            )

    print(
        f"Final video kept here: {CAPTION_OUTPUT_PATH}"
    )

    if moved:
        print(
            f"Moved {len(moved)} component/artifact file(s) to: {COMPONENTS_DIR}"
        )
    else:
        print(
            "No extra rendered artifacts needed to be moved."
        )

    if skipped:
        print(
            "WARNING: Left "
            f"{len(skipped)} locked/unavailable component file(s) "
            "in output/rendered. The final Short is still valid."
        )


# ============================================================
# CLIP SELECTION
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description="ShortsFactory renderer"
    )

    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Path to the source video.",
    )

    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Start time in seconds or HH:MM:SS.mmm.",
    )

    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End time in seconds or HH:MM:SS.mmm.",
    )

    args = parser.parse_args()

    if bool(args.start) != bool(args.end):

        parser.error(
            "--start and --end must be supplied together."
        )

    return args


def resolve_source_video(
    value: str | None,
) -> Path:

    if not value:
        return DEFAULT_SOURCE_VIDEO

    path = Path(value)

    if not path.is_absolute():
        path = ROOT / path

    return path.resolve()

def get_clip_timestamps() -> tuple[str, str]:

    print()
    print(
        "How do you want to select the clip?"
    )

    print()
    print("[1] AI-selected clip")
    print("[2] Manually enter timestamps")
    print()

    choice = input(
        "Choice: "
    ).strip()

    if choice == "2":

        print()

        start = input(
            "Enter START timestamp "
            "(HH:MM:SS.mmm): "
        ).strip()

        end = input(
            "Enter END timestamp "
            "(HH:MM:SS.mmm): "
        ).strip()

        if not start or not end:

            raise RuntimeError(
                "Both start and end timestamps "
                "are required."
            )

        print()
        print(
            f"Selected clip: "
            f"{start} -> {end}"
        )

        return start, end

    plan = load_json(
        PLAN_PATH
    )

    source_clip = plan.get(
        "source_clip",
        {},
    )

    start = source_clip.get(
        "start_timestamp"
    )

    end = source_clip.get(
        "end_timestamp"
    )

    if not start or not end:

        raise RuntimeError(
            "short_plan.json does not contain "
            "valid source clip timestamps."
        )

    print()
    print(
        "Using AI-selected clip from "
        "short_plan.json."
    )

    return str(start), str(end)


# ============================================================
# CONTENT RECT
#
# The shared portrait-framing helper uses this geometry to contain the active
# source picture inside its blurred 9:16 composition. Keep this small helper
# here because the Standard renderer and the Recap portrait-plan module both
# need the exact same aspect-preserving math.
# ============================================================

def ffprobe_source_dimensions(
    source: Path,
) -> tuple[int, int]:

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(source),
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

    data = json.loads(
        result.stdout
    )

    streams = data.get(
        "streams",
        [],
    )

    if not streams:
        raise RuntimeError(
            f"No video stream found in: {source}"
        )

    width = int(
        streams[0]["width"]
    )

    height = int(
        streams[0]["height"]
    )

    return width, height


def content_rect_for_source(
    source_width: int,
    source_height: int,
    canvas_width: int = OUTPUT_WIDTH,
    canvas_height: int = OUTPUT_HEIGHT,
) -> tuple[int, int, int, int]:
    """
    Mirrors render_base_video()'s
    "scale=...:force_original_aspect_ratio=decrease,pad=...:(ow-iw)/2:
    (oh-ih)/2" filter: fits source_width x source_height into the canvas
    preserving aspect ratio, then centers it. Returns
    (content_x, content_y, content_width, content_height) -- the
    rectangle within the canvas the real video content occupies.
    """

    if source_width <= 0 or source_height <= 0:
        return 0, 0, canvas_width, canvas_height

    scale_factor = min(
        canvas_width / source_width,
        canvas_height / source_height,
    )

    content_width = max(
        1,
        round(
            source_width
            * scale_factor
        ),
    )

    content_height = max(
        1,
        round(
            source_height
            * scale_factor
        ),
    )

    content_x = (
        canvas_width
        - content_width
    ) // 2

    content_y = (
        canvas_height
        - content_height
    ) // 2

    return (
        content_x,
        content_y,
        content_width,
        content_height,
    )


# ============================================================
# STEP 1
# ============================================================

def base_video_filter_chain() -> str:
    """Return Standard's production-polish filter tail.

    The shared portrait path applies the color/sharpening filters before its
    source split, so the blurred background and sharp foreground remain a
    matched treatment. This utility remains the standalone production-finish
    contract used by existing callers/tests.
    """

    return ",".join([*polish_filters(PRODUCTION_POLISH_PRESET), "setsar=1", "format=yuv420p"])


def standard_portrait_framing_plan_for_video(source_video: Path) -> dict[str, Any]:
    """Build Standard Mode's source-aware plan with the shared Recap helper."""

    # Import lazily: recap_media.portrait_framing reuses this module's
    # ffprobe/content-rect helpers, so importing it at module load would make
    # the otherwise harmless relationship circular.
    from recap_media.portrait_framing import build_portrait_framing_plan_for_video

    return build_portrait_framing_plan_for_video(source_video)


def standard_portrait_filter_complex(portrait_plan: dict[str, Any]) -> str:
    """Compose Standard's active source over its blurred 9:16 background.

    This deliberately uses the exact Recap filter primitive. Standard still
    owns its normal pipeline and later effects/caption stages; only the
    source-picture geometry and background treatment are shared.
    """

    from recap_media.portrait_framing import build_portrait_filter_chain

    required = ("content_x", "content_y", "content_width", "content_height")
    if not all(key in portrait_plan for key in required):
        raise ValueError("Standard portrait plan is missing content geometry.")

    active_rect = portrait_plan.get("active_rect")
    source_width = portrait_plan.get("source_width")
    source_height = portrait_plan.get("source_height")
    if (
        not isinstance(active_rect, dict)
        or active_rect == {"x": 0, "y": 0, "width": source_width, "height": source_height}
    ):
        active_rect = None

    composition = build_portrait_filter_chain(
        int(portrait_plan["content_x"]),
        int(portrait_plan["content_y"]),
        int(portrait_plan["content_width"]),
        int(portrait_plan["content_height"]),
        canvas_width=int(portrait_plan.get("canvas_width", OUTPUT_WIDTH)),
        canvas_height=int(portrait_plan.get("canvas_height", OUTPUT_HEIGHT)),
        blur_sigma=float(portrait_plan.get("blur_sigma", 25.0)),
        background_dim=float(portrait_plan.get("background_dim", 0.0)),
        active_rect=active_rect,
        pre_split_filters=polish_filters(PRODUCTION_POLISH_PRESET),
    )
    return f"{composition};[recap_out]setsar=1,format=yuv420p[standard_out]"


def render_base_video(
    source_video: Path,
    start: str,
    end: str,
    audio_pitch_semitones: float = 0.0,
) -> None:

    print()
    print(
        "=== STEP 1: Rendering selected "
        "vertical clip ==="
    )
    print()

    # Standard and Recap share the active-picture crop, aspect-preserving
    # foreground geometry, and moving blurred source background. The later
    # Standard stages still work on the same full 1080x1920 canvas.
    portrait_plan = standard_portrait_framing_plan_for_video(source_video)
    settings = load_render_settings()
    settings["content_x"] = 0
    settings["content_y"] = 0
    settings["content_width"] = OUTPUT_WIDTH
    settings["content_height"] = OUTPUT_HEIGHT
    write_render_settings(settings)

    command = [
        "ffmpeg",
        "-y",

        "-ss",
        start,

        "-to",
        end,

        "-i",
        str(source_video),

        "-filter_complex",
        standard_portrait_filter_complex(portrait_plan),

        # ShortsFactory exports only the primary video plus optional primary
        # audio. Some source files carry long timecode/data tracks; allowing
        # FFmpeg to auto-select those can make a 6-second Short report as
        # several minutes long in media players.
        "-map",
        "[standard_out]",

        "-map",
        "0:a:0?",

        "-sn",
        "-dn",

        # The source is often a long-form episode with its own chapter
        # markers (e.g. "Opening Credits", "End Credits"); those are
        # meaningless on an 11-second Short and would otherwise ride
        # along, with shifted timestamps, through every later stage that
        # derives its file from this one.
        "-map_chapters",
        "-1",

        "-c:v",
        "libx264",

        # This encode's output gets re-cut/re-encoded again by at least
        # one later stage before delivery, so the better rate-distortion
        # optimization "medium" buys is wasted here. The final delivery
        # encode uses the same CRF after captions and emoji are combined.
        "-preset",
        "veryfast",

        "-crf",
        "20",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-movflags",
        "+faststart",

        str(BASE_OUTPUT_PATH),
    ]

    pitch_filter = build_standard_audio_pitch_filter(audio_pitch_semitones)
    if pitch_filter:
        audio_map_index = command.index("0:a:0?")
        command[audio_map_index + 2:audio_map_index + 2] = ["-af", pitch_filter]

    run_command(
        command
    )


# ============================================================
# TRANSCRIPTION
# ============================================================

def regenerate_subtitles(
    video_path: Path,
    step_name: str,
    *,
    quality: str = "AUTO",
    selection_start: float | None = None,
    selection_end: float | None = None,
) -> None:

    print()
    print(
        f"=== {step_name} ==="
    )
    print()

    if not SUBTITLES_SCRIPT.exists():

        raise FileNotFoundError(
            f"Subtitle script not found: "
            f"{SUBTITLES_SCRIPT}"
        )

    if not video_path.exists():

        raise FileNotFoundError(
            f"Video for transcription "
            f"not found: {video_path}"
        )

    if SUBTITLES_PATH.exists():

        SUBTITLES_PATH.unlink()

        print(
            "Deleted previous subtitles.json"
        )

    command = [
        python_executable(),
        str(SUBTITLES_SCRIPT),
        "--quality",
        str(quality),
    ]

    if (
        selection_start is not None
        and selection_end is not None
    ):
        command.extend(
            [
                "--selection-start",
                f"{selection_start:.3f}",
                "--selection-end",
                f"{selection_end:.3f}",
            ]
        )

    command.append(
        str(video_path)
    )

    run_command(
        command
    )


def remap_subtitles_after_smart_edit(
    video_path: Path,
    *,
    quality: str = "AUTO",
) -> None:
    print()
    print(
        "=== STEP 6: Remapping transcript through SMART-EDITED clip ==="
    )
    print()

    if not SUBTITLES_SCRIPT.exists():
        raise FileNotFoundError(
            f"Subtitle script not found: {SUBTITLES_SCRIPT}"
        )

    if not video_path.exists():
        raise FileNotFoundError(
            f"Smart-edited video not found: {video_path}"
        )

    if not SUBTITLES_PATH.exists():
        raise FileNotFoundError(
            "Selected source transcript is missing before smart-edit remap."
        )

    run_command(
        [
            python_executable(),
            str(SUBTITLES_SCRIPT),
            "--quality",
            str(quality),
            "--remap-through-cuts",
            str(video_path),
        ]
    )


def source_transcript_selection(
    render_settings: dict,
    current_render_source: Path,
) -> tuple[Path, float, float] | None:
    raw_source = str(
        render_settings.get(
            "source_video",
            "",
        )
        or ""
    ).strip()

    if not raw_source:
        return None

    source_path = Path(raw_source).expanduser()
    if not source_path.is_absolute():
        source_path = ROOT / source_path

    try:
        source_path = source_path.resolve()
        current_resolved = current_render_source.resolve()
    except OSError:
        return None

    if not source_path.exists():
        return None

    try:
        selection_start = float(
            render_settings.get(
                "selection_start",
                0.0,
            )
        )
        selection_end = float(
            render_settings.get(
                "selection_end",
                0.0,
            )
        )
    except (TypeError, ValueError):
        return None

    if selection_end <= selection_start:
        return None

    current_is_original = (
        current_resolved
        == source_path
    )
    current_is_reframed = (
        current_render_source.name.lower()
        == "reframed_source.mp4"
    )

    if not (
        current_is_original
        or current_is_reframed
    ):
        return None

    return (
        source_path,
        selection_start,
        selection_end,
    )


# ============================================================
# SMART EDIT ANALYSIS
# ============================================================

def analyze_pauses() -> None:

    print()
    print(
        "=== STEP 3: Detecting dead air "
        "and long pauses ==="
    )
    print()

    if not AUTO_CUT_SCRIPT.exists():

        raise FileNotFoundError(
            f"Auto-cut script not found: "
            f"{AUTO_CUT_SCRIPT}"
        )

    run_command(
        [
            python_executable(),
            str(AUTO_CUT_SCRIPT),
        ]
    )


def analyze_semantic_cuts() -> None:

    print()
    print(
        "=== STEP 4: AI semantic editing ==="
    )
    print()

    if not SEMANTIC_EDIT_SCRIPT.exists():
        warning = (
            "Semantic editor not found: "
            f"{SEMANTIC_EDIT_SCRIPT}"
        )
        print(
            f"WARNING: {warning}"
        )
        print(
            "Continuing without AI semantic cuts."
        )
        write_semantic_fallback_plan(
            warning
        )
        return

    try:
        run_command(
            [
                python_executable(),
                str(SEMANTIC_EDIT_SCRIPT),
            ]
        )
    except Exception as exc:
        warning = str(
            exc
        )
        print(
            f"WARNING: Semantic editing failed: {warning}"
        )
        print(
            "Continuing with pause and manual edits only."
        )
        write_semantic_fallback_plan(
            warning
        )


def apply_smart_edit() -> None:

    print()
    print(
        "=== STEP 5: Applying approved "
        "smart jump cuts ==="
    )
    print()

    if not APPLY_SMART_EDIT_SCRIPT.exists():

        raise FileNotFoundError(
            f"Smart-edit renderer not found: "
            f"{APPLY_SMART_EDIT_SCRIPT}"
        )

    run_command(
        [
            python_executable(),
            str(APPLY_SMART_EDIT_SCRIPT),
        ]
    )

    if not TIGHT_OUTPUT_PATH.exists():

        raise FileNotFoundError(
            "Smart editor did not produce "
            "short1_tight.mp4."
        )


# ============================================================
# CAPTIONS
# ============================================================

def regenerate_captions() -> None:

    print()
    print(
        "=== STEP 7: Generating expressive "
        "karaoke captions ==="
    )
    print()

    if not CAPTIONS_SCRIPT.exists():

        raise FileNotFoundError(
            f"Caption generator not found: "
            f"{CAPTIONS_SCRIPT}"
        )

    if CAPTIONS_PATH.exists():

        CAPTIONS_PATH.unlink()

        print(
            "Deleted previous captions.ass"
        )

    run_command(
        [
            python_executable(),
            str(CAPTIONS_SCRIPT),
        ]
    )


def burn_captions() -> None:

    print()
    print(
        "=== STEP 8: Preparing captions for final composite ==="
    )
    print()

    if not CAPTIONS_PATH.exists():

        raise FileNotFoundError(
            f"Caption file not found: "
            f"{CAPTIONS_PATH}"
        )

    if not TIGHT_OUTPUT_PATH.exists():

        raise FileNotFoundError(
            f"Tight video not found: "
            f"{TIGHT_OUTPUT_PATH}"
        )

    print(
        "Caption burn deferred until the combined caption + emoji encode."
    )


# ============================================================
# EMOJIS
# ============================================================

def add_emoji_overlay(
    title_ass_path: Path | None = None,
) -> None:

    print()
    print(
        "=== STEP 9: Rendering captions and "
        "full-color emoji overlays ==="
    )
    print()

    if not EMOJI_SCRIPT.exists():

        raise FileNotFoundError(
            f"Emoji overlay script not found: "
            f"{EMOJI_SCRIPT}"
        )

    command = [
        python_executable(),
        str(EMOJI_SCRIPT),
        "--input",
        str(TIGHT_OUTPUT_PATH),
        "--output",
        str(CAPTION_OUTPUT_PATH),
        "--captions",
        str(CAPTIONS_PATH.relative_to(ROOT)),
    ]
    if title_ass_path is not None:
        command.extend(["--title", str(title_ass_path.relative_to(ROOT))])
    run_command(command)


# ============================================================
# SOUND FX
# ============================================================

def add_sound_effects() -> None:

    print()
    print(
        "=== STEP 10: Adding automatic sound effects ==="
    )
    print()

    if not SFX_SCRIPT.exists():

        print(
            f"WARNING: SFX engine script not found: {SFX_SCRIPT}"
        )

        return

    result = subprocess.run(
        [
            python_executable(),
            str(SFX_SCRIPT),
        ],
        cwd=ROOT,
    )

    if result.returncode != 0:

        print(
            (
                "WARNING: SFX engine failed with exit code "
                f"{result.returncode}; continuing without blocking render."
            )
        )


def sanitize_final_output() -> None:

    print()
    print(
        "=== STEP 10.5: Sanitizing final media streams ==="
    )
    print()

    if not CAPTION_OUTPUT_PATH.exists():
        raise FileNotFoundError(
            f"Final Short not found: {CAPTION_OUTPUT_PATH}"
        )

    sanitized_path = CAPTION_OUTPUT_PATH.with_name(
        f"{CAPTION_OUTPUT_PATH.stem}_sanitized{CAPTION_OUTPUT_PATH.suffix}"
    )

    if sanitized_path.exists():
        sanitized_path.unlink()

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(CAPTION_OUTPUT_PATH),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(sanitized_path),
    ]

    run_command(
        command
    )

    sanitized_path.replace(
        CAPTION_OUTPUT_PATH
    )


# ============================================================
# MAIN PIPELINE
# ============================================================

def resolve_render_config(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Any], str, str, str]:
    """
    Resolve and print the effective render configuration: which source
    video, and the edit energy / sound-FX mode / transcription quality
    settings saved from the GUI (each normalized to a known-valid value).
    Raises FileNotFoundError if the resolved source video is missing.
    Returns (source_video, render_settings, edit_energy, sfx_mode,
    transcription_quality).
    """
    source_video = resolve_source_video(
        args.source
    )

    render_settings = load_render_settings()
    edit_energy = normalize_energy(
        render_settings.get(
            "edit_energy",
            DEFAULT_ENERGY,
        )
    )
    sfx_mode = normalize_sfx_mode(
        render_settings.get(
            "sfx_mode",
            "AUTO",
        )
    )
    transcription_quality = str(
        render_settings.get(
            "transcription_quality",
            "AUTO",
        )
        or "AUTO"
    ).upper()
    if transcription_quality not in {
        "AUTO",
        "FAST",
        "ACCURATE",
    }:
        transcription_quality = "AUTO"

    print()
    print(
        "========================================"
    )

    print(
        "       ShortsFactory Renderer"
    )

    print(
        "========================================"
    )

    print()

    print(
        f"Project folder: {ROOT}"
    )

    print(
        f"Edit energy: {edit_energy}"
    )

    print(
        f"Sound FX: {sfx_mode}"
    )

    print(
        f"Transcription quality: {transcription_quality}"
    )

    if not source_video.exists():

        raise FileNotFoundError(
            f"Source video not found: "
            f"{source_video}"
        )

    return (
        source_video,
        render_settings,
        edit_energy,
        sfx_mode,
        transcription_quality,
    )


def resolve_source_timestamps(
    args: argparse.Namespace,
) -> tuple[str, str]:
    """
    Resolve the source-video clip selection: timestamps supplied on the
    command line/by the GUI take priority, otherwise prompt interactively
    for how to select the clip.
    """
    if (
        args.start is not None
        and args.end is not None
    ):

        start = str(
            args.start
        )

        end = str(
            args.end
        )

        print()
        print(
            "Using timestamps supplied by "
            "the app/command line."
        )

    else:

        start, end = get_clip_timestamps()

    print()
    print(
        f"Selected source: "
        f"{start} -> {end}"
    )

    return start, end


def timestamp_to_seconds(value: str) -> float:
    """Parse the timestamp forms accepted by ffmpeg's ``-ss``/``-to``."""

    parts = str(value).strip().split(":")
    try:
        seconds = float(parts[-1])
        if len(parts) >= 2:
            seconds += int(parts[-2]) * 60
        if len(parts) >= 3:
            seconds += int(parts[-3]) * 3600
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, seconds)


def run_transcript_step(
    render_settings: dict[str, Any],
    source_video: Path,
    transcription_quality: str,
) -> None:
    """
    STEP 2: reuse the transcript created when the source was imported --
    only the selected range is copied to subtitles.json and shifted to
    selection-relative timestamps -- falling back to transcribing
    short1_base.mp4 from scratch if the original source's transcript
    metadata isn't available.
    """
    transcript_selection = source_transcript_selection(
        render_settings,
        source_video,
    )

    if transcript_selection is not None:
        (
            transcript_source,
            transcript_start,
            transcript_end,
        ) = transcript_selection

        regenerate_subtitles(
            transcript_source,
            (
                "STEP 2: Preparing selected transcript "
                "from imported source cache"
            ),
            quality=transcription_quality,
            selection_start=transcript_start,
            selection_end=transcript_end,
        )
    else:
        print()
        print(
            (
                "WARNING: Original source transcript metadata is unavailable; "
                "falling back to transcribing short1_base.mp4."
            )
        )

        regenerate_subtitles(
            BASE_OUTPUT_PATH,
            (
                "STEP 2: Transcribing original "
                "selected clip"
            ),
            quality=transcription_quality,
        )


def format_duration_minutes_seconds(
    seconds: float,
) -> str:

    total_seconds = max(
        0,
        int(
            round(
                seconds
            )
        ),
    )
    minutes, remaining_seconds = divmod(
        total_seconds,
        60,
    )
    return f"{minutes}m {remaining_seconds}s"


def print_render_summary(
    start: str,
    end: str,
    elapsed_seconds: float,
) -> None:
    """
    Print the final "RENDERING COMPLETE" banner with the paths to every
    artifact this run produced.
    """
    print()
    print(
        "========================================"
    )

    print(
        "       RENDERING COMPLETE"
    )

    print(
        "========================================"
    )

    print()

    print(
        f"Source clip:       "
        f"{start} -> {end}"
    )

    print(
        f"Original render:   "
        f"{BASE_OUTPUT_PATH}"
    )

    print(
        f"Smart edit:        "
        f"{TIGHT_OUTPUT_PATH}"
    )

    print(
        f"Final transcript:  "
        f"{SUBTITLES_PATH}"
    )

    print(
        f"Caption file:      "
        f"{CAPTIONS_PATH}"
    )

    print(
        f"FINAL SHORT:       "
        f"{CAPTION_OUTPUT_PATH}"
    )

    print(
        f"Total render time: "
        f"{format_duration_minutes_seconds(elapsed_seconds)}"
    )

    print()


def main() -> int:
    render_start_time = time.monotonic()

    args = parse_args()

    (
        source_video,
        render_settings,
        edit_energy,
        sfx_mode,
        transcription_quality,
    ) = resolve_render_config(
        args
    )

    raw_auto_cut_aggression = render_settings.get("auto_cut_aggression")
    auto_cut_aggression = (
        auto_cut_aggression_from_energy(edit_energy)
        if raw_auto_cut_aggression is None
        else coerce_auto_cut_aggression(raw_auto_cut_aggression)
    )
    auto_cuts_enabled = bool(
        render_settings.get(
            "auto_cuts_enabled",
            True,
        )
    ) and auto_cut_aggression > 0
    print(
        f"Auto Cuts: {'ON' if auto_cuts_enabled else 'OFF'}"
    )
    print(f"AutoCut aggression: {auto_cut_aggression}")
    standard_audio_pitch_semitones = render_settings.get(
        "standard_audio_pitch_semitones", 0.0
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Select source section
    # --------------------------------------------------------

    start, end = resolve_source_timestamps(
        args
    )
    title_ass_path = write_persistent_title_ass(
        load_editor_asset_plan().get("persistent_title", {}),
        timestamp_to_seconds(end) - timestamp_to_seconds(start),
        PERSISTENT_TITLE_ASS_PATH,
    )

    # --------------------------------------------------------
    # STEP 1
    # Render source section
    # --------------------------------------------------------

    render_base_video(
        source_video,
        start,
        end,
        audio_pitch_semitones=standard_audio_pitch_semitones,
    )

    # --------------------------------------------------------
    # STEP 2
    # Reuse the transcript created when the source was imported.
    # Only the selected range is copied to subtitles.json and shifted
    # to selection-relative timestamps.
    # --------------------------------------------------------

    run_transcript_step(
        render_settings,
        source_video,
        transcription_quality,
    )

    # --------------------------------------------------------
    # STEP 3
    # Detect pauses
    # --------------------------------------------------------
    #
    # STEP 4
    # Detect + verify redundant speech
    # --------------------------------------------------------
    #
    # Both are skipped entirely -- not run with reduced aggressiveness --
    # when Auto Cuts is off. STEP 5 always runs regardless: it's the sole
    # producer of combined_edit_plan.json every later stage depends on, and
    # its existing missing-plan-file handling already produces exactly the
    # "full, untouched clip, manual cuts still applied" behavior needed
    # here with no changes of its own.
    # --------------------------------------------------------

    if auto_cuts_enabled:
        analyze_pauses()
        analyze_semantic_cuts()
    else:
        print()
        print(
            "=== STEP 3/4: Auto Cuts disabled -- skipping "
            "pause and semantic-edit detection ==="
        )

        # Not calling analyze_pauses()/analyze_semantic_cuts() only stops
        # them from being *regenerated* -- it does nothing about a plan
        # file left over from an earlier render where Auto Cuts was on.
        # apply_smart_edit.py (STEP 5, which always runs) reads whatever
        # is on disk at these paths, so a stale plan from last time would
        # otherwise get silently reapplied even with the toggle off.
        # Removing them makes this a genuinely missing file, which
        # apply_smart_edit.py's existing load_json()/extract_*_cuts()
        # already handle correctly (returns {} / [] -> no cuts).
        for stale_plan_path in (EDIT_PLAN_PATH, SEMANTIC_PLAN_PATH):
            try:
                stale_plan_path.unlink()
            except FileNotFoundError:
                pass
            else:
                print(
                    f"Removed stale plan: {stale_plan_path}"
                )

        print()

    # --------------------------------------------------------
    # STEP 5
    # Merge + apply approved edits
    # --------------------------------------------------------

    apply_smart_edit()

    # --------------------------------------------------------
    # STEP 6
    # Remap the selected transcript through apply_smart_edit.py's exact
    # keep-segment map instead of running Whisper on short1_tight.mp4.
    # --------------------------------------------------------

    remap_subtitles_after_smart_edit(
        TIGHT_OUTPUT_PATH,
        quality=transcription_quality,
    )

    # --------------------------------------------------------
    # STEP 7
    # Generate captions using NEW timestamps
    # --------------------------------------------------------

    regenerate_captions()

    # --------------------------------------------------------
    # STEP 8
    # Burn captions into TIGHT video
    # --------------------------------------------------------

    burn_captions()

    # --------------------------------------------------------
    # STEP 9
    # Full-color emoji graphics
    # --------------------------------------------------------

    add_emoji_overlay(title_ass_path)

    # --------------------------------------------------------
    # STEP 10
    # Automatic sound design
    # --------------------------------------------------------

    add_sound_effects()

    # --------------------------------------------------------
    # STEP 10.5
    # Final export sanitation. Optional emoji/SFX stages may skip
    # rewriting the file, so explicitly remove non-A/V streams here.
    # --------------------------------------------------------

    sanitize_final_output()

    # --------------------------------------------------------
    # STEP 11
    # Keep the rendered folder easy to scan.
    # --------------------------------------------------------

    organize_rendered_output()

    # --------------------------------------------------------
    # DONE
    # --------------------------------------------------------

    print_render_summary(
        start,
        end,
        time.monotonic() - render_start_time,
    )

    return 0


if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except Exception as exc:

        print()
        print(
            "========================================"
        )

        print(
            "       RENDERING FAILED"
        )

        print(
            "========================================"
        )

        print()

        print(
            f"Error: {exc}"
        )

        sys.exit(1)
