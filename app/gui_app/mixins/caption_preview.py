from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from render import (
    CAPTION_SAFE_MARGIN_BOTTOM,
    CAPTION_SAFE_MARGIN_LEFT,
    CAPTION_SAFE_MARGIN_RIGHT,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
)


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

        return (
            coerce_caption_fraction(position_x),
            coerce_caption_fraction(position_y),
        )


    def reset_caption_position(self):

        self.caption_position_x = None
        self.caption_position_y = None
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
        label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 140);"
            "color: #FFFFFF;"
            "font-weight: 700;"
            "font-size: 15px;"
            "border: 1px dashed rgba(255, 255, 255, 120);"
            "padding: 4px 8px;"
        )
        label.hide()
        self.caption_preview_label = label


    def update_caption_preview_overlay(self, position_ms: int):

        if not hasattr(self, "video_widget"):
            return

        self.ensure_caption_preview_label()
        label = self.caption_preview_label

        canvas_x, canvas_y, canvas_width, canvas_height = (
            self.ai_visual_preview_canvas_rect()
        )

        label.setText(self.representative_caption_text(position_ms))

        box_width = min(canvas_width - 24, max(160, int(canvas_width * 0.8)))
        label.setFixedWidth(box_width)
        label.adjustSize()

        position_x, position_y = self.current_caption_position()

        anchor_x = canvas_x + round(position_x * canvas_width)
        anchor_y = canvas_y + round(position_y * canvas_height)

        # ASS \pos() with Alignment=2 anchors bottom-center -- mirror that
        # here so the preview box lines up with where the real burn-in
        # will land.
        screen_x = anchor_x - label.width() // 2
        screen_y = anchor_y - label.height()

        label.move(screen_x, screen_y)
        label.raise_()
        label.show()


    def hide_caption_preview_overlay(self):

        if hasattr(self, "caption_preview_label"):
            self.caption_preview_label.hide()


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

        position_x = coerce_caption_fraction(
            self.caption_preview_drag_start_x
            + delta.x() / max(1, canvas_width)
        )
        position_y = coerce_caption_fraction(
            self.caption_preview_drag_start_y
            + delta.y() / max(1, canvas_height)
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
