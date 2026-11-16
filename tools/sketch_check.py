# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU check of the Sketch presets (audit 2, phase 4): a drawing made by script in the camera view (as pixel
coordinates of the camera frame), then the class preset run as a graph, headless (preset_run_headless.py hosts this
module: the drawing is made at import, on the main thread; main() drives the MCP tools from a worker thread).

    blender -b <scene.blend> --online-mode --python tools/preset_run_headless.py -- <out> --module=sketch_check
        --sketch=over|only
"""
import itertools
import json
import shutil
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.passes import render, sketch

tool = None                      # set by preset_run_headless
OUT = Path(".")
ASSETS = RESULTS = None
MODE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--sketch=")), "over")

# what is drawn (pixels of a 1280×720 camera frame) and what the preset is asked
DRAWINGS = {
    "over": {      # over the cliff house: a hot-air balloon in the empty sky, a person on the upper roof
        "preset": "Sketch → photoreal (FLUX.3 Klein edit)",
        "strokes": [
            ("circle", (230, 135), 70), ("rect", (215, 250), (245, 272)),
            ("line", [(176, 175), (217, 250)]), ("line", [(284, 175), (243, 250)]),
            ("circle", (780, 250), 9), ("line", [(780, 259), (780, 290)]),
            ("line", [(780, 290), (771, 312)]), ("line", [(780, 290), (789, 312)]),
            ("line", [(766, 272), (794, 272)]),
        ],
        "prompt": ("Turn <ref_image_0> into a photograph of a modern house on a granite cliff: a larch-clad upper "
                   "volume on slender steel struts, board-formed concrete walls, pine trees, overcast winter light. "
                   "The black ink circle with a basket in the sky becomes a real red and white hot-air balloon with a "
                   "wicker basket, and the small black ink figure on the roof becomes a person standing there. Keep "
                   "the composition, the camera and the building exactly. Photorealistic."),
    },
    "only": {      # alone on white: a cabin under a tree
        "preset": "Sketch Only → picture (Qwen canny)",
        "strokes": [
            ("rect", (440, 330), (840, 600)), ("line", [(400, 345), (640, 170), (880, 345)]),
            ("rect", (600, 460), (680, 600)), ("rect", (480, 390), (560, 450)), ("rect", (720, 390), (800, 450)),
            ("line", [(250, 600), (1030, 600)]), ("circle", (1010, 370), 95), ("line", [(1010, 465), (1010, 600)]),
            ("line", [(745, 262), (745, 205), (790, 205), (790, 295)]),
        ],
        "prompt": ("A small timber cabin with a steep pitched roof, a stone chimney, two warmly lit windows and a red "
                   "front door, a large oak tree beside it, a meadow at golden hour, photorealistic photograph"),
    },
}
# candidates on the same drawing (2026-11-16: Qwen canny at full strength traced the lines literally)
CABIN = DRAWINGS["only"]["prompt"]
DRAWINGS["only_canny06"] = {"drawing": "only", "preset": "Sketch Only → picture (Qwen canny)", "prompt": CABIN,
                            "set": {"Workflow": {"controlnet_strength_903": 0.6}}}
DRAWINGS["only_flux"] = {"drawing": "only", "preset": "Sketch → photoreal (FLUX.3 Klein edit)",
                         "set": {"Sketch": {"only": True}},
                         "prompt": ("Turn the pencil drawing <ref_image_0> into a photograph with the same layout: "
                                    + CABIN + ". No drawn lines remain.")}


def _poly(points, closed=False):
    pts = list(points)
    return pts + [pts[0]] if closed else pts


def _draw(scene, spec) -> None:
    """Strokes from pixel coordinates of the camera frame to the sketch's plane (x right, y up)."""
    import math
    ob = sketch.ensure(scene)
    pw = sketch.plane_width(scene)
    W, H = 1280, 720
    d = ob.data.layers["Lines"].frames[0].drawing

    def to_plane(u, v):
        return ((u / W - 0.5) * pw, (0.5 - v / H) * pw * H / W, 0.0)
    strokes = []
    for kind, *a in spec:
        if kind == "line":
            strokes.append(_densify(a[0]))
        elif kind == "rect":
            (x0, y0), (x1, y1) = a
            strokes.append(_densify(_poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True)))
        elif kind == "circle":
            (cx, cy), r = a
            strokes.append([(cx + r * math.cos(2 * math.pi * i / 48), cy + r * math.sin(2 * math.pi * i / 48))
                            for i in range(49)])
    first = len(d.strokes)
    d.add_strokes([len(s) for s in strokes])
    for k, pts in enumerate(strokes):
        for p, (u, v) in zip(d.strokes[first + k].points, pts, strict=True):
            p.position, p.radius, p.opacity = to_plane(u, v), 0.002 * pw, 1.0


def _densify(pts, step=12.0):
    out = [pts[0]]
    for (x0, y0), (x1, y1) in itertools.pairwise(pts):
        n = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / step))
        out += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(1, n + 1)]
    return out


SPEC = DRAWINGS[MODE]
DRAWING = SPEC.get("drawing", MODE)
_scene = bpy.context.scene
sketch.remove(_scene)
_draw(_scene, DRAWINGS[DRAWING]["strokes"])
SENT = render.sketch_pass(_scene, DRAWING == "only", 1280, _scene.frame_start)    # what the Sketch node will send


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    INPUT = OUT / f"{MODE}_input.png"
    shutil.copyfile(SENT, INPUT)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 600:
        ok, info = tool("blender_scene_info")
        if ok and info.get("comfyq", {}).get("connected"):
            break
        time.sleep(5)
    time.sleep(5)
    for _ in range(3):
        ok, added = tool("blender_graph_add_preset", preset=SPEC["preset"])
        if ok:
            break
        log("add preset:", added)
        time.sleep(20)
    names = list(added["nodes"])
    ok, g = tool("blender_graph_status")
    prompt = next(n for n in names if n.startswith("Prompt"))
    tool("blender_graph_set", node=prompt, key="text", value=SPEC["prompt"])
    wf = next(n for n in g["nodes"] if n["node"] in names and n.get("workflow_id"))
    for label, kv in SPEC.get("set", {}).items():
        node = wf["node"] if label == "Workflow" else next(n for n in names if n.startswith(label))
        for k, v in kv.items():
            log("set", node, k, v, tool("blender_graph_set", node=node, key=k, value=v)[0])
    ok, msg = tool("blender_graph_run", node=wf["node"], force=True)     # not the file's own graph
    log("run:", msg)
    t1, rec = time.monotonic(), {"mode": MODE, "preset": SPEC["preset"], "input": str(INPUT)}
    while time.monotonic() - t1 < 1800:
        time.sleep(5)
        ok, st = tool("blender_graph_status")
        if not ok or st.get("running"):
            continue
        me = next(n for n in st["nodes"] if n["node"] == wf["node"])
        if me.get("result") or me.get("error"):
            rec.update(ok=bool(me.get("result")), result=me.get("result"), error=me.get("error"),
                       seconds=round(time.monotonic() - t1), job=me.get("job"))
            if me.get("result"):
                shutil.copyfile(me["result"], OUT / f"{MODE}_result{Path(me['result']).suffix}")
            break
    log("RESULT", json.dumps(rec, ensure_ascii=False))
    (OUT / f"{MODE}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
