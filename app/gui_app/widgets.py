"""
Two self-contained reusable widgets extracted from the original gui.py
monolith: TimelineNavigator (the compact full-source overview strip above
the main timeline) and DropZone (the drag-and-drop source video import
area). Neither references ShortsFactoryWindow or any mixin -- pure,
independently testable Qt widgets.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .constants import ROOT, SUPPORTED_EXTENSIONS
from .helpers import format_time


class TimelineNavigator(QWidget):
    """
    Compact full-source navigator.

    The highlighted thumb is the visible timeline viewport:
    - drag center to pan
    - drag left/right edge to resize the viewport
    """

    viewportChangeRequested = Signal(
        int,
        int,
    )

    def __init__(self, parent=None):
        super().__init__(parent)

        self.duration_ms = 0
        self.viewport_start = 0
        self.viewport_end = 0
        self.minimum_visible_ms = 5000
        self.drag_mode: str | None = None
        self.drag_start_x = 0.0
        self.drag_start_viewport = (
            0,
            0,
        )

        self.setMinimumHeight(
            48
        )
        self.setMaximumHeight(
            58
        )
        self.setMouseTracking(
            True
        )
        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
        )

    def set_state(
        self,
        duration_ms: int,
        viewport_start: int,
        viewport_end: int,
        minimum_visible_ms: int = 5000,
    ):

        self.duration_ms = max(
            0,
            int(
                duration_ms
            ),
        )
        self.minimum_visible_ms = max(
            1,
            int(
                minimum_visible_ms
            ),
        )

        start, end = self.clamped_viewport(
            int(
                viewport_start
            ),
            int(
                viewport_end
            ),
        )
        self.viewport_start = start
        self.viewport_end = end
        self.update()

    def usable_rect(
        self,
    ) -> tuple[int, int, int, int]:

        left = 58
        right = max(
            left + 1,
            self.width()
            - 58,
        )
        top = 20
        height = 14
        return (
            left,
            right,
            top,
            height,
        )

    def clamped_viewport(
        self,
        start_ms: int,
        end_ms: int,
    ) -> tuple[int, int]:

        if self.duration_ms <= 0:
            return (
                0,
                0,
            )

        visible = max(
            min(
                self.minimum_visible_ms,
                self.duration_ms,
            ),
            end_ms
            - start_ms,
        )
        visible = min(
            visible,
            self.duration_ms,
        )
        start = max(
            0,
            min(
                int(
                    start_ms
                ),
                self.duration_ms
                - visible,
            ),
        )

        return (
            start,
            start
            + visible,
        )

    def value_to_x(
        self,
        value_ms: int,
    ) -> float:

        left, right, top, height = self.usable_rect()
        if self.duration_ms <= 0:
            return float(
                left
            )

        ratio = max(
            0.0,
            min(
                1.0,
                value_ms
                / self.duration_ms,
            ),
        )
        return left + (
            right
            - left
        ) * ratio

    def x_to_value(
        self,
        x: float,
    ) -> int:

        left, right, top, height = self.usable_rect()
        if self.duration_ms <= 0:
            return 0

        ratio = max(
            0.0,
            min(
                1.0,
                (
                    x
                    - left
                )
                / max(
                    1,
                    right
                    - left,
                ),
            ),
        )
        return int(
            round(
                self.duration_ms
                * ratio
            )
        )

    def thumb_bounds(
        self,
    ) -> tuple[float, float]:

        x1 = self.value_to_x(
            self.viewport_start
        )
        x2 = self.value_to_x(
            self.viewport_end
        )
        minimum_thumb_width = 34
        if x2 - x1 < minimum_thumb_width:
            center = (
                x1
                + x2
            ) / 2
            x1 = center - minimum_thumb_width / 2
            x2 = center + minimum_thumb_width / 2

            left, right, top, height = self.usable_rect()
            if x1 < left:
                x2 += left - x1
                x1 = left
            if x2 > right:
                x1 -= x2 - right
                x2 = right
        return (
            x1,
            x2,
        )

    def hit_mode(
        self,
        x: float,
        y: float,
    ) -> str | None:

        left, right, top, height = self.usable_rect()
        x1, x2 = self.thumb_bounds()

        if not (
            top - 10
            <= y
            <= top
            + height
            + 12
        ):
            return None

        if abs(
            x
            - x1
        ) <= 8:
            return "left"

        if abs(
            x
            - x2
        ) <= 8:
            return "right"

        if x1 < x < x2:
            return "center"

        if left <= x <= right:
            return "jump"

        return None

    def emit_viewport(
        self,
        start_ms: int,
        end_ms: int,
    ):

        start, end = self.clamped_viewport(
            start_ms,
            end_ms,
        )
        self.viewportChangeRequested.emit(
            start,
            end,
        )

    def mousePressEvent(
        self,
        event,
    ):

        position = event.position()
        mode = self.hit_mode(
            position.x(),
            position.y(),
        )

        if mode is None:
            event.ignore()
            return

        if mode == "jump":
            visible = max(
                self.minimum_visible_ms,
                self.viewport_end
                - self.viewport_start,
            )
            center = self.x_to_value(
                position.x()
            )
            self.emit_viewport(
                center
                - visible
                // 2,
                center
                + visible
                // 2,
            )
            mode = "center"

        self.drag_mode = mode
        self.drag_start_x = position.x()
        self.drag_start_viewport = (
            self.viewport_start,
            self.viewport_end,
        )
        self.setCursor(
            Qt.CursorShape.SizeHorCursor
        )
        event.accept()

    def mouseMoveEvent(
        self,
        event,
    ):

        position = event.position()

        if self.drag_mode is not None:
            start, end = self.drag_start_viewport

            if self.drag_mode == "center":
                delta_ms = (
                    self.x_to_value(
                        position.x()
                    )
                    - self.x_to_value(
                        self.drag_start_x
                    )
                )
                self.emit_viewport(
                    start
                    + delta_ms,
                    end
                    + delta_ms,
                )

            elif self.drag_mode == "left":
                new_start = min(
                    self.x_to_value(
                        position.x()
                    ),
                    end
                    - self.minimum_visible_ms,
                )
                self.emit_viewport(
                    new_start,
                    end,
                )

            elif self.drag_mode == "right":
                new_end = max(
                    self.x_to_value(
                        position.x()
                    ),
                    start
                    + self.minimum_visible_ms,
                )
                self.emit_viewport(
                    start,
                    new_end,
                )

            event.accept()
            return

        mode = self.hit_mode(
            position.x(),
            position.y(),
        )
        if mode in {
            "left",
            "right",
            "center",
        }:
            self.setCursor(
                Qt.CursorShape.SizeHorCursor
            )
        else:
            self.setCursor(
                Qt.CursorShape.ArrowCursor
            )

        super().mouseMoveEvent(
            event
        )

    def mouseReleaseEvent(
        self,
        event,
    ):

        self.drag_mode = None
        self.setCursor(
            Qt.CursorShape.ArrowCursor
        )
        event.accept()

    def paintEvent(
        self,
        event,
    ):

        del event

        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        painter.fillRect(
            self.rect(),
            QColor(
                7,
                7,
                8,
            ),
        )

        left, right, top, rail_height = self.usable_rect()
        rail_width = right - left

        painter.setFont(
            QFont(
                "Consolas",
                8,
            )
        )
        painter.setPen(
            QColor(
                126,
                118,
                105,
            )
        )
        painter.drawText(
            8,
            top
            + rail_height,
            "FULL",
        )
        painter.drawText(
            right
            + 8,
            top
            + rail_height,
            format_time(
                self.duration_ms
            ),
        )

        painter.setPen(
            QColor(
                40,
                37,
                36,
            )
        )
        painter.setBrush(
            QColor(
                13,
                13,
                14,
            )
        )
        painter.drawRect(
            left,
            top,
            rail_width,
            rail_height,
        )

        x1, x2 = self.thumb_bounds()
        painter.setPen(
            QColor(
                214,
                203,
                185,
                230,
            )
        )
        painter.setBrush(
            QColor(
                117,
                59,
                45,
                210,
            )
        )
        painter.drawRoundedRect(
            int(
                x1
            ),
            top
            - 4,
            max(
                12,
                int(
                    x2
                    - x1
                ),
            ),
            rail_height
            + 8,
            2,
            2,
        )

        painter.setPen(
            QColor(
                201,
                56,
                79,
            )
        )
        painter.drawLine(
            int(
                x1
            ),
            top
            - 8,
            int(
                x1
            ),
            top
            + rail_height
            + 10,
        )
        painter.drawLine(
            int(
                x2
            ),
            top
            - 8,
            int(
                x2
            ),
            top
            + rail_height
            + 10,
        )

        painter.setPen(
            QColor(
                151,
                143,
                129,
            )
        )
        painter.drawText(
            left,
            self.height()
            - 7,
            (
                "VIEWPORT "
                f"{format_time(self.viewport_start)} - "
                f"{format_time(self.viewport_end)}"
            ),
        )

        painter.end()


class DropZone(QFrame):

    def __init__(self, load_callback):
        super().__init__()

        self.load_callback = load_callback

        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.setObjectName("DropZone")

        layout = QVBoxLayout(self)

        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("＋")
        self.icon_label.setObjectName("DropIcon")
        self.icon_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.title_label = QLabel(
            "Drag & Drop Video"
        )

        self.title_label.setObjectName(
            "DropTitle"
        )

        self.title_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.subtitle_label = QLabel(
            "MP4, MOV, MKV, WEBM"
        )

        self.subtitle_label.setObjectName(
            "DropSubtitle"
        )

        self.subtitle_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.browse_button = QPushButton(
            "Browse Files"
        )

        self.browse_button.clicked.connect(
            self.browse_file
        )

        layout.addStretch()
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addSpacing(12)
        layout.addWidget(self.browse_button)
        layout.addStretch()

    def browse_file(self):

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Video",
            str(ROOT / "input"),
            (
                "Video Files "
                "(*.mp4 *.mov *.mkv *.avi *.webm *.m4v)"
            ),
        )

        if filename:
            self.load_callback(
                Path(filename)
            )

    def mousePressEvent(
        self,
        event: QMouseEvent,
    ):
        # The Browse Files button already opens the dialog on its own
        # click; this only fires for clicks elsewhere in the zone (the
        # "+" icon, title, subtitle) which users naturally click too.
        if event.button() == Qt.MouseButton.LeftButton:
            self.browse_file()
        super().mousePressEvent(event)

    def dragEnterEvent(
        self,
        event: QDragEnterEvent,
    ):

        urls = event.mimeData().urls()

        if not urls:
            return

        path = Path(
            urls[0].toLocalFile()
        )

        if (
            path.is_file()
            and path.suffix.lower()
            in SUPPORTED_EXTENSIONS
        ):
            event.acceptProposedAction()

    def dropEvent(
        self,
        event: QDropEvent,
    ):

        urls = event.mimeData().urls()

        if not urls:
            return

        path = Path(
            urls[0].toLocalFile()
        )

        if (
            path.is_file()
            and path.suffix.lower()
            in SUPPORTED_EXTENSIONS
        ):

            self.load_callback(path)

            event.acceptProposedAction()


