from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = ROOT / "output" / "editor_asset_plan.json"


def read_json(
    path: Path,
) -> dict[str, Any]:

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}

    return data if isinstance(data, dict) else {}


def default_plan() -> dict[str, Any]:

    return {
        "version": 1,
        "clips": [],
    }


def load_editor_asset_plan() -> dict[str, Any]:

    plan = read_json(
        PLAN_PATH
    )

    if not isinstance(
        plan.get(
            "clips",
        ),
        list,
    ):
        plan = default_plan()

    plan["version"] = 1
    return plan


def save_editor_asset_plan(
    plan: dict[str, Any],
) -> None:

    payload = dict(
        plan
    )
    payload["version"] = 1
    payload["clips"] = [
        clip
        for clip in payload.get(
            "clips",
            [],
        )
        if isinstance(
            clip,
            dict,
        )
    ]

    PLAN_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    PLAN_PATH.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def clips_of_kind(
    plan: dict[str, Any],
    kind: str,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:

    normalized = str(
        kind
    ).upper()
    result = []

    for clip in plan.get(
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
        ).upper() != normalized:
            continue
        if active_only and clip.get(
            "active",
            True,
        ) is False:
            continue
        result.append(
            clip
        )

    return result


def replace_kind_clips(
    plan: dict[str, Any],
    kind: str,
    clips: list[dict[str, Any]],
    *,
    preserve_manual: bool = True,
) -> dict[str, Any]:

    normalized = str(
        kind
    ).upper()
    kept: list[dict[str, Any]] = []
    kept_ids: set[str] = set()

    for clip in plan.get(
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
        ).upper() != normalized:
            kept.append(
                clip
            )
            continue

        if preserve_manual and (
            bool(
                clip.get(
                    "manual_override",
                    False,
                )
            )
            or bool(
                clip.get(
                    "locked",
                    False,
                )
            )
        ):
            kept.append(
                clip
            )
            kept_ids.add(
                str(
                    clip.get(
                        "id",
                        "",
                    )
                    or ""
                )
            )

    kept.extend(
        clip
        for clip in clips
        if str(
            clip.get(
                "id",
                "",
            )
            or ""
        )
        not in kept_ids
    )
    plan["clips"] = kept
    return plan


def upsert_clip(
    plan: dict[str, Any],
    clip: dict[str, Any],
) -> dict[str, Any]:

    clip_id = str(
        clip.get(
            "id",
            "",
        )
        or ""
    )
    if not clip_id:
        return plan

    clips = plan.setdefault(
        "clips",
        [],
    )

    for index, existing in enumerate(
        clips
    ):
        if not isinstance(
            existing,
            dict,
        ):
            continue
        if str(
            existing.get(
                "id",
                "",
            )
            or ""
        ) == clip_id:
            clips[index] = {
                **existing,
                **clip,
            }
            return plan

    clips.append(
        clip
    )
    return plan
