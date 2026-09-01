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
        assert window.video_widget.parentWidget() is window.program_monitor_composition
        assert window.program_monitor_composition.parentWidget() is window.program_monitor_viewport
        assert window.program_monitor_composition.background_preview.parentWidget() is window.program_monitor_composition
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

        composition = window.program_monitor_composition
        composition_rect = composition.rect()
        composition_rect.moveTopLeft(composition.mapTo(window, QPoint(0, 0)))
        transport_rect = window.play_button.rect()
        transport_rect.moveTopLeft(window.play_button.mapTo(window, QPoint(0, 0)))
        selection_rect = window.start_button.rect()
        selection_rect.moveTopLeft(window.start_button.mapTo(window, QPoint(0, 0)))

        assert composition.width() > 320
        assert composition.height() > 560
        assert abs(composition.width() * 16 - composition.height() * 9) <= 16
        assert window.video_widget.width() <= composition.width()
        assert window.video_widget.height() < composition.height()
        assert not composition_rect.intersects(transport_rect)
        assert not composition_rect.intersects(selection_rect)
    finally:
        _close(app, window)


def test_program_monitor_active_picture_crop_fills_width_without_side_matte():
    app, window = _window()
    try:
        window.resize(1700, 1000)
        window.show()
        composition = window.program_monitor_composition
        composition.set_active_picture_rect(
            {"x": 240, "y": 0, "width": 1440, "height": 1080},
            source_width=1920,
            source_height=1080,
        )
        app.processEvents()

        foreground = window.video_widget.geometry()
        assert foreground.x() == 0
        assert foreground.width() == composition.width()
        assert foreground.height() == round(composition.width() * 1080 / 1440)
        assert foreground.y() == (composition.height() - foreground.height()) // 2
        assert foreground.height() > round(composition.width() * 9 / 16)
    finally:
        _close(app, window)


def test_program_monitor_title_and_preview_only_shorts_shell_are_independent():
    app, window = _window()
    try:
        window.save_editor_asset_plan_state = lambda: None
        window.editor_asset_plan = {"version": 1, "clips": []}
        window.resize(1600, 900)
        window.show()
        app.processEvents()

        window.persistent_title_input.setText("Gary chooses Patrick")
        app.processEvents()

        assert window.persistent_title_preview_label.isVisible()
        assert window.persistent_title_preview_label.text() == "Gary chooses Patrick"
        assert not window.youtube_shorts_mock_overlay.isVisible()
        assert window.editor_asset_plan["persistent_title"] == {
            "text": "Gary chooses Patrick"
        }

        window.youtube_ui_preview_toggle.setChecked(True)
        app.processEvents()
        assert window.youtube_shorts_mock_overlay.isVisible()
        assert window.persistent_title_preview_label.isVisible()

        window.youtube_ui_preview_toggle.setChecked(False)
        app.processEvents()
        assert not window.youtube_shorts_mock_overlay.isVisible()
        assert window.persistent_title_preview_label.isVisible()
    finally:
        _close(app, window)


def test_program_monitor_title_selection_has_editor_only_handles_and_keeps_mock_ui_visible():
    app, window = _window()
    try:
        window.save_editor_asset_plan_state = lambda: None
        window.editor_asset_plan = {"version": 1, "clips": []}
        window.resize(1600, 900)
        window.show()
        window.persistent_title_input.setText("Gary chooses Patrick")
        window.youtube_ui_preview_toggle.setChecked(True)
        app.processEvents()

        window.select_persistent_title_preview(True)
        app.processEvents()

        assert window.youtube_shorts_mock_overlay.isVisible()
        assert window.youtube_shorts_mock_overlay.geometry() == window.program_monitor_composition.contentsRect()
        assert window.youtube_shorts_mock_overlay.parentWidget() is window.program_monitor_composition
        assert window.persistent_title_preview_label.parentWidget() is window.program_monitor_composition
        assert window.caption_preview_label.parentWidget() is window.program_monitor_composition
        assert window.persistent_title_preview_label.isVisible()
        assert all(
            handle.isVisible()
            for handle in window.persistent_title_resize_handles.values()
        )
        assert "dashed" in window.persistent_title_preview_label.styleSheet()
    finally:
        _close(app, window)


