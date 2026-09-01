"""
Two self-contained reusable widgets extracted from the original gui.py
monolith: TimelineNavigator (the compact full-source overview strip above
the main timeline) and DropZone (the drag-and-drop source video import
area). Neither references ShortsFactoryWindow or any mixin -- pure,
independently testable Qt widgets.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .constants import ROOT, SUPPORTED_EXTENSIONS
from .helpers import format_time


class AspectRatioContainer(QWidget):
    """Center one child inside a stable aspect-ratio presentation area."""

    def __init__(self, width_units: int, height_units: int, parent=None):
        super().__init__(parent)
        self._width_units = max(1, int(width_units))
        self._height_units = max(1, int(height_units))
        self._content: QWidget | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_content(self, widget: QWidget):
        self._content = widget
        widget.setParent(self)
        widget.show()
        self._position_content()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_content()

    def _position_content(self):
        if self._content is None:
            return
        available_width = max(1, self.width())
        available_height = max(1, self.height())
        target_width = min(
            available_width,
            int(available_height * self._width_units / self._height_units),
        )
        target_height = int(target_width * self._height_units / self._width_units)
        if target_height > available_height:
            target_height = available_height
            target_width = int(target_height * self._width_units / self._height_units)
        self._content.setGeometry(
            max(0, (available_width - target_width) // 2),
            max(0, (available_height - target_height) // 2),
            max(1, target_width),
            max(1, target_height),
        )


class YouTubeShortsMockOverlay(QWidget):
    """A preview-only painted mobile Shorts shell; it has no render contract."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(1, self.width())
        height = max(1, self.height())
        unit = max(1.0, width / 360.0)

        def font(size: float, weight: QFont.Weight = QFont.Weight.Normal):
            result = QFont(self.font())
            result.setPixelSize(max(8, round(size * unit)))
            result.setWeight(weight)
            return result

        def text(
            rect: QRect,
            value: str,
            size: float,
            weight=QFont.Weight.Normal,
            align=None,
            color: QColor | None = None,
        ):
            painter.save()
            painter.setFont(font(size, weight))
            painter.setPen(color or QColor(255, 255, 255, 245))
            painter.drawText(
                rect,
                align or (Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                value,
            )
            painter.restore()

        def stroke(width: float = 1.7):
            pen = QPen(QColor(255, 255, 255, 245), max(1.2, width * unit))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        def draw_thumb(x: int, y: int, size: int, down: bool = False):
            painter.save()
            if down:
                painter.translate(0, 2 * y + size)
                painter.scale(1, -1)
            path = QPainterPath()
            path.moveTo(x, y + size * 0.42)
            path.lineTo(x + size * 0.22, y + size * 0.42)
            path.lineTo(x + size * 0.34, y + size * 0.12)
            path.lineTo(x + size * 0.49, y + size * 0.12)
            path.lineTo(x + size * 0.54, y + size * 0.29)
            path.lineTo(x + size * 0.82, y + size * 0.29)
            path.lineTo(x + size * 0.92, y + size * 0.40)
            path.lineTo(x + size * 0.87, y + size * 0.73)
            path.lineTo(x + size * 0.76, y + size * 0.87)
            path.lineTo(x + size * 0.22, y + size * 0.87)
            path.lineTo(x + size * 0.22, y + size * 0.42)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(x + size * 0.22, y + size * 0.42, x + size * 0.22, y + size * 0.87)
            painter.restore()

        def draw_comment(x: int, y: int, size: int):
            bubble = QRect(x, y, round(size * 0.92), round(size * 0.67))
            painter.drawRoundedRect(bubble, round(size * 0.12), round(size * 0.12))
            painter.drawLine(
                x + round(size * 0.58),
                y + round(size * 0.67),
                x + round(size * 0.74),
                y + round(size * 0.88),
            )
            painter.drawLine(
                x + round(size * 0.74),
                y + round(size * 0.88),
                x + round(size * 0.74),
                y + round(size * 0.67),
            )

        def draw_share(x: int, y: int, size: int):
            painter.drawLine(
                x + round(size * 0.10),
                y + round(size * 0.74),
                x + round(size * 0.78),
                y + round(size * 0.27),
            )
            painter.drawLine(
                x + round(size * 0.52),
                y + round(size * 0.27),
                x + round(size * 0.78),
                y + round(size * 0.27),
            )
            painter.drawLine(
                x + round(size * 0.78),
                y + round(size * 0.27),
                x + round(size * 0.71),
                y + round(size * 0.05),
            )
            painter.drawLine(
                x + round(size * 0.10),
                y + round(size * 0.74),
                x + round(size * 0.10),
                y + round(size * 0.48),
            )

        def draw_remix(x: int, y: int, size: int):
            painter.drawLine(
                x + round(size * 0.12),
                y + round(size * 0.31),
                x + round(size * 0.72),
                y + round(size * 0.31),
            )
            painter.drawLine(
                x + round(size * 0.72),
                y + round(size * 0.31),
                x + round(size * 0.58),
                y + round(size * 0.14),
            )
            painter.drawLine(
                x + round(size * 0.72),
                y + round(size * 0.31),
                x + round(size * 0.58),
                y + round(size * 0.48),
            )
            painter.drawLine(
                x + round(size * 0.88),
                y + round(size * 0.69),
                x + round(size * 0.28),
                y + round(size * 0.69),
            )
            painter.drawLine(
                x + round(size * 0.28),
                y + round(size * 0.69),
                x + round(size * 0.42),
                y + round(size * 0.52),
            )
            painter.drawLine(
                x + round(size * 0.28),
                y + round(size * 0.69),
                x + round(size * 0.42),
                y + round(size * 0.86),
            )

        def draw_home(x: int, y: int, size: int):
            path = QPainterPath()
            path.moveTo(x + size * 0.10, y + size * 0.47)
            path.lineTo(x + size * 0.50, y + size * 0.12)
            path.lineTo(x + size * 0.90, y + size * 0.47)
            path.lineTo(x + size * 0.82, y + size * 0.47)
            path.lineTo(x + size * 0.82, y + size * 0.87)
            path.lineTo(x + size * 0.18, y + size * 0.87)
            path.lineTo(x + size * 0.18, y + size * 0.47)
            path.closeSubpath()
            painter.drawPath(path)

        def draw_shorts(x: int, y: int, size: int):
            path = QPainterPath()
            path.moveTo(x + size * 0.35, y + size * 0.08)
            path.lineTo(x + size * 0.72, y + size * 0.30)
            path.lineTo(x + size * 0.55, y + size * 0.46)
            path.lineTo(x + size * 0.75, y + size * 0.68)
            path.lineTo(x + size * 0.39, y + size * 0.91)
            path.lineTo(x + size * 0.22, y + size * 0.74)
            path.lineTo(x + size * 0.42, y + size * 0.53)
            path.lineTo(x + size * 0.23, y + size * 0.30)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(x + size * 0.43, y + size * 0.36, x + size * 0.60, y + size * 0.48)
            painter.drawLine(x + size * 0.43, y + size * 0.60, x + size * 0.59, y + size * 0.49)

        def draw_subscriptions(x: int, y: int, size: int):
            painter.drawRoundedRect(
                QRect(x, y + round(size * 0.16), size, round(size * 0.67)),
                round(size * 0.10),
                round(size * 0.10),
            )
            path = QPainterPath()
            path.moveTo(x + size * 0.42, y + size * 0.34)
            path.lineTo(x + size * 0.42, y + size * 0.65)
            path.lineTo(x + size * 0.68, y + size * 0.50)
            path.closeSubpath()
            painter.drawPath(path)

        def draw_profile(x: int, y: int, size: int):
            painter.drawEllipse(
                QRect(x + round(size * 0.34), y + round(size * 0.08), round(size * 0.32), round(size * 0.32))
            )
            shoulders = QPainterPath()
            shoulders.moveTo(x + size * 0.16, y + size * 0.86)
            shoulders.cubicTo(
                x + size * 0.23,
                y + size * 0.53,
                x + size * 0.77,
                y + size * 0.53,
                x + size * 0.84,
                y + size * 0.86,
            )
            painter.drawPath(shoulders)

        def draw_music_note(x: int, y: int, size: int):
            painter.drawLine(
                x + round(size * 0.62),
                y + round(size * 0.08),
                x + round(size * 0.62),
                y + round(size * 0.70),
            )
            painter.drawLine(
                x + round(size * 0.62),
                y + round(size * 0.08),
                x + round(size * 0.92),
                y + round(size * 0.18),
            )
            painter.drawEllipse(
                QRect(x, y + round(size * 0.57), round(size * 0.42), round(size * 0.28))
            )

        margin = round(17 * unit)
        top_icon = round(24 * unit)
        search_ring = round(16 * unit)

        painter.save()
        stroke(1.35)
        painter.drawLine(margin + top_icon, margin, margin, margin + top_icon // 2)
        painter.drawLine(margin, margin + top_icon // 2, margin + top_icon, margin + top_icon)
        search_x = width - margin - round(top_icon * 3.35)
        painter.drawEllipse(search_x, margin + 2, search_ring, search_ring)
        painter.drawLine(
            search_x + search_ring - 1,
            margin + search_ring + 1,
            search_x + search_ring + round(7 * unit),
            margin + search_ring + round(9 * unit),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 245))
        for index in range(3):
            painter.drawEllipse(
                width - margin - top_icon + 5,
                margin + 5 + index * round(6 * unit),
                max(2, round(2.4 * unit)),
                max(2, round(2.4 * unit)),
            )
        painter.restore()

        rail_width = round(44 * unit)
        rail_x = width - margin - round(34 * unit)
        rail_icon = max(round(17 * unit), round(min(width, height) * 0.045))
        rail_top = round(height * 0.37)
        action_gap = max(round(52 * unit), rail_icon + round(24 * unit))
        actions = (("like", "17K"), ("dislike", "Dislike"), ("comment", "391"), ("share", "Share"), ("remix", "Remix"))
        for index, (action, label) in enumerate(actions):
            y = rail_top + index * action_gap
            icon_x = rail_x + (rail_width - rail_icon) // 2
            painter.save()
            stroke(1.55)
            if action == "like":
                draw_thumb(icon_x, y, rail_icon)
            elif action == "dislike":
                draw_thumb(icon_x, y, rail_icon, down=True)
            elif action == "comment":
                draw_comment(icon_x, y + round(rail_icon * 0.08), rail_icon)
            elif action == "share":
                draw_share(icon_x, y, rail_icon)
            else:
                draw_remix(icon_x, y, rail_icon)
            painter.restore()
            text(
                QRect(rail_x, y + rail_icon + round(3 * unit), rail_width, round(15 * unit)),
                label,
                8,
                QFont.Weight.DemiBold,
            )

        lower_left = round(height * 0.76)
        avatar = round(28 * unit)
        painter.save()
        painter.setBrush(QColor(245, 245, 245, 235))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(margin, lower_left, avatar, avatar)
        painter.setBrush(QColor(180, 180, 180, 255))
        painter.drawEllipse(margin + avatar // 3, lower_left + avatar // 4, avatar // 3, avatar // 3)
        painter.restore()
        text(QRect(margin + avatar + round(8 * unit), lower_left - 2, round(150 * unit), avatar), "@shortsfactory", 10, QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        pill_x = margin + avatar + round(122 * unit)
        painter.save()
        painter.setBrush(QColor(255, 255, 255, 225))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill_x, lower_left + round(4 * unit), round(72 * unit), round(20 * unit), round(10 * unit), round(10 * unit))
        painter.restore()
        text(
            QRect(pill_x, lower_left + round(4 * unit), round(72 * unit), round(20 * unit)),
            "Subscribe",
            8,
            QFont.Weight.DemiBold,
            color=QColor(20, 20, 20, 245),
        )
        text(
            QRect(
                margin,
                lower_left + round(36 * unit),
                width - margin * 2 - round(64 * unit),
                round(30 * unit),
            ),
            "Your short title and description live here.",
            9,
            QFont.Weight.Normal,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        nav_height = round(62 * unit)
        nav_y = height - nav_height
        audio_y = min(
            lower_left + round(76 * unit),
            nav_y - round(21 * unit),
        )
        painter.save()
        stroke(1.15)
        draw_music_note(margin, audio_y + round(3 * unit), round(12 * unit))
        painter.restore()
        text(
            QRect(margin + round(16 * unit), audio_y, width - margin * 2 - round(16 * unit), round(20 * unit)),
            "Original audio - ShortsFactory",
            8,
            QFont.Weight.Normal,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        painter.save()
        painter.setBrush(QColor(0, 0, 0, 245))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(0, nav_y, width, nav_height)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawLine(0, nav_y, width, nav_y)
        painter.restore()
        nav_icon = max(round(18 * unit), round(nav_height * 0.31))
        for index, label in enumerate(("Home", "Shorts", "Create", "Inbox", "You")):
            x = int((index + 0.5) * width / 5)
            icon_x = x - nav_icon // 2
            icon_y = nav_y + round(7 * unit)
            painter.save()
            stroke(1.5)
            if index == 0:
                draw_home(icon_x, icon_y, nav_icon)
            elif index == 1:
                draw_shorts(icon_x, icon_y, nav_icon)
            elif index == 2:
                painter.setBrush(QColor(45, 45, 45, 245))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(icon_x - round(3 * unit), icon_y - round(3 * unit), nav_icon + round(6 * unit), nav_icon + round(6 * unit))
                stroke(1.8)
                painter.drawLine(x - round(nav_icon * 0.28), icon_y + nav_icon // 2, x + round(nav_icon * 0.28), icon_y + nav_icon // 2)
                painter.drawLine(x, icon_y + round(nav_icon * 0.22), x, icon_y + round(nav_icon * 0.78))
            elif index == 3:
                draw_subscriptions(icon_x, icon_y, nav_icon)
            else:
                draw_profile(icon_x, icon_y, nav_icon)
            painter.restore()
            text(
                QRect(x - round(35 * unit), icon_y + nav_icon + round(2 * unit), round(70 * unit), round(15 * unit)),
                label,
                7,
                QFont.Weight.DemiBold,
            )


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


