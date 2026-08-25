"""
CaptionPreviewMixin: the draggable caption-block position override in the
placement editor. Shows a single box (the currently-active transcript
line, or a placeholder) at the caption's current anchor point, draggable
within the same safe floor/ceiling render.py's real burn-in respects
(clamp_caption_drag_position()) -- so a drag can never land somewhere the
render would silently reject/reposition. Right-click resets to the
computed default (see DEFAULT_CAPTION_POSITION_X/Y below).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel

from render import (
    CAPTION_SAFE_MARGIN_BOTTOM,
    CAPTION_SAFE_MARGIN_LEFT,
    CAPTION_SAFE_MARGIN_RIGHT,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    clamp_caption_drag_position,
)
from .resize_geometry import (
    CORNER_NAMES,
    corner_handle_rects,
    corner_point,
    format_scale_readout,
    uniform_scale_ratio,
)


CAPTION_SCALE_MIN = 0.7
CAPTION_SCALE_MAX = 1.6
CAPTION_BASE_FONT_PX = 15

CAPTION_CORNER_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
}


def coerce_caption_scale(value) -> float:

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0

    return max(CAPTION_SCALE_MIN, min(CAPTION_SCALE_MAX, number))


# Matches today's *actual* default caption placement with no override.
# burn_captions() (render.py, STEP 8) burns captions with
# force_style='Alignment=2,MarginL=...,MarginR=...,MarginV=...', which
# completely overrides whatever Style block make_captions.py wrote into
# captions.ass -- so these constants (not make_captions.py's own MARGIN_V)
# are the ones that actually determine where captions land by default.
# Importing them directly from render.py means this can't drift out of
# sync with the real render again.
DEFAULT_CAPTION_POSITION_X = (
    CAPTION_SAFE_MARGIN_LEFT
    + (OUTPUT_WIDTH - CAPTION_SAFE_MARGIN_RIGHT)
) / 2 / OUTPUT_WIDTH
DEFAULT_CAPTION_POSITION_Y = 1.0 - (CAPTION_SAFE_MARGIN_BOTTOM / OUTPUT_HEIGHT)

PLACEHOLDER_CAPTION_TEXT = "Sample caption text"


def coerce_caption_fraction(value) -> float:

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0

    return max(0.0, min(1.0, number))


class CaptionPreviewMixin:

    def current_caption_position(self) -> tuple[float, float]:

        position_x = getattr(self, "caption_position_x", None)
        position_y = getattr(self, "caption_position_y", None)

        if position_x is None or position_y is None:
            return (DEFAULT_CAPTION_POSITION_X, DEFAULT_CAPTION_POSITION_Y)

        # Re-clamp on every read, not just on drag -- a value saved before
        # the drag range was tightened (or edited directly in
        # render_settings.json) gets pulled back into the safe zone the
        # next time it's displayed, instead of silently staying stuck
        # somewhere the render would otherwise refuse to honor.
        return clamp_caption_drag_position(
            coerce_caption_fraction(position_x),
            coerce_caption_fraction(position_y),
        )


    def current_caption_scale(self) -> float:

        scale = getattr(self, "caption_scale", None)
        if scale is None:
            return 1.0
        return coerce_caption_scale(scale)


    def reset_caption_position(self):

        self.caption_position_x = None
        self.caption_position_y = None
        self.caption_scale = None
        self.save_render_settings()
        if hasattr(self, "player"):
            self.update_caption_preview_overlay(self.player.position())


    def representative_caption_text(self, position_ms: int) -> str:

        segments = getattr(self, "source_transcript_segments", [])
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            if (
                segment.get("start_ms", 0)
                <= position_ms
                <= segment.get("end_ms", 0)
            ):
                text = str(segment.get("text", "")).strip()
                if text:
                    return text

        return PLACEHOLDER_CAPTION_TEXT


    def caption_preview_stylesheet(self, scale: float) -> str:

        font_px = max(1, round(CAPTION_BASE_FONT_PX * scale))
        return (
            "background-color: rgba(0, 0, 0, 140);"
            "color: #FFFFFF;"
            "font-weight: 700;"
            f"font-size: {font_px}px;"
            "border: 1px dashed rgba(255, 255, 255, 120);"
            "padding: 4px 8px;"
        )


    def ensure_caption_preview_label(self):

        if hasattr(self, "caption_preview_label"):
            return

        label = QLabel(self.video_widget)
        label.setObjectName("CaptionPreviewOverlay")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            False,
        )
        label.setMouseTracking(True)
        label.setCursor(Qt.CursorShape.OpenHandCursor)
        label.setToolTip(
            "Drag to reposition captions in the final render."
        )
        label.setStyleSheet(self.caption_preview_stylesheet(1.0))
        label.hide()
        self.caption_preview_label = label

        self.caption_resize_handles = {}
        for corner in CORNER_NAMES:
            handle = QLabel(self.video_widget)
            handle.setObjectName("CaptionResizeHandle")
            handle.setCursor(CAPTION_CORNER_CURSORS[corner])
            handle.hide()
            self.caption_resize_handles[corner] = handle

        self.caption_resize_readout = QLabel("", self.video_widget)
        self.caption_resize_readout.setObjectName("CaptionResizeReadout")
        self.caption_resize_readout.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.caption_resize_readout.hide()

        self.caption_resize_hovering = False
        self.caption_resize_dragging = False


    def update_caption_preview_overlay(self, position_ms: int):

        if not hasattr(self, "video_widget"):
            return

        self.ensure_caption_preview_label()
        label = self.caption_preview_label

        canvas_x, canvas_y, canvas_width, canvas_height = (
            self.ai_visual_preview_canvas_rect()
        )

        label.setText(self.representative_caption_text(position_ms))
        label.setStyleSheet(
            self.caption_preview_stylesheet(self.current_caption_scale())
        )

        box_width = min(canvas_width - 24, max(160, int(canvas_width * 0.8)))
        label.setFixedWidth(box_width)
        label.adjustSize()

        position_x, position_y = self.current_caption_position()

        anchor_x = canvas_x + round(position_x * canvas_width)
        anchor_y = canvas_y + round(position_y * canvas_height)

        # ASS \pos() with Alignment=2 anchors bottom-center -- mirror that
        # here so the preview box lines up with where the real burn-in
        # will land. Because the box is always repositioned from this same
        # anchor point regardless of its current size, growing/shrinking
        # the font via a corner-resize drag naturally stays pinned to this
        # point without any extra anchor-preserving math.
        screen_x = anchor_x - label.width() // 2
        screen_y = anchor_y - label.height()

        label.move(screen_x, screen_y)
        label.raise_()
        label.show()

        self.layout_caption_resize_handles()


    def hide_caption_preview_overlay(self):

        if hasattr(self, "caption_preview_label"):
            self.caption_preview_label.hide()
        self.hide_caption_resize_handles()


    def hide_caption_resize_handles(self):

        for handle in getattr(self, "caption_resize_handles", {}).values():
            handle.hide()
        if hasattr(self, "caption_resize_readout"):
            self.caption_resize_readout.hide()


    def layout_caption_resize_handles(self):

        if not hasattr(self, "caption_resize_handles"):
            return

        # Reentrancy guard: show()/raise_() below can synchronously
        # deliver an Enter event for the widget that just appeared under
        # the cursor, which re-enters this method via
        # set_caption_resize_hover() before this call has returned. That
        # nested call used to run the same show()/raise_() calls again,
        # which could re-trigger the same Enter delivery indefinitely.
        if getattr(self, "_laying_out_caption_resize_handles", False):
            return
        self._laying_out_caption_resize_handles = True
        try:
            label = self.caption_preview_label
            if not label.isVisible():
                self.hide_caption_resize_handles()
                return

            geometry = label.geometry()
            rects = corner_handle_rects(
                geometry.x(), geometry.y(), geometry.width(), geometry.height()
            )
            for corner, rect in rects.items():
                self.caption_resize_handles[corner].setGeometry(rect)

            active = (
                getattr(self, "caption_resize_hovering", False)
                or getattr(self, "caption_resize_dragging", False)
            )
            for handle in self.caption_resize_handles.values():
                if active:
                    handle.raise_()
                    handle.show()
                else:
                    handle.hide()
        finally:
            self._laying_out_caption_resize_handles = False


    def set_caption_resize_hover(self, hovering: bool):

        if not hasattr(self, "caption_resize_handles"):
            return
        if self.caption_resize_hovering == hovering:
            return
        self.caption_resize_hovering = hovering
        self.layout_caption_resize_handles()


    def caption_resize_handle_at(self, event, watched):

        for corner, handle in getattr(
            self, "caption_resize_handles", {}
        ).items():
            if not handle.isVisible():
                continue
            hit = watched is handle
            if watched is self.video_widget:
                try:
                    hit = handle.geometry().contains(
                        event.position().toPoint()
                    )
                except Exception:
                    hit = False
            if hit:
                return corner
        return None


    def begin_caption_resize_drag(self, event, watched) -> bool:

        corner = self.caption_resize_handle_at(event, watched)
        if corner is None:
            return False

        label = self.caption_preview_label
        geometry = label.geometry()

        # The caption box is always repositioned from a single fixed
        # bottom-center anchor point regardless of size (see
        # update_caption_preview_overlay()), so that anchor -- not the
        # geometric opposite corner -- is what a resize drag should stay
        # pinned to.
        anchor_point = (
            geometry.x() + geometry.width() // 2,
            geometry.y() + geometry.height(),
        )
        start_point = corner_point(
            corner, geometry.x(), geometry.y(), geometry.width(), geometry.height()
        )

        # anchor_point/start_point above are in video_widget-local
        # coordinates; the live drag ratio is compared against the
        # mouse's *global* screen position on every move, so it needs
        # its own global-mapped copies of these two points -- comparing
        # a local point against a global one produces a meaningless
        # (usually huge) distance, which is what made a resize jump
        # straight to the max clamp on the first pixel of movement and
        # then get stuck there (every subsequent move recomputed the
        # same bogus ratio from the same mismatched pair).
        global_anchor = self.video_widget.mapToGlobal(
            QPoint(anchor_point[0], anchor_point[1])
        )
        global_start = self.video_widget.mapToGlobal(
            QPoint(start_point[0], start_point[1])
        )

        self.caption_resize_dragging = True
        self.caption_resize_handle = corner
        self.caption_resize_anchor = (global_anchor.x(), global_anchor.y())
        self.caption_resize_start_point = (global_start.x(), global_start.y())
        self.caption_resize_start_scale = self.current_caption_scale()
        return True


    def update_caption_resize_drag(self, event):

        if not getattr(self, "caption_resize_dragging", False):
            return

        mouse = event.globalPosition().toPoint()
        anchor_x, anchor_y = self.caption_resize_anchor
        start_x, start_y = self.caption_resize_start_point

        ratio = uniform_scale_ratio(
            anchor_x, anchor_y, start_x, start_y, mouse.x(), mouse.y()
        )
        new_scale = coerce_caption_scale(
            self.caption_resize_start_scale * ratio
        )

        self.caption_scale = round(new_scale, 2)
        self.update_caption_preview_overlay(self.player.position())

        label = self.caption_preview_label
        readout = self.caption_resize_readout
        readout.setText(format_scale_readout(new_scale))
        readout.adjustSize()
        readout.move(
            label.x() + label.width() // 2 - readout.width() // 2,
            max(0, label.y() - readout.height() - 4),
        )
        readout.raise_()
        readout.show()


    def finish_caption_resize_drag(self):

        if not getattr(self, "caption_resize_dragging", False):
            return

        self.caption_resize_dragging = False
        if hasattr(self, "caption_resize_readout"):
            self.caption_resize_readout.hide()
        self.save_render_settings()


    def begin_caption_preview_drag(self, event, watched) -> bool:

        label = getattr(self, "caption_preview_label", None)
        if label is None or not label.isVisible():
            return False

        hit = watched is label
        if watched is self.video_widget:
            try:
                hit = label.geometry().contains(
                    event.position().toPoint()
                )
            except Exception:
                hit = False

        if not hit:
            return False

        self.caption_preview_dragging = True
        self.caption_preview_drag_origin = event.globalPosition().toPoint()
        (
            self.caption_preview_drag_start_x,
            self.caption_preview_drag_start_y,
        ) = self.current_caption_position()
        label.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True


    def update_caption_preview_drag(self, event):

        if not getattr(self, "caption_preview_dragging", False):
            return

        canvas_x, canvas_y, canvas_width, canvas_height = (
            self.ai_visual_preview_canvas_rect()
        )

        delta = event.globalPosition().toPoint() - self.caption_preview_drag_origin

        raw_x = (
            self.caption_preview_drag_start_x
            + delta.x() / max(1, canvas_width)
        )
        raw_y = (
            self.caption_preview_drag_start_y
            + delta.y() / max(1, canvas_height)
        )

        position_x, position_y = clamp_caption_drag_position(
            coerce_caption_fraction(raw_x),
            coerce_caption_fraction(raw_y),
        )

        self.caption_position_x = round(position_x, 3)
        self.caption_position_y = round(position_y, 3)

        label = self.caption_preview_label
        anchor_x = canvas_x + round(position_x * canvas_width)
        anchor_y = canvas_y + round(position_y * canvas_height)
        label.move(anchor_x - label.width() // 2, anchor_y - label.height())


    def finish_caption_preview_drag(self):

        if not getattr(self, "caption_preview_dragging", False):
            return

        self.caption_preview_dragging = False
        if hasattr(self, "caption_preview_label"):
            self.caption_preview_label.setCursor(
                Qt.CursorShape.OpenHandCursor
            )

        self.save_render_settings()
