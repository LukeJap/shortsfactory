from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap

from editor_asset_plan import clips_of_kind


class AIVisualPreviewMixin:

    def hide_ai_visual_preview_overlay(self):
        if hasattr(self, "ai_visual_preview_overlay"):
            self.ai_visual_preview_overlay.hide()
            self.ai_visual_preview_overlay.clear()
        if hasattr(self, "ai_visual_preview_dim"):
            self.ai_visual_preview_dim.hide()
        self.active_visual_preview_clip_id = None
        self.active_visual_preview_signature = None
        self.active_visual_preview_layout_signature = None
        self.active_visual_preview_pixmap = QPixmap()


    def active_ai_visual_preview_clip(
        self,
        position_ms: int,
    ) -> dict | None:
        if not self.editor_asset_context_matches_current_selection():
            return None

        position_ms = int(position_ms)
        candidates = []
        for clip in clips_of_kind(
            self.editor_asset_plan,
            "AI_VISUAL",
            active_only=True,
        ):
            if not isinstance(clip, dict) or bool(clip.get("deleted", False)):
                continue
            try:
                start_ms = int(round(float(clip.get("start", 0.0) or 0.0) * 1000))
                end_ms = int(round(float(clip.get("end", 0.0) or 0.0) * 1000))
            except (TypeError, ValueError):
                continue
            if start_ms <= position_ms <= max(start_ms, end_ms):
                candidates.append((start_ms, clip))

        if not candidates:
            return None

        # If two visual clips overlap, prefer the one that starts latest.
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]


    def ai_visual_preview_asset_path(self, clip: dict) -> Path | None:
        path_text = str(
            clip.get("active_variant_path", "")
            or clip.get("asset_path", "")
            or ""
        ).strip()
        if not path_text:
            return None
        return Path(path_text)


    def ai_visual_preview_display_mode(self, clip: dict) -> str:
        mode = str(
            clip.get("display_mode", "OVERLAY_CARD")
            or "OVERLAY_CARD"
        ).strip().upper()
        if mode not in {
            "OVERLAY_CARD",
            "FULL_FRAME_CONTAIN",
            "FULL_FRAME_COVER",
        }:
            mode = "OVERLAY_CARD"
        return mode


    def ai_visual_preview_scale(self, clip: dict) -> float:
        try:
            scale = float(clip.get("scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        return max(0.6, min(1.4, scale))


    def ai_visual_preview_position(
        self,
        clip: dict,
    ) -> tuple[float, float]:
        return (
            self.coerce_visual_position(
                clip.get(
                    "position_x",
                    0.0,
                )
            ),
            self.coerce_visual_position(
                clip.get(
                    "position_y",
                    0.0,
                )
            ),
        )


    def ai_visual_preview_canvas_rect(
        self,
    ) -> tuple[int, int, int, int]:
        width = max(
            1,
            self.video_widget.width(),
        )
        height = max(
            1,
            self.video_widget.height(),
        )
        canvas_height = height
        canvas_width = max(
            1,
            int(
                round(
                    canvas_height
                    * 9
                    / 16
                )
            ),
        )
        if canvas_width > width:
            canvas_width = width
            canvas_height = max(
                1,
                int(
                    round(
                        canvas_width
                        * 16
                        / 9
                    )
                ),
            )
        canvas_x = max(
            0,
            (width - canvas_width) // 2,
        )
        canvas_y = max(
            0,
            (height - canvas_height) // 2,
        )
        return (
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
        )


    def visual_axis_position(
        self,
        base: int,
        minimum: int,
        maximum: int,
        position: float,
    ) -> int:
        position = self.coerce_visual_position(
            position
        )
        if position >= 0.0:
            return int(
                round(
                    base
                    + (
                        maximum
                        - base
                    )
                    * position
                )
            )
        return int(
            round(
                base
                + (
                    base
                    - minimum
                )
                * position
            )
        )


    def visual_axis_position_from_pixel(
        self,
        base: int,
        minimum: int,
        maximum: int,
        value: int,
    ) -> float:
        value = max(
            min(
                int(value),
                max(
                    minimum,
                    maximum,
                ),
            ),
            min(
                minimum,
                maximum,
            ),
        )
        if value >= base:
            span = maximum - base
            if span == 0:
                return 0.0
            return self.coerce_visual_position(
                (value - base) / span
            )
        span = base - minimum
        if span == 0:
            return 0.0
        return self.coerce_visual_position(
            (value - base) / span
        )


    def repolish_ai_visual_preview(self, widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()


    def layout_ai_visual_preview_overlay(
        self,
        clip: dict,
    ):
        if not hasattr(
            self,
            "ai_visual_preview_overlay",
        ):
            return

        width = max(
            1,
            self.video_widget.width(),
        )
        height = max(
            1,
            self.video_widget.height(),
        )
        mode = self.ai_visual_preview_display_mode(
            clip
        )
        scale = self.ai_visual_preview_scale(
            clip
        )
        position_x, position_y = (
            self.ai_visual_preview_position(
                clip
            )
        )

        (
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
        ) = self.ai_visual_preview_canvas_rect()

        layout_signature = (
            self.active_visual_preview_signature,
            width,
            height,
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
            round(
                position_x,
                3,
            ),
            round(
                position_y,
                3,
            ),
        )
        if (
            layout_signature
            == self.active_visual_preview_layout_signature
        ):
            return
        self.active_visual_preview_layout_signature = (
            layout_signature
        )

        self.ai_visual_preview_dim.setGeometry(
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
        )
        if (
            self.ai_visual_preview_dim.property(
                "displayMode"
            )
            != mode
        ):
            self.ai_visual_preview_dim.setProperty(
                "displayMode",
                mode,
            )
            self.repolish_ai_visual_preview(
                self.ai_visual_preview_dim
            )

        overlay = self.ai_visual_preview_overlay
        if overlay.property(
            "displayMode"
        ) != mode:
            overlay.setProperty(
                "displayMode",
                mode,
            )
            self.repolish_ai_visual_preview(
                overlay
            )

        source_pixmap = (
            self.active_visual_preview_pixmap
        )
        preview_pixmap = QPixmap()

        if mode == "OVERLAY_CARD":
            card_width = max(
                1,
                int(
                    round(
                        canvas_width
                        * (
                            842
                            / 1080
                        )
                        * scale
                    )
                ),
            )
            card_height = max(
                1,
                int(
                    round(
                        canvas_height
                        * (
                            882
                            / 1920
                        )
                        * scale
                    )
                ),
            )
            card_width = min(
                canvas_width,
                card_width,
            )
            card_height = min(
                canvas_height,
                card_height,
            )

            base_x = (
                canvas_x
                + max(
                    0,
                    (
                        canvas_width
                        - card_width
                    )
                    // 2,
                )
            )
            base_y_offset = max(
                int(
                    round(
                        canvas_height
                        * (
                            110
                            / 1920
                        )
                    )
                ),
                int(
                    round(
                        (
                            canvas_height
                            - card_height
                        )
                        * 0.22
                    )
                ),
            )
            base_y = (
                canvas_y
                + min(
                    max(
                        0,
                        base_y_offset,
                    ),
                    max(
                        0,
                        canvas_height
                        - card_height,
                    ),
                )
            )

            x = self.visual_axis_position(
                base_x,
                canvas_x,
                canvas_x
                + canvas_width
                - card_width,
                position_x,
            )
            y = self.visual_axis_position(
                base_y,
                canvas_y,
                canvas_y
                + canvas_height
                - card_height,
                position_y,
            )
            overlay.setGeometry(
                x,
                y,
                card_width,
                card_height,
            )

            if not source_pixmap.isNull():
                preview_pixmap = source_pixmap.scaled(
                    overlay.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                crop_width = min(
                    overlay.width(),
                    preview_pixmap.width(),
                )
                crop_height = min(
                    overlay.height(),
                    preview_pixmap.height(),
                )
                crop_x = max(
                    0,
                    (
                        preview_pixmap.width()
                        - crop_width
                    )
                    // 2,
                )
                crop_y = max(
                    0,
                    (
                        preview_pixmap.height()
                        - crop_height
                    )
                    // 2,
                )
                preview_pixmap = preview_pixmap.copy(
                    crop_x,
                    crop_y,
                    crop_width,
                    crop_height,
                )

        elif mode == "FULL_FRAME_CONTAIN":
            bounding_size = QSize(
                max(
                    1,
                    int(
                        round(
                            canvas_width
                            * scale
                        )
                    ),
                ),
                max(
                    1,
                    int(
                        round(
                            canvas_height
                            * scale
                        )
                    ),
                ),
            )

            if not source_pixmap.isNull():
                preview_pixmap = source_pixmap.scaled(
                    bounding_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                image_width = max(
                    1,
                    preview_pixmap.width(),
                )
                image_height = max(
                    1,
                    preview_pixmap.height(),
                )
            else:
                image_width = bounding_size.width()
                image_height = bounding_size.height()

            base_x = (
                canvas_x
                + (
                    canvas_width
                    - image_width
                )
                // 2
            )
            base_y = (
                canvas_y
                + (
                    canvas_height
                    - image_height
                )
                // 2
            )

            if image_width <= canvas_width:
                min_x = canvas_x
                max_x = (
                    canvas_x
                    + canvas_width
                    - image_width
                )
            else:
                min_x = (
                    canvas_x
                    + canvas_width
                    - image_width
                )
                max_x = canvas_x

            if image_height <= canvas_height:
                min_y = canvas_y
                max_y = (
                    canvas_y
                    + canvas_height
                    - image_height
                )
            else:
                min_y = (
                    canvas_y
                    + canvas_height
                    - image_height
                )
                max_y = canvas_y

            x = self.visual_axis_position(
                base_x,
                min_x,
                max_x,
                position_x,
            )
            y = self.visual_axis_position(
                base_y,
                min_y,
                max_y,
                position_y,
            )
            overlay.setGeometry(
                x,
                y,
                image_width,
                image_height,
            )

        else:
            overlay.setGeometry(
                canvas_x,
                canvas_y,
                canvas_width,
                canvas_height,
            )

            if not source_pixmap.isNull():
                cover_scale = max(
                    1.0,
                    scale,
                )
                expanded_size = QSize(
                    max(
                        1,
                        int(
                            round(
                                canvas_width
                                * cover_scale
                            )
                        ),
                    ),
                    max(
                        1,
                        int(
                            round(
                                canvas_height
                                * cover_scale
                            )
                        ),
                    ),
                )
                preview_pixmap = source_pixmap.scaled(
                    expanded_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                crop_width = min(
                    canvas_width,
                    preview_pixmap.width(),
                )
                crop_height = min(
                    canvas_height,
                    preview_pixmap.height(),
                )
                max_crop_x = max(
                    0,
                    preview_pixmap.width()
                    - crop_width,
                )
                max_crop_y = max(
                    0,
                    preview_pixmap.height()
                    - crop_height,
                )
                crop_x = int(
                    round(
                        max_crop_x
                        * (
                            1.0
                            - position_x
                        )
                        / 2.0
                    )
                )
                crop_y = int(
                    round(
                        max_crop_y
                        * (
                            1.0
                            - position_y
                        )
                        / 2.0
                    )
                )
                preview_pixmap = preview_pixmap.copy(
                    crop_x,
                    crop_y,
                    crop_width,
                    crop_height,
                )

        if not preview_pixmap.isNull():
            overlay.setText("")
            overlay.setPixmap(
                preview_pixmap
            )

        overlay.raise_()

        # Do not put a gray/dim matte around scaled images. The source video
        # remains visible behind overlay-card and contain modes.
        self.ai_visual_preview_dim.hide()


    def update_ai_visual_preview_overlay(
        self,
        position_ms: int,
    ):
        if not hasattr(self, "ai_visual_preview_overlay"):
            return

        active_clip = self.active_ai_visual_preview_clip(position_ms)
        if active_clip is None:
            self.hide_ai_visual_preview_overlay()
            return

        clip_id = str(active_clip.get("id", "") or "")
        mode = self.ai_visual_preview_display_mode(active_clip)
        scale = self.ai_visual_preview_scale(active_clip)
        asset_path = self.ai_visual_preview_asset_path(active_clip)

        asset_stamp = None
        if asset_path is not None and asset_path.exists():
            try:
                asset_stamp = asset_path.stat().st_mtime_ns
            except OSError:
                asset_stamp = None

        position_x, position_y = self.ai_visual_preview_position(
            active_clip
        )

        signature = (
            clip_id,
            str(asset_path or ""),
            asset_stamp,
            mode,
            round(scale, 3),
            round(position_x, 3),
            round(position_y, 3),
        )

        if signature != self.active_visual_preview_signature:
            self.active_visual_preview_signature = signature
            self.active_visual_preview_layout_signature = None
            self.active_visual_preview_clip_id = clip_id
            self.active_visual_preview_pixmap = QPixmap()
            self.ai_visual_preview_overlay.clear()

            if asset_path is not None and asset_path.exists():
                pixmap = QPixmap(str(asset_path))
                if not pixmap.isNull():
                    self.active_visual_preview_pixmap = pixmap
                else:
                    self.ai_visual_preview_overlay.setText("IMAGE PREVIEW ERROR")
            elif asset_path is None:
                self.ai_visual_preview_overlay.setText(
                    str(active_clip.get("label", "AI VISUAL") or "AI VISUAL")
                )
            else:
                self.ai_visual_preview_overlay.setText("MISSING VISUAL")

        self.layout_ai_visual_preview_overlay(active_clip)
        self.ai_visual_preview_overlay.show()


    def select_visual_preview_clip(
        self,
        clip_id: str,
    ):
        normalized_id = str(
            clip_id
            or ""
        )
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
            self.selected_sfx_clip_id = None
            self.timeline.set_selected_asset_clip(
                normalized_id
            )
            self.refresh_visual_plan_display()
            self.load_selected_visual_into_inspector()
            return


    def begin_visual_preview_drag(
        self,
        event,
    ) -> bool:
        active_clip = self.active_ai_visual_preview_clip(
            self.player.position()
        )
        if active_clip is None:
            return False

        clip_id = str(
            active_clip.get(
                "id",
                "",
            )
            or ""
        )
        if clip_id:
            self.select_visual_preview_clip(
                clip_id
            )

        self.visual_preview_dragging = True
        self.visual_preview_drag_origin = (
            event.globalPosition().toPoint()
        )
        self.visual_preview_drag_start_geometry = (
            self.ai_visual_preview_overlay.geometry()
        )
        (
            self.visual_preview_drag_start_x,
            self.visual_preview_drag_start_y,
        ) = self.ai_visual_preview_position(
            active_clip
        )
        self.ai_visual_preview_overlay.setCursor(
            Qt.CursorShape.ClosedHandCursor
        )
        return True


    def update_visual_preview_drag(
        self,
        event,
    ):
        if not self.visual_preview_dragging:
            return

        slot = self.selected_visual_slot()
        if slot is None:
            return

        clip_id = self.visual_clip_id(
            slot,
            self.selected_visual_slot_index
            if self.selected_visual_slot_index is not None
            else 0,
        )
        clip = self.find_editor_clip(
            "AI_VISUAL",
            clip_id,
        )
        if clip is None:
            return

        delta = (
            event.globalPosition().toPoint()
            - self.visual_preview_drag_origin
        )
        mode = self.ai_visual_preview_display_mode(
            clip
        )

        (
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
        ) = self.ai_visual_preview_canvas_rect()

        if (
            mode != "FULL_FRAME_COVER"
            and self.visual_preview_drag_start_geometry
            is not None
        ):
            geometry = (
                self.visual_preview_drag_start_geometry
            )
            target_x = geometry.x() + delta.x()
            target_y = geometry.y() + delta.y()

            # Recompute the zero-position geometry for the active mode by
            # temporarily laying out a neutral-position copy.
            neutral_clip = dict(
                clip
            )
            neutral_clip["position_x"] = 0.0
            neutral_clip["position_y"] = 0.0

            saved_signature = (
                self.active_visual_preview_layout_signature
            )
            self.active_visual_preview_layout_signature = None
            self.layout_ai_visual_preview_overlay(
                neutral_clip
            )
            neutral_geometry = (
                self.ai_visual_preview_overlay.geometry()
            )

            image_width = neutral_geometry.width()
            image_height = neutral_geometry.height()
            base_x = neutral_geometry.x()
            base_y = neutral_geometry.y()

            if image_width <= canvas_width:
                min_x = canvas_x
                max_x = (
                    canvas_x
                    + canvas_width
                    - image_width
                )
            else:
                min_x = (
                    canvas_x
                    + canvas_width
                    - image_width
                )
                max_x = canvas_x

            if image_height <= canvas_height:
                min_y = canvas_y
                max_y = (
                    canvas_y
                    + canvas_height
                    - image_height
                )
            else:
                min_y = (
                    canvas_y
                    + canvas_height
                    - image_height
                )
                max_y = canvas_y

            position_x = (
                self.visual_axis_position_from_pixel(
                    base_x,
                    min_x,
                    max_x,
                    target_x,
                )
            )
            position_y = (
                self.visual_axis_position_from_pixel(
                    base_y,
                    min_y,
                    max_y,
                    target_y,
                )
            )
            self.active_visual_preview_layout_signature = (
                saved_signature
            )
        else:
            position_x = (
                self.visual_preview_drag_start_x
                + (
                    delta.x()
                    / max(
                        1.0,
                        canvas_width
                        / 2.0,
                    )
                )
            )
            position_y = (
                self.visual_preview_drag_start_y
                + (
                    delta.y()
                    / max(
                        1.0,
                        canvas_height
                        / 2.0,
                    )
                )
            )
            position_x = self.coerce_visual_position(
                position_x
            )
            position_y = self.coerce_visual_position(
                position_y
            )

        slot["position_x"] = round(
            position_x,
            3,
        )
        slot["position_y"] = round(
            position_y,
            3,
        )
        clip["position_x"] = slot["position_x"]
        clip["position_y"] = slot["position_y"]
        clip["manual_override"] = True
        clip["locked"] = True
        self.mark_visual_slot_modified(
            slot
        )

        self.updating_visual_inspector = True
        self.visual_x_slider.setValue(
            int(
                round(
                    position_x
                    * 100
                )
            )
        )
        self.visual_y_slider.setValue(
            int(
                round(
                    position_y
                    * 100
                )
            )
        )
        self.visual_x_label.setText(
            str(
                self.visual_x_slider.value()
            )
        )
        self.visual_y_label.setText(
            str(
                self.visual_y_slider.value()
            )
        )
        self.updating_visual_inspector = False

        self.active_visual_preview_signature = None
        self.active_visual_preview_layout_signature = None
        self.update_ai_visual_preview_overlay(
            self.player.position()
        )


    def finish_visual_preview_drag(self):
        if not self.visual_preview_dragging:
            return

        self.visual_preview_dragging = False
        self.ai_visual_preview_overlay.setCursor(
            Qt.CursorShape.OpenHandCursor
        )

        slot = self.selected_visual_slot()
        if slot is None:
            return

        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()


