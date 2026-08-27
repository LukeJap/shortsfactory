"""Content-addressed caches for expensive Track A work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import read_json, write_json


CACHE_SCHEMA_VERSION = 1
FINGERPRINT_CHUNK_BYTES = 1024 * 1024


def source_fingerprint(path: Path) -> dict[str, Any]:
    """Return a stable identity using metadata plus head/tail content hashes."""
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Source video not found: {path}")
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(FINGERPRINT_CHUNK_BYTES))
        if stat.st_size > FINGERPRINT_CHUNK_BYTES:
            handle.seek(max(0, stat.st_size - FINGERPRINT_CHUNK_BYTES))
            digest.update(handle.read(FINGERPRINT_CHUNK_BYTES))
    return {
        "resolved_path": str(path),
        "size_bytes": int(stat.st_size),
        "modified_ns": int(stat.st_mtime_ns),
        "edge_sha256": digest.hexdigest(),
    }


def cache_key(
    *,
    identity: dict[str, Any],
    source_identity: dict[str, Any] | None,
    artifact: str,
    prompt_version: str,
    model_version: str,
) -> str:
    material = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "artifact": artifact,
        "identity": identity,
        "source_identity": source_identity or {},
        "prompt_version": prompt_version,
        "model_version": model_version,
    }
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


class ArtifactCache:
    """JSON cache scoped to one recap output directory."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            return read_json(path)
        except Exception:
            return None

    def put(self, key: str, payload: dict[str, Any]) -> Path:
        path = self.path_for(key)
        write_json(path, payload)
        return path
