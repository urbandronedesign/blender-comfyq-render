# SPDX-License-Identifier: GPL-3.0-or-later
"""A test scene for the 3D-model presets (Remesh, UV unwrap, Clean up, Reshoot): a raw AI-generated mesh (the use
these workflows are made for: gotcha 52) standing on a floor, about 1.8 m tall, selected, a camera at its front three
quarters, a sun. Headless:
    blender -b --factory-startup --python tools/make_mesh_test_scene.py -- <model.glb> <out.blend>
"""
import math
import sys

import bpy
from mathutils import Vector

GLB, OUT = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
ob = bpy.context.view_layer.objects.active
for o in [o for o in bpy.context.scene.objects if o.type == "EMPTY"]:     # glTF root empties
    ob.matrix_world = ob.matrix_world.copy()
    if ob.parent is o:
        mw = ob.matrix_world.copy()
        ob.parent = None
        ob.matrix_world = mw
    bpy.data.objects.remove(o)
ob.name = "AI character mesh"
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
height = max(p.z for p in pts) - min(p.z for p in pts)
ob.scale = (1.8 / height,) * 3
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
ob.location -= Vector(((min(p.x for p in pts) + max(p.x for p in pts)) / 2,
                       (min(p.y for p in pts) + max(p.y for p in pts)) / 2, min(p.z for p in pts)))
bpy.ops.mesh.primitive_plane_add(size=10)
bpy.context.active_object.name = "Floor"
cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
bpy.context.scene.collection.objects.link(cam)
cam.location = (2.6, -3.6, 1.45)
cam.rotation_euler = (Vector((0, 0, 0.95)) - cam.location).to_track_quat("-Z", "Y").to_euler()
cam.data.lens = 50
sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
bpy.context.scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(45), 0, math.radians(35))
sc = bpy.context.scene
sc.camera = cam
sc.render.resolution_x, sc.render.resolution_y = 1024, 1024
bpy.ops.object.select_all(action="DESELECT")
ob.select_set(True)
bpy.context.view_layer.objects.active = ob
print("MESH", ob.name, len(ob.data.polygons), "faces", flush=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT)
print("SAVED", OUT, flush=True)
