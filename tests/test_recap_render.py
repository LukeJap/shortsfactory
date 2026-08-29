from pathlib import Path

import pytest

from recap_media.render import (
    RECAP_PLAYBACK_SPEED,
    RecapRenderError,
    active_voiceover_clips_in_order,
    build_narration_track_filter,
    build_recap_ffmpeg_command,
    build_recap_filter_complex,
    build_source_audio_track_filter,
    escape_ffmpeg_filter_path,
    build_video_track_filter,
    final_recap_duration_seconds,
    input_index_for_voiceover_clips,
    render_recap,
    resolve_recap_source_video,
)


def _shots():
    return [
        {"start": 10.0, "end": 12.5, "duration": 2.5},
        {"start": 40.0, "end": 43.0, "duration": 3.0},
    ]


def _voiceover_clip(clip_id, start, volume=1.0, active=True, deleted=False):
    return {
        "id": clip_id,
        "kind": "VOICEOVER",
        "start": start,
        "active": active,
        "deleted": deleted,
        "volume": volume,
    }


# ============================================================
# build_video_track_filter
# ============================================================

def test_video_track_filter_trims_and_concats_each_shot():
    filter_str, label = build_video_track_filter(_shots())

    assert "trim=start=10.000:end=12.500" in filter_str
    assert "trim=start=40.000:end=43.000" in filter_str
    assert "concat=n=2:v=1:a=0" in filter_str
    assert label == "vconcat"
    assert f"[{label}]" in filter_str


def test_video_track_filter_uses_custom_labels():
    filter_str, label = build_video_track_filter(
        _shots(), video_label="2:v", output_label="myvideo"
    )
    assert filter_str.startswith("[2:v]trim=")
    assert label == "myvideo"


def test_video_track_filter_never_uses_clone_frame_holds():
    shots = [{"start": 10.0, "end": 12.5, "duration": 2.5, "hold_duration_seconds": 3.25}]

    filter_str, _ = build_video_track_filter(shots)

    assert "tpad=" not in filter_str


def test_video_track_filter_empty_shots_raises():
    with pytest.raises(RecapRenderError, match="empty shot list"):
        build_video_track_filter([])


# ============================================================
# build_source_audio_track_filter
# ============================================================

def test_source_audio_track_filter_uses_atrim_and_concat_audio_only():
    filter_str, label = build_source_audio_track_filter(_shots())
    assert "atrim=start=10.000:end=12.500" in filter_str
    assert "asetpts=PTS-STARTPTS" in filter_str
    assert "concat=n=2:v=0:a=1" in filter_str
    assert label == "asource"


def test_source_audio_track_filter_never_extends_source_audio_for_holds():
    shots = [
        {
            "start": 10.0,
            "end": 12.5,
            "duration": 2.5,
            "timeline_duration_seconds": 5.75,
        }
    ]

    filter_str, _ = build_source_audio_track_filter(shots)

    assert "apad=" not in filter_str


def test_source_audio_track_filter_empty_shots_raises():
    with pytest.raises(RecapRenderError, match="empty shot list"):
        build_source_audio_track_filter([])


# ============================================================
# active_voiceover_clips_in_order
# ============================================================

def test_active_voiceover_clips_filters_and_sorts():
    clips = [
        _voiceover_clip("VO_003", start=20.0),
        _voiceover_clip("VO_001", start=0.0),
        _voiceover_clip("VO_002", start=5.0, active=False),
        _voiceover_clip("VO_004", start=8.0, deleted=True),
    ]
    result = active_voiceover_clips_in_order(clips)
    assert [c["id"] for c in result] == ["VO_001", "VO_003"]


# ============================================================
# build_narration_track_filter
# ============================================================

def test_narration_track_filter_positions_by_start_and_applies_volume():
    clips = [_voiceover_clip("VO_001", start=2.5, volume=0.8)]
    index_map = {"VO_001": 1}

    filter_str, label = build_narration_track_filter(clips, index_map)

    assert "[1:a]volume=0.800,adelay=2500|2500" in filter_str
    assert "amix=inputs=1" in filter_str
    assert label == "anarration"


def test_narration_track_filter_empty_clips_raises():
    with pytest.raises(RecapRenderError, match="zero active"):
        build_narration_track_filter([], {})


def test_narration_track_filter_missing_input_index_raises():
    clips = [_voiceover_clip("VO_001", start=0.0)]
    with pytest.raises(RecapRenderError, match="VO_001"):
        build_narration_track_filter(clips, {})


