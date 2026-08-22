from __future__ import annotations

import hashlib
import json
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
    page: int = 1,
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
            # Openverse rejects anonymous requests with page_size > 20.
            # Keep each API request inside the public anonymous limit and
            # paginate when the editor needs a wider candidate pool.
            "page_size": max(
                1,
                min(
                    20,
                    int(
                        page_size
                    ),
                ),
            ),
            "page": max(
                1,
                int(
                    page
                ),
            ),
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

# ============================================================
# COMMAND-LINE BRIDGE FOR THE DESKTOP EDITOR
# ============================================================

WEB_IMAGE_EVENT_PREFIX = "SF_WEB_IMAGE_EVENT "
DEFAULT_SEARCH_RESULTS = WEB_CACHE_DIR / "search_results.json"
DEFAULT_SELECTION_RESULT = WEB_CACHE_DIR / "selected_result.json"
COMMERCIAL_LICENSES = {
    "cc0",
    "pdm",
    "by",
    "by-sa",
}


def commercial_use_allowed(
    result: dict[str, Any],
) -> bool:

    license_name = str(
        result.get(
            "license_name",
            "",
        )
        or ""
    ).strip().lower()

    return license_name in COMMERCIAL_LICENSES


def search_and_cache_openverse_images(
    query: str,
    *,
    page_size: int = 10,
) -> list[dict[str, Any]]:

    requested_count = max(
        1,
        int(
            page_size
        ),
    )

    # Openverse's public anonymous API accepts at most 20 results per request.
    # Pull a few small pages only when needed so license filtering still has a
    # useful candidate pool without triggering Openverse's 401 protection.
    api_page_size = 20
    max_pages = 3
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for page in range(
        1,
        max_pages + 1,
    ):
        raw_results = search_openverse_images(
            query,
            page_size=api_page_size,
            page=page,
        )
        if not raw_results:
            break

        for result in raw_results:
            if not commercial_use_allowed(
                result
            ):
                continue

            source_url = str(
                result.get(
                    "source_url",
                    "",
                )
                or ""
            ).strip()
            if (
                source_url
                and source_url in seen_urls
            ):
                continue
            if source_url:
                seen_urls.add(
                    source_url
                )

            enriched = dict(
                result
            )
            try:
                thumb_path = cache_thumbnail(
                    enriched
                )
            except Exception as exc:
                thumb_path = None
                enriched["thumbnail_error"] = str(
                    exc
                )

            enriched["thumbnail_path"] = (
                str(
                    thumb_path
                )
                if thumb_path is not None
                else ""
            )
            results.append(
                enriched
            )
            if len(
                results
            ) >= requested_count:
                return results

        # If Openverse returned fewer than a full page, there is no next page.
        if len(
            raw_results
        ) < api_page_size:
            break

    return results


