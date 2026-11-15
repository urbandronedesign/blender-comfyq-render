# SPDX-License-Identifier: GPL-3.0-or-later
"""Class presets for Gaussian splats (Blender 5.3 renders them natively; tested on the GPU on 2026-11-15):

  Picture → Gaussian splat (IDOL)                    Image → IDOL → Import to Scene (beside, on the ground)
  Text → Gaussian splat (FLUX.3 Klein + IDOL)        Prompt → FLUX.3 Klein distilled (seed 7) → IDOL → Import
                                                                                   ↘ Viewer (the picture)
IDOL beat TripoSplat on the same picture (an olive tree in a pot): its canopy keeps the picture's shape;
TripoSplat squeezed it into a ball (live_check/splat53/compare/splat_sheet.png).

Runs headless inside Blender with the extension installed (sandbox profile):
    blender -b --online-mode --python tools/make_splat_presets.py -- <out folder>
"""
import json
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.graph import preset_ops
from bl_ext.user_default.comfyq_bridge.graph.tree import TREE, WORKFLOW_NODE, socket_kind
from bl_ext.user_default.comfyq_bridge.ops.workflow import request_specs
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(argv[0] if argv else "presets_splat")
IDOL, KLEIN = "3d_idol_image_to_gaussian_splat", "image_flux3_klein_distilled_t2i"
FILE_SAFE = str.maketrans({c: "-" for c in '<>:"/\\|?*→'})
PROMPT = ("Studio product photo of a mature olive tree in a terracotta pot, the whole tree and pot visible with "
          "space around it, silver-green leaves, twisted trunk, plain pure white background, soft even studio "
          "light, sharp focus, no text")


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


def importer(tree, x):
    n = tree.nodes.new("ComfyQImportNode")
    n.label, n.location = "Import to Scene", (x, 0.0)
    n.auto, n.replace, n.place, n.on_ground, n.size = True, True, "BESIDE", True, 0.0
    return n


def fixed_seed(node, wf_id, value=7):
    it = node.item(next(s.key for s in SESSION.specs[wf_id] if s.kind == "seed"))
    it.i, it.randomize = value, False


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
for wf in (IDOL, KLEIN):
    request_specs(wf)
if not pump_until(lambda: all(w in SESSION.specs for w in (IDOL, KLEIN)), 60):
    sys.exit("Parameters did not load")
OUT.mkdir(parents=True, exist_ok=True)
NOTE_53 = ("Needs Blender 5.3 or later (native splats;",
           "   5.2 keeps the .spz in the job folder)")

# 1. Picture → splat
t = bpy.data.node_groups.new("Picture to splat", TREE)
img = t.nodes.new("ComfyQImageNode")
img.label, img.location = "Picture", (-900.0, 0.0)
idol = wf_node(t, IDOL, "IDOL", -420.0)
fixed_seed(idol, IDOL)
imp = importer(t, 80.0)
link_kind(t, img.outputs[0], idol, "image")
t.links.new(idol.outputs[0], imp.inputs[0])
save(t, "Picture → Gaussian splat (IDOL)",
     "One picture of an object (a plant, a piece of furniture, a sculpture) becomes a 3D Gaussian splat, imported "
     "beside the scene, on the ground, and rendered by Blender itself in Workbench, EEVEE and Cycles (Blender 5.3 or "
     "later). Best with the whole object on a plain background. Splats suit leafy and soft things that meshes do "
     "badly. About 1.5 minutes on the 5090 (the model loads first).",
     ("1. Picture: a photo or render of ONE object,",
      "   whole, on a plain background",
      "2. Run Graph (about 1.5 minutes)",
      "3. The splat is imported beside the scene,",
      "   about 1 m tall: set Size on Import to Scene",
      "   (a tree: 3 to 5 m)",
      "4. " + NOTE_53[0], NOTE_53[1]), -900.0)

# 2. Text → picture → splat
t = bpy.data.node_groups.new("Text to splat", TREE)
pr = t.nodes.new("ComfyQPromptNode")
pr.label, pr.location, pr.text = "Prompt", (-1400.0, 0.0), PROMPT
kl = wf_node(t, KLEIN, "FLUX.3 Klein", -920.0)
fixed_seed(kl, KLEIN)
idol = wf_node(t, IDOL, "IDOL", -420.0)
fixed_seed(idol, IDOL)
imp = importer(t, 80.0)
view = t.nodes.new("ComfyQViewerNode")
view.label, view.location, view.auto = "Viewer", (-420.0, 420.0), True
t.links.new(pr.outputs[0], kl.inputs["Prompt"] if "Prompt" in kl.inputs else
             next(s for s in kl.inputs if socket_kind(s) == "text"))
link_kind(t, kl.outputs[0], idol, "image")
t.links.new(kl.outputs[0], view.inputs[0])
t.links.new(idol.outputs[0], imp.inputs[0])
save(t, "Text → Gaussian splat (FLUX.3 Klein + IDOL)",
     "Describe one object: FLUX.3 Klein makes a clean picture of it (whole object, white background), IDOL turns the "
     "picture into a 3D Gaussian splat, imported beside the scene, on the ground. Blender 5.3 renders it natively in "
     "every engine. Tested on 2026-11-15 with an olive tree in a pot: picture 38 s, splat 98 s, upright and facing "
     "the camera like the picture. Viewer shows the picture.",
     ("1. Describe ONE object in Prompt (whole,",
      "   plain white background)",
      "2. Run Graph: a picture, then the splat",
      "3. Check the picture in Viewer",
      "4. The splat is imported beside the scene:",
      "   set Size on Import to Scene",
      "5. " + NOTE_53[0], NOTE_53[1]), -1400.0)

SESSION.close()
print("DONE", flush=True)
