"""
The custom multi-lane timeline/editor canvas (SuggestionSlider) -- by far
the most bespoke widget in the app, not a stock Qt component. Handles the
source clip trim handles, AI clip-hunter suggestion ranges, and the
stacked event lanes (transcript cuts, captions, smart motion, visual FX,
AI visual/graphic events, SFX), plus zoom/pan (including trackpad
horizontal scroll), drag-to-trim, and the app-wide custom Qt signals the
rest of the GUI listens to for timeline interaction. Largest file in the
gui_app package.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QPolygon
from PySide6.QtWidgets import QSizePolicy, QSlider

from .helpers import format_precise_time, format_time


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
            250
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

        # Always lay lanes out inside the height Qt actually gave the
        # timeline. QSplitter/QScrollArea can temporarily make this widget
        # shorter than its requested minimum height; using a synthetic
        # minimum here painted the SFX lane below the widget and clipped it.
        height = max(
            1,
            self.height(),
        )

        if height < 218:
            # Compact layout: preserve all four editor lanes rather than
            # sacrificing SFX when the center workspace is vertically tight.
            ruler_top = 8
            ruler_bottom = 30
            video_top = 36
            lane_gap = 5
            bottom_margin = 6
            available_lane_height = max(
                4,
                height
                - video_top
                - lane_gap * 3
                - bottom_margin,
            )
            video_height = max(
                1,
                int(
                    available_lane_height
                    * 0.30
                ),
            )
            edit_height = max(
                1,
                int(
                    available_lane_height
                    * 0.18
                ),
            )
            visual_height = max(
                1,
                int(
                    available_lane_height
                    * 0.27
                ),
            )
            sfx_height = max(
                1,
                available_lane_height
                - video_height
                - edit_height
                - visual_height,
            )
        else:
            ruler_top = 14
            ruler_bottom = 42
            video_top = 50
            lane_gap = 7
            bottom_margin = 10
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
            edit_height = 26

            # Give VISUALS its preferred height only when doing so still
            # leaves a visible SFX lane inside the real widget bounds.
            visual_top_preview = (
                video_top
                + video_height
                + lane_gap
                + edit_height
                + lane_gap
            )
            preferred_visual_height = max(
                36,
                min(
                    58,
                    int(
                        height
                        * 0.20
                    ),
                ),
            )
            max_visual_height = max(
                1,
                height
                - visual_top_preview
                - lane_gap
                - 24
                - bottom_margin,
            )
            visual_height = min(
                preferred_visual_height,
                max_visual_height,
            )

        edit_top = video_top + video_height + lane_gap
        visual_top = edit_top + edit_height + lane_gap
        sfx_top = visual_top + visual_height + lane_gap

        if height >= 218:
            sfx_height = max(
                1,
                height
                - sfx_top
                - bottom_margin,
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

    def visual_clip_stack_row(
        self,
        clip: dict,
    ) -> tuple[int, int]:

        clip_id = str(
            clip.get(
                "id",
                "",
            )
            or ""
        )
        visuals = [
            candidate
            for candidate in self.asset_clips
            if isinstance(
                candidate,
                dict,
            )
            and str(
                candidate.get(
                    "kind",
                    "",
                )
                or ""
            ).upper() == "AI_VISUAL"
            and not bool(
                candidate.get(
                    "deleted",
                    False,
                )
            )
        ]

        if len(visuals) <= 1:
            return 0, 1

        visuals.sort(
            key=lambda candidate: (
                self.clip_time_bounds_ms(
                    candidate
                )[0],
                self.clip_time_bounds_ms(
                    candidate
                )[1],
                str(
                    candidate.get(
                        "id",
                        "",
                    )
                    or ""
                ),
            )
        )

        target_start, target_end = self.clip_time_bounds_ms(
            clip
        )
        overlapping = [
            candidate
            for candidate in visuals
            if (
                self.clip_time_bounds_ms(
                    candidate
                )[0]
                < target_end
                and self.clip_time_bounds_ms(
                    candidate
                )[1]
                > target_start
            )
        ]

        if len(overlapping) <= 1:
            return 0, 1

        rows_end = [
            -1,
            -1,
            -1,
        ]
        row_by_id: dict[str, int] = {}

        for candidate in visuals:
            start_ms, end_ms = self.clip_time_bounds_ms(
                candidate
            )
            candidate_id = str(
                candidate.get(
                    "id",
                    "",
                )
                or ""
            )

            row = None
            for row_index, row_end in enumerate(
                rows_end
            ):
                if start_ms >= row_end:
                    row = row_index
                    break

            if row is None:
                row = min(
                    range(
                        len(
                            rows_end
                        )
                    ),
                    key=lambda index: rows_end[index],
                )

            rows_end[row] = max(
                rows_end[row],
                end_ms,
            )
            row_by_id[candidate_id] = row

        return (
            row_by_id.get(
                clip_id,
                0,
            ),
            min(
                3,
                max(
                    2,
                    len(
                        overlapping
                    ),
                ),
            ),
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
            row, row_count = self.visual_clip_stack_row(
                clip
            )
            if row_count <= 1:
                return (
                    lanes["visual_top"] + 4,
                    max(
                        18,
                        lanes["visual_height"] - 8,
                    ),
                )

            usable_height = max(
                18,
                lanes["visual_height"] - 8,
            )
            gap = 2
            row_height = max(
                14,
                int(
                    (
                        usable_height
                        - gap
                        * (
                            row_count
                            - 1
                        )
                    )
                    / row_count
                ),
            )
            row = min(
                row,
                row_count - 1,
            )
            return (
                lanes["visual_top"]
                + 4
                + row
                * (
                    row_height
                    + gap
                ),
                row_height,
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
        """
        Hit-tests in priority order and starts whichever drag interaction
        matches, falling through to plain playhead scrubbing if nothing
        else is hit: (1) an editor asset clip (AI_VISUAL/SFX lane) body
        or trim handle, (2) the source clip's own trim handles/body,
        (3) a legacy handle_at_position() hit, (4) an AI clip-hunter
        suggestion range (clicking one loads it as the selection),
        (5) otherwise, start scrubbing the playhead at the clicked
        position. Each branch sets whichever self.dragging_*/scrubbing_*
        flag the corresponding mouseMoveEvent/mouseReleaseEvent handlers
        check.
        """

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
        """
        Ctrl+wheel zooms the timeline; Shift+wheel pans it; a plain
        trackpad horizontal swipe (no modifier, horizontal delta
        dominant) also pans, via the same horizontal_pan() as Shift+wheel;
        a plain vertical scroll (no modifier) is left unhandled
        (event.ignore()) so it doesn't interfere with whatever a parent
        widget does with vertical scroll input.
        """

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

        # Plain two-finger trackpad horizontal scroll (no modifier key)
        # pans the timeline directly, without needing to hold Shift.
        # Distinguished from an ordinary vertical scroll by the horizontal
        # component being the dominant one, so a normal vertical scroll
        # over the timeline still falls through to event.ignore() below.
        horizontal_delta = event.angleDelta().x()

        if (
            not modifiers
            and horizontal_delta != 0
            and abs(horizontal_delta)
            >= abs(
                event.angleDelta().y()
            )
        ):
            direction = -1 if horizontal_delta > 0 else 1
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


