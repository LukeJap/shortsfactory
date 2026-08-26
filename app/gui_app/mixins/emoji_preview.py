"""
EmojiPreviewMixin: the draggable emoji reaction overlay in the placement
editor. Triggers emoji_planner.py to compute default placements before
any render, renders each active emoji as a draggable glyph/image (reusing
the local reaction-asset cache from emoji_overlay.py, never downloading
from the GUI thread), and provides the double-click picker (a grid of
local reaction assets plus a custom-emoji text field) for changing which
reaction is shown at a given moment.

Once a "Generate Emoji" plan exists (output/editor_asset_plan.json's
EMOJI clips, see gui_app/mixins/editor_assets.py) and still matches the
current source video/selection, that plan becomes the live source of
truth here too -- both for what the overlay renders and for where a drag/
swap made *on the video preview* gets saved -- so it stays in sync with
the editor timeline's EMOJI lane in both directions. Falls back to the
legacy output/emoji_events.json flow when no such plan exists yet (i.e.
before "Generate Emoji" has ever been run for this selection).
"""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QPoint, QProcess, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..constants import ROOT
from canvas_config import OUTPUT_HEIGHT, OUTPUT_WIDTH
from editor_asset_plan import clips_of_kind, upsert_clip
from pipeline_paths import EMOJI_EVENTS_PATH
from emoji_overlay import (
    EMOJI_DIR,
    EMOJI_SIZE,
    coerce_emoji_fraction,
    coerce_emoji_scale,
    emoji_filename,
    emoji_pixel_to_fraction,
    event_default_position_px,
    normalize_emoji,
    resolve_event_asset,
)
from make_captions import load_local_reaction_assets, relative_asset_path
from render import caption_anchor_y_px
from .resize_geometry import (
    CORNER_NAMES,
    OPPOSITE_CORNER,
    corner_handle_rects,
    corner_point,
    format_scale_readout,
    uniform_scale_ratio,
)


EMOJI_PLANNER_SCRIPT = ROOT / "app" / "emoji_planner.py"

EMOJI_CORNER_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
}


