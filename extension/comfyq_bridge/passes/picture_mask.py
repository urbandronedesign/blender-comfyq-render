# SPDX-License-Identifier: GPL-3.0-or-later
"""A mask on ANY picture (a photo, an AI result, a keyframe): what the Picture Mask node sends to inpaint.

The zone = the regions SAM3 found by name that are ticked + shapes drawn on the picture (zone.py, picture
coordinates: x 0..1 left → right, y 0..1 bottom → top), widened by Grow, softened by Feather, optionally inverted.
The mask = the picture with alpha 0 in the zone (ComfyQ's convention: transparent = repaint).
"""
from __future__ import annotations

import functools
from pathlib import Path

import numpy as np

from .. import zone as zmod
from . import segment as seg
from .render import finish_zone, zone_coverage

RED = np.array((0.95, 0.12, 0.08), np.float32)


@functools.lru_cache(maxsize=64)
def _matte(path: str, mtime: float, w: int, h: int) -> np.ndarray:
    """A region's mask (white = the object) as bool (h, w), row 0 = BOTTOM (Blender's pixel order). Cached."""
    return seg._fit(seg.matte(Path(path)), w, h)[::-1].copy()


def matte(path: str, w: int, h: int) -> np.ndarray:
    p = Path(path)
    return _matte(str(p), p.stat().st_mtime if p.is_file() else 0.0, w, h)


def coverage(regions: list[dict], shapes: zmod.Zone, w: int, h: int) -> np.ndarray:
    """0..1 (h, w), row 0 = bottom: ticked regions, then the drawn shapes in order (add / cut)."""
    base = np.zeros((h, w), np.float32)
    for r in regions:
        if r.get("picked") and r.get("mask") and Path(r["mask"]).is_file():
            base = np.maximum(base, matte(r["mask"], w, h).astype(np.float32))
    return zone_coverage(shapes, w, h, base) if shapes.shapes else base


def compose(picture: Path, regions: list[dict], shapes: zmod.Zone, grow: float, feather: float, invert: bool,
            out: Path) -> Path:
    """The inpaint input: `picture` with alpha 0 where the zone is (within ComfyQ's upload size)."""
    img = seg.read_rgba(picture)[::-1].copy()                 # row 0 = bottom, like the coverage
    h, w = img.shape[:2]
    cover = finish_zone(coverage(regions, shapes, w, h), w, grow, feather, invert)
    if not cover.any():
        raise RuntimeError("The zone is empty: tick a found object or draw on the picture (Edit on Picture)")
    if (cover > 0.5).all():
        raise RuntimeError("The zone covers the whole picture: nothing would be kept")
    img[..., 3] = 1.0 - cover
    return seg.fit_upload(seg.write_rgba(img[::-1], out))


def preview(picture: Path, regions: list[dict], shapes: zmod.Zone, grow: float, feather: float, invert: bool,
            out: Path) -> Path:
    """What the student looks at: the picture, the ZONE in red (with grow / feather / invert as sent), and each found
    object that is not ticked in its own colour, lighter (so it can be clicked in)."""
    img = seg.read_rgba(picture)[::-1].copy()
    h, w = img.shape[:2]
    for i, r in enumerate(regions):
        if not r.get("picked") and r.get("mask") and Path(r["mask"]).is_file():
            m = matte(r["mask"], w, h)
            col = np.array(seg.PALETTE[i % len(seg.PALETTE)], np.float32)
            img[m, :3] = img[m, :3] * 0.75 + col * 0.25
    cover = finish_zone(coverage(regions, shapes, w, h), w, grow, feather, invert)[..., None]
    img[..., :3] = img[..., :3] * (1 - 0.55 * cover) + RED * (0.55 * cover)
    img[..., 3] = 1.0
    return seg.write_rgba(img[::-1], out)


def region_at(regions: list[dict], u: float, v: float, w: int, h: int) -> str | None:
    """The found object under a click (picture coordinates, v up): the smallest one there (a cushion on a sofa)."""
    x, y = int(u * w), int(v * h)
    if not (0 <= x < w and 0 <= y < h):
        return None
    hits = [(r.get("coverage_pct", 100.0), r["label"]) for r in regions
            if r.get("mask") and Path(r["mask"]).is_file() and matte(r["mask"], w, h)[y, x]]
    return min(hits)[1] if hits else None
