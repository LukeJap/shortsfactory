"""Program-monitor persistent title and preview-only Shorts UI controls."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QInputDialog, QLabel

from persistent_title import (
    persistent_title_from_plan,
    persistent_title_state_from_plan,
    set_persistent_title_on_plan,
    set_persistent_title_transform_on_plan,
    write_persistent_title_ass,
)

from ..widgets import YouTubeShortsMockOverlay
from .resize_geometry import (
    CORNER_NAMES,
    corner_handle_rects,
    corner_point,
    format_scale_readout,
    uniform_scale_ratio,
)


TITLE_CORNER_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
}


class PersistentTitleMixin:
    """Keep the editable export title independent of captions and mock UI."""

    def current_persistent_video_title(self) -> str:
        return persistent_title_from_plan(getattr(self, "editor_asset_plan", {}))

    def current_persistent_title_state(self) -> dict:
        return persistent_title_state_from_plan(getattr(self, "editor_asset_plan", {}))

    def load_persistent_title_state(self):
        if not hasattr(self, "persistent_title_input"):
            return
        title = self.current_persistent_video_title()
        if self.persistent_title_input.text() != title:
            self.persistent_title_input.blockSignals(True)
            self.persistent_title_input.setText(title)
            self.persistent_title_input.blockSignals(False)
        self.update_persistent_title_preview()

    def persistent_video_title_changed(self, value: str):
        if not hasattr(self, "editor_asset_plan"):
            return
        set_persistent_title_on_plan(self.editor_asset_plan, value)
        self.save_editor_asset_plan_state()
        if hasattr(self, "sync_persistent_title_to_active_recap_plan"):
            self.sync_persistent_title_to_active_recap_plan()
        self.update_persistent_title_preview()

    def ensure_persistent_title_preview(self):
        if hasattr(self, "persistent_title_preview_label"):
            return

        label = QLabel(self.preview_overlay_host())
        label.setObjectName("PersistentTitlePreview")
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        label.setWordWrap(True)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        label.setMouseTracking(True)
        label.setCursor(Qt.CursorShape.OpenHandCursor)
        label.setToolTip(
            "Click to select. Drag to reposition. Drag a corner to scale. "
            "Double-click to edit the title."
        )
        shadow = QGraphicsDropShadowEffect(label)
        shadow.setBlurRadius(9)
        shadow.setOffset(2, 3)
        shadow.setColor(QColor(0, 0, 0, 235))
        label.setGraphicsEffect(shadow)
        label.hide()
        self.persistent_title_preview_label = label

        self.persistent_title_resize_handles = {}
        for corner in CORNER_NAMES:
            handle = QLabel(self.preview_overlay_host())
            handle.setObjectName("PersistentTitleResizeHandle")
            handle.setCursor(TITLE_CORNER_CURSORS[corner])
            handle.hide()
            self.persistent_title_resize_handles[corner] = handle

        self.persistent_title_resize_readout = QLabel("", self.preview_overlay_host())
        self.persistent_title_resize_readout.setObjectName("PersistentTitleResizeReadout")
        self.persistent_title_resize_readout.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self.persistent_title_resize_readout.hide()

        self.persistent_title_selected = False
        self.persistent_title_resize_dragging = False
        self.persistent_title_dragging = False

        # Preview-only chrome remains outside every render/effects plan.
        self.youtube_shorts_mock_overlay = YouTubeShortsMockOverlay(
            self.preview_overlay_host()
        )

    def persistent_title_preview_stylesheet(self, font_pixel_size: int) -> str:
        border = "1px dashed rgba(255, 255, 255, 190)" if getattr(
            self, "persistent_title_selected", False
        ) else "none"
        return (
            "background: transparent; color: #FFFFFF; "
            f"border: {border}; padding: 0px; "
            f"font-family: Arial; font-size: {font_pixel_size}px; font-weight: 900;"
        )

    def update_persistent_title_preview(self):
        if not hasattr(self, "video_widget"):
            return
        self.ensure_persistent_title_preview()
        label = self.persistent_title_preview_label
        state = self.current_persistent_title_state()
        if not state["text"] or not state["active"]:
            label.hide()
            self.hide_persistent_title_resize_handles()
            self.update_youtube_ui_preview()
            return

        canvas_x, canvas_y, canvas_width, canvas_height = self.preview_canvas_rect()
        label.setText(state["text"])
        font_pixel_size = max(16, round(canvas_width * 0.066 * state["scale"]))
        # Apply the selection styling first. Qt stylesheets can otherwise
        # reset a QLabel's font after the dynamic title scale is assigned.
        label.setStyleSheet(self.persistent_title_preview_stylesheet(font_pixel_size))
        font = QFont("Arial")
        font.setPixelSize(font_pixel_size)
        font.setWeight(QFont.Weight.Black)
        label.setFont(font)
        width = min(
            canvas_width - 16,
            # ``width`` is the persistent title's normalized wrap width.
            # Keep it independent from ``scale`` so dragging a title corner
            # visibly scales the glyphs rather than reading as a box resize.
            max(120, round(canvas_width * state["width"])),
        )
        label.setFixedWidth(width)
        line_height = QFontMetrics(font).lineSpacing()
        desired_height = max(line_height, label.heightForWidth(width))
        label.setFixedHeight(min(line_height * 3, desired_height))
        anchor_x = canvas_x + round(canvas_width * state["x"])
        anchor_y = canvas_y + round(canvas_height * state["y"])
        label.move(anchor_x - width // 2, anchor_y)
        label.show()
        self.update_youtube_ui_preview()
        self.restore_program_monitor_overlay_stack()
        # Editor-only handles stay visible while a title is selected even
        # though the preview-only platform chrome is the top content layer.
        self.layout_persistent_title_resize_handles()

    def restore_program_monitor_overlay_stack(self):
        """Restore monitor overlays after the decoded foreground is raised."""

        title = getattr(self, "persistent_title_preview_label", None)
        if title is not None and title.isVisible():
            title.raise_()

        caption = getattr(self, "caption_preview_label", None)
        if caption is not None and caption.isVisible():
            caption.raise_()

        mock_ui = getattr(self, "youtube_shorts_mock_overlay", None)
        if mock_ui is not None and mock_ui.isVisible():
            mock_ui.raise_()

        for handle in getattr(self, "persistent_title_resize_handles", {}).values():
            if handle.isVisible():
                handle.raise_()
        for handle in getattr(self, "caption_resize_handles", {}).values():
            if handle.isVisible():
                handle.raise_()
        readout = getattr(self, "persistent_title_resize_readout", None)
        if readout is not None and readout.isVisible():
            readout.raise_()
        readout = getattr(self, "caption_resize_readout", None)
        if readout is not None and readout.isVisible():
            readout.raise_()

    def set_youtube_ui_preview_enabled(self, enabled: bool):
        self.youtube_ui_preview_enabled = bool(enabled)
        self.update_youtube_ui_preview()

    def update_youtube_ui_preview(self):
        if not hasattr(self, "video_widget"):
            return
        self.ensure_persistent_title_preview()
        overlay = self.youtube_shorts_mock_overlay
        # The checkbox remains authoritative after a deferred layout/state load.
        toggle = getattr(self, "youtube_ui_preview_toggle", None)
        enabled = bool(toggle.isChecked()) if toggle is not None else bool(
            getattr(self, "youtube_ui_preview_enabled", False)
        )
        self.youtube_ui_preview_enabled = enabled
        host = self.preview_overlay_host()
        if overlay.parentWidget() is not host:
            overlay.setParent(host)
        overlay.setGeometry(host.contentsRect())
        if enabled:
            overlay.show()
            overlay.raise_()
            overlay.update()
        else:
            overlay.hide()

    def select_persistent_title_preview(self, selected: bool = True):
        self.persistent_title_selected = bool(selected)
        self.update_persistent_title_preview()

    def hide_persistent_title_resize_handles(self):
        for handle in getattr(self, "persistent_title_resize_handles", {}).values():
            handle.hide()
        if hasattr(self, "persistent_title_resize_readout"):
            self.persistent_title_resize_readout.hide()

    def layout_persistent_title_resize_handles(self):
        if not hasattr(self, "persistent_title_resize_handles"):
            return
        if getattr(self, "_laying_out_persistent_title_handles", False):
            return
        self._laying_out_persistent_title_handles = True
        try:
            label = self.persistent_title_preview_label
            if not label.isVisible() or not getattr(self, "persistent_title_selected", False):
                self.hide_persistent_title_resize_handles()
                return
            geometry = label.geometry()
            for corner, rect in corner_handle_rects(
                geometry.x(), geometry.y(), geometry.width(), geometry.height()
            ).items():
                handle = self.persistent_title_resize_handles[corner]
                handle.setGeometry(rect)
                handle.raise_()
                handle.show()
        finally:
            self._laying_out_persistent_title_handles = False

    def persistent_title_resize_handle_at(self, event, watched):
        for corner, handle in getattr(self, "persistent_title_resize_handles", {}).items():
            if not handle.isVisible():
                continue
            hit = watched is handle
            if watched in (self.video_widget, self.preview_overlay_host()):
                try:
                    hit = handle.geometry().contains(
                        self.preview_event_point(event, watched)
                    )
                except Exception:
                    hit = False
            if hit:
                return corner
        return None

    def begin_persistent_title_resize_drag(self, event, watched) -> bool:
        corner = self.persistent_title_resize_handle_at(event, watched)
        if corner is None:
            return False
        geometry = self.persistent_title_preview_label.geometry()
        opposite = {"nw": "se", "ne": "sw", "sw": "ne", "se": "nw"}[corner]
        anchor = corner_point(opposite, geometry.x(), geometry.y(), geometry.width(), geometry.height())
        start = corner_point(corner, geometry.x(), geometry.y(), geometry.width(), geometry.height())
        host = self.preview_overlay_host()
        global_anchor = host.mapToGlobal(QPoint(*anchor))
        global_start = host.mapToGlobal(QPoint(*start))
        self.persistent_title_resize_dragging = True
        self.persistent_title_resize_anchor = (global_anchor.x(), global_anchor.y())
        self.persistent_title_resize_start_point = (global_start.x(), global_start.y())
        self.persistent_title_resize_start_scale = self.current_persistent_title_state()["scale"]
        return True

    def update_persistent_title_resize_drag(self, event):
        if not getattr(self, "persistent_title_resize_dragging", False):
            return
        mouse = event.globalPosition().toPoint()
        anchor_x, anchor_y = self.persistent_title_resize_anchor
        start_x, start_y = self.persistent_title_resize_start_point
        scale = max(
            0.5,
            min(
                2.0,
                self.persistent_title_resize_start_scale
                * uniform_scale_ratio(anchor_x, anchor_y, start_x, start_y, mouse.x(), mouse.y()),
            ),
        )
        set_persistent_title_transform_on_plan(self.editor_asset_plan, scale=round(scale, 2))
        self.update_persistent_title_preview()
        readout = self.persistent_title_resize_readout
        label = self.persistent_title_preview_label
        readout.setText(format_scale_readout(scale))
        readout.adjustSize()
        readout.move(label.x() + label.width() // 2 - readout.width() // 2, max(0, label.y() - readout.height() - 4))
        readout.raise_()
        readout.show()

    def finish_persistent_title_resize_drag(self):
        if not getattr(self, "persistent_title_resize_dragging", False):
            return
        self.persistent_title_resize_dragging = False
        self.persistent_title_resize_readout.hide()
        self.save_editor_asset_plan_state()
        self.update_persistent_title_preview()

    def begin_persistent_title_preview_drag(self, event, watched) -> bool:
        label = getattr(self, "persistent_title_preview_label", None)
        if label is None or not label.isVisible():
            return False
        hit = watched is label
        if watched in (self.video_widget, self.preview_overlay_host()):
            try:
                hit = label.geometry().contains(
                    self.preview_event_point(event, watched)
                )
            except Exception:
                hit = False
        if not hit:
            return False
        self.select_persistent_title_preview(True)
        self.persistent_title_dragging = True
        self.persistent_title_drag_origin = event.globalPosition().toPoint()
        state = self.current_persistent_title_state()
        self.persistent_title_drag_start_x = state["x"]
        self.persistent_title_drag_start_y = state["y"]
        label.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def update_persistent_title_preview_drag(self, event):
        if not getattr(self, "persistent_title_dragging", False):
            return
        _canvas_x, _canvas_y, canvas_width, canvas_height = self.preview_canvas_rect()
        delta = event.globalPosition().toPoint() - self.persistent_title_drag_origin
        set_persistent_title_transform_on_plan(
            self.editor_asset_plan,
            x=round(self.persistent_title_drag_start_x + delta.x() / max(1, canvas_width), 4),
            y=round(self.persistent_title_drag_start_y + delta.y() / max(1, canvas_height), 4),
        )
        self.update_persistent_title_preview()

    def finish_persistent_title_preview_drag(self):
        if not getattr(self, "persistent_title_dragging", False):
            return
        self.persistent_title_dragging = False
        self.persistent_title_preview_label.setCursor(Qt.CursorShape.OpenHandCursor)
        self.save_editor_asset_plan_state()
        self.update_persistent_title_preview()

    def edit_persistent_title_from_preview(self):
        title, accepted = QInputDialog.getText(
            self,
            "Edit video title",
            "Video title:",
            text=self.current_persistent_video_title(),
        )
        if not accepted:
            return
        self.persistent_title_input.setText(title)
        self.select_persistent_title_preview(True)

    def write_persistent_title_for_export(self, path: Path, duration_seconds: float) -> Path | None:
        return write_persistent_title_ass(self.current_persistent_title_state(), duration_seconds, path)
