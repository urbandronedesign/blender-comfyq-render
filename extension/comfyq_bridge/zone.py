# SPDX-License-Identifier: GPL-3.0-or-later
"""The mask ZONE of a scene, without bpy: free shapes drawn over the camera frame, in frame coordinates.

A zone = objects picked in the 3D View (stored by the add-on on the scene) + shapes drawn with the ComfyQ Mask tool.
Shapes are polygons in FRAME coordinates: x 0..1 left → right, y 0..1 bottom → top of the scene camera's frame,
so a zone stays put whatever the render size. Each shape adds to the zone or cuts it ("add" / "sub"), in order.
Grow and Feather are fractions of the picture width (resolution independent).

The pixels are made in `passes/render.zone_mask` (numpy, inside Blender); `contains` here is the reference used by
the unit tests and to check that raster.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

MAX_POINTS = 400            # a lasso is simplified to at most this many points
MIN_STEP = 0.004            # lasso points closer than this (frame units) are dropped
MIN_SIZE = 0.004            # a box thinner than this is a click, not a zone


@dataclass
class Shape:
    points: list[tuple[float, float]]
    op: str = "add"                       # "add" or "sub"

    def to_wire(self) -> dict:
        return {"op": self.op, "points": [[round(x, 5), round(y, 5)] for x, y in self.points]}


@dataclass
class Zone:
    shapes: list[Shape] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(s.op == "add" for s in self.shapes)


def loads(text: str) -> Zone:
    """The scene's stored shapes (JSON) → Zone. Bad or empty text = an empty zone."""
    try:
        data = json.loads(text) if text else []
    except ValueError:
        return Zone()
    shapes = []
    for d in data if isinstance(data, list) else []:
        pts = [(float(p[0]), float(p[1])) for p in d.get("points") or [] if len(p) >= 2]
        if len(pts) >= 3:
            shapes.append(Shape(pts, "sub" if d.get("op") == "sub" else "add"))
    return Zone(shapes)


def dumps(zone: Zone) -> str:
    return json.dumps([s.to_wire() for s in zone.shapes], separators=(",", ":"))


def to_frame(px: float, py: float, frame_min: tuple[float, float], frame_max: tuple[float, float]) -> tuple[float, float]:
    """A point of the 3D View region → frame coordinates, given where the camera frame is drawn in the region
    (camera view: an axis-aligned rectangle). Points outside the frame give values outside 0..1 (clamped later)."""
    w = (frame_max[0] - frame_min[0]) or 1e-9
    h = (frame_max[1] - frame_min[1]) or 1e-9
    return (px - frame_min[0]) / w, (py - frame_min[1]) / h


def to_region(fx: float, fy: float, frame_min: tuple[float, float], frame_max: tuple[float, float]) -> tuple[float, float]:
    return (frame_min[0] + fx * (frame_max[0] - frame_min[0]), frame_min[1] + fy * (frame_max[1] - frame_min[1]))


def clamp(p: tuple[float, float]) -> tuple[float, float]:
    return min(1.0, max(0.0, p[0])), min(1.0, max(0.0, p[1]))


def box(a: tuple[float, float], b: tuple[float, float], op: str = "add") -> Shape | None:
    """A rectangle from two corners (frame coords), clamped to the frame; None when too thin (a click)."""
    (x0, y0), (x1, y1) = clamp(a), clamp(b)
    if abs(x1 - x0) < MIN_SIZE or abs(y1 - y0) < MIN_SIZE:
        return None
    xa, xb, ya, yb = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    return Shape([(xa, ya), (xb, ya), (xb, yb), (xa, yb)], op)


def lasso(path: list[tuple[float, float]], op: str = "add") -> Shape | None:
    """A free shape from a mouse path (frame coords): close points dropped, at most MAX_POINTS, clamped.
    None when it encloses (almost) nothing."""
    pts: list[tuple[float, float]] = []
    for p in path:
        p = clamp(p)
        if not pts or abs(p[0] - pts[-1][0]) + abs(p[1] - pts[-1][1]) >= MIN_STEP:
            pts.append(p)
    if len(pts) > MAX_POINTS:
        step = len(pts) / MAX_POINTS
        pts = [pts[int(i * step)] for i in range(MAX_POINTS)]
    if len(pts) < 3 or abs(area(pts)) < MIN_SIZE * MIN_SIZE:
        return None
    return Shape(pts, op)


def area(pts: list[tuple[float, float]]) -> float:
    """Signed area (shoelace)."""
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1], strict=True))


def inside(pts: list[tuple[float, float]], x: float, y: float) -> bool:
    """Even-odd point-in-polygon (the same rule as the numpy raster)."""
    c = False
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1], strict=True):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            c = not c
    return c


def contains(zone: Zone, x: float, y: float) -> bool:
    """Is the frame point in the zone's shapes? Shapes apply in order: add sets, sub clears."""
    hit = False
    for s in zone.shapes:
        if inside(s.points, x, y):
            hit = s.op == "add"
    return hit


def describe(n_objects: int, zone: Zone) -> str:
    """'2 objects · 1 shape' / 'Empty' for the bar, the node and the tool header."""
    def count(n, one):
        return f"{n} {one if n == 1 else one + 's'}"
    parts = []
    if n_objects:
        parts.append(count(n_objects, "object"))
    adds = sum(s.op == "add" for s in zone.shapes)
    subs = len(zone.shapes) - adds
    if adds:
        parts.append(count(adds, "shape"))
    if subs:
        parts.append(f"{subs} cut")
    return " · ".join(parts) or "Empty"


def merge_found(old: list[dict], new: list[dict]) -> list[dict]:
    """Regions SAM3 just found (segmentation.region_dicts), keeping what the student ticked last time by label;
    a newly found object starts ticked. Objects SAM3 did not find are dropped."""
    before = {r["label"]: bool(r.get("picked")) for r in old}
    return [{**r, "picked": before.get(r["label"], True)} for r in new if r.get("found")]


def toggle(regions: list[dict], label: str) -> bool | None:
    """Tick / untick one found object; returns its new state (None: no such object)."""
    for r in regions:
        if r["label"] == label:
            r["picked"] = not r.get("picked")
            return r["picked"]
    return None


def words(text: str) -> list[str]:
    """'sofa, rug ; lamp\\nwall|left wall' → ['sofa', 'rug', 'lamp', 'wall|left wall'] (| merges phrases)."""
    import re
    return [w.strip() for w in re.split(r"[,;\n]", text or "") if w.strip()]


def grow_px(grow: float, width: int) -> int:
    """Grow (a fraction of the picture width) in pixels for a picture `width` wide."""
    return max(0, round(grow * width))
