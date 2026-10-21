# SPDX-License-Identifier: GPL-3.0-or-later
"""Blockout of a modern house plugged on top of a cliff (terrace + pool), for the clay animation → photoreal video
test. A rocky cliff with a plateau; on its edge a board-formed concrete core running down the face, a timber volume
cantilevered over the void on steel struts, a white volume and a dark standing-seam tower; a deck terrace with a pool
and glass railing; pines. A 5 s low-angle camera move looking up at the house (1280×720, 24 fps, frames 1–120).
Viewport colours per material so the Material style reads too. Headless:

    blender -b --factory-startup --python tools/make_cliff_house_scene.py -- E:\\zaki\\cliff_house.blend [preview dir]
"""
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector, noise

args = sys.argv[sys.argv.index("--") + 1:]
OUT = args[0]
PREVIEW = Path(args[1]) if len(args) > 1 else None
random.seed(4)

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
sc = bpy.context.scene


def material(name, rgb, rough=0.6, metal=0.0, alpha=1.0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*rgb, alpha)
    m.roughness, m.metallic = rough, metal
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*rgb, 1)
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = metal
        if alpha < 1:
            bsdf.inputs["Alpha"].default_value = alpha
    return m


ROCK = material("Rock", (0.32, 0.30, 0.28), 0.9)
CONCRETE = material("Board-formed concrete", (0.55, 0.55, 0.53), 0.85)
TIMBER = material("Timber cladding", (0.62, 0.45, 0.28), 0.6)
WHITE = material("White panels", (0.85, 0.85, 0.83), 0.5)
DARK = material("Dark zinc", (0.12, 0.13, 0.14), 0.4, 0.6)
GLASS = material("Glass", (0.10, 0.14, 0.16), 0.05, 0.0, 0.35)
STEEL = material("Steel", (0.20, 0.20, 0.21), 0.3, 1.0)
WATER = material("Pool water", (0.10, 0.45, 0.55), 0.05)
DECK = material("Deck", (0.50, 0.38, 0.26), 0.7)
PINE = material("Pine", (0.10, 0.20, 0.12), 0.9)
BARK = material("Bark", (0.20, 0.14, 0.10), 0.9)


def box(name, center, size, mat, parent_coll=None):
    bpy.ops.mesh.primitive_cube_add(location=center)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    ob.data.materials.append(mat)
    return ob


def cut(target, center, size):
    """A recessed opening (window / loggia) carved into `target`."""
    bpy.ops.mesh.primitive_cube_add(location=center)
    c = bpy.context.active_object
    c.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    c.display_type = "WIRE"
    c.hide_render = True
    c.hide_viewport = True
    mod = target.modifiers.new("Opening", "BOOLEAN")
    mod.operation, mod.object = "DIFFERENCE", c
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(c)


# -- cliff: plateau (z ≈ 18) at y > 0 falling steeply towards the camera (-y), rocky noise ---------------------------
TOP = 18.0
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=180, y_segments=180, size=60)
for v in bm.verts:
    x, y = v.co.x, v.co.y
    t = min(1.0, max(0.0, (y + 9.0) / 9.0))                 # 0 at the foot (y = -9), 1 on the plateau edge (y = 0)
    profile = TOP * (t ** 0.6) if y < 0 else TOP + 0.6 * math.sin(x * 0.08) - 0.03 * y
    profile -= max(0.0, -y - 9.0) * 0.35                    # the valley floor below keeps falling a little
    rough = noise.fractal(Vector((x * 0.18, y * 0.18, profile * 0.18)), 0.6, 2.2, 5) * (1.1 if -9 < y < 1 else 0.5)
    v.co.z = profile + rough
mesh = bpy.data.meshes.new("Cliff")
bm.to_mesh(mesh)
bm.free()
cliff = bpy.data.objects.new("Cliff", mesh)
sc.collection.objects.link(cliff)
cliff.data.materials.append(ROCK)
for p in cliff.data.polygons:
    p.use_smooth = True

