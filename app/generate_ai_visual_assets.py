from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

from image_backend_status import (
    DEFAULT_API,
    WebUIImageProvider,
    status_with_optional_launch,
)
from pipeline_paths import AI_VISUAL_PLAN_PATH as DEFAULT_PLAN


ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ASSET_DIR = (
    ROOT
    / "output"
    / "ai_visual_assets"
)

EVENT_PREFIX = "SF_VISUAL_EVENT "

QUALITY_PRESETS = {
    # Keep FAST usable on lower-VRAM cards, but avoid the extremely small
    # 512x896 request that made fine detail noticeably worse in Forge.
    "FAST": {
        "width": 576,
        "height": 1024,
        "steps": 20,
        "cfg_scale": 5.5,
        "sampler_name": "DPM++ 2M",
        "scheduler": "Karras",
    },
    # BALANCED is the editor default. Give it roughly an SDXL-native pixel
    # budget so the app is not quietly asking Forge for a much lower-detail
    # image than users commonly generate in the browser UI.
    "BALANCED": {
        "width": 768,
        "height": 1344,
        "steps": 30,
        "cfg_scale": 6.0,
        "sampler_name": "DPM++ 2M",
        "scheduler": "Karras",
    },
    "HIGH": {
        "width": 832,
        "height": 1472,
        "steps": 36,
        "cfg_scale": 6.0,
        "sampler_name": "DPM++ 2M",
        "scheduler": "Karras",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate image assets for ShortsFactory AI visual slots."
        )
    )
    parser.add_argument(
        "--plan",
        default=str(
            DEFAULT_PLAN
        ),
    )
    parser.add_argument(
        "--asset-dir",
        default=str(
            DEFAULT_ASSET_DIR
        ),
    )
    parser.add_argument(
        "--provider",
        choices=[
            "auto",
            "a1111",
            "openai",
            "preview",
        ],
        default="auto",
    )
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
    )
    parser.add_argument(
        "--quality",
        choices=sorted(
            QUALITY_PRESETS.keys()
        ),
        default="BALANCED",
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "Expected active model title. The generator verifies it but "
            "does not switch checkpoints during generation."
        ),
    )
    parser.add_argument(
        "--slot-id",
        default="",
        help="Regenerate only the visual slot with this stable slot id.",
    )
    parser.add_argument(
        "--new-variant",
        action="store_true",
        help=(
            "Create a new variant file for the selected slot instead of "
            "reusing/replacing the current slot asset."
        ),
    )
    parser.add_argument(
        "--autolaunch",
        action="store_true",
        help="Start Stable Diffusion Forge if the image API is offline.",
    )
    return parser.parse_args()


def load_json(
    path: Path,
) -> dict[str, Any]:
    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}

    return data if isinstance(
        data,
        dict,
    ) else {}


