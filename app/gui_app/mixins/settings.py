"""
SettingsMixin: change handlers + persistence for the app's editable
render preferences (transcription quality, edit-energy tier, color-grade
fx_intensity, SFX mode) -- each setter updates in-memory state, saves to
QSettings (see settings_keys.py for the key names), and save_render_settings()
serializes the current set into render_settings.json for the pipeline to
read.
"""

from __future__ import annotations

from visual_emphasis import (
    DEFAULT_ENERGY,
    auto_cut_aggression_from_energy,
    coerce_auto_cut_aggression,
    normalize_energy,
    normalize_sfx_mode,
    write_render_settings,
)
from visual_fx import coerce_visual_fx_strength, visual_fx_strength_from_energy
from standard_audio_pitch import coerce_standard_audio_pitch, format_standard_audio_pitch

from ..settings_keys import (
    AUTO_CUTS_ENABLED,
    AUTO_CUT_AGGRESSION,
    EDIT_ENERGY,
    EMOJI_ENABLED,
    FILTERS_ENABLED,
    FX_INTENSITY,
    MIN_EMOJI_EVENTS,
    SFX_MODE,
    STANDARD_AUDIO_PITCH_SEMITONES,
    TRANSCRIPTION_QUALITY,
    VISUAL_FX_STRENGTH,
)


