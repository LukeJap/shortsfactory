import sys
from pathlib import Path

import render


def test_resolve_source_video_defaults_when_no_value_given():
    assert render.resolve_source_video(None) == render.DEFAULT_SOURCE_VIDEO
    assert render.resolve_source_video("") == render.DEFAULT_SOURCE_VIDEO


def test_resolve_source_video_resolves_relative_path_against_root():
    result = render.resolve_source_video("videos/foo.mp4")
    assert result == render.ROOT / "videos" / "foo.mp4"


def test_component_target_for_no_collision(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "COMPONENTS_DIR", tmp_path)

    target = render.component_target_for(Path("/somewhere/short1_base.mp4"))

    assert target == tmp_path / "short1_base.mp4"


def test_component_target_for_avoids_collision(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "COMPONENTS_DIR", tmp_path)
    (tmp_path / "short1_base.mp4").write_bytes(b"existing file")

    target = render.component_target_for(Path("/somewhere/short1_base.mp4"))

    assert target == tmp_path / "short1_base_2.mp4"


def test_component_target_for_avoids_multiple_collisions(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "COMPONENTS_DIR", tmp_path)
    (tmp_path / "short1_base.mp4").write_bytes(b"existing file")
    (tmp_path / "short1_base_2.mp4").write_bytes(b"existing file")

    target = render.component_target_for(Path("/somewhere/short1_base.mp4"))

    assert target == tmp_path / "short1_base_3.mp4"


def test_python_executable_returns_current_interpreter_when_it_exists(monkeypatch):
    # sys.executable already points at a real file in this environment --
    # exercises the deterministic "prefer the current interpreter" branch.
    monkeypatch.setattr(sys, "executable", sys.executable)

    assert render.python_executable() == sys.executable
