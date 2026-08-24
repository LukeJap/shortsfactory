"""
ImageAIMixin: local Stable Diffusion (Forge/WebUI) backend status display
and launch control -- checks/refreshes connectivity via
image_backend_status.py and lets the user launch the backend from the
GUI if it isn't already running.
"""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QProcess

from ..constants import ROOT


class ImageAIMixin:

    def display_image_model_name(
        self,
        title: str,
        model_name: str = "",
    ) -> str:

        text = str(
            model_name
            or title
            or ""
        ).strip()

        text = text.replace(
            "\\",
            "/",
        ).split(
            "/"
        )[-1]

        if "[" in text and text.endswith(
            "]"
        ):
            text = text.rsplit(
                "[",
                1,
            )[0].strip()

        for suffix in (
            ".safetensors",
            ".ckpt",
            ".pt",
        ):
            if text.lower().endswith(
                suffix
            ):
                text = text[
                    : -len(
                        suffix
                    )
                ]
                break

        return text or "Image model"


    def update_image_ai_indicator(self):

        if not hasattr(
            self,
            "image_ai_status_label",
        ):
            return

        state = self.image_ai_state

        if (
            hasattr(
                self,
                "visual_asset_process",
            )
            and self.visual_asset_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            state = "generating"

        text_by_state = {
            "not_checked": "● IMAGE AI NOT CHECKED",
            "starting": "● IMAGE AI STARTING",
            "loading_model": "● IMAGE AI LOADING MODEL",
            "offline": "● IMAGE AI OFFLINE",
            "connected_no_model": "● IMAGE AI CONNECTED - NO MODEL",
            "ready": "● IMAGE AI READY",
            "generating": "● IMAGE AI GENERATING",
            "error": "● IMAGE AI ERROR",
        }

        self.image_ai_status_label.setText(
            text_by_state.get(
                state,
                "● IMAGE AI ERROR",
            )
        )

        self.image_ai_status_label.setProperty(
            "state",
            state,
        )

        self.image_ai_status_label.style().unpolish(
            self.image_ai_status_label
        )
        self.image_ai_status_label.style().polish(
            self.image_ai_status_label
        )

        self.update_visual_inspector_buttons()


    def check_image_ai(
        self,
        checked: bool = False,
        set_model: str = "",
    ):

        del checked

        if (
            self.image_status_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        status_script = (
            ROOT
            / "app"
            / "image_backend_status.py"
        )

        if not status_script.exists():
            self.image_ai_state = "error"
            self.update_image_ai_indicator()
            self.visual_status_label.setText(
                "Image AI status checker is not installed."
            )
            return

        self.image_status_stdout = ""
        self.image_status_stderr = ""
        self.pending_image_model_change = set_model

        self.check_image_ai_button.setEnabled(
            False
        )
        self.image_model_combo.setEnabled(
            False
        )

        if set_model:
            self.image_ai_state = "loading_model"
            self.visual_status_label.setText(
                "Changing image model..."
            )
        else:
            self.image_ai_state = "starting"
            self.visual_status_label.setText(
                "Starting or checking Image AI..."
            )
        self.update_image_ai_indicator()

        args = [
            str(
                status_script
            ),
            "--autolaunch",
            "--wait-seconds",
            "180",
        ]

        if set_model:
            args.extend(
                [
                    "--set-model",
                    set_model,
                ]
            )

        self.image_status_process.start(
            sys.executable,
            args,
        )


    def read_image_status_output(self):

        data = (
            self.image_status_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.image_status_stdout += data


    def read_image_status_error(self):

        data = (
            self.image_status_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.image_status_stderr += data


    def image_status_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        del exit_status

        self.check_image_ai_button.setEnabled(
            True
        )

        payload: dict = {}

        try:
            payload = json.loads(
                self.image_status_stdout.strip()
            )
        except json.JSONDecodeError:
            payload = {
                "state": "error",
                "message": "Image AI returned an unreadable status.",
            }

        if exit_code != 0:
            payload["state"] = "error"

        self.image_ai_state = str(
            payload.get(
                "state",
                "error",
            )
            or "error"
        )
        self.image_ai_models = [
            item
            for item in payload.get(
                "models",
                [],
            )
            if isinstance(
                item,
                dict,
            )
        ]
        self.current_image_model_title = str(
            payload.get(
                "current_model_title",
                "",
            )
            or ""
        )

        if self.pending_image_model_change and self.image_ai_state == "ready":
            self.selected_image_model_title = (
                self.current_image_model_title
                or self.pending_image_model_change
            )

        self.pending_image_model_change = ""

        self.populate_image_model_combo()
        self.update_image_ai_indicator()

        if self.image_ai_state == "ready":
            visible_model = self.display_image_model_name(
                self.current_image_model_title,
                str(
                    payload.get(
                        "current_model",
                        "",
                    )
                    or ""
                ),
            )
            self.visual_status_label.setText(
                f"Image AI ready. Model: {visible_model}"
            )
        elif self.image_ai_state == "connected_no_model":
            self.visual_status_label.setText(
                "Image AI connected, but no image model is installed."
            )
        elif self.image_ai_state == "offline":
            self.visual_status_label.setText(
                "Could not connect to Image AI."
            )
        else:
            self.visual_status_label.setText(
                "Image AI error. See render log for details."
            )

        if self.image_status_stderr:
            self.render_log.append(
                self.image_status_stderr.strip()
            )


    def populate_image_model_combo(self):

        if not hasattr(
            self,
            "image_model_combo",
        ):
            return

        self.updating_image_model_combo = True
        self.image_model_combo.clear()

        if self.image_ai_state != "ready" or not self.image_ai_models:
            self.image_model_combo.addItem(
                (
                    "No image model installed"
                    if self.image_ai_state == "connected_no_model"
                    else "Image AI not ready"
                ),
                "",
            )
            self.image_model_combo.setEnabled(
                False
            )
            self.updating_image_model_combo = False
            return

        selected_index = 0
        for index, model in enumerate(
            self.image_ai_models
        ):
            title = str(
                model.get(
                    "title",
                    "",
                )
                or ""
            )
            name = str(
                model.get(
                    "name",
                    "",
                )
                or ""
            )
            self.image_model_combo.addItem(
                self.display_image_model_name(
                    title,
                    name,
                ),
                title,
            )
            if title == self.current_image_model_title:
                selected_index = index

        self.image_model_combo.setCurrentIndex(
            selected_index
        )
        self.selected_image_model_title = str(
            self.image_model_combo.currentData()
            or ""
        )
        self.image_model_combo.setEnabled(
            len(
                self.image_ai_models
            )
            > 0
        )
        self.updating_image_model_combo = False


    def image_model_changed(
        self,
        index: int,
    ):

        if self.updating_image_model_combo:
            return

        model_title = str(
            self.image_model_combo.itemData(
                index
            )
            or ""
        )

        if not model_title:
            return

        self.selected_image_model_title = model_title

        if model_title == self.current_image_model_title:
            return

        self.check_image_ai(
            set_model=model_title,
        )


