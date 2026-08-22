from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QProcess, QProcessEnvironment, QTimer, Signal, QSize, QSettings, QEvent, QPoint
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QColor, QPainter, QFont, QPixmap, QPolygon, QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QScrollArea,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    from .editor_asset_plan import (
        clips_of_kind,
        editor_plan_context_matches,
        load_editor_asset_plan,
        replace_kind_clips,
        save_editor_asset_plan,
        set_editor_plan_context,
        upsert_clip,
    )
    from .sfx_engine import asset_metadata_for_path
    from .visual_emphasis import (
        DEFAULT_ENERGY,
        normalize_energy,
        normalize_sfx_mode,
        write_render_settings,
    )
except ImportError:
    from editor_asset_plan import (
        clips_of_kind,
        editor_plan_context_matches,
        load_editor_asset_plan,
        replace_kind_clips,
        save_editor_asset_plan,
        set_editor_plan_context,
        upsert_clip,
    )
    from sfx_engine import asset_metadata_for_path
    from visual_emphasis import (
        DEFAULT_ENERGY,
        normalize_energy,
        normalize_sfx_mode,
        write_render_settings,
    )


ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
}

VISUAL_EVENT_PREFIX = "SF_VISUAL_EVENT "

GENERIC_EDITOR_PHRASES = (
    "becomes the center of attention",
    "becomes the center of a short exchange",
    "clear setup and payoff",
    "interesting moment",
    "engaging conversation",
    "creates curiosity",
    "viewers will want to know",
    "something surprising happens",
)


def format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"

    return f"{minutes:02d}:{remaining_seconds:02d}"


def format_precise_time(milliseconds: int) -> str:
    milliseconds = max(
        0,
        int(
            milliseconds
        ),
    )
    total_seconds = milliseconds // 1000
    millis = milliseconds % 1000
    hours = total_seconds // 3600
    minutes = (
        total_seconds
        % 3600
    ) // 60
    seconds = total_seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_card_time(milliseconds: int) -> str:
    seconds = max(
        0.0,
        milliseconds / 1000,
    )
    hours = int(
        seconds
        // 3600
    )
    minutes = int(
        (
            seconds
            % 3600
        )
        // 60
    )
    remaining = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:04.1f}"
    return f"{minutes:02d}:{remaining:04.1f}"


def is_generic_editor_text(text: str) -> bool:
    normalized = " ".join(
        str(
            text
            or ""
        ).lower().split()
    )
    if not normalized:
        return True
    return any(
        phrase in normalized
        for phrase in GENERIC_EDITOR_PHRASES
    )


def transcript_excerpt(
    text: str,
    max_words: int = 12,
) -> str:
    clean = " ".join(
        str(
            text
            or ""
        ).split()
    )
    if not clean:
        return ""
    pieces = re.split(
        r"(?<=[.!?])\s+",
        clean,
    )
    source = pieces[0].strip(
        " \"'"
    )
    words = source.split()
    if len(
        words
    ) > max_words:
        source = " ".join(
            words[:max_words]
        ).rstrip(
            ".,;:"
        )
    return source.strip()


def timestamp_to_seconds(value: str) -> float | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parts = text.replace(",", ".").split(":")

        if len(parts) == 3:
            return (
                int(parts[0]) * 3600
                + int(parts[1]) * 60
                + float(parts[2])
            )

        if len(parts) == 2:
            return (
                int(parts[0]) * 60
                + float(parts[1])
            )

        return float(text)

    except (TypeError, ValueError):
        return None


class SuggestionSlider(QSlider):
    """
    Editor timeline with:
    - clickable AI suggestion ranges
    - selected clip range overlay
    - draggable IN / OUT handles
    """

    suggestionClicked = Signal(
        int,
        int,
        int,
        int,
    )

    selectionChanged = Signal(
        int,
        int,
    )

    viewportChanged = Signal(
        int,
        int,
    )

    assetClipSelected = Signal(
        str,
        str,
    )

    assetClipChanged = Signal(
        str,
        object,
    )

    assetClipDoubleClicked = Signal(
        str,
        str,
    )

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)

        self.suggested_ranges: list[
            tuple[int, int, int]
        ] = []

        self.selected_suggestion_index: int | None = None

        self.selection_start = 0
        self.selection_end = 0

        self.dragging_handle: str | None = None
        self.dragging_source_clip = False
        self.source_clip_selected = False
        self.source_clip_drag_anchor = 0
        self.source_clip_drag_start = 0
        self.source_clip_drag_end = 0
        self.scrubbing_playhead = False
        self.asset_clips: list[dict] = []
        self.selected_asset_clip_id: str | None = None
        self.dragging_asset_clip = False
        self.dragging_asset_part: str | None = None
        self.asset_drag_anchor = 0
        self.asset_drag_start = 0
        self.asset_drag_end = 0

        self.handle_radius = 8
        self.minimum_selection_ms = 500
        self.minimum_asset_clip_ms = 80
        self.minimum_visible_ms = 5000
        self.viewport_start = 0
        self.viewport_end = 0
        self.manual_viewport_navigation = False

        # Layered editor overlays.
        self.manual_cut_ranges: list[tuple[int, int]] = []
        self.edited_transcript_ranges: list[tuple[int, int]] = []
        self.scene_cut_positions: list[int] = []
        self.motion_ranges: list[tuple[int, int]] = []
        self.fx_ranges: list[tuple[int, int]] = []
        self.graphic_ranges: list[tuple[int, int]] = []
        self.caption_impact_ranges: list[tuple[int, int]] = []
        self.visual_ranges: list[tuple[int, int]] = []
        self.selected_visual_range: tuple[int, int] | None = None

        self.setMouseTracking(
            True
        )

        self.setMinimumHeight(
            218
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
        )

    def setRange(
        self,
        minimum: int,
        maximum: int,
    ):

        super().setRange(
            minimum,
            maximum,
        )

        if maximum <= 0:
            self.viewport_start = 0
            self.viewport_end = 0
        elif (
            self.viewport_end <= self.viewport_start
            or self.viewport_end > maximum
        ):
            self.fit_source(
                emit_signal=False,
            )
        else:
            self.clamp_viewport(
                emit_signal=False,
            )

        self.update()

    def source_duration(self) -> int:

        return max(
            0,
            self.maximum()
            - self.minimum(),
        )

    def visible_duration(self) -> int:

        if self.viewport_end <= self.viewport_start:
            return max(
                1,
                self.source_duration(),
            )

        return max(
            1,
            self.viewport_end
            - self.viewport_start,
        )

    def usable_rect(self) -> tuple[int, int, int]:

        left = 92
        right = max(
            left + 1,
            self.width()
            - 14,
        )
        width = max(
            1,
            right
            - left,
        )
        return left, right, width

    def lane_geometry(
        self,
    ) -> dict[str, int]:

        height = max(
            218,
            self.height(),
        )
        ruler_top = 14
        ruler_bottom = 42
        video_top = 50
        video_height = max(
            36,
            min(
                48,
                int(
                    height
                    * 0.22
                ),
            ),
        )
        edit_top = video_top + video_height + 7
        edit_height = 26
        visual_top = edit_top + edit_height + 7
        visual_height = max(
            30,
            min(
                38,
                int(
                    height
                    * 0.16
                ),
            ),
        )
        sfx_top = visual_top + visual_height + 7
        sfx_height = max(
            30,
            height
            - sfx_top
            - 14,
        )

        return {
            "ruler_top": ruler_top,
            "ruler_bottom": ruler_bottom,
            "video_top": video_top,
            "video_height": video_height,
            "edit_top": edit_top,
            "edit_height": edit_height,
            "visual_top": visual_top,
            "visual_height": visual_height,
            "sfx_top": sfx_top,
            "sfx_height": sfx_height,
            "lane_bottom": sfx_top
            + sfx_height,
        }

    def emit_viewport_changed(self):

        self.viewportChanged.emit(
            int(
                self.viewport_start
            ),
            int(
                self.viewport_end
            ),
        )

    def clamp_viewport(
        self,
        emit_signal: bool = True,
    ):

        duration = self.source_duration()

        if duration <= 0:
            self.viewport_start = 0
            self.viewport_end = 0
            if emit_signal:
                self.emit_viewport_changed()
            self.update()
            return

        visible = min(
            max(
                self.minimum_visible_ms,
                self.visible_duration(),
            ),
            duration,
        )

        start = max(
            0,
            min(
                int(
                    self.viewport_start
                ),
                duration
                - visible,
            ),
        )

        self.viewport_start = start
        self.viewport_end = start + visible

        if emit_signal:
            self.emit_viewport_changed()

        self.update()

    def set_viewport(
        self,
        start_ms: int,
        end_ms: int,
        manual: bool = False,
        emit_signal: bool = True,
    ):

        duration = self.source_duration()

        if duration <= 0:
            self.viewport_start = 0
            self.viewport_end = 0
        else:
            start_ms = int(
                start_ms
            )
            end_ms = int(
                end_ms
            )

            visible = max(
                self.minimum_visible_ms,
                end_ms
                - start_ms,
            )
            visible = min(
                visible,
                duration,
            )

            start_ms = max(
                0,
                min(
                    start_ms,
                    duration
                    - visible,
                ),
            )

            self.viewport_start = start_ms
            self.viewport_end = start_ms + visible

        if manual:
            self.manual_viewport_navigation = True

        if emit_signal:
            self.emit_viewport_changed()

        self.update()

    def fit_source(
        self,
        emit_signal: bool = True,
    ):

        self.manual_viewport_navigation = False
        self.viewport_start = 0
        self.viewport_end = self.source_duration()

        if emit_signal:
            self.emit_viewport_changed()

        self.update()

    def fit_selection(
        self,
    ):

        if self.selection_end <= self.selection_start:
            self.fit_source()
            return

        selection_duration = (
            self.selection_end
            - self.selection_start
        )

        padding = max(
            2000,
            int(
                selection_duration
                * 0.18
            ),
        )

        self.set_viewport(
            self.selection_start
            - padding,
            self.selection_end
            + padding,
            manual=False,
        )

    def zoom_around(
        self,
        anchor_ms: int,
        anchor_ratio: float,
        factor: float,
        manual: bool = True,
    ):

        duration = self.source_duration()

        if duration <= 0:
            return

        old_visible = self.visible_duration()
        new_visible = int(
            round(
                old_visible
                * factor
            )
        )
        new_visible = max(
            min(
                self.minimum_visible_ms,
                duration,
            ),
            min(
                duration,
                new_visible,
            ),
        )

        anchor_ratio = max(
            0.0,
            min(
                1.0,
                anchor_ratio,
            ),
        )

        new_start = int(
            round(
                anchor_ms
                - new_visible
                * anchor_ratio
            )
        )

        self.set_viewport(
            new_start,
            new_start
            + new_visible,
            manual=manual,
        )

    def set_zoom_fraction(
        self,
        fraction: float,
    ):

        duration = self.source_duration()

        if duration <= 0:
            return

        fraction = max(
            0.0,
            min(
                1.0,
                fraction,
            ),
        )

        min_visible = min(
            self.minimum_visible_ms,
            duration,
        )

        visible = int(
            round(
                duration
                - (
                    duration
                    - min_visible
                )
                * fraction
            )
        )

        center = (
            self.viewport_start
            + self.visible_duration()
            / 2
        )

        self.set_viewport(
            int(
                center
                - visible
                / 2
            ),
            int(
                center
                + visible
                / 2
            ),
            manual=True,
        )

    def zoom_fraction(
        self,
    ) -> float:

        duration = self.source_duration()

        if duration <= 0:
            return 0.0

        min_visible = min(
            self.minimum_visible_ms,
            duration,
        )

        if duration <= min_visible:
            return 0.0

        return max(
            0.0,
            min(
                1.0,
                (
                    duration
                    - self.visible_duration()
                )
                / (
                    duration
                    - min_visible
                ),
            ),
        )

    def horizontal_pan(
        self,
        delta_ms: int,
        manual: bool = True,
    ):

        self.set_viewport(
            self.viewport_start
            + int(
                delta_ms
            ),
            self.viewport_end
            + int(
                delta_ms
            ),
            manual=manual,
        )

    def reveal_time(
        self,
        value_ms: int,
        padding_ratio: float = 0.16,
        manual: bool = False,
    ):

        duration = self.source_duration()

        if duration <= 0:
            return

        value_ms = max(
            0,
            min(
                duration,
                int(
                    value_ms
                ),
            ),
        )

        visible = self.visible_duration()
        padding = int(
            visible
            * padding_ratio
        )

        if (
            self.viewport_start
            + padding
            <= value_ms
            <= self.viewport_end
            - padding
        ):
            return

        if value_ms < self.viewport_start + padding:
            start = value_ms - padding
        else:
            start = value_ms - visible + padding

        self.set_viewport(
            start,
            start + visible,
            manual=manual,
        )

    def reveal_range(
        self,
        start_ms: int,
        end_ms: int,
        padding_ratio: float = 0.18,
        manual: bool = False,
    ):

        if end_ms <= start_ms:
            self.reveal_time(
                start_ms,
                manual=manual,
            )
            return

        visible = self.visible_duration()
        duration = end_ms - start_ms

        if (
            start_ms >= self.viewport_start
            and end_ms <= self.viewport_end
            and duration <= visible
            * 0.75
        ):
            return

        if duration > visible * 0.75:
            padding = max(
                2000,
                int(
                    duration
                    * padding_ratio
                ),
            )
            self.set_viewport(
                start_ms
                - padding,
                end_ms
                + padding,
                manual=manual,
            )
            return

        center = (
            start_ms
            + end_ms
        ) // 2
        self.set_viewport(
            center
            - visible // 2,
            center
            + visible // 2,
            manual=manual,
        )

    def follow_playhead(
        self,
        value_ms: int,
    ):

        if self.manual_viewport_navigation:
            return

        duration = self.source_duration()

        if (
            duration <= 0
            or self.visible_duration() >= duration
        ):
            return

        visible = self.visible_duration()
        soft_end = self.viewport_start + int(
            visible
            * 0.82
        )
        soft_start = self.viewport_start + int(
            visible
            * 0.06
        )

        if value_ms > soft_end:
            new_start = value_ms - int(
                visible
                * 0.42
            )
            self.set_viewport(
                new_start,
                new_start
                + visible,
                manual=False,
            )
        elif value_ms < soft_start:
            new_start = value_ms - int(
                visible
                * 0.18
            )
            self.set_viewport(
                new_start,
                new_start
                + visible,
                manual=False,
            )

    def clear_suggestions(self):

        self.suggested_ranges = []
        self.selected_suggestion_index = None

        self.setCursor(
            Qt.CursorShape.ArrowCursor
        )

        self.update()

    def set_suggestions(
        self,
        suggestions: list[tuple[int, int, int]],
    ):

        self.suggested_ranges = suggestions

        if suggestions:
            self.selected_suggestion_index = 0
        else:
            self.selected_suggestion_index = None

        self.update()

    def set_selected_suggestion(
        self,
        index: int | None,
    ):

        self.selected_suggestion_index = index
        self.update()

    def set_selection_range(
        self,
        start_ms: int,
        end_ms: int,
        emit_signal: bool = False,
    ):

        maximum = max(
            0,
            self.maximum(),
        )

        start_ms = max(
            0,
            min(
                int(start_ms),
                maximum,
            ),
        )

        end_ms = max(
            start_ms,
            min(
                int(end_ms),
                maximum,
            ),
        )

        self.selection_start = start_ms
        self.selection_end = end_ms
        self.source_clip_selected = (
            end_ms
            > start_ms
        )

        self.update()

        if emit_signal:
            self.selectionChanged.emit(
                self.selection_start,
                self.selection_end,
            )

    def value_to_x(
        self,
        value: int,
    ) -> float:

        duration = self.source_duration()

        if duration <= 0:
            return 10.0

        left, right, width = self.usable_rect()

        ratio = max(
            0.0,
            min(
                1.0,
                (
                    int(
                        value
                    )
                    - self.viewport_start
                )
                / self.visible_duration(),
            ),
        )

        return (
            left
            + width * ratio
        )

    def x_to_value(
        self,
        x: float,
    ) -> int:

        duration = self.source_duration()

        if duration <= 0:
            return 0

        left, right, width = self.usable_rect()

        ratio = (
            (x - left)
            / width
        )

        ratio = max(
            0.0,
            min(
                1.0,
                ratio,
            ),
        )

        return int(
            round(
                self.viewport_start
                + self.visible_duration()
                * ratio
            )
        )

    def range_to_geometry(
        self,
        start_ms: int,
        end_ms: int,
    ) -> tuple[float, float] | None:

        if end_ms <= self.viewport_start or start_ms >= self.viewport_end:
            return None

        visible_start = max(
            start_ms,
            self.viewport_start,
        )
        visible_end = min(
            end_ms,
            self.viewport_end,
        )

        if visible_end <= visible_start:
            return None

        return (
            self.value_to_x(
                visible_start
            ),
            self.value_to_x(
                visible_end
            ),
        )

    def suggestion_geometry(
        self,
        index: int,
    ) -> tuple[float, float, float, float] | None:

        if (
            index < 0
            or index >= len(self.suggested_ranges)
        ):
            return None

        start_ms, end_ms, score = (
            self.suggested_ranges[index]
        )

        geometry = self.range_to_geometry(
            start_ms,
            end_ms,
        )

        if geometry is None:
            return None

        x1, x2 = geometry

        width = max(
            5.0,
            x2 - x1,
        )

        lanes = self.lane_geometry()
        marker_y = lanes["video_top"] + lanes["video_height"] - 15

        return (
            x1,
            float(marker_y),
            width,
            12.0,
        )

    def suggestion_at_position(
        self,
        x: float,
        y: float,
    ) -> int | None:

        for index in reversed(
            range(len(self.suggested_ranges))
        ):

            geometry = self.suggestion_geometry(
                index
            )

            if geometry is None:
                continue

            x1, marker_y, width, height = geometry

            if (
                x1 - 6 <= x <= x1 + width + 6
                and marker_y - 5
                <= y
                <= marker_y + height + 6
            ):
                return index

        return None

    def source_clip_geometry(
        self,
    ) -> tuple[float, float, float, float] | None:

        if self.selection_end <= self.selection_start:
            return None

        geometry = self.range_to_geometry(
            self.selection_start,
            self.selection_end,
        )

        if geometry is None:
            return None

        x1, x2 = geometry
        lanes = self.lane_geometry()
        top = lanes["video_top"] + 5
        height = max(
            24,
            lanes["video_height"] - 10,
        )

        return (
            x1,
            float(top),
            max(
                4.0,
                x2 - x1,
            ),
            float(height),
        )

    def source_clip_part_at_position(
        self,
        x: float,
        y: float,
    ) -> str | None:

        geometry = self.source_clip_geometry()

        if geometry is None:
            return None

        clip_x, clip_y, clip_width, clip_height = geometry

        if not (
            clip_x - 8
            <= x
            <= clip_x + clip_width + 8
            and clip_y - 4
            <= y
            <= clip_y + clip_height + 4
        ):
            return None

        edge_width = max(
            8.0,
            min(
                18.0,
                clip_width * 0.16,
            ),
        )

        if x <= clip_x + edge_width:
            return "start"

        if x >= clip_x + clip_width - edge_width:
            return "end"

        return "body"

    def clip_time_bounds_ms(
        self,
        clip: dict,
    ) -> tuple[int, int]:

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
            start_ms = 0

        try:
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
            end_ms = start_ms

        return start_ms, max(
            start_ms + self.minimum_asset_clip_ms,
            end_ms,
        )

    def asset_clip_lane(
        self,
        clip: dict,
    ) -> tuple[int, int] | None:

        kind = str(
            clip.get(
                "kind",
                "",
            )
            or ""
        ).upper()
        lanes = self.lane_geometry()

        if kind == "SFX":
            return (
                lanes["sfx_top"] + 4,
                max(
                    18,
                    lanes["sfx_height"] - 8,
                ),
            )

        if kind == "AI_VISUAL":
            return (
                lanes["visual_top"] + 4,
                max(
                    18,
                    lanes["visual_height"] - 8,
                ),
            )

        return None

    def asset_clip_geometry(
        self,
        clip: dict,
    ) -> tuple[float, float, float, float] | None:

        lane = self.asset_clip_lane(
            clip
        )
        if lane is None:
            return None

        start_ms, end_ms = self.clip_time_bounds_ms(
            clip
        )
        geometry = self.range_to_geometry(
            start_ms,
            end_ms,
        )
        if geometry is None:
            return None

        x1, x2 = geometry
        lane_y, lane_height = lane
        return (
            x1,
            float(
                lane_y
            ),
            max(
                6.0,
                x2 - x1,
            ),
            float(
                lane_height
            ),
        )

    def asset_clip_part_at_position(
        self,
        x: float,
        y: float,
    ) -> tuple[str, str, str] | None:

        for clip in reversed(
            self.asset_clips
        ):
            if not isinstance(
                clip,
                dict,
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

            geometry = self.asset_clip_geometry(
                clip
            )
            if geometry is None:
                continue

            clip_x, clip_y, clip_width, clip_height = geometry
            if not (
                clip_x - 8
                <= x
                <= clip_x + clip_width + 8
                and clip_y - 4
                <= y
                <= clip_y + clip_height + 4
            ):
                continue

            edge_width = max(
                8.0,
                min(
                    16.0,
                    clip_width * 0.22,
                ),
            )
            if x <= clip_x + edge_width:
                part = "start"
            elif x >= clip_x + clip_width - edge_width:
                part = "end"
            else:
                part = "body"

            return (
                str(
                    clip.get(
                        "kind",
                        "",
                    )
                    or ""
                ).upper(),
                clip_id,
                part,
            )

        return None

    def asset_clip_by_id(
        self,
        clip_id: str,
    ) -> dict | None:

        normalized = str(
            clip_id
            or ""
        )
        for clip in self.asset_clips:
            if str(
                clip.get(
                    "id",
                    "",
                )
                or ""
            ) == normalized:
                return clip
        return None

    def mark_asset_clip_manual(
        self,
        clip: dict,
    ):

        clip["manual_override"] = True
        clip["locked"] = True
        clip["origin"] = (
            clip.get(
                "origin",
                "manual",
            )
            or "manual"
        )

    def apply_asset_clip_drag(
        self,
        clip: dict,
        new_start_ms: int,
        new_end_ms: int,
    ):

        new_start_ms = max(
            0,
            int(
                new_start_ms
            ),
        )
        new_end_ms = max(
            new_start_ms + self.minimum_asset_clip_ms,
            int(
                new_end_ms
            ),
        )

        maximum = max(
            0,
            self.maximum(),
        )
        if maximum > 0:
            duration = new_end_ms - new_start_ms
            if new_end_ms > maximum:
                new_end_ms = maximum
                new_start_ms = max(
                    0,
                    new_end_ms - duration,
                )

        clip["start"] = round(
            new_start_ms / 1000,
            3,
        )
        clip["end"] = round(
            new_end_ms / 1000,
            3,
        )
        clip["duration"] = round(
            max(
                0,
                new_end_ms - new_start_ms,
            )
            / 1000,
            3,
        )
        self.mark_asset_clip_manual(
            clip
        )
        self.assetClipChanged.emit(
            str(
                clip.get(
                    "kind",
                    "",
                )
                or ""
            ).upper(),
            dict(
                clip
            ),
        )

    def handle_at_position(
        self,
        x: float,
        y: float,
    ) -> str | None:

        if self.selection_end <= self.selection_start:
            return None

        lanes = self.lane_geometry()
        handle_top = lanes["ruler_bottom"] - 10
        handle_bottom = lanes["lane_bottom"]

        if (
            self.viewport_start
            <= self.selection_start
            <= self.viewport_end
        ):
            start_x = self.value_to_x(
                self.selection_start
            )

            if (
                abs(x - start_x)
                <= self.handle_radius + 5
                and handle_top
                <= y
                <= handle_bottom
            ):
                return "start"

        if (
            self.viewport_start
            <= self.selection_end
            <= self.viewport_end
        ):
            end_x = self.value_to_x(
                self.selection_end
            )

            if (
                abs(x - end_x)
                <= self.handle_radius + 5
                and handle_top
                <= y
                <= handle_bottom
            ):
                return "end"

        return None

    def mousePressEvent(self, event):

        position = event.position()

        asset_hit = self.asset_clip_part_at_position(
            position.x(),
            position.y(),
        )

        if asset_hit is not None:
            kind, clip_id, part = asset_hit
            clip = self.asset_clip_by_id(
                clip_id
            )
            if clip is None:
                return

            self.selected_asset_clip_id = clip_id
            self.dragging_asset_clip = True
            self.dragging_asset_part = part
            self.asset_drag_anchor = self.x_to_value(
                position.x()
            )
            self.asset_drag_start, self.asset_drag_end = (
                self.clip_time_bounds_ms(
                    clip
                )
            )
            self.source_clip_selected = False
            self.assetClipSelected.emit(
                kind,
                clip_id,
            )
            self.setCursor(
                Qt.CursorShape.SizeHorCursor
                if part in {
                    "start",
                    "end",
                }
                else Qt.CursorShape.ClosedHandCursor
            )
            self.update()
            event.accept()
            return

        source_part = self.source_clip_part_at_position(
            position.x(),
            position.y(),
        )

        if source_part is not None:

            self.source_clip_selected = True

            if source_part in {
                "start",
                "end",
            }:
                self.dragging_handle = source_part
            else:
                self.dragging_source_clip = True
                self.source_clip_drag_anchor = self.x_to_value(
                    position.x()
                )
                self.source_clip_drag_start = self.selection_start
                self.source_clip_drag_end = self.selection_end

            self.setCursor(
                Qt.CursorShape.SizeHorCursor
                if source_part
                in {
                    "start",
                    "end",
                }
                else Qt.CursorShape.ClosedHandCursor
            )

            self.update()
            event.accept()
            return

        handle = self.handle_at_position(
            position.x(),
            position.y(),
        )

        if handle is not None:

            self.source_clip_selected = True
            self.dragging_handle = handle

            self.setCursor(
                Qt.CursorShape.SizeHorCursor
            )

            self.update()
            event.accept()
            return

        suggestion_index = (
            self.suggestion_at_position(
                position.x(),
                position.y(),
            )
        )

        if suggestion_index is not None:

            start_ms, end_ms, score = (
                self.suggested_ranges[
                    suggestion_index
                ]
            )

            self.selected_suggestion_index = (
                suggestion_index
            )

            self.set_selection_range(
                start_ms,
                end_ms,
            )

            self.update()

            self.suggestionClicked.emit(
                suggestion_index + 1,
                start_ms,
                end_ms,
                score,
            )

            event.accept()
            return

        new_value = self.x_to_value(
            position.x()
        )

        self.source_clip_selected = False
        self.manual_viewport_navigation = False
        self.scrubbing_playhead = True
        self.setSliderDown(
            True
        )
        self.setValue(
            new_value
        )
        self.sliderMoved.emit(
            new_value
        )

        event.accept()

    def mouseMoveEvent(self, event):

        position = event.position()

        if self.dragging_asset_clip:
            clip = self.asset_clip_by_id(
                self.selected_asset_clip_id
                or ""
            )
            if clip is None:
                self.dragging_asset_clip = False
                self.dragging_asset_part = None
                event.accept()
                return

            new_value = self.x_to_value(
                position.x()
            )

            if self.dragging_asset_part == "body":
                delta = new_value - self.asset_drag_anchor
                duration = self.asset_drag_end - self.asset_drag_start
                new_start = self.asset_drag_start + delta
                new_end = new_start + duration
            elif self.dragging_asset_part == "start":
                new_start = min(
                    new_value,
                    self.asset_drag_end
                    - self.minimum_asset_clip_ms,
                )
                new_end = self.asset_drag_end
            elif self.dragging_asset_part == "end":
                new_start = self.asset_drag_start
                new_end = max(
                    new_value,
                    self.asset_drag_start
                    + self.minimum_asset_clip_ms,
                )
            else:
                event.accept()
                return

            self.apply_asset_clip_drag(
                clip,
                int(
                    new_start
                ),
                int(
                    new_end
                ),
            )
            self.update()
            event.accept()
            return

        if self.dragging_source_clip:

            new_value = self.x_to_value(
                position.x()
            )

            delta = (
                new_value
                - self.source_clip_drag_anchor
            )

            duration = (
                self.source_clip_drag_end
                - self.source_clip_drag_start
            )

            maximum = max(
                0,
                self.maximum(),
            )

            new_start = max(
                0,
                min(
                    self.source_clip_drag_start
                    + delta,
                    maximum
                    - duration,
                ),
            )

            new_end = new_start + duration

            self.selection_start = int(
                new_start
            )
            self.selection_end = int(
                new_end
            )
            self.selected_suggestion_index = None

            self.selectionChanged.emit(
                self.selection_start,
                self.selection_end,
            )

            self.update()
            event.accept()
            return

        if self.dragging_handle is not None:

            new_value = self.x_to_value(
                position.x()
            )

            if self.dragging_handle == "start":

                new_value = min(
                    new_value,
                    self.selection_end
                    - self.minimum_selection_ms,
                )

                new_value = max(
                    0,
                    new_value,
                )

                self.selection_start = (
                    new_value
                )

            elif self.dragging_handle == "end":

                new_value = max(
                    new_value,
                    self.selection_start
                    + self.minimum_selection_ms,
                )

                new_value = min(
                    self.maximum(),
                    new_value,
                )

                self.selection_end = (
                    new_value
                )

            self.selected_suggestion_index = None

            self.selectionChanged.emit(
                self.selection_start,
                self.selection_end,
            )

            self.update()

            event.accept()
            return

        if self.scrubbing_playhead:
            new_value = self.x_to_value(
                position.x()
            )
            self.setValue(
                new_value
            )
            self.sliderMoved.emit(
                new_value
            )
            self.reveal_time(
                new_value,
                manual=False,
            )
            event.accept()
            return

        handle = self.handle_at_position(
            position.x(),
            position.y(),
        )

        asset_hit = self.asset_clip_part_at_position(
            position.x(),
            position.y(),
        )
        source_part = self.source_clip_part_at_position(
            position.x(),
            position.y(),
        )

        if asset_hit is not None and asset_hit[2] in {
            "start",
            "end",
        }:
            self.setCursor(
                Qt.CursorShape.SizeHorCursor
            )

        elif asset_hit is not None:
            self.setCursor(
                Qt.CursorShape.OpenHandCursor
            )

        elif (
            source_part in {
                "start",
                "end",
            }
            or handle is not None
        ):

            self.setCursor(
                Qt.CursorShape.SizeHorCursor
            )

        elif source_part == "body":

            self.setCursor(
                Qt.CursorShape.OpenHandCursor
            )

        elif (
            self.suggestion_at_position(
                position.x(),
                position.y(),
            )
            is not None
        ):

            self.setCursor(
                Qt.CursorShape.PointingHandCursor
            )

        else:

            self.setCursor(
                Qt.CursorShape.ArrowCursor
            )

        super().mouseMoveEvent(
            event
        )

    def mouseReleaseEvent(self, event):

        if self.dragging_asset_clip:
            clip = self.asset_clip_by_id(
                self.selected_asset_clip_id
                or ""
            )
            self.dragging_asset_clip = False
            self.dragging_asset_part = None
            self.setCursor(
                Qt.CursorShape.ArrowCursor
            )
            if clip is not None:
                self.assetClipChanged.emit(
                    str(
                        clip.get(
                            "kind",
                            "",
                        )
                        or ""
                    ).upper(),
                    dict(
                        clip
                    ),
                )
            event.accept()
            return

        if self.dragging_source_clip:

            self.dragging_source_clip = False

            self.setCursor(
                Qt.CursorShape.ArrowCursor
            )

            self.selectionChanged.emit(
                self.selection_start,
                self.selection_end,
            )

            event.accept()
            return

        if self.dragging_handle is not None:

            self.dragging_handle = None

            self.setCursor(
                Qt.CursorShape.ArrowCursor
            )

            self.selectionChanged.emit(
                self.selection_start,
                self.selection_end,
            )

            event.accept()
            return

        if self.scrubbing_playhead:
            self.scrubbing_playhead = False
            self.setSliderDown(
                False
            )
            self.sliderReleased.emit()
            event.accept()
            return

        super().mouseReleaseEvent(
            event
        )

    def mouseDoubleClickEvent(self, event):

        position = event.position()
        asset_hit = self.asset_clip_part_at_position(
            position.x(),
            position.y(),
        )
        if asset_hit is not None:
            kind, clip_id, _part = asset_hit
            self.selected_asset_clip_id = clip_id
            self.assetClipSelected.emit(
                kind,
                clip_id,
            )
            self.assetClipDoubleClicked.emit(
                kind,
                clip_id,
            )
            event.accept()
            return

        super().mouseDoubleClickEvent(
            event
        )

    def wheelEvent(self, event):

        modifiers = event.modifiers()
        delta = event.angleDelta().y()

        if delta == 0:
            delta = event.angleDelta().x()

        if (
            modifiers
            & Qt.KeyboardModifier.ControlModifier
        ):
            left, right, width = self.usable_rect()
            pointer_x = event.position().x()
            anchor_ratio = max(
                0.0,
                min(
                    1.0,
                    (
                        pointer_x
                        - left
                    )
                    / width,
                ),
            )
            anchor_value = self.x_to_value(
                pointer_x
            )
            notch_count = max(
                1,
                abs(
                    delta
                )
                / 120,
            )
            factor = (
                0.86
                if delta > 0
                else 1.16
            ) ** notch_count
            self.zoom_around(
                anchor_value,
                anchor_ratio,
                factor,
                manual=True,
            )
            event.accept()
            return

        if (
            modifiers
            & Qt.KeyboardModifier.ShiftModifier
        ):
            direction = -1 if delta > 0 else 1
            self.horizontal_pan(
                int(
                    self.visible_duration()
                    * 0.14
                    * direction
                ),
                manual=True,
            )
            event.accept()
            return

        event.ignore()

    def set_manual_cut_ranges(
        self,
        ranges: list[tuple[int, int]],
    ):

        self.manual_cut_ranges = [
            (int(start), int(end))
            for start, end in ranges
            if int(end) > int(start)
        ]

        self.update()


    def set_edited_transcript_ranges(
        self,
        ranges: list[tuple[int, int]],
    ):

        self.edited_transcript_ranges = [
            (int(start), int(end))
            for start, end in ranges
            if int(end) > int(start)
        ]

        self.update()


    def set_scene_cut_positions(
        self,
        positions: list[int],
    ):

        self.scene_cut_positions = [
            int(position)
            for position in positions
            if self.minimum()
            <= int(position)
            <= self.maximum()
        ]

        self.update()


    def set_motion_ranges(
        self,
        ranges: list[tuple[int, int]],
    ):

        self.motion_ranges = [
            (int(start), int(end))
            for start, end in ranges
            if int(end) > int(start)
        ]

        self.update()


    def set_fx_ranges(
        self,
        ranges: list[tuple[int, int]],
    ):

        self.fx_ranges = [
            (int(start), int(end))
            for start, end in ranges
            if int(end) > int(start)
        ]

        self.update()


    def set_graphic_ranges(
        self,
        ranges: list[tuple[int, int]],
    ):

        self.graphic_ranges = [
            (int(start), int(end))
            for start, end in ranges
            if int(end) > int(start)
        ]

        self.update()


    def set_caption_impact_ranges(
        self,
        ranges: list[tuple[int, int]],
    ):

        self.caption_impact_ranges = [
            (int(start), int(end))
            for start, end in ranges
            if int(end) > int(start)
        ]

        self.update()


    def set_visual_ranges(
        self,
        ranges: list[tuple[int, int]],
    ):

        self.visual_ranges = [
            (int(start), int(end))
            for start, end in ranges
            if int(end) > int(start)
        ]

        self.update()


    def set_selected_visual_range(
        self,
        start_ms: int | None,
        end_ms: int | None = None,
    ):

        if (
            start_ms is None
            or end_ms is None
            or int(end_ms) <= int(start_ms)
        ):
            self.selected_visual_range = None
        else:
            self.selected_visual_range = (
                int(
                    start_ms
                ),
                int(
                    end_ms
                ),
            )

        self.update()


    def set_asset_clips(
        self,
        clips: list[dict],
    ):

        self.asset_clips = [
            dict(
                clip
            )
            for clip in clips
            if isinstance(
                clip,
                dict,
            )
            and not bool(
                clip.get(
                    "deleted",
                    False,
                )
            )
        ]

        if (
            self.selected_asset_clip_id
            and self.asset_clip_by_id(
                self.selected_asset_clip_id
            )
            is None
        ):
            self.selected_asset_clip_id = None

        self.update()


    def set_selected_asset_clip(
        self,
        clip_id: str | None,
    ):

        self.selected_asset_clip_id = (
            str(
                clip_id
                or ""
            )
            or None
        )
        self.update()


    def clear_editor_overlays(self):

        self.manual_cut_ranges = []
        self.edited_transcript_ranges = []
        self.scene_cut_positions = []
        self.motion_ranges = []
        self.fx_ranges = []
        self.graphic_ranges = []
        self.caption_impact_ranges = []
        self.visual_ranges = []
        self.selected_visual_range = None
        self.asset_clips = []
        self.selected_asset_clip_id = None

        self.update()


    def major_tick_interval(self) -> int:

        visible_seconds = max(
            1,
            self.visible_duration()
            / 1000,
        )
        left, right, width = self.usable_rect()

        candidates = [
            0.25,
            0.5,
            1,
            2,
            5,
            10,
            15,
            30,
            60,
            120,
            300,
            600,
            900,
            1800,
            3600,
        ]

        for seconds in candidates:
            tick_count = visible_seconds / seconds
            if tick_count <= max(
                2,
                width / 82,
            ):
                return int(
                    seconds
                    * 1000
                )

        return 3600000

    def draw_range(
        self,
        painter: QPainter,
        start_ms: int,
        end_ms: int,
        y: int,
        height: int,
        color: QColor,
        selected: bool = False,
        min_width: int = 2,
    ):

        geometry = self.range_to_geometry(
            int(
                start_ms
            ),
            int(
                end_ms
            ),
        )

        if geometry is None:
            return

        x1, x2 = geometry

        if selected:
            painter.setPen(
                QColor(
                    222,
                    216,
                    202,
                    235,
                )
            )
        else:
            painter.setPen(
                Qt.PenStyle.NoPen
            )
        painter.setBrush(
            color
        )
        painter.drawRoundedRect(
            int(
                x1
            ),
            y,
            max(
                min_width,
                int(
                    x2
                    - x1
                ),
            ),
            height,
            2,
            2,
        )

    def draw_asset_clip(
        self,
        painter: QPainter,
        clip: dict,
    ):

        geometry = self.asset_clip_geometry(
            clip
        )
        if geometry is None:
            return

        clip_x, clip_y, clip_width, clip_height = geometry
        kind = str(
            clip.get(
                "kind",
                "",
            )
            or ""
        ).upper()
        selected = str(
            clip.get(
                "id",
                "",
            )
            or ""
        ) == str(
            self.selected_asset_clip_id
            or ""
        )
        active = clip.get(
            "active",
            True,
        ) is not False

        if kind == "SFX":
            fill = QColor(
                226,
                132,
                58,
                238 if active else 88,
            )
            edge = QColor(
                255,
                216,
                156,
                245,
            )
        else:
            fill = QColor(
                72,
                196,
                129,
                235 if active else 80,
            )
            edge = QColor(
                218,
                255,
                228,
                235,
            )

        painter.setPen(
            edge
            if selected
            else QColor(
                54,
                44,
                36,
                190,
            )
        )
        painter.setBrush(
            fill
        )
        painter.drawRoundedRect(
            int(
                clip_x
            ),
            int(
                clip_y
            ),
            int(
                clip_width
            ),
            int(
                clip_height
            ),
            3,
            3,
        )

        grip_color = QColor(
            255,
            235,
            205,
            230 if active else 120,
        )
        painter.fillRect(
            int(
                clip_x
            ),
            int(
                clip_y
            ),
            4,
            int(
                clip_height
            ),
            grip_color,
        )
        painter.fillRect(
            int(
                clip_x
                + clip_width
                - 4
            ),
            int(
                clip_y
            ),
            4,
            int(
                clip_height
            ),
            grip_color,
        )

        if clip_width >= 42:
            label = str(
                clip.get(
                    "label",
                    kind or "CLIP",
                )
                or kind
                or "CLIP"
            ).upper()
            if not active:
                label = "MUTED " + label

            painter.setFont(
                QFont(
                    "Segoe UI",
                    8,
                    QFont.Weight.Bold,
                )
            )
            painter.setPen(
                QColor(
                    23,
                    12,
                    7,
                    235,
                )
                if active
                else QColor(
                    150,
                    142,
                    132,
                    220,
                )
            )
            max_width = max(
                18,
                int(
                    clip_width
                    - 14
                ),
            )
            metrics = painter.fontMetrics()
            while (
                label
                and metrics.horizontalAdvance(
                    label
                )
                > max_width
            ):
                label = label[:-1]
            painter.drawText(
                int(
                    clip_x
                )
                + 8,
                int(
                    clip_y
                    + clip_height / 2
                )
                + 4,
                label,
            )

    def paintEvent(self, event):

        del event

        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        left, right, width = self.usable_rect()
        height = self.height()
        lanes = self.lane_geometry()
        ruler_top = lanes["ruler_top"]
        ruler_bottom = lanes["ruler_bottom"]
        video_top = lanes["video_top"]
        video_height = lanes["video_height"]
        edit_top = lanes["edit_top"]
        edit_height = lanes["edit_height"]
        visual_top = lanes["visual_top"]
        visual_height = lanes["visual_height"]
        sfx_top = lanes["sfx_top"]
        sfx_height = lanes["sfx_height"]
        lane_bottom = min(
            height
            - 10,
            lanes["lane_bottom"],
        )
        label_left = 12
        label_width = left - 24

        painter.fillRect(
            self.rect(),
            QColor(
                7,
                7,
                8,
            ),
        )
        painter.setPen(
            QColor(
                43,
                40,
                39,
            )
        )
        painter.setBrush(
            QColor(
                12,
                12,
                13,
            )
        )
        painter.drawRect(
            6,
            6,
            self.width()
            - 12,
            height
            - 12,
        )

        painter.fillRect(
            left,
            ruler_top,
            width,
            ruler_bottom
            - ruler_top,
            QColor(
                15,
                15,
                16,
            ),
        )

        lane_specs = [
            (
                "V1 SOURCE",
                video_top,
                video_height,
            ),
            (
                "EDITS",
                edit_top,
                edit_height,
            ),
            (
                "VISUALS",
                visual_top,
                visual_height,
            ),
            (
                "SFX",
                sfx_top,
                sfx_height,
            ),
        ]

        painter.setFont(
            QFont(
                "Segoe UI",
                8,
                QFont.Weight.Bold,
            )
        )

        for lane_label, top, lane_height in lane_specs:
            painter.setPen(
                QColor(
                    41,
                    38,
                    37,
                )
            )
            painter.setBrush(
                QColor(
                    10,
                    10,
                    11,
                )
            )
            painter.drawRect(
                label_left,
                top,
                label_width,
                lane_height,
            )
            painter.setBrush(
                QColor(
                    14,
                    14,
                    15,
                )
            )
            painter.drawRect(
                left,
                top,
                width,
                lane_height,
            )
            painter.setPen(
                QColor(
                    188,
                    177,
                    158,
                )
            )
            painter.drawText(
                label_left
                + 8,
                top
                + lane_height
                // 2
                + 4,
                lane_label,
            )

        painter.setPen(
            QColor(
                54,
                50,
                47,
            )
        )
        for y in (
            ruler_bottom,
            video_top
            + video_height,
            edit_top
            + edit_height,
            visual_top
            + visual_height,
            sfx_top
            + sfx_height,
        ):
            painter.drawLine(
                left,
                y,
                right,
                y,
            )

        if self.source_duration() > 0:
            self.draw_range(
                painter,
                0,
                self.source_duration(),
                video_top
                + 9,
                video_height
                - 18,
                QColor(
                    14,
                    35,
                    40,
                    170,
                ),
                min_width=1,
            )

        if self.selection_end > self.selection_start:
            clip_geometry = self.source_clip_geometry()

            if clip_geometry is not None:
                clip_x, clip_y, clip_width, clip_height = (
                    clip_geometry
                )

                painter.setPen(
                    QColor(
                        192,
                        255,
                        255,
                        245,
                    )
                    if self.source_clip_selected
                    else QColor(
                        63,
                        230,
                        240,
                        220,
                    )
                )
                painter.setBrush(
                    QColor(
                        20,
                        178,
                        204,
                        230,
                    )
                )
                painter.drawRoundedRect(
                    int(
                        clip_x
                    ),
                    int(
                        clip_y
                    ),
                    int(
                        clip_width
                    ),
                    int(
                        clip_height
                    ),
                    3,
                    3,
                )

                painter.fillRect(
                    int(
                        clip_x
                    ),
                    int(
                        clip_y
                    ),
                    7,
                    int(
                        clip_height
                    ),
                    QColor(
                        207,
                        255,
                        255,
                        245,
                    ),
                )
                painter.fillRect(
                    int(
                        clip_x
                        + clip_width
                        - 7
                    ),
                    int(
                        clip_y
                    ),
                    7,
                    int(
                        clip_height
                    ),
                    QColor(
                        207,
                        255,
                        255,
                        245,
                    ),
                )

                painter.setFont(
                    QFont(
                        "Segoe UI",
                        8,
                        QFont.Weight.Bold,
                    )
                )
                painter.setPen(
                    QColor(
                        5,
                        22,
                        26,
                        245,
                    )
                )
                label = (
                    "SOURCE VIDEO"
                    if clip_width > 120
                    else "SRC"
                )
                painter.drawText(
                    int(
                        clip_x
                    )
                    + 14,
                    int(
                        clip_y
                        + clip_height
                        / 2
                    )
                    + 4,
                    label,
                )

        interval = self.major_tick_interval()
        minor_interval = max(
            100,
            interval // 5,
        )
        first_minor = (
            self.viewport_start
            // minor_interval
        ) * minor_interval

        painter.setFont(
            QFont(
                "Consolas",
                8,
            )
        )

        tick = first_minor
        last_label_right = -1000
        while tick <= self.viewport_end + minor_interval:
            if tick >= self.viewport_start:
                x = int(
                    self.value_to_x(
                        tick
                    )
                )
                is_major = (
                    tick % interval
                    == 0
                )
                painter.setPen(
                    QColor(
                        160,
                        151,
                        136,
                        215,
                    )
                    if is_major
                    else QColor(
                        79,
                        73,
                        68,
                        150,
                    )
                )
                painter.drawLine(
                    x,
                    ruler_top
                    + 2
                    if is_major
                    else ruler_top
                    + 15,
                    x,
                    lane_bottom,
                )

                if is_major:
                    label = (
                        format_precise_time(
                            tick
                        )
                        if self.visible_duration() <= 15000
                        else format_time(
                            tick
                        )
                    )
                    label_width = painter.fontMetrics().horizontalAdvance(
                        label
                    )
                    if x - label_width // 2 > last_label_right + 6:
                        painter.drawText(
                            x
                            - label_width // 2,
                            ruler_top
                            + 13,
                            label,
                        )
                        last_label_right = (
                            x
                            + label_width // 2
                        )
            tick += minor_interval

        for start_ms, end_ms in self.manual_cut_ranges:
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                edit_top
                + 3,
                7,
                QColor(194, 66, 78, 230),
            )

        for start_ms, end_ms in self.edited_transcript_ranges:
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                edit_top
                + 13,
                7,
                QColor(211, 153, 74, 230),
            )

        for start_ms, end_ms in self.caption_impact_ranges:
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                edit_top
                + 19,
                3,
                QColor(255, 214, 82, 230),
                min_width=3,
            )

        for index, (
            start_ms,
            end_ms,
            score,
        ) in enumerate(
            self.suggested_ranges
        ):
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                video_top
                + video_height
                - 17,
                12,
                QColor(
                    126,
                    112,
                    255,
                    235
                    if index == self.selected_suggestion_index
                    else max(
                        105,
                        180
                        - index
                        * 24,
                    ),
                ),
                selected=(
                    index
                    == self.selected_suggestion_index
                ),
                min_width=5,
            )

        for start_ms, end_ms in self.motion_ranges:
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                edit_top
                + 2,
                6,
                QColor(153, 113, 221, 205),
            )

        for start_ms, end_ms in self.fx_ranges:
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                edit_top
                + 9,
                5,
                QColor(230, 79, 210, 210),
            )

        for start_ms, end_ms in self.graphic_ranges:
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                edit_top
                + 15,
                5,
                QColor(96, 233, 154, 215),
            )

        for start_ms, end_ms in self.visual_ranges:
            selected = self.selected_visual_range == (
                int(
                    start_ms
                ),
                int(
                    end_ms
                ),
            )
            self.draw_range(
                painter,
                start_ms,
                end_ms,
                visual_top
                + (
                    4
                    if selected
                    else 7
                ),
                max(
                    6,
                    visual_height
                    - (
                        8
                        if selected
                        else 14
                    ),
                ),
                QColor(
                    72,
                    196,
                    129,
                    240,
                ),
                selected=selected,
            )

        for clip in self.asset_clips:
            self.draw_asset_clip(
                painter,
                clip,
            )

        painter.setPen(
            QColor(88, 190, 206, 215)
        )
        for position_ms in self.scene_cut_positions:
            if (
                self.viewport_start
                <= position_ms
                <= self.viewport_end
            ):
                x = int(
                    self.value_to_x(
                        position_ms
                    )
                )
                painter.drawLine(
                    x,
                    ruler_bottom,
                    x,
                    lane_bottom,
                )

        if self.selection_end > self.selection_start:
            for label, value in (
                (
                    "IN",
                    self.selection_start,
                ),
                (
                    "OUT",
                    self.selection_end,
                ),
            ):
                if not (
                    self.viewport_start
                    <= value
                    <= self.viewport_end
                ):
                    continue

                x = int(
                    self.value_to_x(
                        value
                    )
                )
                painter.setPen(
                    QColor(
                        226,
                        211,
                        191,
                    )
                )
                painter.setBrush(
                    QColor(
                        28,
                        213,
                        231,
                        255,
                    )
                )
                painter.drawPolygon(
                    QPolygon(
                        [
                            QPoint(
                                x,
                                ruler_bottom
                                - 12,
                            ),
                            QPoint(
                                x
                                - 7,
                                ruler_bottom,
                            ),
                            QPoint(
                                x
                                + 7,
                                ruler_bottom,
                            ),
                        ]
                    )
                )
                painter.drawLine(
                    x,
                    ruler_bottom,
                    x,
                    lane_bottom,
                )
                painter.setFont(
                    QFont(
                        "Consolas",
                        8,
                        QFont.Weight.Bold,
                    )
                )
                painter.drawText(
                    x
                    + 6,
                    ruler_bottom
                    - 14,
                    label,
                )

                if self.dragging_handle in {
                    "start",
                    "end",
                }:
                    active_label = (
                        "IN"
                        if self.dragging_handle == "start"
                        else "OUT"
                    )
                    if active_label == label:
                        time_label = (
                            f"{label} {format_precise_time(value)}"
                        )
                        label_width = painter.fontMetrics().horizontalAdvance(
                            time_label
                        )
                        text_x = max(
                            left
                            + 5,
                            min(
                                x
                                + 15,
                                right
                                - label_width
                                - 7,
                            ),
                        )
                        painter.fillRect(
                            text_x
                            - 5,
                            lane_bottom
                            - 22,
                            label_width
                            + 10,
                            17,
                            QColor(
                                7,
                                31,
                                36,
                                235,
                            ),
                        )
                        painter.drawText(
                            text_x,
                            lane_bottom
                            - 9,
                            time_label,
                        )

        playhead = self.value()
        if (
            self.viewport_start
            <= playhead
            <= self.viewport_end
        ):
            x = int(
                self.value_to_x(
                    playhead
                )
            )
            painter.setPen(
                QColor(
                    201,
                    56,
                    79,
                    255,
                )
            )
            painter.drawLine(
                x,
                ruler_top
                - 2,
                x,
                lane_bottom,
            )
            painter.setBrush(
                QColor(
                    201,
                    56,
                    79,
                    255,
                )
            )
            painter.drawPolygon(
                QPolygon(
                    [
                        QPoint(
                            x,
                            ruler_top
                            - 3,
                        ),
                        QPoint(
                            x
                            - 8,
                            ruler_top
                            + 9,
                        ),
                        QPoint(
                            x
                            + 8,
                            ruler_top
                            + 9,
                        ),
                    ]
                )
            )
            painter.setFont(
                QFont(
                    "Consolas",
                    8,
                    QFont.Weight.Bold,
                )
            )
            painter.drawText(
                min(
                    right
                    - 76,
                    x
                    + 7,
                ),
                ruler_top
                + 26,
                format_precise_time(
                    playhead
                ),
            )

        painter.end()


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
            str(ROOT),
            (
                "Video Files "
                "(*.mp4 *.mov *.mkv *.avi *.webm *.m4v)"
            ),
        )

        if filename:
            self.load_callback(
                Path(filename)
            )

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


class ShortsFactoryWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.video_path: Path | None = None

        self.start_ms = 0
        self.end_ms = 0
        
        self.render_process = QProcess(self)

        self.render_process.setWorkingDirectory(
            str(ROOT)
        )

        # Force UTF-8 for render.py and every Python subprocess it launches.
        # This prevents Windows cp1252 crashes when logs contain emoji or
        # other Unicode characters.
        process_env = QProcessEnvironment.systemEnvironment()
        process_env.insert("PYTHONIOENCODING", "utf-8")
        process_env.insert("PYTHONUTF8", "1")

        self.render_process.setProcessEnvironment(
            process_env
        )

        self.render_process.readyReadStandardOutput.connect(
            self.read_render_output
        )

        self.render_process.readyReadStandardError.connect(
            self.read_render_error
        )

        self.render_process.finished.connect(
            self.render_finished
        )

        self.analysis_process = QProcess(self)

        self.analysis_process.setWorkingDirectory(
            str(ROOT)
        )

        self.analysis_process.setProcessEnvironment(
            process_env
        )

        self.analysis_process.readyReadStandardOutput.connect(
            self.read_analysis_output
        )

        self.analysis_process.readyReadStandardError.connect(
            self.read_analysis_error
        )

        self.analysis_process.finished.connect(
            self.analysis_finished
        )

        self.analysis_stage: str | None = None

        self.visual_process = QProcess(self)

        self.visual_process.setWorkingDirectory(
            str(ROOT)
        )

        self.visual_process.setProcessEnvironment(
            process_env
        )

        self.visual_process.readyReadStandardOutput.connect(
            self.read_visual_output
        )

        self.visual_process.readyReadStandardError.connect(
            self.read_visual_error
        )

        self.visual_process.finished.connect(
            self.visual_plan_finished
        )

        self.visual_plan_slots: list[dict] = []
        self.editor_asset_plan: dict = load_editor_asset_plan()
        self.selected_sfx_clip_id: str | None = None
        self.sfx_preview_triggered: set[str] = set()
        self.active_visual_preview_clip_id: str | None = None
        self.active_visual_preview_signature: tuple | None = None
        self.active_visual_preview_layout_signature: tuple | None = None
        self.active_visual_preview_pixmap = QPixmap()

        self.visual_asset_process = QProcess(self)

        self.visual_asset_process.setWorkingDirectory(
            str(ROOT)
        )

        self.visual_asset_process.setProcessEnvironment(
            process_env
        )

        self.visual_asset_process.readyReadStandardOutput.connect(
            self.read_visual_asset_output
        )

        self.visual_asset_process.readyReadStandardError.connect(
            self.read_visual_asset_error
        )

        self.visual_asset_process.finished.connect(
            self.visual_asset_finished
        )

        self.image_status_process = QProcess(self)

        self.image_status_process.setWorkingDirectory(
            str(ROOT)
        )

        self.image_status_process.setProcessEnvironment(
            process_env
        )

        self.image_status_process.readyReadStandardOutput.connect(
            self.read_image_status_output
        )

        self.image_status_process.readyReadStandardError.connect(
            self.read_image_status_error
        )

        self.image_status_process.finished.connect(
            self.image_status_finished
        )

        self.image_status_stdout = ""
        self.image_status_stderr = ""
        self.pending_image_model_change = ""

        # Pre-render subject-aware 9:16 framing stage.
        self.reframe_process = QProcess(self)

        self.reframe_process.setWorkingDirectory(
            str(ROOT)
        )

        self.reframe_process.setProcessEnvironment(
            process_env
        )

        self.reframe_process.readyReadStandardOutput.connect(
            self.read_reframe_output
        )

        self.reframe_process.readyReadStandardError.connect(
            self.read_reframe_error
        )

        self.reframe_process.finished.connect(
            self.reframe_finished
        )

        self.pending_render_source: Path | None = None
        self.pending_render_duration_seconds = 0.0
        self.pending_original_start_seconds = 0.0
        self.pending_original_end_seconds = 0.0
        self.render_progress_active = False
        self.render_progress_started_at = 0.0
        self.render_progress_stage_started_at = 0.0
        self.render_progress_estimate_seconds = 0.0
        self.render_progress_stage = "idle"
        self.render_progress_floor = 0
        self.render_progress_ceiling = 100
        self.render_progress_stage_estimate_seconds = 1.0
        self.render_progress_last_value = 0

        self.music_path: Path | None = None
        self.music_volume = 18

        # AI clip candidates currently shown in the editor.
        # Each item stores rank, timing, score, hook, description, and reason.
        self.ai_candidates: list[dict] = []

        self.source_transcript_segments: list[dict] = []

        # Manual transcript cuts are stored using absolute source timing.
        # They survive switching between AI clip candidates for the same source.
        self.manual_cut_segments: set[tuple[int, int]] = set()

        # User-fixed transcript text, keyed by absolute source segment timing.
        self.transcript_corrections: dict[tuple[int, int], str] = {}

        self.image_ai_state = "not_checked"
        self.image_ai_models: list[dict] = []
        self.current_image_model_title = ""
        self.selected_image_model_title = ""
        self.updating_image_model_combo = False
        self.image_quality = "BALANCED"
        self.visual_asset_output_buffer = ""
        self.selected_visual_slot_index: int | None = None
        self.updating_visual_inspector = False
        self.user_visual_edits = False
        self.updating_timeline_controls = False
        self.paused_seek_refresh_pending = False
        self.selection_loop_enabled = False
        self.play_request_counter = 0
        self.settings = QSettings(
            "ShortsFactory",
            "ShortsFactory",
        )
        self.preview_volume = int(
            self.settings.value(
                "preview/volume",
                80,
            )
            or 80
        )
        self.preview_volume = max(
            0,
            min(
                100,
                self.preview_volume,
            ),
        )
        self.transcription_quality = str(
            self.settings.value(
                "transcription/quality",
                "AUTO",
            )
            or "AUTO"
        ).upper()
        if self.transcription_quality not in {
            "AUTO",
            "FAST",
            "ACCURATE",
        }:
            self.transcription_quality = "AUTO"

        self.edit_energy = normalize_energy(
            self.settings.value(
                "render/edit_energy",
                DEFAULT_ENERGY,
            )
            or DEFAULT_ENERGY
        )
        self.sfx_mode = normalize_sfx_mode(
            self.settings.value(
                "render/sfx_mode",
                "AUTO",
            )
            or "AUTO"
        )

        self.music_process = QProcess(self)

        self.music_process.setWorkingDirectory(
            str(ROOT)
        )

        self.music_process.setProcessEnvironment(
            process_env
        )

        self.music_process.readyReadStandardOutput.connect(
            self.read_music_output
        )

        self.music_process.readyReadStandardError.connect(
            self.read_music_error
        )

        self.music_process.finished.connect(
            self.music_finished
        )

        self.sfx_process = QProcess(self)

        self.sfx_process.setWorkingDirectory(
            str(ROOT)
        )

        self.sfx_process.setProcessEnvironment(
            process_env
        )

        self.sfx_process.readyReadStandardOutput.connect(
            self.read_sfx_output
        )

        self.sfx_process.readyReadStandardError.connect(
            self.read_sfx_error
        )

        self.sfx_process.finished.connect(
            self.sfx_finished
        )

        self.render_progress_timer = QTimer(
            self
        )
        self.render_progress_timer.setInterval(
            500
        )
        self.render_progress_timer.timeout.connect(
            self.update_render_progress
        )

        self.setWindowTitle(
            "ShortsFactory"
        )

        self.resize(
            1400,
            1040,
        )

        self.setMinimumSize(
            1120,
            720,
        )

        self.audio_output = QAudioOutput()

        self.audio_output.setVolume(
            self.preview_volume
            / 100
        )

        self.player = QMediaPlayer()

        self.player.setAudioOutput(
            self.audio_output
        )

        self.player.positionChanged.connect(
            self.position_changed
        )

        self.player.durationChanged.connect(
            self.duration_changed
        )

        self.player.playbackStateChanged.connect(
            self.update_play_button
        )

        self.player.mediaStatusChanged.connect(
            self.media_status_changed
        )

        self.player.errorOccurred.connect(
            self.playback_error_occurred
        )

        self.sfx_preview_audio = QAudioOutput()
        self.sfx_preview_player = QMediaPlayer()
        self.sfx_preview_player.setAudioOutput(
            self.sfx_preview_audio
        )

        self.build_ui()
        self.apply_style()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(
                self
            )
        QTimer.singleShot(
            0,
            self.restore_layout_settings,
        )
        self.update_image_ai_indicator()
        self.load_selected_visual_into_inspector()
        self.load_editor_asset_plan_state()

    def build_ui(self):

        root = QWidget()
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(18, 16, 18, 18)
        main_layout.setSpacing(14)

        # ====================================================
        # HEADER
        # ====================================================

        header_frame = QFrame()
        header_frame.setObjectName("HeaderPanel")

        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(12)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(2)

        title = QLabel("ShortsFactory")
        title.setObjectName("AppTitle")

        subtitle = QLabel("SLAUGHTERHOUSE EDIT SYSTEM  //  CONTENT PROCESSING / CUT FLOOR")
        subtitle.setObjectName("AppSubtitle")

        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)

        mode_badge = QLabel("CUT FLOOR ARMED")
        mode_badge.setObjectName("ModeBadge")
        mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_layout.addLayout(title_stack, 1)
        header_layout.addWidget(mode_badge)

        main_layout.addWidget(header_frame)

        # ====================================================
        # WORKSPACE
        # ====================================================

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setObjectName("MainSplitter")
        workspace.setChildrenCollapsible(False)
        self.main_splitter = workspace

        # ----------------------------------------------------
        # LEFT RAIL / SOURCE PANEL
        # ----------------------------------------------------

        source_frame = QFrame()
        source_frame.setObjectName("Panel")
        source_frame.setMinimumWidth(220)
        source_frame.setMaximumWidth(440)

        source_layout = QVBoxLayout(source_frame)
        source_layout.setContentsMargins(16, 16, 16, 16)
        source_layout.setSpacing(12)

        left_title = QLabel("SOURCE FEED")
        left_title.setObjectName("SectionTitle")

        self.drop_zone = DropZone(self.load_video)
        self.drop_zone.setMinimumHeight(360)
        self.drop_zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.file_label = QLabel("No video loaded")
        self.file_label.setObjectName("FileLabel")
        self.file_label.setWordWrap(True)

        source_hint = QLabel("Drop a source clip, podcast, movie scene, or episode segment here to begin scouting Shorts moments.")
        source_hint.setObjectName("HintLabel")
        source_hint.setWordWrap(True)

        transcription_label = QLabel("TRANSCRIPTION")
        transcription_label.setObjectName("TinyLabel")

        self.transcription_quality_combo = QComboBox()
        self.transcription_quality_combo.setObjectName("CompactCombo")
        self.transcription_quality_combo.addItems(
            [
                "AUTO",
                "FAST",
                "ACCURATE",
            ]
        )
        self.transcription_quality_combo.setCurrentText(
            self.transcription_quality
        )
        self.transcription_quality_combo.setToolTip(
            "Choose local transcription quality for AI Clip Hunter."
        )
        self.transcription_quality_combo.currentTextChanged.connect(
            self.transcription_quality_changed
        )

        transcription_row = QHBoxLayout()
        transcription_row.setSpacing(8)
        transcription_row.addWidget(transcription_label)
        transcription_row.addStretch()
        transcription_row.addWidget(
            self.transcription_quality_combo
        )

        self.find_clips_button = QPushButton("✦ Find Best Clips")
        self.find_clips_button.setObjectName("AIButton")
        self.find_clips_button.setToolTip("Transcribe the source and highlight AI-ranked Short candidates.")
        self.find_clips_button.setEnabled(False)
        self.find_clips_button.clicked.connect(self.find_best_clips)

        self.generate_button = QPushButton("Generate Short")
        self.generate_button.setObjectName("GenerateButton")
        self.generate_button.setEnabled(False)
        self.generate_button.clicked.connect(self.generate_short)

        source_layout.addWidget(left_title)
        source_layout.addWidget(self.drop_zone, 1)
        source_layout.addWidget(self.file_label)
        source_layout.addWidget(source_hint)
        source_layout.addLayout(transcription_row)
        source_layout.addSpacing(6)
        source_layout.addWidget(self.find_clips_button)
        source_layout.addWidget(self.generate_button)

        workspace.addWidget(source_frame)

        # ----------------------------------------------------
        # CENTER / PREVIEW PANEL
        # ----------------------------------------------------

        center_widget = QWidget()
        center_widget.setObjectName("CenterColumn")
        center_widget.setMinimumWidth(520)

        center_scroll = QScrollArea()
        center_scroll.setObjectName("CenterScroll")
        center_scroll.setWidgetResizable(True)
        center_scroll.setFrameShape(QFrame.Shape.NoFrame)
        center_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        center_scroll.setWidget(center_widget)
        self.center_scroll = center_scroll

        center_column = QVBoxLayout(center_widget)
        center_column.setContentsMargins(0, 0, 0, 0)
        center_column.setSpacing(14)

        center_editor_stack = QWidget()
        center_editor_stack.setObjectName("CenterEditorStack")
        center_editor_layout = QVBoxLayout(center_editor_stack)
        center_editor_layout.setContentsMargins(0, 0, 0, 0)
        center_editor_layout.setSpacing(14)

        preview_frame = QFrame()
        preview_frame.setObjectName("PreviewPanel")

        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(16, 16, 16, 14)
        preview_layout.setSpacing(10)

        preview_header = QHBoxLayout()
        preview_header.setSpacing(10)

        preview_title = QLabel("PREVIEW MONITOR")
        preview_title.setObjectName("SectionTitle")

        preview_tag = QLabel("TRIM / SEEK / AUDITION")
        preview_tag.setObjectName("MicroBadge")

        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        preview_header.addWidget(preview_tag)

        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("VideoPreview")
        self.video_widget.setMinimumSize(520, 260)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.player.setVideoOutput(self.video_widget)

        # Live AI visual preview. These labels sit above the source preview
        # and mirror the active AI_VISUAL clip at the current source time.
        self.ai_visual_preview_dim = QLabel(self.video_widget)
        self.ai_visual_preview_dim.setObjectName("VisualPreviewDim")
        self.ai_visual_preview_dim.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.ai_visual_preview_dim.hide()

        self.ai_visual_preview_overlay = QLabel(self.video_widget)
        self.ai_visual_preview_overlay.setObjectName("VisualPreviewOverlay")
        self.ai_visual_preview_overlay.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.ai_visual_preview_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.ai_visual_preview_overlay.hide()

        playback = QHBoxLayout()
        playback.setSpacing(10)

        self.play_button = QPushButton("PLAY")
        self.play_button.setObjectName("PlayButton")
        self.play_button.setToolTip("Play or pause the preview. Shortcut: Space")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.toggle_playback)

        self.current_time_label = QLabel("00:00.000")
        self.current_time_label.setObjectName("TimeLabel")

        self.duration_label = QLabel("00:00.000")
        self.duration_label.setObjectName("TimeLabel")

        preview_volume_text = QLabel("Preview")
        preview_volume_text.setObjectName("MicroLabel")

        self.preview_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.preview_volume_slider.setObjectName("PreviewVolumeSlider")
        self.preview_volume_slider.setRange(0, 100)
        self.preview_volume_slider.setValue(self.preview_volume)
        self.preview_volume_slider.setFixedWidth(120)
        self.preview_volume_slider.setToolTip(
            "Editor preview volume only. This does not change rendered output volume."
        )
        self.preview_volume_slider.valueChanged.connect(
            self.preview_volume_changed
        )

        self.preview_volume_label = QLabel(
            f"{self.preview_volume}%"
        )
        self.preview_volume_label.setObjectName("MusicVolumeLabel")

        playback.addWidget(self.play_button)
        playback.addWidget(self.current_time_label)
        playback.addWidget(QLabel("/"))
        playback.addWidget(self.duration_label)
        playback.addStretch()
        playback.addWidget(preview_volume_text)
        playback.addWidget(self.preview_volume_slider)
        playback.addWidget(self.preview_volume_label)

        video_stack = QWidget()
        video_stack.setObjectName("VideoStack")
        video_stack_layout = QVBoxLayout(video_stack)
        video_stack_layout.setContentsMargins(0, 0, 0, 0)
        video_stack_layout.setSpacing(10)
        video_stack_layout.addWidget(self.video_widget, 1)
        video_stack_layout.addLayout(playback)

        self.timeline = SuggestionSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.sliderMoved.connect(self.seek_video)
        self.timeline.sliderReleased.connect(self.seek_to_slider_position)
        self.timeline.suggestionClicked.connect(self.select_ai_suggestion)
        self.timeline.selectionChanged.connect(self.timeline_selection_changed)
        self.timeline.viewportChanged.connect(self.timeline_viewport_changed)
        self.timeline.assetClipSelected.connect(self.editor_asset_clip_selected)
        self.timeline.assetClipChanged.connect(self.editor_asset_clip_changed)
        self.timeline.assetClipDoubleClicked.connect(
            self.editor_asset_clip_double_clicked
        )

        timeline_panel = QWidget()
        timeline_panel.setObjectName("TimelinePanel")
        timeline_panel.setMinimumHeight(260)
        timeline_panel_layout = QVBoxLayout(timeline_panel)
        timeline_panel_layout.setContentsMargins(0, 0, 0, 0)
        timeline_panel_layout.setSpacing(8)

        timeline_tools = QHBoxLayout()
        timeline_tools.setSpacing(8)

        timeline_title = QLabel("EDITOR TIMELINE")
        timeline_title.setObjectName("SectionTitle")

        self.timeline_time_label = QLabel("00:00.000 / 00:00.000")
        self.timeline_time_label.setObjectName("TimeLabel")

        zoom_out_label = QLabel("-")
        zoom_out_label.setObjectName("MicroLabel")

        self.timeline_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_zoom_slider.setObjectName("TimelineZoom")
        self.timeline_zoom_slider.setRange(0, 100)
        self.timeline_zoom_slider.setValue(0)
        self.timeline_zoom_slider.setFixedWidth(140)
        self.timeline_zoom_slider.valueChanged.connect(
            self.timeline_zoom_slider_changed
        )

        zoom_in_label = QLabel("+")
        zoom_in_label.setObjectName("MicroLabel")

        self.fit_selection_button = QPushButton("FIT")
        self.fit_selection_button.setObjectName("TinyButton")
        self.fit_selection_button.setToolTip("Fit the current IN/OUT selection on the timeline. Shortcut: F")
        self.fit_selection_button.clicked.connect(self.fit_timeline_selection)

        self.fit_source_button = QPushButton("SRC")
        self.fit_source_button.setObjectName("TinyButton")
        self.fit_source_button.setToolTip("Fit the entire source on the timeline. Shortcut: Ctrl+0")
        self.fit_source_button.clicked.connect(self.fit_timeline_source)

        timeline_tools.addWidget(timeline_title)
        timeline_tools.addSpacing(8)
        timeline_tools.addWidget(self.timeline_time_label)
        timeline_tools.addStretch()
        timeline_tools.addWidget(zoom_out_label)
        timeline_tools.addWidget(self.timeline_zoom_slider)
        timeline_tools.addWidget(zoom_in_label)
        timeline_tools.addWidget(self.fit_selection_button)
        timeline_tools.addWidget(self.fit_source_button)

        self.timeline_navigator = TimelineNavigator()
        self.timeline_navigator.setObjectName("TimelineNavigator")
        self.timeline_navigator.viewportChangeRequested.connect(
            self.timeline_navigator_changed
        )

        timeline_panel_layout.addLayout(timeline_tools)
        timeline_panel_layout.addWidget(self.timeline, 1)
        timeline_panel_layout.addWidget(self.timeline_navigator)

        self.suggestions_label = QLabel("AI clips appear as purple ranges on V1. Click a range to load that pick, then drag IN / OUT handles to tune the cut.")
        self.suggestions_label.setObjectName("SuggestionLabel")
        self.suggestions_label.setWordWrap(True)

        trim_help = QLabel("IN   ●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●   OUT")
        trim_help.setObjectName("TrimHelp")
        trim_help.setAlignment(Qt.AlignmentFlag.AlignCenter)

        selection_frame = QFrame()
        selection_frame.setObjectName("SubPanel")
        selection_layout = QHBoxLayout(selection_frame)
        selection_layout.setContentsMargins(12, 10, 12, 10)
        selection_layout.setSpacing(10)

        self.start_button = QPushButton("Set Start")
        self.end_button = QPushButton("Set End")
        self.start_button.clicked.connect(self.set_start)
        self.end_button.clicked.connect(self.set_end)

        self.selection_label = QLabel("Selection: 00:00 → 00:00")
        self.selection_label.setObjectName("SelectionLabel")

        selection_layout.addWidget(self.start_button)
        selection_layout.addWidget(self.end_button)
        selection_layout.addSpacing(8)
        selection_layout.addWidget(self.selection_label, 1)

        self.preview_timeline_splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_timeline_splitter.setObjectName("PreviewTimelineSplitter")
        self.preview_timeline_splitter.setChildrenCollapsible(False)
        self.preview_timeline_splitter.addWidget(video_stack)
        self.preview_timeline_splitter.addWidget(timeline_panel)
        self.preview_timeline_splitter.setStretchFactor(0, 1)
        self.preview_timeline_splitter.setStretchFactor(1, 0)
        self.preview_timeline_splitter.setMinimumHeight(560)
        self.preview_timeline_splitter.setSizes([330, 300])

        preview_layout.addLayout(preview_header)
        preview_layout.addWidget(self.preview_timeline_splitter, 1)
        preview_layout.addWidget(self.suggestions_label)
        preview_layout.addWidget(selection_frame)

        self.timeline_legend = QLabel(
            "TIMELINE  //  CYAN SOURCE   RED CUT   AMBER EDITED   GOLD CAPTION   VIOLET MOTION   MAGENTA FX   GREEN VISUAL/GRAPHIC"
        )
        self.timeline_legend.setObjectName("MicroLabel")
        self.timeline_legend.setToolTip(
            "Red = transcript cut, amber = corrected transcript text, "
            "bright cyan = real camera cut, violet = automatic motion, "
            "magenta = filter/FX hit, green = planned graphic or AI visual."
        )

        preview_layout.addWidget(
            self.timeline_legend
        )

        center_editor_layout.addWidget(preview_frame, 1)

        audio_frame = QFrame()
        audio_frame.setObjectName("Panel")
        audio_layout = QVBoxLayout(audio_frame)
        audio_layout.setContentsMargins(16, 14, 16, 14)
        audio_layout.setSpacing(8)

        audio_top_row = QHBoxLayout()
        audio_top_row.setSpacing(10)

        audio_bottom_row = QHBoxLayout()
        audio_bottom_row.setSpacing(10)

        audio_title = QLabel("AUDIO")
        audio_title.setObjectName("SectionTitle")

        edit_energy_label = QLabel("Edit Energy")
        edit_energy_label.setObjectName("MicroLabel")

        self.edit_energy_combo = QComboBox()
        self.edit_energy_combo.setObjectName("CompactCombo")
        self.edit_energy_combo.addItems(
            [
                "LOW",
                "PUNCHY",
                "MAXIMUM",
            ]
        )
        self.edit_energy_combo.setCurrentText(
            self.edit_energy
        )
        self.edit_energy_combo.setToolTip(
            "Controls exported visual intensity for captions, motion, emojis, and visual effects."
        )
        self.edit_energy_combo.currentTextChanged.connect(
            self.edit_energy_changed
        )

        sfx_mode_label = QLabel("Sound FX")
        sfx_mode_label.setObjectName("MicroLabel")

        self.sfx_mode_combo = QComboBox()
        self.sfx_mode_combo.setObjectName("CompactCombo")
        self.sfx_mode_combo.addItems(
            [
                "AUTO",
                "OFF",
            ]
        )
        self.sfx_mode_combo.setCurrentText(
            self.sfx_mode
        )
        self.sfx_mode_combo.setToolTip(
            "AUTO plans safe local or generated sound effects. OFF leaves SFX out of the render."
        )
        self.sfx_mode_combo.currentTextChanged.connect(
            self.sfx_mode_changed
        )

        self.generate_sfx_button = QPushButton("Generate SFX")
        self.generate_sfx_button.setObjectName("QuietButton")
        self.generate_sfx_button.setToolTip(
            "Plan editable sound-effect clips for the current selection."
        )
        self.generate_sfx_button.setEnabled(False)
        self.generate_sfx_button.clicked.connect(self.generate_sfx)

        self.open_sfx_folder_button = QPushButton("SFX Folder")
        self.open_sfx_folder_button.setObjectName("QuietButton")
        self.open_sfx_folder_button.setToolTip(
            "Open assets/sfx. Add audio files with descriptive names like whoosh, impact, pop, money, or glitch."
        )
        self.open_sfx_folder_button.clicked.connect(self.open_sfx_folder)

        self.music_button = QPushButton("♫ Add Music")
        self.music_button.setObjectName("MusicButton")
        self.music_button.setToolTip("Import an MP3, WAV, M4A, AAC, FLAC, or OGG file to mix under the Short.")
        self.music_button.clicked.connect(self.choose_music)

        self.music_label = QLabel("No background music")
        self.music_label.setObjectName("MusicLabel")

        self.music_volume_label = QLabel("18%")
        self.music_volume_label.setObjectName("MusicVolumeLabel")

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setObjectName("MusicVolumeSlider")
        self.music_volume_slider.setRange(0, 50)
        self.music_volume_slider.setValue(self.music_volume)
        self.music_volume_slider.setFixedWidth(130)
        self.music_volume_slider.valueChanged.connect(self.music_volume_changed)

        self.clear_music_button = QPushButton("Remove")
        self.clear_music_button.setObjectName("QuietButton")
        self.clear_music_button.setEnabled(False)
        self.clear_music_button.clicked.connect(self.clear_music)

        self.narrator_button = QPushButton("🎙 AI Narrator · Soon")
        self.narrator_button.setObjectName("QuietButton")
        self.narrator_button.setEnabled(False)
        self.narrator_button.setToolTip("Planned: generate and mix AI narration/commentary over selected source clips.")

        self.sfx_context_frame = QFrame()
        self.sfx_context_frame.setObjectName("SubPanel")
        self.sfx_context_frame.setVisible(False)

        sfx_context_layout = QHBoxLayout(self.sfx_context_frame)
        sfx_context_layout.setContentsMargins(8, 6, 8, 6)
        sfx_context_layout.setSpacing(8)

        sfx_selected_label = QLabel("Selected SFX")
        sfx_selected_label.setObjectName("MicroLabel")

        self.sfx_clip_label = QLabel("No SFX selected")
        self.sfx_clip_label.setObjectName("MusicLabel")
        self.sfx_clip_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.sfx_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sfx_volume_slider.setObjectName("MusicVolumeSlider")
        self.sfx_volume_slider.setRange(0, 80)
        self.sfx_volume_slider.setValue(25)
        self.sfx_volume_slider.setFixedWidth(96)
        self.sfx_volume_slider.setEnabled(False)
        self.sfx_volume_slider.setToolTip(
            "Selected SFX clip volume. This affects preview and final SFX mix."
        )
        self.sfx_volume_slider.valueChanged.connect(self.sfx_volume_changed)

        self.swap_sfx_button = QPushButton("Swap")
        self.swap_sfx_button.setObjectName("QuietButton")
        self.swap_sfx_button.setEnabled(False)
        self.swap_sfx_button.setToolTip(
            "Replace the selected SFX clip while keeping its timing."
        )
        self.swap_sfx_button.clicked.connect(self.swap_selected_sfx_clip)

        self.disable_sfx_button = QPushButton("Disable")
        self.disable_sfx_button.setObjectName("QuietButton")
        self.disable_sfx_button.setEnabled(False)
        self.disable_sfx_button.clicked.connect(self.toggle_selected_sfx_clip)

        self.delete_sfx_button = QPushButton("Delete")
        self.delete_sfx_button.setObjectName("CutButton")
        self.delete_sfx_button.setEnabled(False)
        self.delete_sfx_button.clicked.connect(self.delete_selected_sfx_clip)

        sfx_context_layout.addWidget(sfx_selected_label)
        sfx_context_layout.addWidget(self.sfx_clip_label, 1)
        sfx_context_layout.addWidget(self.sfx_volume_slider)
        sfx_context_layout.addWidget(self.swap_sfx_button)
        sfx_context_layout.addWidget(self.disable_sfx_button)
        sfx_context_layout.addWidget(self.delete_sfx_button)

        audio_top_row.addWidget(audio_title)
        audio_top_row.addSpacing(6)
        audio_top_row.addWidget(edit_energy_label)
        audio_top_row.addWidget(self.edit_energy_combo)
        audio_top_row.addSpacing(12)
        audio_top_row.addWidget(sfx_mode_label)
        audio_top_row.addWidget(self.sfx_mode_combo)
        audio_top_row.addWidget(self.generate_sfx_button)
        audio_top_row.addWidget(self.open_sfx_folder_button)
        audio_top_row.addStretch()

        audio_bottom_row.addWidget(self.music_button)
        audio_bottom_row.addWidget(self.music_label, 1)
        audio_bottom_row.addWidget(QLabel("Music"))
        audio_bottom_row.addWidget(self.music_volume_slider)
        audio_bottom_row.addWidget(self.music_volume_label)
        audio_bottom_row.addWidget(self.clear_music_button)
        audio_bottom_row.addSpacing(10)
        audio_bottom_row.addWidget(self.narrator_button)

        audio_layout.addLayout(audio_top_row)
        audio_layout.addLayout(audio_bottom_row)
        audio_layout.addWidget(self.sfx_context_frame)

        center_editor_layout.addWidget(audio_frame, 0)

        log_frame = QFrame()
        log_frame.setObjectName("Panel")
        log_frame.setMinimumHeight(190)
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_header.setSpacing(10)

        log_title = QLabel("RENDER STATUS")
        log_title.setObjectName("SectionTitle")

        self.render_progress_time_label = QLabel("Idle")
        self.render_progress_time_label.setObjectName("RenderProgressTime")
        self.render_progress_time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        log_header.addWidget(log_title)
        log_header.addStretch()
        log_header.addWidget(self.render_progress_time_label)

        self.render_log = QTextEdit()
        self.render_log.setReadOnly(True)
        self.render_log.setMinimumHeight(112)
        self.render_log.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.render_log.setPlaceholderText("Render progress will appear here...")

        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)

        self.render_progress_stage_label = QLabel("WAITING")
        self.render_progress_stage_label.setObjectName("RenderProgressStage")
        self.render_progress_stage_label.setMinimumWidth(120)

        self.render_progress_bar = QProgressBar()
        self.render_progress_bar.setObjectName("RenderProgressBar")
        self.render_progress_bar.setRange(0, 100)
        self.render_progress_bar.setValue(0)
        self.render_progress_bar.setTextVisible(False)
        self.render_progress_bar.setMinimumHeight(16)

        progress_row.addWidget(self.render_progress_stage_label)
        progress_row.addWidget(self.render_progress_bar, 1)

        log_layout.addLayout(log_header)
        log_layout.addWidget(self.render_log, 1)
        log_layout.addLayout(progress_row)

        center_column.addWidget(
            center_editor_stack,
            1,
        )
        center_column.addWidget(log_frame, 0)

        workspace.addWidget(center_scroll)

        # ----------------------------------------------------
        # RIGHT RAIL / AI + TRANSCRIPT
        # ----------------------------------------------------

        right_column = QSplitter(Qt.Orientation.Vertical)
        right_column.setObjectName("RightSplitter")
        right_column.setChildrenCollapsible(False)
        right_column.setMinimumWidth(320)
        self.right_splitter = right_column

        ai_frame = QFrame()
        ai_frame.setObjectName("Panel")
        ai_layout = QVBoxLayout(ai_frame)
        ai_layout.setContentsMargins(16, 16, 16, 16)
        ai_layout.setSpacing(10)

        ai_header = QHBoxLayout()
        ai_title = QLabel("AI CLIP HUNTER")
        ai_title.setObjectName("SectionTitle")

        ai_hint = QLabel("UP TO 6 PICKS")
        ai_hint.setObjectName("MicroBadge")

        ai_header.addWidget(ai_title)
        ai_header.addStretch()
        ai_header.addWidget(ai_hint)

        self.clip_cards_layout = QGridLayout()
        self.clip_cards_layout.setHorizontalSpacing(10)
        self.clip_cards_layout.setVerticalSpacing(10)

        self.clip_cards = []
        for index in range(6):
            card = QPushButton(f"AI PICK #{index + 1}\nRun Find Best Clips to populate")
            card.setObjectName("ClipCard")
            card.setProperty("selected", False)
            card.setMinimumHeight(92)
            card.setEnabled(False)
            card.setVisible(False)
            card.clicked.connect(lambda checked=False, card_index=index: (self.select_ai_card(card_index)))
            self.clip_cards.append(card)
            row = index // 2
            column = index % 2
            self.clip_cards_layout.addWidget(card, row, column)

        ai_layout.addLayout(ai_header)
        ai_layout.addLayout(self.clip_cards_layout)

        visual_frame = QFrame()
        visual_frame.setObjectName("Panel")

        visual_layout = QVBoxLayout(
            visual_frame
        )

        visual_layout.setContentsMargins(
            16,
            14,
            16,
            14,
        )

        visual_layout.setSpacing(
            8
        )

        visual_header = QHBoxLayout()

        visual_title = QLabel(
            "AI VISUAL CUTAWAYS"
        )
        visual_title.setObjectName(
            "SectionTitle"
        )

        self.plan_visuals_button = QPushButton(
            "✦ PLAN VISUALS"
        )
        self.plan_visuals_button.setObjectName(
            "QuietButton"
        )
        self.plan_visuals_button.setToolTip(
            "Use the local AI to propose 0-2 sparse B-roll / AI visual cutaways "
            "for the current selected clip."
        )
        self.plan_visuals_button.setEnabled(
            False
        )
        self.plan_visuals_button.clicked.connect(
            self.plan_ai_visuals
        )

        visual_header.addWidget(
            visual_title
        )
        visual_header.addStretch()
        visual_header.addWidget(
            self.plan_visuals_button
        )

        self.generate_visual_assets_button = QPushButton(
            "⬡ GENERATE ASSETS"
        )
        self.generate_visual_assets_button.setObjectName(
            "QuietButton"
        )
        self.generate_visual_assets_button.setToolTip(
            "Generate local image assets for the planned green cutaway slots. "
            "If no compatible image API is running, preview placeholders are created "
            "so the compositing pipeline can still be tested."
        )
        self.generate_visual_assets_button.setEnabled(
            False
        )
        self.generate_visual_assets_button.clicked.connect(
            self.generate_visual_assets
        )

        visual_header.addWidget(
            self.generate_visual_assets_button
        )

        self.check_image_ai_button = QPushButton(
            "CHECK IMAGE AI"
        )
        self.check_image_ai_button.setObjectName(
            "QuietButton"
        )
        self.check_image_ai_button.setToolTip(
            "Refresh the local image generator connection state."
        )
        self.check_image_ai_button.clicked.connect(
            self.check_image_ai
        )

        self.image_ai_status_label = QLabel(
            "● IMAGE AI NOT CHECKED"
        )
        self.image_ai_status_label.setObjectName(
            "ImageAIStatus"
        )
        self.image_ai_status_label.setProperty(
            "state",
            "not_checked",
        )

        image_status_row = QHBoxLayout()
        image_status_row.setSpacing(
            8
        )
        image_status_row.addWidget(
            self.image_ai_status_label,
            1,
        )
        image_status_row.addWidget(
            self.check_image_ai_button
        )

        self.image_model_combo = QComboBox()
        self.image_model_combo.setObjectName(
            "CompactCombo"
        )
        self.image_model_combo.setEnabled(
            False
        )
        self.image_model_combo.addItem(
            "No image model",
            "",
        )
        self.image_model_combo.currentIndexChanged.connect(
            self.image_model_changed
        )

        self.quality_combo = QComboBox()
        self.quality_combo.setObjectName(
            "CompactCombo"
        )
        self.quality_combo.addItems(
            [
                "FAST",
                "BALANCED",
                "HIGH",
            ]
        )
        self.quality_combo.setCurrentText(
            "BALANCED"
        )
        self.quality_combo.currentTextChanged.connect(
            self.quality_changed
        )

        model_row = QGridLayout()
        model_row.setHorizontalSpacing(
            8
        )
        model_row.setVerticalSpacing(
            6
        )

        image_model_label = QLabel(
            "IMAGE MODEL"
        )
        image_model_label.setObjectName(
            "MicroLabel"
        )

        quality_label = QLabel(
            "QUALITY"
        )
        quality_label.setObjectName(
            "MicroLabel"
        )

        model_row.addWidget(
            image_model_label,
            0,
            0,
        )
        model_row.addWidget(
            self.image_model_combo,
            0,
            1,
        )
        model_row.addWidget(
            quality_label,
            1,
            0,
        )
        model_row.addWidget(
            self.quality_combo,
            1,
            1,
        )

        self.visual_status_label = QLabel(
            "Load a transcript, then plan visuals for the active selection."
        )
        self.visual_status_label.setObjectName(
            "TranscriptStatus"
        )

        self.visual_slots_list = QListWidget()
        self.visual_slots_list.setObjectName(
            "TranscriptList"
        )
        self.visual_slots_list.setMinimumHeight(
            120
        )
        self.visual_slots_list.itemClicked.connect(
            self.visual_slot_clicked
        )

        self.visual_inspector = QFrame()
        self.visual_inspector.setObjectName(
            "SubPanel"
        )
        inspector_layout = QVBoxLayout(
            self.visual_inspector
        )
        inspector_layout.setContentsMargins(
            10,
            10,
            10,
            10,
        )
        inspector_layout.setSpacing(
            7
        )

        self.visual_inspector_title = QLabel(
            "SELECT VISUAL SLOT"
        )
        self.visual_inspector_title.setObjectName(
            "SectionTitle"
        )

        inspector_grid = QGridLayout()
        inspector_grid.setHorizontalSpacing(
            8
        )
        inspector_grid.setVerticalSpacing(
            6
        )

        self.visual_label_edit = QLineEdit()
        self.visual_label_edit.setObjectName(
            "CompactLineEdit"
        )
        self.visual_start_edit = QLineEdit()
        self.visual_start_edit.setObjectName(
            "CompactLineEdit"
        )
        self.visual_end_edit = QLineEdit()
        self.visual_end_edit.setObjectName(
            "CompactLineEdit"
        )
        self.visual_type_edit = QLineEdit()
        self.visual_type_edit.setObjectName(
            "CompactLineEdit"
        )

        self.visual_display_mode_combo = QComboBox()
        self.visual_display_mode_combo.setObjectName(
            "CompactCombo"
        )
        self.visual_display_mode_combo.addItems(
            [
                "OVERLAY_CARD",
                "FULL_FRAME_CONTAIN",
                "FULL_FRAME_COVER",
            ]
        )

        self.visual_scale_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.visual_scale_slider.setObjectName(
            "MusicVolumeSlider"
        )
        self.visual_scale_slider.setRange(
            60,
            140,
        )
        self.visual_scale_slider.setValue(
            100
        )

        self.visual_scale_label = QLabel(
            "100%"
        )
        self.visual_scale_label.setObjectName(
            "MusicVolumeLabel"
        )

        self.visual_label_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_start_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_end_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_type_edit.editingFinished.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_display_mode_combo.currentTextChanged.connect(
            self.visual_inspector_fields_changed
        )
        self.visual_scale_slider.valueChanged.connect(
            self.visual_scale_changed
        )

        inspector_grid.addWidget(
            QLabel("Label"),
            0,
            0,
        )
        inspector_grid.addWidget(
            self.visual_label_edit,
            0,
            1,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Start"),
            1,
            0,
        )
        inspector_grid.addWidget(
            self.visual_start_edit,
            1,
            1,
        )
        inspector_grid.addWidget(
            QLabel("End"),
            1,
            2,
        )
        inspector_grid.addWidget(
            self.visual_end_edit,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Type"),
            2,
            0,
        )
        inspector_grid.addWidget(
            self.visual_type_edit,
            2,
            1,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Mode"),
            3,
            0,
        )
        inspector_grid.addWidget(
            self.visual_display_mode_combo,
            3,
            1,
            1,
            3,
        )
        inspector_grid.addWidget(
            QLabel("Scale"),
            4,
            0,
        )
        inspector_grid.addWidget(
            self.visual_scale_slider,
            4,
            1,
            1,
            2,
        )
        inspector_grid.addWidget(
            self.visual_scale_label,
            4,
            3,
        )

        self.visual_reason_label = QLabel(
            "Select a planned visual to inspect it."
        )
        self.visual_reason_label.setObjectName(
            "HintLabel"
        )
        self.visual_reason_label.setWordWrap(
            True
        )

        self.visual_prompt_edit = QTextEdit()
        self.visual_prompt_edit.setObjectName(
            "PromptEdit"
        )
        self.visual_prompt_edit.setMaximumHeight(
            86
        )
        self.visual_prompt_edit.textChanged.connect(
            self.visual_prompt_changed
        )

        preview_row = QHBoxLayout()
        preview_row.setSpacing(
            10
        )

        self.visual_preview_label = QLabel(
            "NO IMAGE"
        )
        self.visual_preview_label.setObjectName(
            "VisualPreviewThumb"
        )
        self.visual_preview_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.visual_preview_label.setFixedSize(
            86,
            122,
        )

        action_column = QVBoxLayout()
        action_column.setSpacing(
            7
        )

        self.regenerate_visual_button = QPushButton(
            "REGENERATE"
        )
        self.regenerate_visual_button.setObjectName(
            "QuietButton"
        )
        self.regenerate_visual_button.clicked.connect(
            self.regenerate_selected_visual_asset
        )

        self.keep_visual_variant_button = QPushButton(
            "KEEP"
        )
        self.keep_visual_variant_button.setObjectName(
            "QuietButton"
        )
        self.keep_visual_variant_button.setToolTip(
            "Preserve the active image variant so future generation cannot overwrite it."
        )
        self.keep_visual_variant_button.clicked.connect(
            self.keep_selected_visual_variant
        )

        self.generate_more_visual_button = QPushButton(
            "GENERATE MORE"
        )
        self.generate_more_visual_button.setObjectName(
            "QuietButton"
        )
        self.generate_more_visual_button.setToolTip(
            "Generate another image variant without replacing kept images."
        )
        self.generate_more_visual_button.clicked.connect(
            self.generate_more_selected_visual_variant
        )

        variant_nav = QHBoxLayout()
        variant_nav.setSpacing(
            4
        )

        self.previous_visual_variant_button = QPushButton(
            "<"
        )
        self.previous_visual_variant_button.setObjectName(
            "TinyButton"
        )
        self.previous_visual_variant_button.setToolTip(
            "Previous image variant"
        )
        self.previous_visual_variant_button.clicked.connect(
            self.previous_visual_variant
        )

        self.visual_variant_label = QLabel(
            "0/0"
        )
        self.visual_variant_label.setObjectName(
            "MicroLabel"
        )
        self.visual_variant_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.next_visual_variant_button = QPushButton(
            ">"
        )
        self.next_visual_variant_button.setObjectName(
            "TinyButton"
        )
        self.next_visual_variant_button.setToolTip(
            "Next image variant"
        )
        self.next_visual_variant_button.clicked.connect(
            self.next_visual_variant
        )

        variant_nav.addWidget(
            self.previous_visual_variant_button
        )
        variant_nav.addWidget(
            self.visual_variant_label,
            1,
        )
        variant_nav.addWidget(
            self.next_visual_variant_button
        )

        self.disable_visual_button = QPushButton(
            "DISABLE"
        )
        self.disable_visual_button.setObjectName(
            "QuietButton"
        )
        self.disable_visual_button.clicked.connect(
            self.toggle_selected_visual_enabled
        )

        self.delete_visual_button = QPushButton(
            "DELETE"
        )
        self.delete_visual_button.setObjectName(
            "CutButton"
        )
        self.delete_visual_button.clicked.connect(
            self.delete_selected_visual
        )

        action_column.addWidget(
            self.regenerate_visual_button
        )
        action_column.addWidget(
            self.keep_visual_variant_button
        )
        action_column.addWidget(
            self.generate_more_visual_button
        )
        action_column.addLayout(
            variant_nav
        )
        action_column.addWidget(
            self.disable_visual_button
        )
        action_column.addWidget(
            self.delete_visual_button
        )
        action_column.addStretch()

        preview_row.addWidget(
            self.visual_preview_label
        )
        preview_row.addLayout(
            action_column,
            1,
        )

        inspector_layout.addWidget(
            self.visual_inspector_title
        )
        inspector_layout.addLayout(
            inspector_grid
        )
        inspector_layout.addWidget(
            self.visual_reason_label
        )
        inspector_layout.addWidget(
            self.visual_prompt_edit
        )
        inspector_layout.addLayout(
            preview_row
        )

        visual_layout.addLayout(
            visual_header
        )
        visual_layout.addLayout(
            image_status_row
        )
        visual_layout.addLayout(
            model_row
        )
        visual_layout.addWidget(
            self.visual_status_label
        )
        visual_layout.addWidget(
            self.visual_slots_list
        )
        visual_layout.addWidget(
            self.visual_inspector
        )

        transcript_frame = QFrame()
        transcript_frame.setObjectName("Panel")
        transcript_layout = QVBoxLayout(transcript_frame)
        transcript_layout.setContentsMargins(16, 16, 16, 16)
        transcript_layout.setSpacing(10)

        transcript_header = QHBoxLayout()
        transcript_title = QLabel("TRANSCRIPT SCRAP")
        transcript_title.setObjectName("SectionTitle")

        self.transcript_status_label = QLabel("Run Find Best Clips to load the source transcript.")
        self.transcript_status_label.setObjectName("TranscriptStatus")

        transcript_header.addWidget(transcript_title)
        transcript_header.addSpacing(8)
        transcript_header.addWidget(self.transcript_status_label, 1)

        transcript_actions = QHBoxLayout()
        transcript_actions.setSpacing(8)

        self.edit_transcript_button = QPushButton("✎ EDIT TEXT")
        self.edit_transcript_button.setObjectName("QuietButton")
        self.edit_transcript_button.setToolTip(
            "Correct the wording for this transcript line. The corrected text will be used for captions."
        )
        self.edit_transcript_button.clicked.connect(
            self.edit_selected_transcript_segment
        )

        self.reset_transcript_text_button = QPushButton("↶ RESET TEXT")
        self.reset_transcript_text_button.setObjectName("QuietButton")
        self.reset_transcript_text_button.setToolTip(
            "Restore Whisper's original wording for this transcript line."
        )
        self.reset_transcript_text_button.clicked.connect(
            self.reset_selected_transcript_text
        )

        self.cut_transcript_button = QPushButton("✕ CUT")
        self.cut_transcript_button.setObjectName("CutButton")
        self.cut_transcript_button.setToolTip(
            "Mark the selected transcript segment for removal from the final Short."
        )
        self.cut_transcript_button.clicked.connect(
            self.cut_selected_transcript_segment
        )

        self.restore_transcript_button = QPushButton("↺ UNCUT")
        self.restore_transcript_button.setObjectName("RestoreButton")
        self.restore_transcript_button.setToolTip(
            "Restore the selected transcript segment if it was marked for removal."
        )
        self.restore_transcript_button.clicked.connect(
            self.restore_selected_transcript_segment
        )

        transcript_actions.addWidget(self.edit_transcript_button)
        transcript_actions.addWidget(self.reset_transcript_text_button)
        transcript_actions.addStretch()
        transcript_actions.addWidget(self.cut_transcript_button)
        transcript_actions.addWidget(self.restore_transcript_button)

        self.transcript_list = QListWidget()
        self.transcript_list.setObjectName("TranscriptList")
        self.transcript_list.setAlternatingRowColors(False)
        self.transcript_list.itemClicked.connect(self.transcript_item_clicked)
        self.transcript_list.itemDoubleClicked.connect(
            self.edit_transcript_item
        )

        transcript_layout.addLayout(transcript_header)
        transcript_layout.addLayout(transcript_actions)
        transcript_layout.addWidget(self.transcript_list, 1)

        ai_scroll = QScrollArea()
        ai_scroll.setObjectName("PanelScroll")
        ai_scroll.setWidgetResizable(True)
        ai_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ai_scroll.setWidget(ai_frame)

        visual_scroll = QScrollArea()
        visual_scroll.setObjectName("PanelScroll")
        visual_scroll.setWidgetResizable(True)
        visual_scroll.setFrameShape(QFrame.Shape.NoFrame)
        visual_scroll.setWidget(visual_frame)

        right_column.addWidget(ai_scroll)
        right_column.addWidget(visual_scroll)
        right_column.addWidget(transcript_frame)
        right_column.setSizes([180, 360, 520])

        workspace.addWidget(right_column)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        workspace.setStretchFactor(2, 0)
        workspace.setSizes([280, 760, 440])

        main_layout.addWidget(workspace, 1)

    def load_video(
        self,
        path: Path,
    ):

        self.cancel_paused_seek_refresh()
        self.hide_ai_visual_preview_overlay()
        self.play_request_counter += 1
        self.video_path = path

        self.file_label.setText(
            path.name
        )

        self.player.setSource(
            QUrl.fromLocalFile(
                str(path)
            )
        )
        self.player.setPosition(
            0
        )
        self.play_button.setEnabled(
            False
        )
        self.play_button.setText(
            "LOADING"
        )

        self.start_ms = 0
        self.end_ms = 0

        self.timeline.set_selection_range(
            0,
            0,
        )

        self.selection_label.setText(
            "Selection: 00:00 → 00:00"
        )

        self.generate_button.setEnabled(
            True
        )

        self.find_clips_button.setEnabled(
            True
        )

        self.find_clips_button.setText(
            "✦ Find Best Clips"
        )

        self.timeline.clear_suggestions()
        self.timeline.clear_editor_overlays()
        self.selected_sfx_clip_id = None
        self.load_editor_asset_plan_state()

        self.ai_candidates = []
        self.visual_plan_slots = []

        if hasattr(
            self,
            "visual_slots_list",
        ):
            self.visual_slots_list.clear()
            self.visual_status_label.setText(
                "Load a transcript, then plan visuals for the active selection."
            )
            self.plan_visuals_button.setEnabled(
                False
            )

            self.generate_visual_assets_button.setEnabled(
                False
            )

        self.source_transcript_segments = []
        self.manual_cut_segments = set()
        self.transcript_corrections = {}
        self.save_manual_edit_plan()
        self.save_transcript_corrections()

        self.reset_clip_cards()

        if hasattr(
            self,
            "transcript_list",
        ):
            self.transcript_list.clear()

            self.transcript_status_label.setText(
                "Run Find Best Clips to load the source transcript."
            )

        self.suggestions_label.setText(
            "AI clips appear as purple ranges on V1. Drag IN / OUT handles to fine-tune the selected clip."
        )
        self.update_sfx_button_state()
        self.set_selection_loop_enabled(
            False
        )

    def toggle_playback(self):

        if not self.video_path:
            return

        refresh_playback = self.paused_seek_refresh_pending

        if (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
            and not refresh_playback
        ):

            self.play_request_counter += 1
            self.player.pause()

        else:

            self.play_request_counter += 1
            play_request = self.play_request_counter
            self.cancel_paused_seek_refresh()
            self.set_selection_loop_enabled(
                self.position_in_selection(
                    self.player.position()
                )
            )
            self.audio_output.setMuted(
                False
            )

            if (
                self.player.duration() > 0
                and self.player.position()
                >= self.player.duration()
                - 80
            ):
                self.player.setPosition(
                    self.start_ms
                    if self.has_active_selection()
                    else 0
            )

            self.player.play()
            self.update_play_button(
                QMediaPlayer.PlaybackState.PlayingState
            )

            QTimer.singleShot(
                350,
                lambda: self.verify_playback_started(
                    play_request
                ),
            )

    def has_active_selection(self) -> bool:

        return (
            self.end_ms
            > self.start_ms
            and self.end_ms
            - self.start_ms
            >= 500
        )

    def position_in_selection(
        self,
        position: int,
    ) -> bool:

        if not self.has_active_selection():
            return False

        return (
            self.start_ms
            <= int(
                position
            )
            < self.end_ms
        )

    def set_selection_loop_enabled(
        self,
        enabled: bool,
    ):

        enabled = bool(
            enabled
            and self.has_active_selection()
        )

        if self.selection_loop_enabled == enabled:
            return

        self.selection_loop_enabled = enabled

        if hasattr(
            self,
            "selection_label",
        ):
            self.update_selection_label()

    def position_changed(
        self,
        position: int,
    ):

        if (
            self.selection_loop_enabled
            and self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
            and self.has_active_selection()
            and position >= self.end_ms - 45
        ):
            self.player.setPosition(
                self.start_ms
            )
            self.timeline.setValue(
                self.start_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.start_ms
                )
            )
            self.update_ai_visual_preview_overlay(
                self.start_ms
            )
            return

        if not self.timeline.isSliderDown():

            self.timeline.setValue(
                position
            )

            if (
                self.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            ):
                self.timeline.follow_playhead(
                    position
                )

        self.current_time_label.setText(
            format_precise_time(position)
        )
        if hasattr(
            self,
            "timeline_time_label",
        ):
            self.timeline_time_label.setText(
                (
                    f"{format_precise_time(position)} / "
                    f"{format_precise_time(self.player.duration())}"
                )
            )

        self.trigger_sfx_previews(
            position
        )
        self.update_ai_visual_preview_overlay(
            position
        )

    def duration_changed(
        self,
        duration: int,
    ):

        self.timeline.setRange(
            0,
            duration,
        )
        self.timeline.fit_source()

        self.duration_label.setText(
            format_precise_time(duration)
        )
        if hasattr(
            self,
            "timeline_time_label",
        ):
            self.timeline_time_label.setText(
                (
                    f"{format_precise_time(self.player.position())} / "
                    f"{format_precise_time(duration)}"
                )
            )

        if self.end_ms == 0:

            self.end_ms = duration

            self.timeline.set_selection_range(
                self.start_ms,
                self.end_ms,
            )

            self.update_selection_label()

    def seek_video(
        self,
        position: int,
    ):

        self.set_selection_loop_enabled(
            self.position_in_selection(
                position
            )
        )

        was_playing = (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )

        self.player.setPosition(
            position
        )
        self.current_time_label.setText(
            format_precise_time(position)
        )
        if hasattr(
            self,
            "timeline_time_label",
        ):
            self.timeline_time_label.setText(
                (
                    f"{format_precise_time(position)} / "
                    f"{format_precise_time(self.player.duration())}"
                )
            )

        self.update_ai_visual_preview_overlay(
            position
        )

        if not was_playing and getattr(
            self.timeline,
            "scrubbing_playhead",
            False,
        ):
            return

        # On some Windows multimedia backends, a paused video does
        # not immediately redraw after seeking. Briefly advancing the
        # player while muted forces the preview frame to refresh, but
        # never while actively dragging the playhead.
        if not was_playing and not self.paused_seek_refresh_pending:

            self.paused_seek_refresh_pending = True
            self.audio_output.setMuted(
                True
            )

            self.player.play()

            QTimer.singleShot(
                45,
                self.finish_paused_seek,
            )

    def finish_paused_seek(self):

        if self.paused_seek_refresh_pending:
            self.player.pause()

            self.audio_output.setMuted(
                False
            )

        self.paused_seek_refresh_pending = False

    def cancel_paused_seek_refresh(self):

        self.paused_seek_refresh_pending = False
        self.audio_output.setMuted(
            False
        )

    def verify_playback_started(
        self,
        play_request: int,
    ):

        if not self.video_path:
            return

        if play_request != self.play_request_counter:
            return

        if (
            self.player.error()
            != QMediaPlayer.Error.NoError
        ):
            return

        if (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            return

        self.report_playback_message(
            "Preview did not start playback. "
            "Try clicking the timeline once or reload the source; "
            "if it keeps happening, check the media error above."
        )

    def seek_to_slider_position(self):

        self.seek_video(
            self.timeline.value()
        )

    def timeline_viewport_changed(
        self,
        start_ms: int,
        end_ms: int,
    ):

        if not hasattr(
            self,
            "timeline_navigator",
        ):
            return

        self.updating_timeline_controls = True

        duration = max(
            0,
            self.timeline.maximum(),
        )

        self.timeline_navigator.set_state(
            duration,
            start_ms,
            end_ms,
            self.timeline.minimum_visible_ms,
        )

        self.timeline_zoom_slider.setValue(
            int(
                round(
                    self.timeline.zoom_fraction()
                    * 100
                )
            )
        )

        self.updating_timeline_controls = False

    def timeline_navigator_changed(
        self,
        start_ms: int,
        end_ms: int,
    ):

        if self.updating_timeline_controls:
            return

        self.timeline.set_viewport(
            int(
                start_ms
            ),
            int(
                end_ms
            ),
            manual=True,
        )

    def timeline_zoom_slider_changed(
        self,
        value: int,
    ):

        if self.updating_timeline_controls:
            return

        self.timeline.set_zoom_fraction(
            value / 100
        )

    def fit_timeline_selection(self):

        self.timeline.fit_selection()

    def fit_timeline_source(self):

        self.timeline.fit_source()

    def reveal_timeline_range(
        self,
        start_ms: int,
        end_ms: int,
    ):

        self.timeline.reveal_range(
            int(
                start_ms
            ),
            int(
                end_ms
            ),
            manual=False,
        )

    def reveal_timeline_time(
        self,
        position_ms: int,
    ):

        self.timeline.reveal_time(
            int(
                position_ms
            ),
            manual=False,
        )

    def update_play_button(self, state):

        if (
            state
            == QMediaPlayer.PlaybackState.PlayingState
            and not self.paused_seek_refresh_pending
        ):
            self.play_button.setText(
                "PAUSE"
            )
        else:
            self.play_button.setText(
                "PLAY"
            )

    def report_playback_message(
        self,
        message: str,
    ):

        text = f"Preview playback: {message}"

        print(
            text
        )

        if hasattr(
            self,
            "render_log",
        ):
            self.render_log.append(
                text
            )

        if hasattr(
            self,
            "suggestions_label",
        ):
            self.suggestions_label.setText(
                text
            )

    def media_status_changed(
        self,
        status,
    ):

        if (
            status
            == QMediaPlayer.MediaStatus.LoadingMedia
        ):
            if hasattr(
                self,
                "play_button",
            ):
                self.play_button.setEnabled(
                    False
                )
                self.play_button.setText(
                    "LOADING"
                )

            return

        if status in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        }:
            if hasattr(
                self,
                "play_button",
            ):
                self.play_button.setEnabled(
                    self.video_path is not None
                )
                self.update_play_button(
                    self.player.playbackState()
                )

            return

        if (
            status
            == QMediaPlayer.MediaStatus.InvalidMedia
        ):
            if hasattr(
                self,
                "play_button",
            ):
                self.play_button.setEnabled(
                    False
                )
                self.play_button.setText(
                    "PLAY"
                )

            details = (
                self.player.errorString()
                or (
                    self.video_path.name
                    if self.video_path
                    else "unknown source"
                )
            )
            self.report_playback_message(
                f"could not load media. {details}"
            )

        elif (
            status
            == QMediaPlayer.MediaStatus.EndOfMedia
        ):
            self.set_selection_loop_enabled(
                False
            )

    def playback_error_occurred(
        self,
        error,
        error_string: str = "",
    ):

        if error == QMediaPlayer.Error.NoError:
            return

        details = (
            error_string
            or self.player.errorString()
            or str(
                error
            )
        )

        self.report_playback_message(
            f"media error. {details}"
        )

    def timeline_selection_changed(
        self,
        start_ms: int,
        end_ms: int,
    ):

        preserve_editor_assets = bool(
            self.editor_asset_context_matches_current_selection()
            and self.editor_asset_plan.get(
                "clips",
                [],
            )
            and (
                getattr(
                    self.timeline,
                    "dragging_handle",
                    None,
                )
                in {
                    "start",
                    "end",
                }
                or getattr(
                    self.timeline,
                    "dragging_source_clip",
                    False,
                )
            )
        )

        self.start_ms = start_ms
        self.end_ms = end_ms

        self.set_selection_loop_enabled(
            self.selection_loop_enabled
            and self.position_in_selection(
                self.player.position()
            )
        )

        if getattr(
            self.timeline,
            "dragging_handle",
            None,
        ) == "start":
            self.player.setPosition(
                self.start_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.start_ms
                )
            )
        elif getattr(
            self.timeline,
            "dragging_handle",
            None,
        ) == "end":
            self.player.setPosition(
                self.end_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.end_ms
                )
            )
        elif getattr(
            self.timeline,
            "dragging_source_clip",
            False,
        ):
            self.player.setPosition(
                self.start_ms
            )
            self.current_time_label.setText(
                format_precise_time(
                    self.start_ms
                )
            )

        self.timeline.set_selected_suggestion(
            None
        )

        self.refresh_clip_card_selection(
            None
        )

        self.update_selection_label()

        self.update_transcript_panel()
        if preserve_editor_assets:
            self.retarget_editor_asset_context_to_current_selection()
            self.apply_editor_visual_overrides_to_slots()
            if self.visual_plan_slots:
                self.save_ai_visual_plan()
            self.refresh_visual_plan_display()
        else:
            self.clear_visual_plan_display()
            self.selected_sfx_clip_id = None
            self.refresh_editor_asset_timeline()

        if hasattr(
            self,
            "plan_visuals_button",
        ):
            self.plan_visuals_button.setEnabled(
                bool(
                    self.source_transcript_segments
                    and self.end_ms > self.start_ms
                )
            )

    def set_start(self):

        preserve_editor_assets = bool(
            self.editor_asset_context_matches_current_selection()
            and self.editor_asset_plan.get(
                "clips",
                [],
            )
        )

        self.timeline.set_selected_suggestion(
            None
        )

        self.refresh_clip_card_selection(
            None
        )

        self.start_ms = (
            self.player.position()
        )

        if (
            self.end_ms
            and self.start_ms >= self.end_ms
        ):

            self.end_ms = (
                self.player.duration()
            )

        self.timeline.set_selection_range(
            self.start_ms,
            self.end_ms,
        )
        self.reveal_timeline_time(
            self.start_ms
        )

        self.update_selection_label()
        if preserve_editor_assets:
            self.retarget_editor_asset_context_to_current_selection()
            self.apply_editor_visual_overrides_to_slots()
            if self.visual_plan_slots:
                self.save_ai_visual_plan()
            self.refresh_visual_plan_display()
        else:
            self.clear_visual_plan_display()

    def set_end(self):

        preserve_editor_assets = bool(
            self.editor_asset_context_matches_current_selection()
            and self.editor_asset_plan.get(
                "clips",
                [],
            )
        )

        self.timeline.set_selected_suggestion(
            None
        )

        self.refresh_clip_card_selection(
            None
        )

        self.end_ms = (
            self.player.position()
        )

        if self.end_ms <= self.start_ms:

            self.start_ms = 0

        self.timeline.set_selection_range(
            self.start_ms,
            self.end_ms,
        )
        self.reveal_timeline_time(
            self.end_ms
        )

        self.update_selection_label()
        if preserve_editor_assets:
            self.retarget_editor_asset_context_to_current_selection()
            self.apply_editor_visual_overrides_to_slots()
            if self.visual_plan_slots:
                self.save_ai_visual_plan()
            self.refresh_visual_plan_display()
        else:
            self.clear_visual_plan_display()

    def update_selection_label(self):

        duration_ms = max(
            0,
            self.end_ms - self.start_ms,
        )

        loop_text = (
            "   •   LOOP"
            if self.selection_loop_enabled
            else ""
        )

        self.selection_label.setText(
            "Selection: "
            f"{format_time(self.start_ms)}"
            " → "
            f"{format_time(self.end_ms)}"
            f"   •   {duration_ms / 1000:.1f}s"
            f"{loop_text}"
        )

        if hasattr(
            self,
            "transcript_list",
        ):
            self.update_transcript_panel()

        self.update_sfx_button_state()

    def load_source_transcript(self):

        transcript_path = (
            ROOT
            / "output"
            / "subtitles.json"
        )

        self.source_transcript_segments = []

        if not transcript_path.exists():

            self.transcript_status_label.setText(
                "Source transcript is not available."
            )

            self.transcript_list.clear()
            return

        try:

            with transcript_path.open(
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

        except (
            OSError,
            json.JSONDecodeError,
        ):

            self.transcript_status_label.setText(
                "Could not read the source transcript."
            )

            self.transcript_list.clear()
            return

        segments = data.get(
            "segments",
            [],
        )

        if not isinstance(
            segments,
            list,
        ):

            segments = []

        for segment in segments:

            if not isinstance(
                segment,
                dict,
            ):
                continue

            try:
                start = float(
                    segment.get(
                        "start",
                        0,
                    )
                )

                end = float(
                    segment.get(
                        "end",
                        start,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            text = str(
                segment.get(
                    "text",
                    "",
                )
                or ""
            ).strip()

            if (
                not text
                or end <= start
            ):
                continue

            self.source_transcript_segments.append(
                {
                    "start_ms": int(
                        round(
                            start * 1000
                        )
                    ),
                    "end_ms": int(
                        round(
                            end * 1000
                        )
                    ),
                    "text": text,
                    "original_text": text,
                }
            )

        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()

        if hasattr(
            self,
            "plan_visuals_button",
        ):
            self.plan_visuals_button.setEnabled(
                bool(
                    self.source_transcript_segments
                    and self.end_ms > self.start_ms
                )
            )


    def update_transcript_panel(self):

        if not hasattr(
            self,
            "transcript_list",
        ):
            return

        self.transcript_list.clear()

        if not self.source_transcript_segments:

            self.transcript_status_label.setText(
                "Run Find Best Clips to load the source transcript."
            )

            return

        visible_segments = [
            segment
            for segment in self.source_transcript_segments
            if (
                segment["end_ms"] > self.start_ms
                and segment["start_ms"] < self.end_ms
            )
        ]

        visible_cut_seconds = sum(
            max(
                0,
                min(segment["end_ms"], self.end_ms)
                - max(segment["start_ms"], self.start_ms),
            )
            for segment in visible_segments
            if (
                segment["start_ms"],
                segment["end_ms"],
            ) in self.manual_cut_segments
        ) / 1000

        total_cut_seconds = sum(
            max(0, end_ms - start_ms)
            for start_ms, end_ms in self.manual_cut_segments
        ) / 1000

        self.transcript_status_label.setText(
            f"{len(visible_segments)} lines in selection  //  "
            f"CUT {visible_cut_seconds:.1f}s here  //  "
            f"{total_cut_seconds:.1f}s total.  "
            f"{len(self.transcript_corrections)} corrected.  "
            "Click = seek  •  Double-click = EDIT TEXT"
        )

        for segment in visible_segments:

            timestamp = format_time(
                segment["start_ms"]
            )

            segment_key = (
                segment["start_ms"],
                segment["end_ms"],
            )

            is_cut = (
                segment_key
                in self.manual_cut_segments
            )

            segment_key = (
                segment["start_ms"],
                segment["end_ms"],
            )

            corrected_text = self.transcript_corrections.get(
                segment_key,
                segment["text"],
            )

            state_parts = []

            if is_cut:
                state_parts.append("CUT")
            else:
                state_parts.append("KEEP")

            if segment_key in self.transcript_corrections:
                state_parts.append("EDITED")

            state_marker = (
                "["
                + " / ".join(state_parts)
                + "]"
            )

            item = QListWidgetItem(
                f"{state_marker}  {timestamp}    {corrected_text}"
            )

            item.setData(
                Qt.ItemDataRole.UserRole,
                segment["start_ms"],
            )

            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                segment["end_ms"],
            )

            font = QFont(
                item.font()
            )

            if is_cut:

                font.setStrikeOut(
                    True
                )

                item.setForeground(
                    QColor(
                        "#d76572"
                    )
                )

                item.setBackground(
                    QColor(
                        "#211216"
                    )
                )

            else:

                font.setStrikeOut(
                    False
                )

                item.setForeground(
                    QColor(
                        "#d3cdc5"
                    )
                )

            item.setFont(
                font
            )

            item.setToolTip(
                (
                    f"{segment['start_ms'] / 1000:.2f}s → "
                    f"{segment['end_ms'] / 1000:.2f}s\n"
                    f"{corrected_text}\n\n"
                    + (
                        (
                            f"Original Whisper text: {segment['text']}\n\n"
                            if segment_key in self.transcript_corrections
                            else ""
                        )
                    )
                    + (
                        "Marked for removal from the final edit."
                        if is_cut
                        else "Kept in the final edit."
                    )
                )
            )

            self.transcript_list.addItem(
                item
            )


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
            self.visual_status_label.setText(
                "Changing image model..."
            )
        else:
            self.visual_status_label.setText(
                "Checking Image AI..."
            )

        args = [
            str(
                status_script
            ),
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


    def quality_changed(
        self,
        value: str,
    ):

        self.image_quality = str(
            value
            or "BALANCED"
        ).upper()


    def transcription_quality_changed(
        self,
        value: str,
    ):

        quality = str(
            value
            or "AUTO"
        ).upper()

        if quality not in {
            "AUTO",
            "FAST",
            "ACCURATE",
        }:
            quality = "AUTO"

        self.transcription_quality = quality
        self.settings.setValue(
            "transcription/quality",
            quality,
        )


    def current_transcription_quality(self) -> str:

        quality = str(
            getattr(
                self,
                "transcription_quality",
                "AUTO",
            )
            or "AUTO"
        ).upper()

        if quality not in {
            "AUTO",
            "FAST",
            "ACCURATE",
        }:
            return "AUTO"

        return quality


    def edit_energy_changed(
        self,
        value: str,
    ):

        energy = normalize_energy(
            value
        )

        self.edit_energy = energy
        self.settings.setValue(
            "render/edit_energy",
            energy,
        )


    def current_edit_energy(self) -> str:

        return normalize_energy(
            getattr(
                self,
                "edit_energy",
                DEFAULT_ENERGY,
            )
        )


    def sfx_mode_changed(
        self,
        value: str,
    ):

        mode = normalize_sfx_mode(
            value
        )
        self.sfx_mode = mode
        self.settings.setValue(
            "render/sfx_mode",
            mode,
        )
        self.save_render_settings()


    def current_sfx_mode(self) -> str:

        return normalize_sfx_mode(
            getattr(
                self,
                "sfx_mode",
                "AUTO",
            )
        )


    def save_render_settings(self):

        payload = {
            "edit_energy": self.current_edit_energy(),
            "sfx_mode": self.current_sfx_mode(),
            "source_video": (
                str(
                    self.video_path
                )
                if self.video_path
                else ""
            ),
            "selection_start": self.start_ms / 1000,
            "selection_end": self.end_ms / 1000,
        }

        try:

            write_render_settings(
                payload
            )

        except OSError as exc:

            if hasattr(
                self,
                "render_log",
            ):
                self.render_log.append(
                    f"WARNING: Could not save render settings: {exc}"
                )


    def visual_slot_state_text(
        self,
        slot: dict,
    ) -> str:

        if slot.get(
            "enabled",
            True,
        ) is False:
            return "DISABLED"

        state = str(
            slot.get(
                "state",
                "PLANNED",
            )
            or "PLANNED"
        ).upper()

        return state.replace(
            "_",
            " ",
        )


    def ensure_visual_slot_defaults(self):

        for index, slot in enumerate(
            self.visual_plan_slots,
            start=1,
        ):
            if not isinstance(
                slot,
                dict,
            ):
                continue

            if not slot.get(
                "slot_id"
            ):
                try:
                    start_ms = int(
                        round(
                            float(
                                slot.get(
                                    "start",
                                    0.0,
                                )
                            )
                            * 1000
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    start_ms = index

                slot["slot_id"] = (
                    f"visual_{start_ms}_{index:02d}"
                )

            slot.setdefault(
                "enabled",
                True,
            )
            slot.setdefault(
                "state",
                "PLANNED",
            )
            slot.setdefault(
                "display_mode",
                "OVERLAY_CARD",
            )
            slot.setdefault(
                "scale",
                1.0,
            )

            try:
                start = float(
                    slot.get(
                        "start",
                        0.0,
                    )
                )
                end = float(
                    slot.get(
                        "end",
                        start,
                    )
                )
                slot["duration"] = round(
                    max(
                        0.0,
                        end
                        - start,
                    ),
                    3,
                )
            except (
                TypeError,
                ValueError,
            ):
                pass


    def visual_clip_id(self, slot: dict, index: int) -> str:
        slot_id = str(slot.get("slot_id", "") or "")
        return f"visual:{slot_id or f'visual_{index + 1:02d}'}"


    def visual_slot_asset_path_text(self, slot: dict) -> str:
        variants = slot.get("variants", [])
        active_id = str(slot.get("active_variant_id", "") or "")
        if isinstance(variants, list):
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                variant_id = str(variant.get("variant_id", "") or "")
                if active_id and variant_id != active_id:
                    continue
                path = str(variant.get("path", "") or "")
                if path:
                    return path
        return str(slot.get("asset_path", "") or "")


    def visual_slot_to_editor_clip(self, slot: dict, index: int) -> dict:
        try:
            start = float(slot.get("start", 0.0) or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(slot.get("end", start) or start)
        except (TypeError, ValueError):
            end = start
        try:
            scale = float(slot.get("scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        scale = max(0.6, min(1.4, scale))
        display_mode = str(slot.get("display_mode", "OVERLAY_CARD") or "OVERLAY_CARD").strip().upper()
        if display_mode not in {"OVERLAY_CARD", "FULL_FRAME_CONTAIN", "FULL_FRAME_COVER"}:
            display_mode = "OVERLAY_CARD"
        manual = bool(slot.get("user_modified", False))
        asset_path = self.visual_slot_asset_path_text(slot)
        return {
            "id": self.visual_clip_id(slot, index),
            "kind": "AI_VISUAL",
            "time_basis": "source",
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "asset_path": asset_path,
            "active_variant_path": asset_path,
            "label": str(slot.get("label", f"Visual {index + 1}") or f"Visual {index + 1}"),
            "display_mode": display_mode,
            "scale": round(scale, 2),
            "source_type": str(slot.get("source_type", "ai_generated") or "ai_generated"),
            "slot_id": str(slot.get("slot_id", "") or ""),
            "variant_id": str(slot.get("active_variant_id", "") or ""),
            "active": bool(slot.get("enabled", True)),
            "origin": "manual" if manual else "automatic",
            "manual_override": manual,
            "locked": manual,
        }


    def apply_editor_visual_overrides_to_slots(self):
        self.editor_asset_plan = load_editor_asset_plan()
        if not self.editor_asset_context_matches_current_selection():
            return

        visual_clips = {
            str(clip.get("id", "") or ""): clip
            for clip in clips_of_kind(self.editor_asset_plan, "AI_VISUAL")
            if isinstance(clip, dict)
            and (bool(clip.get("manual_override", False)) or bool(clip.get("locked", False)))
        }
        for index, slot in enumerate(self.visual_plan_slots):
            if not isinstance(slot, dict):
                continue
            clip = visual_clips.get(self.visual_clip_id(slot, index))
            if clip is None:
                continue
            try:
                start = float(clip.get("start", slot.get("start", 0.0)))
                end = float(clip.get("end", slot.get("end", start)))
            except (TypeError, ValueError):
                continue
            slot["start"] = round(start, 3)
            slot["end"] = round(max(start + 0.2, end), 3)
            slot["duration"] = round(slot["end"] - slot["start"], 3)
            slot["enabled"] = bool(clip.get("active", True))
            if clip.get("asset_path"):
                slot["asset_path"] = str(clip["asset_path"])
            if clip.get("variant_id"):
                slot["active_variant_id"] = str(clip["variant_id"])
            if clip.get("display_mode"):
                slot["display_mode"] = str(clip["display_mode"])
            if clip.get("scale") is not None:
                try:
                    slot["scale"] = float(clip["scale"])
                except (TypeError, ValueError):
                    pass
            slot["user_modified"] = True
            self.user_visual_edits = True


    def sync_visual_slots_to_editor_asset_plan(self, *, preserve_manual: bool = True):
        if not self.video_path or self.end_ms <= self.start_ms:
            return
        if not self.editor_asset_context_matches_current_selection():
            self.editor_asset_plan = set_editor_plan_context(
                self.editor_asset_plan,
                self.video_path,
                self.start_ms / 1000,
                self.end_ms / 1000,
                clear_clips_on_change=False,
            )
        clips = [
            self.visual_slot_to_editor_clip(slot, index)
            for index, slot in enumerate(self.visual_plan_slots)
            if isinstance(slot, dict)
        ]
        self.editor_asset_plan = replace_kind_clips(
            self.editor_asset_plan,
            "AI_VISUAL",
            clips,
            preserve_manual=preserve_manual,
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def sync_visual_slot_to_editor_asset_plan(self, index: int):
        if not (0 <= index < len(self.visual_plan_slots)):
            return
        slot = self.visual_plan_slots[index]
        if not isinstance(slot, dict):
            return
        self.editor_asset_plan = upsert_clip(
            self.editor_asset_plan,
            self.visual_slot_to_editor_clip(slot, index),
        )
        self.save_editor_asset_plan_state()
        self.refresh_editor_asset_timeline()


    def visual_plan_has_user_edits(self) -> bool:

        if self.user_visual_edits:
            return True

        return any(
            bool(
                slot.get(
                    "user_modified"
                )
            )
            for slot in self.visual_plan_slots
            if isinstance(
                slot,
                dict,
            )
        )


    def confirm_replace_visual_plan(self) -> bool:

        if not self.visual_plan_has_user_edits():
            return True

        box = QMessageBox(self)
        box.setWindowTitle(
            "Replace Visual Plan?"
        )
        box.setText(
            (
                "This clip has visual edits you made by hand. "
                "Keep them or replace them with a new AI plan?"
            )
        )
        keep_button = box.addButton(
            "KEEP MY VISUALS",
            QMessageBox.ButtonRole.RejectRole,
        )
        replace_button = box.addButton(
            "REPLACE WITH NEW AI PLAN",
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.setDefaultButton(
            keep_button
        )
        box.exec()

        return box.clickedButton() == replace_button


    def save_ai_visual_plan(self):

        output_path = (
            ROOT
            / "output"
            / "ai_visual_plan.json"
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        existing = {}
        if output_path.exists():
            try:
                existing = json.loads(
                    output_path.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                existing = {}

        payload = (
            existing
            if isinstance(
                existing,
                dict,
            )
            else {}
        )

        payload["source_video"] = (
            str(
                self.video_path
            )
            if self.video_path
            else payload.get(
                "source_video",
                "",
            )
        )
        payload["selection_start"] = round(
            self.start_ms
            / 1000,
            3,
        )
        payload["selection_end"] = round(
            self.end_ms
            / 1000,
            3,
        )
        payload["slot_count"] = len(
            self.visual_plan_slots
        )
        payload["user_modified"] = self.visual_plan_has_user_edits()
        payload["slots"] = self.visual_plan_slots

        try:
            output_path.write_text(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass


    def refresh_visual_assets_from_manifest(self):

        manifest_path = (
            ROOT
            / "output"
            / "ai_visual_assets"
            / "manifest.json"
        )

        if not manifest_path.exists():
            return

        try:
            data = json.loads(
                manifest_path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            json.JSONDecodeError,
        ):
            return

        assets = data.get(
            "assets",
            [],
        )
        if not isinstance(
            assets,
            list,
        ):
            return

        by_id = {
            str(
                asset.get(
                    "slot_id",
                    "",
                )
                or ""
            ): asset
            for asset in assets
            if isinstance(
                asset,
                dict,
            )
            and asset.get(
                "slot_id"
            )
        }

        by_index = {
            int(
                asset.get(
                    "slot_index",
                    0,
                )
            ): asset
            for asset in assets
            if isinstance(
                asset,
                dict,
            )
        }

        for index, slot in enumerate(
            self.visual_plan_slots,
            start=1,
        ):
            if not isinstance(
                slot,
                dict,
            ):
                continue

            asset = by_id.get(
                str(
                    slot.get(
                        "slot_id",
                        "",
                    )
                    or ""
                )
            )
            if asset is None:
                asset = by_index.get(
                    index
                )

            if asset is None:
                continue

            if (
                slot.get(
                    "user_modified"
                )
                and str(
                    slot.get(
                        "prompt",
                        "",
                    )
                    or ""
                )
                != str(
                    asset.get(
                        "prompt",
                        "",
                    )
                    or ""
                )
            ):
                continue

            slot["asset_path"] = str(
                asset.get(
                    "path",
                    "",
                )
                or ""
            )
            slot["generated"] = bool(
                asset.get(
                    "generated",
                    False,
                )
            )
            slot["provider"] = str(
                asset.get(
                    "provider",
                    "",
                )
                or ""
            )
            slot["state"] = str(
                asset.get(
                    "state",
                    slot.get(
                        "state",
                        "PLANNED",
                    ),
                )
                or "PLANNED"
            )
            if asset.get(
                "error"
            ):
                slot["error"] = str(
                    asset.get(
                        "error"
                    )
                )

            variant_id = str(
                asset.get(
                    "variant_id",
                    "",
                )
                or ""
            )
            if variant_id:
                variants = self.visual_variants(
                    slot
                )
                variant_data = {
                    "variant_id": variant_id,
                    "path": str(
                        asset.get(
                            "path",
                            "",
                        )
                        or ""
                    ),
                    "state": str(
                        asset.get(
                            "state",
                            "READY",
                        )
                        or "READY"
                    ),
                    "provider": str(
                        asset.get(
                            "provider",
                            "",
                        )
                        or ""
                    ),
                    "generated": bool(
                        asset.get(
                            "generated",
                            False,
                        )
                    ),
                }
                if "saved" in asset:
                    variant_data["saved"] = bool(
                        asset.get(
                            "saved",
                            False,
                        )
                    )

                replaced = False
                for variant_index, variant in enumerate(
                    variants
                ):
                    if str(
                        variant.get(
                            "variant_id",
                            "",
                        )
                        or ""
                    ) != variant_id:
                        continue
                    variants[variant_index] = {
                        **variant,
                        **variant_data,
                    }
                    replaced = True
                    break

                if not replaced:
                    variant_data.setdefault(
                        "saved",
                        False,
                    )
                    variants.append(
                        variant_data
                    )

                slot["active_variant_id"] = variant_id
                active_index = self.active_visual_variant_index(
                    slot
                )
                slot["saved_variant"] = bool(
                    active_index >= 0
                    and variants[active_index].get(
                        "saved",
                        False,
                    )
                )


    def visual_asset_path(
        self,
        slot: dict,
    ) -> Path | None:

        raw = str(
            slot.get(
                "asset_path",
                "",
            )
            or ""
        ).strip()

        if not raw:
            return None

        path = Path(
            raw
        )

        return path if path.exists() else None


    def make_visual_slot_widget(
        self,
        slot: dict,
        index: int,
    ) -> QWidget:

        frame = QFrame()
        frame.setObjectName(
            "VisualSlotCard"
        )
        frame.setProperty(
            "selected",
            index
            == self.selected_visual_slot_index,
        )

        layout = QHBoxLayout(
            frame
        )
        layout.setContentsMargins(
            8,
            7,
            8,
            7,
        )
        layout.setSpacing(
            9
        )

        thumb = QLabel()
        thumb.setObjectName(
            "VisualSlotThumb"
        )
        thumb.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        thumb.setFixedSize(
            48,
            66,
        )

        state_text = self.visual_slot_state_text(
            slot
        )
        asset_path = self.visual_asset_path(
            slot
        )

        if state_text == "READY" and asset_path is not None:
            pixmap = QPixmap(
                str(
                    asset_path
                )
            )
            if not pixmap.isNull():
                thumb.setPixmap(
                    pixmap.scaled(
                        QSize(
                            48,
                            66,
                        ),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                thumb.setText(
                    "IMG"
                )
        elif state_text == "PREVIEW ONLY":
            thumb.setText(
                "PREVIEW"
            )
        elif state_text == "GENERATING":
            thumb.setText(
                "GEN"
            )
        elif state_text == "FAILED":
            thumb.setText(
                "FAIL"
            )
        else:
            thumb.setText(
                "PLAN"
            )

        text_stack = QVBoxLayout()
        text_stack.setSpacing(
            3
        )

        try:
            start = float(
                slot.get(
                    "start",
                    0.0,
                )
            )
            end = float(
                slot.get(
                    "end",
                    start,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            start = 0.0
            end = 0.0

        label = str(
            slot.get(
                "label",
                f"Visual {index + 1}",
            )
            or f"Visual {index + 1}"
        ).upper()

        title = QLabel(
            label
        )
        title.setObjectName(
            "VisualSlotTitle"
        )
        title.setWordWrap(
            True
        )

        display_mode = self.normalize_visual_display_mode(
            slot.get(
                "display_mode",
                "OVERLAY_CARD",
            )
        )
        scale_percent = int(
            round(
                self.coerce_visual_scale(
                    slot.get(
                        "scale",
                        1.0,
                    )
                )
                * 100
            )
        )
        variant_number, variant_count, variant_saved = (
            self.visual_variant_state(
                slot
            )
        )
        variant_text = (
            f"    VAR {variant_number}/{variant_count}"
            if variant_count
            else ""
        )
        if variant_saved:
            variant_text += " KEEP"

        meta = QLabel(
            (
                f"{format_time(int(start * 1000))} -> "
                f"{format_time(int(end * 1000))}    "
                f"{state_text}    "
                f"{display_mode} / {scale_percent}%"
                f"{variant_text}"
            )
        )
        meta.setObjectName(
            "VisualSlotMeta"
        )

        text_stack.addWidget(
            title
        )
        text_stack.addWidget(
            meta
        )
        text_stack.addStretch()

        layout.addWidget(
            thumb
        )
        layout.addLayout(
            text_stack,
            1,
        )

        frame.style().unpolish(
            frame
        )
        frame.style().polish(
            frame
        )

        return frame


    def refresh_visual_plan_display(self):

        self.ensure_visual_slot_defaults()

        if not hasattr(
            self,
            "visual_slots_list",
        ):
            return

        self.visual_slots_list.clear()

        visual_ranges: list[tuple[int, int]] = []

        for index, slot in enumerate(
            self.visual_plan_slots
        ):
            if not isinstance(
                slot,
                dict,
            ):
                continue

            try:
                start = float(
                    slot.get(
                        "start",
                        0.0,
                    )
                )
                end = float(
                    slot.get(
                        "end",
                        start,
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            start_ms = int(
                round(
                    start
                    * 1000
                )
            )
            end_ms = int(
                round(
                    end
                    * 1000
                )
            )

            item = QListWidgetItem()
            item.setData(
                Qt.ItemDataRole.UserRole,
                index,
            )
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                start_ms,
            )
            item.setData(
                Qt.ItemDataRole.UserRole + 2,
                end_ms,
            )
            item.setSizeHint(
                QSize(
                    120,
                    82,
                )
            )
            item.setToolTip(
                (
                    f"{start:.2f}s -> {end:.2f}s\n\n"
                    f"WHY:\n{slot.get('reason', '')}\n\n"
                    f"GENERATION PROMPT:\n{slot.get('prompt', '')}"
                )
            )

            self.visual_slots_list.addItem(
                item
            )
            self.visual_slots_list.setItemWidget(
                item,
                self.make_visual_slot_widget(
                    slot,
                    index,
                ),
            )

            if (
                slot.get(
                    "enabled",
                    True,
                )
                and self.visual_slot_state_text(
                    slot
                )
                != "FAILED"
                and end_ms > start_ms
            ):
                visual_ranges.append(
                    (
                        start_ms,
                        end_ms,
                    )
                )

        has_editor_visual_clips = bool(
            hasattr(self, "editor_asset_plan")
            and self.editor_asset_context_matches_current_selection()
            and clips_of_kind(
                self.editor_asset_plan,
                "AI_VISUAL",
            )
        )
        self.timeline.set_visual_ranges(
            []
            if has_editor_visual_clips
            else visual_ranges
        )

        if (
            self.selected_visual_slot_index is not None
            and 0
            <= self.selected_visual_slot_index
            < self.visual_slots_list.count()
        ):
            self.visual_slots_list.setCurrentRow(
                self.selected_visual_slot_index
            )
            slot = self.visual_plan_slots[
                self.selected_visual_slot_index
            ]
            if has_editor_visual_clips:
                self.timeline.set_selected_visual_range(
                    None
                )
            else:
                try:
                    self.timeline.set_selected_visual_range(
                        int(
                            round(
                                float(
                                    slot.get(
                                        "start",
                                        0.0,
                                    )
                                )
                                * 1000
                            )
                        ),
                        int(
                            round(
                                float(
                                    slot.get(
                                        "end",
                                        0.0,
                                    )
                                )
                                * 1000
                            )
                        ),
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    self.timeline.set_selected_visual_range(
                        None
                    )
        else:
            self.timeline.set_selected_visual_range(
                None
            )

        self.generate_visual_assets_button.setEnabled(
            bool(
                self.visual_plan_slots
            )
            and self.visual_asset_process.state()
            == QProcess.ProcessState.NotRunning
        )
        self.update_visual_inspector_buttons()


    def selected_visual_slot(self) -> dict | None:

        if self.selected_visual_slot_index is None:
            return None

        if not (
            0
            <= self.selected_visual_slot_index
            < len(
                self.visual_plan_slots
            )
        ):
            return None

        slot = self.visual_plan_slots[
            self.selected_visual_slot_index
        ]

        return slot if isinstance(
            slot,
            dict,
        ) else None


    def set_visual_inspector_enabled(
        self,
        enabled: bool,
    ):

        for widget in (
            self.visual_label_edit,
            self.visual_start_edit,
            self.visual_end_edit,
            self.visual_type_edit,
            self.visual_display_mode_combo,
            self.visual_prompt_edit,
            self.keep_visual_variant_button,
            self.generate_more_visual_button,
            self.previous_visual_variant_button,
            self.next_visual_variant_button,
            self.disable_visual_button,
            self.delete_visual_button,
        ):
            widget.setEnabled(
                enabled
            )

        self.visual_scale_slider.setEnabled(
            enabled
        )

        self.regenerate_visual_button.setEnabled(
            enabled
            and self.image_ai_state == "ready"
            and self.visual_asset_process.state()
            == QProcess.ProcessState.NotRunning
        )


    def normalize_visual_display_mode(
        self,
        value,
    ) -> str:

        normalized = str(
            value or ""
        ).strip().upper()

        if normalized in {
            "OVERLAY_CARD",
            "FULL_FRAME_CONTAIN",
            "FULL_FRAME_COVER",
        }:
            return normalized

        return "OVERLAY_CARD"


    def coerce_visual_scale(
        self,
        value,
    ) -> float:

        try:
            number = float(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            number = 1.0

        return max(
            0.6,
            min(
                1.4,
                number,
            ),
        )


    def visual_variants(
        self,
        slot: dict | None,
    ) -> list[dict]:

        if not isinstance(
            slot,
            dict,
        ):
            return []

        variants = slot.get(
            "variants",
            [],
        )
        if not isinstance(
            variants,
            list,
        ):
            variants = []
            slot["variants"] = variants

        variants = [
            variant
            for variant in variants
            if isinstance(
                variant,
                dict,
            )
        ]
        slot["variants"] = variants

        current_path = str(
            slot.get(
                "asset_path",
                "",
            )
            or ""
        ).strip()
        active_variant_id = str(
            slot.get(
                "active_variant_id",
                "",
            )
            or ""
        ).strip()

        if current_path and not variants:
            variant_id = (
                active_variant_id
                or "variant_001"
            )
            variants.append(
                {
                    "variant_id": variant_id,
                    "path": current_path,
                    "state": str(
                        slot.get(
                            "state",
                            "READY",
                        )
                        or "READY"
                    ),
                    "provider": str(
                        slot.get(
                            "provider",
                            "",
                        )
                        or ""
                    ),
                    "generated": bool(
                        slot.get(
                            "generated",
                            False,
                        )
                    ),
                    "saved": bool(
                        slot.get(
                            "saved_variant",
                            False,
                        )
                    ),
                }
            )
            slot["active_variant_id"] = variant_id
            active_variant_id = variant_id

        if variants:
            known_ids = {
                str(
                    variant.get(
                        "variant_id",
                        "",
                    )
                    or ""
                )
                for variant in variants
            }
            if (
                not active_variant_id
                or active_variant_id not in known_ids
            ):
                fallback_id = str(
                    variants[0].get(
                        "variant_id",
                        "variant_001",
                    )
                    or "variant_001"
                )
                slot["active_variant_id"] = fallback_id

        return variants


    def active_visual_variant_index(
        self,
        slot: dict | None,
    ) -> int:

        variants = self.visual_variants(
            slot
        )
        if not variants or not isinstance(
            slot,
            dict,
        ):
            return -1

        active_variant_id = str(
            slot.get(
                "active_variant_id",
                "",
            )
            or ""
        )
        for index, variant in enumerate(
            variants
        ):
            if str(
                variant.get(
                    "variant_id",
                    "",
                )
                or ""
            ) == active_variant_id:
                return index

        return 0


    def visual_variant_state(
        self,
        slot: dict | None,
    ) -> tuple[int, int, bool]:

        variants = self.visual_variants(
            slot
        )
        index = self.active_visual_variant_index(
            slot
        )
        if index < 0:
            return 0, 0, False

        return (
            index + 1,
            len(variants),
            bool(
                variants[index].get(
                    "saved",
                    False,
                )
            ),
        )


    def select_visual_variant(
        self,
        offset: int,
    ):

        slot = self.selected_visual_slot()
        if slot is None:
            return

        variants = self.visual_variants(
            slot
        )
        if not variants:
            return

        current = self.active_visual_variant_index(
            slot
        )
        if current < 0:
            current = 0

        next_index = (
            current
            + int(offset)
        ) % len(variants)
        variant = variants[
            next_index
        ]

        slot["active_variant_id"] = str(
            variant.get(
                "variant_id",
                "",
            )
            or ""
        )
        if variant.get(
            "path"
        ):
            slot["asset_path"] = str(
                variant.get(
                    "path"
                )
            )
        slot["state"] = str(
            variant.get(
                "state",
                slot.get(
                    "state",
                    "READY",
                ),
            )
            or slot.get(
                "state",
                "READY",
            )
        )
        slot["provider"] = str(
            variant.get(
                "provider",
                slot.get(
                    "provider",
                    "",
                ),
            )
            or ""
        )
        slot["generated"] = bool(
            variant.get(
                "generated",
                slot.get(
                    "generated",
                    False,
                ),
            )
        )
        slot["saved_variant"] = bool(
            variant.get(
                "saved",
                False,
            )
        )

        self.mark_visual_slot_modified(
            slot
        )
        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()


    def previous_visual_variant(self):

        self.select_visual_variant(
            -1
        )


    def next_visual_variant(self):

        self.select_visual_variant(
            1
        )


    def keep_selected_visual_variant(self):

        slot = self.selected_visual_slot()
        if slot is None:
            return

        variants = self.visual_variants(
            slot
        )
        index = self.active_visual_variant_index(
            slot
        )
        if index < 0:
            return

        variants[index]["saved"] = True
        slot["saved_variant"] = True

        self.mark_visual_slot_modified(
            slot
        )
        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()
        self.visual_status_label.setText(
            "Active image variant kept. Future generation will preserve it."
        )


    def generate_more_selected_visual_variant(self):

        slot = self.selected_visual_slot()
        if slot is None:
            return


        if self.image_ai_state != "ready":
            self.visual_status_label.setText(
                "Image AI is offline. Existing variants are preserved."
            )
            return

        self.visual_inspector_fields_changed()
        slot = self.selected_visual_slot()
        if slot is None:
            return

        slot["force_new_variant"] = True
        self.mark_visual_slot_modified(
            slot
        )
        self.save_ai_visual_plan()
        self.start_visual_asset_generation(
            str(
                slot.get(
                    "slot_id",
                    "",
                )
                or ""
            ),
            new_variant=True,
        )


    def visual_scale_changed(
        self,
        value: int,
    ):

        self.visual_scale_label.setText(
            f"{int(value)}%"
        )

        if self.updating_visual_inspector:
            return

        self.visual_inspector_fields_changed()


    def load_selected_visual_into_inspector(self):

        if not hasattr(
            self,
            "visual_label_edit",
        ):
            return

        slot = self.selected_visual_slot()
        self.updating_visual_inspector = True

        if slot is None:
            self.visual_inspector_title.setText(
                "SELECT VISUAL SLOT"
            )
            self.visual_label_edit.setText("")
            self.visual_start_edit.setText("")
            self.visual_end_edit.setText("")
            self.visual_type_edit.setText("")
            self.visual_display_mode_combo.setCurrentText(
                "OVERLAY_CARD"
            )
            self.visual_scale_slider.setValue(
                100
            )
            self.visual_scale_label.setText(
                "100%"
            )
            self.visual_variant_label.setText(
                "0/0"
            )
            self.keep_visual_variant_button.setText(
                "KEEP"
            )
            self.visual_reason_label.setText(
                "Select a planned visual to inspect it."
            )
            self.visual_prompt_edit.setPlainText("")
            self.visual_preview_label.setPixmap(
                QPixmap()
            )
            self.visual_preview_label.setText(
                "NO IMAGE"
            )
            self.set_visual_inspector_enabled(
                False
            )
            self.updating_visual_inspector = False
            return

        self.visual_inspector_title.setText(
            (
                "VISUAL SLOT "
                f"{self.selected_visual_slot_index + 1:02d}"
            )
        )
        self.visual_label_edit.setText(
            str(
                slot.get(
                    "label",
                    "",
                )
                or ""
            )
        )
        self.visual_start_edit.setText(
            f"{float(slot.get('start', 0.0)):.3f}"
        )
        self.visual_end_edit.setText(
            f"{float(slot.get('end', 0.0)):.3f}"
        )
        self.visual_type_edit.setText(
            str(
                slot.get(
                    "visual_type",
                    "",
                )
                or ""
            )
        )
        self.visual_display_mode_combo.setCurrentText(
            self.normalize_visual_display_mode(
                slot.get(
                    "display_mode",
                    "OVERLAY_CARD",
                )
            )
        )
        visual_scale_percent = int(
            round(
                self.coerce_visual_scale(
                    slot.get(
                        "scale",
                        1.0,
                    )
                )
                * 100
            )
        )
        self.visual_scale_slider.setValue(
            visual_scale_percent
        )
        self.visual_scale_label.setText(
            f"{visual_scale_percent}%"
        )

        variant_number, variant_count, variant_saved = (
            self.visual_variant_state(
                slot
            )
        )
        self.visual_variant_label.setText(
            (
                f"{variant_number}/{variant_count}"
                + (
                    "  KEPT"
                    if variant_saved
                    else ""
                )
            )
        )
        self.keep_visual_variant_button.setText(
            "KEPT"
            if variant_saved
            else "KEEP"
        )
        self.visual_reason_label.setText(
            (
                "Why AI suggested this: "
                + str(
                    slot.get(
                        "reason",
                        "",
                    )
                    or "No reason was recorded."
                )
            )
        )
        self.visual_prompt_edit.setPlainText(
            str(
                slot.get(
                    "prompt",
                    "",
                )
                or ""
            )
        )

        asset_path = self.visual_asset_path(
            slot
        )
        state_text = self.visual_slot_state_text(
            slot
        )

        self.visual_preview_label.setPixmap(
            QPixmap()
        )
        if state_text == "READY" and asset_path is not None:
            pixmap = QPixmap(
                str(
                    asset_path
                )
            )
            if not pixmap.isNull():
                self.visual_preview_label.setText("")
                self.visual_preview_label.setPixmap(
                    pixmap.scaled(
                        self.visual_preview_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                self.visual_preview_label.setText(
                    "IMAGE"
                )
        elif state_text == "PREVIEW ONLY":
            self.visual_preview_label.setText(
                "PREVIEW ONLY"
            )
        elif state_text == "FAILED":
            self.visual_preview_label.setText(
                "FAILED"
            )
        else:
            self.visual_preview_label.setText(
                state_text
            )

        self.disable_visual_button.setText(
            (
                "ENABLE"
                if slot.get(
                    "enabled",
                    True,
                )
                is False
                else "DISABLE"
            )
        )
        self.set_visual_inspector_enabled(
            True
        )
        self.updating_visual_inspector = False
        self.update_visual_inspector_buttons()


    def mark_visual_slot_modified(
        self,
        slot: dict,
    ):

        slot["user_modified"] = True
        self.user_visual_edits = True


    def visual_inspector_fields_changed(self):

        if self.updating_visual_inspector:
            return

        slot = self.selected_visual_slot()
        if slot is None:
            return

        old_prompt = str(
            slot.get(
                "prompt",
                "",
            )
            or ""
        )

        try:
            start = float(
                self.visual_start_edit.text()
            )
            end = float(
                self.visual_end_edit.text()
            )
        except ValueError:
            start = float(
                slot.get(
                    "start",
                    0.0,
                )
            )
            end = float(
                slot.get(
                    "end",
                    start,
                )
            )

        selection_start = self.start_ms / 1000
        selection_end = self.end_ms / 1000
        start = max(
            selection_start,
            min(
                selection_end,
                start,
            ),
        )
        end = max(
            start
            + 0.2,
            min(
                selection_end,
                end,
            ),
        )

        slot["label"] = self.visual_label_edit.text().strip() or "AI Visual"
        slot["start"] = round(
            start,
            3,
        )
        slot["end"] = round(
            end,
            3,
        )
        slot["duration"] = round(
            end
            - start,
            3,
        )
        slot["visual_type"] = (
            self.visual_type_edit.text().strip()
            or "ai_recreation"
        )
        slot["display_mode"] = (
            self.normalize_visual_display_mode(
                self.visual_display_mode_combo.currentText()
            )
        )
        slot["scale"] = round(
            self.coerce_visual_scale(
                self.visual_scale_slider.value()
                / 100.0
            ),
            2,
        )

        if old_prompt != str(
            slot.get(
                "prompt",
                "",
            )
            or ""
        ):
            slot["state"] = "PLANNED"

        self.mark_visual_slot_modified(
            slot
        )
        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()


    def visual_prompt_changed(self):

        if self.updating_visual_inspector:
            return

        slot = self.selected_visual_slot()
        if slot is None:
            return

        new_prompt = self.visual_prompt_edit.toPlainText().strip()
        old_prompt = str(
            slot.get(
                "prompt",
                "",
            )
            or ""
        ).strip()

        if new_prompt == old_prompt:
            return

        slot["prompt"] = new_prompt
        if self.visual_slot_state_text(
            slot
        ) in (
            "READY",
            "PREVIEW ONLY",
        ):
            slot["state"] = "PLANNED"
        self.mark_visual_slot_modified(
            slot
        )
        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()


    def update_visual_inspector_buttons(self):

        if not hasattr(
            self,
            "regenerate_visual_button",
        ):
            return

        slot = self.selected_visual_slot()
        running = (
            self.visual_asset_process.state()
            != QProcess.ProcessState.NotRunning
        )
        selected = slot is not None
        enabled = bool(
            slot.get(
                "enabled",
                True,
            )
        ) if slot else False

        variants = (
            self.visual_variants(
                slot
            )
            if selected
            else []
        )

        self.regenerate_visual_button.setEnabled(
            selected
            and enabled
            and self.image_ai_state == "ready"
            and not running
        )
        self.keep_visual_variant_button.setEnabled(
            selected
            and bool(variants)
            and not running
        )
        self.generate_more_visual_button.setEnabled(
            selected
            and enabled
            and self.image_ai_state == "ready"
            and not running
        )
        self.previous_visual_variant_button.setEnabled(
            selected
            and len(variants) > 1
            and not running
        )
        self.next_visual_variant_button.setEnabled(
            selected
            and len(variants) > 1
            and not running
        )
        self.disable_visual_button.setEnabled(
            selected
            and not running
        )
        self.delete_visual_button.setEnabled(
            selected
            and not running
        )


    def apply_visual_generation_event(
        self,
        event: dict,
    ):

        slot_id = str(
            event.get(
                "slot_id",
                "",
            )
            or ""
        )
        slot_index = event.get(
            "slot_index"
        )

        slot: dict | None = None
        index_match: int | None = None

        for index, candidate in enumerate(
            self.visual_plan_slots
        ):
            if not isinstance(
                candidate,
                dict,
            ):
                continue

            if slot_id and candidate.get(
                "slot_id"
            ) == slot_id:
                slot = candidate
                index_match = index
                break

        if slot is None:
            try:
                index_match = int(
                    slot_index
                ) - 1
            except (
                TypeError,
                ValueError,
            ):
                index_match = None

            if (
                index_match is not None
                and 0
                <= index_match
                < len(
                    self.visual_plan_slots
                )
            ):
                slot = self.visual_plan_slots[
                    index_match
                ]

        if slot is None:
            return

        state = str(
            event.get(
                "state",
                "",
            )
            or ""
        ).upper()

        if state:
            slot["state"] = state

        if event.get(
            "path"
        ):
            slot["asset_path"] = str(
                event.get(
                    "path"
                )
            )

        variant_id = str(
            event.get(
                "variant_id",
                "",
            )
            or ""
        )
        if variant_id:
            variants = self.visual_variants(
                slot
            )
            event_path = str(
                event.get(
                    "path",
                    "",
                )
                or ""
            )
            replaced = False
            for variant_index, variant in enumerate(
                variants
            ):
                if str(
                    variant.get(
                        "variant_id",
                        "",
                    )
                    or ""
                ) != variant_id:
                    continue

                variants[variant_index] = {
                    **variant,
                    "variant_id": variant_id,
                    "path": event_path or str(
                        variant.get(
                            "path",
                            "",
                        )
                        or ""
                    ),
                    "state": state or str(
                        variant.get(
                            "state",
                            "READY",
                        )
                        or "READY"
                    ),
                    "provider": str(
                        event.get(
                            "provider",
                            variant.get(
                                "provider",
                                "",
                            ),
                        )
                        or ""
                    ),
                    "generated": bool(
                        event.get(
                            "generated",
                            variant.get(
                                "generated",
                                False,
                            ),
                        )
                    ),
                }
                replaced = True
                break

            if not replaced:
                variants.append(
                    {
                        "variant_id": variant_id,
                        "path": event_path,
                        "state": state or "READY",
                        "provider": str(
                            event.get(
                                "provider",
                                "",
                            )
                            or ""
                        ),
                        "generated": bool(
                            event.get(
                                "generated",
                                False,
                            )
                        ),
                        "saved": False,
                    }
                )

            slot["active_variant_id"] = variant_id
            slot["saved_variant"] = bool(
                next(
                    (
                        variant.get(
                            "saved",
                            False,
                        )
                        for variant in variants
                        if str(
                            variant.get(
                                "variant_id",
                                "",
                            )
                            or ""
                        ) == variant_id
                    ),
                    False,
                )
            )
            slot.pop(
                "force_new_variant",
                None,
            )

        if "generated" in event:
            slot["generated"] = bool(
                event.get(
                    "generated"
                )
            )

        if event.get(
            "provider"
        ):
            slot["provider"] = str(
                event.get(
                    "provider"
                )
            )

        if event.get(
            "error"
        ):
            slot["error"] = str(
                event.get(
                    "error"
                )
            )

        self.save_ai_visual_plan()
        if index_match is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                index_match
            )
        self.refresh_visual_plan_display()

        if index_match == self.selected_visual_slot_index:
            self.load_selected_visual_into_inspector()


    def start_visual_asset_generation(
        self,
        slot_id: str = "",
        new_variant: bool = False,
    ):

        if not self.visual_plan_slots:
            return

        if (
            self.visual_asset_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        generator_script = (
            ROOT
            / "app"
            / "generate_ai_visual_assets.py"
        )

        plan_path = (
            ROOT
            / "output"
            / "ai_visual_plan.json"
        )

        if not generator_script.exists():
            self.visual_status_label.setText(
                "generate_ai_visual_assets.py is not installed."
            )
            return

        self.save_ai_visual_plan()

        self.visual_asset_output_buffer = ""
        self.generate_visual_assets_button.setEnabled(
            False
        )
        self.generate_visual_assets_button.setText(
            "Generating..."
        )
        self.plan_visuals_button.setEnabled(
            False
        )
        self.visual_status_label.setText(
            "Generating visual assets..."
        )
        self.update_visual_inspector_buttons()

        self.render_log.append(
            ""
        )
        self.render_log.append(
            "=== AI VISUAL ASSET GENERATION ==="
        )

        args = [
            str(
                generator_script
            ),
            "--plan",
            str(
                plan_path
            ),
            "--asset-dir",
            str(
                ROOT
                / "output"
                / "ai_visual_assets"
            ),
            "--provider",
            "auto",
            "--quality",
            self.image_quality,
        ]

        if self.selected_image_model_title:
            args.extend(
                [
                    "--model",
                    self.selected_image_model_title,
                ]
            )

        if slot_id:
            args.extend(
                [
                    "--slot-id",
                    slot_id,
                ]
            )

        if new_variant:
            args.append(
                "--new-variant"
            )

        self.visual_asset_process.start(
            sys.executable,
            args,
        )
        self.update_image_ai_indicator()
        self.update_visual_inspector_buttons()


    def regenerate_selected_visual_asset(self):

        slot = self.selected_visual_slot()
        if slot is None:
            return

        if self.image_ai_state != "ready":
            self.visual_status_label.setText(
                "Image AI is offline. Existing assets are preserved."
            )
            return

        self.visual_inspector_fields_changed()
        slot = self.selected_visual_slot()
        if slot is None:
            return

        slot["state"] = "GENERATING"
        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()
        self.start_visual_asset_generation(
            str(
                slot.get(
                    "slot_id",
                    "",
                )
                or ""
            )
        )


    def toggle_selected_visual_enabled(self):

        slot = self.selected_visual_slot()
        if slot is None:
            return

        slot["enabled"] = not bool(
            slot.get(
                "enabled",
                True,
            )
        )
        self.mark_visual_slot_modified(
            slot
        )
        self.save_ai_visual_plan()
        if self.selected_visual_slot_index is not None:
            self.sync_visual_slot_to_editor_asset_plan(
                self.selected_visual_slot_index
            )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()


    def delete_selected_visual(self):

        if self.selected_visual_slot_index is None:
            return

        if not (
            0
            <= self.selected_visual_slot_index
            < len(
                self.visual_plan_slots
            )
        ):
            return

        deleted_index = self.selected_visual_slot_index
        deleted_slot = self.visual_plan_slots[
            deleted_index
        ]
        deleted_clip_id = (
            self.visual_clip_id(
                deleted_slot,
                deleted_index,
            )
            if isinstance(
                deleted_slot,
                dict,
            )
            else ""
        )

        del self.visual_plan_slots[
            deleted_index
        ]
        self.user_visual_edits = True

        if deleted_clip_id:
            self.editor_asset_plan["clips"] = [
                clip
                for clip in self.editor_asset_plan.get(
                    "clips",
                    [],
                )
                if not (
                    isinstance(
                        clip,
                        dict,
                    )
                    and str(
                        clip.get(
                            "id",
                            "",
                        )
                        or ""
                    )
                    == deleted_clip_id
                )
            ]
            self.save_editor_asset_plan_state()

        if self.selected_visual_slot_index >= len(
            self.visual_plan_slots
        ):
            self.selected_visual_slot_index = (
                len(
                    self.visual_plan_slots
                )
                - 1
            )

        if self.selected_visual_slot_index < 0:
            self.selected_visual_slot_index = None

        self.save_ai_visual_plan()
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()
        self.refresh_editor_asset_timeline()


    def clear_visual_plan_display(self):

        self.visual_plan_slots = []
        self.selected_visual_slot_index = None

        if hasattr(
            self,
            "visual_slots_list",
        ):
            self.visual_slots_list.clear()

        if hasattr(
            self,
            "visual_status_label",
        ):
            self.visual_status_label.setText(
                "Visual plan is not generated for this selection yet."
            )

        self.timeline.set_visual_ranges(
            []
        )
        self.timeline.set_selected_visual_range(
            None
        )

        if hasattr(
            self,
            "generate_visual_assets_button",
        ):
            self.generate_visual_assets_button.setEnabled(
                False
            )

        if hasattr(
            self,
            "visual_label_edit",
        ):
            self.load_selected_visual_into_inspector()


    def append_visual_log(
        self,
        data: str,
    ):

        if not data:
            return

        self.render_log.moveCursor(
            self.render_log
            .textCursor()
            .MoveOperation
            .End
        )

        self.render_log.insertPlainText(
            data
        )

        scrollbar = (
            self.render_log
            .verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )


    def read_visual_output(self):

        data = (
            self.visual_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_visual_log(
            data
        )


    def read_visual_error(self):

        data = (
            self.visual_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_visual_log(
            data
        )


    def plan_ai_visuals(self):

        if (
            not self.video_path
            or not self.source_transcript_segments
            or self.end_ms <= self.start_ms
        ):
            return

        if (
            self.visual_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        planner_script = (
            ROOT
            / "app"
            / "ai_visual_planner.py"
        )

        transcript_path = (
            ROOT
            / "output"
            / "subtitles.json"
        )

        if not planner_script.exists():

            self.visual_status_label.setText(
                "ai_visual_planner.py is not installed."
            )
            return

        if not self.confirm_replace_visual_plan():
            self.visual_status_label.setText(
                "Kept your existing visual plan."
            )
            return

        self.ensure_current_editor_asset_context(
            clear_on_change=True
        )
        self.editor_asset_plan["clips"] = [
            clip
            for clip in self.editor_asset_plan.get(
                "clips",
                [],
            )
            if not (
                isinstance(
                    clip,
                    dict,
                )
                and str(
                    clip.get(
                        "kind",
                        "",
                    )
                    or ""
                ).upper()
                == "AI_VISUAL"
            )
        ]
        self.save_editor_asset_plan_state()
        self.user_visual_edits = False
        self.selected_visual_slot_index = None

        self.clear_visual_plan_display()

        self.plan_visuals_button.setEnabled(
            False
        )

        self.plan_visuals_button.setText(
            "Planning..."
        )

        self.visual_status_label.setText(
            "Local AI is choosing sparse visual cutaway moments..."
        )

        self.save_manual_edit_plan()
        self.save_transcript_corrections()

        self.render_log.append(
            ""
        )
        self.render_log.append(
            "=== AI VISUAL CUTAWAY PLANNER ==="
        )
        self.render_log.append(
            (
                f"Planning visuals for "
                f"{self.start_ms / 1000:.2f}s -> "
                f"{self.end_ms / 1000:.2f}s"
            )
        )

        output_path = (
            ROOT
            / "output"
            / "ai_visual_plan.json"
        )

        self.visual_process.start(
            sys.executable,
            [
                str(
                    planner_script
                ),
                "--video",
                str(
                    self.video_path
                ),
                "--transcript",
                str(
                    transcript_path
                ),
                "--start",
                f"{self.start_ms / 1000:.3f}",
                "--end",
                f"{self.end_ms / 1000:.3f}",
                "--corrections",
                str(
                    ROOT
                    / "output"
                    / "transcript_corrections.json"
                ),
                "--manual-cuts",
                str(
                    ROOT
                    / "output"
                    / "manual_edit_plan.json"
                ),
                "--output",
                str(
                    output_path
                ),
            ],
        )


    def load_ai_visual_plan(self):

        plan_path = (
            ROOT
            / "output"
            / "ai_visual_plan.json"
        )

        if not plan_path.exists():
            raise FileNotFoundError(
                f"Visual plan not found: {plan_path}"
            )

        data = json.loads(
            plan_path.read_text(
                encoding="utf-8"
            )
        )

        plan_start = float(
            data.get(
                "selection_start",
                -1.0,
            )
        )

        plan_end = float(
            data.get(
                "selection_end",
                -1.0,
            )
        )

        current_start = (
            self.start_ms
            / 1000
        )

        current_end = (
            self.end_ms
            / 1000
        )

        if (
            abs(
                plan_start
                - current_start
            )
            > 0.08
            or abs(
                plan_end
                - current_end
            )
            > 0.08
        ):
            raise RuntimeError(
                "Visual plan belongs to a different clip selection."
            )

        slots = data.get(
            "slots",
            [],
        )

        if not isinstance(
            slots,
            list,
        ):
            slots = []

        self.visual_plan_slots = [
            slot
            for slot in slots
            if isinstance(
                slot,
                dict,
            )
        ]

        self.user_visual_edits = bool(
            data.get(
                "user_modified",
                False,
            )
        )
        self.selected_visual_slot_index = (
            0
            if self.visual_plan_slots
            else None
        )
        self.ensure_visual_slot_defaults()
        self.refresh_visual_assets_from_manifest()
        self.apply_editor_visual_overrides_to_slots()
        self.save_ai_visual_plan()
        self.sync_visual_slots_to_editor_asset_plan(
            preserve_manual=True
        )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()

        if self.visual_plan_slots:

            self.visual_status_label.setText(
                (
                    f"{len(self.visual_plan_slots)} proposed cutaway"
                    + (
                        ""
                        if len(self.visual_plan_slots) == 1
                        else "s"
                    )
                    + ". Click one to inspect, seek, and select its range."
                )
            )

        else:

            self.visual_status_label.setText(
                "No AI visual cutaway is necessary for this selection."
            )


    def visual_plan_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        self.plan_visuals_button.setText(
            "✦ PLAN VISUALS"
        )

        self.plan_visuals_button.setEnabled(
            bool(
                self.video_path
                and self.source_transcript_segments
                and self.end_ms > self.start_ms
            )
        )

        if exit_code != 0:

            self.visual_status_label.setText(
                "Visual planning failed. See the render log."
            )

            self.render_log.append(
                f"✕ VISUAL PLANNER FAILED (exit code {exit_code})"
            )

            return

        try:

            self.load_ai_visual_plan()

        except Exception as exc:

            self.visual_status_label.setText(
                "Could not display the visual plan."
            )

            self.render_log.append(
                f"Could not display visual plan: {exc}"
            )

            return

        self.render_log.append(
            ""
        )

        self.render_log.append(
            (
                f"✓ AI visual plan ready: "
                f"{len(self.visual_plan_slots)} slot(s)."
            )
        )


    def read_visual_asset_output(self):

        data = (
            self.visual_asset_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        if not data:
            return

        self.visual_asset_output_buffer += data

        lines = self.visual_asset_output_buffer.splitlines(
            keepends=True
        )

        if (
            lines
            and not lines[-1].endswith(
                (
                    "\n",
                    "\r",
                )
            )
        ):
            self.visual_asset_output_buffer = lines.pop()
        else:
            self.visual_asset_output_buffer = ""

        log_text = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith(
                VISUAL_EVENT_PREFIX
            ):
                try:
                    event = json.loads(
                        stripped[
                            len(
                                VISUAL_EVENT_PREFIX
                            ):
                        ]
                    )
                except json.JSONDecodeError:
                    continue

                self.apply_visual_generation_event(
                    event
                )

                state = str(
                    event.get(
                        "state",
                        "",
                    )
                    or ""
                ).replace(
                    "_",
                    " ",
                )
                label = str(
                    event.get(
                        "label",
                        "visual",
                    )
                    or "visual"
                )
                if state:
                    log_text.append(
                        f"Visual {event.get('slot_index', '')}: {label} - {state}\n"
                    )
            else:
                log_text.append(
                    line
                )

        if log_text:
            self.append_visual_log(
                "".join(
                    log_text
                )
            )


    def read_visual_asset_error(self):

        data = (
            self.visual_asset_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_visual_log(
            data
        )


    def generate_visual_assets(self):

        self.start_visual_asset_generation()


    def visual_asset_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        del exit_status

        if self.visual_asset_output_buffer.strip():
            self.append_visual_log(
                self.visual_asset_output_buffer
            )
            self.visual_asset_output_buffer = ""

        self.generate_visual_assets_button.setText(
            "⬡ GENERATE ASSETS"
        )

        self.generate_visual_assets_button.setEnabled(
            bool(
                self.visual_plan_slots
            )
        )

        self.plan_visuals_button.setEnabled(
            bool(
                self.video_path
                and self.source_transcript_segments
                and self.end_ms > self.start_ms
            )
        )

        self.update_image_ai_indicator()
        self.update_visual_inspector_buttons()

        if exit_code != 0:

            self.visual_status_label.setText(
                "Visual asset generation failed. See render log."
            )

            self.render_log.append(
                f"✕ VISUAL ASSET GENERATION FAILED (exit code {exit_code})"
            )

            return

        self.refresh_visual_assets_from_manifest()
        self.save_ai_visual_plan()
        self.sync_visual_slots_to_editor_asset_plan(
            preserve_manual=True
        )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()

        manifest_path = (
            ROOT
            / "output"
            / "ai_visual_assets"
            / "manifest.json"
        )

        provider_summary = ""

        if manifest_path.exists():

            try:

                data = json.loads(
                    manifest_path.read_text(
                        encoding="utf-8"
                    )
                )

                assets = data.get(
                    "assets",
                    [],
                )

                real_count = sum(
                    1
                    for item in assets
                    if isinstance(
                        item,
                        dict,
                    )
                    and item.get(
                        "generated"
                    )
                )

                preview_count = sum(
                    1
                    for item in assets
                    if isinstance(
                        item,
                        dict,
                    )
                    and str(
                        item.get(
                            "state",
                            "",
                        )
                        or ""
                    ).upper()
                    == "PREVIEW_ONLY"
                )

                failed_count = sum(
                    1
                    for item in assets
                    if isinstance(
                        item,
                        dict,
                    )
                    and str(
                        item.get(
                            "state",
                            "",
                        )
                        or ""
                    ).upper()
                    == "FAILED"
                )

                provider_summary = (
                    f"{real_count} ready, "
                    f"{preview_count} preview-only, "
                    f"{failed_count} failed"
                )

                if failed_count:
                    provider_summary += (
                        ". Try BALANCED or FAST if memory ran out"
                    )

            except (
                OSError,
                json.JSONDecodeError,
            ):

                provider_summary = ""

        if provider_summary:

            self.visual_status_label.setText(
                (
                    "Assets ready — "
                    f"{provider_summary}. "
                    "Generate Short to composite them."
                )
            )

        else:

            self.visual_status_label.setText(
                "Visual assets ready. Generate Short to composite them."
            )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "✓ AI visual assets ready for the next render."
        )


    def visual_slot_clicked(
        self,
        item: QListWidgetItem,
    ):

        slot_index = item.data(
            Qt.ItemDataRole.UserRole
        )
        position = item.data(
            Qt.ItemDataRole.UserRole + 1
        )
        end_position = item.data(
            Qt.ItemDataRole.UserRole + 2
        )

        try:
            slot_index = int(
                slot_index
            )
            position = int(
                position
            )
            end_position = int(
                end_position
            )
        except (
            TypeError,
            ValueError,
        ):
            return

        self.selected_visual_slot_index = slot_index
        self.selected_sfx_clip_id = None

        self.player.setPosition(
            position
        )

        self.timeline.setValue(
            position
        )

        self.timeline.set_selected_visual_range(
            position,
            end_position,
        )

        if (
            0
            <= slot_index
            < len(
                self.visual_plan_slots
            )
        ):
            slot = self.visual_plan_slots[
                slot_index
            ]
            if isinstance(
                slot,
                dict,
            ):
                self.timeline.set_selected_asset_clip(
                    self.visual_clip_id(
                        slot,
                        slot_index,
                    )
                )

        self.reveal_timeline_range(
            position,
            end_position,
        )

        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()
        self.refresh_editor_asset_timeline()


    def transcript_item_clicked(
        self,
        item: QListWidgetItem,
    ):

        position = item.data(
            Qt.ItemDataRole.UserRole
        )

        if position is None:
            return

        try:
            position = int(
                position
            )
        except (
            TypeError,
            ValueError,
        ):
            return

        self.player.setPosition(
            position
        )

        self.timeline.setValue(
            position
        )

        self.reveal_timeline_time(
            position
        )


    def selected_transcript_segment_key(self) -> tuple[int, int] | None:

        item = self.transcript_list.currentItem()

        if item is None:
            return None

        start_ms = item.data(
            Qt.ItemDataRole.UserRole
        )

        end_ms = item.data(
            Qt.ItemDataRole.UserRole + 1
        )

        try:
            return (
                int(start_ms),
                int(end_ms),
            )
        except (TypeError, ValueError):
            return None


    def transcript_segment_for_key(
        self,
        segment_key: tuple[int, int],
    ) -> dict | None:

        start_ms, end_ms = segment_key

        return next(
            (
                segment
                for segment in self.source_transcript_segments
                if (
                    segment["start_ms"] == start_ms
                    and segment["end_ms"] == end_ms
                )
            ),
            None,
        )


    def edit_selected_transcript_segment(self):

        item = self.transcript_list.currentItem()

        if item is None:
            return

        self.edit_transcript_item(
            item
        )


    def edit_transcript_item(
        self,
        item: QListWidgetItem,
    ):

        start_ms = item.data(
            Qt.ItemDataRole.UserRole
        )

        end_ms = item.data(
            Qt.ItemDataRole.UserRole + 1
        )

        try:
            segment_key = (
                int(start_ms),
                int(end_ms),
            )
        except (TypeError, ValueError):
            return

        segment = self.transcript_segment_for_key(
            segment_key
        )

        if segment is None:
            return

        current_text = self.transcript_corrections.get(
            segment_key,
            segment.get(
                "text",
                "",
            ),
        )

        corrected_text, accepted = QInputDialog.getMultiLineText(
            self,
            "Correct Transcript",
            (
                "Edit exactly what should appear in the captions.\n"
                "Timing will stay attached to this spoken segment."
            ),
            current_text,
        )

        if not accepted:
            return

        corrected_text = " ".join(
            corrected_text.split()
        ).strip()

        if not corrected_text:
            return

        original_text = str(
            segment.get(
                "text",
                "",
            )
            or ""
        ).strip()

        if corrected_text == original_text:
            self.transcript_corrections.pop(
                segment_key,
                None,
            )
        else:
            self.transcript_corrections[
                segment_key
            ] = corrected_text

        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()


    def reset_selected_transcript_text(self):

        segment_key = (
            self.selected_transcript_segment_key()
        )

        if segment_key is None:
            return

        self.transcript_corrections.pop(
            segment_key,
            None,
        )

        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()


    def refresh_transcript_timeline_overlays(self):

        self.timeline.set_manual_cut_ranges(
            list(
                self.manual_cut_segments
            )
        )

        self.timeline.set_edited_transcript_ranges(
            list(
                self.transcript_corrections.keys()
            )
        )


    def load_render_timeline_overlays(self):
        """
        Load scene and punch-in decisions from the most recent render and
        display them on the source timeline. Scene positions are exact for
        the selected source range. Motion positions are mapped back using
        the current selection offset.
        """

        selection_offset_ms = int(
            self.start_ms
        )

        scene_positions: list[int] = []
        motion_ranges: list[
            tuple[int, int]
        ] = []
        fx_ranges: list[
            tuple[int, int]
        ] = []
        graphic_ranges: list[
            tuple[int, int]
        ] = []
        caption_impact_ranges: list[
            tuple[int, int]
        ] = []

        scene_plan = (
            ROOT
            / "output"
            / "scene_plan.json"
        )

        if scene_plan.exists():

            try:
                data = json.loads(
                    scene_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            raw_cuts = data.get(
                "cuts",
                [],
            )

            if isinstance(
                raw_cuts,
                list,
            ):

                for value in raw_cuts:

                    try:
                        seconds = float(
                            value
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    scene_positions.append(
                        selection_offset_ms
                        + int(
                            round(
                                seconds
                                * 1000
                            )
                        )
                    )

        motion_plan = (
            ROOT
            / "output"
            / "smart_motion_plan.json"
        )

        if motion_plan.exists():

            try:
                data = json.loads(
                    motion_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            events = data.get(
                "events",
                [],
            )

            if isinstance(
                events,
                list,
            ):

                for event in events:

                    if not isinstance(
                        event,
                        dict,
                    ):
                        continue

                    try:
                        start = float(
                            event.get(
                                "start",
                                0.0,
                            )
                        )

                        end = float(
                            event.get(
                                "end",
                                start,
                            )
                        )

                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    if end <= start:
                        continue

                    motion_ranges.append(
                        (
                            selection_offset_ms
                            + int(
                                round(
                                    start
                                    * 1000
                                )
                            ),
                            selection_offset_ms
                            + int(
                                round(
                                    end
                                    * 1000
                                )
                            ),
                        )
                    )

        visual_fx_plan = (
            ROOT
            / "output"
            / "visual_fx_plan.json"
        )

        if visual_fx_plan.exists():

            try:
                data = json.loads(
                    visual_fx_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            events = data.get(
                "events",
                [],
            )

            if isinstance(
                events,
                list,
            ):

                for event in events:

                    if not isinstance(
                        event,
                        dict,
                    ):
                        continue

                    try:
                        start = float(
                            event.get(
                                "start",
                                0.0,
                            )
                        )
                        end = float(
                            event.get(
                                "end",
                                start,
                            )
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    if end <= start:
                        continue

                    mapped_range = (
                        selection_offset_ms
                        + int(
                            round(
                                start
                                * 1000
                            )
                        ),
                        selection_offset_ms
                        + int(
                            round(
                                end
                                * 1000
                            )
                        ),
                    )

                    if event.get(
                        "type"
                    ) == "graphic":
                        graphic_ranges.append(
                            mapped_range
                        )
                    else:
                        fx_ranges.append(
                            mapped_range
                        )

        visual_edit_plan = (
            ROOT
            / "output"
            / "visual_edit_plan.json"
        )

        if visual_edit_plan.exists():

            try:
                data = json.loads(
                    visual_edit_plan.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                data = {}

            events = data.get(
                "events",
                [],
            )

            if isinstance(
                events,
                list,
            ):

                for event in events:

                    if not isinstance(
                        event,
                        dict,
                    ):
                        continue

                    if event.get(
                        "type"
                    ) != "caption_emphasis":
                        continue

                    try:
                        start = float(
                            event.get(
                                "start",
                                0.0,
                            )
                        )
                        end = float(
                            event.get(
                                "end",
                                start,
                            )
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        continue

                    if end <= start:
                        continue

                    caption_impact_ranges.append(
                        (
                            selection_offset_ms
                            + int(
                                round(
                                    start
                                    * 1000
                                )
                            ),
                            selection_offset_ms
                            + int(
                                round(
                                    end
                                    * 1000
                                )
                            ),
                        )
                    )

        self.timeline.set_scene_cut_positions(
            scene_positions
        )

        self.timeline.set_motion_ranges(
            motion_ranges
        )

        self.timeline.set_fx_ranges(
            fx_ranges
        )

        self.timeline.set_graphic_ranges(
            graphic_ranges
        )

        self.timeline.set_caption_impact_ranges(
            caption_impact_ranges
        )

        self.refresh_transcript_timeline_overlays()


    def save_transcript_corrections(self):

        output_path = (
            ROOT
            / "output"
            / "transcript_corrections.json"
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        corrections = []

        for (
            start_ms,
            end_ms,
        ), corrected_text in sorted(
            self.transcript_corrections.items()
        ):

            segment = self.transcript_segment_for_key(
                (
                    start_ms,
                    end_ms,
                )
            )

            corrections.append(
                {
                    "start": start_ms / 1000,
                    "end": end_ms / 1000,
                    "original_text": (
                        segment.get(
                            "text",
                            "",
                        )
                        if segment
                        else ""
                    ),
                    "corrected_text": corrected_text,
                }
            )

        payload = {
            "source_video": (
                str(self.video_path)
                if self.video_path
                else ""
            ),
            "selection_start": self.start_ms / 1000,
            "selection_end": self.end_ms / 1000,
            "correction_count": len(
                corrections
            ),
            "corrections": corrections,
        }

        try:
            output_path.write_text(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass


    def cut_selected_transcript_segment(self):

        segment_key = (
            self.selected_transcript_segment_key()
        )

        if segment_key is None:
            return

        self.manual_cut_segments.add(
            segment_key
        )

        self.save_manual_edit_plan()
        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()
        self.clear_visual_plan_display()
        self.refresh_transcript_timeline_overlays()


    def restore_selected_transcript_segment(self):

        segment_key = (
            self.selected_transcript_segment_key()
        )

        if segment_key is None:
            return

        self.manual_cut_segments.discard(
            segment_key
        )

        self.save_manual_edit_plan()
        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()
        self.clear_visual_plan_display()
        self.refresh_transcript_timeline_overlays()


    def toggle_transcript_item_cut(
        self,
        item: QListWidgetItem,
    ):

        start_ms = item.data(
            Qt.ItemDataRole.UserRole
        )

        end_ms = item.data(
            Qt.ItemDataRole.UserRole + 1
        )

        try:
            segment_key = (
                int(start_ms),
                int(end_ms),
            )
        except (TypeError, ValueError):
            return

        if segment_key in self.manual_cut_segments:

            self.manual_cut_segments.discard(
                segment_key
            )

        else:

            self.manual_cut_segments.add(
                segment_key
            )

        self.save_manual_edit_plan()
        self.save_transcript_corrections()
        self.update_transcript_panel()
        self.refresh_transcript_timeline_overlays()
        self.clear_visual_plan_display()
        self.clear_visual_plan_display()
        self.refresh_transcript_timeline_overlays()


    def save_manual_edit_plan(self):

        output_path = (
            ROOT
            / "output"
            / "manual_edit_plan.json"
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        cuts = []

        for start_ms, end_ms in sorted(
            self.manual_cut_segments
        ):

            matching_segment = next(
                (
                    segment
                    for segment
                    in self.source_transcript_segments
                    if (
                        segment["start_ms"] == start_ms
                        and segment["end_ms"] == end_ms
                    )
                ),
                None,
            )

            cuts.append(
                {
                    "start": start_ms / 1000,
                    "end": end_ms / 1000,
                    "duration": (
                        end_ms - start_ms
                    ) / 1000,
                    "text": (
                        matching_segment.get("text", "")
                        if matching_segment
                        else ""
                    ),
                    "source": "manual_transcript_cut",
                }
            )

        payload = {
            "source_video": (
                str(self.video_path)
                if self.video_path
                else ""
            ),
            "selection_start": self.start_ms / 1000,
            "selection_end": self.end_ms / 1000,
            "cut_count": len(cuts),
            "time_removed_seconds": round(
                sum(
                    cut["duration"]
                    for cut in cuts
                ),
                3,
            ),
            "cuts": cuts,
        }

        try:

            output_path.write_text(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                ) + "\n",
                encoding="utf-8",
            )

        except OSError:
            pass


    def reset_clip_cards(self):

        if not hasattr(
            self,
            "clip_cards",
        ):
            return

        for index, card in enumerate(
            self.clip_cards
        ):

            card.setText(
                f"AI PICK #{index + 1}\nRun Find Best Clips to populate"
            )

            card.setEnabled(
                False
            )

            card.setVisible(
                False
            )

            card.setProperty(
                "selected",
                False,
            )

            card.style().unpolish(
                card
            )

            card.style().polish(
                card
            )


    def refresh_clip_card_selection(
        self,
        selected_index: int | None,
    ):

        for index, card in enumerate(
            self.clip_cards
        ):

            is_selected = (
                selected_index is not None
                and index == selected_index
            )

            card.setProperty(
                "selected",
                is_selected,
            )

            card.style().unpolish(
                card
            )

            card.style().polish(
                card
            )

            card.update()


    def populate_clip_cards(self):

        for index, card in enumerate(
            self.clip_cards
        ):

            if index >= len(
                self.ai_candidates
            ):

                card.setVisible(
                    False
                )

                continue

            candidate = (
                self.ai_candidates[index]
            )

            rank = int(
                candidate.get(
                    "rank",
                    index + 1,
                )
            )

            start_ms = int(
                candidate.get(
                    "start_ms",
                    0,
                )
            )

            end_ms = int(
                candidate.get(
                    "end_ms",
                    0,
                )
            )

            score = int(
                candidate.get(
                    "score",
                    0,
                )
            )

            hook = str(
                candidate.get(
                    "hook",
                    "",
                )
                or ""
            ).strip()

            reason = str(
                candidate.get(
                    "reason",
                    "",
                )
                or ""
            ).strip()

            description = str(
                candidate.get(
                    "description",
                    "",
                )
                or ""
            ).strip()

            if is_generic_editor_text(
                hook
            ):
                hook = transcript_excerpt(
                    description,
                    max_words=7,
                )

            # Keep cards compact enough to feel like an editor,
            # not a wall of AI-generated text.
            quote = transcript_excerpt(
                description,
                max_words=12,
            )

            grounded_reason = reason
            if is_generic_editor_text(
                grounded_reason
            ):
                grounded_reason = (
                    f'Anchored by "{quote}".'
                    if quote
                    else "Transcript-grounded candidate."
                )

            title = hook or quote or "Transcript moment"

            if len(title) > 54:

                title = title[:51].rstrip() + "..."

            if len(grounded_reason) > 92:
                grounded_reason = grounded_reason[:89].rstrip() + "..."

            duration = max(
                0,
                end_ms
                - start_ms,
            ) / 1000

            headline = (
                f"\"{quote or title}\"\n"
                f"{grounded_reason}"
            )

            card.setText(
                f"AI PICK #{rank}   •   {score}/100\n"
                f"{format_time(start_ms)} → {format_time(end_ms)}\n"
                f"{headline}"
            )

            card.setToolTip(
                (
                    f"AI Pick #{rank}\n"
                    f"Score: {score}/100\n"
                    f"Range: "
                    f"{start_ms / 1000:.2f}s → "
                    f"{end_ms / 1000:.2f}s\n\n"
                    f"Hook: {hook or '—'}\n\n"
                    f"Description: {description or '—'}\n\n"
                    f"Why selected: {reason or '—'}"
                )
            )

            card.setEnabled(
                True
            )

            card.setVisible(
                True
            )

        self.refresh_clip_card_selection(
            0
            if self.ai_candidates
            else None
        )


    def select_ai_card(
        self,
        card_index: int,
    ):

        if (
            card_index < 0
            or card_index >= len(
                self.ai_candidates
            )
        ):
            return

        candidate = (
            self.ai_candidates[
                card_index
            ]
        )

        self.select_ai_suggestion(
            int(
                candidate["rank"]
            ),
            int(
                candidate["start_ms"]
            ),
            int(
                candidate["end_ms"]
            ),
            int(
                candidate["score"]
            ),
        )


    def select_ai_suggestion(
        self,
        rank: int,
        start_ms: int,
        end_ms: int,
        score: int,
    ):

        self.start_ms = start_ms
        self.end_ms = end_ms

        self.timeline.set_selected_suggestion(
            rank - 1
        )

        self.timeline.set_selection_range(
            start_ms,
            end_ms,
        )

        if (
            self.timeline.maximum() > 120000
            and end_ms
            - start_ms
            < self.timeline.maximum()
            * 0.25
        ):
            self.timeline.fit_selection()
        else:
            self.reveal_timeline_range(
                start_ms,
                end_ms,
            )

        self.refresh_clip_card_selection(
            rank - 1
        )

        self.update_selection_label()
        self.clear_visual_plan_display()
        self.selected_sfx_clip_id = None
        self.refresh_editor_asset_timeline()

        if hasattr(
            self,
            "plan_visuals_button",
        ):
            self.plan_visuals_button.setEnabled(
                bool(
                    self.source_transcript_segments
                    and self.end_ms > self.start_ms
                )
            )

        self.player.setPosition(
            start_ms
        )

        self.suggestions_label.setText(
            f"Selected AI clip #{rank}: "
            f"{format_time(start_ms)}–{format_time(end_ms)} "
            f"({score}/100). "
            "Click another purple range to switch."
        )

        self.render_log.append(
            f"Selected AI clip #{rank}: "
            f"{start_ms / 1000:.2f}s → "
            f"{end_ms / 1000:.2f}s "
            f"({score}/100)"
        )


    def choose_music(self):

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Background Music",
            str(ROOT),
            (
                "Audio Files "
                "(*.mp3 *.wav *.m4a *.aac *.flac *.ogg)"
            ),
        )

        if not filename:
            return

        self.music_path = Path(
            filename
        )

        self.music_label.setText(
            self.music_path.name
        )

        self.clear_music_button.setEnabled(
            True
        )

        self.music_button.setText(
            "♫ Change Music"
        )


    def clear_music(self):

        self.music_path = None

        self.music_label.setText(
            "No background music"
        )

        self.clear_music_button.setEnabled(
            False
        )

        self.music_button.setText(
            "♫ Add Music"
        )


    def music_volume_changed(
        self,
        value: int,
    ):

        self.music_volume = value

        self.music_volume_label.setText(
            f"{value}%"
        )


    def preview_volume_changed(
        self,
        value: int,
    ):

        self.preview_volume = max(
            0,
            min(
                100,
                int(
                    value
                ),
            ),
        )

        self.audio_output.setVolume(
            self.preview_volume
            / 100
        )
        if hasattr(
            self,
            "sfx_preview_audio",
        ):
            self.sfx_preview_audio.setVolume(
                self.preview_volume
                / 100
            )

        self.preview_volume_label.setText(
            f"{self.preview_volume}%"
        )

        self.settings.setValue(
            "preview/volume",
            self.preview_volume,
        )


    def format_progress_duration(
        self,
        seconds: float,
    ) -> str:

        seconds = max(
            0,
            int(
                round(
                    seconds
                )
            ),
        )
        minutes = seconds // 60
        remaining = seconds % 60

        return f"{minutes:02d}:{remaining:02d}"


    def estimate_render_seconds(
        self,
        clip_duration_seconds: float,
        has_music: bool,
        has_reframe: bool,
    ) -> float:

        duration = max(
            1.0,
            float(
                clip_duration_seconds
                or 1.0
            ),
        )
        energy = self.current_edit_energy()
        multiplier = {
            "LOW": 2.2,
            "PUNCHY": 2.85,
            "MAXIMUM": 3.55,
        }.get(
            energy,
            2.85,
        )

        estimate = 30.0 + duration * multiplier

        if has_reframe:
            estimate += max(
                10.0,
                duration * 0.55,
            )

        if has_music:
            estimate += max(
                12.0,
                duration * 0.55,
            )

        return max(
            45.0,
            estimate,
        )


    def start_render_progress(
        self,
        clip_duration_seconds: float,
        has_music: bool,
        has_reframe: bool,
    ):

        self.render_progress_active = True
        self.render_progress_started_at = time.monotonic()
        self.render_progress_stage_started_at = (
            self.render_progress_started_at
        )
        self.render_progress_estimate_seconds = (
            self.estimate_render_seconds(
                clip_duration_seconds,
                has_music,
                has_reframe,
            )
        )
        self.render_progress_last_value = 0
        self.render_progress_bar.setValue(
            0
        )
        self.render_progress_time_label.setText(
            (
                "Estimated "
                + self.format_progress_duration(
                    self.render_progress_estimate_seconds
                )
            )
        )
        self.set_render_progress_stage(
            "framing"
            if has_reframe
            else "rendering"
        )
        self.render_progress_timer.start()


    def set_render_progress_stage(
        self,
        stage: str,
    ):

        if not hasattr(
            self,
            "render_progress_bar",
        ):
            return

        self.render_progress_stage = stage
        self.render_progress_stage_started_at = time.monotonic()

        stage_map = {
            "framing": (
                "FRAMING",
                0,
                15,
                0.18,
            ),
            "rendering": (
                "RENDERING",
                15,
                92
                if self.music_path
                else 98,
                0.72,
            ),
            "music": (
                "MUSIC MIX",
                92,
                99,
                0.16,
            ),
            "complete": (
                "COMPLETE",
                100,
                100,
                0.01,
            ),
            "failed": (
                "FAILED",
                self.render_progress_last_value,
                self.render_progress_last_value,
                0.01,
            ),
            "idle": (
                "WAITING",
                0,
                100,
                1.0,
            ),
        }

        label, floor, ceiling, ratio = stage_map.get(
            stage,
            stage_map[
                "idle"
            ],
        )

        self.render_progress_floor = int(
            floor
        )
        self.render_progress_ceiling = int(
            ceiling
        )
        self.render_progress_stage_estimate_seconds = max(
            1.0,
            self.render_progress_estimate_seconds
            * float(
                ratio
            ),
        )

        self.render_progress_stage_label.setText(
            label
        )
        self.update_render_progress()


    def update_render_progress(self):

        if not hasattr(
            self,
            "render_progress_bar",
        ):
            return

        if not self.render_progress_active:
            return

        now = time.monotonic()
        elapsed = max(
            0.0,
            now
            - self.render_progress_started_at,
        )
        remaining = max(
            0.0,
            self.render_progress_estimate_seconds
            - elapsed,
        )

        if self.render_progress_stage in {
            "complete",
            "failed",
        }:
            return

        stage_elapsed = max(
            0.0,
            now
            - self.render_progress_stage_started_at,
        )
        stage_fraction = min(
            0.96,
            stage_elapsed
            / max(
                1.0,
                self.render_progress_stage_estimate_seconds,
            ),
        )
        value = int(
            round(
                self.render_progress_floor
                + (
                    self.render_progress_ceiling
                    - self.render_progress_floor
                )
                * stage_fraction
            )
        )
        value = max(
            self.render_progress_last_value,
            min(
                self.render_progress_ceiling,
                value,
            ),
        )
        self.render_progress_last_value = value
        self.render_progress_bar.setValue(
            value
        )
        self.render_progress_time_label.setText(
            (
                f"Elapsed {self.format_progress_duration(elapsed)}"
                "  /  "
                f"ETA {self.format_progress_duration(remaining)}"
            )
        )


    def finish_render_progress(
        self,
        success: bool,
    ):

        if not hasattr(
            self,
            "render_progress_bar",
        ):
            return

        self.render_progress_timer.stop()
        elapsed = (
            time.monotonic()
            - self.render_progress_started_at
            if self.render_progress_started_at
            else 0.0
        )
        self.render_progress_active = False

        if success:
            self.render_progress_last_value = 100
            self.render_progress_bar.setValue(
                100
            )
            self.render_progress_stage_label.setText(
                "COMPLETE"
            )
            self.render_progress_time_label.setText(
                (
                    "Finished in "
                    + self.format_progress_duration(
                        elapsed
                    )
                )
            )
        else:
            self.render_progress_stage_label.setText(
                "FAILED"
            )
            self.render_progress_time_label.setText(
                (
                    "Stopped after "
                    + self.format_progress_duration(
                        elapsed
                    )
                )
            )


    def append_music_log(
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

        scrollbar = (
            self.render_log.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )


    def read_music_output(self):

        data = (
            self.music_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_music_log(
            data
        )


    def read_music_error(self):

        data = (
            self.music_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_music_log(
            data
        )


    def start_music_mix(self):

        if not self.music_path:
            self.finish_short_success()
            return

        final_video = (
            ROOT
            / "output"
            / "rendered"
            / "short1_captioned.mp4"
        )

        music_script = (
            ROOT
            / "app"
            / "music_overlay.py"
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "=== FINAL AUDIO: Mixing background music ==="
        )

        self.render_log.append(
            f"Music: {self.music_path.name}"
        )

        self.render_log.append(
            f"Music volume: {self.music_volume}%"
        )

        self.generate_button.setText(
            "Adding Music..."
        )

        self.set_render_progress_stage(
            "music"
        )

        self.music_process.start(
            sys.executable,
            [
                str(music_script),

                "--video",
                str(final_video),

                "--music",
                str(self.music_path),

                "--volume",
                str(
                    self.music_volume / 100
                ),
            ],
        )


    def music_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        if exit_code == 0:

            self.finish_short_success()

            return

        self.generate_button.setEnabled(
            True
        )

        self.generate_button.setText(
            "Generate Again"
        )

        self.find_clips_button.setEnabled(
            self.video_path is not None
        )

        self.music_button.setEnabled(
            True
        )

        self.finish_render_progress(
            False
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "⚠ SHORT RENDERED, BUT MUSIC MIX FAILED"
        )

        self.render_log.append(
            f"Music process exit code: {exit_code}"
        )


    def finish_short_success(self):

        self.generate_button.setEnabled(
            True
        )

        self.generate_button.setText(
            "Generate Again"
        )

        self.find_clips_button.setEnabled(
            self.video_path is not None
        )

        self.music_button.setEnabled(
            True
        )

        self.finish_render_progress(
            True
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "✓ SHORT COMPLETE"
        )

        final_video = (
            ROOT
            / "output"
            / "rendered"
            / "short1_captioned.mp4"
        )

        self.render_log.append(
            f"Final video: {final_video}"
        )

        self.load_render_timeline_overlays()
        self.open_final_video(final_video)


    def open_final_video(
        self,
        final_video: Path,
    ):

        if not final_video.exists():
            self.render_log.append(
                "Could not auto-open final video because the file was not found."
            )
            return

        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                str(
                    final_video
                )
            )
        )

        if opened:
            self.render_log.append(
                "Opened final video in the default video player."
            )
        else:
            self.render_log.append(
                "Could not auto-open final video."
            )


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
            if str(
                clip.get(
                    "kind",
                    "",
                )
                or ""
            ).upper() not in {
                "SFX",
                "AI_VISUAL",
            }:
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

            selected_asset_id = self.selected_sfx_clip_id
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
        if hasattr(self, "ai_visual_preview_overlay"):
            self.update_ai_visual_preview_overlay(
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

        if normalized_kind != "SFX":
            return

        self.selected_visual_slot_index = None
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

        if normalized_kind != "SFX":
            return

        self.selected_sfx_clip_id = str(
            clip_id
            or ""
        )
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

        sfx_dir = ROOT / "assets" / "sfx"
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

        sfx_dir = ROOT / "assets" / "sfx"
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


    def repolish_ai_visual_preview(self, widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()


    def layout_ai_visual_preview_overlay(
        self,
        clip: dict,
    ):
        if not hasattr(self, "ai_visual_preview_overlay"):
            return

        width = max(1, self.video_widget.width())
        height = max(1, self.video_widget.height())
        mode = self.ai_visual_preview_display_mode(clip)
        scale = self.ai_visual_preview_scale(clip)

        # The exported Short is always a 9:16 canvas. The preview widget is
        # usually much wider because it shows the uncropped source, so sizing
        # an overlay from the whole widget makes a card look like a banner.
        # Build a centered virtual 9:16 output canvas and size visual overlays
        # from that instead. This keeps preview geometry representative of the
        # final 1080x1920 render.
        canvas_height = height
        canvas_width = max(1, int(round(canvas_height * 9 / 16)))
        if canvas_width > width:
            canvas_width = width
            canvas_height = max(1, int(round(canvas_width * 16 / 9)))
        canvas_x = max(0, (width - canvas_width) // 2)
        canvas_y = max(0, (height - canvas_height) // 2)

        layout_signature = (
            self.active_visual_preview_signature,
            width,
            height,
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
        )
        if layout_signature == self.active_visual_preview_layout_signature:
            return
        self.active_visual_preview_layout_signature = layout_signature

        self.ai_visual_preview_dim.setGeometry(
            canvas_x,
            canvas_y,
            canvas_width,
            canvas_height,
        )
        if self.ai_visual_preview_dim.property("displayMode") != mode:
            self.ai_visual_preview_dim.setProperty("displayMode", mode)
            self.repolish_ai_visual_preview(self.ai_visual_preview_dim)

        overlay = self.ai_visual_preview_overlay
        if overlay.property("displayMode") != mode:
            overlay.setProperty("displayMode", mode)
            self.repolish_ai_visual_preview(overlay)

        source_pixmap = self.active_visual_preview_pixmap

        if mode == "OVERLAY_CARD":
            # Mirror apply_ai_visuals.py: 842x882 on a 1080x1920 output.
            # Using the same proportions here prevents the live preview card
            # from becoming an ultra-wide banner on a landscape source.
            card_width = max(1, int(round(canvas_width * (842 / 1080) * scale)))
            card_height = max(1, int(round(canvas_height * (882 / 1920) * scale)))
            card_width = min(canvas_width, card_width)
            card_height = min(canvas_height, card_height)
            x = canvas_x + max(0, (canvas_width - card_width) // 2)
            y_offset = max(
                int(round(canvas_height * (110 / 1920))),
                int(round((canvas_height - card_height) * 0.22)),
            )
            y = canvas_y + min(
                max(0, y_offset),
                max(0, canvas_height - card_height),
            )
            overlay.setGeometry(x, y, card_width, card_height)
            # Overlay cards behave like cropped cutaways rather than
            # letterboxed images with blank side bands.
            transform = Qt.AspectRatioMode.KeepAspectRatioByExpanding
            target_size = overlay.size()
        elif mode == "FULL_FRAME_CONTAIN":
            overlay.setGeometry(
                canvas_x,
                canvas_y,
                canvas_width,
                canvas_height,
            )
            transform = Qt.AspectRatioMode.KeepAspectRatio
            target_size = QSize(
                max(1, int(round(canvas_width * scale))),
                max(1, int(round(canvas_height * scale))),
            )
        else:
            overlay.setGeometry(
                canvas_x,
                canvas_y,
                canvas_width,
                canvas_height,
            )
            transform = Qt.AspectRatioMode.KeepAspectRatioByExpanding
            target_size = overlay.size()

        if not source_pixmap.isNull():
            overlay.setText("")
            preview_pixmap = source_pixmap.scaled(
                target_size,
                transform,
                Qt.TransformationMode.SmoothTransformation,
            )
            if transform == Qt.AspectRatioMode.KeepAspectRatioByExpanding:
                # QPixmap.scaled(...ByExpanding) may be larger than the label.
                # Crop it explicitly so the live preview matches a real
                # crop-to-fill render rather than showing blank side bands.
                crop_width = min(target_size.width(), preview_pixmap.width())
                crop_height = min(target_size.height(), preview_pixmap.height())
                crop_x = max(0, (preview_pixmap.width() - crop_width) // 2)
                crop_y = max(0, (preview_pixmap.height() - crop_height) // 2)
                preview_pixmap = preview_pixmap.copy(
                    crop_x,
                    crop_y,
                    crop_width,
                    crop_height,
                )
            overlay.setPixmap(preview_pixmap)

        overlay.raise_()
        if mode in {"OVERLAY_CARD", "FULL_FRAME_COVER"}:
            # Do not place a full-frame dim label over an overlay card. On
            # QVideoWidget that can obscure the native video surface, making
            # the cutaway look like it is floating on black instead of over
            # the source clip.
            self.ai_visual_preview_dim.hide()
        else:
            self.ai_visual_preview_dim.show()
            self.ai_visual_preview_dim.raise_()
            overlay.raise_()


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

        signature = (
            clip_id,
            str(asset_path or ""),
            asset_stamp,
            mode,
            round(scale, 3),
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

        sfx_dir = ROOT / "assets" / "sfx"
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


    def generate_short(self):

        if not self.video_path:
            return

        if self.end_ms <= self.start_ms:
            self.render_log.append(
                "ERROR: Please select a valid start and end point."
            )
            return

        start_seconds = (
            self.start_ms
            / 1000
        )

        end_seconds = (
            self.end_ms
            / 1000
        )

        duration_seconds = (
            end_seconds
            - start_seconds
        )

        self.pending_original_start_seconds = (
            start_seconds
        )

        self.pending_original_end_seconds = (
            end_seconds
        )

        self.pending_render_duration_seconds = (
            duration_seconds
        )

        reframed_source = (
            ROOT
            / "output"
            / "rendered"
            / "reframed_source.mp4"
        )

        self.pending_render_source = (
            reframed_source
        )

        reframe_script = (
            ROOT
            / "app"
            / "smart_reframe.py"
        )

        self.start_render_progress(
            duration_seconds,
            bool(
                self.music_path
            ),
            reframe_script.exists(),
        )

        self.render_log.clear()

        self.render_log.append(
            "Starting ShortsFactory..."
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            f"Source: {self.video_path.name}"
        )

        self.save_render_settings()

        self.render_log.append(
            f"Edit energy: {self.current_edit_energy()}"
        )

        self.render_log.append(
            (
                "Selection: "
                f"{start_seconds:.2f}s "
                "→ "
                f"{end_seconds:.2f}s"
            )
        )

        self.render_log.append(
            ""
        )

        self.generate_button.setEnabled(
            False
        )

        self.generate_button.setText(
            "Framing..."
        )

        self.find_clips_button.setEnabled(
            False
        )

        self.music_button.setEnabled(
            False
        )

        if self.music_path:

            self.render_log.append(
                (
                    "Background music queued: "
                    f"{self.music_path.name} "
                    f"at {self.music_volume}%"
                )
            )

            self.render_log.append(
                ""
            )

        # If the subject-aware framing helper is missing for any reason,
        # fall back to the existing renderer rather than blocking output.
        if not reframe_script.exists():

            self.render_log.append(
                (
                    "Smart reframe script not installed; "
                    "using existing center crop."
                )
            )

            self.render_log.append(
                ""
            )

            self.start_main_render(
                self.video_path,
                start_seconds,
                end_seconds,
            )

            return

        arguments = [
            str(
                reframe_script
            ),
            "--source",
            str(
                self.video_path
            ),
            "--start",
            f"{start_seconds:.3f}",
            "--end",
            f"{end_seconds:.3f}",
            "--output",
            str(
                reframed_source
            ),
        ]

        self.render_log.append(
            "=== PRE-RENDER: SUBJECT-AWARE 9:16 FRAMING ==="
        )

        self.render_log.append(
            ""
        )

        self.reframe_process.start(
            sys.executable,
            arguments,
        )


    def start_main_render(
        self,
        source_path: Path,
        start_seconds: float,
        end_seconds: float,
    ):

        render_script = (
            ROOT
            / "app"
            / "render.py"
        )

        arguments = [
            str(
                render_script
            ),
            "--source",
            str(
                source_path
            ),
            "--start",
            f"{start_seconds:.3f}",
            "--end",
            f"{end_seconds:.3f}",
        ]

        self.generate_button.setText(
            "Rendering..."
        )

        self.set_render_progress_stage(
            "rendering"
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "=== MAIN SHORTSFACTORY RENDER ==="
        )

        self.render_log.append(
            ""
        )

        self.render_process.start(
            sys.executable,
            arguments,
        )


    def read_reframe_output(self):

        data = (
            self.reframe_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        if data:

            self.render_log.moveCursor(
                self.render_log
                .textCursor()
                .MoveOperation
                .End
            )

            self.render_log.insertPlainText(
                data
            )

            scrollbar = (
                self.render_log
                .verticalScrollBar()
            )

            scrollbar.setValue(
                scrollbar.maximum()
            )


    def read_reframe_error(self):

        data = (
            self.reframe_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        if data:

            self.render_log.moveCursor(
                self.render_log
                .textCursor()
                .MoveOperation
                .End
            )

            self.render_log.insertPlainText(
                data
            )

            scrollbar = (
                self.render_log
                .verticalScrollBar()
            )

            scrollbar.setValue(
                scrollbar.maximum()
            )


    def reframe_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        duration = max(
            0.01,
            self.pending_render_duration_seconds,
        )

        if (
            exit_code == 0
            and self.pending_render_source is not None
            and self.pending_render_source.exists()
        ):

            self.render_log.append(
                ""
            )

            self.render_log.append(
                "✓ Subject-aware framing stage complete."
            )

            # The temporary source is already the exact selected clip
            # and already 1080x1920. Feed it to render.py from t=0.
            self.start_main_render(
                self.pending_render_source,
                0.0,
                duration,
            )

            return

        self.render_log.append(
            ""
        )

        self.render_log.append(
            (
                "WARNING: Smart reframe failed. "
                "Falling back to the existing center crop."
            )
        )

        self.start_main_render(
            self.video_path,
            self.pending_original_start_seconds,
            self.pending_original_end_seconds,
        )


    def read_render_output(self):

        data = (
            self.render_process
            .readAllStandardOutput()
            .data()
            .decode("utf-8", errors="replace")
        )

        if data:
            self.render_log.moveCursor(
                self.render_log.textCursor().MoveOperation.End
            )
            self.render_log.insertPlainText(data)
            scrollbar = self.render_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def read_render_error(self):

        data = (
            self.render_process
            .readAllStandardError()
            .data()
            .decode("utf-8", errors="replace")
        )

        if data:
            self.render_log.moveCursor(
                self.render_log.textCursor().MoveOperation.End
            )
            self.render_log.insertPlainText(data)
            scrollbar = self.render_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def render_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        if exit_code == 0:

            if self.music_path:

                self.start_music_mix()

            else:

                self.finish_short_success()

            return

        self.generate_button.setEnabled(
            True
        )

        self.generate_button.setText(
            "Generate Short"
        )

        self.find_clips_button.setEnabled(
            self.video_path is not None
        )

        self.music_button.setEnabled(
            True
        )

        self.finish_render_progress(
            False
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "✕ RENDER FAILED"
        )

        self.render_log.append(
            f"Exit code: {exit_code}"
        )


    def append_analysis_log(
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

        scrollbar = (
            self.render_log.verticalScrollBar()
        )

        scrollbar.setValue(
            scrollbar.maximum()
        )


    def read_analysis_output(self):

        data = (
            self.analysis_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_analysis_log(
            data
        )


    def read_analysis_error(self):

        data = (
            self.analysis_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        self.append_analysis_log(
            data
        )


    def find_best_clips(self):

        if not self.video_path:
            return

        if (
            self.analysis_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        self.render_log.clear()

        self.render_log.append(
            "Finding strong Short candidates..."
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            f"Source: {self.video_path.name}"
        )

        transcription_quality = self.current_transcription_quality()

        self.render_log.append(
            f"Transcription quality: {transcription_quality}"
        )

        self.render_log.append(
            "Stage 1/2: Transcribing the full source video..."
        )

        self.find_clips_button.setEnabled(
            False
        )

        self.find_clips_button.setText(
            "Analyzing..."
        )

        self.generate_button.setEnabled(
            False
        )

        self.timeline.clear_suggestions()

        self.suggestions_label.setText(
            "Analyzing the source video..."
        )

        subtitles_script = (
            ROOT
            / "app"
            / "subtitles.py"
        )

        self.analysis_stage = (
            "transcribe"
        )

        self.analysis_process.start(
            sys.executable,
            [
                str(subtitles_script),
                "--quality",
                transcription_quality,
                str(self.video_path),
            ],
        )


    def start_clip_analyzer(self):

        if not self.video_path:
            return

        analyzer_script = (
            ROOT
            / "app"
            / "analyze.py"
        )

        transcript_path = (
            ROOT
            / "output"
            / "subtitles.json"
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            "Stage 2/2: Ranking the strongest clip windows with local AI..."
        )

        self.analysis_stage = (
            "analyze"
        )

        source_duration_ms = max(
            0,
            self.player.duration(),
        )

        target_clip_count = (
            6
            if source_duration_ms >= 10 * 60 * 1000
            else 3
        )

        self.render_log.append(
            f"Requesting {target_clip_count} clip candidates "
            f"for a {source_duration_ms / 60000:.1f}-minute source."
        )

        self.analysis_process.start(
            sys.executable,
            [
                str(analyzer_script),

                "--video",
                str(self.video_path),

                "--transcript",
                str(transcript_path),

                "--clip-discovery-only",

                "--max-clips",
                str(target_clip_count),
            ],
        )


    def load_clip_suggestions(self):

        analysis_path = (
            ROOT
            / "output"
            / "analysis.json"
        )

        if not analysis_path.exists():

            raise FileNotFoundError(
                f"Analysis file not found: {analysis_path}"
            )

        with analysis_path.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        analysis = data.get(
            "analysis",
            data,
        )

        self.load_source_transcript()

        candidates = analysis.get(
            "candidate_clips",
            [],
        )

        suggestions = []
        label_parts = []

        self.ai_candidates = []

        for rank, candidate in enumerate(
            candidates[:6],
            start=1,
        ):

            start_seconds = timestamp_to_seconds(
                candidate.get(
                    "start_timestamp",
                    "",
                )
            )

            end_seconds = timestamp_to_seconds(
                candidate.get(
                    "end_timestamp",
                    "",
                )
            )

            if (
                start_seconds is None
                or end_seconds is None
                or end_seconds <= start_seconds
            ):
                continue

            score = int(
                candidate.get(
                    "score",
                    0,
                )
                or 0
            )

            start_ms = int(
                round(
                    start_seconds * 1000
                )
            )

            end_ms = int(
                round(
                    end_seconds * 1000
                )
            )

            suggestions.append(
                (
                    start_ms,
                    end_ms,
                    score,
                )
            )

            self.ai_candidates.append(
                {
                    "rank": rank,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "score": score,
                    "hook": str(
                        candidate.get(
                            "hook",
                            "",
                        )
                        or ""
                    ).strip(),
                    "description": str(
                        candidate.get(
                            "description",
                            "",
                        )
                        or ""
                    ).strip(),
                    "reason": str(
                        candidate.get(
                            "reason",
                            "",
                        )
                        or ""
                    ).strip(),
                }
            )

            label_parts.append(
                f"#{rank} "
                f"{format_time(start_ms)}–{format_time(end_ms)} "
                f"({score}/100)"
            )

        if not suggestions:

            self.timeline.clear_suggestions()

            self.ai_candidates = []

            self.reset_clip_cards()

            self.suggestions_label.setText(
                "AI analysis completed, but no usable clip ranges were returned."
            )

            return

        self.timeline.set_suggestions(
            suggestions
        )

        self.timeline.set_selected_suggestion(
            0
        )

        self.populate_clip_cards()

        self.suggestions_label.setText(
            "AI suggestions — click a purple range or a card: "
            + "   •   ".join(label_parts)
        )

        # Automatically select the highest-ranked suggestion while still
        # showing all candidate ranges on the timeline.
        best_start, best_end, best_score = suggestions[0]

        self.start_ms = best_start
        self.end_ms = best_end

        self.timeline.set_selection_range(
            best_start,
            best_end,
        )

        if (
            self.timeline.maximum() > 120000
            and best_end
            - best_start
            < self.timeline.maximum()
            * 0.25
        ):
            self.timeline.fit_selection()

        self.update_selection_label()
        self.selected_sfx_clip_id = None
        self.refresh_editor_asset_timeline()

        self.player.setPosition(
            best_start
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            f"✓ Found {len(suggestions)} strong clip candidates."
        )

        self.render_log.append(
            "The highest-ranked range has been selected automatically. "
            "Click any purple range to switch candidates."
        )


    def analysis_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        if exit_code != 0:

            self.analysis_stage = None

            self.find_clips_button.setEnabled(
                self.video_path is not None
            )

            self.find_clips_button.setText(
                "✦ Find Best Clips"
            )

            self.generate_button.setEnabled(
                self.video_path is not None
            )

            self.suggestions_label.setText(
                "AI clip discovery failed. See the render log below."
            )

            self.render_log.append(
                ""
            )

            self.render_log.append(
                f"✕ CLIP DISCOVERY FAILED (exit code {exit_code})"
            )

            return

        if self.analysis_stage == "transcribe":

            self.start_clip_analyzer()

            return

        if self.analysis_stage == "analyze":

            self.analysis_stage = None

            try:

                self.load_clip_suggestions()

            except Exception as exc:

                self.render_log.append(
                    ""
                )

                self.render_log.append(
                    f"Could not display clip suggestions: {exc}"
                )

            self.find_clips_button.setEnabled(
                self.video_path is not None
            )

            self.find_clips_button.setText(
                "✦ Refresh Clips"
            )

            self.generate_button.setEnabled(
                self.video_path is not None
            )

    def keyPressEvent(
        self,
        event,
    ):

        if self.handle_editor_shortcut(
            event
        ):
            return

        super().keyPressEvent(
            event
        )

    def eventFilter(
        self,
        watched,
        event,
    ):

        if (
            watched is getattr(self, "video_widget", None)
            and event.type() == QEvent.Type.Resize
            and hasattr(self, "ai_visual_preview_overlay")
        ):
            QTimer.singleShot(
                0,
                lambda: self.update_ai_visual_preview_overlay(
                    self.player.position()
                ),
            )

        if (
            event.type()
            == QEvent.Type.KeyPress
            and self.handle_editor_shortcut(
                event,
                watched,
            )
        ):
            return True

        return super().eventFilter(
            watched,
            event,
        )

    def text_editor_has_focus(
        self,
        source_widget=None,
    ) -> bool:

        candidates = [
            source_widget,
            QApplication.focusWidget(),
        ]

        for widget in candidates:
            while widget is not None:
                if isinstance(
                    widget,
                    (
                        QLineEdit,
                        QPlainTextEdit,
                        QTextEdit,
                    ),
                ):
                    if hasattr(
                        widget,
                        "isReadOnly",
                    ) and widget.isReadOnly():
                        return False
                    return True

                if not hasattr(
                    widget,
                    "parentWidget",
                ):
                    break

                widget = widget.parentWidget()

        return False

    def handle_editor_shortcut(
        self,
        event,
        source_widget=None,
    ) -> bool:

        if self.text_editor_has_focus(
            source_widget
        ):
            return False

        key = event.key()
        modifiers = event.modifiers()

        if (
            QApplication.activeModalWidget() is None
            and key == Qt.Key.Key_Backspace
            and modifiers == Qt.KeyboardModifier.NoModifier
            and self.selected_sfx_clip() is not None
        ):
            self.delete_selected_sfx_clip()
            event.accept()
            return True

        if (
            key == Qt.Key.Key_Space
            and modifiers == Qt.KeyboardModifier.NoModifier
        ):
            if not event.isAutoRepeat():
                self.toggle_playback()
            event.accept()
            return True

        if (
            key == Qt.Key.Key_F
            and modifiers == Qt.KeyboardModifier.NoModifier
        ):
            self.fit_timeline_selection()
            event.accept()
            return True

        if (
            key == Qt.Key.Key_0
            and modifiers
            & Qt.KeyboardModifier.ControlModifier
        ):
            self.fit_timeline_source()
            event.accept()
            return True

        return False

    def restore_layout_settings(self):

        for key, splitter_name in (
            ("main_splitter", "main_splitter"),
            ("right_splitter", "right_splitter"),
            ("preview_timeline_splitter", "preview_timeline_splitter"),
        ):
            splitter = getattr(
                self,
                splitter_name,
                None,
            )
            state = self.settings.value(
                f"layout/{key}"
            )
            if splitter is not None and state:
                splitter.restoreState(
                    state
                )

    def save_layout_settings(self):

        for key, splitter_name in (
            ("main_splitter", "main_splitter"),
            ("right_splitter", "right_splitter"),
            ("preview_timeline_splitter", "preview_timeline_splitter"),
        ):
            splitter = getattr(
                self,
                splitter_name,
                None,
            )
            if splitter is not None:
                self.settings.setValue(
                    f"layout/{key}",
                    splitter.saveState(),
                )

    def closeEvent(
        self,
        event,
    ):

        self.save_layout_settings()
        super().closeEvent(
            event
        )


    def apply_style(self):

        self.setStyleSheet(
            """
            QMainWindow {
                background: #09090A;
            }

            QWidget {
                color: #DED6C8;
                font-family: Segoe UI;
                font-size: 13px;
            }

            QFrame#HeaderPanel {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #101012, stop:0.58 #17171A, stop:1 #210D12);
                border: 1px solid #3B2227;
                border-left: 4px solid #741C28;
                border-radius: 5px;
            }

            QFrame#Panel, QFrame#PreviewPanel, QFrame#SubPanel {
                background: #101012;
                border: 1px solid #252429;
                border-radius: 5px;
            }

            QFrame#PreviewPanel {
                border: 1px solid #40333A;
                border-top: 2px solid #733B2D;
            }

            QFrame#SubPanel {
                background: #0A0A0B;
                border: 1px solid #252429;
                border-radius: 4px;
            }

            QSplitter::handle {
                background: #09090A;
                border: 1px solid #252429;
            }

            QSplitter::handle:horizontal {
                width: 8px;
            }

            QSplitter::handle:vertical {
                height: 8px;
            }

            QSplitter::handle:hover {
                background: #741C28;
                border: 1px solid #C9384F;
            }

            QScrollArea#PanelScroll {
                background: transparent;
                border: none;
            }

            QScrollArea#CenterScroll {
                background: transparent;
                border: none;
            }

            QLabel#AppTitle {
                font-size: 31px;
                font-weight: 900;
                letter-spacing: 0px;
                color: #DED6C8;
            }

            QLabel#AppSubtitle {
                color: #C9384F;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 2px;
            }

            QLabel#ModeBadge, QLabel#MicroBadge {
                color: #DED6C8;
                background: #160B0E;
                border: 1px solid #741C28;
                border-radius: 3px;
                padding: 6px 10px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1px;
            }

            QLabel#SectionTitle {
                color: #B8AEA1;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 2px;
                text-transform: uppercase;
            }

            QLabel#HintLabel {
                color: #918B84;
                font-size: 11px;
                line-height: 1.4;
            }

            QLabel#TinyLabel {
                color: #7E7670;
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QFrame#DropZone {
                background: #0A0A0B;
                border: 1px dashed #5A3433;
                border-radius: 5px;
            }

            QFrame#DropZone:hover {
                background: #121418;
                border: 1px dashed #d04b5f;
            }

            QLabel#DropIcon {
                font-size: 44px;
                color: #d04b5f;
            }

            QLabel#DropTitle {
                font-size: 18px;
                font-weight: 800;
                color: #f2eee8;
            }

            QLabel#DropSubtitle {
                color: #7b7370;
                font-size: 11px;
                letter-spacing: 1px;
            }

            QLabel#FileLabel, QLabel#MusicLabel {
                color: #D2C8BA;
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 9px 11px;
            }

            QLabel#SelectionLabel {
                color: #f4f0ea;
                font-weight: 700;
                padding-left: 4px;
            }

            QLabel#SuggestionLabel {
                color: #9a8f88;
                font-size: 11px;
                padding: 0px 2px 0px 2px;
            }

            QLabel#TrimHelp {
                color: #756d68;
                font-size: 10px;
                font-family: Consolas;
                letter-spacing: 1px;
            }

            QLabel#TranscriptStatus, QLabel#MusicVolumeLabel, QLabel#TimeLabel {
                color: #968b86;
                font-size: 11px;
            }

            QListWidget#TranscriptList {
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 6px;
                color: #D3CBBF;
                outline: none;
                font-size: 12px;
            }

            QListWidget#TranscriptList::item {
                border-radius: 3px;
                padding: 8px 10px;
                margin: 2px 0px;
            }

            QListWidget#TranscriptList::item:hover {
                background: #181216;
                color: #ffffff;
            }

            QListWidget#TranscriptList::item:selected {
                background: #241016;
                border: 1px solid #C9384F;
                color: #FFF3E3;
            }

            QScrollBar:vertical, QScrollBar:horizontal {
                background: #09090A;
                border: 1px solid #1C1B1F;
                margin: 0px;
            }

            QScrollBar:vertical {
                width: 10px;
            }

            QScrollBar:horizontal {
                height: 10px;
            }

            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #3A3637;
                border: 1px solid #5A3433;
                border-radius: 2px;
                min-height: 24px;
                min-width: 24px;
            }

            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background: #741C28;
                border: 1px solid #C9384F;
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                width: 0px;
                height: 0px;
            }

            QScrollBar::add-page, QScrollBar::sub-page {
                background: transparent;
            }

            QLabel#ImageAIStatus {
                color: #8d8580;
                background: #0b0d0f;
                border: 1px solid #242226;
                border-radius: 8px;
                padding: 7px 9px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QLabel#ImageAIStatus[state="ready"] {
                color: #bdf8d2;
                border: 1px solid #315c40;
                background: #0b1610;
            }

            QLabel#ImageAIStatus[state="generating"] {
                color: #e5d49a;
                border: 1px solid #5d4f28;
                background: #171408;
            }

            QLabel#ImageAIStatus[state="offline"],
            QLabel#ImageAIStatus[state="error"] {
                color: #ffbac4;
                border: 1px solid #64303a;
                background: #1a0e12;
            }

            QLabel#ImageAIStatus[state="connected_no_model"] {
                color: #d4c5a7;
                border: 1px solid #5a4930;
                background: #17120d;
            }

            QComboBox#CompactCombo, QLineEdit#CompactLineEdit {
                color: #ded7cf;
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 3px;
                padding: 6px 8px;
                min-height: 24px;
            }

            QComboBox#CompactCombo:disabled,
            QLineEdit#CompactLineEdit:disabled {
                color: #625b58;
                background: #101114;
                border: 1px solid #201d20;
            }

            QTextEdit#PromptEdit {
                font-family: Segoe UI;
                font-size: 11px;
                color: #d8d0c8;
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 3px;
                padding: 8px;
            }

            QInputDialog QTextEdit,
            QInputDialog QPlainTextEdit,
            QInputDialog QLineEdit {
                color: #000000;
                background: #FFFFFF;
                selection-color: #000000;
                selection-background-color: #B8D7FF;
            }

            QInputDialog QComboBox,
            QInputDialog QListView,
            QInputDialog QAbstractItemView {
                color: #F2ECE4;
                background: #09090A;
                border: 1px solid #5D252E;
                selection-color: #FFFFFF;
                selection-background-color: #6E1E2B;
            }

            QFrame#VisualSlotCard {
                background: #0A0D0B;
                border: 1px solid #24302A;
                border-left: 3px solid #315C40;
                border-radius: 4px;
            }

            QFrame#VisualSlotCard[selected="true"] {
                background: #101713;
                border: 1px solid #55c783;
                border-left: 3px solid #55C783;
            }

            QLabel#VisualSlotThumb, QLabel#VisualPreviewThumb {
                color: #8f9d92;
                background: #050706;
                border: 1px solid #25342b;
                border-radius: 7px;
                font-size: 9px;
                font-weight: 900;
            }

            QLabel#VisualSlotTitle {
                color: #e3ddd4;
                font-size: 11px;
                font-weight: 900;
            }

            QLabel#VisualSlotMeta {
                color: #83c99d;
                font-size: 10px;
                font-weight: 800;
            }

            QLabel#VisualPreviewDim {
                border: none;
                background: transparent;
            }

            QLabel#VisualPreviewDim[displayMode="OVERLAY_CARD"] {
                background: rgba(0, 0, 0, 46);
            }

            QLabel#VisualPreviewDim[displayMode="FULL_FRAME_CONTAIN"] {
                background: rgba(0, 0, 0, 61);
            }

            QLabel#VisualPreviewOverlay {
                color: #DFF8E7;
                background: transparent;
                border: none;
                font-size: 13px;
                font-weight: 900;
            }

            QLabel#VisualPreviewOverlay[displayMode="OVERLAY_CARD"] {
                background: #F4EFE6;
                border: 3px solid #F4EFE6;
                border-radius: 5px;
                color: #111111;
            }

            QVideoWidget#VideoPreview {
                background: #020203;
                border: 1px solid #2e272b;
                border-radius: 18px;
            }

            QWidget#TimelinePanel {
                background: #080809;
                border: 1px solid #2E2927;
            }

            QWidget#VideoStack {
                background: transparent;
            }

            QPushButton {
                background: #17171A;
                color: #DED6C8;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 10px 16px;
                font-weight: 700;
            }

            QPushButton:hover {
                background: #212329;
                border: 1px solid #5a434c;
            }

            QPushButton:pressed {
                background: #111216;
            }

            QPushButton#PlayButton {
                min-width: 74px;
                max-width: 74px;
                min-height: 38px;
                padding: 4px 10px;
                background: #190B10;
                border: 1px solid #741C28;
                border-radius: 4px;
                color: #FFF0E8;
                font-size: 11px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QPushButton#TinyButton {
                color: #DED6C8;
                background: #101012;
                border: 1px solid #3A3030;
                border-radius: 3px;
                padding: 5px 9px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QPushButton#TinyButton:hover {
                background: #1C1014;
                border: 1px solid #C9384F;
            }

            QPushButton#CutButton {
                color: #ffd8dd;
                background: #2a141a;
                border: 1px solid #8d3445;
                padding: 7px 10px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QPushButton#CutButton:hover {
                background: #3a1821;
                border: 1px solid #e4586d;
            }

            QPushButton#RestoreButton {
                color: #c6c0b9;
                background: #131518;
                border: 1px solid #373136;
                padding: 7px 10px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1px;
            }

            QPushButton#RestoreButton:hover {
                color: #ffffff;
                background: #1b1d21;
                border: 1px solid #695a62;
            }

            QPushButton#MusicButton, QPushButton#AIButton {
                color: #ffd8dd;
                background: #1b1417;
                border: 1px solid #5f3740;
            }

            QPushButton#MusicButton:hover, QPushButton#AIButton:hover {
                background: #26191e;
                border: 1px solid #d04b5f;
            }

            QPushButton#QuietButton {
                color: #9d9089;
                background: #131417;
                border: 1px solid #282327;
            }

            QPushButton#QuietButton:disabled {
                color: #5d5755;
                background: #101114;
                border: 1px solid #201d20;
            }

            QPushButton#ClipCard {
                background: #09090A;
                color: #D1C7C0;
                border: 1px solid #252429;
                border-left: 3px solid #4B3657;
                border-radius: 4px;
                padding: 11px 12px;
                text-align: left;
                font-size: 11px;
                font-weight: 700;
            }

            QPushButton#ClipCard:hover {
                background: #171317;
                border: 1px solid #6b3f49;
                color: #fff8f2;
            }

            QPushButton#ClipCard[selected="true"] {
                background: #1A0D12;
                color: #FFF3E3;
                border: 1px solid #C9384F;
                border-left: 3px solid #C9384F;
            }

            QPushButton#GenerateButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #741C28, stop:1 #C9384F);
                color: white;
                border: 1px solid #E05C6F;
                border-radius: 4px;
                font-weight: 900;
                padding: 12px 20px;
            }

            QPushButton#GenerateButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #a12740, stop:1 #ef5d74);
            }

            QPushButton#GenerateButton:disabled {
                background: #29242a;
                border: 1px solid #3b3238;
                color: #6e676a;
            }

            QTextEdit {
                background: #09090A;
                border: 1px solid #252429;
                border-radius: 4px;
                padding: 10px;
                color: #cfc8c1;
                selection-background-color: #5e2631;
                font-family: Consolas;
                font-size: 11px;
            }

            QLabel#RenderProgressStage {
                color: #F2E6D4;
                background: #09090A;
                border: 1px solid #30292D;
                border-left: 3px solid #C9384F;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1px;
            }

            QLabel#RenderProgressTime {
                color: #AFA59B;
                font-family: Consolas;
                font-size: 11px;
                font-weight: 700;
            }

            QProgressBar#RenderProgressBar {
                background: #070708;
                border: 1px solid #30292D;
                border-radius: 3px;
                height: 16px;
            }

            QProgressBar#RenderProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #741C28, stop:0.55 #C9384F, stop:1 #F0A85A);
                border-radius: 2px;
            }

            QSlider::groove:horizontal {
                background: #17171A;
                height: 7px;
                border-radius: 2px;
            }

            QSlider::handle:horizontal {
                background: #DED6C8;
                border: 2px solid #C9384F;
                width: 16px;
                margin: -6px 0;
                border-radius: 3px;
            }

            QSlider::handle:horizontal:hover {
                background: #fff2f4;
            }

            QSlider::sub-page:horizontal {
                background: #741C28;
                border-radius: 2px;
            }

            QWidget#TimelineNavigator {
                background: #070708;
                border: 1px solid #242020;
            }

            QSlider#TimelineZoom::handle:horizontal {
                width: 12px;
            }
            """
        )



def main() -> int:

    app = QApplication(
        sys.argv
    )

    window = ShortsFactoryWindow()

    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
