# SPDX-License-Identifier: GPL-3.0-or-later
"""The scene's mask zone (bpy side): what the ComfyQ Mask tool picked and drew, and the mask pass made from it.

One zone per scene (`scene.comfyq.zone_*`): picked objects + shapes in frame coordinates (`zone.py`). The bar's
*Render + Selection Mask* source and the graph's Mask node use the zone when it has anything in it; otherwise the
objects selected at Run time, as before (so old files and presets keep working).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import bpy

from .. import zone as zmod


def shapes(scene) -> zmod.Zone:
    return zmod.loads(scene.comfyq.zone_shapes)


def objects(scene) -> list[bpy.types.Object]:
    """Picked objects still in this scene (deleted ones are skipped)."""
    names = set(scene.objects.keys())
    return [it.obj for it in scene.comfyq.zone_objects if it.obj is not None and it.obj.name in names]


def active(scene) -> bool:
    """Does the scene have a zone (picked objects or drawn shapes)?"""
    return bool(objects(scene)) or not shapes(scene).empty


def describe(scene) -> str:
    return zmod.describe(len(objects(scene)), shapes(scene))


def signature(scene) -> str:
    """What the mask depends on beyond the scene render: for graph hashes (a new zone makes the result out of date)."""
    cq = scene.comfyq
    h = hashlib.sha1(repr((sorted(o.name for o in objects(scene)), cq.zone_shapes, round(cq.zone_grow, 3),
                           round(cq.zone_feather, 3), cq.zone_invert)).encode())
    return h.hexdigest()[:16]


def mask(scene, selected_now: list, style: str = "CLAY", max_edge: int | None = None,
         frame: int | None = None) -> Path:
    """The mask pass for this scene: its zone when there is one, else the objects selected now."""
    from . import render
    cq = scene.comfyq
    if active(scene):
        return render.mask_pass(scene, objects(scene), style, max_edge, frame, shapes=shapes(scene),
                                grow=cq.zone_grow / 100.0, feather=cq.zone_feather / 100.0, invert=cq.zone_invert)
    return render.mask_pass(scene, selected_now, style, max_edge, frame)


# -- editing (the tool's operators call these) ---------------------------------------------------------------------
def toggle_object(scene, obj) -> bool:
    """Add `obj` to the zone, or take it out if it is in. Returns True when it is now in the zone."""
    items = scene.comfyq.zone_objects
    for n, it in enumerate(items):
        if it.obj == obj:
            items.remove(n)                 # never keep a reference across remove (gotcha 7)
            return False
    items.add().obj = obj
    return True


def add_shape(scene, shape: zmod.Shape) -> None:
    z = shapes(scene)
    z.shapes.append(shape)
    scene.comfyq.zone_shapes = zmod.dumps(z)


def clear(scene) -> None:
    scene.comfyq.zone_objects.clear()
    scene.comfyq.zone_shapes = ""
