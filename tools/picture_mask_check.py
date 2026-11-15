# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU check of the Picture Mask node on an EXISTING picture: SAM3 finds objects by name, one is unticked, Flux2 Klein
inpaint repaints only the ticked one. Headless, sandbox profile (Solo on: loads SAM3, then the inpaint model).

    blender -b --online-mode --python tools/picture_mask_check.py -- <picture.png> <out folder> "<words>" "<untick>" "<prompt>"
"""
import importlib
import json
import shutil
import sys
import time
from pathlib import Path

import addon_utils
import bpy

PIC, OUT, WORDS, UNTICK, PROMPT = sys.argv[sys.argv.index("--") + 1:][:5]
OUT = Path(OUT)
OUT.mkdir(parents=True, exist_ok=True)
MOD = "bl_ext.user_default.comfyq_bridge"
INPAINT = "image_edit_flux2_klein_inpaint_prompt"


def ext(name):
    return importlib.import_module(f"{MOD}.{name}")


def log(text, **data):
    print(time.strftime("%H:%M:%S"), "[pm]", text, json.dumps(data, default=str) if data else "", flush=True)


def pump_until(pred, timeout):
    S, grun = ext("runtime.session").SESSION, ext("graph.run")
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        S.pump()
        grun.tick_all()
        if pred():
            return True
        time.sleep(0.2)
    return False


addon_utils.enable(MOD, default_set=True)
S, grun = ext("runtime.session").SESSION, ext("graph.run")
prefs = bpy.context.preferences.addons[MOD].preferences
prefs.solo_mode = True                                       # sandbox profile only
S.connect(prefs.profile, "picture-mask-check", prefs.url.strip())
if not pump_until(lambda: S.connected and S.workflows, 60):
    sys.exit("cannot connect")
S.specs[INPAINT] = S.client.params(INPAINT)

tree = bpy.data.node_groups.new("Picture mask check", "ComfyQGraphTree")
img_node, pm, wf, pr = (tree.nodes.new(t) for t in ("ComfyQImageNode", "ComfyQPictureMaskNode", "ComfyQWorkflowNode",
                                                      "ComfyQPromptNode"))
img_node.image = bpy.data.images.load(PIC)
pm.words, pm.grow, pm.feather = WORDS, 1.0, 0.4
wf.workflow_id = INPAINT
wf.build(S.specs[INPAINT])
pr.text = PROMPT
tree.links.new(img_node.outputs[0], pm.inputs["Image"])
tree.links.new(pm.outputs["Mask"], next(s for s in wf.inputs if ext("graph.tree").socket_kind(s) == "mask"))
tree.links.new(pr.outputs[0], next(s for s in wf.inputs if ext("graph.tree").socket_kind(s) == "text"))


def run(targets, what):
    t0 = time.monotonic()
    ok, msg = grun.start(tree, bpy.context.scene, targets, [])
    log(f"run {what}", ok=ok, msg=msg)
    pump_until(lambda: tree.name not in grun.RUNS, 1800)
    log(f"done {what}", seconds=round(time.monotonic() - t0), note=pm.note, wf_note=getattr(wf, "note", ""))


run([pm.name], "find")
found = [(r["label"], r["coverage_pct"], r.get("picked")) for r in pm.found()]
log("found", regions=found)
if pm.preview:
    shutil.copy(pm.preview, OUT / "1_found_preview.png")
if UNTICK:
    bpy.ops.comfyq.picture_mask_pick(tree=tree.name, node=pm.name, label=UNTICK)
log("needs SAM3 again?", model=grun.needed_model(pm) or "no")
run([wf.name], "inpaint")
if pm.preview:
    shutil.copy(pm.preview, OUT / "2_zone_preview.png")
if pm.result:
    shutil.copy(pm.result, OUT / "3_mask_sent.png")
if wf.result and Path(wf.result).is_file():
    shutil.copy(wf.result, OUT / "4_inpaint_result.png")
log("RESULT", ok=bool(wf.result), result=wf.result, found=found)
S.close()