def write_json_file(
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


def emit_web_event(
    **payload: Any,
) -> None:

    print(
        WEB_IMAGE_EVENT_PREFIX
        + json.dumps(
            payload,
            ensure_ascii=False,
        ),
        flush=True,
    )


def parse_cli_args():

    import argparse

    parser = argparse.ArgumentParser(
        description="ShortsFactory openly licensed web-image helper.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    search_parser = subparsers.add_parser(
        "search",
        help="Search Openverse and cache candidate thumbnails.",
    )
    search_parser.add_argument(
        "--query",
        required=True,
    )
    search_parser.add_argument(
        "--page-size",
        type=int,
        default=8,
    )
    search_parser.add_argument(
        "--fallback-query",
        action="append",
        default=[],
        help=(
            "Optional shorter query to try if the primary search has no "
            "commercial-use-compatible results. May be supplied more than once."
        ),
    )
    search_parser.add_argument(
        "--output",
        default=str(
            DEFAULT_SEARCH_RESULTS
        ),
    )

    download_parser = subparsers.add_parser(
        "download",
        help="Download one result from a prior search.",
    )
    download_parser.add_argument(
        "--results",
        default=str(
            DEFAULT_SEARCH_RESULTS
        ),
    )
    download_parser.add_argument(
        "--index",
        type=int,
        required=True,
    )
    download_parser.add_argument(
        "--slot-id",
        required=True,
    )
    download_parser.add_argument(
        "--output",
        default=str(
            DEFAULT_SELECTION_RESULT
        ),
    )

    return parser.parse_args()


def cli_main() -> int:

    args = parse_cli_args()

    if args.command == "search":
        query = str(
            args.query
            or ""
        ).strip()
        if not query:
            print(
                "ERROR: Web image search query is empty.",
                flush=True,
            )
            return 1

        page_size = max(
            1,
            min(
                20,
                int(
                    args.page_size
                ),
            ),
        )

        candidate_queries: list[str] = []
        for candidate in [
            query,
            *list(
                args.fallback_query
                or []
            ),
        ]:
            normalized = " ".join(
                str(
                    candidate
                    or ""
                ).split()
            ).strip()
            if (
                normalized
                and normalized.casefold() not in {
                    item.casefold()
                    for item in candidate_queries
                }
            ):
                candidate_queries.append(
                    normalized
                )

        results: list[dict[str, Any]] = []
        query_used = query
        queries_tried: list[str] = []
        try:
            for candidate in candidate_queries:
                queries_tried.append(
                    candidate
                )
                print(
                    f"Openverse query: {candidate}",
                    flush=True,
                )
                results = search_and_cache_openverse_images(
                    candidate,
                    page_size=page_size,
                )
                if results:
                    query_used = candidate
                    break
        except Exception as exc:
            print(
                f"ERROR: Web image search failed: {exc}",
                flush=True,
            )
            return 1

        output_path = Path(
            args.output
        ).resolve()
        write_json_file(
            output_path,
            {
                "provider": "openverse",
                "query": query_used,
                "requested_query": query,
                "queries_tried": queries_tried,
                "commercial_use_filter": True,
                "result_count": len(
                    results
                ),
                "results": results,
            },
        )
        emit_web_event(
            operation="search",
            state="READY",
            result_count=len(
                results
            ),
            output=str(
                output_path
            ),
        )
        print(
            (
                f"Web image search ready: {len(results)} commercially usable "
                "Openverse result(s)."
            ),
            flush=True,
        )
        return 0

    if args.command == "download":
        results_path = Path(
            args.results
        ).resolve()
        try:
            data = json.loads(
                results_path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            print(
                f"ERROR: Could not read web image results: {exc}",
                flush=True,
            )
            return 1

        results = data.get(
            "results",
            [],
        )
        if not isinstance(
            results,
            list,
        ):
            results = []

        index = int(
            args.index
        )
        if not (
            0
            <= index
            < len(
                results
            )
        ):
            print(
                "ERROR: Selected web image result is no longer available.",
                flush=True,
            )
            return 1

        result = results[index]
        if not isinstance(
            result,
            dict,
        ):
            print(
                "ERROR: Selected web image result is invalid.",
                flush=True,
            )
            return 1

        try:
            selected = download_result_image(
                result,
                slot_id=str(
                    args.slot_id
                ),
            )
        except Exception as exc:
            print(
                f"ERROR: Could not download selected web image: {exc}",
                flush=True,
            )
            return 1

        output_path = Path(
            args.output
        ).resolve()
        write_json_file(
            output_path,
            {
                "provider": "openverse",
                "slot_id": str(
                    args.slot_id
                ),
                "selected_index": index,
                "result": selected,
            },
        )
        emit_web_event(
            operation="download",
            state="READY",
            slot_id=str(
                args.slot_id
            ),
            path=selected.get(
                "local_path",
                "",
            ),
            output=str(
                output_path
            ),
        )
        print(
            "Selected web image downloaded.",
            flush=True,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(
        cli_main()
    )