def test_narration_track_filter_splits_and_delays_audio_around_source_insert():
    clips = [
        {
            **_voiceover_clip("VO_001", start=4.0, volume=0.8),
            "dialogue_pauses": [
                {"narration_offset_seconds": 2.0, "duration_seconds": 2.5}
            ],
        }
    ]

    filter_str, _ = build_narration_track_filter(clips, {"VO_001": 1})

    assert "atrim=start=0.000:end=2.000" in filter_str
    assert "adelay=4000|4000[narr1_0]" in filter_str
    assert "atrim=start=2.000" in filter_str
    assert "adelay=8500|8500[narr1_1]" in filter_str


# ============================================================
# input_index_for_voiceover_clips
# ============================================================

def test_input_index_assignment_starts_at_one_by_default():
    clips = [_voiceover_clip("VO_001", 0.0), _voiceover_clip("VO_002", 5.0)]
    assert input_index_for_voiceover_clips(clips) == {"VO_001": 1, "VO_002": 2}


def test_input_index_assignment_custom_first_index():
    clips = [_voiceover_clip("VO_001", 0.0)]
    assert input_index_for_voiceover_clips(clips, first_index=3) == {"VO_001": 3}


# ============================================================
# build_recap_filter_complex
# ============================================================

def _sequence():
    return {
        "total_duration_seconds": 5.5,
        "segments": [{"shots": _shots()}],
    }


def _portrait_plan():
    return {
        "content_x": 0,
        "content_y": 555,
        "content_width": 1080,
        "content_height": 810,
        "canvas_width": 1080,
        "canvas_height": 1920,
        "blur_sigma": 25.0,
        "background_dim": 0.0,
    }


def _duck_plan():
    return {
        "narration_keyframes": [[0.0, 0.95]],
        "source_keyframes": [[0.0, 0.2]],
        "settings": {"limiter_limit": 0.92},
    }


def test_filter_complex_assembles_all_pieces_without_captions():
    clips = [_voiceover_clip("VO_001", start=0.0)]
    index_map = {"VO_001": 1}

    filter_complex, video_label, audio_label = build_recap_filter_complex(
        _sequence(), clips, index_map, _portrait_plan(), _duck_plan()
    )

    assert "concat=n=2:v=1:a=0" in filter_complex  # video track
    assert "concat=n=2:v=0:a=1" in filter_complex  # source audio track
    assert "adelay=" in filter_complex  # narration track
    assert "split=2" in filter_complex and "gblur" in filter_complex  # portrait framing
    assert "amix=" in filter_complex and "alimiter=" in filter_complex  # duck mix
    assert "subtitles=" not in filter_complex
    assert "setpts=PTS/1.500" in filter_complex
    assert "atempo=1.500" in filter_complex
    assert video_label == "recap_playback_video"
    assert audio_label == "recap_playback_audio"


def test_filter_complex_includes_captions_when_path_given(tmp_path):
    clips = [_voiceover_clip("VO_001", start=0.0)]
    index_map = {"VO_001": 1}
    ass_path = tmp_path / "narration.ass"

    filter_complex, video_label, audio_label = build_recap_filter_complex(
        _sequence(), clips, index_map, _portrait_plan(), _duck_plan(), ass_path
    )

    assert f"subtitles=filename={escape_ffmpeg_filter_path(ass_path)}" in filter_complex
    assert "[recap_captioned]setpts=PTS/1.500" in filter_complex
    assert video_label == "recap_playback_video"


def test_filter_complex_uses_shared_fx_sfx_and_emoji_before_the_single_speed_pass(tmp_path):
    clips = [_voiceover_clip("VO_001", start=0.0)]
    ass_path = tmp_path / "narration.ass"
    effects = {
        "visual_fx_events": [
            {"type": "filter", "effect": "impact_punch", "start": 1.0, "end": 1.5}
        ],
        "motion_events": [
            {"start": 1.0, "end": 1.5, "zoom": 1.08, "movement": "impact_punch"}
        ],
        "sfx_events": [
            {
                "start": 1.0,
                "duration": 0.2,
                "volume": 0.2,
                "trim_in": 0.0,
                "asset_path": str(tmp_path / "ding.mp3"),
            }
        ],
        "emoji_events": [
            {
                "path": tmp_path / "emoji.png",
                "start": 1.2,
                "end": 2.2,
                "position_x": 0.2,
                "position_y": 0.3,
            }
        ],
    }

    filter_complex, video_label, audio_label = build_recap_filter_complex(
        _sequence(), clips, {"VO_001": 1}, _portrait_plan(), _duck_plan(), ass_path,
        recap_effects=effects,
    )

    assert "[recap_out]zoompan=" in filter_complex
    assert "[recap_motion]" in filter_complex
    assert "[2:a:0]atrim=" in filter_complex
    assert "[3:v]format=rgba" in filter_complex
    assert "[ov0]subtitles=" in filter_complex
    assert filter_complex.index("[ov0]subtitles=") < filter_complex.index("setpts=PTS/1.500")
    assert filter_complex.count("atempo=1.500") == 1
    assert video_label == "recap_playback_video"
    assert audio_label == "recap_playback_audio"


