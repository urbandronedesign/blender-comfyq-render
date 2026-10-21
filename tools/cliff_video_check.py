# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU driver: blockout clay animation → photoreal video, the first frame made by FLUX.3 Klein (image edit).
Runs the plugin's own graph headless in the sandbox profile (Solo on), on tools/make_cliff_house_scene.py's scene.

    blender -b <scene.blend> --online-mode --python tools/cliff_video_check.py -- <out> keyframe
    blender -b <scene.blend> --online-mode --python tools/cliff_video_check.py -- <out> video <keyframe.png> [canny,depth,3dreal]

keyframe: frame 1 as a CLAY and a MATERIAL Workbench render → FLUX.3 Klein edit (same prompt, seed 7): which input reads
          better as a first frame. video: Scene Animation (clay) + the chosen keyframe → each candidate (one ComfyQ main
          switch each; the extra lanes are closed first so a restart does not restore them next to a 30 GB model).
Every result goes to <out>/state.json (and stays in the plugin's cache).
"""
import importlib
import json
import sys
import time
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
OUT, STEP = Path(ARGS[0]), ARGS[1]
MOD = "bl_ext.user_default.comfyq_bridge"
KLEIN = "image_edit_flux3_klein_image_edit"
VIDEO = {"canny": "video_edit_ltx2_5_iclora_canny_control", "depth": "video_edit_ltx2_5_iclora_union_control",
         "3dreal": "video_edit_ltx2_3_3dreal_vid2vid"}
SEED = 7
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
OUT.mkdir(parents=True, exist_ok=True)
STATE = OUT / "state.json"
state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}


def log(text, **data):
    print(time.strftime("%H:%M:%S"), "[cliff]", text, json.dumps(data, default=str) if data else "", flush=True)


def save():
    STATE.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")


def ext(name):
    return importlib.import_module(f"{MOD}.{name}")


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


def wf_node(tree, wf_id, x):
    S = ext("runtime.session").SESSION
    n = tree.nodes.new("ComfyQWorkflowNode")
    n.workflow_id, n.location = wf_id, (x, 0)
    n.build(S.specs[wf_id])
    return n


def spec(wf_id, kind, word=""):
    S = ext("runtime.session").SESSION
    return next(s for s in S.specs[wf_id] if s.kind == kind and word in s.label.lower())


def link(tree, src, node, kind, word=""):
    key = spec(node.workflow_id, kind, word).key
    tree.links.new(src.outputs[0], next(i for i in node.inputs if i.identifier == key))


def run(tree, target, what):
    grun = ext("graph.run")
    t0 = time.monotonic()
    ok, msg = grun.start(tree, bpy.context.scene, [target.name], [])
    log(f"{what}: Run Graph", ok=ok, msg=msg)
    seen = {}

    def done():
        for n in tree.nodes:
            if getattr(n, "note", "") and seen.get(n.name) != n.note and "Loading its model" not in n.note:
                seen[n.name] = n.note
                log(f"  {n.name}: {n.note}")
        return tree.name not in grun.RUNS
    if ok:
        pump_until(done, 3600)
    S = ext("runtime.session").SESSION
    job = S.jobs.get(target.job_id)
    res = {"seconds": round(time.monotonic() - t0), "ok": bool(target.result) and Path(target.result).is_file(),
           "result": target.result, "note": target.note, "job": target.job_id,
           "files": [str(f) for f in job.files] if job else [], "error": None if ok else msg}
    log(f"{what}: done", **res)
    return res


def connect():
    addon_utils.enable(MOD, default_set=True)
    S = ext("runtime.session").SESSION
    prefs = bpy.context.preferences.addons[MOD].preferences
    prefs.solo_mode = True                                          # sandbox profile only
    S.connect(prefs.profile, "cliff-test", prefs.url.strip())
    if not pump_until(lambda: S.connected and S.workflows, 60):
        raise SystemExit("cannot connect")
    for w in (KLEIN, *VIDEO.values()):
        S.specs[w] = S.client.params(w)
    return S


def keyframes(S):
    tree = bpy.data.node_groups.new("Keyframes", "ComfyQGraphTree")
    for style in ("CLAY", "MATERIAL"):
        tree.nodes.clear()
        rnd = tree.nodes.new("ComfyQSceneRenderNode")
        rnd.style, rnd.frame, rnd.location = style, "START", (-500, 0)
        k = wf_node(tree, KLEIN, 0)
        link(tree, rnd, k, "image")
        k.item(spec(KLEIN, "prompt").key).s = KEY_PROMPT
        it = k.item(spec(KLEIN, "seed").key)
        it.i, it.randomize = SEED, False
        state.setdefault("keyframe", {})[style] = run(tree, k, f"keyframe from {style}")
        save()


def video(S, key_png: Path, only):
    client, serving = S.client, ext("runtime.serving")
    for wf in [ln["workflowId"] for ln in client.server_lanes().get("lanes") or [] if not ln.get("primary")]:
        client.close_lane(wf)                                       # a main switch would restore them beside 30 GB
        log("closed lane", wf=wf)
    serving.wait_restored(client)
    img = bpy.data.images.load(str(key_png), check_existing=True)
    tree = bpy.data.node_groups.new("Video", "ComfyQGraphTree")
    for name in only:
        wf = VIDEO[name]
        tree.nodes.clear()
        anim = tree.nodes.new("ComfyQSceneAnimationNode")
        anim.style, anim.location = "CLAY", (-500, 200)
        key = tree.nodes.new("ComfyQImageNode")
        key.image, key.location = img, (-500, -200)
        v = wf_node(tree, wf, 0)
        link(tree, anim, v, "video")
        link(tree, key, v, "image")
        for s in S.specs[wf]:
            it = v.item(s.key)
            if it is None:
                continue
            label = s.label.lower()
            if s.kind == "prompt":
                it.s = VIDEO_PROMPT
            elif s.kind == "seed":
                it.i, it.randomize = SEED, False
            elif "length" in label and s.kind in ("int", "float"):
                it.i, it.f = 5, 5.0
            elif s.kind == "bool" and "start image" in label:
                it.b = True
            elif s.kind == "bool" and "enhance" in label:
                it.b = False
        state.setdefault("video", {})[name] = {**run(tree, v, f"video {name}"), "workflow": wf}
        save()


try:
    S = connect()
    state["served_before_" + STEP] = sorted(S.served_ids())
    if STEP == "keyframe":
        keyframes(S)
    else:
        video(S, Path(ARGS[2]), (ARGS[3] if len(ARGS) > 3 else "canny,depth,3dreal").split(","))
    state["served_after_" + STEP] = sorted(S.served_ids())
    save()
    log("DONE")
    S.close()
except SystemExit as e:
    log("STOPPED", reason=str(e))
