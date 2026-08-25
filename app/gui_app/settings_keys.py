"""
Named QSettings key constants for ShortsFactory's persisted user
preferences. Each key was previously a bare string literal duplicated in
two places -- once where it's loaded at startup (main_window.py) and once
where it's saved on change (the relevant mixin) -- with nothing tying the
two together, so a typo in either one would silently break persistence
for that preference with no error. Both sides now import from here.
"""

from __future__ import annotations

PREVIEW_VOLUME = "preview/volume"
TRANSCRIPTION_QUALITY = "transcription/quality"
EDIT_ENERGY = "render/edit_energy"
FX_INTENSITY = "render/fx_intensity"
SFX_MODE = "render/sfx_mode"
AUTO_CUTS_ENABLED = "render/auto_cuts_enabled"
