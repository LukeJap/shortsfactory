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
VISUAL_FX_STRENGTH = "render/visual_fx_strength"
SFX_MODE = "render/sfx_mode"
AUTO_CUTS_ENABLED = "render/auto_cuts_enabled"
AUTO_CUT_AGGRESSION = "render/auto_cut_aggression"
STANDARD_AUDIO_PITCH_SEMITONES = "render/standard_audio_pitch_semitones"
FILTERS_ENABLED = "render/filters_enabled"
EMOJI_ENABLED = "render/emoji_enabled"
MIN_EMOJI_EVENTS = "render/min_emoji_events"
RECAP_TARGET_DURATION_SECONDS = "recap/target_duration_seconds"
RECAP_VOICE = "recap/voice"
RECAP_STYLE = "recap/style"
RECAP_SCRIPT_SOURCE = "recap/script_source"
RECAP_SPEED = "recap/speed"
RECAP_NARRATION_GAIN_DB = "recap/narration_gain_db"
RECAP_NARRATION_PITCH_SEMITONES = "recap/narration_pitch_semitones"
RECAP_SOURCE_PITCH_SEMITONES = "recap/source_pitch_semitones"
