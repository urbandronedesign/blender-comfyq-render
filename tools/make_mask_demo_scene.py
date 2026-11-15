# SPDX-License-Identifier: GPL-3.0-or-later
"""A test scene for MASKING (inpaint): a small living room with objects worth replacing, and the Flux2 Klein inpaint
graph ready in its ComfyQ graph. Select an object, Run Graph: only that object's area is redrawn from the prompt.

Headless, sandbox profile with the extension installed (connects to ComfyQ for the workflow's parameters):
    $env:BLENDER_USER_RESOURCES = "$env:TEMP\\comfyq_bridge_smoke_blender"
    blender -b --online-mode --python tools/make_mask_demo_scene.py -- E:\\zaki\\mask_demo.blend
"""
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

OUT = Path(sys.argv[sys.argv.index("--") + 1])
PRESET = (Path(__file__).resolve().parents[1] / "presets" / "library" / "Image edit"
          / "Flux2 Klein 9B — inpaint (paint & prompt).json")
# The zone is the object's exact outline: ask for something of the same shape (a bouquet would spill outside it)
PROMPT = ("a glossy white porcelain vase hand-painted with blue Delft flowers, photorealistic, soft daylight, same "
          "perspective and lighting as the room")

# ---------------------------------------------------------------- the room
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)
sc = bpy.context.scene
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
sc.frame_start = sc.frame_end = 1


def mat(name, rgb, rough=0.6):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.diffuse_color = (*rgb, 1.0)            # what the Material style (Workbench) shows
    m.roughness = rough
    return m


def box(name, size, loc, m, rot_z=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=(0, 0, rot_z))
    o = bpy.context.object
    o.name, o.scale = name, size
    bpy.ops.object.transform_apply(scale=True)
    o.data.materials.append(m)
    return o


def cyl(name, r, h, loc, m, verts=48, r2=None):
    if r2 is None:
        bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=h, location=loc)
    else:
        bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r, radius2=r2, depth=h, location=loc)
    o = bpy.context.object
    o.name = name
    bpy.ops.object.shade_smooth()
    o.data.materials.append(m)
    return o


def join(name, parts):
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    o = bpy.context.object
    o.name = name
    return o


wall, floor, oak, walnut = mat("Wall", (0.86, 0.83, 0.78)), mat("Floor", (0.55, 0.40, 0.26)), \
    mat("Oak", (0.62, 0.45, 0.28)), mat("Walnut", (0.32, 0.20, 0.12))
ceramic, sofa_m, shade = mat("Blue ceramic", (0.12, 0.30, 0.62), 0.2), mat("Green fabric", (0.16, 0.32, 0.22), 0.9), \
    mat("Lamp shade", (0.93, 0.88, 0.76), 0.8)
brass, black, canvas = mat("Brass", (0.75, 0.58, 0.25), 0.3), mat("Black", (0.04, 0.04, 0.04)), \
    mat("Painting", (0.85, 0.42, 0.20))
glass = mat("Window", (0.75, 0.88, 1.0), 0.05)

box("Floor", (8, 8, 0.1), (0, 0, -0.05), floor)
box("Back wall", (8, 0.1, 3.0), (0, 3.0, 1.5), wall)
box("Left wall", (0.1, 8, 3.0), (-3.2, 0, 1.5), wall)
box("Window", (1.6, 0.05, 1.3), (1.6, 2.94, 1.6), glass)
# table + legs
top = box("Table top", (1.6, 0.9, 0.05), (0, 0.6, 0.75), oak)
legs = [box(f"leg{i}", (0.06, 0.06, 0.72), (x, y, 0.36), oak)
        for i, (x, y) in enumerate(((-0.72, 0.22), (0.72, 0.22), (-0.72, 0.98), (0.72, 0.98)))]
join("Table", [top, *legs])
# the vase (the default thing to replace), a bowl
vase = join("Vase", [cyl("vbody", 0.17, 0.46, (-0.25, 0.55, 1.01), ceramic),
                     cyl("vshoulder", 0.17, 0.10, (-0.25, 0.55, 1.29), ceramic, r2=0.08),
                     cyl("vneck", 0.08, 0.12, (-0.25, 0.55, 1.40), ceramic)])
