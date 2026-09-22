from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from gui_app.helpers import show_message


def _app():
    return QApplication.instance() or QApplication([])


def test_show_message_enables_word_wrap_on_the_label(monkeypatch):
    _app()
    captured = {}

    def fake_exec(self):
        label = self.findChild(QLabel, "qt_msgbox_label")
        captured["wrap"] = label.wordWrap() if label is not None else None
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    show_message(None, QMessageBox.Icon.Warning, "Title", "Some long message text.")

    assert captured["wrap"] is True


def test_no_static_qmessagebox_calls_remain_under_gui_app():
    app_dir = Path(__file__).resolve().parent.parent / "app" / "gui_app"
    pattern = re.compile(r"QMessageBox\.(warning|information|critical)\(")
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
