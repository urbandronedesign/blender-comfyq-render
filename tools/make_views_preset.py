# SPDX-License-Identifier: GPL-3.0-or-later
"""Class preset "Four views: clay or photo → rendering (Flux2 Klein edit)": four Image nodes (front, left, back,
right) the student fills with clay renders of a 3D model or with real photos → one shared Prompt → four Klein image
edits with the SAME fixed seed (consistent materials across the views) → a Save per view + one Viewer. No 3D step.

Runs headless inside Blender with the extension installed (sandbox profile), connects to ComfyQ for Klein's
parameters, builds the graph with the plugin's own nodes and saves it with `preset_ops.serialize`:

    blender -b --online-mode --python tools/make_views_preset.py -- <out .json>
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
OUT = Path(argv[0] if argv else "four_views.json")
WF = "image_edit_flux2_klein_9b_image_edit"
SEED = 7
LABEL = "Four views: clay or photo → rendering (Flux2 Klein edit)"
PROMPT = ("Re-render this picture of a building as a finished photorealistic architectural photograph, keeping "
          "exactly the same shape, proportions, openings and the same camera view: the main volume is clad in a "
          "perforated copper skin with a pixelated pattern of punched holes of varying sizes, the openings are deep "
          "windows with dark glass, the ground floor is glazed, the base is a light concrete plinth. Plain pure "
          "white background, soft even daylight, sharp architectural photograph.")
STEPS = ("1. In each Image node, open one view of the",
         "   same building: Front, Left, Back, Right",
         "   (clay renders of your model, or photos)",
         "2. Describe the materials in Prompt",
         "   (it feeds the 4 views; keep 'same shape')",
         "3. Run Graph: 4 jobs, one per view, all with",
         "   the same seed so the materials match",
         "4. Each Save keeps its view next to the .blend",
         "   (change Seed on all 4 for another variant)")


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
request_specs(WF)
if not pump_until(lambda: WF in SESSION.specs, 60):
    sys.exit(f"Parameters of {WF} did not load")
specs = SESSION.specs[WF]
image_key = next(s.key for s in specs if s.kind == "image")
prompt_key = next(s.key for s in specs if s.kind == "prompt" and "negative" not in s.label.lower())
seed_key = next(s.key for s in specs if s.kind == "seed")

tree = bpy.data.node_groups.new("Four views", TREE)
prompt = tree.nodes.new("ComfyQPromptNode")
prompt.label, prompt.location, prompt.text = "Prompt (all 4 views)", (-900.0, 620.0), PROMPT
STEP = -360.0
for n, view in enumerate(("Front", "Left", "Back", "Right")):
    y = n * STEP
    img = tree.nodes.new("ComfyQImageNode")
    img.label, img.location = f"{view} view", (-900.0, y)
    wf = tree.nodes.new(WORKFLOW_NODE)
    wf.workflow_id = WF
    wf.build(specs)
    wf.label, wf.location = f"{view} rendering", (-440.0, y)
    seed = wf.item(seed_key)
    seed.i, seed.randomize = SEED, False           # the same seed on the 4 views: the same materials
    tree.links.new(img.outputs[0], next(s for s in wf.inputs if s.identifier == image_key))
    tree.links.new(prompt.outputs[0], next(s for s in wf.inputs if s.identifier == prompt_key))
    save = tree.nodes.new("ComfyQSaveNode")
    save.label, save.location = f"Save {view.lower()}", (60.0, y)
    save.folder, save.pattern = "//comfyq/views/", f"{view.lower()}_{{date}}_{{time}}"
    save.auto = True                                # "Save When Ready": each view is written as soon as it arrives
    tree.links.new(wf.outputs[0], save.inputs[0])
    if view == "Front":
        viewer = tree.nodes.new("ComfyQViewerNode")
        viewer.label, viewer.location = "Viewer", (60.0, y + 300.0)
        tree.links.new(wf.outputs[0], viewer.inputs[0])
note = preset_ops.steps_note(tree, STEPS, f"How to use · {LABEL}")
note.location = (-900.0 - note.width - 60.0, 620.0)

bad = [lk for lk in tree.links if not lk.is_valid]
kinds = sorted({socket_kind(s) for n in tree.nodes if n.bl_idname == WORKFLOW_NODE for s in n.inputs if s.is_linked})
print(f"BUILT {len(tree.nodes)} nodes, {len(tree.links)} links ({len(bad)} invalid), linked input kinds {kinds}", flush=True)
data = preset_ops.serialize(tree, LABEL, "Four views of one building (front, left, back, right) — clay renders of "
                            "your 3D model or real photos — rendered as finished photographs by Flux2 Klein image edit, "
                            "one shared prompt and the same seed so the materials match across the views. "
                            "No 3D step: the four pictures are saved next to the .blend.")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
print("PRESET", OUT, flush=True)
SESSION.close()
