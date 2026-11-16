# SPDX-License-Identifier: GPL-3.0-or-later
"""The scene's mask zone (bpy side): what the ComfyQ Mask tool picked and drew, and the mask pass made from it.

One zone per scene (`scene.comfyq.zone_*`): picked objects + shapes in frame coordinates (`zone.py`). The bar's
*Render + Selection Mask* source and the graph's Mask node use the zone when it has anything in it; otherwise the
objects selected at Run time, as before (so old files and presets keep working).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy

from .. import zone as zmod


def shapes(scene) -> zmod.Zone:
    return zmod.loads(scene.comfyq.zone_shapes)


def objects(scene) -> list[bpy.types.Object]:
    """Picked objects still in this scene (deleted ones are skipped)."""
    names = set(scene.objects.keys())
    return [it.obj for it in scene.comfyq.zone_objects if it.obj is not None and it.obj.name in names]


def found_info(scene) -> dict:
    """What Find by Name found last (SAM3 on the camera view): words, frame, regions with their mask files."""
    try:
        info = json.loads(scene.comfyq.zone_found or "{}")
    except ValueError:
        return {}
    info["regions"] = [r for r in info.get("regions", []) if r.get("mask") and Path(r["mask"]).is_file()]
    return info


def found(scene) -> list[dict]:
    return found_info(scene).get("regions", [])


def picked(scene) -> list[dict]:
    """Found surfaces that are in the zone (ticked)."""
    return [r for r in found(scene) if r.get("picked")]


def active(scene) -> bool:
    """Does the scene have a zone (picked objects, drawn shapes or found surfaces)?"""
    return bool(objects(scene)) or not shapes(scene).empty or bool(picked(scene))


def describe(scene) -> str:
    base = zmod.describe(len(objects(scene)), shapes(scene))
    n = len(picked(scene))
    if not n:
        return base
    found_txt = f"{n} found"
    return found_txt if not objects(scene) and shapes(scene).empty else f"{base} · {found_txt}"


def set_found(scene, info: dict) -> None:
    scene.comfyq.zone_found = json.dumps(info)


def toggle_found(scene, label: str) -> None:
    info = found_info(scene)
    for r in info.get("regions", []):
        if r["label"] == label:
            r["picked"] = not r.get("picked")
    set_found(scene, info)


def signature(scene) -> str:
    """What the mask depends on beyond the scene render: for graph hashes (a new zone makes the result out of date)."""
    cq = scene.comfyq
    h = hashlib.sha1(repr((sorted(o.name for o in objects(scene)), cq.zone_shapes, round(cq.zone_grow, 3),
                           round(cq.zone_feather, 3), cq.zone_invert,
                           [(r["label"], r["mask"]) for r in picked(scene)])).encode())
    return h.hexdigest()[:16]


def mask(scene, selected_now: list, style: str = "CLAY", max_edge: int | None = None,
         frame: int | None = None) -> Path:
    """The mask pass for this scene: its zone when there is one, else the objects selected now."""
    from . import render
    cq = scene.comfyq
    if active(scene):
        return render.mask_pass(scene, objects(scene), style, max_edge, frame, shapes=shapes(scene),
                                grow=cq.zone_grow / 100.0, feather=cq.zone_feather / 100.0, invert=cq.zone_invert,
                                regions=picked(scene))
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
    scene.comfyq.zone_found = ""
