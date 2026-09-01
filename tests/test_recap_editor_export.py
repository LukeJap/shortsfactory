from __future__ import annotations

from pathlib import Path

from gui_app.mixins.render_pipeline import RenderPipelineMixin
from gui_app.mixins.recap import _clean_recap_editor_effects
from recap_media.combined_captions import write_combined_recap_caption_ass
from recap_media.render import (
    bind_recap_editor_export_inputs,
    build_recap_editor_export_command,
    build_recap_editor_export_filter_complex,
)


SFX_PATH = "assets/sfx/dragon-studio-pop-402323.mp3"
EMOJI_PATHS = [
    "assets/emoji/23866-spongebob-dance.gif",
    "assets/emoji/587472-patrick.png",
    "assets/emoji/84710-joesad.png",
    "assets/emoji/450135-timeisticking.png",
]


def _effects(*, sfx_count: int = 1, emoji_count: int = 1):
    return {
        "time_basis": "recap_final_timeline",
        "motion_events": [
            {"start": 2.0, "end": 3.0, "zoom": 1.08, "x": 0.5, "y": 0.5}
        ],
        "visual_fx_events": [{"start": 3.0, "end": 4.0, "effect": "flash"}],
        "emoji_events": [
            {
                "emoji": ["spongebob dance", "patrick", "joesad", "timeisticking"][index],
                "path": Path(EMOJI_PATHS[index]).resolve(),
                "start": 4.0 + index,
                "end": 5.0 + index,
                "position_x": 0.5,
                "position_y": 0.5,
                "scale": 1.0,
            }
            for index in range(emoji_count)
        ],
        "sfx_events": [
            {"asset_path": SFX_PATH, "start": 5.0 + index, "end": 5.3 + index, "duration": 0.3}
            for index in range(sfx_count)
        ],
    }


def test_clean_recap_editor_base_excludes_all_editable_effect_kinds():
    assert _clean_recap_editor_effects() == {
        "visual_fx_events": [],
        "motion_events": [],
        "sfx_events": [],
        "emoji_events": [],
        "time_basis": "recap_final_timeline",
    }


def test_recap_editor_export_filter_consumes_current_entities_without_reassembly(tmp_path):
    captions = tmp_path / "recap.ass"
    _inputs, bindings = bind_recap_editor_export_inputs(tmp_path / "editor_base.mp4", _effects())
    effects = _effects()
    effects["sfx_events"] = bindings["sfx_events"]
    effects["emoji_events"] = bindings["emoji_events"]
    filters, video_label, audio_label = build_recap_editor_export_filter_complex(
        effects,
        captions_ass_path=captions,
        timeline_fps=24.0,
        emoji_first_input_index=bindings["emoji_first_input_index"],
    )

    assert video_label == "recap_editor_captioned"
    assert audio_label == "recap_editor_sfx"
    assert "zoompan=" in filters
    assert "subtitles=filename=" in filters
    assert "amix=inputs=2" in filters
    assert "[0:v]trim=start=" not in filters
    assert "crop=" not in filters
    assert "setpts=PTS/" not in filters
    assert "atempo=" not in filters
    assert "rubberband=" not in filters


def test_recap_editor_export_places_persistent_title_before_captions(tmp_path):
    title = tmp_path / "persistent_title.ass"
    captions = tmp_path / "recap.ass"
    filters, video_label, _audio_label = build_recap_editor_export_filter_complex(
        _effects(sfx_count=0, emoji_count=0),
        captions_ass_path=captions,
        title_ass_path=title,
        timeline_fps=24.0,
    )

    assert video_label == "recap_editor_captioned"
    assert filters.index("[recap_editor_titled]") < filters.index(
        "[recap_editor_captioned]"
    )
    assert "YouTubeShortsMockOverlay" not in filters


def test_recap_editor_export_uses_current_sfx_for_music_ducking_and_no_new_inputs(tmp_path):
    captions = tmp_path / "recap.ass"
    input_arguments, bindings = bind_recap_editor_export_inputs(
        tmp_path / "editor_base.mp4", _effects(), music_path=tmp_path / "music.mp3"
    )
    effects = _effects()
    effects["sfx_events"] = bindings["sfx_events"]
    effects["emoji_events"] = bindings["emoji_events"]
    filters, _video_label, audio_label = build_recap_editor_export_filter_complex(
        effects,
        captions_ass_path=captions,
        timeline_fps=24.0,
        emoji_first_input_index=bindings["emoji_first_input_index"],
        music_input_index=bindings["music_input_index"],
        music_volume=0.05,
    )
    command = build_recap_editor_export_command(
        input_arguments,
        filters,
        "recap_editor_captioned",
        audio_label,
        tmp_path / "final.mp4",
    )

    assert "volume='if(between(t,4.940,5.480),0.0290,0.0500)'" in filters
    assert command.count("-i") == 4
    assert "-stream_loop" in command
    assert "render.py" not in " ".join(command)