class EmojiPreviewMixin:

    def plan_emoji_preview(self):
        """
        Refresh the emoji preview (default positions for the currently
        selected clip) so it is visible before a full render runs. This is
        invisible plumbing, not a user-facing step -- it piggybacks on the
        same "clip selection is finalized, plan pre-render data" moment
        that already kicks off AI visual planning (see plan_ai_visuals()
        in ai_visual_pipeline.py).
        """

        if (
            not self.video_path
            or not self.source_transcript_segments
            or self.end_ms <= self.start_ms
        ):
            return

        if (
            self.emoji_preview_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        if not EMOJI_PLANNER_SCRIPT.exists():
            return

        transcript_path = ROOT / "output" / "subtitles.json"
        if not transcript_path.exists():
            return

        self.emoji_preview_process.start(
            sys.executable,
            [
                str(EMOJI_PLANNER_SCRIPT),
                "--transcript",
                str(transcript_path),
                "--start",
                f"{self.start_ms / 1000:.3f}",
                "--end",
                f"{self.end_ms / 1000:.3f}",
                "--energy",
                self.current_edit_energy(),
            ],
        )


    def emoji_preview_plan_finished(self, exit_code: int, exit_status):

        if exit_code != 0:
            return

        self._emoji_events_cache = None
        if hasattr(self, "player"):
            self.update_emoji_preview_overlay(self.player.position())


    def load_emoji_events_file(self) -> dict:

        if not EMOJI_EVENTS_PATH.exists():
            self._emoji_events_cache = None
            return {}

        try:
            mtime = EMOJI_EVENTS_PATH.stat().st_mtime_ns
        except OSError:
            mtime = None

        cache = getattr(self, "_emoji_events_cache", None)
        if cache is not None and mtime is not None and cache[0] == mtime:
            return cache[1]

        try:
            data = json.loads(
                EMOJI_EVENTS_PATH.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {}

        data = data if isinstance(data, dict) else {}
        self._emoji_events_cache = (mtime, data)
        return data


    def save_emoji_events_file(self, data: dict):

        EMOJI_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)

        try:
            EMOJI_EVENTS_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            if hasattr(self, "render_log"):
                self.render_log.append(
                    f"WARNING: Could not save emoji preview position: {exc}"
                )


    def emoji_editor_plan_clips_for_preview(self) -> list[dict] | None:
        """
        The current selection's active EMOJI editor-plan clips (absolute
        source-video time, same convention as emoji_events.json's own
        "absolute" time_base) if a "Generate Emoji" plan exists and its
        stored context still matches the current selection, else None so
        the caller falls back to the legacy emoji_events.json preview.
        """

        if not self.editor_asset_context_matches_current_selection():
            return None

        clips = clips_of_kind(
            self.editor_asset_plan,
            "EMOJI",
            active_only=True,
        )
        return clips or None


    def active_emoji_preview_events(
        self,
        position_ms: int,
    ) -> list[tuple[str, str | int, dict]]:
        """
        Returns (source, key, event) tuples for whichever emoji reactions
        are active at position_ms -- source is "editor_plan" (key is the
        clip id in output/editor_asset_plan.json) or "legacy" (key is the
        event's index in output/emoji_events.json), so callers that save a
        drag/swap know which store to write back into.
        """

        editor_clips = self.emoji_editor_plan_clips_for_preview()
        if editor_clips is not None:

            reference_ms = int(position_ms)

            active = []
            for clip in editor_clips:
                try:
                    start_ms = int(round(float(clip.get("start", 0.0)) * 1000))
                    end_ms = int(round(float(clip.get("end", 0.0)) * 1000))
                except (TypeError, ValueError):
                    continue
                if start_ms <= reference_ms <= max(start_ms, end_ms):
                    clip_id = str(clip.get("id", "") or "")
                    if clip_id:
                        active.append(("editor_plan", clip_id, clip))

            return active

        data = self.load_emoji_events_file()
        events = data.get("events", [])
        if not isinstance(events, list):
            return []

        if data.get("time_base") == "absolute":
            reference_ms = int(position_ms)
        else:
            reference_ms = int(position_ms) - int(
                getattr(self, "start_ms", 0)
            )

        active = []
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            try:
                start_ms = int(round(float(event.get("start", 0.0)) * 1000))
                end_ms = int(round(float(event.get("end", 0.0)) * 1000))
            except (TypeError, ValueError):
                continue
            if start_ms <= reference_ms <= max(start_ms, end_ms):
                active.append(("legacy", index, event))

        return active


    def emoji_preview_glyph_pixmap(self, event: dict) -> QPixmap | None:

        asset_path = resolve_event_asset(event)
        if asset_path is not None:
            pixmap = QPixmap(str(asset_path))
            return pixmap if not pixmap.isNull() else None

        emoji = normalize_emoji(event.get("emoji", ""))
        if not emoji:
            return None

        # Preview must never trigger a network download of a missing emoji
        # asset (would block the UI thread on every scrub/refresh) -- only
        # use what is already cached locally from a previous real render.
        cached_path = EMOJI_DIR / emoji_filename(emoji)
        if cached_path.exists() and cached_path.stat().st_size > 0:
            pixmap = QPixmap(str(cached_path))
            if not pixmap.isNull():
                return pixmap

        return None


    def ensure_emoji_preview_label_pool(self, count: int):

        if not hasattr(self, "emoji_preview_labels"):
            self.emoji_preview_labels = []

        if not hasattr(self, "emoji_resize_handles"):
            # NOTE: main_window.py's __init__ pre-seeds
            # self.emoji_preview_labels = [] before this mixin ever runs,
            # so a hasattr() check on that attribute is always True here --
            # this block must be guarded on its own attribute instead, or
            # it silently never runs and every emoji_resize_* method below
            # blows up with an AttributeError the first time anything
            # touches self.emoji_resize_handles.
            self.emoji_resize_handles = []
            self.emoji_resize_hover_slot = None
            self.emoji_resize_dragging = False
            self.emoji_resize_readout = QLabel("", self.video_widget)
            self.emoji_resize_readout.setObjectName("EmojiResizeReadout")
            self.emoji_resize_readout.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )
            self.emoji_resize_readout.hide()

        while len(self.emoji_preview_labels) < count:
            label = QLabel(self.video_widget)
            label.setObjectName("EmojiPreviewOverlay")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                False,
            )
            label.setMouseTracking(True)
            label.setCursor(Qt.CursorShape.OpenHandCursor)
            label.setToolTip("Drag to reposition this emoji reaction.")
            label.hide()
            self.emoji_preview_labels.append(label)

            handles = {}
            for corner in CORNER_NAMES:
                handle = QLabel(self.video_widget)
                handle.setObjectName("EmojiResizeHandle")
                handle.setCursor(EMOJI_CORNER_CURSORS[corner])
                handle.hide()
                handles[corner] = handle
            self.emoji_resize_handles.append(handles)


    def hide_emoji_preview_overlays(self):

        for label in getattr(self, "emoji_preview_labels", []):
            label.hide()
        for handles in getattr(self, "emoji_resize_handles", []):
            for handle in handles.values():
                handle.hide()
        if hasattr(self, "emoji_resize_readout"):
            self.emoji_resize_readout.hide()
        self.emoji_resize_hover_slot = None
        self.emoji_preview_active = []


    def update_emoji_preview_overlay(self, position_ms: int):

        if not hasattr(self, "video_widget"):
            return

        active = self.active_emoji_preview_events(position_ms)
        self.ensure_emoji_preview_label_pool(len(active))

        canvas_x, canvas_y, canvas_width, canvas_height = (
            self.ai_visual_preview_canvas_rect()
        )

        for slot_index, label in enumerate(self.emoji_preview_labels):
            if slot_index >= len(active):
                label.hide()
                for handle in self.emoji_resize_handles[slot_index].values():
                    handle.hide()
                continue

            _source, _key, event = active[slot_index]

            event_scale = coerce_emoji_scale(event.get("scale", 1.0))
            emoji_width = max(
                1,
                round(canvas_width * (EMOJI_SIZE * event_scale / OUTPUT_WIDTH)),
            )
            emoji_height = max(
                1,
                round(canvas_height * (EMOJI_SIZE * event_scale / OUTPUT_HEIGHT)),
            )

            position_x = coerce_emoji_fraction(event.get("position_x", 0.0))
            position_y = coerce_emoji_fraction(event.get("position_y", 0.0))

            screen_x = canvas_x + round(
                position_x * max(0, canvas_width - emoji_width)
            )
            screen_y = canvas_y + round(
                position_y * max(0, canvas_height - emoji_height)
            )

            label.setGeometry(screen_x, screen_y, emoji_width, emoji_height)

            pixmap = self.emoji_preview_glyph_pixmap(event)
            if pixmap is not None:
                label.setText("")
                label.setPixmap(
                    pixmap.scaled(
                        label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                label.setPixmap(QPixmap())
                font = QFont()
                font.setPointSize(max(12, emoji_height // 3))
                label.setFont(font)
                label.setText(str(event.get("emoji", "?")))

            label.raise_()
            label.show()

            self.layout_emoji_resize_handles(slot_index, screen_x, screen_y, emoji_width, emoji_height)

        self.emoji_preview_active = active


    def begin_emoji_preview_drag(self, event, watched) -> bool:

        for slot_index, label in enumerate(
            getattr(self, "emoji_preview_labels", [])
        ):
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

            if not hit:
                continue

            active = getattr(self, "emoji_preview_active", [])
            if slot_index >= len(active):
                return False

            _source, _key, active_event = active[slot_index]

            self.emoji_preview_dragging = True
            self.emoji_preview_drag_slot = slot_index
            self.emoji_preview_drag_origin = (
                event.globalPosition().toPoint()
            )
            self.emoji_preview_drag_start_x = coerce_emoji_fraction(
                active_event.get("position_x", 0.0)
            )
            self.emoji_preview_drag_start_y = coerce_emoji_fraction(
                active_event.get("position_y", 0.0)
            )
            label.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True

        return False


    def update_emoji_preview_drag(self, event):

        if not getattr(self, "emoji_preview_dragging", False):
            return

        slot_index = self.emoji_preview_drag_slot
        active = getattr(self, "emoji_preview_active", [])
        if slot_index >= len(active):
            return

        canvas_x, canvas_y, canvas_width, canvas_height = (
            self.ai_visual_preview_canvas_rect()
        )

        _source, _key, active_event = active[slot_index]
        event_scale = coerce_emoji_scale(active_event.get("scale", 1.0))
        emoji_width = max(
            1,
            round(canvas_width * (EMOJI_SIZE * event_scale / OUTPUT_WIDTH)),
        )
        emoji_height = max(
            1,
            round(canvas_height * (EMOJI_SIZE * event_scale / OUTPUT_HEIGHT)),
        )

        delta = event.globalPosition().toPoint() - self.emoji_preview_drag_origin

        x_span = max(1, canvas_width - emoji_width)
        y_span = max(1, canvas_height - emoji_height)

        position_x = coerce_emoji_fraction(
            self.emoji_preview_drag_start_x + delta.x() / x_span
        )
        position_y = coerce_emoji_fraction(
            self.emoji_preview_drag_start_y + delta.y() / y_span
        )

        active_event["position_x"] = round(position_x, 3)
        active_event["position_y"] = round(position_y, 3)

        label = self.emoji_preview_labels[slot_index]
        screen_x = canvas_x + round(position_x * x_span)
        screen_y = canvas_y + round(position_y * y_span)
        label.setGeometry(screen_x, screen_y, emoji_width, emoji_height)
        self.layout_emoji_resize_handles(slot_index, screen_x, screen_y, emoji_width, emoji_height)


    def layout_emoji_resize_handles(self, slot_index, x, y, width, height):
        if slot_index >= len(self.emoji_resize_handles):
            return

        # Reentrancy guard -- see the matching comment in
        # caption_preview.py's layout_caption_resize_handles().
        if getattr(self, "_laying_out_emoji_resize_handles", False):
            return
        self._laying_out_emoji_resize_handles = True
        try:
            handles = self.emoji_resize_handles[slot_index]
            rects = corner_handle_rects(x, y, width, height)
            for corner, rect in rects.items():
                handles[corner].setGeometry(rect)

            active = self.emoji_resize_hover_slot == slot_index or (
                getattr(self, "emoji_resize_dragging", False)
                and getattr(self, "emoji_resize_drag_slot", None) == slot_index
            )
            for handle in handles.values():
                if active:
                    handle.raise_()
                    handle.show()
                else:
                    handle.hide()
        finally:
            self._laying_out_emoji_resize_handles = False


    def set_emoji_resize_hover(self, slot_index):
        if not hasattr(self, "emoji_preview_labels"):
            return
        if self.emoji_resize_hover_slot == slot_index:
            return

        previous = self.emoji_resize_hover_slot
        self.emoji_resize_hover_slot = slot_index

        for index in {previous, slot_index}:
            if index is None or index >= len(self.emoji_preview_labels):
                continue
            label = self.emoji_preview_labels[index]
            if label.isVisible():
                self.layout_emoji_resize_handles(
                    index, label.x(), label.y(), label.width(), label.height()
                )


    def emoji_resize_handle_at(self, event, watched):
        for slot_index, handles in enumerate(
            getattr(self, "emoji_resize_handles", [])
        ):
            for corner, handle in handles.items():
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
                    return (slot_index, corner)
        return None


    def begin_emoji_resize_drag(self, event, watched) -> bool:
        hit = self.emoji_resize_handle_at(event, watched)
        if hit is None:
            return False
        slot_index, corner = hit

        active = getattr(self, "emoji_preview_active", [])
        if slot_index >= len(active):
            return False

        label = self.emoji_preview_labels[slot_index]
        geometry = label.geometry()

        anchor_name = OPPOSITE_CORNER[corner]
        anchor_point = corner_point(
            anchor_name,
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        )
        start_point = corner_point(
            corner,
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        )

        _source, _key, active_event = active[slot_index]

        # See the matching comment in ai_visual_preview.py's
        # begin_visual_resize_drag(): anchor_point/start_point are in
        # video_widget-local coordinates (needed below to solve for the
        # new position fraction), but the live drag ratio is compared
        # against the mouse's *global* position on every move -- so that
        # comparison needs its own global-mapped copies of these two
        # points, or the computed ratio is meaningless.
        global_anchor = self.video_widget.mapToGlobal(
            QPoint(anchor_point[0], anchor_point[1])
        )
        global_start = self.video_widget.mapToGlobal(
            QPoint(start_point[0], start_point[1])
        )

        self.emoji_resize_dragging = True
        self.emoji_resize_drag_slot = slot_index
        self.emoji_resize_handle = corner
        self.emoji_resize_anchor_name = anchor_name
        self.emoji_resize_anchor = anchor_point
        self.emoji_resize_start_point = start_point
        self.emoji_resize_global_anchor = (global_anchor.x(), global_anchor.y())
        self.emoji_resize_global_start = (global_start.x(), global_start.y())
        self.emoji_resize_start_scale = coerce_emoji_scale(
            active_event.get("scale", 1.0)
        )
        return True


    def update_emoji_resize_drag(self, event):
        if not getattr(self, "emoji_resize_dragging", False):
            return

        slot_index = self.emoji_resize_drag_slot
        active = getattr(self, "emoji_preview_active", [])
        if slot_index >= len(active):
            return

        canvas_x, canvas_y, canvas_width, canvas_height = (
            self.ai_visual_preview_canvas_rect()
        )

        mouse = event.globalPosition().toPoint()
        anchor_x, anchor_y = self.emoji_resize_anchor
        global_anchor_x, global_anchor_y = self.emoji_resize_global_anchor
        global_start_x, global_start_y = self.emoji_resize_global_start

        ratio = uniform_scale_ratio(
            global_anchor_x, global_anchor_y, global_start_x, global_start_y,
            mouse.x(), mouse.y(),
        )
        new_scale = coerce_emoji_scale(self.emoji_resize_start_scale * ratio)

        new_width = max(
            1, round(canvas_width * (EMOJI_SIZE * new_scale / OUTPUT_WIDTH))
        )
        new_height = max(
            1, round(canvas_height * (EMOJI_SIZE * new_scale / OUTPUT_HEIGHT))
        )

        anchor_name = self.emoji_resize_anchor_name
        target_x = (
            anchor_x if anchor_name in ("nw", "sw") else anchor_x - new_width
        )
        target_y = (
            anchor_y if anchor_name in ("nw", "ne") else anchor_y - new_height
        )

        x_span = max(1, canvas_width - new_width)
        y_span = max(1, canvas_height - new_height)
        position_x = coerce_emoji_fraction((target_x - canvas_x) / x_span)
        position_y = coerce_emoji_fraction((target_y - canvas_y) / y_span)

        _source, _key, active_event = active[slot_index]
        active_event["scale"] = round(new_scale, 2)
        active_event["position_x"] = round(position_x, 3)
        active_event["position_y"] = round(position_y, 3)

        screen_x = canvas_x + round(position_x * x_span)
        screen_y = canvas_y + round(position_y * y_span)
        label = self.emoji_preview_labels[slot_index]
        label.setGeometry(screen_x, screen_y, new_width, new_height)
        pixmap = self.emoji_preview_glyph_pixmap(active_event)
        if pixmap is not None:
            label.setPixmap(
                pixmap.scaled(
                    label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.layout_emoji_resize_handles(
            slot_index, screen_x, screen_y, new_width, new_height
        )

        readout = self.emoji_resize_readout
        readout.setText(format_scale_readout(new_scale))
        readout.adjustSize()
        readout.move(screen_x, max(0, screen_y - readout.height() - 4))
        readout.raise_()
        readout.show()


    def finish_emoji_resize_drag(self):
        if not getattr(self, "emoji_resize_dragging", False):
            return

        self.emoji_resize_dragging = False
        self.emoji_resize_readout.hide()

        slot_index = self.emoji_resize_drag_slot
        active = getattr(self, "emoji_preview_active", [])
        if slot_index >= len(active):
            return

        source, key, active_event = active[slot_index]
        active_event["manual_override"] = True

        if source == "editor_plan":
            clip = self.find_editor_clip("EMOJI", key)
            if clip is None:
                return
            clip["scale"] = active_event["scale"]
            clip["position_x"] = active_event["position_x"]
            clip["position_y"] = active_event["position_y"]
            clip["manual_override"] = True
            clip["locked"] = True
            self.editor_asset_plan = upsert_clip(
                self.editor_asset_plan,
                clip,
            )
            self.save_editor_asset_plan_state()
            self.refresh_editor_asset_timeline()
            return

        event_index = key
        data = self.load_emoji_events_file()
        events = data.get("events", [])
        if isinstance(events, list) and 0 <= event_index < len(events):
            events[event_index]["scale"] = active_event["scale"]
            events[event_index]["position_x"] = active_event["position_x"]
            events[event_index]["position_y"] = active_event["position_y"]
            events[event_index]["manual_override"] = True
            data["events"] = events
            self.save_emoji_events_file(data)


    def reset_emoji_preview_position(self, slot_index: int) -> bool:

        active = getattr(self, "emoji_preview_active", [])
        if not (0 <= slot_index < len(active)):
            return False

        source, key, _active_event = active[slot_index]

        caption_anchor_y = caption_anchor_y_px(
            {"caption_position_y": getattr(self, "caption_position_y", None)}
        )
        default_x, default_y = event_default_position_px(
            slot_index, caption_anchor_y
        )
        position_x, position_y = emoji_pixel_to_fraction(default_x, default_y)

        if source == "editor_plan":
            clip = self.find_editor_clip("EMOJI", key)
            if clip is None:
                return False
            clip["position_x"] = round(position_x, 3)
            clip["position_y"] = round(position_y, 3)
            clip["scale"] = 1.0
            clip["manual_override"] = False
            self.editor_asset_plan = upsert_clip(
                self.editor_asset_plan,
                clip,
            )
            self.save_editor_asset_plan_state()
            self.refresh_editor_asset_timeline()
            self.update_emoji_preview_overlay(self.player.position())
            return True

        event_index = key
        data = self.load_emoji_events_file()
        events = data.get("events", [])
        if not (isinstance(events, list) and 0 <= event_index < len(events)):
            return False

        events[event_index]["position_x"] = round(position_x, 3)
        events[event_index]["position_y"] = round(position_y, 3)
        events[event_index]["scale"] = 1.0
        events[event_index]["manual_override"] = False
        data["events"] = events
        self.save_emoji_events_file(data)

        self._emoji_events_cache = None
        self.update_emoji_preview_overlay(self.player.position())
        return True


    def finish_emoji_preview_drag(self):

        if not getattr(self, "emoji_preview_dragging", False):
            return

        self.emoji_preview_dragging = False
        slot_index = self.emoji_preview_drag_slot
        active = getattr(self, "emoji_preview_active", [])

        if 0 <= slot_index < len(self.emoji_preview_labels):
            self.emoji_preview_labels[slot_index].setCursor(
                Qt.CursorShape.OpenHandCursor
            )

        if slot_index >= len(active):
            return

        source, key, active_event = active[slot_index]
        active_event["manual_override"] = True

        if source == "editor_plan":
            clip = self.find_editor_clip("EMOJI", key)
            if clip is None:
                return
            clip["position_x"] = active_event["position_x"]
            clip["position_y"] = active_event["position_y"]
            clip["manual_override"] = True
            clip["locked"] = True
            self.editor_asset_plan = upsert_clip(
                self.editor_asset_plan,
                clip,
            )
            self.save_editor_asset_plan_state()
            self.refresh_editor_asset_timeline()
            return

        event_index = key
        data = self.load_emoji_events_file()
        events = data.get("events", [])
        if isinstance(events, list) and 0 <= event_index < len(events):
            events[event_index]["position_x"] = active_event["position_x"]
            events[event_index]["position_y"] = active_event["position_y"]
            events[event_index]["manual_override"] = True
            data["events"] = events
            self.save_emoji_events_file(data)


    def open_emoji_picker(self, slot_index: int):

        active = getattr(self, "emoji_preview_active", [])
        if not (0 <= slot_index < len(active)):
            return

        source, key, active_event = active[slot_index]

        if source == "editor_plan":
            # Reuse the editor timeline's own picker/save logic (editor_
            # assets.py) so a swap made from the video preview writes into
            # the same editor_asset_plan.json clip the timeline lane shows,
            # instead of duplicating a second copy of this dialog.
            self.open_editor_emoji_picker(key)
            return

        event_index = key

        dialog = QDialog(self)
        dialog.setWindowTitle("Change Emoji Reaction")
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        hint = QLabel(
            f"Currently: {active_event.get('emoji', '?')} "
            f"(\"{active_event.get('matched_word', '')}\")"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(320)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)

        assets = load_local_reaction_assets()
        columns = 5

        for index, asset in enumerate(assets):
            path = asset["path"]
            button = QPushButton()
            button.setToolTip(asset.get("description", path.stem))
            button.setFixedSize(64, 64)
            button.setIconSize(QSize(52, 52))

            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                button.setIcon(QIcon(pixmap))
            else:
                button.setText(path.stem[:6])

            button.clicked.connect(
                lambda checked=False, chosen_path=path, chosen_description=asset.get(
                    "description", path.stem
                ): self.apply_emoji_picker_choice(
                    event_index,
                    dialog,
                    asset_path=chosen_path,
                    description=chosen_description,
                )
            )
            grid.addWidget(button, index // columns, index % columns)

        scroll.setWidget(grid_widget)
        layout.addWidget(scroll)

        custom_row = QHBoxLayout()
        custom_label = QLabel("Or type a custom emoji:")
        custom_input = QLineEdit()
        custom_input.setPlaceholderText("e.g. \U0001f525")
        custom_input.setMaximumWidth(80)
        custom_use_button = QPushButton("Use")
        custom_use_button.clicked.connect(
            lambda: self.apply_emoji_picker_choice(
                event_index,
                dialog,
                custom_emoji=custom_input.text(),
            )
        )
        custom_row.addWidget(custom_label)
        custom_row.addWidget(custom_input)
        custom_row.addWidget(custom_use_button)
        custom_row.addStretch()
        layout.addLayout(custom_row)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        layout.addWidget(cancel_button)

        dialog.exec()


    def apply_emoji_picker_choice(
        self,
        event_index: int,
        dialog,
        asset_path=None,
        description: str = "",
        custom_emoji: str = "",
    ):

        data = self.load_emoji_events_file()
        events = data.get("events", [])
        if not (isinstance(events, list) and 0 <= event_index < len(events)):
            dialog.reject()
            return

        event = events[event_index]

        if asset_path is not None:
            event["asset_path"] = relative_asset_path(asset_path)
            event["asset_description"] = description
            event["asset_type"] = "local"
            event["emoji"] = description
        else:
            emoji = normalize_emoji(custom_emoji)
            if not emoji:
                dialog.reject()
                return
            event["emoji"] = emoji
            event.pop("asset_path", None)
            event.pop("asset_description", None)
            event.pop("asset_type", None)

        event["manual_override"] = True
        event["content_override"] = True

        data["events"] = events
        self.save_emoji_events_file(data)
        self._emoji_events_cache = None

        dialog.accept()
        self.update_emoji_preview_overlay(self.player.position())
