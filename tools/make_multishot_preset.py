# SPDX-License-Identifier: GPL-3.0-or-later
"""Class preset "Multi-shot film: one look in every shot (FLUX.3 + Qwen board + LTX canny)" (docs/multi-shot.md).

Three shots = three timeline markers bound to cameras, "Shot 1", "Shot 2", "Shot 3" (the source nodes' Shot field):

  Look prompt (materials, season, light) ── joined to every shot's own lines
  Shot 1: first frame (material colours) → FLUX.3 Klein edit → hero keyframe ──→ Material Board
  Shot 2, 3: first frame + the board → Qwen 2.1 edit → keyframe   (the board carries the materials, not the layout)
  every shot: clay animation + its keyframe → LTX 2.5 canny (seed 7, up to 10 s) → Film (Shot 1, 2, 3)

Headless, sandbox profile:  blender -b --online-mode --python tools/make_multishot_preset.py -- <out .json>
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
OUT = Path(argv[0] if argv else "multishot.json")
FLUX, QWEN, LTX = ("image_edit_flux3_klein_image_edit", "image_qwen_image_2_1_image_edit",
                   "video_edit_ltx2_5_iclora_canny_control")
LABEL = "Multi-shot film: one look in every shot (FLUX.3 + Qwen board + LTX canny)"
LOOK = ("The same house in every shot: a modern house cantilevered over the edge of a rocky granite cliff, "
        "board-formed concrete core and piers, a warm larch-clad volume with vertical battens on thin steel struts, "
        "white fibre-cement panels, a dark standing-seam zinc tower, a timber deck terrace with a glass-sided pool of "
        "turquoise water, pine trees on the plateau, snow patches and dry winter grass on the rocks, soft warm "
        "overcast winter light at dusk, warm light glowing in the windows, high-end architectural photography.")
KEY_FLUX = ("Turn <ref_image_0> into a photorealistic photograph, keeping exactly the same composition, camera "
            "angle, shapes and proportions.")
KEY_QWEN = ("Turn <image1> into a photorealistic photograph, keeping exactly its composition, camera angle, shapes "
            "and proportions. Use only the materials shown in the swatches of <image2>, not their layout.")
SHOTS = (
    ("Shot 1", KEY_FLUX + " A low view from the slope up at the house and its concrete piers.",
     ("Photorealistic cinematic shot: a slow push-in rising towards the house from the slope below. Smooth steady "
     "camera, no people. Sound: wind over the cliff.")),
    ("Shot 2", KEY_QWEN + " A three-quarter view from the right: the larch volume on its struts, the terrace and the "
     "glass pool.",
     ("Photorealistic cinematic shot: a slow orbit around the house from the right. Smooth steady camera, no people. "
     "Sound: wind over the cliff.")),
    ("Shot 3", KEY_QWEN + " A view from above onto the timber terrace and the glass-sided pool, the larch volume in "
     "front.",
     ("Photorealistic cinematic shot: a slow descent towards the terrace and the pool. Smooth steady camera, no "
     "people. Sound: wind over the cliff, water lapping.")),
)
STEPS = ("1. One camera per shot, bound to markers",
         "   Shot 1, Shot 2, Shot 3 (Timeline:",
         "   select the camera, Ctrl+B, rename F2)",
         "2. Look: materials, season and light,",
         "   the same for every shot",
         "3. Each shot: what its first frame shows,",
         "   then its camera move and sound",
         "4. Run Graph: shot 1's keyframe, the",
         "   Material Board, shots 2-3 keyframes,",
         "   then the videos (3 model loads)",
         "5. Film: Assemble, then Render Film...",
         "   (a Soundtrack: e.g. Stable Audio)",
         "6. More shots: copy a shot's row, set its",
         "   Shot, link its video to the next input")


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
for wf in (FLUX, QWEN, LTX):
    request_specs(wf)
if not pump_until(lambda: all(w in SESSION.specs for w in (FLUX, QWEN, LTX)), 60):
    sys.exit("Parameters did not load")


def key_of(wf, kind, word=""):
    return next(s.key for s in SESSION.specs[wf] if s.kind == kind and word in s.label.lower())


def sock(node, key):
    return next(s for s in node.inputs if s.identifier == key)


def node(tree, idn, label, x, y, **props):
    n = tree.nodes.new(idn)
    n.label, n.location = label, (x, y)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def wf_node(tree, wf_id, label, x, y):
    n = node(tree, WORKFLOW_NODE, label, x, y)
    n.workflow_id = wf_id
    n.build(SESSION.specs[wf_id])
    return n


def seed(n, wf_id, value=7):
    it = n.item(key_of(wf_id, "seed"))
    it.i, it.randomize = value, False


tree = bpy.data.node_groups.new("Multi-shot", TREE)
look = node(tree, "ComfyQPromptNode", "Look (every shot)", -2300, 400, text=LOOK)
film = node(tree, "ComfyQFilmNode", "Film", 900, -700)
board = None
for i, (shot, key_line, move_line) in enumerate(SHOTS):
    y = -i * 1100
    first = node(tree, "ComfyQSceneRenderNode", f"{shot} first frame", -1800, y, style="MATERIAL", frame="START",
                 shot=shot)
    kline = node(tree, "ComfyQPromptNode", f"{shot} keyframe", -1800, y - 380, text=key_line)
    kjoin = node(tree, "ComfyQJoinTextNode", f"{shot} keyframe + look", -1400, y - 380)
    tree.links.new(kline.outputs[0], kjoin.inputs[0])
    tree.links.new(look.outputs[0], kjoin.inputs[1])
    if i == 0:
        key = wf_node(tree, FLUX, f"{shot} keyframe (FLUX.3, the hero)", -1000, y)
        tree.links.new(first.outputs[0], sock(key, key_of(FLUX, "image")))
        tree.links.new(kjoin.outputs[0], sock(key, key_of(FLUX, "prompt")))
        seed(key, FLUX)
        board = node(tree, "ComfyQBoardNode", "Material Board (the hero's materials)", -600, y - 520)
        tree.links.new(key.outputs[0], board.inputs[0])
        hero_view = node(tree, "ComfyQViewerNode", "Hero keyframe", -600, y + 360, auto=True)
        tree.links.new(key.outputs[0], hero_view.inputs[0])
    else:
        key = wf_node(tree, QWEN, f"{shot} keyframe (Qwen + board)", -1000, y)
        tree.links.new(first.outputs[0], sock(key, "loadimage_image_470"))
        tree.links.new(board.outputs[0], sock(key, "loadimage_image_475"))
        tree.links.new(kjoin.outputs[0], sock(key, key_of(QWEN, "prompt")))
        seed(key, QWEN)
        key.item(key_of(QWEN, "int", "steps")).i = 40
    anim = node(tree, "ComfyQSceneAnimationNode", f"{shot} clay", -1000, y - 600, style="CLAY", shot=shot)
    vline = node(tree, "ComfyQPromptNode", f"{shot} move + sound", -1000, y - 900, text=move_line)
    vjoin = node(tree, "ComfyQJoinTextNode", f"{shot} move + look", -600, y - 900)
    tree.links.new(vline.outputs[0], vjoin.inputs[0])
    tree.links.new(look.outputs[0], vjoin.inputs[1])
    video = wf_node(tree, LTX, f"{shot} video (LTX canny)", 200, y - 200)
    tree.links.new(anim.outputs[0], sock(video, key_of(LTX, "video")))
    tree.links.new(key.outputs[0], sock(video, key_of(LTX, "image")))
    tree.links.new(vjoin.outputs[0], sock(video, key_of(LTX, "prompt")))
    for s in SESSION.specs[LTX]:
        it = video.item(s.key)
        label = s.label.lower()
        if s.kind == "seed":
            it.i, it.randomize = 7, False
        elif "length" in label:
            it.f = 10.0                              # the whole shot, up to LTX's 10 s
        elif s.kind == "bool" and "start image" in label:
            it.b = True
        elif s.kind == "bool" and "enhance" in label:
            it.b = False
    tree.links.new(video.outputs[0], film.inputs[f"Shot {i + 1}"])
note = preset_ops.steps_note(tree, STEPS, f"How to use · {LABEL}")
note.location = (-2300 - note.width - 60.0, 400.0)
bad = sum(not lk.is_valid for lk in tree.links)
data = preset_ops.serialize(
    tree, LABEL,
    "A film of several shots with one look. Each shot is a timeline marker bound to a camera (Shot 1, 2, 3). Shot 1's "
    "first frame becomes the hero keyframe (FLUX.3 Klein); a Material Board cut from it (swatches, no layout) gives "
    "shots 2 and 3 the same materials through Qwen 2.1 edit, while each keeps its own composition. LTX 2.5 canny films "
    "every shot's clay animation from its keyframe (seed 7), and the Film node puts the clips end to end in the ComfyQ "
    "Film scene (Render Film writes the MP4). One Look prompt is shared by every shot. Tested on the cliff house "
    "(2026-10-22, docs/multi-shot.md).")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"PRESET {OUT.name}: {len(tree.nodes)} nodes, {len(tree.links)} links ({bad} invalid)", flush=True)
SESSION.close()
