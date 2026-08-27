"""
EditorAssetsMixin: SFX/AI-visual "editor asset" clip management on the
timeline -- browsing the local SFX library (SFX_DIR), invoking
sfx_engine.py to (re)plan automatic SFX placement, and the shared
editor_asset_plan.json read/write context (ensure_current_editor_asset_
context()) that also backs the AI visual slots system.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from editor_asset_plan import (
    clips_of_kind,
    editor_plan_context_matches,
    load_editor_asset_plan,
    save_editor_asset_plan,
    set_editor_plan_context,
    upsert_clip,
)
from emoji_overlay import normalize_emoji
from make_captions import load_local_reaction_assets
from sfx_engine import asset_metadata_for_path

from ..constants import ROOT

SFX_DIR = ROOT / "assets" / "sfx"


class EditorAssetsMixin:

    def current_editor_asset_context(self) -> tuple[str, float, float]:

        return (
            str(self.video_path) if self.video_path else "",
            self.start_ms / 1000,
            self.end_ms / 1000,
        )


    def editor_asset_context_matches_current_selection(self) -> bool:

        source_video, selection_start, selection_end = (
            self.current_editor_asset_context()
        )
        if (
            not source_video
            or selection_end <= selection_start
        ):
            return False

        return editor_plan_context_matches(
            self.editor_asset_plan,
            source_video,
            selection_start,
            selection_end,
        )


    def ensure_current_editor_asset_context(
        self,
        *,
        clear_on_change: bool,
    ):

        if not self.video_path or self.end_ms <= self.start_ms:
            return

        self.editor_asset_plan = set_editor_plan_context(
            self.editor_asset_plan,
            self.video_path,
            self.start_ms / 1000,
            self.end_ms / 1000,
            clear_clips_on_change=clear_on_change,
        )
        self.selected_sfx_clip_id = None
        self.selected_emoji_clip_id = None
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def retarget_editor_asset_context_to_current_selection(self):

        if (
            not self.video_path
            or self.end_ms <= self.start_ms
            or not self.editor_asset_plan.get(
                "clips",
                [],
            )
        ):
            return

        self.editor_asset_plan = set_editor_plan_context(
            self.editor_asset_plan,
            self.video_path,
            self.start_ms / 1000,
            self.end_ms / 1000,
            clear_clips_on_change=False,
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def load_editor_asset_plan_state(self):

        self.editor_asset_plan = load_editor_asset_plan()
        self.refresh_editor_asset_timeline()


    def save_editor_asset_plan_state(self):

        save_editor_asset_plan(
            self.editor_asset_plan
        )


    def visible_editor_asset_clips(self) -> list[dict]:

        if not self.editor_asset_context_matches_current_selection():
            return []

        # editor_asset_plan.json survives between app sessions. A previous
        # session can therefore contain AI_VISUAL clips whose source/range
        # happens to match a newly selected Find Best Clips candidate. Those
        # stale clips must not reappear before the current session has actually
        # planned visuals. Keep the persisted data untouched, but only expose
        # AI_VISUAL clips represented by the current in-memory visual plan.
        current_visual_clip_ids = {
            self.visual_clip_id(
                slot,
                index,
            )
            for index, slot in enumerate(
                self.visual_plan_slots
            )
            if isinstance(
                slot,
                dict,
            )
        }

        clips = []
        for clip in self.editor_asset_plan.get(
            "clips",
            [],
        ):
            if not isinstance(
                clip,
                dict,
            ):
                continue
            if bool(
                clip.get(
                    "deleted",
                    False,
                )
            ):
                continue

            kind = str(
                clip.get(
                    "kind",
                    "",
                )
                or ""
            ).upper()
            if kind not in {
                "SFX",
                "AI_VISUAL",
                "EMOJI",
                "VOICEOVER",
            }:
                continue

            if kind == "AI_VISUAL":
                clip_id = str(
                    clip.get(
                        "id",
                        "",
                    )
                    or ""
                )
                if clip_id not in current_visual_clip_ids:
                    continue

            clips.append(
                clip
            )
        return clips


    def refresh_editor_asset_timeline(self):

        if hasattr(
            self,
            "timeline",
        ):
            self.timeline.set_asset_clips(
                self.visible_editor_asset_clips()
            )

            selected_asset_id = (
                self.selected_sfx_clip_id
                or self.selected_emoji_clip_id
            )
            if (
                self.selected_visual_slot_index is not None
                and 0
                <= self.selected_visual_slot_index
                < len(
                    self.visual_plan_slots
                )
            ):
                slot = self.visual_plan_slots[
                    self.selected_visual_slot_index
                ]
                if isinstance(
                    slot,
                    dict,
                ):
                    selected_asset_id = self.visual_clip_id(
                        slot,
                        self.selected_visual_slot_index,
                    )

            self.timeline.set_selected_asset_clip(
                selected_asset_id
            )
        self.update_sfx_inspector()
        self.update_emoji_inspector()
        if hasattr(self, "ai_visual_preview_overlay"):
            self.update_ai_visual_preview_overlay(
                self.player.position()
            )
            self.update_emoji_preview_overlay(
                self.player.position()
            )
            self.update_caption_preview_overlay(
                self.player.position()
            )


    def find_editor_clip(
        self,
        kind: str,
        clip_id: str,
    ) -> dict | None:

        normalized_kind = str(
            kind
            or ""
        ).upper()
        normalized_id = str(
            clip_id
            or ""
        )
        for clip in self.editor_asset_plan.get(
            "clips",
            [],
        ):
            if not isinstance(
                clip,
                dict,
            ):
                continue
            if str(
                clip.get(
                    "kind",
                    "",
                )
                or ""
            ).upper() != normalized_kind:
                continue
            if str(
                clip.get(
                    "id",
                    "",
                )
                or ""
            ) == normalized_id:
                return clip
        return None


    def selected_sfx_clip(self) -> dict | None:

        if not self.editor_asset_context_matches_current_selection():
            return None
        if not self.selected_sfx_clip_id:
            return None
        return self.find_editor_clip(
            "SFX",
            self.selected_sfx_clip_id,
        )


    def selected_emoji_clip(self) -> dict | None:

        if not self.editor_asset_context_matches_current_selection():
            return None
        if not self.selected_emoji_clip_id:
            return None
        return self.find_editor_clip(
            "EMOJI",
            self.selected_emoji_clip_id,
        )


    def editor_asset_clip_selected(
        self,
        kind: str,
        clip_id: str,
    ):

        normalized_kind = str(
            kind
            or ""
        ).upper()
        normalized_id = str(
            clip_id
            or ""
        )

        if normalized_kind == "AI_VISUAL":
            self.selected_sfx_clip_id = None
            self.selected_emoji_clip_id = None

            for index, slot in enumerate(
                self.visual_plan_slots
            ):
                if not isinstance(
                    slot,
                    dict,
                ):
                    continue
                if self.visual_clip_id(
                    slot,
                    index,
                ) != normalized_id:
                    continue

                self.selected_visual_slot_index = index
                self.timeline.set_selected_asset_clip(
                    normalized_id
                )

                try:
                    start_ms = int(
                        round(
                            float(
                                slot.get(
                                    "start",
                                    0.0,
                                )
                                or 0.0
                            )
                            * 1000
                        )
                    )
                    end_ms = int(
                        round(
                            float(
                                slot.get(
                                    "end",
                                    slot.get(
                                        "start",
                                        0.0,
                                    ),
                                )
                            )
                            * 1000
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    start_ms = self.player.position()
                    end_ms = start_ms

                self.player.setPosition(
                    start_ms
                )
                self.timeline.setValue(
                    start_ms
                )
                self.reveal_timeline_range(
                    start_ms,
                    end_ms,
                )
                self.refresh_visual_plan_display()
                self.load_selected_visual_into_inspector()
                self.refresh_editor_asset_timeline()
                return

            return

        if normalized_kind == "EMOJI":
            self.selected_visual_slot_index = None
            self.selected_sfx_clip_id = None
            self.refresh_visual_plan_display()

            self.selected_emoji_clip_id = str(
                clip_id
                or ""
            )
            self.timeline.set_selected_asset_clip(
                self.selected_emoji_clip_id
            )
            self.update_emoji_inspector()

            clip = self.selected_emoji_clip()
            if clip is not None:
                try:
                    start_ms = int(
                        round(
                            float(
                                clip.get(
                                    "start",
                                    0.0,
                                )
                                or 0.0
                            )
                            * 1000
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    start_ms = self.player.position()

                self.player.setPosition(
                    start_ms
                )
                self.timeline.setValue(
                    start_ms
                )
                self.reveal_timeline_time(
                    start_ms
                )
                self.update_emoji_preview_overlay(
                    self.player.position()
                )

            return

        if normalized_kind != "SFX":
            return

        self.selected_visual_slot_index = None
        self.selected_emoji_clip_id = None
        self.refresh_visual_plan_display()

        self.selected_sfx_clip_id = str(
            clip_id
            or ""
        )
        self.timeline.set_selected_asset_clip(
            self.selected_sfx_clip_id
        )
        self.update_sfx_inspector()

        clip = self.selected_sfx_clip()
        if clip is not None:
            try:
                start_ms = int(
                    round(
                        float(
                            clip.get(
                                "start",
                                0.0,
                            )
                            or 0.0
                        )
                        * 1000
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                start_ms = self.player.position()

            self.player.setPosition(
                start_ms
            )
            self.timeline.setValue(
                start_ms
            )
            self.reveal_timeline_time(
                start_ms
            )
            self.play_sfx_preview_clip(
                clip
            )


    def editor_asset_clip_changed(
        self,
        kind: str,
        clip: object,
    ):

        if not isinstance(
            clip,
            dict,
        ):
            return

        normalized_kind = str(
            kind
            or clip.get(
                "kind",
                "",
            )
            or ""
        ).upper()

        if normalized_kind == "AI_VISUAL":
            clip["kind"] = "AI_VISUAL"

            try:
                start = float(
                    clip.get(
                        "start",
                        0.0,
                    )
                    or 0.0
                )
                end = float(
                    clip.get(
                        "end",
                        start,
                    )
                    or start
                )
            except (
                TypeError,
                ValueError,
            ):
                start = 0.0
                end = 0.2

            end = max(
                start + 0.2,
                end,
            )
            clip["start"] = round(
                start,
                3,
            )
            clip["end"] = round(
                end,
                3,
            )
            clip["duration"] = round(
                end - start,
                3,
            )
            clip["manual_override"] = True
            clip["locked"] = True
            clip["origin"] = (
                clip.get(
                    "origin",
                    "manual",
                )
                or "manual"
            )

            self.editor_asset_plan = upsert_clip(
                self.editor_asset_plan,
                clip,
            )
            self.save_editor_asset_plan_state()

            clip_id = str(
                clip.get(
                    "id",
                    "",
                )
                or ""
            )
            self.selected_sfx_clip_id = None
            self.selected_emoji_clip_id = None

            for index, slot in enumerate(
                self.visual_plan_slots
            ):
                if not isinstance(
                    slot,
                    dict,
                ):
                    continue
                if self.visual_clip_id(
                    slot,
                    index,
                ) != clip_id:
                    continue

                slot["start"] = clip["start"]
                slot["end"] = clip["end"]
                slot["duration"] = clip["duration"]
                slot["enabled"] = bool(
                    clip.get(
                        "active",
                        slot.get(
                            "enabled",
                            True,
                        ),
                    )
                )
                slot["user_modified"] = True
                self.user_visual_edits = True
                self.selected_visual_slot_index = index

                self.save_ai_visual_plan()
                self.refresh_visual_plan_display()
                self.load_selected_visual_into_inspector()
                self.refresh_editor_asset_timeline()
                return

            return

        if normalized_kind == "EMOJI":
            clip["kind"] = "EMOJI"
            clip["manual_override"] = True
            clip["locked"] = True
            self.editor_asset_plan = upsert_clip(
                self.editor_asset_plan,
                clip,
            )
            self.save_editor_asset_plan_state()
            self.selected_emoji_clip_id = str(
                clip.get(
                    "id",
                    "",
                )
                or ""
            )
            self.update_emoji_inspector()
            if hasattr(self, "ai_visual_preview_overlay"):
                self.update_emoji_preview_overlay(
                    self.player.position()
                )
            return

        if normalized_kind != "SFX":
            return

        clip["kind"] = "SFX"
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.save_editor_asset_plan_state()
        self.selected_sfx_clip_id = str(
            clip.get(
                "id",
                "",
            )
            or ""
        )
        self.selected_emoji_clip_id = None
        self.update_sfx_inspector()


    def editor_asset_clip_double_clicked(
        self,
        kind: str,
        clip_id: str,
    ):

        normalized_kind = str(
            kind
            or ""
        ).upper()

        if normalized_kind == "AI_VISUAL":
            self.editor_asset_clip_selected(
                kind,
                clip_id,
            )
            return

        if normalized_kind == "EMOJI":
            self.selected_visual_slot_index = None
            self.selected_sfx_clip_id = None
            self.selected_emoji_clip_id = str(
                clip_id
                or ""
            )
            self.update_emoji_inspector()
            self.swap_selected_emoji_clip()
            return

        if normalized_kind != "SFX":
            return

        self.selected_sfx_clip_id = str(
            clip_id
            or ""
        )
        self.selected_emoji_clip_id = None
        self.update_sfx_inspector()
        self.swap_selected_sfx_clip()


    def update_sfx_inspector(self):

        if not hasattr(
            self,
            "sfx_clip_label",
        ):
            return

        clip = self.selected_sfx_clip()
        if clip is None or bool(
            clip.get(
                "deleted",
                False,
            )
        ):
            if hasattr(
                self,
                "sfx_context_frame",
            ):
                self.sfx_context_frame.setVisible(
                    False
                )
            self.sfx_clip_label.setText(
                "No SFX selected"
            )
            self.swap_sfx_button.setEnabled(False)
            self.disable_sfx_button.setEnabled(False)
            self.delete_sfx_button.setEnabled(False)
            self.sfx_volume_slider.setEnabled(False)
            self.disable_sfx_button.setText("Disable")
            return

        self.sfx_context_frame.setVisible(
            True
        )
        label = str(
            clip.get(
                "label",
                "SFX",
            )
            or "SFX"
        )
        self.sfx_clip_label.setText(
            f"SFX: {label}"
        )
        self.swap_sfx_button.setEnabled(True)
        self.disable_sfx_button.setEnabled(True)
        self.delete_sfx_button.setEnabled(True)
        self.disable_sfx_button.setText(
            "Enable"
            if clip.get(
                "active",
                True,
            )
            is False
            else "Disable"
        )

        try:
            volume = float(
                clip.get(
                    "volume",
                    0.25,
                )
                or 0.25
            )
        except (
            TypeError,
            ValueError,
        ):
            volume = 0.25
        self.sfx_volume_slider.blockSignals(True)
        self.sfx_volume_slider.setValue(
            int(
                round(
                    max(
                        0.0,
                        min(
                            0.8,
                            volume,
                        ),
                    )
                    * 100
                )
            )
        )
        self.sfx_volume_slider.blockSignals(False)
        self.sfx_volume_slider.setEnabled(True)


    def available_sfx_files(self) -> list[Path]:

        sfx_dir = SFX_DIR
        if not sfx_dir.exists():
            return []

        supported = {
            ".wav",
            ".mp3",
            ".ogg",
            ".m4a",
            ".aac",
            ".flac",
        }
        return sorted(
            [
                path
                for path in sfx_dir.rglob("*")
                if path.is_file()
                and path.suffix.lower() in supported
            ],
            key=lambda path: str(
                path.relative_to(
                    sfx_dir
                )
            ).lower(),
        )


    def swap_selected_sfx_clip(self):

        clip = self.selected_sfx_clip()
        if clip is None:
            return

        paths = self.available_sfx_files()
        if not paths:
            QMessageBox.information(
                self,
                "Swap SFX",
                "Add sound files to assets/sfx first.",
            )
            return

        sfx_dir = SFX_DIR
        labels = [
            str(
                path.relative_to(
                    sfx_dir
                )
            )
            for path in paths
        ]

        current_path = str(
            clip.get(
                "asset_path",
                "",
            )
            or ""
        )
        selected_index = 0
        for index, path in enumerate(
            paths
        ):
            if str(path) == current_path:
                selected_index = index
                break

        choice, accepted = QInputDialog.getItem(
            self,
            "Swap SFX",
            "Sound:",
            labels,
            selected_index,
            False,
        )
        if not accepted or not choice:
            return

        chosen_path = paths[
            labels.index(
                choice
            )
        ]
        metadata = asset_metadata_for_path(
            chosen_path,
            fallback_category=str(
                clip.get(
                    "category",
                    "",
                )
                or ""
            ),
        )
        clip["asset_path"] = str(
            chosen_path
        )
        clip["asset_source"] = "manual_swap"
        clip["category"] = str(
            metadata.get(
                "category",
                clip.get(
                    "category",
                    "",
                ),
            )
            or ""
        )
        clip["label"] = str(
            metadata.get(
                "label",
                clip.get(
                    "label",
                    "SFX",
                ),
            )
            or "SFX"
        )
        clip["asset_filename"] = str(
            metadata.get(
                "asset_filename",
                chosen_path.name,
            )
            or chosen_path.name
        )
        clip["description"] = str(
            metadata.get(
                "description",
                chosen_path.stem,
            )
            or chosen_path.stem
        )
        clip["manual_override"] = True
        clip["locked"] = True

        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()
        self.play_sfx_preview_clip(
            clip
        )


    def toggle_selected_sfx_clip(self):

        clip = self.selected_sfx_clip()
        if clip is None:
            return

        clip["active"] = not bool(
            clip.get(
                "active",
                True,
            )
        )
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def delete_selected_sfx_clip(self):

        clip = self.selected_sfx_clip()
        if clip is None:
            return

        clip["active"] = False
        clip["deleted"] = True
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.selected_sfx_clip_id = None
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def sfx_volume_changed(
        self,
        value: int,
    ):

        clip = self.selected_sfx_clip()
        if clip is None:
            return

        clip["volume"] = round(
            max(
                0,
                min(
                    80,
                    int(
                        value
                    ),
                ),
            )
            / 100,
            3,
        )
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.save_editor_asset_plan_state()
        self.update_sfx_inspector()


    def update_emoji_inspector(self):

        if not hasattr(
            self,
            "emoji_clip_label",
        ):
            return

        clip = self.selected_emoji_clip()
        if clip is None or bool(
            clip.get(
                "deleted",
                False,
            )
        ):
            if hasattr(
                self,
                "emoji_context_frame",
            ):
                self.emoji_context_frame.setVisible(
                    False
                )
            self.emoji_clip_label.setText(
                "No emoji selected"
            )
            self.swap_emoji_button.setEnabled(False)
            self.disable_emoji_button.setEnabled(False)
            self.delete_emoji_button.setEnabled(False)
            self.disable_emoji_button.setText("Disable")
            return

        self.emoji_context_frame.setVisible(
            True
        )
        label = str(
            clip.get(
                "label",
                clip.get(
                    "emoji",
                    "Emoji",
                ),
            )
            or "Emoji"
        )
        self.emoji_clip_label.setText(
            f"Emoji: {label}"
        )
        self.swap_emoji_button.setEnabled(True)
        self.disable_emoji_button.setEnabled(True)
        self.delete_emoji_button.setEnabled(True)
        self.disable_emoji_button.setText(
            "Enable"
            if clip.get(
                "active",
                True,
            )
            is False
            else "Disable"
        )


    def open_editor_emoji_picker(
        self,
        clip_id: str,
    ):

        clip = self.find_editor_clip(
            "EMOJI",
            clip_id,
        )
        if clip is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Change Emoji Reaction")
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        hint = QLabel(
            f"Currently: {clip.get('emoji', '?')} "
            f"(\"{clip.get('label', '')}\")"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(320)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)

        assets = load_local_reaction_assets()
        columns = 5

        for index, asset in enumerate(assets):
            path = asset["path"]
            button = QPushButton()
            button.setToolTip(
                asset.get(
                    "description",
                    path.stem,
                )
            )
            button.setFixedSize(64, 64)
            button.setIconSize(QSize(52, 52))

            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                button.setIcon(QIcon(pixmap))
            else:
                button.setText(path.stem[:6])

            button.clicked.connect(
                lambda checked=False, chosen_path=path, chosen_description=asset.get(
                    "description", path.stem
                ): self.apply_editor_emoji_picker_choice(
                    clip_id,
                    dialog,
                    asset_path=chosen_path,
                    description=chosen_description,
                )
            )
            grid.addWidget(button, index // columns, index % columns)

        scroll.setWidget(grid_widget)
        layout.addWidget(scroll)

        custom_row = QHBoxLayout()
        custom_label = QLabel("Or type a custom emoji:")
        custom_input = QLineEdit()
        custom_input.setPlaceholderText("e.g. \U0001f525")
        custom_input.setMaximumWidth(80)
        custom_use_button = QPushButton("Use")
        custom_use_button.clicked.connect(
            lambda: self.apply_editor_emoji_picker_choice(
                clip_id,
                dialog,
                custom_emoji=custom_input.text(),
            )
        )
        custom_row.addWidget(custom_label)
        custom_row.addWidget(custom_input)
        custom_row.addWidget(custom_use_button)
        custom_row.addStretch()
        layout.addLayout(custom_row)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        layout.addWidget(cancel_button)

        dialog.exec()


    def apply_editor_emoji_picker_choice(
        self,
        clip_id: str,
        dialog,
        asset_path=None,
        description: str = "",
        custom_emoji: str = "",
    ):

        clip = self.find_editor_clip(
            "EMOJI",
            clip_id,
        )
        if clip is None:
            dialog.reject()
            return

        if asset_path is not None:
            clip["asset_path"] = str(
                asset_path
            )
            clip["asset_description"] = description
            clip["asset_type"] = "local"
            clip["emoji"] = description
            clip["label"] = description
        else:
            emoji = normalize_emoji(
                custom_emoji
            )
            if not emoji:
                dialog.reject()
                return
            clip["emoji"] = emoji
            clip["label"] = emoji
            clip.pop("asset_path", None)
            clip.pop("asset_description", None)
            clip.pop("asset_type", None)

        clip["manual_override"] = True
        clip["content_override"] = True
        clip["locked"] = True

        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()

        dialog.accept()


    def swap_selected_emoji_clip(self):

        clip = self.selected_emoji_clip()
        if clip is None:
            return

        self.open_editor_emoji_picker(
            self.selected_emoji_clip_id
        )


    def toggle_selected_emoji_clip(self):

        clip = self.selected_emoji_clip()
        if clip is None:
            return

        clip["active"] = not bool(
            clip.get(
                "active",
                True,
            )
        )
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def delete_selected_emoji_clip(self):

        clip = self.selected_emoji_clip()
        if clip is None:
            return

        clip["active"] = False
        clip["deleted"] = True
        clip["manual_override"] = True
        clip["locked"] = True
        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            clip,
        )
        self.selected_emoji_clip_id = None
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def play_sfx_preview_clip(
        self,
        clip: dict,
    ):

        if clip.get(
            "active",
            True,
        ) is False:
            return

        asset_path = Path(
            str(
                clip.get(
                    "asset_path",
                    "",
                )
                or ""
            )
        )
        if not asset_path.exists():
            return

        try:
            volume = float(
                clip.get(
                    "volume",
                    0.25,
                )
                or 0.25
            )
        except (
            TypeError,
            ValueError,
        ):
            volume = 0.25

        self.sfx_preview_audio.setVolume(
            max(
                0.0,
                min(
                    1.0,
                    volume
                    * max(
                        0.0,
                        min(
                            1.0,
                            self.preview_volume / 100,
                        ),
                    ),
                ),
            )
        )
        self.sfx_preview_player.stop()
        self.sfx_preview_player.setSource(
            QUrl.fromLocalFile(
                str(
                    asset_path
                )
            )
        )
        try:
            trim_in_ms = int(
                round(
                    float(
                        clip.get(
                            "trim_in",
                            0.0,
                        )
                        or 0.0
                    )
                    * 1000
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            trim_in_ms = 0
        self.sfx_preview_player.setPosition(
            max(
                0,
                trim_in_ms,
            )
        )
        self.sfx_preview_player.play()

        try:
            duration_ms = int(
                round(
                    float(
                        clip.get(
                            "duration",
                            0.25,
                        )
                        or 0.25
                    )
                    * 1000
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            duration_ms = 250
        QTimer.singleShot(
            max(
                90,
                duration_ms,
            ),
            self.sfx_preview_player.stop,
        )


    def trigger_sfx_previews(
        self,
        position_ms: int,
    ):

        if not self.editor_asset_context_matches_current_selection():
            self.sfx_preview_triggered.clear()
            return

        if (
            self.player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState
        ):
            self.sfx_preview_triggered.clear()
            return

        active_ids = set()
        for clip in clips_of_kind(
            self.editor_asset_plan,
            "SFX",
            active_only=True,
        ):
            if not isinstance(
                clip,
                dict,
            ) or bool(
                clip.get(
                    "deleted",
                    False,
                )
            ):
                continue

            clip_id = str(
                clip.get(
                    "id",
                    "",
                )
                or ""
            )
            if not clip_id:
                continue

            try:
                start_ms = int(
                    round(
                        float(
                            clip.get(
                                "start",
                                0.0,
                            )
                            or 0.0
                        )
                        * 1000
                    )
                )
                end_ms = int(
                    round(
                        float(
                            clip.get(
                                "end",
                                start_ms / 1000,
                            )
                            or start_ms / 1000
                        )
                        * 1000
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            if start_ms <= int(position_ms) <= end_ms:
                active_ids.add(
                    clip_id
                )
                if clip_id not in self.sfx_preview_triggered:
                    self.play_sfx_preview_clip(
                        clip
                    )
                    self.sfx_preview_triggered.add(
                        clip_id
                    )

        self.sfx_preview_triggered.intersection_update(
            active_ids
        )


    def update_sfx_button_state(self):

        if not hasattr(
            self,
            "generate_sfx_button",
        ):
            return

        running = (
            self.sfx_process.state()
            != QProcess.ProcessState.NotRunning
        )
        self.generate_sfx_button.setEnabled(
            bool(
                self.video_path
                and self.end_ms > self.start_ms
                and not running
            )
        )


    def append_sfx_log(
        self,
        data: str,
    ):

        if not data:
            return

        self.render_log.moveCursor(
            self.render_log.textCursor().MoveOperation.End
        )
        self.render_log.insertPlainText(
            data
        )
        scrollbar = self.render_log.verticalScrollBar()
        scrollbar.setValue(
            scrollbar.maximum()
        )


    def read_sfx_output(self):

        data = (
            self.sfx_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        self.append_sfx_log(
            data
        )


    def read_sfx_error(self):

        data = (
            self.sfx_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        self.append_sfx_log(
            data
        )


    def generate_sfx(self):

        if not self.video_path or self.end_ms <= self.start_ms:
            return

        if (
            self.sfx_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        sfx_script = ROOT / "app" / "sfx_engine.py"
        if not sfx_script.exists():
            self.render_log.append(
                "SFX engine is not installed."
            )
            return

        self.ensure_current_editor_asset_context(
            clear_on_change=True
        )
        self.save_render_settings()
        self.generate_sfx_button.setEnabled(False)
        self.generate_sfx_button.setText("Generating...")

        self.render_log.append("")
        self.render_log.append("=== EDITOR SFX GENERATION ===")
        self.render_log.append(
            f"Sound FX: {self.current_sfx_mode()}"
        )
        self.render_log.append(
            "Selection: "
            f"{self.start_ms / 1000:.3f}s -> "
            f"{self.end_ms / 1000:.3f}s"
        )

        self.sfx_process.start(
            sys.executable,
            [
                str(sfx_script),
                "--editor-plan",
                "--selection-start",
                f"{self.start_ms / 1000:.3f}",
                "--selection-end",
                f"{self.end_ms / 1000:.3f}",
            ],
        )


    def sfx_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        del exit_status

        self.generate_sfx_button.setText("Generate SFX")
        self.update_sfx_button_state()

        if exit_code != 0:
            self.render_log.append(
                f"SFX generation failed with exit code {exit_code}."
            )
            return

        sfx_plan_path = ROOT / "output" / "sfx_plan.json"
        event_count = 0
        try:
            payload = json.loads(
                sfx_plan_path.read_text(
                    encoding="utf-8"
                )
            )
            event_count = int(
                payload.get(
                    "event_count",
                    0,
                )
                or 0
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            event_count = 0

        self.render_log.append(
            f"SFX plan ready: {event_count} clip(s)."
        )
        self.load_editor_asset_plan_state()
        self.suggestions_label.setText(
            f"SFX plan ready: {event_count} clip(s). Select orange clips on the SFX lane to edit them."
        )


    def open_sfx_folder(self):

        sfx_dir = SFX_DIR
        try:
            sfx_dir.mkdir(
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "SFX Folder",
                f"Could not create SFX folder:\n{exc}",
            )
            return

        if not QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                str(sfx_dir)
            )
        ):
            QMessageBox.warning(
                self,
                "SFX Folder",
                "Could not open the SFX folder.",
            )


    def update_emoji_generate_button_state(self):

        if not hasattr(
            self,
            "generate_emoji_button",
        ):
            return

        running = (
            self.emoji_generate_process.state()
            != QProcess.ProcessState.NotRunning
        )
        self.generate_emoji_button.setEnabled(
            bool(
                self.video_path
                and self.end_ms > self.start_ms
                and not running
            )
        )


    def append_emoji_generate_log(
        self,
        data: str,
    ):

        if not data:
            return

        self.render_log.moveCursor(
            self.render_log.textCursor().MoveOperation.End
        )
        self.render_log.insertPlainText(
            data
        )
        scrollbar = self.render_log.verticalScrollBar()
        scrollbar.setValue(
            scrollbar.maximum()
        )


    def read_emoji_generate_output(self):

        data = (
            self.emoji_generate_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        self.append_emoji_generate_log(
            data
        )


    def read_emoji_generate_error(self):

        data = (
            self.emoji_generate_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        self.append_emoji_generate_log(
            data
        )


    def generate_emoji(self):

        if not self.video_path or self.end_ms <= self.start_ms:
            return

        if (
            self.emoji_generate_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        emoji_script = ROOT / "app" / "emoji_planner.py"
        if not emoji_script.exists():
            self.render_log.append(
                "Emoji planner is not installed."
            )
            return

        transcript_path = ROOT / "output" / "subtitles.json"
        if not transcript_path.exists():
            self.render_log.append(
                "No transcript loaded yet -- run Find Best Clips first."
            )
            return

        self.ensure_current_editor_asset_context(
            clear_on_change=True
        )
        self.save_render_settings()
        self.generate_emoji_button.setEnabled(False)
        self.generate_emoji_button.setText("Generating...")

        self.render_log.append("")
        self.render_log.append("=== EDITOR EMOJI GENERATION ===")
        self.render_log.append(
            "Selection: "
            f"{self.start_ms / 1000:.3f}s -> "
            f"{self.end_ms / 1000:.3f}s"
        )

        self.emoji_generate_process.start(
            sys.executable,
            [
                str(emoji_script),
                "--transcript",
                str(transcript_path),
                "--start",
                f"{self.start_ms / 1000:.3f}",
                "--end",
                f"{self.end_ms / 1000:.3f}",
                "--energy",
                self.current_edit_energy(),
                "--min-events",
                str(self.current_min_emoji_events()),
                "--editor-plan",
            ],
        )


    def emoji_generate_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        del exit_status

        self.generate_emoji_button.setText("Generate Emoji")
        self.update_emoji_generate_button_state()

        if exit_code != 0:
            self.render_log.append(
                f"Emoji generation failed with exit code {exit_code}."
            )
            return

        self.load_editor_asset_plan_state()
        event_count = len(
            clips_of_kind(
                self.editor_asset_plan,
                "EMOJI",
                active_only=True,
            )
        )
        self.render_log.append(
            f"Emoji plan ready: {event_count} event(s)."
        )
        if hasattr(self, "player"):
            self._emoji_events_cache = None
            self.update_emoji_preview_overlay(
                self.player.position()
            )


