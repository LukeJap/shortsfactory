"""
Pure, Qt-state-free geometry helpers for the corner/edge resize-handle
interaction shared by the AI-visual, emoji, and caption placement-editor
previews. No widget/data-model access here -- each preview mixin owns its
own handle widget pool, hit-testing, and data model, and calls into these
functions only for the shared math: where a handle sits relative to an
overlay's screen rect, and how far a drag has scaled/stretched that rect
relative to a fixed opposite anchor point.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRect


HANDLE_PIXELS = 12

CORNER_NAMES = ("nw", "ne", "sw", "se")
EDGE_NAMES = ("n", "s", "e", "w")

OPPOSITE_CORNER = {
    "nw": "se",
    "ne": "sw",
    "sw": "ne",
    "se": "nw",
}


def corner_point(
    corner: str,
    x: int,
    y: int,
    w: int,
    h: int,
) -> tuple[int, int]:
    px = x if corner in ("nw", "sw") else x + w
    py = y if corner in ("nw", "ne") else y + h
    return (px, py)


def corner_handle_rect(
    corner: str,
    x: int,
    y: int,
    w: int,
    h: int,
) -> QRect:
    px, py = corner_point(corner, x, y, w, h)
    half = HANDLE_PIXELS // 2
    return QRect(px - half, py - half, HANDLE_PIXELS, HANDLE_PIXELS)


def corner_handle_rects(
    x: int,
    y: int,
    w: int,
    h: int,
) -> dict[str, QRect]:
    return {
        corner: corner_handle_rect(corner, x, y, w, h)
        for corner in CORNER_NAMES
    }


def edge_midpoint(
    edge: str,
    x: int,
    y: int,
    w: int,
    h: int,
) -> tuple[int, int]:
    if edge == "n":
        return (x + w // 2, y)
    if edge == "s":
        return (x + w // 2, y + h)
    if edge == "w":
        return (x, y + h // 2)
    return (x + w, y + h // 2)


def edge_handle_rect(
    edge: str,
    x: int,
    y: int,
    w: int,
    h: int,
) -> QRect:
    px, py = edge_midpoint(edge, x, y, w, h)
    half = HANDLE_PIXELS // 2
    return QRect(px - half, py - half, HANDLE_PIXELS, HANDLE_PIXELS)


def edge_handle_rects(
    x: int,
    y: int,
    w: int,
    h: int,
) -> dict[str, QRect]:
    return {
        edge: edge_handle_rect(edge, x, y, w, h)
        for edge in EDGE_NAMES
    }


def uniform_scale_ratio(
    anchor_x: float,
    anchor_y: float,
    start_x: float,
    start_y: float,
    mouse_x: float,
    mouse_y: float,
) -> float:
    """
    Ratio to multiply a corner-anchored uniform scale by, derived from how
    far the mouse has moved from the fixed (opposite-corner) anchor point,
    relative to where the dragged corner started. 1.0 = no change.
    """

    start_dist = math.hypot(start_x - anchor_x, start_y - anchor_y)
    if start_dist <= 0:
        return 1.0
    mouse_dist = math.hypot(mouse_x - anchor_x, mouse_y - anchor_y)
    return mouse_dist / start_dist


def axis_scale_ratio(
    anchor: float,
    start: float,
    mouse: float,
) -> float:
    """
    Single-axis equivalent of uniform_scale_ratio(), for edge-handle
    (non-uniform stretch) drags.
    """

    start_span = abs(start - anchor)
    if start_span <= 0:
        return 1.0
    return abs(mouse - anchor) / start_span


def format_scale_readout(
    scale_x: float,
    scale_y: float | None = None,
) -> str:
    if scale_y is None or round(scale_x, 2) == round(scale_y, 2):
        return f"{round(scale_x * 100)}%"
    return f"{round(scale_x * 100)}% x {round(scale_y * 100)}%"
