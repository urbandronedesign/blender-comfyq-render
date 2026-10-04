# SPDX-License-Identifier: GPL-3.0-or-later
"""Labelled surfaces of a render (no bpy): what SAM3 found where, and the prompt that names them.

Architecture renders start from a blocked-out scene; SAM3 segments its render by name ("floor", "wall"…),
each found surface gets a material description, and the render prompt lists them so the image model
knows what every surface is made of. The masks also drive per-surface inpainting.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# What an interior / exterior blocking scene usually shows. SAM3 takes short concrete nouns and ComfyQ's
# workflow returns ONE surface per phrase: "a|b|c" = label a, the union of what each phrase finds.
ARCH_LABELS = ("floor", "wall|left wall|right wall", "ceiling", "window", "door", "stairs", "column", "sofa", "table",
               "rug", "shelf", "lamp", "plant", "facade", "roof", "terrace", "pavement", "grass", "sky")
MIN_COVERAGE = 0.002            # a "found" surface covers at least 0.2 % of the picture


@dataclass
class Region:
    label: str
    coverage: float                                  # 0–1 of the picture
    bbox: tuple[float, float, float, float]          # x0, y0, x1, y1 in 0–1, y down (image rows)
    mask: str = ""                                   # white = the surface (PNG)
    extra: dict = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.coverage >= MIN_COVERAGE

    @property
    def where(self) -> str:
        return where(self.bbox)


def where(bbox: tuple[float, float, float, float]) -> str:
    """'bottom', 'top left', 'centre', 'across the picture'… from a normalised box (y down)."""
    x0, y0, x1, y1 = bbox
    if x1 - x0 > 0.8 and y1 - y0 > 0.8:
        return "across the picture"
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    col = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else ""
    row = "top" if cy < 1 / 3 else "bottom" if cy > 2 / 3 else ""
    if x1 - x0 > 0.8:
        col = ""                                     # spans the width: only the row matters
    if y1 - y0 > 0.8:
        row = ""
    return " ".join(p for p in (row, col) if p) or "centre"


def _plural(label: str) -> bool:
    return label.endswith("s") and not label.endswith("ss")


def surface_prompt(regions: list[Region], materials: dict[str, str] | None = None, intro: str = "",
                   outro: str = "") -> str:
    """One prompt naming each found surface and its material, biggest surfaces first:
    'Photorealistic interior. The floor is light oak parquet. The walls are white lime plaster. …'
    Surfaces without a material are still named (with where they are) so the model keeps them."""
    materials = {k.lower(): v for k, v in (materials or {}).items() if v}
    parts = [intro.strip()] if intro.strip() else []
    for r in sorted((r for r in regions if r.found), key=lambda r: -r.coverage):
        name = r.label.lower()
        what = materials.get(name)
        verb = "are" if _plural(name) else "is"
        if what:
            parts.append(f"The {name} {verb} {what.strip().rstrip('.')}.")
        else:
            parts.append(f"The {name} ({r.where}).")
    if outro.strip():
        parts.append(outro.strip())
    return " ".join(parts)


def label_name(label: str) -> str:
    """'wall|left wall|right wall' -> 'wall' (the name the prompt and the masks use)."""
    return label.split("|")[0].strip().lower()


def labels_from(text: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """'floor, wall; window' or a list -> ['floor', 'wall', 'window'] (order kept, no duplicates)."""
    if not text:
        return list(ARCH_LABELS[:12])
    items = text if isinstance(text, list | tuple) else text.replace(";", ",").split(",")
    out: list[str] = []
    for it in items:
        it = str(it).strip().lower()
        if it and it not in out:
            out.append(it)
    return out