class SettingsMixin:

    def quality_changed(
        self,
        value: str,
    ):

        self.image_quality = str(
            value
            or "BALANCED"
        ).upper()


    def transcription_quality_changed(
        self,
        value: str,
    ):

        quality = str(
            value
            or "AUTO"
        ).upper()

        if quality not in {
            "AUTO",
            "FAST",
            "ACCURATE",
        }:
            quality = "AUTO"

        self.transcription_quality = quality
        self.settings.setValue(
            TRANSCRIPTION_QUALITY,
            quality,
        )

        if self.video_path:
            self.start_transcript_preload()


    def current_transcription_quality(self) -> str:

        quality = str(
            getattr(
                self,
                "transcription_quality",
                "AUTO",
            )
            or "AUTO"
        ).upper()

        if quality not in {
            "AUTO",
            "FAST",
            "ACCURATE",
        }:
            return "AUTO"

        return quality


    def edit_energy_changed(
        self,
        value: str,
    ):

        energy = normalize_energy(
            value
        )

        self.edit_energy = energy
        self.settings.setValue(
            EDIT_ENERGY,
            energy,
        )


    def current_edit_energy(self) -> str:

        return normalize_energy(
            getattr(
                self,
                "edit_energy",
                DEFAULT_ENERGY,
            )
        )


    def auto_cuts_toggled(
        self,
        checked: bool,
    ):

        self.auto_cuts_enabled = bool(checked)
        self.settings.setValue(
            AUTO_CUTS_ENABLED,
            self.auto_cuts_enabled,
        )

        if hasattr(self, "auto_cuts_button"):
            self.auto_cuts_button.setText(
                "AUTO CUTS: ON"
                if self.auto_cuts_enabled
                else "AUTO CUTS: OFF"
            )

        self.refresh_render_features_summary()
        self.save_render_settings()


    def current_auto_cuts_enabled(self) -> bool:

        return bool(
            getattr(
                self,
                "auto_cuts_enabled",
                True,
            )
        )


    def filters_toggled(
        self,
        checked: bool,
    ):

        self.filters_enabled = bool(checked)
        self.settings.setValue(
            FILTERS_ENABLED,
            self.filters_enabled,
        )

        if hasattr(self, "filters_button"):
            self.filters_button.setText(
                "FILTERS: ON"
                if self.filters_enabled
                else "FILTERS: OFF"
            )

        # Nothing left for these to control while filters are off -- grey
        # them out rather than leaving an active-looking slider with no
        # effect.
        for widget_name in (
            "fx_intensity_title",
            "fx_intensity_slider",
            "fx_intensity_label",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(self.filters_enabled)

        self.refresh_program_monitor_filter_preview()
        self.refresh_render_features_summary()
        self.save_render_settings()


    def current_filters_enabled(self) -> bool:

        return bool(
            getattr(
                self,
                "filters_enabled",
                True,
            )
        )


    def emoji_toggled(
        self,
        checked: bool,
    ):

        self.emoji_enabled = bool(checked)
        self.settings.setValue(
            EMOJI_ENABLED,
            self.emoji_enabled,
        )

        if hasattr(self, "emoji_button"):
            self.emoji_button.setText(
                "EMOJI: ON"
                if self.emoji_enabled
                else "EMOJI: OFF"
            )

        if hasattr(self, "timeline"):
            self.timeline.emoji_feature_enabled = self.emoji_enabled
            self.timeline.update()

        if hasattr(self, "update_emoji_preview_overlay") and hasattr(
            self, "player"
        ):
            self.update_emoji_preview_overlay(self.player.position())

        self.refresh_render_features_summary()
        self.save_render_settings()


    def current_emoji_enabled(self) -> bool:

        return bool(
            getattr(
                self,
                "emoji_enabled",
                True,
            )
        )


    def render_features_summary_text(self) -> str:

        def on_off(value: bool) -> str:
            return "ON" if value else "OFF"

        return (
            f"Cuts: {on_off(self.current_auto_cuts_enabled())} · "
            f"Filters: {on_off(self.current_filters_enabled())} · "
            f"Emoji: {on_off(self.current_emoji_enabled())}"
        )


    def refresh_render_features_summary(self):

        if hasattr(self, "render_features_summary_label"):
            self.render_features_summary_label.setText(
                self.render_features_summary_text()
            )


    def min_emoji_events_changed(
        self,
        value: int,
    ):

        count = max(
            0,
            min(
                10,
                int(value),
            ),
        )

        self.min_emoji_events = count
        self.settings.setValue(
            MIN_EMOJI_EVENTS,
            count,
        )
        self.save_render_settings()


    def current_min_emoji_events(self) -> int:

        try:
            return max(
                0,
                min(
                    10,
                    int(
                        getattr(
                            self,
                            "min_emoji_events",
                            0,
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            return 0


    def fx_intensity_changed(
        self,
        value: int,
    ):

        intensity = min(
            2.0,
            max(
                0.0,
                value / 100.0,
            ),
        )

        self.fx_intensity = intensity
        self.settings.setValue(
            FX_INTENSITY,
            intensity,
        )

        if hasattr(
            self,
            "fx_intensity_label",
        ):
            self.fx_intensity_label.setText(
                f"{value}%"
            )

        self.refresh_program_monitor_filter_preview()


    def refresh_program_monitor_filter_preview(self):
        composition = getattr(self, "program_monitor_composition", None)
        if composition is None:
            return

        composition.set_filter_preview(
            enabled=self.current_filters_enabled(),
            energy=self.current_edit_energy(),
            intensity=self.current_fx_intensity(),
        )


    def auto_cut_aggression_changed(self, value: int):
        aggression = coerce_auto_cut_aggression(value)
        self.auto_cut_aggression = aggression
        self.settings.setValue(AUTO_CUT_AGGRESSION, aggression)

        if hasattr(self, "auto_cut_aggression_label"):
            self.auto_cut_aggression_label.setText(str(aggression))

        self.save_render_settings()


    def current_auto_cut_aggression(self) -> int:
        value = getattr(self, "auto_cut_aggression", None)
        if value is None:
            return auto_cut_aggression_from_energy(self.current_edit_energy())
        return coerce_auto_cut_aggression(value)


    def standard_audio_pitch_changed(self, slider_value: int):
        semitones = coerce_standard_audio_pitch(slider_value / 10.0)
        self.standard_audio_pitch_semitones = semitones
        self.settings.setValue(STANDARD_AUDIO_PITCH_SEMITONES, semitones)
        if hasattr(self, "standard_audio_pitch_label"):
            self.standard_audio_pitch_label.setText(format_standard_audio_pitch(semitones))
        self.save_render_settings()
        if hasattr(self, "schedule_standard_pitch_preview_restart"):
            self.schedule_standard_pitch_preview_restart()


    def current_standard_audio_pitch_semitones(self) -> float:
        return coerce_standard_audio_pitch(
            getattr(self, "standard_audio_pitch_semitones", 0.0)
        )


    def visual_fx_strength_changed(self, value: int):
        strength = coerce_visual_fx_strength(value)
        self.visual_fx_strength = strength
        self.settings.setValue(VISUAL_FX_STRENGTH, strength)

        if hasattr(self, "visual_fx_strength_label"):
            self.visual_fx_strength_label.setText(str(strength))

        self.save_render_settings()


    def current_visual_fx_strength(self) -> int:
        value = getattr(self, "visual_fx_strength", None)
        if value is None:
            return visual_fx_strength_from_energy(self.current_edit_energy())
        return coerce_visual_fx_strength(value)


    def current_fx_intensity(self) -> float:

        try:
            intensity = float(
                getattr(
                    self,
                    "fx_intensity",
                    1.0,
                )
            )
        except (TypeError, ValueError):
            return 1.0

        return min(
            2.0,
            max(
                0.0,
                intensity,
            ),
        )


    def sfx_mode_changed(
        self,
        value: str,
    ):

        mode = normalize_sfx_mode(
            value
        )
        self.sfx_mode = mode
        self.settings.setValue(
            SFX_MODE,
            mode,
        )
        self.save_render_settings()


    def current_sfx_mode(self) -> str:

        return normalize_sfx_mode(
            getattr(
                self,
                "sfx_mode",
                "AUTO",
            )
        )


    def save_render_settings(self):

        payload = {
            "edit_energy": self.current_edit_energy(),
            "fx_intensity": self.current_fx_intensity(),
            "visual_fx_strength": self.current_visual_fx_strength(),
            "sfx_mode": self.current_sfx_mode(),
            "auto_cuts_enabled": self.current_auto_cuts_enabled(),
            "auto_cut_aggression": self.current_auto_cut_aggression(),
            "standard_audio_pitch_semitones": self.current_standard_audio_pitch_semitones(),
            "filters_enabled": self.current_filters_enabled(),
            "emoji_enabled": self.current_emoji_enabled(),
            "min_emoji_events": self.current_min_emoji_events(),
            "transcription_quality": self.current_transcription_quality(),
            "source_video": (
                str(
                    self.video_path
                )
                if self.video_path
                else ""
            ),
            "selection_start": self.start_ms / 1000,
            "selection_end": self.end_ms / 1000,
        }

        caption_position_x = getattr(self, "caption_position_x", None)
        caption_position_y = getattr(self, "caption_position_y", None)
        if caption_position_x is not None and caption_position_y is not None:
            payload["caption_position_x"] = caption_position_x
            payload["caption_position_y"] = caption_position_y

        caption_scale = getattr(self, "caption_scale", None)
        if caption_scale is not None:
            payload["caption_scale"] = caption_scale

        try:

            write_render_settings(
                payload
            )

        except OSError as exc:

            if hasattr(
                self,
                "render_log",
            ):
                self.render_log.append(
                    f"WARNING: Could not save render settings: {exc}"
                )