def test_filter_complex_escapes_windows_ass_drive_path_for_subtitles():
    clips = [_voiceover_clip("VO_001", start=0.0)]
    index_map = {"VO_001": 1}
    ass_path = Path(r"C:\Users\lukej\Desktop\ShortsFactory\output\recap\narration.ass")

    filter_complex, _, _ = build_recap_filter_complex(
        _sequence(), clips, index_map, _portrait_plan(), _duck_plan(), ass_path
    )

    expected_path = r"C\\:/Users/lukej/Desktop/ShortsFactory/output/recap/narration.ass"
    assert escape_ffmpeg_filter_path(ass_path) == expected_path
    assert f"subtitles=filename={expected_path}" in filter_complex
    assert "original_size=/Users" not in filter_complex
    assert "[recap_captioned]setpts=PTS/1.500" in filter_complex


def test_filter_path_escaping_leaves_posix_paths_valid():
    assert escape_ffmpeg_filter_path(Path("/tmp/recap/narration.ass")) == "/tmp/recap/narration.ass"


def test_final_duration_scales_with_recap_playback_speed():
    assert RECAP_PLAYBACK_SPEED == 1.5
    assert final_recap_duration_seconds(83.883) == pytest.approx(55.922, abs=0.001)


def test_recap_source_resolves_only_from_identity_provenance(tmp_path):
    source_name = "accepted_dumped.mkv"
    accepted = tmp_path / source_name
    accepted.write_bytes(b"video")
    identity = {
        "query": {"source_filename": source_name},
        "source_video": str(tmp_path / "stale_unrelated.mkv"),
    }

    assert resolve_recap_source_video(identity, tmp_path) == accepted


def test_recap_source_missing_from_identity_fails_clearly(tmp_path):
    with pytest.raises(RecapRenderError, match="source_filename"):
        resolve_recap_source_video({"query": {}}, tmp_path)


def test_render_requires_caption_ass_path_before_running_ffmpeg(tmp_path):
    with pytest.raises(RecapRenderError, match="captions are required"):
        render_recap(
            {"query": {"source_filename": "accepted.mkv"}},
            {"segments": [], "visual_coverage_shortfall_seconds": 0.0},
            [],
            _portrait_plan(),
            _duck_plan(),
        )


# ============================================================
# build_recap_ffmpeg_command
# ============================================================

def test_ffmpeg_command_includes_source_and_voiceover_inputs_and_maps(tmp_path):
    clips = [_voiceover_clip("VO_001", start=0.0), _voiceover_clip("VO_002", start=3.0)]
    output_path = tmp_path / "final_recap.mp4"

    command = build_recap_ffmpeg_command(
        tmp_path / "source.mp4",
        clips,
        "somefilter",
        "recap_out",
        "mixed",
        output_path,
        total_duration_seconds=12.345,
    )

    assert command[:3] == ["ffmpeg", "-y", "-i"]
    assert str(tmp_path / "source.mp4") in command
    # one -i per voiceover clip beyond the source video
    assert command.count("-i") == 1 + len(clips)
    assert "-map" in command and "[recap_out]" in command and "[mixed]" in command
    assert "-t" in command and "12.345" in command
    assert str(output_path) in command


def test_ffmpeg_command_adds_shared_sfx_and_emoji_inputs_after_voiceover(tmp_path):
    clips = [_voiceover_clip("VO_001", start=0.0)]
    command = build_recap_ffmpeg_command(
        tmp_path / "source.mp4",
        clips,
        "somefilter",
        "recap_out",
        "mixed",
        tmp_path / "final_recap.mp4",
        sfx_events=[{"asset_path": tmp_path / "ding.mp3"}],
        emoji_events=[{"path": tmp_path / "emoji.png"}],
    )

    assert command.count("-i") == 4
    assert str(tmp_path / "ding.mp3") in command
    assert str(tmp_path / "emoji.png") in command
