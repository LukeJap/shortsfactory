from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent.parent
WEB_CACHE_DIR = ROOT / "output" / "ai_visual_assets" / "web"
THUMB_CACHE_DIR = WEB_CACHE_DIR / "thumbs"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
USER_AGENT = "ShortsFactory/1.0"

EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def safe_slug(
    value: str,
) -> str:

    cleaned = re.sub(
        r"[^a-z0-9]+",
        "-",
        str(
            value
            or ""
        ).lower(),
    ).strip(
        "-"
    )
    return cleaned[:48] or "image"


def guess_extension(
    url: str,
    content_type: str = "",
) -> str:

    lowered = str(
        content_type
        or ""
    ).split(
        ";",
        1,
    )[0].strip().lower()
    if lowered in EXTENSION_BY_CONTENT_TYPE:
        return EXTENSION_BY_CONTENT_TYPE[
            lowered
        ]

    suffix = Path(
        str(
            url
            or ""
        ).split(
            "?",
            1,
        )[0]
    ).suffix.lower()
    if suffix in {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
    }:
        return ".jpg" if suffix == ".jpeg" else suffix

    return ".jpg"


def license_text(
    item: dict[str, Any],
) -> str:

    license_name = str(
        item.get(
            "license",
            "",
        )
        or ""
    ).upper()
    version = str(
        item.get(
            "license_version",
            "",
        )
        or ""
    ).strip()
    if license_name and version:
        return f"{license_name} {version}"
    return license_name or "Unknown license"


def normalize_openverse_result(
    item: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any]:

    title = str(
        item.get(
            "title",
            "",
        )
        or ""
    ).strip() or f"Image {index}"
    creator = str(
        item.get(
            "creator",
            "",
        )
        or ""
    ).strip()
    source_url = str(
        item.get(
            "url",
            "",
        )
        or ""
    ).strip()
    thumbnail_url = str(
        item.get(
            "thumbnail",
            "",
        )
        or item.get(
            "thumbnail_url",
            "",
        )
        or ""
    ).strip()
    foreign_landing_url = str(
        item.get(
            "foreign_landing_url",
            "",
        )
        or ""
    ).strip()
    detail_url = str(
        item.get(
            "detail_url",
            "",
        )
        or ""
    ).strip()
    source_name = str(
        item.get(
            "source",
            "",
        )
        or "openverse"
    ).strip() or "openverse"

    return {
        "provider": "openverse",
        "provider_source": source_name,
        "title": title,
        "creator": creator,
        "license": license_text(
            item
        ),
        "license_name": str(
            item.get(
                "license",
                "",
            )
            or ""
        ),
        "license_version": str(
            item.get(
                "license_version",
                "",
            )
            or ""
        ),
        "source_url": source_url,
        "thumbnail_url": thumbnail_url,
        "foreign_landing_url": foreign_landing_url,
        "detail_url": detail_url,
    }


def search_openverse_images(
    query: str,
    *,
    page_size: int = 8,
    timeout: int = 20,
) -> list[dict[str, Any]]:

    text = str(
        query
        or ""
    ).strip()
    if not text:
        return []

    response = requests.get(
        OPENVERSE_API,
        params={
            "q": text,
            "page_size": page_size,
        },
        headers={
            "User-Agent": USER_AGENT,
        },
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    results = data.get(
        "results",
        [],
    )
    if not isinstance(
        results,
        list,
    ):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(
        results,
        start=1,
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue
        source_url = str(
            item.get(
                "url",
                "",
            )
            or ""
        ).strip()
        thumbnail_url = str(
            item.get(
                "thumbnail",
                "",
            )
            or item.get(
                "thumbnail_url",
                "",
            )
            or ""
        ).strip()
        if not source_url or not thumbnail_url:
            continue
        normalized.append(
            normalize_openverse_result(
                item,
                index=index,
            )
        )

    return normalized


def download_file(
    url: str,
    destination: Path,
    *,
    timeout: int = 30,
) -> Path:

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    destination.write_bytes(
        response.content
    )
    return destination


def cache_thumbnail(
    result: dict[str, Any],
) -> Path | None:

    thumbnail_url = str(
        result.get(
            "thumbnail_url",
            "",
        )
        or ""
    ).strip()
    if not thumbnail_url:
        return None

    digest = hashlib.sha1(
        thumbnail_url.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()[:12]
    extension = guess_extension(
        thumbnail_url
    )
    destination = (
        THUMB_CACHE_DIR
        / f"{digest}{extension}"
    )
    if destination.exists():
        return destination

    return download_file(
        thumbnail_url,
        destination,
        timeout=20,
    )


def download_result_image(
    result: dict[str, Any],
    *,
    slot_id: str,
) -> dict[str, Any]:

    source_url = str(
        result.get(
            "source_url",
            "",
        )
        or ""
    ).strip()
    if not source_url:
        raise RuntimeError(
            "Selected search result has no downloadable source URL."
        )

    digest = hashlib.sha1(
        source_url.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()[:12]
    title = str(
        result.get(
            "title",
            "",
        )
        or "image"
    )
    extension = guess_extension(
        source_url
    )
    destination = (
        WEB_CACHE_DIR
        / f"{safe_slug(slot_id)}_{safe_slug(title)}_{digest}{extension}"
    )
    if not destination.exists():
        response = requests.get(
            source_url,
            headers={
                "User-Agent": USER_AGENT,
            },
            timeout=45,
        )
        response.raise_for_status()
        extension = guess_extension(
            source_url,
            response.headers.get(
                "Content-Type",
                "",
            ),
        )
        if destination.suffix.lower() != extension:
            destination = destination.with_suffix(
                extension
            )
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        destination.write_bytes(
            response.content
        )

    thumb_path = cache_thumbnail(
        result
    )

    return {
        **result,
        "local_path": str(
            destination
        ),
        "thumbnail_path": str(
            thumb_path
        )
        if thumb_path is not None
        else "",
        "source_type": "web_sourced",
    }
