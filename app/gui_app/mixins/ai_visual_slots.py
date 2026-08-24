"""
AIVisualSlotsMixin: owns the AI visual cutaway "slot" data model and the
right-panel inspector UI (position/scale/display-mode sliders, variant
keep/generate-more workflow, replan mode). Largest file in gui_app.
Writes/reads output/ai_visual_plan.json (save_ai_visual_plan()) and
mirrors slot state into output/editor_asset_plan.json
(sync_visual_slot_to_editor_asset_plan()) so the timeline and the live
preview overlay (ai_visual_preview.py) both see the same data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from editor_asset_plan import (
    clips_of_kind,
    load_editor_asset_plan,
    replace_kind_clips,
    set_editor_plan_context,
    upsert_clip,
)

from ..constants import ROOT
from ..helpers import format_time


class AIVisualSlotsMixin:

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

        normalized_slots: list[dict] = []

        for index, raw_slot in enumerate(
            self.visual_plan_slots,
            start=1,
        ):
            if not isinstance(
                raw_slot,
                dict,
            ):
                continue

            slot = raw_slot

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
            slot.setdefault(
                "position_x",
                0.0,
            )
            slot.setdefault(
                "position_y",
                0.0,
            )
            slot["image_source"] = self.normalize_visual_image_source(
                slot.get(
                    "image_source",
                    slot.get(
                        "provider",
                        "FORGE",
                    ),
                )
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

            variants = slot.get(
                "variants",
                [],
            )
            variants = [
                dict(
                    variant
                )
                for variant in variants
                if isinstance(
                    variant,
                    dict,
                )
                and str(
                    variant.get(
                        "path",
                        "",
                    )
                    or ""
                ).strip()
            ] if isinstance(
                variants,
                list,
            ) else []

            # KEEP is no longer part of the visual model. Every image entity
            # persists until the user deletes it. Older saved variants are
            # migrated into independent visual entities below.
            for variant in variants:
                variant["saved"] = False
            slot.pop(
                "saved_variant",
                None,
            )

            if len(
                variants
            ) <= 1:
                if variants:
                    variant = variants[0]
                    slot["variants"] = [
                        variant
                    ]
                    slot["active_variant_id"] = str(
                        variant.get(
                            "variant_id",
                            "variant_001",
                        )
                        or "variant_001"
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
                normalized_slots.append(
                    slot
                )
                continue

            base_id = str(
                slot.get(
                    "slot_id",
                    f"visual_{index:02d}",
                )
                or f"visual_{index:02d}"
            )
            active_variant_id = str(
                slot.get(
                    "active_variant_id",
                    "",
                )
                or ""
            )

            ordered_variants = sorted(
                variants,
                key=lambda variant: (
                    0
                    if str(
                        variant.get(
                            "variant_id",
                            "",
                        )
                        or ""
                    ) == active_variant_id
                    else 1,
                    str(
                        variant.get(
                            "variant_id",
                            "",
                        )
                        or ""
                    ),
                ),
            )

            for variant_index, variant in enumerate(
                ordered_variants
            ):
                entity = self.clone_visual_slot(
                    slot
                )
                variant_id = str(
                    variant.get(
                        "variant_id",
                        f"variant_{variant_index + 1:03d}",
                    )
                    or f"variant_{variant_index + 1:03d}"
                )
                entity["slot_id"] = (
                    base_id
                    if variant_index == 0
                    else f"{base_id}__{variant_id}"
                )
                if variant_index > 0:
                    entity["label"] = (
                        str(
                            slot.get(
                                "label",
                                "AI Visual",
                            )
                            or "AI Visual"
                        )
                        + f" ALT {variant_index + 1}"
                    )
                entity["variants"] = [
                    variant
                ]
                entity["enabled"] = (
                    bool(
                        slot.get(
                            "enabled",
                            True,
                        )
                    )
                    if variant_index == 0
                    else False
                )
                entity["active_variant_id"] = variant_id
                entity["asset_path"] = str(
                    variant.get(
                        "path",
                        "",
                    )
                    or ""
                )
                entity["state"] = str(
                    variant.get(
                        "state",
                        entity.get(
                            "state",
                            "READY",
                        ),
                    )
                    or entity.get(
                        "state",
                        "READY",
                    )
                )
                entity["provider"] = str(
                    variant.get(
                        "provider",
                        entity.get(
                            "provider",
                            "",
                        ),
                    )
                    or ""
                )
                entity["generated"] = bool(
                    variant.get(
                        "generated",
                        entity.get(
                            "generated",
                            False,
                        ),
                    )
                )
                entity["user_modified"] = True
                normalized_slots.append(
                    entity
                )

        self.visual_plan_slots = normalized_slots


    def visual_clip_id(self, slot: dict, index: int) -> str:
        slot_id = str(slot.get("slot_id", "") or "")
        return f"visual:{slot_id or f'visual_{index + 1:02d}'}"


    def visual_slot_asset_path_text(self, slot: dict) -> str:

        direct_path = str(
            slot.get(
                "asset_path",
                "",
            )
            or ""
        ).strip()
        if direct_path:
            return direct_path

        variants = slot.get(
            "variants",
            [],
        )
        if isinstance(
            variants,
            list,
        ):
            for variant in variants:
                if not isinstance(
                    variant,
                    dict,
                ):
                    continue
                path = str(
                    variant.get(
                        "path",
                        "",
                    )
                    or ""
                ).strip()
                if path:
                    return path

        return ""


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
            "position_x": round(
                self.coerce_visual_position(
                    slot.get("position_x", 0.0)
                ),
                3,
            ),
            "position_y": round(
                self.coerce_visual_position(
                    slot.get("position_y", 0.0)
                ),
                3,
            ),
            "source_type": str(slot.get("source_type", "ai_generated") or "ai_generated"),
            "image_source": self.normalize_visual_image_source(
                slot.get(
                    "image_source",
                    slot.get(
                        "provider",
                        "FORGE",
                    ),
                )
            ),
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
            if clip.get("image_source") is not None:
                slot["image_source"] = self.normalize_visual_image_source(
                    clip.get(
                        "image_source",
                        "FORGE",
                    )
                )
            if clip.get("scale") is not None:
                try:
                    slot["scale"] = float(clip["scale"])
                except (TypeError, ValueError):
                    pass
            if clip.get("position_x") is not None:
                slot["position_x"] = self.coerce_visual_position(
                    clip.get("position_x", 0.0)
                )
            if clip.get("position_y") is not None:
                slot["position_y"] = self.coerce_visual_position(
                    clip.get("position_y", 0.0)
                )
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
        # visual_plan_slots is the authoritative entity list. Replacing this
        # kind exactly prevents stale/duplicate clips from surviving while
        # also guaranteeing that every existing entity remains until the user
        # explicitly deletes it from visual_plan_slots.
        self.editor_asset_plan = replace_kind_clips(
            self.editor_asset_plan,
            "AI_VISUAL",
            clips,
            preserve_manual=False,
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

        return bool(
            self.visual_deleted_slots
        ) or any(
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


    def clone_visual_slot(self, slot: dict) -> dict:

        try:
            return json.loads(
                json.dumps(
                    slot,
                    ensure_ascii=False,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return dict(slot)


    def visual_slot_time_bounds(
        self,
        slot: dict,
    ) -> tuple[float, float]:

        try:
            start = float(
                slot.get(
                    "start",
                    0.0,
                )
                or 0.0
            )
        except (
            TypeError,
            ValueError,
        ):
            start = 0.0

        try:
            end = float(
                slot.get(
                    "end",
                    start,
                )
                or start
            )
        except (
            TypeError,
            ValueError,
        ):
            end = start

        return (
            start,
            max(
                start,
                end,
            ),
        )


    def visual_slots_conflict(
        self,
        first: dict,
        second: dict,
        padding: float = 1.25,
    ) -> bool:

        first_id = str(
            first.get(
                "slot_id",
                "",
            )
            or ""
        )
        second_id = str(
            second.get(
                "slot_id",
                "",
            )
            or ""
        )

        if (
            first_id
            and second_id
            and first_id == second_id
        ):
            return True

        first_start, first_end = self.visual_slot_time_bounds(
            first
        )
        second_start, second_end = self.visual_slot_time_bounds(
            second
        )

        if (
            first_end + padding >= second_start
            and second_end + padding >= first_start
        ):
            return True

        first_center = (
            first_start
            + first_end
        ) / 2.0
        second_center = (
            second_start
            + second_end
        ) / 2.0

        return abs(
            first_center
            - second_center
        ) <= max(
            2.25,
            padding,
        )


    def unique_visual_entity_id(
        self,
        preferred: str,
        existing_ids: set[str],
    ) -> str:

        base = str(
            preferred
            or "visual"
        ).strip() or "visual"

        if base not in existing_ids:
            existing_ids.add(
                base
            )
            return base

        suffix = 2
        while True:
            candidate = f"{base}__{suffix:02d}"
            if candidate not in existing_ids:
                existing_ids.add(
                    candidate
                )
                return candidate
            suffix += 1


    def place_new_visual_entity(
        self,
        slot: dict,
        existing: list[dict],
    ) -> dict:

        candidate = self.clone_visual_slot(
            slot
        )
        start, end = self.visual_slot_time_bounds(
            candidate
        )
        duration = max(
            0.8,
            end - start,
        )
        selection_start = self.start_ms / 1000
        selection_end = self.end_ms / 1000

        def conflicts(
            proposed_start: float,
            proposed_end: float,
        ) -> bool:
            return any(
                proposed_start
                < self.visual_slot_time_bounds(
                    item
                )[1]
                and proposed_end
                > self.visual_slot_time_bounds(
                    item
                )[0]
                for item in existing
                if isinstance(
                    item,
                    dict,
                )
                and item.get(
                    "enabled",
                    True,
                ) is not False
            )

        if not conflicts(
            start,
            end,
        ):
            return candidate

        cursor = max(
            selection_start,
            start,
        )
        sorted_existing = sorted(
            [
                item
                for item in existing
                if isinstance(
                    item,
                    dict,
                )
            ],
            key=lambda item: self.visual_slot_time_bounds(
                item
            )[0],
        )

        for item in sorted_existing:
            item_start, item_end = self.visual_slot_time_bounds(
                item
            )
            if item_end <= cursor:
                continue
            if cursor + duration <= item_start:
                break
            cursor = max(
                cursor,
                item_end + 0.15,
            )

        if cursor + duration <= selection_end:
            candidate["start"] = round(
                cursor,
                3,
            )
            candidate["end"] = round(
                cursor + duration,
                3,
            )
            candidate["duration"] = round(
                duration,
                3,
            )
            return candidate

        cursor = min(
            selection_end - duration,
            start,
        )
        for item in reversed(
            sorted_existing
        ):
            item_start, item_end = self.visual_slot_time_bounds(
                item
            )
            if item_start >= cursor + duration:
                continue
            if item_end <= cursor:
                break
            cursor = min(
                cursor,
                item_start - duration - 0.15,
            )

        if (
            cursor >= selection_start
            and cursor + duration <= selection_end
        ):
            candidate["start"] = round(
                cursor,
                3,
            )
            candidate["end"] = round(
                cursor + duration,
                3,
            )
            candidate["duration"] = round(
                duration,
                3,
            )

        return candidate


    def merge_visual_plan_with_preserved_changes(
        self,
        planned_slots: list[dict],
    ) -> list[dict]:

        merged = [
            self.clone_visual_slot(
                slot
            )
            for slot in self.pending_visual_preserved_slots
            if isinstance(
                slot,
                dict,
            )
        ]

        existing_ids = {
            str(
                slot.get(
                    "slot_id",
                    "",
                )
                or ""
            )
            for slot in merged
            if isinstance(
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
        }

        for planned in planned_slots:
            if not isinstance(
                planned,
                dict,
            ):
                continue

            entity = self.place_new_visual_entity(
                planned,
                merged,
            )
            preferred_id = str(
                entity.get(
                    "slot_id",
                    "visual",
                )
                or "visual"
            )
            entity["slot_id"] = self.unique_visual_entity_id(
                preferred_id,
                existing_ids,
            )
            entity["user_modified"] = False
            entity.pop(
                "saved_variant",
                None,
            )
            variants = entity.get(
                "variants",
                [],
            )
            if isinstance(
                variants,
                list,
            ):
                for variant in variants:
                    if isinstance(
                        variant,
                        dict,
                    ):
                        variant["saved"] = False

            merged.append(
                entity
            )

        merged.sort(
            key=lambda slot: self.visual_slot_time_bounds(
                slot
            )[0]
        )

        return merged


    def visual_replan_mode(self) -> str:

        return (
            "append"
            if self.visual_plan_slots
            else "replace"
        )


    def reset_pending_visual_replan_state(self):

        self.pending_visual_replan_mode = "replace"
        self.pending_visual_preserved_slots = []
        self.pending_visual_preserved_deleted_slots = []
        self.pending_visual_selected_slot_id = None


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
        payload["deleted_slots"] = self.visual_deleted_slots

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

            slot_id = str(
                slot.get(
                    "slot_id",
                    "",
                )
                or ""
            )
            asset = by_id.get(
                slot_id
            )
            if asset is None and not slot_id:
                asset = by_index.get(
                    index
                )

            if asset is None:
                continue

            if (
                slot.get(
                    "user_modified"
                )
                and (
                    slot.get(
                        "asset_path"
                    )
                    or self.visual_variants(
                        slot
                    )
                )
            ):
                # Manual visual choices are authoritative. The manifest can
                # populate automatic slots, but must not silently switch a
                # user's kept/selected image or variant.
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
            64,
            86,
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
                            64,
                            86,
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
        meta = QLabel(
            (
                f"{format_time(int(start * 1000))} -> "
                f"{format_time(int(end * 1000))}    "
                f"{state_text}    "
                f"{display_mode} / {scale_percent}%"
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
                    102,
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
            self.visual_image_source_combo,
            self.visual_display_mode_combo,
            self.visual_prompt_edit,
            self.generate_more_visual_button,
            self.disable_visual_button,
            self.delete_visual_button,
        ):
            widget.setEnabled(
                enabled
            )

        self.visual_scale_slider.setEnabled(
            enabled
        )
        self.visual_x_slider.setEnabled(
            enabled
        )
        self.visual_y_slider.setEnabled(
            enabled
        )

        slot = self.selected_visual_slot()
        image_source = self.normalize_visual_image_source(
            slot.get(
                "image_source",
                slot.get(
                    "provider",
                    "FORGE",
                ),
            )
            if slot
            else "FORGE"
        )
        source_available = (
            image_source == "WEB"
            or (
                image_source == "FORGE"
                and self.image_ai_state == "ready"
            )
        )
        self.regenerate_visual_button.setEnabled(
            enabled
            and source_available
            and self.visual_asset_process.state()
            == QProcess.ProcessState.NotRunning
            and self.web_image_process.state()
            == QProcess.ProcessState.NotRunning
        )


    def normalize_visual_image_source(
        self,
        value,
    ) -> str:

        normalized = str(
            value or ""
        ).strip().upper().replace(
            "-",
            "_",
        ).replace(
            " ",
            "_",
        )

        if normalized in {
            "WEB",
            "WEB_SEARCH",
            "WEB_SOURCED",
            "OPENVERSE",
            "WIKIMEDIA",
        }:
            return "WEB"

        if normalized in {
            "CHATGPT",
            "OPENAI",
            "OPENAI_IMAGE",
        }:
            return "CHATGPT"

        return "FORGE"


    def set_visual_image_source_combo(
        self,
        value,
    ):

        source = self.normalize_visual_image_source(
            value
        )
        index = self.visual_image_source_combo.findData(
            source
        )
        self.visual_image_source_combo.setCurrentIndex(
            max(
                0,
                index,
            )
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


    def coerce_visual_position(
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
            number = 0.0

        return max(
            -1.0,
            min(
                1.0,
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
            "Image entity updated."
        )


    def generate_more_selected_visual_variant(self):

        source_slot = self.selected_visual_slot()
        if source_slot is None:
            return

        image_source = self.normalize_visual_image_source(
            source_slot.get(
                "image_source",
                source_slot.get(
                    "provider",
                    "FORGE",
                ),
            )
        )
        if image_source == "WEB":
            self.visual_inspector_fields_changed()
            source_slot = self.selected_visual_slot()
            if source_slot is None:
                return

            existing_ids = {
                str(
                    slot.get(
                        "slot_id",
                        "",
                    )
                    or ""
                )
                for slot in self.visual_plan_slots
                if isinstance(
                    slot,
                    dict,
                )
            }
            base_id = str(
                source_slot.get(
                    "slot_id",
                    "visual",
                )
                or "visual"
            )
            entity_id = self.unique_visual_entity_id(
                f"{base_id}__web",
                existing_ids,
            )
            entity = self.clone_visual_slot(
                source_slot
            )
            entity["slot_id"] = entity_id
            entity["label"] = (
                str(
                    source_slot.get(
                        "label",
                        "AI Visual",
                    )
                    or "AI Visual"
                )
                + " WEB"
            )
            entity["image_source"] = "WEB"
            entity["asset_path"] = ""
            entity["variants"] = []
            entity["active_variant_id"] = ""
            entity["state"] = "PLANNED"
            entity["generated"] = False
            entity["provider"] = ""
            entity.pop(
                "web_source",
                None,
            )
            entity.pop(
                "error",
                None,
            )
            entity.pop(
                "saved_variant",
                None,
            )
            entity["user_modified"] = True
            entity = self.place_new_visual_entity(
                entity,
                [
                    slot
                    for slot in self.visual_plan_slots
                    if isinstance(
                        slot,
                        dict,
                    )
                ],
            )
            insert_index = (
                self.selected_visual_slot_index + 1
                if self.selected_visual_slot_index is not None
                else len(
                    self.visual_plan_slots
                )
            )
            self.visual_plan_slots.insert(
                insert_index,
                entity,
            )
            self.selected_visual_slot_index = insert_index
            self.user_visual_edits = True
            self.save_ai_visual_plan()
            self.sync_visual_slots_to_editor_asset_plan(
                preserve_manual=True
            )
            self.refresh_visual_plan_display()
            self.load_selected_visual_into_inspector()
            self.start_web_image_search(
                entity
            )
            return

        if image_source == "CHATGPT":
            self.visual_inspector_fields_changed()
            source_slot = self.selected_visual_slot()
            if source_slot is None:
                return

            existing_ids = {
                str(
                    slot.get(
                        "slot_id",
                        "",
                    )
                    or ""
                )
                for slot in self.visual_plan_slots
                if isinstance(
                    slot,
                    dict,
                )
            }
            base_id = str(
                source_slot.get(
                    "slot_id",
                    "visual",
                )
                or "visual"
            )
            entity_id = self.unique_visual_entity_id(
                f"{base_id}__chatgpt",
                existing_ids,
            )
            entity = self.clone_visual_slot(
                source_slot
            )
            entity["slot_id"] = entity_id
            entity["label"] = (
                str(
                    source_slot.get(
                        "label",
                        "AI Visual",
                    )
                    or "AI Visual"
                )
                + " GPT"
            )
            entity["image_source"] = "CHATGPT"
            entity["asset_path"] = ""
            entity["variants"] = []
            entity["active_variant_id"] = ""
            entity["state"] = "PLANNED"
            entity["generated"] = False
            entity["provider"] = ""
            entity.pop(
                "web_source",
                None,
            )
            entity.pop(
                "error",
                None,
            )
            entity.pop(
                "saved_variant",
                None,
            )
            entity["user_modified"] = True
            entity = self.place_new_visual_entity(
                entity,
                [
                    slot
                    for slot in self.visual_plan_slots
                    if isinstance(
                        slot,
                        dict,
                    )
                ],
            )
            insert_index = (
                self.selected_visual_slot_index + 1
                if self.selected_visual_slot_index is not None
                else len(
                    self.visual_plan_slots
                )
            )
            self.visual_plan_slots.insert(
                insert_index,
                entity,
            )
            self.selected_visual_slot_index = insert_index
            self.user_visual_edits = True
            self.save_ai_visual_plan()
            self.sync_visual_slots_to_editor_asset_plan(
                preserve_manual=True
            )
            self.refresh_visual_plan_display()
            self.load_selected_visual_into_inspector()
            self.visual_status_label.setText(
                "Created a new independent ChatGPT image entity. Generating..."
            )
            self.start_visual_asset_generation(
                entity_id,
                new_variant=False,
                provider="openai",
            )
            return

        if self.image_ai_state != "ready":
            self.visual_status_label.setText(
                "Image AI is offline. Existing image entities are unchanged."
            )
            return

        self.visual_inspector_fields_changed()
        source_slot = self.selected_visual_slot()
        if source_slot is None:
            return

        existing_ids = {
            str(
                slot.get(
                    "slot_id",
                    "",
                )
                or ""
            )
            for slot in self.visual_plan_slots
            if isinstance(
                slot,
                dict,
            )
        }
        base_id = str(
            source_slot.get(
                "slot_id",
                "visual",
            )
            or "visual"
        )
        entity_id = self.unique_visual_entity_id(
            f"{base_id}__image",
            existing_ids,
        )

        entity = self.clone_visual_slot(
            source_slot
        )
        entity["slot_id"] = entity_id
        entity["label"] = (
            str(
                source_slot.get(
                    "label",
                    "AI Visual",
                )
                or "AI Visual"
            )
            + " ALT"
        )
        entity["asset_path"] = ""
        entity["variants"] = []
        entity["active_variant_id"] = ""
        entity["state"] = "PLANNED"
        entity["generated"] = False
        entity["provider"] = ""
        entity.pop(
            "error",
            None,
        )
        entity.pop(
            "saved_variant",
            None,
        )
        entity["user_modified"] = True

        entity = self.place_new_visual_entity(
            entity,
            [
                slot
                for slot in self.visual_plan_slots
                if isinstance(
                    slot,
                    dict,
                )
            ],
        )

        insert_index = (
            self.selected_visual_slot_index + 1
            if self.selected_visual_slot_index is not None
            else len(
                self.visual_plan_slots
            )
        )
        self.visual_plan_slots.insert(
            insert_index,
            entity,
        )
        self.selected_visual_slot_index = insert_index
        self.user_visual_edits = True

        self.save_ai_visual_plan()
        self.sync_visual_slots_to_editor_asset_plan(
            preserve_manual=True
        )
        self.refresh_visual_plan_display()
        self.load_selected_visual_into_inspector()
        self.visual_status_label.setText(
            "Created a new independent image entity. Generating its image..."
        )
        self.start_visual_asset_generation(
            entity_id,
            new_variant=False,
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


    def visual_position_slider_changed(
        self,
        _value: int,
    ):

        self.visual_x_label.setText(
            str(
                int(
                    self.visual_x_slider.value()
                )
            )
        )
        self.visual_y_label.setText(
            str(
                int(
                    self.visual_y_slider.value()
                )
            )
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
                "SELECT IMAGE ENTITY"
            )
            self.visual_label_edit.setText("")
            self.visual_start_edit.setText("")
            self.visual_end_edit.setText("")
            self.visual_type_edit.setText("")
            self.set_visual_image_source_combo(
                "FORGE"
            )
            self.visual_display_mode_combo.setCurrentText(
                "OVERLAY_CARD"
            )
            self.visual_scale_slider.setValue(
                100
            )
            self.visual_scale_label.setText(
                "100%"
            )
            self.visual_x_slider.setValue(
                0
            )
            self.visual_y_slider.setValue(
                0
            )
            self.visual_x_label.setText(
                "0"
            )
            self.visual_y_label.setText(
                "0"
            )
            self.visual_reason_label.setText(
                "Select an image thumbnail or green timeline block to edit it."
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
                "IMAGE ENTITY "
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
        self.set_visual_image_source_combo(
            slot.get(
                "image_source",
                slot.get(
                    "provider",
                    "FORGE",
                ),
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

        visual_x_percent = int(
            round(
                self.coerce_visual_position(
                    slot.get(
                        "position_x",
                        0.0,
                    )
                )
                * 100
            )
        )
        visual_y_percent = int(
            round(
                self.coerce_visual_position(
                    slot.get(
                        "position_y",
                        0.0,
                    )
                )
                * 100
            )
        )
        self.visual_x_slider.setValue(
            visual_x_percent
        )
        self.visual_y_slider.setValue(
            visual_y_percent
        )
        self.visual_x_label.setText(
            str(
                visual_x_percent
            )
        )
        self.visual_y_label.setText(
            str(
                visual_y_percent
            )
        )

        reason_text = (
            "Why AI suggested this: "
            + str(
                slot.get(
                    "reason",
                    "",
                )
                or "No reason was recorded."
            )
        )
        web_source = slot.get(
            "web_source",
            {},
        )
        if (
            self.normalize_visual_image_source(
                slot.get(
                    "image_source",
                    slot.get(
                        "provider",
                        "FORGE",
                    ),
                )
            )
            == "WEB"
            and isinstance(
                web_source,
                dict,
            )
            and web_source
        ):
            source_title = str(
                web_source.get(
                    "title",
                    "Web image",
                )
                or "Web image"
            )
            source_creator = str(
                web_source.get(
                    "creator",
                    "",
                )
                or ""
            )
            source_license = str(
                web_source.get(
                    "license",
                    "Unknown license",
                )
                or "Unknown license"
            )
            reason_text += (
                "\nWeb source: "
                + source_title
                + (
                    f" · {source_creator}"
                    if source_creator
                    else ""
                )
                + f" · {source_license}"
            )

        self.visual_reason_label.setText(
            reason_text
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
        slot["image_source"] = self.normalize_visual_image_source(
            self.visual_image_source_combo.currentData()
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
        slot["position_x"] = round(
            self.coerce_visual_position(
                self.visual_x_slider.value()
                / 100.0
            ),
            3,
        )
        slot["position_y"] = round(
            self.coerce_visual_position(
                self.visual_y_slider.value()
                / 100.0
            ),
            3,
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

        # Inspector sliders/fields should update the live monitor immediately,
        # not only after the playhead moves again.
        self.active_visual_preview_signature = None
        self.active_visual_preview_layout_signature = None
        self.update_ai_visual_preview_overlay(
            self.player.position()
        )


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
            or self.web_image_process.state()
            != QProcess.ProcessState.NotRunning
        )
        selected = slot is not None
        enabled = bool(
            slot.get(
                "enabled",
                True,
            )
        ) if slot else False

        image_source = self.normalize_visual_image_source(
            slot.get(
                "image_source",
                slot.get(
                    "provider",
                    "FORGE",
                ),
            )
            if slot
            else "FORGE"
        )
        forge_available = (
            image_source == "FORGE"
            and self.image_ai_state == "ready"
        )

        if image_source == "WEB":
            self.regenerate_visual_button.setText(
                "SEARCH WEB"
            )
            self.generate_more_visual_button.setText(
                "FIND ANOTHER"
            )
        elif image_source == "CHATGPT":
            self.regenerate_visual_button.setText(
                "GENERATE CHATGPT"
            )
            self.generate_more_visual_button.setText(
                "NEW CHATGPT IMAGE"
            )
        else:
            self.regenerate_visual_button.setText(
                "REGENERATE"
            )
            self.generate_more_visual_button.setText(
                "GENERATE NEW IMAGE"
            )

        source_available = (
            forge_available
            or image_source in {
                "WEB",
                "CHATGPT",
            }
        )

        self.regenerate_visual_button.setEnabled(
            selected
            and enabled
            and source_available
            and not running
        )
        self.generate_more_visual_button.setEnabled(
            selected
            and enabled
            and source_available
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
        provider: str = "auto",
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
        self.visual_asset_provider = str(
            provider
            or "auto"
        ).strip().lower()
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
            (
                "Generating ChatGPT image..."
                if self.visual_asset_provider == "openai"
                else "Generating visual assets..."
            )
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
            self.visual_asset_provider,
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


