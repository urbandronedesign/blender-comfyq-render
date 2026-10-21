# SPDX-License-Identifier: GPL-3.0-or-later
"""A small demo scene for the 3D-model / camera presets: Suzanne (subdivided, 15,744 faces once applied) on a floor,
a camera straight at its face, a sun. Headless:
    blender -b --factory-startup --python tools/make_demo_scene.py -- E:\\zaki\\new_inputs_demo.blend
"""
import math
import sys

import bpy

out = sys.argv[sys.argv.index("--") + 1]
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 1.0))
m = bpy.context.active_object
m.name = "Suzanne"
m.modifiers.new("Subdivision", "SUBSURF").levels = 2
bpy.ops.object.shade_smooth()
bpy.ops.mesh.primitive_plane_add(size=12, location=(0, 0, 0))
bpy.context.active_object.name = "Floor"
m.location.z = -min(v.co.z for v in m.data.vertices) + 0.02      # resting on the floor
cam_data = bpy.data.cameras.new("Camera")
cam = bpy.data.objects.new("Camera", cam_data)
bpy.context.scene.collection.objects.link(cam)
cam.location, cam.rotation_euler = (0, -5.0, 1.3), (math.radians(87), 0, 0)
bpy.context.scene.camera = cam
sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
bpy.context.scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
sc = bpy.context.scene
sc.render.resolution_x = sc.render.resolution_y = 1024
bpy.ops.object.select_all(action="DESELECT")
m.select_set(True)
bpy.context.view_layer.objects.active = m
bpy.ops.wm.save_as_mainfile(filepath=out)
print("SCENE", out, flush=True)
