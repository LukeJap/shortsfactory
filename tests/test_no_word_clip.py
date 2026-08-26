import json

import auto_cut
import make_captions


def test_auto_cut_preserves_a_clip_without_word_timestamps(tmp_path, monkeypatch):
    base_video = tmp_path / "base.mp4"
    tight_video = tmp_path / "tight.mp4"
    subtitles_path = tmp_path / "subtitles.json"
    edit_plan_path = tmp_path / "edit_plan.json"

    base_video.write_bytes(b"base")
    subtitles_path.write_text(
        json.dumps({"words": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(auto_cut, "INPUT_VIDEO", base_video)
    monkeypatch.setattr(auto_cut, "OUTPUT_VIDEO", tight_video)
    monkeypatch.setattr(auto_cut, "SUBTITLES_PATH", subtitles_path)
    monkeypatch.setattr(auto_cut, "EDIT_PLAN_PATH", edit_plan_path)
    monkeypatch.setattr(auto_cut, "get_video_duration", lambda path: 14.003)
    monkeypatch.setattr(
        auto_cut,
        "run",
        lambda command: tight_video.write_bytes(b"tight"),
    )

    assert auto_cut.main() == 0
    assert tight_video.exists()

    plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    assert plan["cut_count"] == 0
    assert plan["keep_ranges"] == [
        {"start": 0.0, "end": 14.003, "duration": 14.003}
    ]


def test_make_captions_writes_empty_caption_and_emoji_plans(
    tmp_path,
    monkeypatch,
):
    subtitles_path = tmp_path / "subtitles.json"
    captions_path = tmp_path / "captions.ass"
    emoji_path = tmp_path / "emoji_events.json"

    subtitles_path.write_text(
        json.dumps({"words": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(make_captions, "INPUT_PATH", subtitles_path)
    monkeypatch.setattr(make_captions, "OUTPUT_PATH", captions_path)
    monkeypatch.setattr(make_captions, "EMOJI_OUTPUT_PATH", emoji_path)
    monkeypatch.setattr(make_captions, "load_render_settings", lambda: {})
    monkeypatch.setattr(
        make_captions,
        "emoji_events_from_editor_plan",
        lambda settings: None,
    )
    monkeypatch.setattr(
        make_captions,
        "build_visual_edit_plan",
        lambda *args: {"event_count": 0},
    )
    monkeypatch.setattr(
        make_captions,
        "write_visual_edit_plan",
        lambda plan: None,
    )

    assert make_captions.main() == 0
    assert captions_path.exists()
    assert "Dialogue:" not in captions_path.read_text(encoding="utf-8")
    assert json.loads(emoji_path.read_text(encoding="utf-8"))["events"] == []
