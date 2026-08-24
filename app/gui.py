"""
ShortsFactory's desktop app entry point. Deliberately just a launcher
shim (`python app/gui.py`) -- the actual application lives in the
gui_app/ package (see gui_app/main_window.py for the ShortsFactoryWindow
class and how the UI is composed from mixins). Kept this thin so the
documented launch command never has to change even as the GUI itself is
restructured.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui_app.main_window import main


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