def test_program_monitor_title_scale_changes_glyphs_without_expanding_wrap_width():
    app, window = _window()
    try:
        window.save_editor_asset_plan_state = lambda: None
        window.editor_asset_plan = {
            "version": 1,
            "clips": [],
            "persistent_title": {
                "text": "Gary chooses Patrick",
                "width": 0.7,
            },
        }
        window.resize(1600, 900)
        window.show()
        window.update_persistent_title_preview()
        app.processEvents()

        label = window.persistent_title_preview_label
        baseline_font_size = label.font().pixelSize()
        baseline_width = label.width()

        window.editor_asset_plan["persistent_title"]["scale"] = 1.5
        window.update_persistent_title_preview()
        app.processEvents()

        assert label.font().pixelSize() > baseline_font_size
        assert label.width() == baseline_width
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
        window.save_editor_asset_plan_state = lambda: None
        window.editor_asset_plan = {"version": 1, "clips": []}
        window.persistent_title_input.setText("A recap title")
        window.youtube_ui_preview_toggle.setChecked(True)
        app.processEvents()

        recap_viewport = window.recap_scroll_area.viewport()
        assert window.recap_frame.width() <= recap_viewport.width()
        right_stack = window.right_editor_content.layout()
        assert right_stack.itemAt(0).widget() is window.timeline_panel
        assert right_stack.itemAt(1).widget() is window.transcript_frame
        assert right_stack.itemAt(2).widget() is window.render_log_frame
        assert window.program_monitor_composition.height() > 500
        assert window.persistent_title_preview_label.isVisible()
        assert window.youtube_shorts_mock_overlay.isVisible()
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
        assert timeline.maximumHeight() == timeline.required_lane_stack_height()
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
            assert window.timeline.height() == window.timeline.required_lane_stack_height()

        assert window.right_editor_scroll.verticalScrollBar().maximum() > 0
        assert window.right_editor_scroll.horizontalScrollBar().maximum() == 0
        assert window.transcript_list.verticalScrollBar() is not None
        assert window.render_log.verticalScrollBar() is not None
    finally:
        _close(app, window)


def test_hidden_timeline_inspector_does_not_reserve_editor_height():
    app, window = _window()
    try:
        window.resize(1920, 1080)
        window.show()
        app.processEvents()

        panel = window.timeline_panel
        expected_hidden_height = (
            window.timeline.required_lane_stack_height()
            + window.timeline_tools.minimumSize().height()
            + window.timeline_footer.minimumSizeHint().height()
            + (window.timeline_panel_layout.spacing() * 2)
        )

        assert not window.timeline_item_inspector.isVisible()
        assert panel.minimumHeight() < (
            expected_hidden_height
            + window.timeline_item_inspector.minimumSizeHint().height()
        )

        hidden_minimum = panel.minimumHeight()
        window.selected_timeline_item_kind = "SOURCE"
        window.update_timeline_item_inspector()
        app.processEvents()

        assert window.timeline_item_inspector.isVisible()
        assert panel.minimumHeight() > hidden_minimum

        window.selected_timeline_item_kind = ""
        window.update_timeline_item_inspector()
        app.processEvents()
        assert not window.timeline_item_inspector.isVisible()
        assert hidden_minimum <= panel.minimumHeight() <= expected_hidden_height
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


