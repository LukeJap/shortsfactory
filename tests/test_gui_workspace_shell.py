from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QSplitter

from gui_app.main_window import ShortsFactoryWindow


def _window():
    app = QApplication.instance() or QApplication([])
    window = ShortsFactoryWindow()
    app.processEvents()
    return app, window


def _close(app, window):
    window.close()
    window.deleteLater()
    app.processEvents()


def _populate_standard_clip_cards(window):
    window.ai_candidates = [
        {
            "rank": index + 1,
            "start_ms": index * 10_000,
            "end_ms": (index + 1) * 10_000,
            "score": 95 - index,
            "hook": "A revealing moment changes the entire story",
            "description": (
                "This deliberately long transcript description represents a "
                "strong candidate moment with enough words to exercise the "
                "responsive clip-card layout."
            ),
            "reason": (
                "The character makes an important emotional choice that "
                "creates a consequence and gives the recap an immediately "
                "understandable turning point."
            ),
        }
        for index in range(6)
    ]
    window.populate_clip_cards()


def _rect_in_viewport(widget, viewport):
    rect = widget.rect()
    rect.moveTopLeft(widget.mapTo(viewport, QPoint(0, 0)))
    return rect


def test_workspace_places_timeline_transcript_and_render_log_in_right_editor_column():
    app, window = _window()
    try:
        assert window.workspace_splitter.count() == 3
        right_stack = window.right_editor_content.layout()
        assert right_stack.count() == 3
        assert right_stack.itemAt(0).widget() is window.timeline_panel
        assert right_stack.itemAt(1).widget() is window.transcript_frame
        assert right_stack.itemAt(2).widget() is window.render_log_frame
        assert window.right_editor_scroll.widget() is window.right_editor_content
        assert not hasattr(window, "right_splitter")
        assert not window.right_editor_content.findChildren(QSplitter)
        assert window.timeline.parentWidget() is window.timeline_panel
        assert window.transcript_list.parentWidget() is window.transcript_frame
        assert window.render_log.parentWidget() is window.render_log_frame
        assert window.video_widget.parentWidget() is window.program_monitor_viewport
        assert not hasattr(window, "source_monitor_player")
        assert not hasattr(window, "source_video_widget")
        assert not hasattr(window, "visual_plan_slots")
        assert not hasattr(window, "visual_generate_button")
        assert not hasattr(window, "timeline_legend")
        assert window.global_progress_frame.findChild(type(window.timeline)) is None
    finally:
        _close(app, window)


def test_program_monitor_is_portrait_and_clear_of_its_transport_controls():
    app, window = _window()
    try:
        window.resize(1700, 1000)
        window.show()
        app.processEvents()

        video = window.video_widget
        video_rect = video.rect()
        video_rect.moveTopLeft(video.mapTo(window, QPoint(0, 0)))
        transport_rect = window.play_button.rect()
        transport_rect.moveTopLeft(window.play_button.mapTo(window, QPoint(0, 0)))
        selection_rect = window.start_button.rect()
        selection_rect.moveTopLeft(window.start_button.mapTo(window, QPoint(0, 0)))

        assert video.width() > 320
        assert video.height() > 560
        assert abs(video.width() * 16 - video.height() * 9) <= 16
        assert not video_rect.intersects(transport_rect)
        assert not video_rect.intersects(selection_rect)
    finally:
        _close(app, window)


def test_workspace_rebalances_width_to_the_right_editor_without_shrinking_preview():
    app, window = _window()
    try:
        window.show()

        for width, height in ((1920, 1080), (1600, 900), (1280, 720)):
            window.resize(width, height)
            app.processEvents()

            left, center, right = window.workspace_splitter.sizes()
            assert left >= 300
            assert right >= 520
            assert right >= center
            assert window.right_editor_scroll.horizontalScrollBar().maximum() == 0

        window.resize(1920, 1080)
        app.processEvents()

        assert window.video_widget.width() >= 390
        assert window.video_widget.width() <= 450
        assert window.center_scroll.width() <= 700
        assert window.right_editor_scroll.width() > window.center_scroll.width()
    finally:
        _close(app, window)


def test_timeline_footer_and_transcript_actions_stay_within_the_right_editor():
    app, window = _window()
    try:
        window.resize(1280, 720)
        window.show()
        app.processEvents()

        timeline_widgets = (
            window.timeline_navigator,
            window.start_button,
            window.end_button,
            window.selection_label,
            window.render_features_summary_label,
            window.timeline_time_label,
            window.timeline_zoom_slider,
            window.fit_selection_button,
            window.fit_source_button,
        )
        for widget in timeline_widgets:
            rect = _rect_in_viewport(widget, window.timeline_panel)
            assert rect.left() >= 0
            assert rect.right() < window.timeline_panel.width()

        assert not hasattr(window, "timeline_legend")
        assert not hasattr(window, "timeline_legend_labels")

        for button in (
            window.edit_transcript_button,
            window.reset_transcript_text_button,
            window.cut_transcript_button,
            window.restore_transcript_button,
        ):
            rect = _rect_in_viewport(button, window.transcript_frame)
            assert rect.left() >= 0
            assert rect.right() < window.transcript_frame.width()
    finally:
        _close(app, window)


