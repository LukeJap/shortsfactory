"""
AIVisualPreviewMixin: the live, draggable AI visual cutaway overlay shown
on top of the video preview -- the original template this session's
placement-editor work (emoji_preview.py, caption_preview.py) followed.
Converts a slot's stored position_x/position_y fraction (-1..1) and
display_mode (OVERLAY_CARD/FULL_FRAME_CONTAIN/FULL_FRAME_COVER) into
screen-space geometry (ai_visual_preview_canvas_rect(),
visual_axis_position()), handles drag-to-reposition via the app-wide
eventFilter in main_window.py, and keeps the right-panel numeric fields
(ai_visual_slots.py) in sync in both directions.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
from editor_asset_plan import clips_of_kind
from .resize_geometry import (
    CORNER_NAMES,
    EDGE_NAMES,
    OPPOSITE_CORNER,
    axis_scale_ratio,
    corner_handle_rects,
    corner_point,
    edge_handle_rects,
    edge_midpoint,
    format_scale_readout,
    uniform_scale_ratio,
)


OPPOSITE_EDGE = {
    "n": "s",
    "s": "n",
    "e": "w",
    "w": "e",
}

CORNER_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
}

EDGE_CURSORS = {
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}


class AIVisualPreviewMixin:

    def hide_ai_visual_preview_overlay(self):
        if hasattr(self, "ai_visual_preview_overlay"):
            self.ai_visual_preview_overlay.hide()
            self.ai_visual_preview_overlay.clear()
        if hasattr(self, "ai_visual_preview_dim"):
            self.ai_visual_preview_dim.hide()
        if hasattr(self, "ai_visual_preview_full_frame_tag"):
            self.ai_visual_preview_full_frame_tag.hide()
        self.hide_ai_visual_resize_handles()
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


    def ai_visual_preview_stretch(self, clip: dict) -> tuple[float, float]:
        return (
            self.coerce_visual_stretch(clip.get("stretch_x", 1.0)),
            self.coerce_visual_stretch(clip.get("stretch_y", 1.0)),
        )


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
        stretch_x, stretch_y = (
            self.ai_visual_preview_stretch(
                clip
            )
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
                            / OUTPUT_WIDTH
                        )
                        * scale
                        * stretch_x
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
                            / OUTPUT_HEIGHT
                        )
                        * scale
                        * stretch_y
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
                            / OUTPUT_HEIGHT
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

        # FULL_FRAME_COVER always fills the whole canvas by design -- there
        # is no bounding box to move, only the crop window within it (still
        # draggable, panning which part of the image is visible). Tag it so
        # that isn't mistaken for a broken drag.
        if hasattr(self, "ai_visual_preview_full_frame_tag"):
            if mode == "FULL_FRAME_COVER":
                tag = self.ai_visual_preview_full_frame_tag
                tag.adjustSize()
                tag.move(
                    canvas_x + 10,
                    canvas_y + 10,
                )
                tag.raise_()
                tag.show()
            else:
                self.ai_visual_preview_full_frame_tag.hide()

        self.layout_ai_visual_resize_handles(mode)


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
            self.selected_emoji_clip_id = None
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


    def reset_active_visual_preview_position(self) -> bool:

        active_clip = self.active_ai_visual_preview_clip(
            self.player.position()
        )
        if active_clip is None:
            return False

        clip_id = str(active_clip.get("id", "") or "")
        if clip_id:
            self.select_visual_preview_clip(clip_id)

        slot = self.selected_visual_slot()
        if slot is None:
            return False

        clip = self.find_editor_clip("AI_VISUAL", clip_id)
        if clip is None:
            return False

        slot["position_x"] = 0.0
        slot["position_y"] = 0.0
        slot["scale"] = 1.0
        slot["stretch_x"] = 1.0
        slot["stretch_y"] = 1.0
        clip["position_x"] = 0.0
        clip["position_y"] = 0.0
        clip["scale"] = 1.0
        clip["stretch_x"] = 1.0
        clip["stretch_y"] = 1.0
        clip["manual_override"] = False
        clip["locked"] = False
        self.mark_visual_slot_modified(slot)

        self.updating_visual_inspector = True
        self.visual_x_slider.setValue(0)
        self.visual_y_slider.setValue(0)
        self.visual_x_label.setText("0")
        self.visual_y_label.setText("0")
        self.visual_scale_slider.setValue(100)
        self.visual_scale_label.setText("100")
        self.updating_visual_inspector = False

        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()

        self.active_visual_preview_signature = None
        self.active_visual_preview_layout_signature = None
        self.update_ai_visual_preview_overlay(self.player.position())
        return True


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


    def ensure_ai_visual_resize_handles(self):
        if hasattr(self, "ai_visual_resize_handles"):
            return

        self.ai_visual_resize_handles = {}
        for corner in CORNER_NAMES:
            label = QLabel(self.video_widget)
            label.setObjectName("VisualResizeHandle")
            label.setCursor(CORNER_CURSORS[corner])
            label.hide()
            self.ai_visual_resize_handles[corner] = label

        self.ai_visual_resize_edge_handles = {}
        for edge in EDGE_NAMES:
            label = QLabel(self.video_widget)
            label.setObjectName("VisualResizeHandle")
            label.setCursor(EDGE_CURSORS[edge])
            label.hide()
            self.ai_visual_resize_edge_handles[edge] = label

        self.ai_visual_resize_readout = QLabel("", self.video_widget)
        self.ai_visual_resize_readout.setObjectName("VisualResizeReadout")
        self.ai_visual_resize_readout.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.ai_visual_resize_readout.hide()

        self.ai_visual_resize_hovering = False
        self.visual_resize_dragging = False


    def hide_ai_visual_resize_handles(self):
        for label in getattr(self, "ai_visual_resize_handles", {}).values():
            label.hide()
        for label in getattr(self, "ai_visual_resize_edge_handles", {}).values():
            label.hide()
        if hasattr(self, "ai_visual_resize_readout"):
            self.ai_visual_resize_readout.hide()


    def layout_ai_visual_resize_handles(self, mode: str):
        self.ensure_ai_visual_resize_handles()

        # Reentrancy guard -- see the matching comment in
        # caption_preview.py's layout_caption_resize_handles().
        if getattr(self, "_laying_out_ai_visual_resize_handles", False):
            return
        self._laying_out_ai_visual_resize_handles = True
        try:
            overlay = self.ai_visual_preview_overlay
            if not overlay.isVisible() or mode == "FULL_FRAME_COVER":
                self.hide_ai_visual_resize_handles()
                return

            geometry = overlay.geometry()
            corner_rects = corner_handle_rects(
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            )
            for corner, rect in corner_rects.items():
                self.ai_visual_resize_handles[corner].setGeometry(rect)

            show_edges = mode == "OVERLAY_CARD"
            if show_edges:
                edge_rects = edge_handle_rects(
                    geometry.x(),
                    geometry.y(),
                    geometry.width(),
                    geometry.height(),
                )
                for edge, rect in edge_rects.items():
                    self.ai_visual_resize_edge_handles[edge].setGeometry(rect)

            active = (
                getattr(self, "ai_visual_resize_hovering", False)
                or getattr(self, "visual_resize_dragging", False)
            )
            if not active:
                self.hide_ai_visual_resize_handles()
                return

            for label in self.ai_visual_resize_handles.values():
                label.raise_()
                label.show()
            for edge, label in self.ai_visual_resize_edge_handles.items():
                if show_edges:
                    label.raise_()
                    label.show()
                else:
                    label.hide()
        finally:
            self._laying_out_ai_visual_resize_handles = False


    def set_ai_visual_resize_hover(self, hovering: bool):
        self.ensure_ai_visual_resize_handles()
        if self.ai_visual_resize_hovering == hovering:
            return
        self.ai_visual_resize_hovering = hovering

        active_clip = self.active_ai_visual_preview_clip(
            self.player.position()
        )
        mode = (
            self.ai_visual_preview_display_mode(active_clip)
            if active_clip is not None
            else "OVERLAY_CARD"
        )
        self.layout_ai_visual_resize_handles(mode)


    def visual_resize_handle_at(self, event, watched) -> tuple[str, str] | None:
        """
        (handle_kind, handle_name) for whichever resize handle -- if any --
        the given press/hover event lands on, checking both the handle
        widget itself and (since a native video surface can swallow events
        before they reach a child label) the shared video_widget.
        """

        for name, label in self.ai_visual_resize_handles.items():
            if not label.isVisible():
                continue
            hit = watched is label
            if watched is self.video_widget:
                try:
                    hit = label.geometry().contains(
                        event.position().toPoint()
                    )
                except Exception:
                    hit = False
            if hit:
                return ("corner", name)

        for name, label in self.ai_visual_resize_edge_handles.items():
            if not label.isVisible():
                continue
            hit = watched is label
            if watched is self.video_widget:
                try:
                    hit = label.geometry().contains(
                        event.position().toPoint()
                    )
                except Exception:
                    hit = False
            if hit:
                return ("edge", name)

        return None


    def begin_visual_resize_drag(self, event, watched) -> bool:
        self.ensure_ai_visual_resize_handles()

        active_clip = self.active_ai_visual_preview_clip(
            self.player.position()
        )
        if active_clip is None:
            return False

        mode = self.ai_visual_preview_display_mode(active_clip)
        if mode == "FULL_FRAME_COVER":
            return False

        hit = self.visual_resize_handle_at(event, watched)
        if hit is None:
            return False
        kind, name = hit

        clip_id = str(active_clip.get("id", "") or "")
        if clip_id:
            self.select_visual_preview_clip(clip_id)

        geometry = self.ai_visual_preview_overlay.geometry()

        if kind == "corner":
            anchor_name = OPPOSITE_CORNER[name]
            anchor_point = corner_point(
                anchor_name,
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            )
            start_point = corner_point(
                name,
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            )
        else:
            anchor_name = OPPOSITE_EDGE[name]
            anchor_point = edge_midpoint(
                anchor_name,
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            )
            start_point = edge_midpoint(
                name,
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            )

        # anchor_point/start_point are in video_widget-local coordinates
        # (matching canvas_rect()/visual_axis_position()'s coordinate
        # space, needed below to solve for the new position fraction).
        # The live drag ratio, though, is compared against the mouse's
        # *global* screen position each move -- mixing a local point with
        # a global one silently produces a meaningless distance (usually
        # huge or near-zero), which is what made a resize jump straight
        # to the max/min clamp on the first pixel of movement. Map both
        # reference points to global coordinates once, up front, for that
        # comparison specifically.
        global_anchor = self.video_widget.mapToGlobal(
            QPoint(anchor_point[0], anchor_point[1])
        )
        global_start = self.video_widget.mapToGlobal(
            QPoint(start_point[0], start_point[1])
        )

        self.visual_resize_dragging = True
        self.visual_resize_kind = kind
        self.visual_resize_handle = name
        self.visual_resize_anchor_name = anchor_name
        self.visual_resize_anchor = anchor_point
        self.visual_resize_start_point = start_point
        self.visual_resize_global_anchor = (global_anchor.x(), global_anchor.y())
        self.visual_resize_global_start = (global_start.x(), global_start.y())
        self.visual_resize_start_geometry = geometry
        self.visual_resize_start_scale = self.ai_visual_preview_scale(
            active_clip
        )
        self.visual_resize_start_stretch = self.ai_visual_preview_stretch(
            active_clip
        )
        return True


    def update_visual_resize_drag(self, event):
        if not getattr(self, "visual_resize_dragging", False):
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
        clip = self.find_editor_clip("AI_VISUAL", clip_id)
        if clip is None:
            return

        mouse = event.globalPosition().toPoint()
        anchor_x, anchor_y = self.visual_resize_anchor
        start_x, start_y = self.visual_resize_start_point
        global_anchor_x, global_anchor_y = self.visual_resize_global_anchor
        global_start_x, global_start_y = self.visual_resize_global_start

        new_scale = self.visual_resize_start_scale
        stretch_x, stretch_y = self.visual_resize_start_stretch

        if self.visual_resize_kind == "corner":
            ratio = uniform_scale_ratio(
                global_anchor_x,
                global_anchor_y,
                global_start_x,
                global_start_y,
                mouse.x(),
                mouse.y(),
            )
            new_scale = self.coerce_visual_scale(
                self.visual_resize_start_scale * ratio
            )
        elif self.visual_resize_handle in ("e", "w"):
            ratio = axis_scale_ratio(global_anchor_x, global_start_x, mouse.x())
            stretch_x = self.coerce_visual_stretch(
                self.visual_resize_start_stretch[0] * ratio
            )
        else:
            ratio = axis_scale_ratio(global_anchor_y, global_start_y, mouse.y())
            stretch_y = self.coerce_visual_stretch(
                self.visual_resize_start_stretch[1] * ratio
            )

        neutral_clip = dict(clip)
        neutral_clip["position_x"] = 0.0
        neutral_clip["position_y"] = 0.0
        neutral_clip["scale"] = new_scale
        neutral_clip["stretch_x"] = stretch_x
        neutral_clip["stretch_y"] = stretch_y

        saved_signature = self.active_visual_preview_layout_signature
        self.active_visual_preview_layout_signature = None
        self.layout_ai_visual_preview_overlay(neutral_clip)
        neutral_geometry = self.ai_visual_preview_overlay.geometry()
        self.active_visual_preview_layout_signature = saved_signature

        image_width = neutral_geometry.width()
        image_height = neutral_geometry.height()
        base_x = neutral_geometry.x()
        base_y = neutral_geometry.y()

        (
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
        ) = self.ai_visual_preview_canvas_rect()

        if image_width <= canvas_width:
            min_x = canvas_x
            max_x = canvas_x + canvas_width - image_width
        else:
            min_x = canvas_x + canvas_width - image_width
            max_x = canvas_x

        if image_height <= canvas_height:
            min_y = canvas_y
            max_y = canvas_y + canvas_height - image_height
        else:
            min_y = canvas_y + canvas_height - image_height
            max_y = canvas_y

        anchor_name = self.visual_resize_anchor_name
        target_x = (
            anchor_x
            if anchor_name in ("nw", "sw", "w")
            else anchor_x - image_width
        )
        target_y = (
            anchor_y
            if anchor_name in ("nw", "ne", "n")
            else anchor_y - image_height
        )
        if self.visual_resize_kind == "edge":
            if self.visual_resize_handle in ("e", "w"):
                target_y = self.visual_resize_start_geometry.y()
            else:
                target_x = self.visual_resize_start_geometry.x()

        position_x = self.visual_axis_position_from_pixel(
            base_x,
            min_x,
            max_x,
            target_x,
        )
        position_y = self.visual_axis_position_from_pixel(
            base_y,
            min_y,
            max_y,
            target_y,
        )

        slot["scale"] = round(new_scale, 2)
        slot["stretch_x"] = round(stretch_x, 2)
        slot["stretch_y"] = round(stretch_y, 2)
        slot["position_x"] = round(position_x, 3)
        slot["position_y"] = round(position_y, 3)
        clip["scale"] = slot["scale"]
        clip["stretch_x"] = slot["stretch_x"]
        clip["stretch_y"] = slot["stretch_y"]
        clip["position_x"] = slot["position_x"]
        clip["position_y"] = slot["position_y"]
        clip["manual_override"] = True
        clip["locked"] = True
        self.mark_visual_slot_modified(slot)

        self.updating_visual_inspector = True
        self.visual_scale_slider.setValue(int(round(new_scale * 100)))
        self.visual_scale_label.setText(str(self.visual_scale_slider.value()))
        self.visual_x_slider.setValue(int(round(position_x * 100)))
        self.visual_y_slider.setValue(int(round(position_y * 100)))
        self.visual_x_label.setText(str(self.visual_x_slider.value()))
        self.visual_y_label.setText(str(self.visual_y_slider.value()))
        self.updating_visual_inspector = False

        self.active_visual_preview_signature = None
        self.active_visual_preview_layout_signature = None
        self.update_ai_visual_preview_overlay(self.player.position())

        if self.visual_resize_kind == "corner":
            readout_text = format_scale_readout(new_scale)
        else:
            readout_text = format_scale_readout(stretch_x, stretch_y)
        overlay_geometry = self.ai_visual_preview_overlay.geometry()
        readout = self.ai_visual_resize_readout
        readout.setText(readout_text)
        readout.adjustSize()
        readout.move(
            overlay_geometry.x(),
            max(0, overlay_geometry.y() - readout.height() - 4),
        )
        readout.raise_()
        readout.show()


    def finish_visual_resize_drag(self):
        if not getattr(self, "visual_resize_dragging", False):
            return

        self.visual_resize_dragging = False
        if hasattr(self, "ai_visual_resize_readout"):
            self.ai_visual_resize_readout.hide()

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

        active_clip = self.active_ai_visual_preview_clip(
            self.player.position()
        )
        mode = (
            self.ai_visual_preview_display_mode(active_clip)
            if active_clip is not None
            else "OVERLAY_CARD"
        )
        self.layout_ai_visual_resize_handles(mode)


