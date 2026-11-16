# SPDX-License-Identifier: GPL-3.0-or-later
"""Class presets for the ComfyQ Sketch (audit 2, phase 4; 2026-11-16):

  * Sketch → photoreal (FLUX.3 Klein edit): the sketch drawn OVER the clay scene → FLUX.3 Klein distilled edit
    (the prompt says what the lines are) → Viewer + Save;
  * Sketch Only → picture (Qwen canny): the drawing alone on white → Qwen Image 2.1 "draw from edges" (its lines are
    the layout, the prompt everything else; size matched to the scene) → Viewer + Save.

    blender -b --online-mode --python tools/make_sketch_presets.py -- <class presets folder>

Seed 7 (not random), How to use notes. Qwen canny at control strength 0.6 (1.0 traces the lines; GPU 2026-11-16:
cabin drawing → Qwen 1.0 / Qwen 0.6 / FLUX.3, live_check/sketch/only_compare.jpg). Runs headless inside Blender with the extension installed (sandbox
profile; connects to ComfyQ for the workflows' parameters).
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
FLUX3 = "image_edit_flux3_klein_distilled_image_edit"
CANNY = "image_qwen_image_2_1_control_canny"
PRESETS = [
    {
        "label": "Sketch → photoreal (FLUX.3 Klein edit)",
        "workflow": FLUX3, "only": False, "seed_key": "seed",
        "description": ("Draw over the scene in the camera view (Start Sketch) and turn scene and drawing into a "
                        "photograph: FLUX.3 Klein distilled edit keeps the camera and the layout and makes each "
                        "drawn line the object it stands for. Say in the prompt what the lines are. About 20 s."),
        "prompt": ("Turn <ref_image_0> into a photograph: keep the scene, the composition and the camera exactly; "
                   "every black ink line becomes the real object it draws, at the same place and size. "
                   "Photorealistic, natural light."),
        "steps": ("1. Sketch › Start Sketch: draw in the",
                  "   camera view (Tab when done)",
                  "2. Prompt: say what the lines are",
                  "   (the circle in the sky is a balloon)",
                  "3. Run Graph"),
    },
    {
        "label": "Sketch Only → picture (Qwen canny)",
        "workflow": CANNY, "only": True, "seed_key": "ksampler_seed_459_458",
        # 1.0 (trained strength) traced the drawing literally: a circle-on-a-stick tree stayed a drawn circle in the
        # sky; 0.6 kept the house's place and shape with no lines left (GPU 2026-11-16, live_check/sketch/)
        "settings": {"controlnet_strength_903": 0.6},
        "description": ("Draw a picture from nothing: the drawing alone, on white (Sketch Only), is the layout and "
                        "Qwen Image 2.1 draw-from-edges paints everything else from the prompt (materials, light, "
                        "place). Size follows the scene's camera. About 30 s."),
        "prompt": ("A photograph of what the drawing shows, its lines kept as the outlines of real objects, natural "
                   "materials and light, photorealistic"),
        "steps": ("1. Sketch › Start Sketch: draw the",
                  "   whole picture in the camera view",
                  "   (Sketch Only: the scene is not sent)",
                  "2. Prompt: what it is, materials, light",
                  "3. Run Graph"),
    },
]


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.05)
    return False


SESSION.connect("", "preset-builder")
if not pump_until(lambda: SESSION.connected and SESSION.workflows, 30):
    sys.exit(f"Cannot connect to ComfyQ: {SESSION.error or SESSION.status}")
for p in PRESETS:
    SESSION.specs[p["workflow"]] = SESSION.client.params(p["workflow"])

for p in PRESETS:
    tree = bpy.data.node_groups.new("sketch preset", TREE)
    sk = tree.nodes.new("ComfyQSketchNode")
    sk.only, sk.label, sk.location = p["only"], "Sketch", (-850, 0)
    wf = tree.nodes.new(WORKFLOW_NODE)
    wf.workflow_id = p["workflow"]
    wf.build(SESSION.specs[p["workflow"]])
    wf.location = (-380, 0)
    seed = wf.item(p["seed_key"])
    seed.i, seed.randomize = 7, False
    for k, v in p.get("settings", {}).items():
        wf.item(k).f = v
    pr = tree.nodes.new("ComfyQPromptNode")
    pr.text, pr.label, pr.location = p["prompt"], "Prompt", (-850, -420)
    view, save = tree.nodes.new("ComfyQViewerNode"), tree.nodes.new("ComfyQSaveNode")
    view.auto, view.location = True, (120, 160)
    save.auto, save.location = False, (120, -200)
    tree.links.new(sk.outputs[0], next(s for s in wf.inputs if socket_kind(s) == "image"))
    tree.links.new(pr.outputs[0], next(s for s in wf.inputs if socket_kind(s) == "text"))
    tree.links.new(wf.outputs[0], view.inputs[0])
    tree.links.new(wf.outputs[0], save.inputs[0])
    note = preset_ops.steps_note(tree, p["steps"], f"How to use · {p['label']}")
    note.location = (-850 - note.width - 60, 0)
    bad = sum(not lk.is_valid for lk in tree.links)
    out = preset_ops.serialize(tree, p["label"], p["description"])
    for d in out["nodes"]:
        if d["type"] == WORKFLOW_NODE:
            d["match_scene"] = True              # size / aspect from the scene the preset is added to
    path = CLASS / (p["label"].translate(str.maketrans({c: "-" for c in '<>:"/\\|?*'})) + ".json")
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"PRESET {path.name}: {len(tree.nodes)} nodes, {len(tree.links)} links ({bad} invalid)", flush=True)
    bpy.data.node_groups.remove(tree)
SESSION.close()
print("DONE", flush=True)
