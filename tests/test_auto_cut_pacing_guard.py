"""
Real-ffmpeg verification that auto_cut.py (STEP 3) applies the same
"natural pacing guard" apply_smart_edit.py (STEP 5) applies, so the two
stages' cut lists actually converge in the common case (no approved
semantic/manual cuts) -- letting STEP 5's existing reuse-skip logic
(keep_segments_match_existing_tight_video()) avoid a fully redundant
second re-encode of the same clip. See auto_cut.py's module docstring
for the render-log-confirmed motivation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import auto_cut
import apply_smart_edit


def _make_source_video(path: Path, duration: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={duration}",
            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _words_with_frequent_gaps() -> list[dict]:
    # 8 words, ~1.0s gaps between each -- well over MAXIMUM's
    # auto_cut_min_gap (0.9s), and with min_spacing=0.9s/max_removal_
    # ratio=0.35 on a 10s clip, this reliably triggers BOTH the crowding
    # rejection and the removal-budget rejection (mirroring the real
    # render log this fix was based on: "Skipped 2 crowded cuts and 1
    # cuts over the removal budget").
    words = []
    t = 0.0
    for i in range(8):
        words.append({"word": f"w{i}", "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += 1.3
    return words


def test_auto_cut_applies_same_pacing_guard_as_apply_smart_edit(tmp_path, monkeypatch):
    input_video = tmp_path / "short1_base.mp4"
    output_video = tmp_path / "short1_tight.mp4"
    edit_plan_path = tmp_path / "edit_plan.json"
    subtitles_path = tmp_path / "subtitles.json"

    _make_source_video(input_video, duration=10.0)
    subtitles_path.write_text(
        json.dumps({"words": _words_with_frequent_gaps()}), encoding="utf-8"
    )

    monkeypatch.setattr(auto_cut, "INPUT_VIDEO", input_video)
    monkeypatch.setattr(auto_cut, "OUTPUT_VIDEO", output_video)
    monkeypatch.setattr(auto_cut, "EDIT_PLAN_PATH", edit_plan_path)
    monkeypatch.setattr(auto_cut, "SUBTITLES_PATH", subtitles_path)
    monkeypatch.setattr(auto_cut, "load_render_settings", lambda: {"edit_energy": "MAXIMUM"})

    exit_code = auto_cut.main()
    assert exit_code == 0
    assert output_video.exists()

    edit_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    written_keep_ranges = [
        (item["start"], item["end"]) for item in edit_plan["keep_ranges"]
    ]

    # Independently recompute what the pacing guard should have produced,
    # using the exact same functions apply_smart_edit.py (STEP 5) itself
    # calls -- this is the actual convergence this fix guarantees.
    duration = auto_cut.get_video_duration(input_video)
    raw_cuts = auto_cut.detect_pause_cuts(
        _words_with_frequent_gaps(),
        min_gap_to_edit=0.9,
        keep_gap_seconds=0.3,
    )
    assert len(raw_cuts) >= 5, "test fixture should produce several raw pause cuts"

    guarded_pause_cuts, _, warning = apply_smart_edit.apply_automatic_cut_safety(
        apply_smart_edit.extract_pause_cuts({"cuts": raw_cuts}),
        [],
        duration,
        energy="MAXIMUM",
    )
    assert warning is not None, "test fixture should actually trigger the pacing guard"
    assert len(guarded_pause_cuts) < len(raw_cuts), (
        "the guard should have rejected at least one cut -- otherwise this "
        "test isn't exercising the fix"
    )

    expected_keep_ranges = auto_cut.cuts_to_keep_ranges(
        [
            cut
            for cut in raw_cuts
            if (round(cut["start"], 3), round(cut["end"], 3))
            in {(round(g["start"], 3), round(g["end"], 3)) for g in guarded_pause_cuts}
        ],
        duration,
    )

    assert written_keep_ranges == expected_keep_ranges

    # And the real rendered video's duration reflects the GUARDED list,
    # not the raw one -- proving the fix changed what actually got
    # encoded, not just what got logged.
    rendered_duration = auto_cut.get_video_duration(output_video)
    expected_duration = sum(end - start for start, end in expected_keep_ranges)
    assert abs(rendered_duration - expected_duration) < 0.2


def test_apply_smart_edit_reuse_check_now_matches_the_common_case(tmp_path, monkeypatch):
    """
    The actual payoff: with STEP 3's cut list already pacing-guard-capped,
    apply_smart_edit.keep_segments_match_existing_tight_video() -- the
    real check that decides whether STEP 5 skips its re-encode -- returns
    True for the common case (no approved semantic/manual cuts), which
    it did not before this fix whenever the guard rejected anything.
    """
    input_video = tmp_path / "short1_base.mp4"
    output_video = tmp_path / "short1_tight.mp4"
    edit_plan_path = tmp_path / "edit_plan.json"
    subtitles_path = tmp_path / "subtitles.json"

    _make_source_video(input_video, duration=10.0)
    subtitles_path.write_text(
        json.dumps({"words": _words_with_frequent_gaps()}), encoding="utf-8"
    )

    monkeypatch.setattr(auto_cut, "INPUT_VIDEO", input_video)
    monkeypatch.setattr(auto_cut, "OUTPUT_VIDEO", output_video)
    monkeypatch.setattr(auto_cut, "EDIT_PLAN_PATH", edit_plan_path)
    monkeypatch.setattr(auto_cut, "SUBTITLES_PATH", subtitles_path)
    monkeypatch.setattr(auto_cut, "load_render_settings", lambda: {"edit_energy": "MAXIMUM"})

    assert auto_cut.main() == 0
    pause_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))

    # STEP 5's own independent recomputation, with zero semantic/manual
    # cuts (the common case this fix targets).
    duration = auto_cut.get_video_duration(input_video)
    raw_cuts = auto_cut.detect_pause_cuts(
        _words_with_frequent_gaps(), min_gap_to_edit=0.9, keep_gap_seconds=0.3
    )
    guarded_pause_cuts, _, _ = apply_smart_edit.apply_automatic_cut_safety(
        apply_smart_edit.extract_pause_cuts({"cuts": raw_cuts}),
        [],
        duration,
        energy="MAXIMUM",
    )
    keep_segments = auto_cut.cuts_to_keep_ranges(
        [
            cut
            for cut in raw_cuts
            if (round(cut["start"], 3), round(cut["end"], 3))
            in {(round(g["start"], 3), round(g["end"], 3)) for g in guarded_pause_cuts}
        ],
        duration,
    )

    assert apply_smart_edit.keep_segments_match_existing_tight_video(
        keep_segments, pause_plan
    )