def test_recap_export_binds_resolved_optional_inputs_without_network(monkeypatch, tmp_path):
    import emoji_overlay

    monkeypatch.setattr(
        emoji_overlay,
        "download_emoji",
        lambda _emoji: (_ for _ in ()).throw(AssertionError("network must not be used")),
    )
    _command, bindings = bind_recap_editor_export_inputs(
        tmp_path / "editor_base.mp4",
        _effects(sfx_count=6, emoji_count=4),
        music_path=tmp_path / "music.mp3",
    )

    assert [event["input_index"] for event in bindings["sfx_events"]] == [1, 2, 3, 4, 5, 6]
    assert [event["input_index"] for event in bindings["emoji_events"]] == [7, 8, 9, 10]
    assert bindings["music_input_index"] == 11


def test_missing_optional_emoji_shifts_music_to_its_actual_input_index(tmp_path):
    effects = _effects(sfx_count=1, emoji_count=1)
    effects["emoji_events"][0]["path"] = tmp_path / "missing.png"
    _command, bindings = bind_recap_editor_export_inputs(
        tmp_path / "editor_base.mp4", effects, music_path=tmp_path / "music.mp3"
    )

    assert bindings["emoji_events"] == []
    assert bindings["music_input_index"] == 2
    effects["sfx_events"] = bindings["sfx_events"]
    effects["emoji_events"] = bindings["emoji_events"]
    filters, _video_label, _audio_label = build_recap_editor_export_filter_complex(
        effects,
        captions_ass_path=tmp_path / "recap.ass",
        timeline_fps=24.0,
        music_input_index=bindings["music_input_index"],
        music_volume=0.07,
    )
    assert "[2:a]volume=" in filters


def test_recap_export_optional_input_bindings_handle_disabled_sfx_and_empty_families(tmp_path):
    no_optional = _effects(sfx_count=0, emoji_count=0)
    command, bindings = bind_recap_editor_export_inputs(tmp_path / "editor_base.mp4", no_optional)
    assert command == ["ffmpeg", "-y", "-i", str(tmp_path / "editor_base.mp4")]
    assert bindings["sfx_events"] == []
    assert bindings["emoji_events"] == []
    assert bindings["music_input_index"] is None

    effects = _effects(sfx_count=7, emoji_count=0)
    effects["sfx_events"][3]["asset_path"] = str(tmp_path / "disabled.mp3")
    _command, bindings = bind_recap_editor_export_inputs(
        tmp_path / "editor_base.mp4", effects, music_path=tmp_path / "music.mp3"
    )
    assert len(bindings["sfx_events"]) == 6
    assert [event["input_index"] for event in bindings["sfx_events"]] == [1, 2, 3, 4, 5, 6]
    assert bindings["emoji_first_input_index"] is None
    assert bindings["music_input_index"] == 7


def test_recap_export_filter_handles_no_sfx_emoji_or_music(tmp_path):
    filters, video_label, audio_label = build_recap_editor_export_filter_complex(
        _effects(sfx_count=0, emoji_count=0),
        captions_ass_path=tmp_path / "recap.ass",
        timeline_fps=24.0,
    )

    assert video_label == "recap_editor_captioned"
    assert audio_label == "0:a"
    assert "amix=inputs=2" not in filters
    assert "overlay=" not in filters


def test_render_pipeline_routes_only_explicit_recap_editor_context():
    class Window(RenderPipelineMixin):
        recap_editor_mode = True
        recap_editor_effects_path = Path("effects_plan.json")
        recap_editor_asset_plan_path = Path("editor_asset_plan.json")

    window = Window()
    assert window.is_recap_editor_export() is True
    window.recap_editor_mode = False
    assert window.is_recap_editor_export() is False
    window.recap_editor_mode = True
    window.recap_editor_effects_path = None
    assert window.is_recap_editor_export() is False


def test_recap_editor_export_keeps_caption_position_and_scale_settings(tmp_path):
    output = tmp_path / "recap.ass"
    write_combined_recap_caption_ass(
        {
            "cues": [
                {"block_id": "N_001", "start": 1.0, "end": 2.0, "text": "Edited caption"},
                {"block_id": "S_001", "start": 2.0, "end": 3.0, "text": "Source dialogue"},
            ]
        },
        output,
        {"caption_position_x": 0.5, "caption_position_y": 0.5, "caption_scale": 1.5},
    )

    content = output.read_text(encoding="utf-8")
    assert "Style: Recap,Arial,117," in content
    assert "{\\pos(540.0,960.0)}Edited caption" in content
    assert "{\\pos(540.0,960.0)}Source dialogue" in content
