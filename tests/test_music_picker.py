from pathlib import Path

from gui_app.mixins import music
from music_overlay import (
    music_gain_at_time,
    music_volume_expression,
    normalized_music_gain,
)


def test_music_picker_prefers_the_project_music_directory(monkeypatch, tmp_path):
    music_directory = tmp_path / "assets" / "music"
    music_directory.mkdir(parents=True)
    monkeypatch.setattr(music, "ROOT", tmp_path)

    assert music.music_picker_start_directory() == music_directory


def test_music_picker_falls_back_to_the_project_root(monkeypatch, tmp_path):
    monkeypatch.setattr(music, "ROOT", tmp_path)

    assert music.music_picker_start_directory() == Path(tmp_path)


def test_choose_music_resets_the_dialog_start_directory_each_time(monkeypatch, tmp_path):
    music_directory = tmp_path / "assets" / "music"
    music_directory.mkdir(parents=True)
    monkeypatch.setattr(music, "ROOT", tmp_path)
    starts = []

    def get_open_file_name(*args):
        starts.append(args[2])
        return "", ""

    monkeypatch.setattr(music.QFileDialog, "getOpenFileName", get_open_file_name)
    window = music.MusicMixin()

    window.choose_music()
    window.choose_music()

    assert starts == [str(music_directory), str(music_directory)]


def test_editor_music_clock_uses_selection_start_and_output_speed():
    assert music.editor_position_to_music_position(12_000, 3_000, 1.5) == 6_000
    assert music.editor_position_to_music_position(2_000, 3_000, 1.5) == 0
    assert music.editor_position_to_music_position(4_250, 0, 1.0) == 4_250


def test_preview_and_render_share_the_same_linear_music_gain():
    assert normalized_music_gain(0.18) == 0.18
    assert music_volume_expression(0.18, []) == "0.1800"

    window = music.MusicMixin()
    window.music_volume = 18
    window.preview_volume = 50
    assert window.current_music_preview_gain() == 0.09


def test_live_music_uses_the_render_ducking_factor_during_sfx():
    events = [{"start": 1.0, "end": 1.3}]
    assert music_gain_at_time(0.2, events, 0.5) == 0.2
    assert music_gain_at_time(0.2, events, 1.1) == 0.116

    window = music.MusicMixin()
    window.music_volume = 20
    window.preview_volume = 100
    window.visible_editor_asset_clips = lambda: [
        {"kind": "SFX", "start": 1.0, "end": 1.3, "active": True}
    ]
    assert window.current_music_preview_gain(1_100) == 0.116


def test_music_volume_change_updates_live_audio_output_immediately():
    class AudioOutput:
        def __init__(self):
            self.volume = None

        def setVolume(self, volume):
            self.volume = volume

    class Label:
        def setText(self, text):
            self.text = text

    window = music.MusicMixin()
    window.preview_volume = 100
    window.music_preview_audio = AudioOutput()
    window.music_volume_label = Label()

    window.music_volume_changed(27)

    assert window.music_preview_audio.volume == 0.27
    assert window.music_volume_label.text == "27%"


def test_music_seek_uses_output_clock_and_wraps_at_track_duration():
    class Transport:
        def playbackRate(self):
            return 1.5

        def position(self):
            return 10_500

    class Preview:
        def __init__(self):
            self.position_ms = 0

        def duration(self):
            return 3_000

        def position(self):
            return self.position_ms

        def setPosition(self, position):
            self.position_ms = position

    window = music.MusicMixin()
    window.player = Transport()
    window.start_ms = 3_000
    window.music_path = Path("music.mp3")
    window.music_preview_player = Preview()

    window.sync_music_preview(force=True)

    # (10.5s - 3s selection start) / 1.5x = 5s, looped over 3s = 2s.
    assert window.music_preview_player.position_ms == 2_000
