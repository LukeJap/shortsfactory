"""
WebImagesMixin: the web image search dialog for AI visual cutaways --
queries openly-licensed image sources (web_image_sources.py, currently
Openverse) as an alternative to generating images locally/via OpenAI, and
wires a selected result into the same AI visual slot system
ai_visual_slots.py owns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QSize, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..constants import ROOT


class WebImagesMixin:

    def web_image_slot_by_id(
        self,
        slot_id: str,
    ) -> tuple[int | None, dict | None]:

        normalized = str(
            slot_id
            or ""
        )
        for index, slot in enumerate(
            self.visual_plan_slots
        ):
            if not isinstance(
                slot,
                dict,
            ):
                continue
            if str(
                slot.get(
                    "slot_id",
                    "",
                )
                or ""
            ) == normalized:
                return index, slot

        return None, None


    def start_web_image_search(
        self,
        slot: dict,
    ):

        if (
            self.web_image_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        script = (
            ROOT
            / "app"
            / "web_image_sources.py"
        )
        if not script.exists():
            self.visual_status_label.setText(
                "web_image_sources.py is not installed."
            )
            return

        search_query = str(
            slot.get(
                "search_query",
                "",
            )
            or ""
        ).strip()
        label = str(
            slot.get(
                "label",
                "",
            )
            or ""
        ).strip()
        generation_prompt = str(
            slot.get(
                "prompt",
                "",
            )
            or ""
        ).strip()

        # Web search needs literal search terms, not the long AI-generation
        # description. New visual plans provide search_query explicitly; older
        # plans intentionally fall back to the short visual label first.
        query = search_query or label or generation_prompt
        if not query:
            self.visual_status_label.setText(
                "Enter a visual subject before searching the web."
            )
            return

        fallback_queries = []
        if (
            label
            and label.casefold() != query.casefold()
        ):
            fallback_queries.append(
                label
            )

        if not search_query and label:
            slot["search_query"] = label
            self.save_ai_visual_plan()

        slot_id = str(
            slot.get(
                "slot_id",
                "",
            )
            or ""
        )
        if not slot_id:
            self.visual_status_label.setText(
                "Selected visual entity has no stable ID."
            )
            return

        # Do not alter the currently selected image while search is running.
        # The visual entity changes only after the user explicitly chooses a
        # candidate from the result gallery.
        self.web_image_operation = "search"
        self.web_image_target_slot_id = slot_id
        self.web_image_output_buffer = ""
        self.web_image_search_results = []
        try:
            if self.web_image_results_path.exists():
                self.web_image_results_path.unlink()
        except OSError:
            pass

        self.visual_status_label.setText(
            "Searching openly licensed web images..."
        )
        self.render_log.append(
            ""
        )
        self.render_log.append(
            "=== WEB IMAGE SEARCH ==="
        )
        self.render_log.append(
            f"Query: {query}"
        )
        if fallback_queries:
            self.render_log.append(
                "Fallback query: "
                + " | ".join(
                    fallback_queries
                )
            )
        self.update_visual_inspector_buttons()

        args = [
            str(
                script
            ),
            "search",
            "--query",
            query,
            "--page-size",
            "8",
            "--output",
            str(
                self.web_image_results_path
            ),
        ]
        for fallback_query in fallback_queries:
            args.extend(
                [
                    "--fallback-query",
                    fallback_query,
                ]
            )

        self.web_image_process.start(
            sys.executable,
            args,
        )


    def start_web_image_download(
        self,
        slot_id: str,
        result_index: int,
    ):

        if (
            self.web_image_process.state()
            != QProcess.ProcessState.NotRunning
        ):
            return

        script = (
            ROOT
            / "app"
            / "web_image_sources.py"
        )
        if not script.exists():
            self.visual_status_label.setText(
                "web_image_sources.py is not installed."
            )
            return

        self.web_image_operation = "download"
        self.web_image_target_slot_id = str(
            slot_id
            or ""
        )
        self.web_image_output_buffer = ""
        try:
            if self.web_image_selection_path.exists():
                self.web_image_selection_path.unlink()
        except OSError:
            pass

        self.visual_status_label.setText(
            "Downloading selected web image..."
        )
        self.update_visual_inspector_buttons()

        self.web_image_process.start(
            sys.executable,
            [
                str(
                    script
                ),
                "download",
                "--results",
                str(
                    self.web_image_results_path
                ),
                "--index",
                str(
                    int(
                        result_index
                    )
                ),
                "--slot-id",
                self.web_image_target_slot_id,
                "--output",
                str(
                    self.web_image_selection_path
                ),
            ],
        )


    def read_web_image_output(self):

        data = (
            self.web_image_process
            .readAllStandardOutput()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        if not data:
            return

        self.web_image_output_buffer += data
        visible_lines = [
            line
            for line in data.splitlines()
            if not line.strip().startswith(
                "SF_WEB_IMAGE_EVENT "
            )
        ]
        if visible_lines:
            self.render_log.append(
                "\n".join(
                    visible_lines
                )
            )


    def read_web_image_error(self):

        data = (
            self.web_image_process
            .readAllStandardError()
            .data()
            .decode(
                "utf-8",
                errors="replace",
            )
        )
        if data:
            self.web_image_output_buffer += data
            self.render_log.append(
                data.strip()
            )


    def show_web_image_results_dialog(
        self,
        slot_id: str,
        query: str,
    ):

        results = self.web_image_search_results
        if not results:
            self.visual_status_label.setText(
                "No commercially usable Openverse images matched these search terms."
            )
            return

        dialog = QDialog(
            self
        )
        dialog.setWindowTitle(
            "Choose Web Image"
        )
        dialog.setMinimumSize(
            660,
            540,
        )

        layout = QVBoxLayout(
            dialog
        )
        layout.setContentsMargins(
            14,
            14,
            14,
            14,
        )
        layout.setSpacing(
            10
        )

        heading = QLabel(
            "OPENLY LICENSED IMAGE RESULTS"
        )
        heading.setObjectName(
            "SectionTitle"
        )
        query_label = QLabel(
            f"Search: {query}"
        )
        query_label.setWordWrap(
            True
        )
        license_hint = QLabel(
            "Results are filtered to licenses compatible with commercial use. "
            "CC BY / BY-SA images may require attribution; source and license "
            "metadata are saved with the visual entity."
        )
        license_hint.setObjectName(
            "HintLabel"
        )
        license_hint.setWordWrap(
            True
        )

        result_list = QListWidget()
        result_list.setObjectName(
            "TranscriptList"
        )

        for index, result in enumerate(
            results
        ):
            item = QListWidgetItem()
            item.setData(
                Qt.ItemDataRole.UserRole,
                index,
            )
            item.setSizeHint(
                QSize(
                    560,
                    94,
                )
            )
            result_list.addItem(
                item
            )

            row = QWidget()
            row_layout = QHBoxLayout(
                row
            )
            row_layout.setContentsMargins(
                6,
                5,
                6,
                5,
            )
            row_layout.setSpacing(
                10
            )

            thumb = QLabel(
                "NO\nTHUMB"
            )
            thumb.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            thumb.setFixedSize(
                70,
                78,
            )
            thumbnail_path = Path(
                str(
                    result.get(
                        "thumbnail_path",
                        "",
                    )
                    or ""
                )
            )
            if thumbnail_path.exists():
                pixmap = QPixmap(
                    str(
                        thumbnail_path
                    )
                )
                if not pixmap.isNull():
                    thumb.setText(
                        ""
                    )
                    thumb.setPixmap(
                        pixmap.scaled(
                            thumb.size(),
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )

            title = str(
                result.get(
                    "title",
                    "Untitled image",
                )
                or "Untitled image"
            )
            creator = str(
                result.get(
                    "creator",
                    "",
                )
                or "Unknown creator"
            )
            license_name = str(
                result.get(
                    "license",
                    "",
                )
                or "Unknown license"
            )
            source_name = str(
                result.get(
                    "provider_source",
                    "openverse",
                )
                or "openverse"
            )
            details = QLabel(
                f"{title}\n{creator}  •  {license_name}  •  {source_name}"
            )
            details.setWordWrap(
                True
            )

            row_layout.addWidget(
                thumb
            )
            row_layout.addWidget(
                details,
                1,
            )
            result_list.setItemWidget(
                item,
                row,
            )

        if result_list.count() > 0:
            result_list.setCurrentRow(
                0
            )

        action_row = QHBoxLayout()
        open_source_button = QPushButton(
            "OPEN SOURCE PAGE"
        )
        open_source_button.setObjectName(
            "QuietButton"
        )
        cancel_button = QPushButton(
            "CANCEL"
        )
        cancel_button.setObjectName(
            "QuietButton"
        )
        use_button = QPushButton(
            "USE SELECTED IMAGE"
        )
        use_button.setObjectName(
            "GenerateButton"
        )

        open_source_button.clicked.connect(
            lambda checked=False: self.open_selected_web_image_source(
                result_list
            )
        )
        cancel_button.clicked.connect(
            dialog.reject
        )
        use_button.clicked.connect(
            lambda checked=False: self.choose_web_image_result(
                dialog,
                result_list,
                slot_id,
            )
        )
        result_list.itemDoubleClicked.connect(
            lambda item: self.choose_web_image_result(
                dialog,
                result_list,
                slot_id,
            )
        )

        action_row.addWidget(
            open_source_button
        )
        action_row.addStretch()
        action_row.addWidget(
            cancel_button
        )
        action_row.addWidget(
            use_button
        )

        layout.addWidget(
            heading
        )
        layout.addWidget(
            query_label
        )
        layout.addWidget(
            license_hint
        )
        layout.addWidget(
            result_list,
            1,
        )
        layout.addLayout(
            action_row
        )

        dialog.exec()


    def selected_web_image_result(
        self,
        result_list: QListWidget,
    ) -> tuple[int | None, dict | None]:

        item = result_list.currentItem()
        if item is None:
            return None, None

        try:
            index = int(
                item.data(
                    Qt.ItemDataRole.UserRole
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return None, None

        if not (
            0
            <= index
            < len(
                self.web_image_search_results
            )
        ):
            return None, None

        result = self.web_image_search_results[
            index
        ]
        return (
            index,
            result
            if isinstance(
                result,
                dict,
            )
            else None,
        )


    def open_selected_web_image_source(
        self,
        result_list: QListWidget,
    ):

        _index, result = self.selected_web_image_result(
            result_list
        )
        if result is None:
            return

        source_url = str(
            result.get(
                "foreign_landing_url",
                "",
            )
            or result.get(
                "detail_url",
                "",
            )
            or result.get(
                "source_url",
                "",
            )
            or ""
        ).strip()
        if source_url:
            QDesktopServices.openUrl(
                QUrl(
                    source_url
                )
            )


    def choose_web_image_result(
        self,
        dialog: QDialog,
        result_list: QListWidget,
        slot_id: str,
    ):

        index, result = self.selected_web_image_result(
            result_list
        )
        if index is None or result is None:
            self.visual_status_label.setText(
                "Choose a web image first."
            )
            return

        dialog.accept()
        self.start_web_image_download(
            slot_id,
            index,
        )


    def apply_web_image_selection(self) -> bool:

        try:
            data = json.loads(
                self.web_image_selection_path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            json.JSONDecodeError,
        ):
            return False

        slot_id = str(
            data.get(
                "slot_id",
                self.web_image_target_slot_id,
            )
            or self.web_image_target_slot_id
            or ""
        )
        result = data.get(
            "result",
            {},
        )
        if not isinstance(
            result,
            dict,
        ):
            return False

        local_path = Path(
            str(
                result.get(
                    "local_path",
                    "",
                )
                or ""
            )
        )
        if not local_path.exists():
            return False

        index, slot = self.web_image_slot_by_id(
            slot_id
        )
        if slot is None or index is None:
            return False

        title = str(
            result.get(
                "title",
                "Web image",
            )
            or "Web image"
        )
        creator = str(
            result.get(
                "creator",
                "",
            )
            or ""
        )
        license_name = str(
            result.get(
                "license",
                "",
            )
            or "Unknown license"
        )
        landing_url = str(
            result.get(
                "foreign_landing_url",
                "",
            )
            or result.get(
                "detail_url",
                "",
            )
            or ""
        )

        slot["asset_path"] = str(
            local_path
        )
        slot["image_source"] = "WEB"
        slot["provider"] = "openverse"
        slot["source_type"] = "web_sourced"
        slot["generated"] = False
        slot["state"] = "READY"
        slot["active_variant_id"] = "web_selected"
        slot["variants"] = [
            {
                "variant_id": "web_selected",
                "path": str(
                    local_path
                ),
                "state": "READY",
                "provider": "openverse",
                "generated": False,
                "saved": False,
            }
        ]
        slot["web_source"] = {
            "title": title,
            "creator": creator,
            "license": license_name,
            "license_name": str(
                result.get(
                    "license_name",
                    "",
                )
                or ""
            ),
            "license_version": str(
                result.get(
                    "license_version",
                    "",
                )
                or ""
            ),
            "provider_source": str(
                result.get(
                    "provider_source",
                    "openverse",
                )
                or "openverse"
            ),
            "source_url": str(
                result.get(
                    "source_url",
                    "",
                )
                or ""
            ),
            "landing_url": landing_url,
        }
        slot.pop(
            "error",
            None,
        )
        self.mark_visual_slot_modified(
            slot
        )
        self.selected_visual_slot_index = index
        self.save_ai_visual_plan()
        self.sync_visual_slot_to_editor_asset_plan(
            index
        )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()
        self.active_visual_preview_signature = None
        self.active_visual_preview_layout_signature = None
        self.update_ai_visual_preview_overlay(
            self.player.position()
        )

        attribution = (
            f" · {creator}"
            if creator
            else ""
        )
        self.visual_status_label.setText(
            f"Web image ready: {title}{attribution} · {license_name}"
        )
        return True


    def web_image_finished(
        self,
        exit_code: int,
        exit_status,
    ):

        del exit_status

        operation = self.web_image_operation
        target_slot_id = self.web_image_target_slot_id
        self.web_image_operation = ""
        self.update_visual_inspector_buttons()

        if exit_code != 0:
            self.visual_status_label.setText(
                (
                    "Web image search failed. See render log."
                    if operation == "search"
                    else "Web image download failed. See render log."
                )
            )
            self.render_log.append(
                f"✕ WEB IMAGE {operation.upper()} FAILED (exit code {exit_code})"
            )
            return

        if operation == "search":
            try:
                data = json.loads(
                    self.web_image_results_path.read_text(
                        encoding="utf-8"
                    )
                )
            except (
                OSError,
                json.JSONDecodeError,
            ):
                self.visual_status_label.setText(
                    "Could not read web image search results."
                )
                return

            results = data.get(
                "results",
                [],
            )
            self.web_image_search_results = [
                item
                for item in results
                if isinstance(
                    item,
                    dict,
                )
            ] if isinstance(
                results,
                list,
            ) else []
            query = str(
                data.get(
                    "query",
                    "",
                )
                or ""
            )
            if not self.web_image_search_results:
                self.visual_status_label.setText(
                    "No commercially usable Openverse images matched these search terms."
                )
                return

            self.visual_status_label.setText(
                f"Choose from {len(self.web_image_search_results)} web image results."
            )
            self.show_web_image_results_dialog(
                target_slot_id,
                query,
            )
            return

        if operation == "download":
            if self.apply_web_image_selection():
                self.render_log.append(
                    "✓ Web image attached to visual entity."
                )
            else:
                self.visual_status_label.setText(
                    "Downloaded web image could not be attached to the visual entity."
                )

