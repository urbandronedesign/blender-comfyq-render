# SPDX-License-Identifier: GPL-3.0-or-later
"""Class presets for the workflows with ComfyQ's new input types (tested on the GPU on 2026-10-21):

  Remesh: scene objects → clean low-poly          Scene Objects → Remesh & decimate → Import (beside, on the ground)
  UV unwrap: scene objects → UVs + AO             Scene Objects → UV unwrap → Import
  Clean up a model: remesh + UV unwrap            Scene Objects → Remesh → UV unwrap → Import
  Reshoot from another angle (Qwen 2511)          Scene Render (clay) → Qwen 2511 (Around 90, seed 7) → Viewer + Save

Runs headless inside Blender with the extension installed (sandbox profile):
    blender -b --online-mode --python tools/make_new_inputs_presets.py -- <out folder>
"""
import json
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.graph import preset_ops
from bl_ext.user_default.comfyq_bridge.graph.tree import TREE, WORKFLOW_NODE, socket_kind
from bl_ext.user_default.comfyq_bridge.ops.workflow import request_specs, set_camera
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(argv[0] if argv else "presets_new_inputs")
REMESH, UV, QWEN = "3d_mesh_remesh_decimate", "3d_mesh_uv_unwrap", "image_edit_qwen_2511_multiple_angles"
FILE_SAFE = str.maketrans({c: "-" for c in '<>:"/\\|?*→'})


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.05)
    return False


def wf_node(tree, wf_id, label, x):
    n = tree.nodes.new(WORKFLOW_NODE)
    n.workflow_id = wf_id
    n.build(SESSION.specs[wf_id])
    n.label, n.location = label, (x, 0.0)
    return n


def link_kind(tree, src_socket, node, kind):
    tree.links.new(src_socket, next(s for s in node.inputs if socket_kind(s) == kind))


def objects(tree, x):
    n = tree.nodes.new("ComfyQSceneObjectsNode")
    n.label, n.location = "Scene Objects", (x, 0.0)
    return n


def importer(tree, x):
    n = tree.nodes.new("ComfyQImportNode")
    n.label, n.location = "Import to Scene", (x, 0.0)
    n.auto, n.replace, n.place, n.on_ground, n.size = True, True, "BESIDE", True, 0.0
    return n


def save(tree, label, description, steps, left_x):
    note = preset_ops.steps_note(tree, steps, f"How to use · {label}")
    note.location = (left_x - note.width - 60.0, 0.0)
    bad = sum(not lk.is_valid for lk in tree.links)
    data = preset_ops.serialize(tree, label, description)
    path = OUT / f"{label.translate(FILE_SAFE)}.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"PRESET {path.name}: {len(tree.nodes)} nodes, {len(tree.links)} links ({bad} invalid)", flush=True)
    bpy.data.node_groups.remove(tree)


SESSION.connect("", "preset-builder")
if not pump_until(lambda: SESSION.connected and SESSION.workflows, 30):
    sys.exit(f"Cannot connect to ComfyQ: {SESSION.error or SESSION.status}")
for wf in (REMESH, UV, QWEN):
    request_specs(wf)
if not pump_until(lambda: all(w in SESSION.specs for w in (REMESH, UV, QWEN)), 60):
    sys.exit("Parameters did not load")
OUT.mkdir(parents=True, exist_ok=True)
LARGE = ("   Empty result (Faces: 0)? The model is too",
         "   large: scale it down, or turn off",
         "   Watertight remesh")

# 1. Remesh
t = bpy.data.node_groups.new("Remesh", TREE)
src, rm, imp = objects(t, -900.0), wf_node(t, REMESH, "Remesh", -420.0), importer(t, 80.0)
link_kind(t, src.outputs[0], rm, "model")
t.links.new(rm.outputs[0], imp.inputs[0])
save(t, "Remesh: scene objects → clean low-poly",
     "The selected objects (or a collection) sent as one model and rebuilt as a clean low-poly mesh: Target "
     "triangle count, Watertight remesh (one closed shell), Smoothing passes, Sharp-edge angle. The result is "
     "imported beside the scene, on the ground, at the size it was made. A model in millimetres (very large) can "
     "come back empty: scale it down or turn off Watertight remesh.",
     ("1. Select the objects to rebuild (or pick",
      "   a Collection on Scene Objects)",
      "2. Remesh: set the Target triangle count",
      "3. Run Graph",
      "4. The low-poly model is imported beside",
      "   the scene, next to the original",
      *LARGE), -900.0)

