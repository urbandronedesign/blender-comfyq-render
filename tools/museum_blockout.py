# SPDX-License-Identifier: GPL-3.0-or-later
"""A blocked-out museum (a cantilevered box on a recessed glazed ground floor and a concrete plinth, a few cut-out
windows) and its 4 turnaround views — front, left, back, right — rendered by the plugin's own clay pass with
orthographic cameras, so the views agree by construction (input for Pixal3D Multi-View after an AI restyle).

    blender -b --factory-startup --python tools/museum_blockout.py -- <out folder> [<.blend to save>]
Needs the extension installed in the Blender profile it runs with (use the sandbox profile).
"""
import math
import shutil
import sys
from pathlib import Path

import addon_utils
import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(argv[0] if argv else "museum_views")
OUT.mkdir(parents=True, exist_ok=True)
addon_utils.enable("bl_ext.user_default.comfyq_bridge", default_set=True)
from bl_ext.user_default.comfyq_bridge.passes import render

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene


def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    return o


def cut(target, size, loc):
    c = box("cut", size, loc)
    m = target.modifiers.new("cut", "BOOLEAN")
    m.operation, m.object = "DIFFERENCE", c
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(c)


# metres: a 36 × 20 × 14 m copper volume over a 24 × 12 m glazed ground floor, on a 44 × 28 m plinth
box("Plinth", (44, 28, 0.8), (0, 0, 0.4))
box("Ground floor", (24, 12, 4.5), (0, 0, 0.8 + 2.25))
upper = box("Copper volume", (36, 20, 14), (0, 0, 5.3 + 7))
cut(upper, (9, 6, 2.2), (-8, -10, 15.5))          # long slot, front (3 m deep recesses, so they read)
cut(upper, (6, 6, 2.6), (9, -10, 9.5))            # front, lower right
cut(upper, (6, 7, 2.4), (18, 3, 13))              # right side
cut(upper, (6, 5, 3.0), (-18, -4, 9))             # left side
cut(upper, (10, 6, 2.0), (6, 10, 16))             # back
for o in sc.objects:
    o.select_set(False)

cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.type, cam.data.ortho_scale = "ORTHO", 50
sc.render.resolution_x, sc.render.resolution_y = 1600, 900
# The clay pass flattens straight-on faces to one grey: shadows (light from high on the left) and cavity make the
# window recesses and the space under the cantilever read (the plugin keeps the scene's own shadow / cavity settings)
sh = sc.display.shading
sh.show_shadows, sh.shadow_intensity = True, 0.6
sh.show_cavity, sh.cavity_type = True, "BOTH"
sh.cavity_ridge_factor, sh.cavity_valley_factor = 1.0, 1.0
world = bpy.data.worlds.new("White")
world.color = (1, 1, 1)
sc.world = world
# The same camera and light for every view: the BUILDING turns (0 / 90 / 180 / 270°), so each side is seen and lit
# exactly like the front — the 4 views must look alike for the AI restyle and Pixal3D Multi-View
pivot = bpy.data.objects.new("Pivot", None)
sc.collection.objects.link(pivot)
for o in list(sc.objects):
    if o.type == "MESH":
        o.parent = pivot
cam.location, cam.rotation_euler = (0, -80, 10.5), (math.radians(90), 0, 0)
sc.display.light_direction = (-0.45, -0.35, 0.82)
# turning the building by +90° brings its left side (-X) to face the camera (-Y), and so on
for name, turn in (("front", 0), ("left", 90), ("back", 180), ("right", 270)):
    pivot.rotation_euler = (0, 0, math.radians(turn))
    bpy.context.view_layer.update()
    p = render.structure_pass(sc, "CLAY", 1600)
    shutil.copy(p, OUT / f"{name}.png")
    print("VIEW", name, OUT / f"{name}.png", flush=True)
pivot.rotation_euler = (0, 0, 0)
if len(argv) > 1:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(argv[1]).resolve()))
print("BLOCKOUT DONE", flush=True)
