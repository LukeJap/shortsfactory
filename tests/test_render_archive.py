from render_archive import (
    archive_final_video,
    is_archived_clip_name,
    next_archived_clip_path,
)


def test_archive_names_are_reserved_for_numbered_final_clips():
    assert is_archived_clip_name("short1.mp4")
    assert is_archived_clip_name("SHORT12.MP4")
    assert not is_archived_clip_name("short1_captioned.mp4")
    assert not is_archived_clip_name("short0.mp4")


def test_next_archive_index_is_monotonic(tmp_path):
    rendered = tmp_path / "rendered"
    rendered.mkdir()
    (rendered / "short1.mp4").write_bytes(b"one")
    (rendered / "short3.mp4").write_bytes(b"three")

    assert next_archived_clip_path(rendered) == rendered / "short4.mp4"


def test_archive_final_video_moves_without_overwriting_previous_clips(tmp_path):
    rendered = tmp_path / "rendered"
    rendered.mkdir()
    (rendered / "short1.mp4").write_bytes(b"first")
    current = rendered / "short1_captioned.mp4"
    current.write_bytes(b"second")

    archived = archive_final_video(current)

    assert archived == rendered / "short2.mp4"
    assert archived.read_bytes() == b"second"
    assert (rendered / "short1.mp4").read_bytes() == b"first"
    assert not current.exists()
