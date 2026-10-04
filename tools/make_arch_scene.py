# SPDX-License-Identifier: GPL-3.0-or-later
"""A basic architecture test scene: a living room modelled with boxes (floor, walls with a window and a
door opening, ceiling, sofa, coffee table, rug, shelf, pendant lamp), simple materials, sun + sky, a
camera at eye height. The kind of "blocked-out" scene an AI render starts from.

    blender -b --factory-startup --python tools/make_arch_scene.py -- <out.blend>
"""
import math
import sys

import bmesh
import bpy
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1]
W, D, H = 6.0, 7.0, 3.0            # room: x -3..3, y 0..7 (back wall at y = 7), z 0..3
T = 0.2                            # wall thickness

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.name = "Room"
MATS = {}


def mat(name, color, rough=0.6, metal=0.0, alpha=1.0, transmission=0.0):
    if name in MATS:
        return MATS[name]
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1.0)                     # Workbench "material colors" too
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if transmission:
        bsdf.inputs["Transmission Weight"].default_value = transmission
        bsdf.inputs["IOR"].default_value = 1.45
    MATS[name] = m
    return m


def box(name, lo, hi, material, parent=None):
    lo, hi = Vector(lo), Vector(hi)
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * (hi.x - lo.x), v.co.y * (hi.y - lo.y), v.co.z * (hi.z - lo.z)))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    ob.location = (lo + hi) / 2
    me.materials.append(material)
    coll = bpy.data.collections.get(parent) if parent else sc.collection
    coll.objects.link(ob)
    return ob


def collection(name):
    c = bpy.data.collections.new(name)
    sc.collection.children.link(c)
    return name


wall = mat("Wall plaster", (0.82, 0.80, 0.76), 0.9)
floor = mat("Floor", (0.45, 0.33, 0.22), 0.5)
ceil = mat("Ceiling", (0.9, 0.9, 0.9), 0.9)
glass = mat("Glass", (0.9, 0.95, 1.0), 0.02, transmission=1.0)
frame = mat("Window frame", (0.12, 0.12, 0.12), 0.4, 0.8)
door = mat("Door", (0.55, 0.42, 0.30), 0.5)
fabric = mat("Sofa fabric", (0.35, 0.38, 0.42), 0.95)
wood = mat("Table wood", (0.25, 0.17, 0.10), 0.4)
rug = mat("Rug", (0.70, 0.62, 0.50), 1.0)
metal = mat("Lamp metal", (0.05, 0.05, 0.05), 0.3, 1.0)
ground = mat("Outside ground", (0.30, 0.38, 0.22), 1.0)

arch = collection("Architecture")
box("Floor", (-W / 2, 0, -0.1), (W / 2, D, 0), floor, arch)
box("Ceiling", (-W / 2, 0, H), (W / 2, D, H + 0.1), ceil, arch)
# back wall with a large window (x -1.4..1.4, z 0.6..2.5)
wx0, wx1, wz0, wz1 = -1.4, 1.4, 0.6, 2.5
box("Wall back left", (-W / 2 - T, D, 0), (wx0, D + T, H), wall, arch)
box("Wall back right", (wx1, D, 0), (W / 2 + T, D + T, H), wall, arch)
box("Wall back sill", (wx0, D, 0), (wx1, D + T, wz0), wall, arch)
box("Wall back lintel", (wx0, D, wz1), (wx1, D + T, H), wall, arch)
win = collection("Window")
box("Window glass", (wx0, D + 0.09, wz0), (wx1, D + 0.11, wz1), glass, win)
for name, lo, hi in (("Window frame bottom", (wx0, D + 0.05, wz0), (wx1, D + 0.15, wz0 + 0.06)),
                     ("Window frame top", (wx0, D + 0.05, wz1 - 0.06), (wx1, D + 0.15, wz1)),
                     ("Window frame left", (wx0, D + 0.05, wz0), (wx0 + 0.06, D + 0.15, wz1)),
                     ("Window frame right", (wx1 - 0.06, D + 0.05, wz0), (wx1, D + 0.15, wz1)),
                     ("Window frame middle", (-0.03, D + 0.05, wz0), (0.03, D + 0.15, wz1))):
    box(name, lo, hi, frame, win)
# left wall with a door opening (y 4.6..5.5, z 0..2.1)
dy0, dy1, dz1 = 4.6, 5.5, 2.1
box("Wall left front", (-W / 2 - T, 0, 0), (-W / 2, dy0, H), wall, arch)
box("Wall left back", (-W / 2 - T, dy1, 0), (-W / 2, D, H), wall, arch)
box("Wall left over door", (-W / 2 - T, dy0, dz1), (-W / 2, dy1, H), wall, arch)
box("Door", (-W / 2 - 0.12, dy0 + 0.02, 0), (-W / 2 - 0.08, dy1 - 0.02, dz1 - 0.02), door, arch)
box("Wall right", (W / 2, 0, 0), (W / 2 + T, D, H), wall, arch)

furn = collection("Furniture")
box("Rug", (-1.6, 2.6, 0), (1.6, 5.2, 0.015), rug, furn)
box("Sofa base", (-1.3, 5.3, 0), (1.3, 6.2, 0.42), fabric, furn)
box("Sofa back", (-1.3, 6.0, 0.42), (1.3, 6.25, 0.85), fabric, furn)
box("Sofa arm left", (-1.5, 5.3, 0), (-1.3, 6.25, 0.62), fabric, furn)
box("Sofa arm right", (1.3, 5.3, 0), (1.5, 6.25, 0.62), fabric, furn)
box("Coffee table", (-0.6, 3.6, 0.35), (0.6, 4.4, 0.40), wood, furn)
for i, (x, y) in enumerate(((-0.55, 3.65), (0.5, 3.65), (-0.55, 4.3), (0.5, 4.3))):
    box(f"Table leg {i + 1}", (x, y, 0), (x + 0.05, y + 0.05, 0.35), wood, furn)
box("Shelf", (W / 2 - 0.4, 1.8, 0), (W / 2, 3.6, 2.0), wood, furn)
bpy.ops.mesh.primitive_cylinder_add(radius=0.25, depth=0.3, location=(0, 4.0, 2.2))
lamp = bpy.context.active_object
lamp.name = "Pendant lamp"
lamp.data.materials.append(metal)
bpy.data.collections[furn].objects.link(lamp)
sc.collection.objects.unlink(lamp)
box("Lamp cable", (-0.005, 3.995, 2.35), (0.005, 4.005, H), metal, furn)
box("Outside ground", (-30, D + T, -0.3), (30, 60, -0.29), ground)

# light: sun through the window, sky, a soft fill inside
sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sun.data.energy = 4.0
sun.data.angle = math.radians(2)
sun.rotation_euler = (math.radians(55), 0, math.radians(160))
sc.collection.objects.link(sun)
fill = bpy.data.objects.new("Fill", bpy.data.lights.new("Fill", "AREA"))
fill.data.energy = 150
fill.data.size = 3
fill.location = (0, 2.0, 2.9)
sc.collection.objects.link(fill)
world = bpy.data.worlds.new("Sky")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.55, 0.7, 0.95, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.5
world.color = (0.55, 0.7, 0.95)
sc.world = world

cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
cam.data.lens = 20
cam.location = (-2.2, 0.6, 1.5)
direction = Vector((0.9, D - 0.5, 1.25)) - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
sc.collection.objects.link(cam)
sc.camera = cam
sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
sc.render.engine = "CYCLES"
sc.cycles.samples = 64
sc.frame_start = sc.frame_end = 1
bpy.ops.wm.save_as_mainfile(filepath=OUT)
print("SAVED", OUT)