def write_json(
    path: Path,
    data: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def emit_event(
    **payload: Any,
) -> None:
    print(
        EVENT_PREFIX
        + json.dumps(
            payload,
            ensure_ascii=False,
        ),
        flush=True,
    )


def slot_id_for(
    slot: dict[str, Any],
    index: int,
) -> str:
    value = str(
        slot.get(
            "slot_id",
            "",
        )
        or ""
    ).strip()
    if value:
        return value
    return f"slot_{index:02d}"


def enabled_slot(
    slot: dict[str, Any],
) -> bool:
    return bool(
        slot.get(
            "enabled",
            True,
        )
    )


def normalized_image_source(
    slot: dict[str, Any],
) -> str:
    value = str(
        slot.get(
            "image_source",
            slot.get(
                "provider",
                "FORGE",
            ),
        )
        or "FORGE"
    ).strip().upper().replace(
        "-",
        "_",
    ).replace(
        " ",
        "_",
    )

    if value in {
        "WEB",
        "WEB_SEARCH",
        "WEB_SOURCED",
        "OPENVERSE",
        "WIKIMEDIA",
    }:
        return "WEB"
    if value in {
        "CHATGPT",
        "OPENAI",
        "OPENAI_IMAGE",
    }:
        return "CHATGPT"
    return "FORGE"


def asset_key(
    item: dict[str, Any],
) -> str:
    slot_id = str(
        item.get(
            "slot_id",
            "",
        )
        or ""
    ).strip()
    if slot_id:
        return "id:" + slot_id

    try:
        slot_index = int(
            item.get(
                "slot_index",
                0,
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        slot_index = 0

    return f"index:{slot_index}"


def generated_asset_exists(
    asset: dict[str, Any] | None,
) -> bool:
    if not isinstance(
        asset,
        dict,
    ):
        return False

    if not asset.get(
        "generated"
    ):
        return False

    path = Path(
        str(
            asset.get(
                "path",
                "",
            )
        )
    )
    return path.exists()


def existing_asset_map(
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    assets = manifest.get(
        "assets",
        [],
    )
    if not isinstance(
        assets,
        list,
    ):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for item in assets:
        if isinstance(
            item,
            dict,
        ):
            result[
                asset_key(
                    item
                )
            ] = item
    return result


def forge_prompt_suffix(
    visual_type: str,
) -> str:
    normalized = str(
        visual_type
        or "ai_recreation"
    ).strip().lower()

    suffixes = {
        "object_detail": (
            "professional editorial object photography, realistic materials "
            "and texture, crisp subject detail, natural depth of field"
        ),
        "environment": (
            "professional environmental photography, believable spatial "
            "depth, natural lighting, detailed surroundings"
        ),
        "graphic_explainer": (
            "clean editorial graphic, strong visual hierarchy, simple "
            "coherent composition, polished professional design"
        ),
        "archival_style": (
            "authentic documentary archival aesthetic, period-appropriate "
            "materials and lighting, subtle natural film texture"
        ),
        "ai_recreation": (
            "cinematic realistic recreation, believable lighting and "
            "materials, coherent anatomy, natural documentary framing"
        ),
    }

    return suffixes.get(
        normalized,
        suffixes["ai_recreation"],
    )


def forge_negative_prompt(
    visual_type: str,
) -> str:
    common = (
        "text, captions, subtitles, logos, watermark, UI, interface, "
        "low resolution, blurry, out of focus, jpeg artifacts, noisy image, "
        "bad anatomy, deformed anatomy, malformed hands, extra fingers, "
        "missing fingers, extra limbs, duplicated people, duplicated objects, "
        "distorted face, crossed eyes, plastic skin, oversharpened"
    )

    normalized = str(
        visual_type
        or ""
    ).strip().lower()

    if normalized in {
        "ai_recreation",
        "object_detail",
        "environment",
        "archival_style",
    }:
        return (
            common
            + ", cheap 3d render, obvious CGI, video game screenshot, "
            "generic stock illustration"
        )

    return common


def build_forge_payload(
    prompt: str,
    visual_type: str,
    preset: dict[str, Any],
) -> dict[str, Any]:
    clean_prompt = " ".join(
        str(
            prompt
            or ""
        ).split()
    ).strip()

    if not clean_prompt:
        raise RuntimeError(
            "Visual prompt is empty."
        )

    styled_prompt = (
        clean_prompt
        + ", vertical 9:16 composition, strong focal subject, "
        + forge_prompt_suffix(
            visual_type
        )
    )

    return {
        "prompt": styled_prompt,
        "negative_prompt": forge_negative_prompt(
            visual_type
        ),
        "steps": int(
            preset["steps"]
        ),
        "cfg_scale": float(
            preset["cfg_scale"]
        ),
        "width": int(
            preset["width"]
        ),
        "height": int(
            preset["height"]
        ),
        "sampler_name": str(
            preset["sampler_name"]
        ),
        "scheduler": str(
            preset.get(
                "scheduler",
                "Karras",
            )
            or "Karras"
        ),
        "seed": -1,
        "batch_size": 1,
        "n_iter": 1,
        "restore_faces": False,
        "tiling": False,
    }


def generate_a1111(
    api: str,
    prompt: str,
    visual_type: str,
    output_path: Path,
    preset: dict[str, Any],
) -> None:
    payload = build_forge_payload(
        prompt,
        visual_type,
        preset,
    )

    print(
        (
            "Forge request: "
            f"{payload['width']}x{payload['height']}, "
            f"{payload['steps']} steps, "
            f"CFG {payload['cfg_scale']}, "
            f"{payload['sampler_name']} / {payload['scheduler']}"
        ),
        flush=True,
    )

    response = requests.post(
        api.rstrip("/")
        + "/sdapi/v1/txt2img",
        json=payload,
        timeout=360,
    )

    response.raise_for_status()

    data = response.json()
    images = data.get(
        "images",
        [],
    )
    if not images:
        raise RuntimeError(
            "Image AI returned no image."
        )

    encoded = str(
        images[0]
    )
    if "," in encoded:
        encoded = encoded.split(
            ",",
            1,
        )[1]

    output_path.write_bytes(
        base64.b64decode(
            encoded
        )
    )




OPENAI_IMAGE_API = os.getenv(
    "SHORTSFACTORY_OPENAI_IMAGE_API",
    "https://api.openai.com/v1/images/generations",
)

OPENAI_IMAGE_MODEL = os.getenv(
    "SHORTSFACTORY_OPENAI_IMAGE_MODEL",
    "gpt-image-2",
)


def build_openai_prompt(
    prompt: str,
    visual_type: str,
) -> str:
    clean_prompt = " ".join(
        str(
            prompt
            or ""
        ).split()
    ).strip()
    if not clean_prompt:
        raise RuntimeError(
            "Visual prompt is empty."
        )

    normalized_type = str(
        visual_type
        or "ai_recreation"
    ).strip().lower()
    type_guidance = {
        "object_detail": (
            "Make the subject visually specific, realistic, and easy to read "
            "at phone size."
        ),
        "environment": (
            "Make the location/environment believable, detailed, and visually "
            "clear at phone size."
        ),
        "graphic_explainer": (
            "Use a clean editorial graphic style with strong hierarchy and no "
            "unrequested text."
        ),
        "archival_style": (
            "Use a convincing documentary/archive-inspired treatment while "
            "keeping the depicted scene coherent."
        ),
        "ai_recreation": (
            "Create a believable cinematic recreation with coherent anatomy, "
            "materials, lighting, and spatial relationships."
        ),
    }.get(
        normalized_type,
        "Create a polished, coherent visual with a strong focal subject.",
    )

    return (
        clean_prompt
        + "\n\n"
        + type_guidance
        + " Compose vertically for a 9:16 short-form video. "
        + "Do not add captions, subtitles, watermarks, logos, UI, borders, "
        + "or readable text unless the prompt explicitly requires text."
    )


def openai_quality_for(
    quality: str,
) -> str:
    return {
        "FAST": "low",
        "BALANCED": "medium",
        "HIGH": "high",
    }.get(
        str(quality or "BALANCED").upper(),
        "medium",
    )


def generate_openai_image(
    api_key: str,
    prompt: str,
    visual_type: str,
    output_path: Path,
    quality: str,
) -> None:
    key = str(
        api_key
        or ""
    ).strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in Windows and restart ShortsFactory."
        )

    payload = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": build_openai_prompt(
            prompt,
            visual_type,
        ),
        "size": "1024x1536",
        "quality": openai_quality_for(
            quality
        ),
        "n": 1,
    }

    print(
        (
            "OpenAI image request: "
            f"{OPENAI_IMAGE_MODEL}, "
            f"{payload['size']}, "
            f"quality={payload['quality']}"
        ),
        flush=True,
    )

    response = requests.post(
        OPENAI_IMAGE_API,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=420,
    )

    if not response.ok:
        message = ""
        try:
            error_payload = response.json()
            if isinstance(
                error_payload,
                dict,
            ):
                error_data = error_payload.get(
                    "error",
                    {},
                )
                if isinstance(
                    error_data,
                    dict,
                ):
                    message = str(
                        error_data.get(
                            "message",
                            "",
                        )
                        or ""
                    ).strip()
        except ValueError:
            message = ""
        if message:
            raise RuntimeError(
                f"OpenAI image generation failed: {message}"
            )
        response.raise_for_status()

    data = response.json()
    items = data.get(
        "data",
        [],
    ) if isinstance(
        data,
        dict,
    ) else []
    if not isinstance(
        items,
        list,
    ) or not items or not isinstance(
        items[0],
        dict,
    ):
        raise RuntimeError(
            "OpenAI returned no generated image."
        )

    first = items[0]
    encoded = str(
        first.get(
            "b64_json",
            "",
        )
        or ""
    ).strip()
    image_url = str(
        first.get(
            "url",
            "",
        )
        or ""
    ).strip()

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if encoded:
        output_path.write_bytes(
            base64.b64decode(
                encoded
            )
        )
        return

    if image_url:
        image_response = requests.get(
            image_url,
            timeout=120,
        )
        image_response.raise_for_status()
        output_path.write_bytes(
            image_response.content
        )
        return

    raise RuntimeError(
        "OpenAI returned an image response without image data."
    )


def write_ppm_preview(
    path: Path,
    label: str,
    visual_type: str,
    preset: dict[str, Any],
) -> None:
    """
    Dependency-free placeholder image. This is deliberately a timing preview,
    not fake generated artwork. PPM is supported by FFmpeg and Qt.
    """

    width = int(
        preset["width"]
    )
    height = int(
        preset["height"]
    )
    pixels = bytearray()

    for y in range(
        height
    ):
        ratio = y / max(
            1,
            height - 1,
        )
        base = int(
            20
            + 24
            * ratio
        )

        for x in range(
            width
        ):
            edge = abs(
                x
                - width / 2
            ) / (
                width / 2
            )
            r = max(
                0,
                min(
                    255,
                    base
                    + int(
                        28
                        * (
                            1.0
                            - edge
                        )
                    ),
                ),
            )
            g = max(
                0,
                min(
                    255,
                    base
                    + 16,
                ),
            )
            b = max(
                0,
                min(
                    255,
                    base
                    + 10,
                ),
            )
            pixels.extend(
                (
                    r,
                    g,
                    b,
                )
            )

    path.write_bytes(
        f"P6\n{width} {height}\n255\n".encode(
            "ascii"
        )
        + pixels
    )

    path.with_suffix(
        ".txt"
    ).write_text(
        (
            "SHORTSFACTORY AI VISUAL PREVIEW\n"
            f"Label: {label}\n"
            f"Type: {visual_type}\n"
            "No local image model was available; this is a timing placeholder.\n"
        ),
        encoding="utf-8",
    )


def update_slot_from_asset(
    slot: dict[str, Any],
    asset: dict[str, Any],
) -> None:
    slot["state"] = str(
        asset.get(
            "state",
            slot.get(
                "state",
                "PLANNED",
            ),
        )
    )
    slot["asset_path"] = str(
        asset.get(
            "path",
            slot.get(
                "asset_path",
                "",
            ),
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
    if asset.get(
        "error"
    ):
        slot["error"] = str(
            asset.get(
                "error"
            )
        )
    else:
        slot.pop(
            "error",
            None,
        )

    variant_id = str(
        asset.get(
            "variant_id",
            "",
        )
        or ""
    )
    if variant_id:
        variants = slot.setdefault(
            "variants",
            [],
        )
        if not isinstance(
            variants,
            list,
        ):
            variants = []
            slot["variants"] = variants

        variant = {
            "variant_id": variant_id,
            "path": asset.get(
                "path",
                "",
            ),
            "state": asset.get(
                "state",
                "",
            ),
            "provider": asset.get(
                "provider",
                "",
            ),
            "generated": asset.get(
                "generated",
                False,
            ),
            "quality": asset.get(
                "quality",
                "",
            ),
            "saved": bool(
                asset.get(
                    "saved",
                    False,
                )
            ),
        }

        replaced = False
        for index, existing in enumerate(
            variants
        ):
            if not isinstance(
                existing,
                dict,
            ):
                continue
            if str(
                existing.get(
                    "variant_id",
                    "",
                )
                or ""
            ) == variant_id:
                variants[index] = {
                    **existing,
                    **variant,
                }
                replaced = True
                break

        if not replaced:
            variants.append(
                variant
            )

        slot["active_variant_id"] = variant_id


def build_asset(
    *,
    slot: dict[str, Any],
    slot_index: int,
    slot_id: str,
    label: str,
    visual_type: str,
    prompt: str,
    path: Path | None,
    provider: str,
    generated: bool,
    state: str,
    quality: str,
    error: str = "",
    variant_id: str = "",
    saved: bool = False,
) -> dict[str, Any]:
    asset = {
        "slot_index": slot_index,
        "slot_id": slot_id,
        "start": slot.get(
            "start"
        ),
        "end": slot.get(
            "end"
        ),
        "label": label,
        "visual_type": visual_type,
        "prompt": prompt,
        "path": str(
            path
        )
        if path is not None
        else "",
        "provider": provider,
        "generated": generated,
        "state": state,
        "quality": quality,
    }
    if variant_id:
        asset["variant_id"] = variant_id
    if saved:
        asset["saved"] = True
    if error:
        asset["error"] = error
    return asset


def existing_variants_for_slot(
    slot: dict[str, Any],
) -> list[dict[str, Any]]:

    variants = slot.get(
        "variants",
        [],
    )
    if not isinstance(
        variants,
        list,
    ):
        return []
    return [
        variant
        for variant in variants
        if isinstance(
            variant,
            dict,
        )
    ]


def next_variant_id(
    slot: dict[str, Any],
) -> str:

    highest = 0
    for variant in existing_variants_for_slot(
        slot
    ):
        raw = str(
            variant.get(
                "variant_id",
                "",
            )
            or ""
        )
        if raw.startswith(
            "variant_"
        ):
            try:
                highest = max(
                    highest,
                    int(
                        raw.rsplit(
                            "_",
                            1,
                        )[1]
                    ),
                )
            except (
                IndexError,
                ValueError,
            ):
                pass

    return f"variant_{highest + 1:03d}"


def variant_output_path(
    asset_dir: Path,
    slot_index: int,
    slot: dict[str, Any],
    suffix: str,
    *,
    force_new: bool,
) -> tuple[Path, str]:

    variants = existing_variants_for_slot(
        slot
    )
    active_variant_id = str(
        slot.get(
            "active_variant_id",
            "",
        )
        or ""
    )

    if not force_new:
        for variant in variants:
            if active_variant_id and str(
                variant.get(
                    "variant_id",
                    "",
                )
                or ""
            ) != active_variant_id:
                continue
            if bool(
                variant.get(
                    "saved",
                    False,
                )
            ):
                # A kept variant is immutable. Regeneration creates a new
                # sibling instead of overwriting the file the user saved.
                break

            raw_path = str(
                variant.get(
                    "path",
                    "",
                )
                or ""
            )
            if raw_path:
                return Path(
                    raw_path
                ), str(
                    variant.get(
                        "variant_id",
                        active_variant_id
                        or "variant_001",
                    )
                    or "variant_001"
                )

    variant_id = next_variant_id(
        slot
    )
    return (
        asset_dir
        / f"visual_{slot_index:02d}_{variant_id}{suffix}",
        variant_id,
    )


def main() -> int:
    args = parse_args()

    plan_path = Path(
        args.plan
    ).resolve()
    asset_dir = Path(
        args.asset_dir
    ).resolve()
    quality = str(
        args.quality
    ).upper()
    preset = QUALITY_PRESETS[
        quality
    ]

    print(
        "ShortsFactory AI visual asset generator starting...",
        flush=True,
    )

    if not plan_path.exists():
        print(
            f"ERROR: Visual plan not found: {plan_path}",
            flush=True,
        )
        return 1

    plan = load_json(
        plan_path
    )
    slots = plan.get(
        "slots",
        [],
    )
    if not isinstance(
        slots,
        list,
    ):
        slots = []

    asset_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_path = (
        asset_dir
        / "manifest.json"
    )
    existing_manifest = load_json(
        manifest_path
    )
    assets_by_key = existing_asset_map(
        existing_manifest
    )

    provider = args.provider
    backend_status: dict[str, Any] = {}

    if provider in {
        "auto",
        "a1111",
        "preview",
    }:
        backend = WebUIImageProvider(
            args.api
        )
        backend_status = status_with_optional_launch(
            backend,
            autolaunch=bool(
                args.autolaunch
                and provider
                in {
                    "auto",
                    "a1111",
                }
            ),
            wait_seconds=180.0,
        )

        if provider == "auto":
            provider = (
                "a1111"
                if backend_status.get(
                    "state"
                )
                == "ready"
                else "preview"
            )

    if provider == "a1111":
        if backend_status.get(
            "state"
        ) != "ready":
            print(
                (
                    "Image AI is not ready. Existing assets will be preserved "
                    "and missing slots will be marked failed."
                ),
                flush=True,
            )
            provider = "failed"
        elif args.model:
            current_title = str(
                backend_status.get(
                    "current_model_title",
                    "",
                )
                or ""
            )
            if current_title and current_title != args.model:
                print(
                    (
                        "WARNING: The active image model no longer matches "
                        "the editor selection. Generation will use the "
                        "currently loaded model; the generator will not switch "
                        "models during a render."
                    ),
                    flush=True,
                )

    if provider == "a1111":
        print(
            (
                "Image AI ready. Generating visuals with "
                f"{quality.lower()} quality."
            ),
            flush=True,
        )
    elif provider == "openai":
        print(
            (
                "OpenAI image generation selected. "
                f"Model: {OPENAI_IMAGE_MODEL}."
            ),
            flush=True,
        )
    elif provider == "preview":
        print(
            (
                "Image AI is offline or has no model. Creating clearly "
                "labeled preview-only timing assets for missing slots."
            ),
            flush=True,
        )

    enabled_count = 0
    ready_count = 0
    preview_count = 0
    failed_count = 0

    for slot_index, slot in enumerate(
        slots,
        start=1,
    ):
        if not isinstance(
            slot,
            dict,
        ):
            continue

        slot_id = slot_id_for(
            slot,
            slot_index,
        )
        slot["slot_id"] = slot_id

        if args.slot_id and slot_id != args.slot_id:
            continue

        if not enabled_slot(
            slot
        ):
            continue

        # Each acquisition backend only touches entities assigned to it.
        # Web-sourced entities are handled by web_image_sources.py and are
        # always left alone here.
        slot_source = normalized_image_source(
            slot
        )
        if provider == "openai":
            if slot_source != "CHATGPT":
                continue
        elif slot_source != "FORGE":
            continue

        enabled_count += 1
        key = "id:" + slot_id
        existing_asset = assets_by_key.get(
            key
        )
        if existing_asset is None:
            existing_asset = assets_by_key.get(
                f"index:{slot_index}"
            )

        label = str(
            slot.get(
                "label",
                f"Visual {slot_index}",
            )
            or f"Visual {slot_index}"
        ).strip()
        visual_type = str(
            slot.get(
                "visual_type",
                "ai_recreation",
            )
            or "ai_recreation"
        ).strip()
        prompt = str(
            slot.get(
                "prompt",
                "",
            )
            or ""
        ).strip()

        emit_event(
            slot_index=slot_index,
            slot_id=slot_id,
            state="GENERATING",
            label=label,
        )

        force_new_variant = bool(
            args.new_variant
            or slot.pop(
                "force_new_variant",
                False,
            )
        )

        if provider == "preview":
            if generated_asset_exists(
                existing_asset
            ) and not force_new_variant:
                asset = build_asset(
                    slot=slot,
                    slot_index=slot_index,
                    slot_id=slot_id,
                    label=label,
                    visual_type=visual_type,
                    prompt=prompt,
                    path=Path(
                        str(
                            existing_asset.get(
                                "path",
                                "",
                            )
                        )
                    ),
                    provider=str(
                        existing_asset.get(
                            "provider",
                            "a1111",
                        )
                    ),
                    generated=True,
                    state="READY",
                    quality=str(
                        existing_asset.get(
                            "quality",
                            quality,
                        )
                    ),
                    variant_id=str(
                        existing_asset.get(
                            "variant_id",
                            slot.get(
                                "active_variant_id",
                                "variant_001",
                            ),
                        )
                        or "variant_001"
                    ),
                    saved=bool(
                        existing_asset.get(
                            "saved",
                            False,
                        )
                    ),
                )
                ready_count += 1
                print(
                    f"Preserved existing visual {slot_index}: {label}",
                    flush=True,
                )
            else:
                output_path, variant_id = variant_output_path(
                    asset_dir,
                    slot_index,
                    slot,
                    ".ppm",
                    force_new=True,
                )
                write_ppm_preview(
                    output_path,
                    label,
                    visual_type,
                    preset,
                )
                asset = build_asset(
                    slot=slot,
                    slot_index=slot_index,
                    slot_id=slot_id,
                    label=label,
                    visual_type=visual_type,
                    prompt=prompt,
                    path=output_path,
                    provider="preview",
                    generated=False,
                    state="PREVIEW_ONLY",
                    quality=quality,
                    variant_id=variant_id,
                )
                preview_count += 1
                print(
                    f"Preview-only visual {slot_index}: {label}",
                    flush=True,
                )

        elif provider == "openai":
            output_path, variant_id = variant_output_path(
                asset_dir,
                slot_index,
                slot,
                ".png",
                force_new=force_new_variant,
            )
            print(
                f"Generating ChatGPT/OpenAI visual {slot_index}: {label}",
                flush=True,
            )
            try:
                generate_openai_image(
                    os.getenv(
                        "OPENAI_API_KEY",
                        "",
                    ),
                    prompt,
                    visual_type,
                    output_path,
                    quality,
                )
                asset = build_asset(
                    slot=slot,
                    slot_index=slot_index,
                    slot_id=slot_id,
                    label=label,
                    visual_type=visual_type,
                    prompt=prompt,
                    path=output_path,
                    provider="openai",
                    generated=True,
                    state="READY",
                    quality=quality,
                    variant_id=variant_id,
                )
                ready_count += 1
            except Exception as exc:
                message = str(
                    exc
                )
                asset = build_asset(
                    slot=slot,
                    slot_index=slot_index,
                    slot_id=slot_id,
                    label=label,
                    visual_type=visual_type,
                    prompt=prompt,
                    path=None,
                    provider="openai",
                    generated=False,
                    state="FAILED",
                    quality=quality,
                    error=message,
                    variant_id=variant_id,
                )
                failed_count += 1
                print(
                    (
                        f"WARNING: ChatGPT/OpenAI visual {slot_index} failed: "
                        f"{message}"
                    ),
                    flush=True,
                )

        elif provider == "a1111":
            output_path, variant_id = variant_output_path(
                asset_dir,
                slot_index,
                slot,
                ".png",
                force_new=force_new_variant,
            )
            print(
                f"Generating visual {slot_index}: {label}",
                flush=True,
            )
            try:
                generate_a1111(
                    args.api,
                    prompt,
                    visual_type,
                    output_path,
                    preset,
                )
                asset = build_asset(
                    slot=slot,
                    slot_index=slot_index,
                    slot_id=slot_id,
                    label=label,
                    visual_type=visual_type,
                    prompt=prompt,
                    path=output_path,
                    provider="a1111",
                    generated=True,
                    state="READY",
                    quality=quality,
                    variant_id=variant_id,
                )
                ready_count += 1
            except Exception as exc:
                message = str(
                    exc
                )
                if (
                    "CUDA"
                    in message.upper()
                    or "MEMORY"
                    in message.upper()
                    or "OUTOFMEMORY"
                    in message.replace(
                        " ",
                        "",
                    ).upper()
                ):
                    message = (
                        message
                        + " Try BALANCED or FAST quality."
                    )
                asset = build_asset(
                    slot=slot,
                    slot_index=slot_index,
                    slot_id=slot_id,
                    label=label,
                    visual_type=visual_type,
                    prompt=prompt,
                    path=None,
                    provider="a1111",
                    generated=False,
                    state="FAILED",
                    quality=quality,
                    error=message,
                    variant_id=variant_id,
                )
                failed_count += 1
                print(
                    (
                        f"WARNING: Visual {slot_index} failed: "
                        f"{message}"
                    ),
                    flush=True,
                )

        else:
            asset = build_asset(
                slot=slot,
                slot_index=slot_index,
                slot_id=slot_id,
                label=label,
                visual_type=visual_type,
                prompt=prompt,
                path=None,
                provider="a1111",
                generated=False,
                state="FAILED",
                quality=quality,
                error="Image AI is not ready.",
                variant_id=str(
                    slot.get(
                        "active_variant_id",
                        "variant_001",
                    )
                    or "variant_001"
                ),
            )
            failed_count += 1

        assets_by_key[
            key
        ] = asset
        update_slot_from_asset(
            slot,
            asset,
        )
        write_json(
            plan_path,
            plan,
        )
        emit_event(
            slot_index=slot_index,
            slot_id=slot_id,
            state=asset["state"],
            label=label,
            path=asset.get(
                "path",
                "",
            ),
            generated=asset.get(
                "generated",
                False,
            ),
            provider=asset.get(
                "provider",
                "",
            ),
            variant_id=asset.get(
                "variant_id",
                "",
            ),
            error=asset.get(
                "error",
                "",
            ),
        )

    manifest_assets: list[dict[str, Any]] = []
    for slot_index, slot in enumerate(
        slots,
        start=1,
    ):
        if not isinstance(
            slot,
            dict,
        ):
            continue
        slot_id = slot_id_for(
            slot,
            slot_index,
        )
        key = "id:" + slot_id
        asset = assets_by_key.get(
            key
        )
        if asset is not None:
            manifest_assets.append(
                asset
            )

    manifest = {
        "source_plan": str(
            plan_path
        ),
        "asset_count": len(
            manifest_assets
        ),
        "provider": provider,
        "quality": quality,
        "assets": manifest_assets,
    }
    write_json(
        manifest_path,
        manifest,
    )

    print(
        (
            "Visual assets complete: "
            f"{ready_count} ready, "
            f"{preview_count} preview-only, "
            f"{failed_count} failed."
        ),
        flush=True,
    )
    print(
        f"Asset manifest: {manifest_path}",
        flush=True,
    )

    if enabled_count == 0:
        print(
            "No enabled visual slots to generate.",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
