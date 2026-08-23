from __future__ import annotations

import json
import sys

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QLabel

from ..constants import ROOT
from emoji_overlay import (
    EMOJI_DIR,
    EMOJI_SIZE,
    coerce_emoji_fraction,
    emoji_filename,
    emoji_pixel_to_fraction,
    event_default_position_px,
    normalize_emoji,
    resolve_event_asset,
)


EMOJI_EVENTS_PATH = ROOT / "output" / "emoji_events.json"
EMOJI_PLANNER_SCRIPT = ROOT / "app" / "emoji_planner.py"


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


    def active_emoji_preview_events(
        self,
        position_ms: int,
    ) -> list[tuple[int, dict]]:

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
                active.append((index, event))

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


    def hide_emoji_preview_overlays(self):

        for label in getattr(self, "emoji_preview_labels", []):
            label.hide()
        self.emoji_preview_active = []


    def update_emoji_preview_overlay(self, position_ms: int):

        if not hasattr(self, "video_widget"):
            return

        active = self.active_emoji_preview_events(position_ms)
        self.ensure_emoji_preview_label_pool(len(active))

        canvas_x, canvas_y, canvas_width, canvas_height = (
            self.ai_visual_preview_canvas_rect()
        )

        emoji_width = max(1, round(canvas_width * (EMOJI_SIZE / 1080)))
        emoji_height = max(1, round(canvas_height * (EMOJI_SIZE / 1920)))

        for slot_index, label in enumerate(self.emoji_preview_labels):
            if slot_index >= len(active):
                label.hide()
                continue

            _event_index, event = active[slot_index]

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

            _event_index, active_event = active[slot_index]

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
        emoji_width = max(1, round(canvas_width * (EMOJI_SIZE / 1080)))
        emoji_height = max(1, round(canvas_height * (EMOJI_SIZE / 1920)))

        delta = event.globalPosition().toPoint() - self.emoji_preview_drag_origin

        x_span = max(1, canvas_width - emoji_width)
        y_span = max(1, canvas_height - emoji_height)

        position_x = coerce_emoji_fraction(
            self.emoji_preview_drag_start_x + delta.x() / x_span
        )
        position_y = coerce_emoji_fraction(
            self.emoji_preview_drag_start_y + delta.y() / y_span
        )

        _event_index, active_event = active[slot_index]
        active_event["position_x"] = round(position_x, 3)
        active_event["position_y"] = round(position_y, 3)

        label = self.emoji_preview_labels[slot_index]
        screen_x = canvas_x + round(position_x * x_span)
        screen_y = canvas_y + round(position_y * y_span)
        label.setGeometry(screen_x, screen_y, emoji_width, emoji_height)


    def reset_emoji_preview_position(self, slot_index: int) -> bool:

        active = getattr(self, "emoji_preview_active", [])
        if not (0 <= slot_index < len(active)):
            return False

        event_index, _active_event = active[slot_index]

        default_x, default_y = event_default_position_px(event_index)
        position_x, position_y = emoji_pixel_to_fraction(default_x, default_y)

        data = self.load_emoji_events_file()
        events = data.get("events", [])
        if not (isinstance(events, list) and 0 <= event_index < len(events)):
            return False

        events[event_index]["position_x"] = round(position_x, 3)
        events[event_index]["position_y"] = round(position_y, 3)
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

        event_index, active_event = active[slot_index]
        active_event["manual_override"] = True

        data = self.load_emoji_events_file()
        events = data.get("events", [])
        if isinstance(events, list) and 0 <= event_index < len(events):
            events[event_index]["position_x"] = active_event["position_x"]
            events[event_index]["position_y"] = active_event["position_y"]
            events[event_index]["manual_override"] = True
            data["events"] = events
            self.save_emoji_events_file(data)