# -- the house on the edge: hung over the face (y < 0), its core and piers running down the rock -----------------------
core = box("Concrete core", (0.0, -4.0, 15.0), (5.0, 3.0, 12.0), CONCRETE)
pier = box("Concrete pier", (5.2, -5.6, 13.5), (2.2, 2.2, 13.0), CONCRETE)
timber = box("Timber volume", (3.0, -7.0, 23.5), (11.0, 7.0, 4.2), TIMBER)          # cantilevered over the void
cut(timber, (6.0, -10.6, 23.6), (4.5, 1.6, 2.8))                                    # deep loggia facing the valley
box("Glass loggia", (6.0, -9.9, 23.6), (4.3, 0.06, 2.6), GLASS)
white = box("White volume", (-5.0, -4.5, 21.2), (7.0, 6.0, 3.8), WHITE)
cut(white, (-7.0, -7.6, 21.4), (2.6, 1.4, 2.6))
cut(white, (-3.2, -7.6, 21.2), (1.2, 1.4, 2.2))
tower = box("Dark tower", (-1.2, -1.5, 25.0), (3.6, 4.0, 9.0), DARK)
cut(tower, (-1.2, -3.6, 26.0), (1.4, 1.2, 3.0))
for i, (top, bottom) in enumerate((((0.5, -10.0, 21.4), (1.5, -6.2, 9.5)), ((6.5, -10.0, 21.4), (5.5, -6.6, 8.5)))):
    a, b = Vector(top), Vector(bottom)
    d = b - a
    bpy.ops.mesh.primitive_cylinder_add(radius=0.16, depth=d.length, location=(a + b) / 2)
    st = bpy.context.active_object
    st.name = f"Steel strut {i + 1}"
    st.rotation_mode = "QUATERNION"
    st.rotation_quaternion = d.to_track_quat("Z", "Y")
    st.data.materials.append(STEEL)
for x in (-1.5, 7.5):                                                               # tension rods down to the rock
    box(f"Steel rod {x:+.1f}", (x, -10.3, 11.0), (0.08, 0.08, 20.8), STEEL)

# terrace on the plateau edge, its deck cantilevered; a glass-sided pool on its front edge (the water reads from below)
box("Terrace base", (13.0, 0.5, TOP + 0.8), (12.0, 5.0, 2.0), CONCRETE)
deck = box("Terrace deck", (13.0, -3.0, TOP + 1.9), (13.0, 8.0, 0.5), DECK)
box("Pool water", (14.0, -5.6, TOP + 2.75), (7.8, 2.8, 1.2), WATER)
box("Pool glass", (14.0, -5.6, TOP + 2.8), (8.0, 3.0, 1.4), GLASS)
box("Terrace railing", (8.0, -6.95, TOP + 2.7), (3.0, 0.05, 1.1), GLASS)
box("Living pavilion", (12.0, 3.0, TOP + 3.6), (9.0, 6.0, 3.2), WHITE)

# pines on the plateau and the slope
for i in range(38):
    x, y = random.uniform(-28, 28), random.uniform(-6, 26)
    if -11 < x < 22 and -12 < y < 9:
        continue                                                                     # keep the house clear
    t = min(1.0, max(0.0, (y + 9.0) / 9.0))
    z = TOP * (t ** 0.6) if y < 0 else TOP + 0.6 * math.sin(x * 0.08) - 0.03 * y
    h = random.uniform(5, 10)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.18, depth=h * 0.3, location=(x, y, z + h * 0.15))
    bpy.context.active_object.data.materials.append(BARK)
    bpy.ops.mesh.primitive_cone_add(radius1=h * 0.22, depth=h * 0.85, location=(x, y, z + h * 0.3 + h * 0.42))
    bpy.context.active_object.data.materials.append(PINE)

# -- camera: low on the slope, looking up; a slow move to the right and up (5 s) -------------------------------------
target = bpy.data.objects.new("Camera target", None)
sc.collection.objects.link(target)
target.location = (4.0, -5.0, 18.5)
cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
sc.collection.objects.link(cam)
cam.data.lens = 24
sc.camera = cam
track = cam.constraints.new("TRACK_TO")
track.target, track.track_axis, track.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
sc.frame_start, sc.frame_end = 1, 120
sc.render.fps = 24
for frame, loc in ((1, (-15.0, -30.0, 5.0)), (120, (-3.0, -29.0, 8.0))):
    cam.location = loc
    cam.keyframe_insert("location", frame=frame)
for fc in cam.animation_data.action.fcurves if hasattr(cam.animation_data.action, "fcurves") else []:
    for kp in fc.keyframe_points:
        kp.interpolation = "BEZIER"

sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sc.collection.objects.link(sun)
sun.rotation_euler = (math.radians(55), 0, math.radians(-40))
sun.data.energy = 3.0
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
bpy.ops.object.select_all(action="DESELECT")
bpy.ops.wm.save_as_mainfile(filepath=OUT)
print("SCENE", OUT, flush=True)

if PREVIEW:                                                     # Workbench stills at the start, middle and end
    PREVIEW.mkdir(parents=True, exist_ok=True)
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "MATERIAL"
    sc.display.shading.show_shadows = True
    sc.display.shading.show_cavity = True
    for f in (1, 60, 120):
        sc.frame_set(f)
        sc.render.filepath = str(PREVIEW / f"cliff_house_f{f:03d}.png")
        bpy.ops.render.render(write_still=True)
    print("PREVIEW", PREVIEW, flush=True)
