from __future__ import annotations

from visual_emphasis import (
    DEFAULT_ENERGY,
    normalize_energy,
    normalize_sfx_mode,
    write_render_settings,
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
            "transcription/quality",
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
            "render/edit_energy",
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
            "render/fx_intensity",
            intensity,
        )

        if hasattr(
            self,
            "fx_intensity_label",
        ):
            self.fx_intensity_label.setText(
                f"{value}%"
            )


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
            "render/sfx_mode",
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
            "sfx_mode": self.current_sfx_mode(),
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