cyl("Bowl", 0.16, 0.07, (0.35, 0.70, 0.81), ceramic, r2=0.10)
# floor lamp
join("Floor lamp", [cyl("lbase", 0.18, 0.03, (-1.9, 1.9, 0.015), brass),
                    cyl("lpole", 0.02, 1.5, (-1.9, 1.9, 0.78), brass),
                    cyl("lshade", 0.28, 0.35, (-1.9, 1.9, 1.62), shade, r2=0.18)])
# armchair
join("Armchair", [box("aseat", (0.8, 0.8, 0.4), (1.7, 0.4, 0.25), sofa_m),
                  box("aback", (0.8, 0.18, 0.55), (1.7, 0.75, 0.72), sofa_m),
                  box("aarmL", (0.14, 0.8, 0.3), (1.37, 0.4, 0.55), sofa_m),
                  box("aarmR", (0.14, 0.8, 0.3), (2.03, 0.4, 0.55), sofa_m)], )
bpy.context.object.rotation_euler.z = math.radians(-25)
# painting on the back wall
join("Painting", [box("pframe", (1.0, 0.04, 0.7), (-1.2, 2.93, 1.75), black),
                  box("pcanvas", (0.9, 0.02, 0.6), (-1.2, 2.90, 1.75), canvas)])

cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.lens = 30
cam.location = (0.3, -3.6, 1.55)
cam.rotation_euler = (Vector((-0.1, 1.2, 0.95)) - cam.location).to_track_quat("-Z", "Y").to_euler()
sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sc.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
sc.display.shading.show_shadows = True
sc.display.shading.show_cavity = True

# ---------------------------------------------------------------- the ComfyQ graph (Flux2 Klein inpaint)
from bl_ext.user_default.comfyq_bridge.graph import preset_ops  # noqa: E402
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION  # noqa: E402


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.05)
    return False


SESSION.connect("", "mask-demo")
if not pump_until(lambda: SESSION.connected and SESSION.workflows, 30):
    sys.exit(f"Cannot connect to ComfyQ: {SESSION.error or SESSION.status}")
data = json.loads(PRESET.read_text(encoding="utf-8"))
for d in data["nodes"]:
    if d.get("workflow_id"):
        SESSION.specs[d["workflow_id"]] = SESSION.client.params(d["workflow_id"])
tree = bpy.data.node_groups.new("ComfyQ Graph", "ComfyQGraphTree")
tree.use_fake_user = True
preset_ops.build_saved(data, tree, sc)
for n in tree.nodes:
    if n.bl_idname == "ComfyQMaskNode":
        n.style = "MATERIAL"                  # the AI sees colours and materials, not grey clay
        n.label = "Mask (selected objects)"
    elif n.bl_idname == "ComfyQPromptNode":
        n.text = PROMPT
    elif n.bl_idname == "ComfyQSaveNode":
        n.auto = True
left = min(n.location.x for n in tree.nodes)
note = preset_ops.steps_note(tree, (
    "1. Select the object to replace (the Vase",
    "   is selected; try the Armchair, the",
    "   Floor lamp or the Painting too)",
    "2. Prompt: what should be there instead",
    "3. Run Graph (Solo loads Flux2 Klein",
    "   inpaint, about a minute the first time)",
    "4. Viewer: only the selected area changed",
    "5. Mask node › Render Frame shows the",
    "   zone before sending (transparent =",
    "   repainted)"), "How to use · masking test")
note.location = (left - note.width - 60.0, 0.0)
bpy.ops.object.select_all(action="DESELECT")
vase = bpy.data.objects["Vase"]
vase.select_set(True)
bpy.context.view_layer.objects.active = vase
SESSION.close()
OUT.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT))
print("SAVED", OUT, "graph nodes:", len(tree.nodes), "links:", len(tree.links),
      "invalid:", sum(not lk.is_valid for lk in tree.links), flush=True)
