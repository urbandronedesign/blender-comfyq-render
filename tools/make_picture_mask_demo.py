# SPDX-License-Identifier: GPL-3.0-or-later
"""A test file for masking an EXISTING picture: Image (packed) → Picture Mask (SAM3 finds "armchair, floor lamp") →
Flux2 Klein inpaint → Viewer + Save, with a How to use note. Nothing is run here (the student runs it).

Headless, sandbox profile with the extension installed (connects to ComfyQ for the inpaint workflow's parameters):
    blender -b --online-mode --python tools/make_picture_mask_demo.py -- <picture.png> <out.blend>
"""
import sys
import time

import bpy
from bl_ext.user_default.comfyq_bridge.graph import preset_ops
from bl_ext.user_default.comfyq_bridge.graph.tree import socket_kind
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION

PIC, OUT = sys.argv[sys.argv.index("--") + 1:][:2]
INPAINT = "image_edit_flux2_klein_inpaint_prompt"
PROMPT = ("a brown leather Chesterfield armchair with a buttoned back, photorealistic, same perspective and soft "
          "daylight as the room")


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.05)
    return False


SESSION.connect("", "picture-mask-demo")
if not pump_until(lambda: SESSION.connected and SESSION.workflows, 30):
    sys.exit(f"Cannot connect to ComfyQ: {SESSION.error or SESSION.status}")
SESSION.specs[INPAINT] = SESSION.client.params(INPAINT)

tree = bpy.data.node_groups.new("ComfyQ Graph", "ComfyQGraphTree")
tree.use_fake_user = True
img = bpy.data.images.load(PIC)
img.name = "Room render (an existing picture)"
img.pack()                                            # the file carries its picture
src = tree.nodes.new("ComfyQImageNode")
src.image, src.label, src.location = img, "Existing picture", (-1300, 0)
pm = tree.nodes.new("ComfyQPictureMaskNode")
pm.words, pm.grow, pm.feather, pm.location = "armchair, floor lamp", 1.0, 0.4, (-850, 0)
wf = tree.nodes.new("ComfyQWorkflowNode")
wf.workflow_id = INPAINT
wf.build(SESSION.specs[INPAINT])
wf.location = (-380, 0)
pr = tree.nodes.new("ComfyQPromptNode")
pr.text, pr.location = PROMPT, (-850, -520)
view, save = tree.nodes.new("ComfyQViewerNode"), tree.nodes.new("ComfyQSaveNode")
view.location, save.location, view.auto, save.auto = (120, 160), (120, -160), True, True
tree.links.new(src.outputs[0], pm.inputs["Image"])
tree.links.new(pm.outputs["Mask"], next(s for s in wf.inputs if socket_kind(s) == "mask"))
tree.links.new(pr.outputs[0], next(s for s in wf.inputs if socket_kind(s) == "text"))
tree.links.new(wf.outputs[0], view.inputs[0])
tree.links.new(wf.outputs[0], save.inputs[0])
note = preset_ops.steps_note(tree, (
    "1. Picture Mask › Find: the objects to look",
    "   for (armchair, floor lamp…)",
    "2. Run Graph: SAM3 finds them (Solo loads",
    "   it), the ticked ones are repainted",
    "3. Untick what to keep, or Edit on Picture:",
    "   click a found object to tick / untick,",
    "   drag a box, Shift+drag a free shape,",
    "   Ctrl+drag cuts",
    "4. Prompt: what goes in the zone",
    "5. Run Graph again: only the inpaint runs",
    "   (the objects are not searched again)"), "How to use · mask an existing picture")
note.location = (-1300 - note.width - 60, 0)
SESSION.close()
bpy.ops.wm.save_as_mainfile(filepath=OUT)
print("SAVED", OUT, "nodes", len(tree.nodes), "links", len(tree.links), "invalid",
      sum(not lk.is_valid for lk in tree.links), flush=True)
