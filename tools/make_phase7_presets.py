# SPDX-License-Identifier: GPL-3.0-or-later
"""Class presets of audit 2, phase 7 (2026-11-16):

  * Find and replace an object (SAM3 + Flux2 Klein inpaint): the Mask Zone node (its panel has Find by Name, the
    Mask Tool, Grow / Feather) → Flux2 Klein inpaint → Viewer + Save;
  * Sketch → video (FLUX.3 Klein + LTX 2.5): the sketch over the clay scene → FLUX.3 Klein distilled edit (a photoreal
    start frame) → LTX 2.5 image to video (duration / size / fps from the scene) → Viewer + Save, with a "Start frame"
    Viewer.

    blender -b --online-mode --python tools/make_phase7_presets.py -- <class presets folder>

Seed 7 (not random), How to use notes (lines under ~45 characters, gotcha 43). Runs headless inside Blender with the
extension installed (sandbox profile; connects to ComfyQ for the workflows' parameters).
"""
import json
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.graph import preset_ops
from bl_ext.user_default.comfyq_bridge.graph.tree import TREE, WORKFLOW_NODE, socket_kind
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION

CLASS = Path(sys.argv[sys.argv.index("--") + 1])
INPAINT = "image_edit_flux2_klein_inpaint_prompt"
FLUX3 = "image_edit_flux3_klein_distilled_image_edit"
LTX = "video_ltx2_5_i2v"
SAFE = str.maketrans({c: "-" for c in '<>:"/\\|?*'})


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.05)
    return False


def workflow(tree, wf_id, loc, seed_key=None):
    n = tree.nodes.new(WORKFLOW_NODE)
    n.workflow_id = wf_id
    n.build(SESSION.specs[wf_id])
    n.location = loc
    seed = n.item(seed_key) if seed_key else next((it for it in n.params if it.kind == "seed"), None)
    if seed is not None:
        seed.i, seed.randomize = 7, False
    return n


def sock(node, kind):
    return next(s for s in node.inputs if socket_kind(s) == kind)


def prompt(tree, text, label, loc):
    p = tree.nodes.new("ComfyQPromptNode")
    p.text, p.label, p.location = text, label, loc
    return p


def outputs(tree, src, loc):
    view, save = tree.nodes.new("ComfyQViewerNode"), tree.nodes.new("ComfyQSaveNode")
    view.auto, view.location = True, loc
    save.auto, save.location = False, (loc[0], loc[1] - 360)
    tree.links.new(src.outputs[0], view.inputs[0])
    tree.links.new(src.outputs[0], save.inputs[0])


def write(tree, label, description, steps, left):
    note = preset_ops.steps_note(tree, steps, f"How to use · {label}")
    note.location = (left - note.width - 60, 0)
    bad = sum(not lk.is_valid for lk in tree.links)
    out = preset_ops.serialize(tree, label, description)
    for d in out["nodes"]:
        if d["type"] == WORKFLOW_NODE:
            d["match_scene"] = True              # size / duration / fps from the scene the preset is added to
    path = CLASS / (label.translate(SAFE) + ".json")
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"PRESET {path.name}: {len(tree.nodes)} nodes, {len(tree.links)} links ({bad} invalid)", flush=True)
    bpy.data.node_groups.remove(tree)


SESSION.connect("", "preset-builder")
if not pump_until(lambda: SESSION.connected and SESSION.workflows, 30):
    sys.exit(f"Cannot connect to ComfyQ: {SESSION.error or SESSION.status}")
for wid in (INPAINT, FLUX3, LTX):
    SESSION.specs[wid] = SESSION.client.params(wid)

# -- Find and replace an object ------------------------------------------------------------------------------------
tree = bpy.data.node_groups.new("find replace", TREE)
mask = tree.nodes.new("ComfyQMaskNode")
mask.style, mask.frame, mask.location, mask.label = "MATERIAL", "CURRENT", (-850, 0), "What to replace"
inp = workflow(tree, INPAINT, (-380, 0))
pr = prompt(tree, ("a deep emerald green velvet sofa with brass legs, same size and place, soft daylight, "
                   "photorealistic"), "The new object", (-850, -520))
tree.links.new(mask.outputs[0], sock(inp, "mask"))
tree.links.new(pr.outputs[0], sock(inp, "text"))
outputs(tree, inp, (120, 160))
write(tree, "Find and replace an object (SAM3 + Flux2 Klein inpaint)",
      ("Replace one object of the scene and keep the rest: type its name in Mask Zone › Find (SAM3 finds it in the "
       "camera view; the Mask Tool or Paint Zone also work), describe the new object, Run Graph. Flux2 Klein inpaint "
       "repaints only the zone, in the scene's material colours. About 30 s each for Find and inpaint (two models: "
       "Solo loads them in turn)."),
      ("1. Mask Zone › Find: the object to",
       "   replace (sofa), press ▶ and wait",
       "2. Grow: room for a bigger new object",
       "3. Prompt: the NEW object only",
       "4. Run Graph"), -850)

# -- Sketch → video -----------------------------------------------------------------------------------------------
tree = bpy.data.node_groups.new("sketch video", TREE)
sk = tree.nodes.new("ComfyQSketchNode")
sk.only, sk.frame, sk.label, sk.location = False, "START", "Sketch", (-1300, 0)
key = workflow(tree, FLUX3, (-850, 0), "seed")
kp = prompt(tree, ("Turn <ref_image_0> into a photograph: keep the scene, the composition and the camera exactly; every "
                   "black ink line becomes the real object it draws, at the same place and size. Photorealistic, "
                   "natural light."), "Start frame", (-1300, -420))
vid = workflow(tree, LTX, (-380, 0))
vp = prompt(tree, ("The camera holds still. What the drawing added comes to life with natural, gentle movement; the "
                   "building stays perfectly still. Natural ambient sound."), "Motion", (-850, -620))
tree.links.new(sk.outputs[0], sock(key, "image"))
tree.links.new(kp.outputs[0], sock(key, "text"))
tree.links.new(key.outputs[0], sock(vid, "image"))
tree.links.new(vp.outputs[0], sock(vid, "text"))
first = tree.nodes.new("ComfyQViewerNode")
first.label, first.auto, first.location = "Start frame", True, (-380, 520)
tree.links.new(key.outputs[0], first.inputs[0])
outputs(tree, vid, (120, 160))
write(tree, "Sketch → video (FLUX.3 Klein + LTX 2.5)",
      ("Draw over the scene in the camera view (Start Sketch), then FLUX.3 Klein makes it a photoreal start frame "
       "and LTX 2.5 animates it with sound, at the scene's length and size. Say what the lines are in the Start frame "
       "prompt and how things move in the Motion prompt. About 3 minutes (two models: Solo loads them in turn)."),
      ("1. Sketch › Start Sketch: draw over",
       "   the scene (Tab when done)",
       "2. Start frame: say what the lines are",
       "3. Motion: how things move (not the",
       "   camera, it holds still)",
       "4. Run Graph"), -1300)
SESSION.close()
print("DONE", flush=True)
