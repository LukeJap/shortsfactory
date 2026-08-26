"""Helpers for preserving completed Shorts across repeated renders."""

from __future__ import annotations

import re
from pathlib import Path


ARCHIVED_CLIP_PATTERN = re.compile(
    r"^short(?P<index>[1-9][0-9]*)\.mp4$",
    re.IGNORECASE,
)


def archived_clip_index(path: Path) -> int | None:
    """Return the numeric archive index for a ``shortN.mp4`` path."""

    match = ARCHIVED_CLIP_PATTERN.fullmatch(path.name)
    if match is None:
        return None

    try:
        return int(match.group("index"))
    except ValueError:
        return None


def is_archived_clip_name(name: str) -> bool:
    """Return whether *name* is a numbered final clip filename."""

    return archived_clip_index(Path(name)) is not None


def next_archived_clip_path(rendered_dir: Path) -> Path:
    """Choose the next never-used ``shortN.mp4`` path in *rendered_dir*."""

    highest_index = 0
    if rendered_dir.exists():
        for path in rendered_dir.iterdir():
            index = archived_clip_index(path)
            if index is not None:
                highest_index = max(highest_index, index)

    return rendered_dir / f"short{highest_index + 1}.mp4"


def archive_final_video(
    final_path: Path,
    rendered_dir: Path | None = None,
) -> Path:
    """Move a completed fixed-path final into its next numbered archive."""

    if not final_path.exists():
        raise FileNotFoundError(f"Final video not found: {final_path}")

    destination_dir = rendered_dir or final_path.parent
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = next_archived_clip_path(destination_dir)

    # The destination is selected from the existing directory contents and
    # replace() keeps the move atomic on the same filesystem.
    final_path.replace(destination)
    return destination
