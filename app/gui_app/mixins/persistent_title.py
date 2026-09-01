"""Program-monitor persistent title and preview-only Shorts UI controls."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel

from persistent_title import (
    persistent_title_from_plan,
    set_persistent_title_on_plan,
    write_persistent_title_ass,
)

from ..widgets import YouTubeShortsMockOverlay


class PersistentTitleMixin:
    """Keep the editable export title independent of captions and mock UI."""

    def current_persistent_video_title(self) -> str:
        return persistent_title_from_plan(
            getattr(self, "editor_asset_plan", {}),
        )

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
        self.update_persistent_title_preview()

    def ensure_persistent_title_preview(self):
        if hasattr(self, "persistent_title_preview_label"):
            return

        label = QLabel(self.video_widget)
        label.setObjectName("PersistentTitlePreview")
        label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )
        label.setWordWrap(True)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        shadow = QGraphicsDropShadowEffect(label)
        shadow.setBlurRadius(9)
        shadow.setOffset(2, 3)
        shadow.setColor(QColor(0, 0, 0, 235))
        label.setGraphicsEffect(shadow)
        label.hide()
        self.persistent_title_preview_label = label

        mock = YouTubeShortsMockOverlay(self.video_widget)
        self.youtube_shorts_mock_overlay = mock

    def update_persistent_title_preview(self):
        if not hasattr(self, "video_widget"):
            return
        self.ensure_persistent_title_preview()
        label = self.persistent_title_preview_label
        title = self.current_persistent_video_title()
        if not title:
            label.hide()
            self.update_youtube_ui_preview()
            return

        canvas_x, canvas_y, canvas_width, canvas_height = self.preview_canvas_rect()
        label.setText(title)
        font = QFont("Arial")
        font.setPixelSize(max(16, round(canvas_width * 0.066)))
        font.setWeight(QFont.Weight.Black)
        label.setFont(font)
        label.setStyleSheet(
            "background: transparent; color: #FFFFFF; border: none;"
            "padding: 0px;",
        )
        width = min(canvas_width - 24, max(170, round(canvas_width * 0.76)))
        label.setFixedWidth(width)
        line_height = QFontMetrics(font).lineSpacing()
        desired_height = max(line_height, label.heightForWidth(width))
        label.setFixedHeight(min(line_height * 3, desired_height))
        label.move(
            canvas_x + (canvas_width - width) // 2,
            canvas_y + round(canvas_height * 0.22),
        )
        label.show()
        label.raise_()

        caption = getattr(self, "caption_preview_label", None)
        if caption is not None and caption.isVisible():
            caption.raise_()
        self.update_youtube_ui_preview()

    def set_youtube_ui_preview_enabled(self, enabled: bool):
        self.youtube_ui_preview_enabled = bool(enabled)
        self.update_youtube_ui_preview()

    def update_youtube_ui_preview(self):
        if not hasattr(self, "video_widget"):
            return
        self.ensure_persistent_title_preview()
        overlay = self.youtube_shorts_mock_overlay
        overlay.setGeometry(self.video_widget.rect())
        overlay.setVisible(bool(getattr(self, "youtube_ui_preview_enabled", False)))
        if overlay.isVisible():
            overlay.raise_()

    def write_persistent_title_for_export(
        self,
        path: Path,
        duration_seconds: float,
    ) -> Path | None:
        return write_persistent_title_ass(
            self.current_persistent_video_title(),
            duration_seconds,
            path,
        )