def _timeline_inspector_clips():
    return [
        {
            "id": "motion",
            "kind": "RECAP_MOTION",
            "label": "Push in",
            "start": 1.0,
            "end": 2.0,
            "active": True,
        },
        {
            "id": "fx",
            "kind": "RECAP_VISUAL_FX",
            "label": "Impact",
            "start": 2.0,
            "end": 3.0,
            "active": True,
        },
        {
            "id": "sfx",
            "kind": "SFX",
            "label": "Whoosh",
            "asset_filename": "whoosh.wav",
            "start": 3.0,
            "end": 3.6,
            "active": True,
        },
        {
            "id": "emoji",
            "kind": "EMOJI",
            "label": "Laugh",
            "emoji": "laugh",
            "start": 4.0,
            "end": 5.0,
            "position_x": 0.25,
            "position_y": 0.55,
            "scale": 1.0,
            "active": True,
        },
        {
            "id": "voice",
            "kind": "VOICEOVER",
            "label": "Narration 1",
            "start": 5.0,
            "end": 6.0,
            "active": True,
        },
    ]


def test_timeline_item_inspector_switches_by_selected_asset_kind_and_clears():
    app, window = _window()
    try:
        clips = _timeline_inspector_clips()
        window.editor_asset_plan = {"version": 1, "clips": clips}
        window.timeline.set_asset_clips(clips)

        window.editor_asset_clip_selected("SFX", "sfx")
        assert not window.timeline_item_inspector.isHidden()
        assert window.timeline_item_inspector_header.text().startswith("SELECTED: SFX")
        assert "whoosh.wav" in window.timeline_item_inspector_summary.text()
        assert not window.timeline_inspector_emoji_controls.isVisible()

        window.editor_asset_clip_selected("EMOJI", "emoji")
        assert window.timeline_item_inspector_header.text().startswith("SELECTED: EMOJI")
        assert not window.timeline_inspector_emoji_controls.isHidden()
        assert window.timeline_inspector_emoji_x.value() == 0.25

        window.editor_asset_clip_selected("RECAP_MOTION", "motion")
        assert window.timeline_item_inspector_header.text().startswith("SELECTED: SMART MOTION")
        assert window.timeline_inspector_emoji_controls.isHidden()

        window.editor_asset_clip_selected("RECAP_VISUAL_FX", "fx")
        assert window.timeline_item_inspector_header.text().startswith("SELECTED: VISUAL FX")

        window.editor_asset_clip_selected("VOICEOVER", "voice")
        assert window.timeline_item_inspector_header.text().startswith("SELECTED: VOICEOVER")
        assert window.timeline_inspector_start.isHidden()
        assert "Read-only narration" in window.timeline_item_inspector_summary.text()

        window.editor_asset_clip_selected("SOURCE", "")
        assert window.timeline_item_inspector_header.text().startswith("SELECTED: SOURCE")
        assert "Read-only source selection" in window.timeline_item_inspector_summary.text()

        window.timeline.assetClipSelected.emit("", "")
        assert window.timeline_item_inspector.isHidden()
        assert window.timeline.selected_asset_clip_id is None
    finally:
        _close(app, window)


def test_timeline_item_inspector_preserves_manual_override_for_existing_emoji_edits():
    app, window = _window()
    try:
        clips = _timeline_inspector_clips()
        window.editor_asset_plan = {"version": 1, "clips": clips}
        window.timeline.set_asset_clips(clips)
        window.save_editor_asset_plan_state = lambda: None
        window.editor_asset_clip_selected("EMOJI", "emoji")

        window.timeline_inspector_start.setValue(4.2)
        window.timeline_inspector_emoji_x.setValue(0.4)
        window.timeline_inspector_enabled.setChecked(False)

        clip = window.find_editor_clip("EMOJI", "emoji")
        assert clip is not None
        assert clip["start"] == 4.2
        assert clip["position_x"] == 0.4
        assert clip["active"] is False
        assert clip["manual_override"] is True
        assert clip["locked"] is True
        assert window.timeline.selected_asset_clip_id == "emoji"
    finally:
        _close(app, window)
