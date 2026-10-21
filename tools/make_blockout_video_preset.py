# SPDX-License-Identifier: GPL-3.0-or-later
"""Class preset "Blockout animation → photoreal video (FLUX.3 keyframe + LTX canny)", chosen on the GPU on 2026-10-21
(tools/cliff_video_check.py, live_check/cliff_house/):

  Scene Render (Material colours, first frame) → FLUX.3 Klein edit (Keyframe prompt, seed 7) → Keyframe viewer
  Scene Animation (clay) + that keyframe → LTX 2.5 IC-LoRA canny (Video prompt, seed 7, clip = the scene) → Viewer + Save

Material colours beat clay as the keyframe input (clay cones became zinc sculptures, green cones became pines); canny
beat depth by a little (crisper glass and edges, less flicker) and 3DReal failed (black sky, white cones).
The built-in "AI keyframe → video" preset is unchanged. Headless, sandbox profile:
    blender -b --online-mode --python tools/make_blockout_video_preset.py -- <out .json>
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
OUT = Path(argv[0] if argv else "blockout_video.json")
KLEIN, LTX = "image_edit_flux3_klein_image_edit", "video_edit_ltx2_5_iclora_canny_control"
LABEL = "Blockout animation → photoreal video (FLUX.3 keyframe + LTX canny)"
KEY_PROMPT = (
    "Turn <ref_image_0> into a photorealistic architectural photograph, keeping exactly the same composition, camera "
    "angle, shapes and proportions. A modern house hangs over the edge of a rocky granite cliff: board-formed concrete "
    "core and piers running down the rock, a warm larch-clad volume cantilevered over the void on thin steel struts "
    "with a deep glazed loggia, a white fibre-cement panelled volume with recessed windows, a dark standing-seam zinc "
    "tower, a timber deck terrace with a glass-sided infinity pool full of turquoise water. Pine forest on the plateau, "
    "snow patches and dry winter grass on the rocks, overcast winter sky with soft diffuse light, warm light glowing "
    "in the windows, crisp detail, high-end architectural visualisation.")
VIDEO_PROMPT = (
    "Photorealistic cinematic shot slowly rising and pushing in towards a modern house on the edge of a rocky cliff: "
    "board-formed concrete piers down the rock, a warm larch-clad volume cantilevered on thin steel struts, a white "
    "panelled volume, a dark zinc tower, a glass-sided pool with turquoise water on a timber deck. Snow patches and dry "
    "winter grass on the rocks, pine forest, overcast winter sky, soft diffuse light, warm light in the windows. Smooth "
    "steady camera, no people. Sound: soft wind over the cliff and distant birds.")
STEPS = ("1. Animate the scene camera (the blockout",
         "   can be plain boxes; give trees, water,",
         "   glass their own viewport colours)",
         "2. Keyframe prompt: describe the finished",
         "   picture (keep <ref_image_0> in it)",
         "3. Video prompt: the same scene, the camera",
         "   move and the sound",
         "4. Run Graph: FLUX.3 makes the first frame",
         "   (Keyframe viewer), then LTX films the",
         "   clay animation in that look (about 4 min)",
         "5. Keyframe wrong? Change its prompt or",
         "   seed and run again before the video")


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
for wf in (KLEIN, LTX):
    request_specs(wf)
if not pump_until(lambda: KLEIN in SESSION.specs and LTX in SESSION.specs, 60):
    sys.exit("Parameters did not load")


def key_of(wf, kind, word=""):
    return next(s.key for s in SESSION.specs[wf] if s.kind == kind and word in s.label.lower())


def node_input(node, key):
    return next(s for s in node.inputs if s.identifier == key)


tree = bpy.data.node_groups.new("Blockout video", TREE)
first = tree.nodes.new("ComfyQSceneRenderNode")
first.label, first.location, first.style, first.frame = "First frame (material colours)", (-1300.0, 300.0), \
    "MATERIAL", "START"
kprompt = tree.nodes.new("ComfyQPromptNode")
kprompt.label, kprompt.location, kprompt.text = "Keyframe prompt", (-1300.0, -20.0), KEY_PROMPT
klein = tree.nodes.new(WORKFLOW_NODE)
klein.workflow_id = KLEIN
klein.build(SESSION.specs[KLEIN])
klein.label, klein.location = "Keyframe (FLUX.3 Klein)", (-820.0, 220.0)
seed = klein.item(key_of(KLEIN, "seed"))
seed.i, seed.randomize = 7, False
tree.links.new(first.outputs[0], node_input(klein, key_of(KLEIN, "image")))
tree.links.new(kprompt.outputs[0], node_input(klein, key_of(KLEIN, "prompt")))
kview = tree.nodes.new("ComfyQViewerNode")
kview.label, kview.location, kview.auto = "Keyframe viewer", (-340.0, 520.0), True
tree.links.new(klein.outputs[0], kview.inputs[0])

anim = tree.nodes.new("ComfyQSceneAnimationNode")
anim.label, anim.location, anim.style = "Clay animation", (-820.0, -320.0), "CLAY"
vprompt = tree.nodes.new("ComfyQPromptNode")
vprompt.label, vprompt.location, vprompt.text = "Video prompt", (-820.0, -620.0), VIDEO_PROMPT
ltx = tree.nodes.new(WORKFLOW_NODE)
ltx.workflow_id = LTX
ltx.build(SESSION.specs[LTX])
ltx.label, ltx.location = "Video (LTX 2.5 canny)", (-340.0, -40.0)
for s in SESSION.specs[LTX]:
    it = ltx.item(s.key)
    if s.kind == "seed":
        it.i, it.randomize = 7, False
    elif s.kind == "bool" and "start image" in s.label.lower():
        it.b = True
    elif s.kind == "bool" and "enhance" in s.label.lower():
        it.b = False
tree.links.new(anim.outputs[0], node_input(ltx, key_of(LTX, "video")))
tree.links.new(klein.outputs[0], node_input(ltx, key_of(LTX, "image")))
tree.links.new(vprompt.outputs[0], node_input(ltx, key_of(LTX, "prompt")))
view = tree.nodes.new("ComfyQViewerNode")
view.label, view.location, view.auto = "Viewer", (160.0, 160.0), True
save = tree.nodes.new("ComfyQSaveNode")
save.label, save.location = "Save", (160.0, -200.0)
tree.links.new(ltx.outputs[0], view.inputs[0])
tree.links.new(ltx.outputs[0], save.inputs[0])
note = preset_ops.steps_note(tree, STEPS, f"How to use · {LABEL}")
note.location = (-1300.0 - note.width - 60.0, 300.0)
bad = sum(not lk.is_valid for lk in tree.links)
data = preset_ops.serialize(
    tree, LABEL,
    "A blockout's camera animation filmed as a photoreal video. FLUX.3 Klein first turns the first frame (rendered "
    "with the materials' viewport colours, so trees, water and glass read as such) into a photoreal picture; LTX 2.5 "
    "IC-LoRA canny then follows the clay animation's edges and camera move in that look, with a sound track. Tested on "
    "a cliff-edge house (2026-10-21): the volumes, struts and glass pool hold for the whole move; about 2 min for the "
    "keyframe and 4 min for the video, two model loads. Canny beat depth (crisper edges), 3DReal failed (black sky).")
for d in data["nodes"]:
    if d.get("workflow_id") == LTX:
        d["match_scene"] = True                       # clip length = the scene's duration where the preset is added
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"PRESET {OUT.name}: {len(tree.nodes)} nodes, {len(tree.links)} links ({bad} invalid)", flush=True)
SESSION.close()