# 2. UV unwrap
t = bpy.data.node_groups.new("UV unwrap", TREE)
src, uv, imp = objects(t, -900.0), wf_node(t, UV, "UV unwrap", -420.0), importer(t, 80.0)
link_kind(t, src.outputs[0], uv, "model")
t.links.new(uv.outputs[0], imp.inputs[0])
save(t, "UV unwrap: scene objects → UVs + AO",
     "The selected objects (or a collection) sent as one model and given UVs ready for texturing, with an ambient "
     "occlusion bake. The model comes back imported beside the scene; the UV layout and AO pictures are in the "
     "job's results folder. Cost follows the mesh density (14 s for 3,500 triangles, about a minute for 700,000).",
     ("1. Select the objects to unwrap (or pick",
      "   a Collection on Scene Objects)",
      "2. Run Graph",
      "3. The model with UVs is imported beside",
      "   the scene; the UV layout and AO pictures",
      "   are in its results folder"), -900.0)

# 3. Chain
t = bpy.data.node_groups.new("Clean up", TREE)
src, rm, uv, imp = (objects(t, -1400.0), wf_node(t, REMESH, "Remesh", -920.0), wf_node(t, UV, "UV unwrap", -420.0),
                    importer(t, 80.0))
link_kind(t, src.outputs[0], rm, "model")
link_kind(t, rm.outputs[0], uv, "model")
t.links.new(uv.outputs[0], imp.inputs[0])
save(t, "Clean up a model: remesh + UV unwrap",
     "A heavy model (a scan, an AI-made mesh) made ready for texturing: rebuilt as a clean low-poly mesh, then given "
     "UVs and an ambient occlusion bake. Tested on 2026-10-21: 15,744 → 2,000 triangles with UVs in under 30 s. The "
     "result is imported beside the scene.",
     ("1. Select the model to clean up",
      "2. Remesh: set the Target triangle count",
      "3. Run Graph: remesh, then UV unwrap",
      "4. The clean model with UVs is imported",
      "   beside the scene",
      *LARGE), -1400.0)

# 4. Qwen 2511 reshoot
t = bpy.data.node_groups.new("Reshoot", TREE)
rnd = t.nodes.new("ComfyQSceneRenderNode")
rnd.label, rnd.location, rnd.style, rnd.frame = "Scene Render", (-900.0, 0.0), "CLAY", "CURRENT"
q = wf_node(t, QWEN, "Reshoot", -420.0)
specs = SESSION.specs[QWEN]
cam = next(s for s in specs if s.kind == "camera")
set_camera(q.item(cam.key), {"horizontal": 90, "vertical": 0, "zoom": 5}, cam)
seed = q.item(next(s.key for s in specs if s.kind == "seed"))
seed.i, seed.randomize = 7, False                 # one seed: views of one subject that agree
link_kind(t, rnd.outputs[0], q, "image")
view = t.nodes.new("ComfyQViewerNode")
view.label, view.location, view.auto = "Viewer", (80.0, 160.0), True
sv = t.nodes.new("ComfyQSaveNode")
sv.label, sv.location = "Save", (80.0, -160.0)
t.links.new(q.outputs[0], view.inputs[0])
t.links.new(q.outputs[0], sv.inputs[0])
save(t, "Reshoot from another angle (Qwen 2511)",
     "The scene camera's view of the subject is the front; Qwen Image-Edit 2511 redraws the same subject from "
     "where the camera block says: Around (0 front, 90 right side, 180 back), Height, Zoom, or From 3D View "
     "(orbit the 3D Viewport around the selected subject, then press it). One fixed seed, so several angles of one "
     "subject agree. The hidden sides are invented by the model. Tested on 2026-10-21: 90 → profile, 180 → back, "
     "12 s per view once loaded.",
     ("1. Frame the subject with the scene camera",
      "   (that view is the front)",
      "2. Reshoot: set Around / Height / Zoom, or",
      "   orbit the 3D Viewport and press",
      "   From 3D View",
      "3. Run Graph; Viewer shows the new view",
      "4. Same seed for every angle: the views",
      "   of one subject agree"), -900.0)

SESSION.close()
print("DONE", flush=True)
