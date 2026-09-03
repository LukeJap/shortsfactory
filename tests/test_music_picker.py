from pathlib import Path

from gui_app.mixins import music


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
