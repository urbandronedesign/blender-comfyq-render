# SPDX-License-Identifier: GPL-3.0-or-later
"""Class preset: repaint the BACKGROUND of an existing picture, keeping the subject pixel for pixel (2026-11-15).

    blender -b --online-mode --python tools/make_bg_repaint_preset.py -- <class presets folder>

Image → Picture Mask (SAM3 finds the subject, Invert = everything else is repainted, Grow keeps a margin around the
subject, Feather blends the edge) → Flux2 Klein inpaint (prompt = the background only) → SeedVR2 4K → Viewer + Save,
with a "Before upscale" Viewer. Made for splat / scan renders whose background is smeared (few photos saw it).
KEEP THE GROUND too (GPU 2026-11-15, truck splat): with only "truck" kept, the inpaint repainted the street, lost the
perspective and invented a second truck under a floating one; "truck, sidewalk, road" kept → clean tower and trees.
Runs headless inside Blender with the extension installed (sandbox profile; connects to ComfyQ for parameters).
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
UPSCALE = "utility_seedvr2_4k_upscale"
LABEL = "Repaint the background of a picture (SAM3 + Flux2 Klein inpaint + SeedVR2 4K)"
DESCRIPTION = ("Keeps the subject of an existing picture exactly as it is and paints a new, sharp background around "
               "it: SAM3 finds the subject and the ground it stands on by name (Picture Mask, Invert on), Flux2 Klein "
               "inpaint repaints everything else from the prompt, SeedVR2 enlarges the result to 4K. For renders of Gaussian splats or scans whose "
               "background is smeared. About 1 minute plus model loading (three models: Solo loads them in turn).")
PROMPT = ("behind the subject: clean architecture with crisp windows and reflections, natural trees, clear sky; "
          "sharp photorealistic daylight photograph, same perspective and light as the subject, nothing in front of "
          "the subject")
STEPS = ("1. Image: the picture to fix",
         "2. Picture Mask › Find: what to KEEP: the",
         "   subject AND the ground it stands on",
         "   (truck, sidewalk, road); Invert is on,",
         "   so everything else is repainted",
         "3. Prompt: describe ONLY the background",
         "4. Run Graph: SAM3, inpaint, then 4K",
         "5. A piece of the subject repainted? Raise",
         "   Grow, or Edit on Picture and draw it")


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
for wid in (INPAINT, UPSCALE):
    SESSION.specs[wid] = SESSION.client.params(wid)

tree = bpy.data.node_groups.new("bg repaint", TREE)
src = tree.nodes.new("ComfyQImageNode")
src.label, src.location = "Picture to fix", (-1300, 0)
pm = tree.nodes.new("ComfyQPictureMaskNode")
pm.words, pm.invert, pm.grow, pm.feather, pm.location = "truck, sidewalk, road", True, 0.6, 0.4, (-850, 0)
pm.label = "Subject to keep"
wf = tree.nodes.new(WORKFLOW_NODE)
wf.workflow_id = INPAINT
wf.build(SESSION.specs[INPAINT])
wf.label, wf.location = "Repaint background", (-380, 0)
pr = tree.nodes.new("ComfyQPromptNode")
pr.text, pr.label, pr.location = PROMPT, "Background", (-850, -520)
up = tree.nodes.new(WORKFLOW_NODE)
up.workflow_id = UPSCALE
up.build(SESSION.specs[UPSCALE])
up.label, up.location = "Upscale", (100, 0)
before, view, save = (tree.nodes.new("ComfyQViewerNode"), tree.nodes.new("ComfyQViewerNode"),
                      tree.nodes.new("ComfyQSaveNode"))
before.label, before.auto, before.location = "Before upscale", True, (100, 420)
view.auto, view.location = True, (600, 160)
save.auto, save.location = False, (600, -200)
tree.links.new(src.outputs[0], pm.inputs["Image"])
tree.links.new(pm.outputs["Mask"], next(s for s in wf.inputs if socket_kind(s) == "mask"))
tree.links.new(pr.outputs[0], next(s for s in wf.inputs if socket_kind(s) == "text"))
tree.links.new(wf.outputs[0], next(s for s in up.inputs if socket_kind(s) == "image"))
tree.links.new(wf.outputs[0], before.inputs[0])
tree.links.new(up.outputs[0], view.inputs[0])
tree.links.new(up.outputs[0], save.inputs[0])
note = preset_ops.steps_note(tree, STEPS, "How to use · repaint the background")
note.location = (-1300 - note.width - 60, 0)
bad = sum(not lk.is_valid for lk in tree.links)
out = preset_ops.serialize(tree, LABEL, DESCRIPTION)
path = CLASS / (LABEL.replace("/", "-") + ".json")
path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"PRESET {path.name}: {len(tree.nodes)} nodes, {len(tree.links)} links ({bad} invalid)", flush=True)
SESSION.close()
print("DONE", flush=True)
