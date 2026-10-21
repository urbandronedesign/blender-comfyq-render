# SPDX-License-Identifier: GPL-3.0-or-later
"""The `model3d` input: scene objects exported as a GLB file (main thread only).

ComfyQ's mesh workflows (remesh / decimate, UV unwrap) take one mesh file. Modifiers are applied
and the objects keep their world placement; the user's selection is restored afterwards.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

from .render import _out_path

# ComfyQ's remesh returns an EMPTY mesh, silently, for models of ~80 units or more (a model exported in
# millimetres); 19 units worked. Above this size the bar and the graph say so before sending.
LARGE_SIZE = 50.0

EXPORTABLE = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def exportable(objects) -> list[bpy.types.Object]:
    return [o for o in objects if o.type in EXPORTABLE]


def size(objects) -> float:
    """The largest side of the objects' world bounding box (0 when there is nothing to export)."""
    corners = [o.matrix_world @ Vector(c) for o in exportable(objects) for c in o.bound_box]
    if not corners:
        return 0.0
    return max(max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3))


def bounds(objects) -> tuple[Vector, float] | None:
    """Centre and radius (half the bounding-box diagonal) of the objects, in world space."""
    corners = [o.matrix_world @ Vector(c) for o in exportable(objects) for c in o.bound_box]
    if not corners:
        return None
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    return (lo + hi) / 2, max((hi - lo).length / 2, 1e-6)


def fingerprint(objects) -> str:
    """A hash of what the export of `objects` depends on: names, placement, modifiers, mesh shape, materials."""
    h = hashlib.sha1()
    for ob in sorted(exportable(objects), key=lambda o: o.name):
        h.update(repr((ob.name, ob.data.name if ob.data else None,
                       [tuple(round(x, 5) for x in row) for row in ob.matrix_world],
                       [(m.name, m.type, m.show_viewport) for m in getattr(ob, "modifiers", ())],
                       [sl.material.name for sl in ob.material_slots if sl.material])).encode())
        if isinstance(ob.data, bpy.types.Mesh):
            co = np.empty(len(ob.data.vertices) * 3, dtype=np.float32)
            ob.data.vertices.foreach_get("co", co)
            h.update(co.tobytes())
    return h.hexdigest()[:16]


def size_warning(objects) -> str:
    s = size(objects)
    if s < LARGE_SIZE:
        return ""
    return (f"The model is {s:.0f} m across: remeshing models this large can return an empty mesh. "
            "Scale it down or turn off Watertight Remesh")


def mesh_pass(objects) -> Path:
    """Export `objects` as one GLB file (modifiers applied); returns its path."""
    view_layer = bpy.context.view_layer
    objs = [o for o in exportable(objects) if o.name in view_layer.objects]   # others cannot be selected
    if not objs:
        raise RuntimeError("Select the objects to send (meshes, curves or text)")
    out = _out_path("mesh").with_suffix(".glb")
    selected = [o for o in view_layer.objects if o.select_get()]
    active = view_layer.objects.active
    hidden = [o for o in objs if o.hide_get()]
    try:
        for o in selected:
            o.select_set(False)
        for o in objs:
            o.hide_set(False)                  # hidden objects cannot be selected (nor exported)
            o.select_set(True)
        bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=True, export_apply=True,
                                  export_animations=False, export_cameras=False, export_lights=False)
    finally:
        for o in objs:
            o.select_set(False)
        for o in hidden:
            o.hide_set(True)
        for o in selected:
            if o.name in view_layer.objects:
                o.select_set(True)
        view_layer.objects.active = active
    if not out.is_file():
        raise RuntimeError("The GLB export wrote no file")
    return out
