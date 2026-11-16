# SPDX-License-Identifier: GPL-3.0-or-later
"""The ComfyQ Sketch (audit 2, phase 4; docs/ux-audit-2-plan.md §3.2): a Grease Pencil drawing made in the camera
view, sent as an image input — over the clay scene, or alone on white (*Sketch Only*).

The drawing is a Grease Pencil object parented to the scene camera, a little in front of it, drawn on the view
plane through its origin, shown in front of everything: whatever the camera moves to, the strokes stay where they
were drawn in the frame. Every other pass (scene render, mask, animation) leaves it out (`visibility`), so a sketch
never sneaks into a render it was not meant for.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager

import bpy

MARK = "comfyq_sketch"
NAME = "ComfyQ Sketch"
INK = "ComfyQ Sketch Ink"
FRAME_WIDTH = 15.0     # metres the frame spans where the sketch is: Blender's brush sizes are in scene units (the
                       # Pencil is 0.06 m), so this gives lines of about 0.4 % of the frame, as on paper. The sketch
                       # shows in front of everything (In Front, also in renders), whatever is nearer the camera.


def depth(cam) -> float:
    """How far in front of the camera the sketch sits."""
    data = cam.data
    if data.type == "ORTHO":
        d = data.clip_start * 2 + 0.1
    else:
        import math
        d = FRAME_WIDTH / (2 * math.tan(data.angle / 2))
    return min(d, data.clip_end * 0.5)


def plane_width(scene) -> float:
    """The frame's width on the sketch's plane (scripts drawing strokes use it)."""
    import math
    data = scene.camera.data
    return data.ortho_scale if data.type == "ORTHO" else 2 * depth(scene.camera) * math.tan(data.angle / 2)


def objects(scene: bpy.types.Scene) -> list[bpy.types.Object]:
    return [o for o in scene.objects if o.get(MARK)]


def has_strokes(scene: bpy.types.Scene) -> bool:
    return any(_stroke_count(o.data) for o in objects(scene))


def _drawings(gp):
    for layer in getattr(gp, "layers", ()):
        for fr in layer.frames:
            if fr.drawing is not None:
                yield layer, fr, fr.drawing


def _stroke_count(gp) -> int:
    return sum(len(d.strokes) for _l, _f, d in _drawings(gp))


def describe(scene: bpy.types.Scene) -> str:
    n = sum(_stroke_count(o.data) for o in objects(scene))
    if not objects(scene):
        return "No sketch yet"
    return f"{n} stroke{'s' if n != 1 else ''}" if n else "Sketch started, nothing drawn yet"


def ensure(scene: bpy.types.Scene) -> bpy.types.Object:
    """The scene's sketch object (made the first time), in front of the scene camera."""
    cam = scene.camera
    if cam is None:
        raise RuntimeError("The scene has no camera: add one (the sketch is drawn through it)")
    ob = next(iter(objects(scene)), None)
    if ob is None:
        gp = bpy.data.grease_pencils.new(NAME)
        ob = bpy.data.objects.new(NAME, gp)
        ob[MARK] = 1
        scene.collection.objects.link(ob)
        mat = bpy.data.materials.get(INK)
        if mat is None:
            mat = bpy.data.materials.new(INK)
            bpy.data.materials.create_gpencil_data(mat)
            mat.grease_pencil.color = (0.02, 0.02, 0.02, 1.0)
        gp.materials.append(mat)
        layer = gp.layers.new("Lines")
        layer.frames.new(scene.frame_start)          # one drawing for the whole frame range
        gp.layers.active = layer
    if ob.parent != cam:                             # follows the camera (a new camera: the sketch moves to it)
        ob.parent = cam
        ob.matrix_parent_inverse.identity()
    ob.location = (0.0, 0.0, -depth(cam))
    ob.rotation_euler = (0.0, 0.0, 0.0)
    ob.scale = (1.0, 1.0, 1.0)
    ob.show_in_front = True
    ob.hide_render = False
    return ob


def remove(scene: bpy.types.Scene) -> int:
    n = 0
    for ob in objects(scene):
        data = ob.data
        bpy.data.objects.remove(ob)
        if data is not None and data.users == 0:
            bpy.data.grease_pencils.remove(data)
        n += 1
    return n


def signature(scene: bpy.types.Scene) -> str:
    """A hash of the strokes (positions, widths, colours): a new stroke makes what the sketch feeds out of date."""
    import numpy as np
    h = hashlib.sha1()
    for ob in sorted(objects(scene), key=lambda o: o.name):
        h.update(repr((ob.name, ob.hide_render, ob.parent.name if ob.parent else None, tuple(ob.location))).encode())
        for layer, fr, d in _drawings(ob.data):
            h.update(repr((layer.name, layer.hide, layer.opacity, fr.frame_number, len(d.strokes))).encode())
            for name, width in (("position", 3), ("radius", 1), ("opacity", 1), ("vertex_color", 4)):
                attr = d.attributes.get(name)
                if attr is None or not len(attr.data):
                    continue
                buf = np.empty(len(attr.data) * width, dtype=np.float32)
                try:
                    attr.data.foreach_get("vector" if width == 3 else "color" if width == 4 else "value", buf)
                except (AttributeError, RuntimeError, TypeError):
                    continue
                h.update(buf.tobytes())
        h.update(repr([(m.name, tuple(m.grease_pencil.color)) for m in ob.data.materials
                       if m is not None and m.grease_pencil is not None]).encode())
    return h.hexdigest()[:16]


@contextmanager
def visibility(scene: bpy.types.Scene, mode: str = "HIDE"):
    """What a pass renders: HIDE = the scene without the sketch (every pass but the sketch's own); SHOW = the scene
    with the sketch over it; ONLY = the sketch alone on white, in colour."""
    sk = objects(scene)
    saved = [(o, o.hide_render) for o in scene.objects]
    world = scene.world
    world_color = tuple(world.color) if world is not None else None
    ims = scene.render.image_settings
    cmode = ims.color_mode
    try:
        if mode == "HIDE":
            for o in sk:
                o.hide_render = True
        elif mode == "ONLY":
            for o in scene.objects:
                if o not in sk:
                    o.hide_render = True
            if world is not None:
                world.color = (1.0, 1.0, 1.0)
            ims.color_mode = "RGB"
        else:
            ims.color_mode = "RGB"                  # coloured ink stays coloured over the grey clay
        yield
    finally:
        for o, hidden in saved:
            if o.hide_render != hidden:
                o.hide_render = hidden
        if world is not None and world_color is not None:
            world.color = world_color
        ims.color_mode = cmode
