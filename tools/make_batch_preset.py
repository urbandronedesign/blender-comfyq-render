# SPDX-License-Identifier: GPL-3.0-or-later
"""Class preset "Batch: folder → rendering (Flux2 Klein edit)": a Media Folder (pictures: photos or clay renders,
subfolders included) → one Prompt → Klein image edit with a fixed seed (a consistent series) → Save with a batch
suffix. Run Graph renders the files one after the other into `<folder>_renders`, same subfolders.

Runs headless inside Blender with the extension installed (sandbox profile):
    blender -b --online-mode --python tools/make_batch_preset.py -- <out .json>
"""
import json
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.graph import preset_ops
from bl_ext.user_default.comfyq_bridge.graph.tree import TREE, WORKFLOW_NODE
from bl_ext.user_default.comfyq_bridge.ops.workflow import request_specs
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(argv[0] if argv else "batch_folder.json")
WF = "image_edit_flux2_klein_9b_image_edit"
LABEL = "Batch: folder → rendering (Flux2 Klein edit)"
PROMPT = ("Re-render this picture as a finished photorealistic photograph, keeping exactly the same shapes, "
          "proportions, openings and the same camera view. Describe the materials here: for example a perforated "
          "copper skin, deep windows with dark glass, a glazed ground floor, a light concrete plinth. Soft even "
          "daylight, sharp architectural photograph.")
STEPS = ("1. Media Folder: choose a folder of pictures",
         "   (photos or clay renders; subfolders too)",
         "2. Describe the materials in Prompt",
         "3. Run Graph: one picture after the other,",
         "   progress and time left on the folder node",
         "4. Results: a new folder <name>_renders next",
         "   to yours, same subfolders, name + suffix",
         "   (Save › Batch Suffix). Stop, then Run Graph",
         "   again: it goes on with what is missing")


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

tree = bpy.data.node_groups.new("Batch folder", TREE)
folder = tree.nodes.new("ComfyQFolderNode")
folder.label, folder.location = "Media Folder", (-900.0, 0.0)
prompt = tree.nodes.new("ComfyQPromptNode")
prompt.label, prompt.location, prompt.text = "Prompt", (-900.0, -330.0), PROMPT
wf = tree.nodes.new(WORKFLOW_NODE)
wf.workflow_id = WF
wf.build(specs)
wf.label, wf.location = "Rendering", (-420.0, 0.0)
seed = wf.item(seed_key)
seed.i, seed.randomize = 7, False                  # one seed for the whole folder: a consistent series
tree.links.new(folder.outputs["Image"], next(s for s in wf.inputs if s.identifier == image_key))
tree.links.new(prompt.outputs[0], next(s for s in wf.inputs if s.identifier == prompt_key))
save = tree.nodes.new("ComfyQSaveNode")
save.label, save.location, save.batch_suffix = "Save", (80.0, 0.0), "_render"
tree.links.new(wf.outputs[0], save.inputs[0])
note = preset_ops.steps_note(tree, STEPS, f"How to use · {LABEL}")
note.location = (-900.0 - note.width - 60.0, 0.0)
print(f"BUILT {len(tree.nodes)} nodes, {len(tree.links)} links ({sum(not lk.is_valid for lk in tree.links)} invalid)",
      flush=True)
data = preset_ops.serialize(tree, LABEL, "A folder of pictures (photos or clay renders, subfolders included) rendered "
                            "one after the other by Flux2 Klein image edit, one prompt and one seed for a consistent "
                            "series. The results go to a new folder next to it, <name>_renders, with the same "
                            "subfolders and each file's name plus the Save node's Batch Suffix.")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
print("PRESET", OUT, flush=True)
SESSION.close()