def test_live_render_output_uses_the_right_log_while_footer_keeps_status():
    app, window = _window()
    try:
        window.append_live_render_log("Applying visual FX\n")
        window.set_render_progress_stage("rendering")

        assert "Applying visual FX" in window.render_log.toPlainText()
        assert window.render_progress_stage_label.text() == "RENDERING"
        assert window.render_log.verticalScrollBar() is not None
        assert window.transcript_list.verticalScrollBar() is not None
    finally:
        _close(app, window)


def test_standard_sidebar_cards_fit_the_viewport_at_supported_desktop_sizes():
    app, window = _window()
    try:
        _populate_standard_clip_cards(window)
        window.show()

        for width, height in ((1920, 1080), (1600, 900), (1280, 720)):
            window.resize(width, height)
            app.processEvents()

            viewport = window.standard_short_mode_frame.viewport()
            assert window.standard_short_controls_frame.width() <= viewport.width()
            for card in window.clip_cards:
                if card.isVisible():
                    rect = _rect_in_viewport(card, viewport)
                    assert rect.left() >= 0
                    assert rect.right() < viewport.width()

        assert window.standard_short_mode_frame.verticalScrollBar().maximum() > 0
    finally:
        _close(app, window)


def test_recap_sidebar_and_right_editor_stack_remain_responsive():
    app, window = _window()
    try:
        window.resize(1600, 900)
        window.show()
        window.set_recap_mode("recap")
        app.processEvents()

        recap_viewport = window.recap_scroll_area.viewport()
        assert window.recap_frame.width() <= recap_viewport.width()
        right_stack = window.right_editor_content.layout()
        assert right_stack.itemAt(0).widget() is window.timeline_panel
        assert right_stack.itemAt(1).widget() is window.transcript_frame
        assert right_stack.itemAt(2).widget() is window.render_log_frame
        assert window.video_widget.height() > 500
    finally:
        _close(app, window)


def test_timeline_uses_non_overlapping_five_lane_geometry():
    app, window = _window()
    try:
        timeline = window.timeline
        lanes = timeline.lane_geometry()
        names = ("source", "visual", "sfx", "emoji", "voiceover")
        bounds = [timeline.lane_rect(name) for name in names]

        assert [height for _top, height in bounds] == [40, 34, 32, 36, 42]
        assert all(
            top + height < next_top
            for (top, height), (next_top, _next_height) in zip(bounds, bounds[1:])
        )
        assert lanes["lane_bottom"] == bounds[-1][0] + bounds[-1][1]
        assert timeline.minimumHeight() >= timeline.required_lane_stack_height()
    finally:
        _close(app, window)


def test_right_editor_stack_preserves_section_minimums_and_timeline_lanes():
    app, window = _window()
    try:
        window.show()

        for width, height in ((1920, 1080), (1600, 900), (1280, 720)):
            window.resize(width, height)
            app.processEvents()

            assert window.timeline_panel.height() >= window.timeline_panel.minimumHeight()
            assert window.transcript_frame.height() >= window.transcript_frame.minimumHeight()
            assert window.render_log_frame.height() >= window.render_log_frame.minimumHeight()
            assert window.timeline.height() >= window.timeline.required_lane_stack_height()
            assert all(
                top + lane_height <= window.timeline.height()
                for top, lane_height in (
                    window.timeline.lane_rect(name)
                    for name in ("source", "visual", "sfx", "emoji", "voiceover")
                )
            )

        assert window.right_editor_scroll.verticalScrollBar().maximum() > 0
        assert window.right_editor_scroll.horizontalScrollBar().maximum() == 0
        assert window.transcript_list.verticalScrollBar() is not None
        assert window.render_log.verticalScrollBar() is not None
    finally:
        _close(app, window)


def test_timeline_maps_editable_entities_to_the_approved_lanes_and_hit_tests_them():
    app, window = _window()
    try:
        timeline = window.timeline
        timeline.setRange(0, 10_000)
        timeline.fit_source()
        clips = [
            {"id": "motion", "kind": "RECAP_MOTION", "start": 1.0, "end": 2.0},
            {"id": "fx", "kind": "RECAP_VISUAL_FX", "start": 2.0, "end": 3.0},
            {"id": "sfx", "kind": "SFX", "start": 3.0, "end": 4.0},
            {"id": "emoji", "kind": "EMOJI", "start": 4.0, "end": 5.0},
            {"id": "voice", "kind": "VOICEOVER", "start": 5.0, "end": 6.0},
        ]
        timeline.set_asset_clips(clips)

        expected_lanes = {
            "motion": "visual",
            "fx": "visual",
            "sfx": "sfx",
            "emoji": "emoji",
            "voice": "voiceover",
        }
        for clip in clips:
            geometry = timeline.asset_clip_geometry(clip)
            assert geometry is not None
            _x, y, _width, height = geometry
            lane_top, lane_height = timeline.lane_rect(expected_lanes[clip["id"]])
            assert lane_top <= y
            assert y + height <= lane_top + lane_height
            assert timeline.asset_clip_part_at_position(
                geometry[0] + geometry[2] / 2,
                geometry[1] + geometry[3] / 2,
            ) == (clip["kind"], clip["id"], "body")
    finally:
        _close(app, window)
