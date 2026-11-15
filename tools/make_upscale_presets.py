# SPDX-License-Identifier: GPL-3.0-or-later
"""Class presets that end with an UPSCALE: an existing class preset + the upscaler after its last model (2026-11-15).

    blender -b --online-mode --python tools/make_upscale_presets.py -- <class presets folder> <video upscaler id>

  Cycles render → photoreal 4K (FLUX.3 Klein + SeedVR2 4K)       = Cycles render → photoreal + SeedVR2 4K
  Blockout animation → photoreal video ×2 (… + <video upscaler>) = Blockout animation → photoreal video + ×2

Viewer + Save show the upscaled result; a second Viewer ("Before upscale") keeps the model's own result in view.
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

argv = sys.argv[sys.argv.index("--") + 1:]
CLASS = Path(argv[0])
VIDEO_UP = argv[1] if len(argv) > 1 else "utility_relayvsr_video_x2_upscale"
IMAGE_UP = "utility_seedvr2_4k_upscale"
FILE_SAFE = str.maketrans({c: "-" for c in '<>:"/\\|?*→'})
VIDEO_UP_NAME = {"utility_relayvsr_video_x2_upscale": "RelayVSR", "utility_seedvr2_hd_upscale": "SeedVR2 HD",
                 "utility_seedvr2_3b_int8_video_upscale": "SeedVR2 3B"}.get(VIDEO_UP, VIDEO_UP)


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.05)
    return False


def build_with_upscale(src_json: Path, up_id: str, label: str, description: str, steps: tuple) -> Path:
    data = json.loads(src_json.read_text(encoding="utf-8"))
    for d in data["nodes"]:
        if d.get("workflow_id"):
            SESSION.specs[d["workflow_id"]] = SESSION.client.params(d["workflow_id"])
    SESSION.specs[up_id] = SESSION.client.params(up_id)
    tree = bpy.data.node_groups.new("upscale", TREE)
    preset_ops.build_saved(data, tree, bpy.context.scene)
    # the model feeding the Save node = the last one of the chain; only ITS outputs move to the upscaler
    # (a keyframe viewer fed by an earlier model stays where it is)
    save = next(n for n in tree.nodes if n.bl_idname == "ComfyQSaveNode")
    last = next(lk.from_node for lk in tree.links if lk.to_node == save)
    outs = [lk.to_node for lk in tree.links if lk.from_node == last
            and lk.to_node.bl_idname in ("ComfyQViewerNode", "ComfyQSaveNode")]
    kind = socket_kind(last.outputs[0])
    up = tree.nodes.new(WORKFLOW_NODE)
    up.workflow_id = up_id
    up.build(SESSION.specs[up_id])
    up.label = "Upscale"
    up.location = (last.location.x + 480, last.location.y)
    tree.links.new(last.outputs[0], next(s for s in up.inputs if socket_kind(s) == kind))
    for n in outs:                                     # outputs move one column right, fed by the upscaler
        n.location.x += 480
        for lk in [lk for lk in tree.links if lk.to_node == n]:
            tree.links.remove(lk)
        tree.links.new(up.outputs[0], n.inputs[0])
    before = tree.nodes.new("ComfyQViewerNode")
    before.label, before.auto = "Before upscale", True
    before.location = (last.location.x + 480, last.location.y + 420)
    tree.links.new(last.outputs[0], before.inputs[0])
    for f in [n for n in tree.nodes if n.bl_idname == "NodeFrame"]:   # the old How to use note
        tree.nodes.remove(f)
    note = preset_ops.steps_note(tree, steps, f"How to use · {label}")
    note.location = (min(n.location.x for n in tree.nodes) - note.width - 60.0, 0.0)
    bad = sum(not lk.is_valid for lk in tree.links)
    out = preset_ops.serialize(tree, label, description)
    path = CLASS / f"{label.translate(FILE_SAFE)}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"PRESET {path.name}: {len(tree.nodes)} nodes, {len(tree.links)} links ({bad} invalid)", flush=True)
    bpy.data.node_groups.remove(tree)
    return path


SESSION.connect("", "preset-builder")
if not pump_until(lambda: SESSION.connected and SESSION.workflows, 30):
    sys.exit(f"Cannot connect to ComfyQ: {SESSION.error or SESSION.status}")

build_with_upscale(
    next(CLASS.glob("Cycles render - photoreal (FLUX.3 Klein).json")), IMAGE_UP,
    "Cycles render → photoreal 4K (FLUX.3 Klein + SeedVR2 4K)",
    "A Cycles render of the camera view made photoreal by FLUX.3 Klein (keeps the layout), then enlarged to 4K by "
    "SeedVR2 with real detail rebuilt (fabric, wood, foliage). Before upscale shows the FLUX.3 picture; Viewer and Save "
    "the 4K one. About 20 s for the picture, then 1 to 2 minutes for the 4K (two models: Solo loads them in turn).",
    ("1. Frame the shot with the scene camera",
     "2. Describe the materials in Prompt",
     "3. Run Graph: Cycles, FLUX.3, then 4K",
     "4. Before upscale: the photoreal picture",
     "   Viewer / Save: the 4K version",
     "5. Only the 4K step again: Generate on",
     "   the Upscale node"))

build_with_upscale(
    next(CLASS.glob("Blockout animation - photoreal video*.json")), VIDEO_UP,
    f"Blockout animation → photoreal video ×2 (FLUX.3 + LTX canny + {VIDEO_UP_NAME})",
    f"The blockout animation filmed as a photoreal video (FLUX.3 keyframe + LTX 2.5 canny, as the base preset), then "
    f"enlarged ×2 by {VIDEO_UP_NAME} with the sound kept. Before upscale shows the LTX clip; Viewer and Save the "
    f"enlarged one.",
    ("1. Animate the camera; give trees, water",
     "   and glass their own viewport colours",
     "2. Keyframe prompt: frame 1 only; Video",
     "   prompt: the whole shot",
     "3. Run Graph: keyframe, video, then ×2",
     "4. Before upscale: the LTX clip",
     "   Viewer / Save: the enlarged video"))
SESSION.close()
print("DONE", flush=True)
