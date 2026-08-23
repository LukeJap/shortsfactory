from __future__ import annotations

import json
import sys

from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import QListWidgetItem

from ..constants import ROOT, VISUAL_EVENT_PREFIX


class AIVisualPipelineMixin:

    def regenerate_selected_visual_asset(self):

        slot = self.selected_visual_slot()
        if slot is None:
            return

        image_source = self.normalize_visual_image_source(
            slot.get(
                "image_source",
                slot.get(
                    "provider",
                    "FORGE",
                ),
            )
        )
        if image_source == "WEB":
            self.visual_inspector_fields_changed()
            slot = self.selected_visual_slot()
            if slot is None:
                return
            self.start_web_image_search(
                slot
            )
            return

        if image_source == "CHATGPT":
            self.visual_inspector_fields_changed()
            slot = self.selected_visual_slot()
            if slot is None:
                return

            slot["state"] = "GENERATING"
            slot.pop(
                "error",
                None,
            )
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
                ),
                provider="openai",
            )
            return

        if self.image_ai_state != "ready":
            self.visual_status_label.setText(
                "Image AI is offline. Existing image entities are unchanged."
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

        if isinstance(
            deleted_slot,
            dict,
        ):
            tombstone = self.clone_visual_slot(
                deleted_slot
            )
            tombstone["deleted"] = True
            tombstone["enabled"] = False
            tombstone["user_modified"] = True

            tombstone_id = str(
                tombstone.get(
                    "slot_id",
                    "",
                )
                or ""
            )
            self.visual_deleted_slots = [
                existing
                for existing in self.visual_deleted_slots
                if not (
                    isinstance(
                        existing,
                        dict,
                    )
                    and tombstone_id
                    and str(
                        existing.get(
                            "slot_id",
                            "",
                        )
                        or ""
                    )
                    == tombstone_id
                )
            ]
            self.visual_deleted_slots.append(
                tombstone
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

        self.apply_editor_visual_overrides_to_slots()
        self.ensure_visual_slot_defaults()

        replan_mode = self.visual_replan_mode()
        self.pending_visual_replan_mode = replan_mode
        self.pending_visual_preserved_slots = []
        self.pending_visual_preserved_deleted_slots = []
        self.pending_visual_selected_slot_id = None

        selected_slot = self.selected_visual_slot()
        if isinstance(
            selected_slot,
            dict,
        ):
            self.pending_visual_selected_slot_id = str(
                selected_slot.get(
                    "slot_id",
                    "",
                )
                or ""
            ) or None

        if replan_mode == "append":
            # Every existing image is an independent persistent entity.
            # Planning adds new entities; it never needs a KEEP flag.
            self.pending_visual_preserved_slots = [
                self.clone_visual_slot(
                    slot
                )
                for slot in self.visual_plan_slots
                if isinstance(
                    slot,
                    dict,
                )
            ]
            self.ensure_current_editor_asset_context(
                clear_on_change=False
            )
            self.save_ai_visual_plan()
        else:
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
            self.selected_visual_slot_index = None
            self.clear_visual_plan_display()

        self.plan_visuals_button.setEnabled(
            False
        )
        self.plan_visuals_button.setText(
            "Planning..."
        )

        if replan_mode == "append":
            self.visual_status_label.setText(
                "AI is adding new visual entities. Existing images will stay until you delete them."
            )
        else:
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

        planned_slots = [
            slot
            for slot in slots
            if isinstance(
                slot,
                dict,
            )
        ]

        if self.pending_visual_replan_mode == "append":
            self.visual_plan_slots = (
                self.merge_visual_plan_with_preserved_changes(
                    planned_slots
                )
            )
            self.visual_deleted_slots = []
            self.user_visual_edits = bool(
                self.pending_visual_preserved_slots
                or data.get(
                    "user_modified",
                    False,
                )
            )
        else:
            self.visual_plan_slots = planned_slots
            deleted_slots = data.get(
                "deleted_slots",
                [],
            )
            self.visual_deleted_slots = [
                slot
                for slot in deleted_slots
                if isinstance(
                    slot,
                    dict,
                )
            ] if isinstance(
                deleted_slots,
                list,
            ) else []
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

        if (
            self.pending_visual_replan_mode == "append"
            and self.pending_visual_selected_slot_id
        ):
            for index, slot in enumerate(
                self.visual_plan_slots
            ):
                if (
                    isinstance(
                        slot,
                        dict,
                    )
                    and str(
                        slot.get(
                            "slot_id",
                            "",
                        )
                        or ""
                    )
                    == self.pending_visual_selected_slot_id
                ):
                    self.selected_visual_slot_index = index
                    break
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

            preserved_count = (
                len(
                    self.pending_visual_preserved_slots
                )
                if self.pending_visual_replan_mode == "append"
                else 0
            )
            preserved_suffix = (
                (
                    f" Preserved {preserved_count} existing visual "
                    + (
                        "entity."
                        if preserved_count == 1
                        else "entities."
                    )
                )
                if preserved_count
                else ""
            )

            self.visual_status_label.setText(
                (
                    f"{len(self.visual_plan_slots)} proposed cutaway"
                    + (
                        ""
                        if len(self.visual_plan_slots) == 1
                        else "s"
                    )
                    + "."
                    + preserved_suffix
                    + " Click a thumbnail or green timeline block to edit that image entity."
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

            if self.pending_visual_replan_mode == "append":
                self.visual_status_label.setText(
                    "Visual planning failed. Your existing image entities are unchanged."
                )
            else:
                self.visual_status_label.setText(
                    "Visual planning failed. See the render log."
                )

            self.render_log.append(
                f"✕ VISUAL PLANNER FAILED (exit code {exit_code})"
            )
            self.reset_pending_visual_replan_state()

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
            self.reset_pending_visual_replan_state()

            return

        preserved_count = len(
            self.pending_visual_preserved_slots
        )
        deleted_count = len(
            self.pending_visual_preserved_deleted_slots
        )

        self.render_log.append(
            ""
        )

        self.render_log.append(
            (
                f"✓ AI visual plan ready: "
                f"{len(self.visual_plan_slots)} image entity/ies."
            )
        )

        if preserved_count or deleted_count:
            self.render_log.append(
                (
                    "Preserved visual entities: "
                    f"{preserved_count} existing image(s)."
                )
            )

        self.reset_pending_visual_replan_state()


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


