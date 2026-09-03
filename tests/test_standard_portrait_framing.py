from __future__ import annotations

from pathlib import Path

import render


def _plan(*, source_width=1920, source_height=1080, active_rect=None):
    active_rect = active_rect or {
        "x": 0,
        "y": 0,
        "width": source_width,
        "height": source_height,
    }
    return {
        "source_width": source_width,
        "source_height": source_height,
        "active_rect": active_rect,
        "content_x": 0,
        "content_y": 656,
        "content_width": 1080,
        "content_height": 608,
        "canvas_width": 1080,
        "canvas_height": 1920,
        "blur_sigma": 25.0,
        "background_dim": 0.0,
    }


def test_standard_filter_uses_active_picture_before_blurred_split():
    plan = _plan(
        active_rect={"x": 240, "y": 0, "width": 1440, "height": 1080}
    )
    plan.update({"content_y": 555, "content_height": 810})

    chain = render.standard_portrait_filter_complex(plan)

    assert "crop=1440:1080:240:0" in chain
    assert chain.index("crop=1440:1080:240:0") < chain.index("split=2")
    assert "gblur=sigma=25.00" in chain
    assert "overlay=0:555[recap_out]" in chain


def test_vertical_standard_source_keeps_full_foreground_without_unneeded_crop():
    plan = _plan(source_width=1080, source_height=1920)
    plan.update({"content_y": 0, "content_height": 1920})

    chain = render.standard_portrait_filter_complex(plan)

    assert "[0:v]crop=" not in chain
    assert "overlay=0:0[recap_out]" in chain


def test_standard_render_base_video_maps_shared_portrait_output(monkeypatch, tmp_path):
    commands = []
    settings = {}
    monkeypatch.setattr(render, "standard_portrait_framing_plan_for_video", lambda _path: _plan())
    monkeypatch.setattr(render, "load_render_settings", lambda: settings)
    monkeypatch.setattr(render, "write_render_settings", lambda value: settings.update(value))
    monkeypatch.setattr(render, "run_command", commands.append)
    monkeypatch.setattr(render, "BASE_OUTPUT_PATH", tmp_path / "standard.mp4")

    render.render_base_video(Path("source.mkv"), "00:00:01.000", "00:00:04.000")

    command = commands[0]
    assert "-filter_complex" in command
    assert "-vf" not in command
    assert command[command.index("-map") + 1] == "[standard_out]"
    assert "gblur=sigma=25.00" in command[command.index("-filter_complex") + 1]
    assert settings["content_width"] == 1080
    assert settings["content_height"] == 1920


def test_standard_render_base_video_applies_duration_preserving_pitch(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(render, "standard_portrait_framing_plan_for_video", lambda _path: _plan())
    monkeypatch.setattr(render, "load_render_settings", lambda: {})
    monkeypatch.setattr(render, "write_render_settings", lambda _value: None)
    monkeypatch.setattr(render, "run_command", commands.append)
    monkeypatch.setattr(render, "BASE_OUTPUT_PATH", tmp_path / "standard.mp4")

    render.render_base_video(Path("source.mkv"), "00:00:01.000", "00:00:04.000", 1.8)

    command = commands[0]
    assert command[command.index("-af") + 1] == (
        "rubberband=pitch=1.109569:tempo=1.000:formant=preserved:pitchq=quality"
    )
