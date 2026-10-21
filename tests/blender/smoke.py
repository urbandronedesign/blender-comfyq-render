"""Headless smoke test, run inside Blender by tools/smoke_test.ps1.

  blender -b --factory-startup --python tests/blender/smoke.py -- [--book]

bpy.app.timers do not fire in background mode, so this script pumps the
session itself. Without --book it stops before sending a job (no GPU use).
The detached window and the dialog need a real screen: check those by hand.
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

import addon_utils
import bpy
import numpy as np

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
BOOK = "--book" in ARGS
MODULE = "bl_ext.user_default.comfyq_bridge"
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        failures.append(msg)


def pump_until(pred, timeout, what):
    from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        SESSION.pump()
        if pred():
            return True
        if SESSION.error:
            print("  session error:", SESSION.error)
            return False
        time.sleep(0.2)
    print(f"  timeout waiting for {what}")
    return False


print(f"== host: {bpy.app.version} (version_string {bpy.app.version_string})")
addon_utils.enable(MODULE, default_set=True)
check(MODULE in bpy.context.preferences.addons, "extension enabled")
from bl_ext.user_default.comfyq_bridge import match as scene_match
from bl_ext.user_default.comfyq_bridge.importers.media import import_files
from bl_ext.user_default.comfyq_bridge.ops.workflow import scene_facts
from bl_ext.user_default.comfyq_bridge.passes import animation, render
from bl_ext.user_default.comfyq_bridge.props import param_item, workflow_items
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION, cache_dir

scene = bpy.context.scene
check(hasattr(scene, "comfyq"), "scene properties registered")


def items(wf_id):
    return workflow_items(scene, wf_id)


def wait_params(wf_id, what):
    return pump_until(lambda: wf_id in SESSION.specs
                      and {i.key for i in items(wf_id)} == {s.key for s in SESSION.specs[wf_id]}, 15, what)


# -- passes (no network) --------------------------------------------------------------
before = (scene.render.engine, scene.render.resolution_x, scene.render.resolution_y)
p = render.structure_pass(scene, "CLAY", 1280)
img = bpy.data.images.load(str(p))
check(tuple(img.size) == render.fit_size(render.scene_aspect(scene), 1280), f"structure pass size {tuple(img.size)}")
check((scene.render.engine, scene.render.resolution_x, scene.render.resolution_y) == before,
      "render settings restored")

# grayscale clay render: even with a strongly coloured world and a video output format
world = scene.world or bpy.data.worlds.new("World")
scene.world = world
world.color = (0.0, 0.2, 0.9)
if hasattr(scene.render.image_settings, "media_type"):
    scene.render.image_settings.media_type = "VIDEO"
    scene.render.image_settings.file_format = "FFMPEG"
g = render.structure_pass(scene, "CLAY")
gi = bpy.data.images.load(str(g))
gp = np.empty(gi.size[0] * gi.size[1] * 4, dtype=np.float32)
gi.pixels.foreach_get(gp)
gp = gp.reshape(-1, 4)
spread = float(np.abs(gp[:, 0] - gp[:, 1]).max() + np.abs(gp[:, 1] - gp[:, 2]).max())
check(spread < 1e-6, f"clay render is grayscale (max channel spread {spread:.2g}, {gi.size[0]}×{gi.size[1]})")
check(tuple(round(c, 3) for c in world.color) == (0.0, 0.2, 0.9), "world colour restored")
if hasattr(scene.render.image_settings, "media_type"):
    check(scene.render.image_settings.file_format == "FFMPEG", "scene's video output format restored")
    scene.render.image_settings.media_type = "IMAGE"
    scene.render.image_settings.file_format = "PNG"

# THE BUG: with a movie strip in the scene's sequencer, Blender renders the strip, not the camera.
# Our passes must always show the camera, and imported results must not land in the 3D scene.
def _px(path):
    im = bpy.data.images.load(str(path))
    a = np.empty(im.size[0] * im.size[1] * 4, dtype=np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return a
clean = _px(render.structure_pass(scene, "CLAY"))
first_mp4 = next(cache_dir().rglob("*.mp4"), None)
if first_mp4 is not None:
    seq = scene.sequence_editor_create()
    seq.strips.new_movie(name="ComfyQ oldresult", filepath=str(first_mp4), channel=1, frame_start=scene.frame_start)
    with_strip = _px(render.structure_pass(scene, "CLAY"))
    check(clean.shape == with_strip.shape and float(np.abs(clean - with_strip).max()) < 1e-6,
          "clay pass renders the camera even with an old result in the sequencer")
    check(scene.render.use_sequencer, "the scene's own sequencer setting is restored")
    from bl_ext.user_default.comfyq_bridge.ops.results import scene_has_result_strips
    check(scene_has_result_strips(scene), "the bar can warn about result strips in the scene")
    bpy.ops.comfyq.cleanup()
    check(not scene_has_result_strips(scene), "Clean up removes them")
else:
    print("  skip: no cached mp4 to use as a fake result strip")
cube = bpy.data.objects.get("Cube")
bpy.ops.object.select_all(action="DESELECT")
cube.select_set(True)
m = render.mask_pass(scene, [cube], "CLAY")
mi = bpy.data.images.load(str(m))
px = np.empty(mi.size[0] * mi.size[1] * 4, dtype=np.float32)
mi.pixels.foreach_get(px)
hole = float((px.reshape(-1, 4)[:, 3] < 0.5).mean())
check(0.02 < hole < 0.6, f"mask pass: {hole:.1%} of pixels transparent (the cube)")
check(tuple(cube.color) == (1.0, 1.0, 1.0, 1.0), "object color restored")

# -- importers (no network) -------------------------------------------------------------
tmp = cache_dir() / "smoke"
tmp.mkdir(parents=True, exist_ok=True)
# The GLB is a committed fixture (no glTF export at test time; the bridge only imports).
glb = Path(__file__).resolve().parents[1] / "fixtures" / "cube.glb"
ply, wav = tmp / "cube.ply", tmp / "tone.wav"
bpy.ops.wm.ply_export(filepath=str(ply), export_selected_objects=True)
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(22050)
    w.writeframes(b"".join(struct.pack("<h", (i % 100) * 200) for i in range(22050)))
msgs = import_files([glb, ply, wav], "smoketest-0000", "smoke_workflow", scene)
print("  imported:", msgs)
coll = bpy.data.collections.get("ComfyQ smoketes")
check(coll is not None and len(coll.objects) == 2, "GLB + PLY imported into the job collection")
check(all(o.get("comfyq_job") == "smoketest-0000" for o in (coll.objects if coll else [])), "imports are tagged")
results_sc = bpy.data.scenes.get("ComfyQ Results")
check(results_sc is not None and any(s.type == "SOUND" for s in results_sc.sequence_editor.strips),
      "audio imported into the separate ComfyQ Results scene")
check(scene.sequence_editor is None or not len(scene.sequence_editor.strips), "the 3D scene's sequencer stays empty")
# placement: generators put models at the origin (inside the scene); by default they go beside it, on the ground
from bl_ext.user_default.comfyq_bridge.importers import media as _media

bpy.context.view_layer.update()
_glb_objs = [o for o in coll.objects if o.type == "MESH" and o.get("comfyq_job")] if coll else []
_rest = [o for o in scene.objects if o.type == "MESH" and o not in _glb_objs and o.get("comfyq_job") is None]
_b, _r = _media._bbox(_glb_objs[:1]), _media._bbox(_rest)
check(_b is not None and _r is not None and _b[0].x > _r[1].x and abs(_b[0].z) < 1e-4,
      f"an imported model is placed beside the scene, on the ground (x {_b and round(_b[0].x, 2)} > "
      f"{_r and round(_r[1].x, 2)}, z {_b and round(_b[0].z, 4)})")
scene.cursor.location = (0.0, -6.0, 1.0)
before_objs = set(bpy.data.objects)
import_files([glb], "smoketest-0001", "smoke_workflow", scene, _media.Placement("CURSOR", True, 4.5))
bpy.context.view_layer.update()
_new = [o for o in bpy.data.objects if o not in before_objs and o.type == "MESH"]
_nb = _media._bbox(_new)
check(_nb is not None and abs(max(_nb[1] - _nb[0]) - 4.5) < 1e-3 and abs(_nb[0].z - 1.0) < 1e-3
      and abs((_nb[0].y + _nb[1].y) / 2 + 6.0) < 1e-3,
      f"Place at the 3D cursor, size 4.5: longest side {_nb and round(max(_nb[1] - _nb[0]), 3)}, on the cursor's height")
for _o in [o for o in bpy.data.objects if o not in before_objs]:
    bpy.data.objects.remove(_o)
scene.cursor.location = (0.0, 0.0, 0.0)

# -- connect + workflow --------------------------------------------------------------
check(bpy.app.online_access, "online access enabled (--online-mode)")
SESSION.connect("", "smoke-test")
ok = pump_until(lambda: SESSION.connected and SESSION.workflows, 20, "connection")
check(ok, f"connected; {len(SESSION.workflows)} workflows")
served = sorted(SESSION.served_ids())
check(bool(served), f"served: {served}")
if not served:
    sys.exit(1)
# a served workflow with a prompt (solo mode / sweeps leave whatever was last loaded, e.g. a 3D one)
wf = next((w for w in served if any(s.kind == "prompt" for s in SESSION.client.params(w))), served[0])
bpy.ops.comfyq.set_workflow(workflow_id=wf)
check(wait_params(wf, "params"), f"{wf}: {len(items(wf))} params synced to the scene")

# -- animation → video-to-video (no GPU) -------------------------------------------------
scene.frame_start, scene.frame_end, scene.render.fps = 1, 48, 24       # 2 s
cube.location = (0, 0, 0)
cube.keyframe_insert("location", frame=1)
cube.location = (3, 0, 0)
cube.keyframe_insert("location", frame=48)
t0 = time.monotonic()
a = animation.animation_pass(scene, "CLAY", 1280)
check(a.path.suffix == ".mp4" and a.path.stat().st_size > 1000,
      f"animation pass {a.path.name}: {a.frames} frames, {a.size}, {time.monotonic() - t0:.1f}s to render")
check(a.frames == 48 and abs(a.seconds - 2.0) < 1e-6, "animation pass covers the frame range")
check(animation.animation_pass(scene, "CLAY", 1280).path == a.path, "unchanged scene reuses the pass")
import aud

try:
    _snd = aud.Sound(str(a.path))
    _has_audio = _snd.length > 0
except Exception as _e:  # noqa: BLE001
    _has_audio = False
check(_has_audio, "the clay animation has a sound track (workflows that keep the source audio need one)")
scene.frame_end = 72
check(animation.current(scene, "CLAY", 1280) is None, "changing the frame range invalidates it")
scene.frame_end = 48

v2v = "video_edit_ltx2_5_iclora_union_control"
bpy.ops.comfyq.set_workflow(workflow_id=v2v)
check(wait_params(v2v, "v2v params"), f"{v2v} params synced")
vid = next(i for i in items(v2v) if i.kind == "video")
check(vid.source == "ANIMATION", f"video input defaults to the scene animation ({vid.source})")
check(scene.comfyq.tab == "VIDEO", "tab follows the workflow category")
specs = SESSION.specs[v2v]
clip = next(s for s in specs if s.label.lower().startswith("clip length"))
clip_value = param_item(scene, clip.key, v2v).value()
check(abs(clip_value - 2) < 1e-6, f"clip length matched to the 2 s scene ({clip_value})")
start = next(s for s in specs if "start image" in s.label.lower())
check(scene_match.frame_for(start, scene_facts(scene)) == 1, "start image renders frame_start")
name = SESSION.client.upload(a.path)
check(name.startswith("comfyq_session__"), f"ComfyQ accepts the animation pass upload ({name[:40]}…)")
check(bool(import_files([a.path], "smoketest-vid0", v2v, scene))
      and any(s.type == "MOVIE" for s in bpy.data.scenes["ComfyQ Results"].sequence_editor.strips)
      and (scene.sequence_editor is None or not len(scene.sequence_editor.strips)),
      "video imported into ComfyQ Results, not into the 3D scene")

# -- AI keyframe step: its own model and settings, kept per workflow -------------------------
kf = "image_qwen_image_2_1_control_depth"
start_it = param_item(scene, start.key, v2v)
start_it.source = "KEYFRAME"
bpy.ops.comfyq.set_workflow(workflow_id=kf, role="keyframe")
check(wait_params(kf, "keyframe params"), f"keyframe model {kf}: {len(items(kf))} params")
check(scene.comfyq.workflow_id == v2v, "choosing the keyframe model keeps the main workflow")
strength_key = next(i.key for i in items(kf) if i.kind == "float")
param_item(scene, strength_key, kf).f = 0.42
# Never keep item references across collection changes: Blender reallocates them (stale pointer → crash).
bpy.ops.comfyq.set_workflow(workflow_id=wf)
wait_params(wf, "served params")
bpy.ops.comfyq.set_workflow(workflow_id=v2v)
check(param_item(scene, start.key, v2v).source == "KEYFRAME"
      and abs(param_item(scene, strength_key, kf).f - 0.42) < 1e-5,
      "switching workflows keeps each workflow's settings")

# -- multi-line prompt -------------------------------------------------------------------
prompt = next(i for i in items(v2v) if i.kind == "prompt")
bpy.ops.comfyq.edit_prompt(key=prompt.key, workflow_id=v2v)   # no window in background: links the text only
check(prompt.text is not None, "prompt linked to a Text block")
prompt.text.clear()
prompt.text.write("line one\nline two")
check(prompt.value() == "line one\nline two", "multi-line prompt text is what gets sent")

# -- reattach a finished job from the server ----------------------------------------------
done_jobs = SESSION.client.http.get("/jobs", status="completed", limit=200)["jobs"]
pick = next((j for j in done_jobs if any(o.get("kind") == "image" for o in j.get("outputs") or [])), None)
if pick:
    rec = scene.comfyq.jobs.add()
    rec.job_id, rec.workflow_id, rec.label = pick["id"], pick["workflow_id"], "reattach test"

    def imported():
        return next(r for r in scene.comfyq.jobs if r.job_id == pick["id"]).imported

    SESSION.reattach([(pick["id"], pick["workflow_id"])])
    view_ok = pump_until(lambda: (v := SESSION.jobs.get(pick["id"])) is not None and v.files and imported(),
                         60, "reattach")
    check(view_ok, f"reattached finished job {pick['id'][:8]}: downloaded and imported once")
    files_before = list(SESSION.jobs[pick["id"]].files)
    SESSION.jobs.pop(pick["id"])
    SESSION.reattach([(pick["id"], pick["workflow_id"])])
    pump_until(lambda: SESSION.jobs[pick["id"]].files, 30, "reattach from cache")
    check(SESSION.jobs[pick["id"]].files == files_before, "second reattach reuses the cached files")

    # job card buttons
    out_copy = tmp / "saved_copy"
    src = files_before[0]
    res = bpy.ops.comfyq.save_result("EXEC_DEFAULT", source=str(src), filepath=str(out_copy / src.name))
    check(res == {"FINISHED"} and (out_copy / src.name).stat().st_size == src.stat().st_size,
          "Save copy writes the result where asked")
    n_imgs = len(bpy.data.images)
    res = bpy.ops.comfyq.import_file("EXEC_DEFAULT", job_id=pick["id"], path=str(src))
    check(res == {"FINISHED"}, f"Import file ran ({len(bpy.data.images) - n_imgs} new image datablock(s))")
    res = bpy.ops.comfyq.download(job_id=pick["id"])
    check(res == {"FINISHED"} and pump_until(lambda: SESSION.jobs[pick["id"]].files, 30, "download again"),
          "Download fetches the result again")

bpy.ops.comfyq.set_workflow(workflow_id=wf)
check(wait_params(wf, "served params") and scene.comfyq.workflow_id == wf, f"back on the served workflow {wf}")

# -- node graph (N0): nodes built from the served workflow, link rules, rebuild, save/reload ----------
from bl_ext.user_default.comfyq_bridge.graph import kinds as gkinds
from bl_ext.user_default.comfyq_bridge.graph import sync_all
from bl_ext.user_default.comfyq_bridge.graph.ui import ensure_graph

tree = ensure_graph()
wnode = tree.nodes.new("ComfyQWorkflowNode")
wnode.workflow_id = v2v
wnode.build(SESSION.specs[v2v])
want = [s.key for s in SESSION.specs[v2v] if gkinds.input_kind(s.kind)]
check([s.identifier for s in wnode.inputs] == want,
      f"workflow node: one socket per input, in order ({len(want)}: {', '.join(want)})")
check([s.comfyq_kind for s in wnode.outputs] == ["video"], "V2V node outputs a video")
chips = [s.key for s in SESSION.specs[v2v] if not gkinds.input_kind(s.kind)]
check({it.key for it in wnode.params} >= set(chips), f"settings stored on the node as chips ({len(chips)})")
anim = tree.nodes.new("ComfyQSceneAnimationNode")
img_node = tree.nodes.new("ComfyQImageNode")
vid_sock = next(s for s in wnode.inputs if s.comfyq_kind == "video")
good = tree.links.new(anim.outputs[0], vid_sock)
tree.update()
check(good.is_valid, "Scene Animation → video input is a valid link")
img_sock = next((s for s in wnode.inputs if s.comfyq_kind == "image"), None)
if img_sock is not None:
    tree.links.new(img_node.outputs[0], img_sock)
bad = tree.links.new(img_node.outputs[0], vid_sock)       # replaces the animation link: wrong kind
tree.update()
check(not bad.is_valid, "Image → video input is refused (drawn red)")
tree.links.remove(bad)
tree.links.new(anim.outputs[0], vid_sock)
n_links = len(tree.links)
vid_key = vid_sock.identifier                     # never keep socket refs across socket changes
wnode.signature = "stale"                                  # as if ComfyQ changed the workflow
victim = next((s for s in wnode.inputs if s.identifier != vid_key), None)
if victim is not None:
    wnode.inputs.remove(victim)
sync_all()
check([s.identifier for s in wnode.inputs] == want and wnode.signature == gkinds.spec_signature(SESSION.specs[v2v]),
      "a changed workflow rebuilds the node's sockets")
check(any(link.to_socket.identifier == vid_key for link in tree.links),
      f"links on inputs that still exist are kept ({n_links} → {len(tree.links)})")
# N1: every ComfyQ workflow becomes a node with the right sockets and output
from bl_ext.user_default.comfyq_bridge.graph.tree import socket_kind

bad_nodes = []
for w in SESSION.workflows:
    if w.id not in SESSION.specs:
        SESSION.specs[w.id] = SESSION.client.params(w.id)
    n = tree.nodes.new("ComfyQWorkflowNode")
    n.workflow_id = w.id
    n.build(SESSION.specs[w.id])
    want_kinds = [gkinds.input_kind(s.kind) for s in SESSION.specs[w.id] if gkinds.input_kind(s.kind)]
    if [socket_kind(x) for x in n.inputs] != want_kinds or len(n.outputs) != 1 or not socket_kind(n.outputs[0]):
        bad_nodes.append(w.id)
    tree.nodes.remove(n)
check(not bad_nodes, f"all {len(SESSION.workflows)} workflows become nodes with the right sockets {bad_nodes}")

# N1: change model keeps links on inputs of the same kind, and each model's settings
other = next(w.id for w in SESSION.workflows if w.id != v2v and w.id in SESSION.specs
             and any(s.kind == "video" for s in SESSION.specs[w.id]))
float_key = next(s.key for s in SESSION.specs[v2v] if s.kind == "float")
wnode.item(float_key).f = 0.37
bpy.ops.comfyq.graph_set_workflow(tree=tree.name, node=wnode.name, workflow=other)
check(wnode.workflow_id == other and any(lk.to_node == wnode and socket_kind(lk.to_socket) == "video"
                                         for lk in tree.links),
      f"Change model ({other}): the clay animation stays linked to the video input")
bpy.ops.comfyq.graph_set_workflow(tree=tree.name, node=wnode.name, workflow=v2v)
check(abs(wnode.item(float_key).f - 0.37) < 1e-5, "Change model back: the node kept that model's settings")

# N1: "+" on an unlinked input adds the matching source node, linked
free = next((x for x in wnode.inputs if not x.is_linked), None)
if free is not None:
    free_key, free_kind = free.identifier, socket_kind(free)
    n_nodes = len(tree.nodes)
    res = bpy.ops.comfyq.graph_add_source(tree=tree.name, node=wnode.name, key=free_key)
    sock = next(x for x in wnode.inputs if x.identifier == free_key)
    check(res == {"FINISHED"} and len(tree.nodes) == n_nodes + 1 and sock.is_linked
          and socket_kind(sock.links[0].from_socket) in gkinds.ACCEPTS[free_kind],
          f"+ on “{free.name}” adds a {sock.links[0].from_node.bl_label if sock.is_linked else '?'} node, linked")

# N1: multi-line prompts in nodes
pnode = tree.nodes.new("ComfyQPromptNode")
pnode.text = "a red car"
bpy.ops.comfyq.graph_edit_text(tree=tree.name, node=pnode.name, key="")
pnode.text_block.clear()
pnode.text_block.write("a red car\nat night")
check(pnode.value() == "a red car\nat night", "Prompt node: multi-line text is its value")
bpy.ops.comfyq.graph_unlink_text(tree=tree.name, node=pnode.name, key="")
check(pnode.text_block is None and pnode.text == "a red car at night", "Prompt node: back to one line")
tree.nodes.remove(pnode)
for extra in [n for n in tree.nodes if n not in (wnode, anim, img_node)]:
    tree.nodes.remove(extra)

# N2 (no GPU): what a run books, what it reuses, what it sends, what it refuses
from bl_ext.user_default.comfyq_bridge.graph import engine as gengine
from bl_ext.user_default.comfyq_bridge.graph import run as grun

for x in list(tree.links):
    if x.to_node == wnode and socket_kind(x.to_socket) != "video":
        tree.links.remove(x)
nodes = grun.describe(tree, scene)
check(gengine.plan(nodes, [wnode.name]) == [wnode.name], "graph run: Generate on a node books that node")
r = grun.Run(tree, scene, [wnode.name], nodes, [])
vals, ups = r._collect(scene, wnode, SESSION.specs[v2v])
vid_key2 = next(x.identifier for x in wnode.inputs if socket_kind(x) == "video")
check(vid_key2 in ups and ups[vid_key2].suffix == ".mp4" and ups[vid_key2].is_file(),
      f"graph run: the linked Scene Animation is rendered and sent ({ups.get(vid_key2)})")
prompt_key = next(s.key for s in SESSION.specs[v2v] if s.kind == "prompt")
check(vals.get(prompt_key) == wnode.item(prompt_key).value(), "graph run: the node's prompt is sent")
# B fed by A's result: A is reused while up to date, re-run once its settings change
b_node = tree.nodes.new("ComfyQWorkflowNode")
b_node.workflow_id = v2v
b_node.build(SESSION.specs[v2v])
tree.links.new(wnode.outputs[0], next(x for x in b_node.inputs if socket_kind(x) == "video"))
nodes = grun.describe(tree, scene)
check(gengine.plan(nodes, [b_node.name]) == [wnode.name, b_node.name], "graph run: a chain books upstream first")
wnode.result = str(ups[vid_key2])                          # pretend A has made a result
wnode.result_hash = gengine.node_hash(grun.describe(tree, scene), wnode.name)
check(gengine.plan(grun.describe(tree, scene), [b_node.name]) == [b_node.name],
      "graph run: an up-to-date upstream result is reused")
r2 = grun.Run(tree, scene, [b_node.name], grun.describe(tree, scene), [])
_v, ups_b = r2._collect(scene, b_node, SESSION.specs[v2v])
check(next(iter(ups_b.values())) == ups[vid_key2], "graph run: B receives A's result")
# What happens after a real run must not make A's scene input look edited (GUI check found this):
# the node receiving its result, a result image loaded, a depsgraph update.
def _reuses_a():
    return gengine.plan(grun.describe(tree, scene), [b_node.name]) == [b_node.name]


# What happens during and after a real run must not make A's scene input look edited (the live check
# found the UI re-tagging objects): results arriving, images, custom properties, the timeline.
wnode.job_id = "x"
probe = bpy.data.images.new("ComfyQ probe", 8, 8)
cube["ui_tag"] = 1
keep_frame = scene.frame_current
scene.frame_set(scene.frame_start + 10)
bpy.context.view_layer.update()
check(_reuses_a(), "graph run: results arriving, custom properties and scrubbing the timeline do not "
                   "invalidate the scene input")
scene.frame_set(keep_frame)
bpy.data.images.remove(probe)
del cube["ui_tag"]
cam = scene.camera
cam.location.x += 0.5                                      # a real change of what the camera sees
check(not _reuses_a(), "graph run: moving the camera makes the scene-fed node run again")
cam.location.x -= 0.5
check(_reuses_a(), "graph run: moving it back makes the old result valid again")
fc_cube = next(render._fcurves(cube.animation_data.action))
fc_cube.keyframe_points[-1].co.y += 1.0                     # a changed keyframe
check(not _reuses_a(), "graph run: changing a keyframe makes the scene-fed node run again")
fc_cube.keyframe_points[-1].co.y -= 1.0
wnode.result_hash = gengine.node_hash(grun.describe(tree, scene), wnode.name)
wnode.item(prompt_key).s = wnode.item(prompt_key).s + " (changed)"
check(gengine.plan(grun.describe(tree, scene), [b_node.name]) == [wnode.name, b_node.name],
      "graph run: changing A's prompt makes A run again")
unlinked = tree.nodes.new("ComfyQWorkflowNode")
unlinked.workflow_id = wf                                  # the served workflow (v2v above is not served)
unlinked.build(SESSION.specs[wf])
if any(s.is_media and s.required for s in SESSION.specs[wf]):   # (a served t2i has nothing to link)
    ok, msg = grun.start(tree, scene, [unlinked.name], [])
    check(not ok and "Link these inputs" in msg,
          f"graph run: refused when a needed input is not linked ({msg[:60]})")
unserved = next((w.id for w in SESSION.workflows if w.id not in SESSION.served_ids()), None)
if unserved:
    unlinked.change_workflow(unserved)
    ok, msg = grun.start(tree, scene, [unlinked.name], [])
    check(not ok and "Not served" in msg, f"graph run: refused when a workflow is not served ({msg[:60]})")
# solo mode (Preferences): Run Graph loads the model a node needs. ComfyQ's switch is faked here: a smoke
# test never changes what the live server serves
t2i = next((w.id for w in SESSION.workflows if w.category == "t2i" and w.id not in SESSION.served_ids()
            and not w.unavailable), None)
if t2i:
    from bl_ext.user_default.comfyq_bridge.runtime import serving as gserving
    SESSION.specs[t2i] = SESSION.client.params(t2i)
    unlinked.change_workflow(t2i)
    _pp = bpy.context.preferences.addons["bl_ext.user_default.comfyq_bridge"].preferences
    _pp.solo_mode = True
    from bl_ext.user_default.comfyq_bridge.prefs import teacher_unlocked as _unlocked
    _had_admin = os.environ.pop("COMFYQ_ADMIN_PASSWORD", None)
    _had_cfg = os.environ.get("COMFYQ_BRIDGE_CONFIG")
    os.environ["COMFYQ_BRIDGE_CONFIG"] = os.path.join(bpy.app.tempdir or ".", "no_profiles.toml")  # a student PC
    check(not _unlocked(_pp) and not grun.solo_on(),
          "solo: locked without the admin password in the profile (a student cannot switch models)")
    os.environ["COMFYQ_ADMIN_PASSWORD"] = "smoke"          # the teacher's PC (this process only)
    check(_unlocked(_pp) and grun.solo_on(), "solo: on with the admin password in the profile")
    real_serve, real_blockers, real_choose, asked = gserving.serve, gserving.blockers, gserving.choose, []
    gserving.choose = lambda client, wf_id, how="auto", force=False: "main"   # (the live GPU may be busy)
    pump_until(lambda: (grun.tick_all(), tree.name not in grun.RUNS)[1], 60, "the previous graph run to end")
    gserving.blockers = lambda client: ["someone · x (running)"]
    ok, msg = grun.start(tree, scene, [unlinked.name], [])
    check(not ok and "nobody is using the server" in msg, f"solo: refused while anyone has a job ({msg[:70]})")
    gserving.blockers = lambda client: []
    gserving.serve = lambda client, wf_id, how, force=False: asked.append((wf_id, how))
    ok, msg = grun.start(tree, scene, [unlinked.name], [])
    run_ = grun.RUNS.get(tree.name)
    pump_until(lambda: asked and run_.switch and run_.switch["state"] == "loading", 20, "solo switch asked")
    check(ok and "solo" in msg and asked == [(t2i, "main")] and unlinked.note.startswith("Loading its model")
          and not run_.booked,
          f"solo: Run Graph asks ComfyQ to serve {t2i} ({asked}) and waits for it")
    grun.SWITCH_TIMEOUT_S, run_.switch["t0"] = 0, 0
    pump_until(lambda: (grun.tick_all(), tree.name not in grun.RUNS)[1], 10, "solo timeout")
    check("Could not load its model" in unlinked.note, f"solo: a model that never comes gives up ({unlinked.note})")
    grun.SWITCH_TIMEOUT_S = 900
    gserving.serve, gserving.blockers, gserving.choose = real_serve, real_blockers, real_choose
    _pp.solo_mode = False
    os.environ.pop("COMFYQ_ADMIN_PASSWORD", None)
    if _had_cfg is None:
        os.environ.pop("COMFYQ_BRIDGE_CONFIG", None)
    else:
        os.environ["COMFYQ_BRIDGE_CONFIG"] = _had_cfg
tree.nodes.remove(unlinked)
tree.nodes.remove(b_node)
wnode.result, wnode.result_hash = "", ""
check(not grun.RUNS, "graph run: nothing left running after refusals")

# N3: look — no group panels, video thumbnails, the bar's setup as nodes
from bl_ext.user_default.comfyq_bridge.graph import tree as gtree_mod
from bl_ext.user_default.comfyq_bridge.graph import ui as gui

check(tree.bl_use_group_interface is False, "graph: no Group Sockets / Node Width panels (no group interface)")
gtree_mod.movie_thumbnail(str(ups[vid_key2]))
thumb = gtree_mod._PREVIEWS[0].get(gtree_mod._MOVIES.get(str(ups[vid_key2]), ""))
check(thumb is not None and thumb.image_size[0] > 0,
      f"graph: a video gets a thumbnail ({tuple(thumb.image_size) if thumb else None})")
scene.comfyq.workflow_id = wf
from bl_ext.user_default.comfyq_bridge.ops.workflow import sync_params

sync_params(scene, wf, SESSION.specs[wf])
bar_prompt = next((s for s in SESSION.specs[wf] if s.kind == "prompt"), None)   # (a served 3D workflow has none)
if bar_prompt is not None:
    param_item(scene, bar_prompt.key, wf).s = "bar prompt for the graph"
bar_num = next((s for s in SESSION.specs[wf] if s.kind == "float"), None)
if bar_num is not None:
    param_item(scene, bar_num.key, wf).f = 0.61
tmp_tree = bpy.data.node_groups.new("smoke open as graph", gtree_mod.TREE)
from_bar = gui.node_from_bar(tmp_tree, scene, wf, 0.0, 0.0)
check(from_bar.workflow_id == wf
      and (bar_prompt is None or from_bar.item(bar_prompt.key).s == "bar prompt for the graph")
      and (bar_num is None or abs(from_bar.item(bar_num.key).f - 0.61) < 1e-5),
      "Open as graph: the node has the bar's model, prompt and settings")
fed = {x.identifier: x.links[0].from_node.bl_idname for x in from_bar.inputs if x.is_linked}
want_fed = {s.key for s in SESSION.specs[wf] if s.is_media
            and param_item(scene, s.key, wf).source != "NONE"
            and next(x for x in from_bar.inputs if x.identifier == s.key).enabled}
check(set(fed) >= {k for k in want_fed if param_item(scene, k, wf).source in ("STRUCTURE", "MASK", "ANIMATION")},
      f"Open as graph: each input is fed like in the bar {fed}")
bpy.data.node_groups.remove(tmp_tree)

# N4: output nodes (Viewer / Import / Save), Reroutes, result history

from bl_ext.user_default.comfyq_bridge.graph import outputs as gout

n_before_n4 = set(tree.nodes)
clip = Path(str(ups[vid_key2]))
gtree_mod.remember_result(wnode, "smokejob1", str(clip), "h1")
viewer = tree.nodes.new("ComfyQViewerNode")
imp = tree.nodes.new("ComfyQImportNode")
save = tree.nodes.new("ComfyQSaveNode")
rr = tree.nodes.new("NodeReroute")
tree.links.new(wnode.outputs[0], viewer.inputs[0])
tree.links.new(wnode.outputs[0], imp.inputs[0])
tree.links.new(wnode.outputs[0], rr.inputs[0])
tree.links.new(rr.outputs[0], save.inputs[0])
tree.update()
m = gout.linked(save)
check(m is not None and m.path == str(clip) and m.kind == "video" and all(lk.is_valid for lk in tree.links),
      "output nodes: Save sees the workflow result through a Reroute")
res_scene = lambda: bpy.data.scenes.get("ComfyQ Results")
n_strips = lambda: len(gout._snapshot(scene)["strips"])
s0 = n_strips()
gout.act(imp, scene, force=True)
s1 = n_strips()
gout.act(imp, scene, force=True)
check(s1 == s0 + 1 and n_strips() == s1, f"Import to Scene: imports, and Replace replaces ({s0} → {s1} → {n_strips()})")
imp.replace = False
gout.act(imp, scene, force=True)
check(n_strips() == s1 + 1, "Import to Scene: without Replace a second copy is added")
gout.remove_imported(imp)
save_dir = tmp / "saved_n4"
shutil.rmtree(save_dir, ignore_errors=True)
save.folder = str(save_dir)
gout.act(save, scene, force=True)
gout.act(save, scene, force=True)
saved = sorted(p.name for p in save_dir.iterdir()) if save_dir.is_dir() else []
check(len(saved) == 2 and all(p.endswith(".mp4") for p in saved), f"Save: copies with unique names {saved}")
save.auto, save.handled = True, ""
gout.tick(tree, scene)
gout.tick(tree, scene)
check(len(list(save_dir.iterdir())) == 3, "Save when ready: acts once per new result")
time.sleep(0.05)
clip.touch()
gout.tick(tree, scene)
check(len(list(save_dir.iterdir())) == 4, "Save when ready: a new result (file changed) is saved again")
save.auto = False
# Generate on an output node fed only by a source: acts right away, no job
src_save = tree.nodes.new("ComfyQSaveNode")
src_save.folder = str(save_dir)
tree.links.new(anim.outputs[0], src_save.inputs[0])
res = bpy.ops.comfyq.graph_run(tree=tree.name, node=src_save.name)
check(res == {"FINISHED"} and not grun.RUNS and len(list(save_dir.iterdir())) == 5,
      f"Save fed by Scene Animation: Generate saves the clay video, no job ({src_save.status[:50]})")
# a text: Prompt → Viewer
pr = tree.nodes.new("ComfyQPromptNode")
pr.text = "smoke text"
tree.links.new(pr.outputs[0], viewer.inputs[0])
check(gout.linked(viewer).text == "smoke text", "Viewer: a prompt/text is shown as text")
# workflow input fed through a Reroute
b2 = tree.nodes.new("ComfyQWorkflowNode")
b2.workflow_id = v2v
b2.build(SESSION.specs[v2v])
rr2 = tree.nodes.new("NodeReroute")
tree.links.new(anim.outputs[0], rr2.inputs[0])
tree.links.new(rr2.outputs[0], next(x for x in b2.inputs if socket_kind(x) == "video"))
tree.update()
check(grun.describe(tree, scene)[b2.name].inputs.get(vid_key2) == anim.name,
      "Reroute: a run follows it (the workflow node is fed by the Scene Animation)")
img_rr = tree.nodes.new("ComfyQImageNode")
rr3 = tree.nodes.new("NodeReroute")
tree.links.new(img_rr.outputs[0], rr3.inputs[0])
bad = tree.links.new(rr3.outputs[0], next(x for x in b2.inputs if socket_kind(x) == "video"))
tree.update()
check(not bad.is_valid, "Reroute: an image through a reroute into a video input is still refused")
# history
gtree_mod.remember_result(wnode, "smokejob2", str(ups[vid_key2]).replace(".mp4", "_b.mp4"), "h2")
check(len(wnode.history) == 2, "a workflow node keeps its earlier results")
bpy.ops.comfyq.graph_use_result(tree=tree.name, node=wnode.name, key=str(clip))
check(wnode.result == str(clip) and wnode.result_hash == "h1" and wnode.job_id == "smokejob1",
      "sidebar › Use this result: an earlier result becomes current")
for extra in [n for n in tree.nodes if n not in n_before_n4]:
    tree.nodes.remove(extra)
wnode.result, wnode.result_hash, wnode.job_id = "", "", ""
wnode.history.clear()
shutil.rmtree(save_dir, ignore_errors=True)

# N5: class presets (built in: workflows picked from what is served; saved: exact graphs)
from bl_ext.user_default.comfyq_bridge.graph import preset_ops as gpo
from bl_ext.user_default.comfyq_bridge.graph import presets as gpresets

keep_tree = None
for p in gpresets.BUILTIN:
    for r in [n.role for n in p.nodes if n.role]:
        for wf_id in gpresets.candidates(r, SESSION.workflows):
            if wf_id not in SESSION.specs:
                SESSION.specs[wf_id] = SESSION.client.params(wf_id)
    pt = bpy.data.node_groups.new(f"smoke preset {p.id}", gtree_mod.TREE)
    choice = gpo.choose(p)
    made = gpo.build_builtin(p, pt, scene, choice)
    wnodes = [n for n in made if n.bl_idname == "ComfyQWorkflowNode"]
    videos_fed = all(x.is_linked for n in wnodes for x in n.inputs if x.enabled and socket_kind(x) == "video")
    check(all(choice.values()) and len(wnodes) == sum(1 for n in p.nodes if n.role)
          and all(lk.is_valid for lk in pt.links) and videos_fed
          and all(any(o.is_linked for o in n.outputs) for n in wnodes),
          f"preset “{p.label}”: built from {list(choice.values())} ({len(pt.nodes)} nodes, {len(pt.links)} links)")
    _note = next((n for n in made if n.bl_idname == "NodeFrame" and n.label == "How to use"), None)
    check(_note is not None and _note.text is not None and _note.text.as_string().startswith("1. ")
          and _note.location.x + _note.width < min(n.location.x for n in made if n != _note),
          f"F3: preset “{p.label}” has a How to use note (numbered steps, left of the nodes)")
    if p.id == "keyframe_v2v":
        video = next(n for n in wnodes if any(socket_kind(x) == "video" for x in n.inputs))
        start = next(x for x in video.inputs if socket_kind(x) == "image")
        check(start.enabled and start.is_linked and start.links[0].from_node.bl_idname == "ComfyQWorkflowNode",
              "preset AI keyframe → video: the keyframe feeds the start image (switched on)")
        prompts = [n for n in made if n.bl_idname == "ComfyQPromptNode"]
        check(len(prompts) == 1 and len(prompts[0].outputs[0].links) == 2 and bool(prompts[0].text),
              "preset AI keyframe → video: one prompt feeds both workflows (starts from an example)")
        keep_tree = pt
        continue
    bpy.data.node_groups.remove(pt)
# saved presets: the graph as a file, read back the same; listed from the shared folder
os.environ["COMFYQ_BRIDGE_PRESETS"] = str(tmp / "shared_presets")
shared = gpresets.folders()[1]
shared.mkdir(parents=True, exist_ok=True)
wkey = next(n for n in keep_tree.nodes if n.bl_idname == "ComfyQWorkflowNode")
fkey = next((it for it in wkey.params if it.workflow_id == wkey.workflow_id and it.kind == "float"), None)
if fkey is not None:
    fkey.f = 0.73
frame_n = keep_tree.nodes.new("NodeFrame")
frame_n.label = "Step 1"
data = gpo.serialize(keep_tree, "Smoke class", "for the smoke test")
(shared / "smoke_class.json").write_text(json.dumps(data), encoding="utf-8")
listed = [h["label"] for _f, h in gpo.saved_presets()]
check("Smoke class" in listed, f"saved preset listed from the shared folder {listed}")
back = bpy.data.node_groups.new("smoke preset back", gtree_mod.TREE)
gpo.build_saved(json.loads((shared / "smoke_class.json").read_text(encoding="utf-8")), back)
same_types = sorted(n.bl_idname for n in back.nodes) == sorted(n.bl_idname for n in keep_tree.nodes)
wb = next(n for n in back.nodes if n.bl_idname == "ComfyQWorkflowNode" and n.workflow_id == wkey.workflow_id)
check(same_types and len(back.links) == len(keep_tree.links) and all(lk.is_valid for lk in back.links)
      and (fkey is None or abs(wb.item(fkey.key).f - 0.73) < 1e-5),
      f"saved preset rebuilds the same graph ({len(back.nodes)} nodes, {len(back.links)} links, settings kept)")
_bnote = next((n for n in back.nodes if n.bl_idname == "NodeFrame" and n.label == "How to use"), None)
check(_bnote is not None and _bnote.text is not None and _bnote.text.as_string().startswith("1. "),
      "F3: a saved preset keeps its How to use note (the Frame's text)")
from bl_ext.user_default.comfyq_bridge.ops import workflow as _wfops

check(_wfops.HELP_PAGE.is_file() and "quickstart" in _wfops.HELP_PAGE.read_text(encoding="utf-8").lower(),
      f"F3: the student quickstart ships with the extension ({_wfops.HELP_PAGE.name})")
# the generated library (tools/make_preset_library.py): subfolders = submenus; workflow nodes saved with
# no settings + "match_scene" take the workflow's defaults and the scene's duration / size / fps
lib = json.loads(json.dumps(data))
for d in lib["nodes"]:
    if d["type"] == "ComfyQWorkflowNode":
        d["params"], d["match_scene"] = [], True
(shared / "Video → video").mkdir(exist_ok=True)
(shared / "Video → video" / "lib.json").write_text(json.dumps({**lib, "label": "Smoke lib"}), encoding="utf-8")
groups = {h["label"]: h["group"] for _f, h in gpo.saved_presets()}
check(groups.get("Smoke lib") == "Video → video" and groups.get("Smoke class") == "",
      f"presets in a subfolder are grouped (a submenu): {groups}")
# F4: the Shared Presets Folder preference and a profile's presets_dir are listed too (this sandbox's prefs)
from bl_ext.user_default.comfyq_bridge import prefs as _prefs_mod

_pp = _prefs_mod.prefs()
_pdir, _cdir = tmp / "pref_presets", tmp / "profile_presets"
for _d, _lab in ((_pdir, "Smoke pref preset"), (_cdir, "Smoke profile preset")):
    _d.mkdir(parents=True, exist_ok=True)
    (_d / "p.json").write_text(json.dumps({**data, "label": _lab}), encoding="utf-8")
_cfg0 = os.environ.get("COMFYQ_BRIDGE_CONFIG")
_cfg = tmp / "profiles_presets.toml"
_cfg.write_text(f'default = "c"\n[profiles.c]\npresets_dir = "{_cdir.as_posix()}"\n'
                '[profiles.c.comfyq]\nurl = "http://127.0.0.1:3000"\n', encoding="utf-8")
try:
    _pp.presets_dir = str(_pdir)
    os.environ["COMFYQ_BRIDGE_CONFIG"] = str(_cfg)
    _sv = {h["label"]: h["shared"] for _f, h in gpo.saved_presets()}
    _fo = gpo.preset_folders()
    check(_sv.get("Smoke pref preset") is True and _sv.get("Smoke profile preset") is True
          and _fo[1] == _pdir and _cdir in _fo,
          f"Shared Presets Folder (preference + profile presets_dir) listed as shared: {[str(f) for f in _fo]}")
    _pp.presets_dir = str(_cdir)
    check(gpo.preset_folders().count(_cdir) == 1, "a folder named twice is listed once")
finally:
    _pp.presets_dir = ""
    if _cfg0 is None:
        os.environ.pop("COMFYQ_BRIDGE_CONFIG", None)
    else:
        os.environ["COMFYQ_BRIDGE_CONFIG"] = _cfg0
lib_back = bpy.data.node_groups.new("smoke lib back", gtree_mod.TREE)
gpo.build_saved(lib, lib_back, scene)
wl = next(n for n in lib_back.nodes if n.bl_idname == "ComfyQWorkflowNode" and n.workflow_id == wkey.workflow_id)
from bl_ext.user_default.comfyq_bridge import match as _sm
from bl_ext.user_default.comfyq_bridge.ops.workflow import scene_facts as _facts

_want = {k: v for k, v in _sm.match(SESSION.specs[wl.workflow_id], _facts(scene)).values.items()
         if wl.item(k) is not None and wl.item(k).kind in ("int", "seed", "float")}     # what _match_scene sets
_got = {k: (wl.item(k).i if wl.item(k).kind in ("int", "seed") else wl.item(k).f) for k in _want}
check(len(lib_back.links) == len(keep_tree.links) and all(abs(_got[k] - float(v)) < 1e-3 for k, v in _want.items())
      and (fkey is None or abs(wl.item(fkey.key).f - 0.73) > 1e-5),
      f"library preset: same links, workflow defaults, matched to the scene {_got}")
for t in (keep_tree, back, lib_back):
    bpy.data.node_groups.remove(t)
shutil.rmtree(shared, ignore_errors=True)
del os.environ["COMFYQ_BRIDGE_PRESETS"]

graph_file = tmp / "graph.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(graph_file), copy=True)
bpy.ops.wm.open_mainfile(filepath=str(graph_file))
scene = bpy.context.scene
t2 = bpy.data.node_groups.get("ComfyQ Graph")
check(t2 is not None and len(t2.nodes) == 3 and len(t2.links) >= 1, "the graph is saved with the file")
w2 = next(n for n in t2.nodes if n.bl_idname == "ComfyQWorkflowNode")
check(w2.workflow_id == v2v and len(w2.inputs) == len(want), "workflow node reloads with its sockets")
graph_file.unlink(missing_ok=True)

# P1.4: the MCP server for AI agents, called over HTTP like an agent would (the test pumps the main thread)
import threading
import urllib.error
import urllib.request

from bl_ext.user_default.comfyq_bridge import mcp as bmcp

os.environ["COMFYQ_BRIDGE_CONFIG"] = str(tmp / "agent_cfg" / "profiles.toml")      # not the user's mcp.json
check(bmcp.start(0) and bmcp.info_path().is_file(), f"MCP server starts ({bmcp.STATE['url']})")
_info = json.loads(bmcp.info_path().read_text(encoding="utf-8"))


def mcp_rpc(method, params=None, token=None):
    box = {}

    def work():
        req = urllib.request.Request(_info["url"], method="POST", data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode(), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token or _info['token']}"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                box["v"] = json.loads(r.read())
        except urllib.error.HTTPError as e:
            box["v"] = e.code
    t = threading.Thread(target=work)
    t.start()
    while t.is_alive():
        SESSION.pump()
        time.sleep(0.02)
    return box["v"]


def mcp_tool(name, **args):
    res = mcp_rpc("tools/call", {"name": name, "arguments": args})["result"]
    return res["isError"], json.loads(res["content"][0]["text"])


check(mcp_rpc("ping", token="wrong") == 401, "MCP: a wrong token is refused")
names = [t["name"] for t in mcp_rpc("tools/list")["result"]["tools"]]
check(len(names) == 23 and "blender_segment" in names and "blender_mask_image" in names and "blender_shadow_catcher" in names and "blender_world" in names and "blender_transform" in names and "comfyq_submit" in names and "blender_graph_run" in names, f"MCP: {len(names)} tools")
err, info = mcp_tool("blender_scene_info")
check(not err and info["camera"] and info["frames"] == [scene.frame_start, scene.frame_end],
      f"MCP blender_scene_info: {info.get('scene')} {info.get('frames')} camera {info.get('camera')}")
err, wfs = mcp_tool("comfyq_list_workflows")
check(not err and {w["id"] for w in wfs} == SESSION.served_ids() and all(w["served"] for w in wfs),
      f"MCP comfyq_list_workflows: served {[w['id'] for w in wfs]}")
err, desc = mcp_tool("comfyq_describe_workflow", workflow_id=wf)
check(not err and desc["parameters"] and all("key" in p and "kind" in p for p in desc["parameters"]),
      f"MCP comfyq_describe_workflow ({len(desc.get('parameters', []))} parameters)")
err, still = mcp_tool("blender_render", kind="still", max_edge=512)
check(not err and Path(still["path"]).is_file(), f"MCP blender_render still → {Path(still['path']).name}")
# Cycles still: rendered in a separate Blender (the GPU is free again when it exits); the open file is untouched
_t0, _file_before = time.monotonic(), bpy.data.filepath
err, cyc = mcp_tool("blender_render", kind="still", style="CYCLES", max_edge=128, samples=4)
_cimg = bpy.data.images.load(cyc["path"]) if not err else None
check(not err and _cimg is not None and max(_cimg.size) == 128 and bpy.data.filepath == _file_before,
      f"MCP blender_render CYCLES in a separate Blender: {_cimg and tuple(_cimg.size)} in "
      f"{time.monotonic() - _t0:.1f} s ({cyc if err else Path(cyc['path']).name})")
if _cimg is not None:
    bpy.data.images.remove(_cimg)
err, bad = mcp_tool("blender_render", kind="animation", style="CYCLES")
check(err and "must be one of" in bad["error"], "Cycles is for stills only (animation refused)")
from bl_ext.user_default.comfyq_bridge.passes import render as _render

# F1: the Cycles render does not block Blender: started, then polled (progress "Cycles n/m"), ✕ kills it
_t0 = time.monotonic()
_cj = _render.CyclesRender(scene, 256, None, 16, tag="smoke")
_started = time.monotonic() - _t0
_texts, _out = set(), _cj.poll()
check(_out is None and _cj in _render.ACTIVE and _started < 10,
      f"F1: a Cycles render starts and returns at once ({_started:.1f} s), polled while it runs")
while _out is None:
    _texts.add(_cj.text().split(" · ")[0])
    time.sleep(0.2)
    _out = _cj.poll()
check(_out.is_file() and _cj not in _render.ACTIVE and not _cj.blend.exists() and _cj.done_samples == 16
      and _cj.text().startswith("Cycles 16/16"),
      f"F1: progress read from the render's samples ({_cj.text()}; seen while polling: {sorted(_texts)})")
_cj = _render.CyclesRender(scene, 256, None, 4096, tag="smoke")
time.sleep(1.0)
_n = _render.cancel_cycles()
try:
    _cj.poll()
    _why = ""
except RuntimeError as e:
    _why = str(e)
check(_n == 1 and "cancelled" in _why and _cj.proc.poll() is not None and not _render.ACTIVE and not _cj.blend.exists(),
      f"F1: ✕ (Stop Cycles Render) kills the separate Blender ({_why})")
# the graph run waits for a Cycles source without blocking (Rendering), then uses its file
from bl_ext.user_default.comfyq_bridge.graph import run as _grun_c

_ct = bpy.data.node_groups.new("Cycles smoke", "ComfyQGraphTree")
_cs = _ct.nodes.new("ComfyQSceneRenderNode")
_cs.style, _cs.cycles_samples = "CYCLES", 4
_crun = _grun_c.Run(_ct, scene, [], {}, [])


class _Spec:
    max_input_edge, label = 128, "Image"


_waits, _cp = 0, None
while _cp is None and _waits < 600:
    try:
        _cp = _crun._file(scene, _cs, _Spec)
    except _grun_c.Rendering:
        _waits += 1
        time.sleep(0.2)
check(_cp is not None and _cp.is_file() and _waits > 0 and not _crun.cycles,
      f"F1: graph run polls the Cycles source ({_waits} ticks while rendering), then uses {_cp and _cp.name}")
_crun2 = _grun_c.Run(_ct, scene, [], {}, [])
_cs.cycles_samples = 4096
try:
    _crun2._file(scene, _cs, _Spec)
except _grun_c.Rendering:
    pass
_crun2.end_renders()
check(not _render.ACTIVE and not _crun2.cycles, "F1: graph Stop / run end kills its Cycles render")
bpy.data.node_groups.remove(_ct)

_fp1 = _render.scene_fingerprint(scene, "CYCLES")
_w = scene.world or bpy.data.worlds.new("smoke world")
scene.world = _w
_w.color = (0.3, 0.2, 0.1)
try:
    _w.node_tree.nodes["Background"].inputs["Strength"].default_value += 0.5
except (AttributeError, KeyError, TypeError):
    pass
check(_render.scene_fingerprint(scene, "CYCLES") != _fp1 or _w.node_tree is None,
      "a world / HDRI change makes a Cycles source out of date (not a Clay one)")
err, short = mcp_tool("blender_render", kind="animation", max_edge=256, max_seconds=0.5)
fps_ = scene.render.fps / scene.render.fps_base
check(not err and short["frames"] == max(1, round(0.5 * fps_)) and scene.frame_end == info["frames"][1],
      f"MCP blender_render animation max_seconds: {short.get('frames')} frames, scene range kept")
err, out = mcp_tool("comfyq_submit", workflow_id=unserved or "x", prompt="no")
check(err and "not served" in out["error"], "MCP comfyq_submit refuses a workflow that is not served")
err, g = mcp_tool("blender_graph_describe")
wnode_name = next(n["name"] for n in g["nodes"] if n["type"] == "Workflow")
err2, s = mcp_tool("blender_graph_set", node=wnode_name, key="prompt", value="set by an agent")
check(not err and not err2 and bpy.data.node_groups["ComfyQ Graph"].nodes[wnode_name].current_values()
      .get(prompt_key) == "set by an agent", f"MCP blender_graph_describe + blender_graph_set ({s})")
_img_node = bpy.data.node_groups["ComfyQ Graph"].nodes.new("ComfyQImageNode")
err, s = mcp_tool("blender_graph_set", node=_img_node.name, key="image", value=still["path"])
check(not err and _img_node.image is not None and Path(bpy.path.abspath(_img_node.image.filepath)).name ==
      Path(still["path"]).name, f"MCP blender_graph_set loads a file into an Image node ({s})")
_fresh = bpy.data.images.load(still["path"])                  # never displayed: pixels not read yet
_fresh.name = "smoke fresh image"
check(render.image_pass(_fresh, 256).is_file(),
      "an image loaded from a file and never displayed uploads (image_pass reads its pixels first)")
bpy.data.images.remove(_fresh)
# two inputs prepared in the same second must not share a file (a 4-view preset sent the left view as the front)
_ia, _ib = bpy.data.images.new("smoke pass A", 64, 64), bpy.data.images.new("smoke pass B", 64, 64)
_ia.pixels = [1.0, 0.0, 0.0, 1.0] * (64 * 64)
_ib.pixels = [0.0, 0.0, 1.0, 1.0] * (64 * 64)
_pa, _pb = render.image_pass(_ia, 64), render.image_pass(_ib, 64)
_ra = bpy.data.images.load(str(_pa))
_rb = bpy.data.images.load(str(_pb))
check(_pa != _pb and _ra.pixels[0] > 0.9 and _rb.pixels[2] > 0.9 and _rb.pixels[0] < 0.1,
      f"two passes in the same second get their own files ({_pa.name}, {_pb.name})")
for _im in (_ia, _ib, _ra, _rb):
    bpy.data.images.remove(_im)
bpy.data.node_groups["ComfyQ Graph"].nodes.remove(_img_node)
err, st = mcp_tool("blender_graph_status")
check(not err and not st["running"] and st["nodes"], "MCP blender_graph_status")
err, imp = mcp_tool("blender_import", path=still["path"])
check(not err and imp["imported"], f"MCP blender_import: {imp.get('imported')}")
err, imp = mcp_tool("blender_import", path=str(glb), place="ORIGIN", size=2.0)
_tn = [o.name for o in bpy.data.objects if o.get("comfyq_job") == "agent" and o.type == "MESH"]
err2, tr = mcp_tool("blender_transform", names=_tn[-1:], location=[1, 2, 3], scale=0.5, hide=True)
_to = bpy.data.objects.get(_tn[-1]) if _tn else None
check(not err and "placed at the origin" in imp["imported"][0] and not err2 and _to is not None
      and tuple(round(v, 3) for v in _to.location) == (1, 2, 3) and _to.hide_get(),
      f"MCP blender_import place/size + blender_transform ({imp.get('imported')}, {tr})")
err, w1 = mcp_tool("blender_world", hdri="builtin:forest", strength=1.5, rotation_deg=90, background=False)
_wn = scene.world.node_tree.nodes if scene.world else None
_first = (not err and _wn is not None and _wn.get("ComfyQ HDRI") is not None
          and abs(_wn["ComfyQ Background"].inputs["Strength"].default_value - 1.5) < 1e-4
          and abs(_wn["ComfyQ Mapping"].inputs["Rotation"].default_value[2] - 1.5708) < 1e-3
          and scene.render.film_transparent)
err2, w2 = mcp_tool("blender_world", hdri="builtin:city")           # a second call updates, no duplicates
_envs = [n for n in scene.world.node_tree.nodes if n.bl_idname == "ShaderNodeTexEnvironment"]
check(_first and not err2 and len(_envs) == 1 and _envs[0].image.name.startswith("city"),
      f"MCP blender_world: HDRI, strength, rotation, background; a second call updates it ({w1.get('hdri')})")
err, sc1 = mcp_tool("blender_shadow_catcher", plane=True, strength=0.8)
_pl = bpy.data.objects.get(sc1.get("objects", ["?"])[0]) if not err else None
check(_pl is not None and _pl.is_shadow_catcher and not _pl.visible_shadow and _pl.data.materials[0].name ==
      "ComfyQ Shadow Catcher" and abs(_pl.data.materials[0].node_tree.nodes["Strength"].inputs[1].default_value
                                      - 0.8) < 1e-4,
      f"MCP blender_shadow_catcher plane: a ground plane, Cycles flag + EEVEE material ({sc1})")
_sc_coll = bpy.data.collections.new("smoke catcher coll")
scene.collection.children.link(_sc_coll)
_sc_obj = bpy.data.objects.new("smoke catcher obj", bpy.data.meshes.new("smoke catcher obj"))
_sc_coll.objects.link(_sc_obj)
err, sc2 = mcp_tool("blender_shadow_catcher", collection="smoke catcher coll")
err2, sc3 = mcp_tool("blender_shadow_catcher", names=["smoke catcher obj"], remove=True)
check(not err and sc2["objects"] == ["smoke catcher obj"] and not err2 and not _sc_obj.is_shadow_catcher
      and not any(m and m.name == "ComfyQ Shadow Catcher" for m in _sc_obj.data.materials),
      "MCP blender_shadow_catcher on a collection, and remove undoes it")
for _o in (_pl, _sc_obj):
    if _o is not None:
        bpy.data.objects.remove(_o)
bpy.data.collections.remove(_sc_coll)
err, w3 = mcp_tool("blender_world", clear=True)
check(not err and scene.world.node_tree.nodes.get("ComfyQ HDRI") is None and not scene.render.film_transparent,
      "MCP blender_world clear: back to a plain world")
# segmentation helpers (SAM3 takes a video): still → MP4 → frame back, mask stats, overlay, repaint mask
import numpy as _np
from bl_ext.user_default.comfyq_bridge.passes import segment as _seg

_wd = _seg.work_dir("smoke")
_still = Path(still["path"])
_mp4 = _seg.still_to_video(_still, _wd / "in.mp4")
_back = _seg.video_frame(_mp4, _wd / "back.png")
_a, _b = _seg.read_rgba(_still), _seg.read_rgba(_back)
_sh = (min(_a.shape[0], _b.shape[0]), min(_a.shape[1], _b.shape[1]))
_diff = float(_np.abs(_a[:_sh[0], :_sh[1], :3] - _b[:_sh[0], :_sh[1], :3]).mean())
check(_mp4.is_file() and _b.shape[0] >= _a.shape[0] - 1 and _diff < 0.03,
      f"Segment: still → MP4 → frame keeps the picture ({_b.shape[1]}×{_b.shape[0]}, mean diff {_diff:.3f})")
_m = _np.zeros((_a.shape[0], _a.shape[1], 4), dtype=_np.float32)
_m[_a.shape[0] // 2:, :, :] = 1.0                       # bottom half white = "floor"
_mpath = _seg.write_rgba(_m, _wd / "mask_floor.png")
_reg = _seg.region("floor", _mpath)
check(abs(_reg.coverage - 0.5) < 0.02 and _reg.where == "bottom" and _reg.found,
      f"Segment: mask stats (floor {_reg.coverage:.2f} of the picture, {_reg.where})")
_ov = _seg.overlay(_still, [_reg], _wd / "overlay.png")
check(_ov.is_file() and _reg.extra.get("colour"), f"Segment: colour overlay ({_reg.extra.get('colour')})")
err, _mi = mcp_tool("blender_mask_image", image=str(_still), masks=[str(_mpath)], grow_px=4)
_mrgba = _seg.read_rgba(Path(_mi["path"])) if not err else None
check(not err and _mrgba[-1, 0, 3] == 0.0 and _mrgba[0, 0, 3] == 1.0
      and abs(float((_mrgba[..., 3] == 0).mean()) - 0.5) < 0.03,
      "MCP blender_mask_image: the floor half is transparent (= repainted), the rest opaque")
if "utility_video_segment_sam3" not in SESSION.served_ids():     # never books SAM3 jobs here
    err, out = mcp_tool("blender_segment", labels=["floor"], show=False)
    check(err and "not served" in out["error"], "MCP blender_segment refuses while SAM3 is not served")
err, out = mcp_tool("blender_task_status")
check(not err and "task" in out, f"MCP blender_task_status ({(out.get('task') or {}).get('kind')})")
# UX phase D1: a workflow with 16 reference images shows one free input, the rest behind "+ Add …"
_qe = "image_qwen_image_2_1_image_edit"
if any(w.id == _qe for w in SESSION.workflows):
    SESSION.specs.setdefault(_qe, SESSION.client.params(_qe))
    _dt = bpy.data.node_groups.new("D1 smoke", "ComfyQGraphTree")
    _dn = _dt.nodes.new("ComfyQWorkflowNode")
    _dn.workflow_id = _qe
    _dn.build(SESSION.specs[_qe])
    _dn.refresh_visibility(SESSION.specs[_qe])
    _imgs = [x for x in _dn.inputs if x.bl_idname.endswith("Image")]
    _vis = [x for x in _imgs if not x.hide]
    _dn.extra_inputs += 1
    _dn.refresh_visibility(SESSION.specs[_qe])
    _vis2 = [x for x in _imgs if not x.hide]
    _dt.links.new(_dt.nodes.new("ComfyQSceneRenderNode").outputs[0], _vis2[0])
    _dn.refresh_visibility(SESSION.specs[_qe])
    _vis3 = [x for x in _imgs if not x.hide]
    check(len(_imgs) == 16 and len(_vis) == 1 and len(_vis2) == 2 and len(_vis3) == 3,
          f"D1: 16 optional image inputs → 1 shown, +Add → 2, linking one shows the next ({len(_imgs)}: "
          f"{len(_vis)}, {len(_vis2)}, {len(_vis3)})")
    bpy.data.node_groups.remove(_dt)
# UX phase D3: presets land in a Frame; Run Graph runs the selection (a Frame), else asks for several chains
from bl_ext.user_default.comfyq_bridge.graph import preset_ops as _po3
from bl_ext.user_default.comfyq_bridge.graph import run as _run3

_t3 = bpy.data.node_groups.new("D3 smoke", "ComfyQGraphTree")
_a3, _b3 = _t3.nodes.new("ComfyQWorkflowNode"), _t3.nodes.new("ComfyQWorkflowNode")
_pa, _pb = _t3.nodes.new("ComfyQPromptNode"), _t3.nodes.new("ComfyQPromptNode")
_a3.location, _pa.location, _b3.location, _pb.location = (0, 0), (-300, 0), (0, -600), (-300, -600)
_fr = _po3.wrap_in_frame(_t3, [_a3.name, _pa.name], "Preset A")
_kept = tuple(_a3.location_absolute) if hasattr(_a3, "location_absolute") else (0.0, 0.0)
for _n in _t3.nodes:
    _n.select = False
_all, _from_sel = _run3.graph_targets(_t3, scene)
_fr.select = True
_one, _from_sel2 = _run3.graph_targets(_t3, scene)
check(_fr is not None and _a3.parent == _fr and _fr.label == "Preset A" and abs(_kept[0]) < 1 and abs(_kept[1]) < 1
      and sorted(_all) == sorted([_a3.name, _b3.name]) and not _from_sel and _one == [_a3.name] and _from_sel2
      and _run3.chain_name(_t3, _a3.name) == "Preset A",
      f"D3: a preset's nodes go in a labelled Frame (kept in place); Run Graph = the selected Frame "
      f"({_one}), else every chain ({len(_all)})")
bpy.data.node_groups.remove(_t3)
# UX phase E: draw code never writes ID data; header buttons are never appended twice
from bl_ext.user_default.comfyq_bridge.ui import player as _pl

_mp4s = sorted((Path(os.environ.get("LOCALAPPDATA", "")) / "comfyq-bridge" / "cache").rglob("*.mp4"))
if _mp4s:
    _mimg = bpy.data.images.load(str(_mp4s[-1]), check_existing=False)
    _pl.forget_meta(_mimg)
    _nclips = len(bpy.data.movieclips)
    _est = _pl.peek_meta(_mimg)                          # what the Player panel's draw() calls
    check(len(bpy.data.movieclips) == _nclips and _est[0] >= 1,
          f"Player draw reads movie info without creating data (estimate {_est}; the timer reads it)")
    bpy.data.images.remove(_mimg)
from bl_ext.user_default.comfyq_bridge import graph as _graph_pkg
from bl_ext.user_default.comfyq_bridge.ui import panel as _panel_mod

_graph_pkg.register_extras()                            # a second registration (failed reload)
_panel_mod.register_extras()
_hf = [f for f in getattr(bpy.types.NODE_HT_header.draw, "_draw_funcs", []) if f.__name__ == "_header"]
_vf = [f for f in getattr(bpy.types.VIEW3D_HT_header.draw, "_draw_funcs", []) if f.__name__ == "_header_button"]
check(len(_hf) == 1 and len(_vf) == 1, f"header buttons appended once even when registered twice ({len(_hf)}, {len(_vf)})")
# UX phase B: why Generate is greyed out, Stop a graph run, remove a finished job from the list
from bl_ext.user_default.comfyq_bridge.ops import generate as _gen

_uns = next((w for w in SESSION.workflows if w.id not in SESSION.served_ids() and not w.unavailable), None)
if _uns is not None:
    SESSION.specs.setdefault(_uns.id, SESSION.client.params(_uns.id))
    _why = _gen.why_not_generate(scene, _uns.id)
    check("not served" in _why and _uns.name in _why, f"Generate greyed out for an unserved model, with why ({_why[:70]})")
check(_gen.why_not_generate(scene, "") == "Choose a model first", "Generate: no model chosen → says so")
from bl_ext.user_default.comfyq_bridge.graph import run as _grun_b

_bt = bpy.data.node_groups.new("Stop smoke", "ComfyQGraphTree")
_bw = _bt.nodes.new("ComfyQPromptNode")
_bs = _bt.nodes.new("ComfyQSurfacesNode")
_bt.links.new(_bt.nodes.new("ComfyQSceneRenderNode").outputs[0], _bs.inputs["Image"])
_brun = _grun_b.Run(_bt, scene, [_bs.name], _grun_b.describe(_bt, scene), [])
_brun.booking.add(_bs.name)
_brun.surface_jobs[_bs.name] = ["no-such-job"]
_grun_b.RUNS[_bt.name] = _brun
with bpy.context.temp_override(**({} if not bpy.context.window else {"window": bpy.context.window})):
    _res = bpy.ops.comfyq.graph_stop(tree=_bt.name)
_grun_b.tick_all()
check(_res == {"FINISHED"} and _bt.name not in _grun_b.RUNS and _bs.note == "Stopped",
      f"Graph › Stop ends the run, cancels its SAM3 jobs, marks the nodes ({_res}, {_bs.note})")
bpy.data.node_groups.remove(_bt)
_jr = scene.comfyq.jobs.add()
_jr.job_id, _jr.workflow_id, _jr.label = "smoke-remove-job", "x", "Smoke"
_n0 = len(scene.comfyq.jobs)
_res = bpy.ops.comfyq.remove_job(job_id="smoke-remove-job")
check(_res == {"FINISHED"} and len(scene.comfyq.jobs) == _n0 - 1
      and all(r.job_id != "smoke-remove-job" for r in scene.comfyq.jobs), "Job card › Remove from List")
# F2: teacher Server panel (read only: never loads a model) + Class mode
from bl_ext.user_default.comfyq_bridge.graph import run as _grun_f2
from bl_ext.user_default.comfyq_bridge.prefs import teacher_unlocked as _unl2
from bl_ext.user_default.comfyq_bridge.ui import server as _srv

_env2 = {k: os.environ.get(k) for k in ("COMFYQ_BRIDGE_CONFIG", "COMFYQ_ADMIN_PASSWORD")}
_pp2 = bpy.context.preferences.addons["bl_ext.user_default.comfyq_bridge"].preferences
try:
    _ccfg = Path(tempfile.mkdtemp()) / "profiles.toml"
    _ccfg.write_text('default = "class"\n[profiles.class]\nclass_mode = true\n'
                     '[profiles.class.comfyq]\nurl = "http://127.0.0.1:3000"\nadmin_password = "x"\n', encoding="utf-8")
    os.environ["COMFYQ_BRIDGE_CONFIG"] = str(_ccfg)
    os.environ.pop("COMFYQ_ADMIN_PASSWORD", None)
    _pp2.solo_mode = True
    check(not _unl2(_pp2) and not _grun_f2.solo_on() and not _srv.visible()
          and not bpy.ops.comfyq.server_load_model.poll(),
          "F2: Class mode (profile) turns teacher controls off even with an admin password; no Server panel")
    os.environ["COMFYQ_BRIDGE_CONFIG"] = str(_ccfg.with_name("none.toml"))   # teacher: admin password, no class mode
    os.environ["COMFYQ_ADMIN_PASSWORD"] = "smoke"
    _pp2.solo_mode = False
    check(_srv.visible() and bpy.ops.comfyq.server_load_model.poll(), "F2: Server panel + Load Model on the teacher's PC")
    _srv.SERVER.update(drawn=time.monotonic(), t=0.0, lanes=[], card={})
    _srv.tick()
    pump_until(lambda: not _srv.SERVER["busy"], 15, "server panel refresh")
    _ln = [x.get("workflowId") for x in _srv.SERVER["lanes"]]
    check(set(_ln) == SESSION.served_ids() and _srv.SERVER["card"].get("vramGb") and not _srv.SERVER["error"],
          f"F2: Server panel reads the served models and GPU memory ({_ln}, {_srv.SERVER['card']})")
    _users = SESSION.client.users()
    check(SESSION.client.profile.user in _users, f"F2: connected users from ComfyQ's state ({_users})")
finally:
    _pp2.solo_mode = False
    for _k, _v in _env2.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
# F5 Retry: booking stores the values sent; Retry books them again with the same files (nothing really booked:
# SESSION.run / track are faked, so no GPU job)
from bl_ext.user_default.comfyq_bridge.ops import generate as _gen

_rf = Path(tempfile.mkdtemp()) / "retry_in.png"
_rf.write_bytes(b"png")
_booked = []
_run0, _track0 = SESSION.run, SESSION.track
SESSION.run = lambda work, done, **kw: (_booked.append(work), done(f"smoke-retry-{len(_booked)}"))
SESSION.track = lambda *a, **kw: None
try:
    _gen.book(scene, wf, SESSION.specs[wf], {"smoke_value": 7}, {"smoke_file": _rf}, label="Smoke retry")
    _r1 = next(r for r in scene.comfyq.jobs if r.job_id == "smoke-retry-1")
    check(json.loads(_r1.values) == {"smoke_value": 7} and json.loads(_r1.inputs) == {"smoke_file": str(_rf)},
          "Generate stores the values and files sent (for Retry)")
    check(_gen.why_not_retry(scene, "smoke-retry-1") == "", f"Retry possible ({_gen.why_not_retry(scene, 'smoke-retry-1')})")
    _res = bpy.ops.comfyq.retry(job_id="smoke-retry-1")
    _r2 = next((r for r in scene.comfyq.jobs if r.job_id == "smoke-retry-2"), None)
    check(_res == {"FINISHED"} and _r2 is not None and _r2.values == _r1.values and _r2.inputs == _r1.inputs
          and _r2.label == "Retry · Smoke retry" and _r2.workflow_id == wf,
          "Job card › Retry books the same workflow, values and files again")
    _rf.unlink()
    _why = _gen.why_not_retry(scene, "smoke-retry-1")
    check("no longer on disk" in _why and bpy.ops.comfyq.retry(job_id="smoke-retry-1") == {"CANCELLED"},
          f"Retry refused with the reason when a file is gone ({_why})")
    _r1.values = ""
    check("before Retry existed" in _gen.why_not_retry(scene, "smoke-retry-1"), "Retry refused for old job records")
finally:
    SESSION.run, SESSION.track = _run0, _track0
    for _jid in ("smoke-retry-1", "smoke-retry-2"):
        _ix = next((n for n, r in enumerate(scene.comfyq.jobs) if r.job_id == _jid), None)
        if _ix is not None:
            scene.comfyq.jobs.remove(_ix)
# Batch renders from a Media Folder (no GPU: booking and finished jobs are faked, as for Retry)
from bl_ext.user_default.comfyq_bridge.client.models import COMPLETED, Job
from bl_ext.user_default.comfyq_bridge.graph import batch as _gbatch
from bl_ext.user_default.comfyq_bridge.graph import run as _grun_bt
from bl_ext.user_default.comfyq_bridge.runtime.session import JobView

_img_wf = next((w for w in sorted(SESSION.served_ids())        # one required media input, and it is an image
                if [x.kind for x in SESSION.client.params(w) if x.is_media and x.required] == ["image"]), None)
if _img_wf is None:
    check(True, "batch: no served workflow with an image input: skipped")
else:
    SESSION.specs[_img_wf] = SESSION.client.params(_img_wf)
    _broot = Path(tempfile.mkdtemp()) / "photos"
    for _rel in ("a.png", "sub/b.png", "sub/deep/c.png"):
        _f = _broot / _rel
        _f.parent.mkdir(parents=True, exist_ok=True)
        _im = bpy.data.images.new("smoke batch src", 32, 24)
        _im.filepath_raw, _im.file_format = str(_f), "PNG"
        _im.save()
        bpy.data.images.remove(_im)
    _done_before = _gbatch.result_stem(_broot, _broot / "sub" / "b.png", "_smk")
    _done_before.parent.mkdir(parents=True)
    (_done_before.parent / (_done_before.name + ".png")).write_bytes(b"earlier")     # rendered before: skipped
    _bt = bpy.data.node_groups.new("Batch smoke", "ComfyQGraphTree")
    _bf = _bt.nodes.new("ComfyQFolderNode")
    _bf.folder = str(_broot)
    _bw = _bt.nodes.new("ComfyQWorkflowNode")
    _bw.workflow_id = _img_wf
    _bw.build(SESSION.specs[_img_wf])
    _bsock = next(x for x in _bw.inputs if socket_kind(x) == "image" and x.enabled)
    _bt.links.new(_bf.outputs["Image"], _bsock)
    _bp = _bt.nodes.new("ComfyQPromptNode")
    _bp.text = "batch smoke"
    for _x in _bw.inputs:
        if socket_kind(_x) == "text" and _x.enabled and not _x.is_linked:
            _bt.links.new(_bp.outputs[0], _x)
    _ok0, _m0 = _grun_bt.start(_bt, scene, *[_grun_bt.graph_targets(_bt, scene)[0], []])
    check(not _ok0 and "Save node" in _m0, f"batch: refused without a Save node ({_m0})")
    _bs = _bt.nodes.new("ComfyQSaveNode")
    _bs.batch_suffix = "_smk"
    _bt.links.new(_bw.outputs[0], _bs.inputs[0])
    _fake = {"n": 0, "uploaded": []}

    def _fake_run(work, done, **kw):
        _fake["n"] += 1
        jid = f"smoke-batch-{_fake['n']}"
        res = Path(tempfile.mkdtemp()) / "result.png"
        res.write_bytes(b"result")
        SESSION.jobs[jid] = JobView(job=Job(id=jid, workflow_id=_img_wf, user_id="smoke", phase=COMPLETED), files=[res])
        done(jid)

    _run0b, _track0b = SESSION.run, SESSION.track
    SESSION.run, SESSION.track = _fake_run, (lambda *a, **kw: None)
    try:
        _ok, _msg = _grun_bt.start(_bt, scene, _grun_bt.graph_targets(_bt, scene)[0], [])
        pump_until(lambda: (_grun_bt.tick_all(), _bt.name not in _grun_bt.RUNS)[1], 30, "batch run")
        _outs = sorted(x.relative_to(_gbatch.out_root(_broot)).as_posix() for x in _gbatch.out_root(_broot).rglob("*")
                       if x.is_file())
        check(_ok and "2 images to render" in _msg and "1 already done" in _msg and _fake["n"] == 2
              and _outs == ["a_smk.png", "sub/b_smk.png", "sub/deep/c_smk.png"]
              and (_gbatch.out_root(_broot) / "sub" / "b_smk.png").read_bytes() == b"earlier"
              and (_gbatch.out_root(_broot) / "a_smk.png").read_bytes() == b"result" and not _bf.current,
              f"batch: each picture rendered once, results mirrored into photos_renders with the suffix, the one "
              f"done before skipped ({_msg}; {_outs}; jobs {_fake['n']})")
        _ok2, _msg2 = _grun_bt.start(_bt, scene, _grun_bt.graph_targets(_bt, scene)[0], [])
        check(_ok2 and "Nothing to do" in _msg2 and _fake["n"] == 2, f"batch: a second run has nothing to do ({_msg2})")
        _bt.links.new(_bf.outputs["Video"], _bt.nodes.new("ComfyQViewerNode").inputs[0])
        _ok3, _msg3 = _grun_bt.start(_bt, scene, _grun_bt.graph_targets(_bt, scene)[0], [])
        check(not _ok3 and "one of them" in _msg3, f"batch: Image and Video both linked is refused ({_msg3})")
    finally:
        SESSION.run, SESSION.track = _run0b, _track0b
        for _k in [k for k in SESSION.jobs if k.startswith("smoke-batch-")]:
            SESSION.jobs.pop(_k, None)
        _grun_bt.RUNS.pop(_bt.name, None)
        bpy.data.node_groups.remove(_bt)
# Surfaces node (SAM3 → prompt) in the graph: no SAM3 job here (found surfaces are faked)
from bl_ext.user_default.comfyq_bridge.graph import outputs as _gout
from bl_ext.user_default.comfyq_bridge.graph import preset_ops as _gpo
from bl_ext.user_default.comfyq_bridge.graph import run as _grun

_st = bpy.data.node_groups.new("Surfaces smoke", "ComfyQGraphTree")
_sr, _sf, _sv = (_st.nodes.new(t) for t in ("ComfyQSceneRenderNode", "ComfyQSurfacesNode", "ComfyQViewerNode"))
_st.links.new(_sr.outputs[0], _sf.inputs["Image"])
_st.links.new(_sf.outputs["Prompt"], _sv.inputs[0])
_sf.regions = json.dumps([
    {"label": "wall", "found": True, "coverage_pct": 34.5, "where": "across the picture", "bbox": [0, 0, 1, 0.8],
     "mask": str(_mpath)},
    {"label": "floor", "found": True, "coverage_pct": 12.1, "where": "bottom", "bbox": [0, 0.7, 1, 1], "mask": str(_mpath)}])
_sp = _sf.prompt()
check(_sp.startswith(_sf.intro) and _sp.index("The wall is smooth") < _sp.index("The floor is wide-plank")
      and _sp.endswith(_sf.outro), f"Surfaces node: prompt from the found surfaces + the list's materials ({_sp[:90]}…)")
_sf.text_block.clear()
_sf.text_block.write("floor: polished concrete\nwall|left wall|right wall: exposed brick")
check("The floor is polished concrete." in _sf.prompt() and "exposed brick" in _sf.prompt(),
      "Surfaces node: a material edit rewrites the prompt (no new segmentation)")
_gd = _grun.describe(_st, scene)
check(_gd[_sf.name].workflow and _gd[_sf.name].inputs == {"Image": _sr.name} and _grun._text(_sf) == _sf.prompt(),
      "Surfaces node: booked by graph runs, fed by its Image input, its Prompt goes downstream")
_lm = _gout.linked(_sv)
check(_lm is not None and _lm.text == _sf.prompt(), "Surfaces node › Viewer gets the prompt text")
_data = _gpo.serialize(_st, "Surfaces smoke", "")
_st2 = bpy.data.node_groups.new("Surfaces smoke 2", "ComfyQGraphTree")
_made = _gpo.build_saved(_data, _st2, scene)
_sf2 = next((n for n in _made if n.bl_idname == "ComfyQSurfacesNode"), None)
check(_sf2 is not None and _sf2.value() == _sf.value() and _sf2.intro == _sf.intro and len(_st2.links) == 2,
      "Surfaces node: saved in a preset and rebuilt (list, text, links)")
if "utility_video_segment_sam3" not in SESSION.served_ids() and not _grun.solo_on():
    _ok, _msg = _grun.start(_st, scene, [_sf.name], [])
    check(not _ok and "Not served" in _msg and "Surfaces" in _msg, f"Surfaces node: not run while SAM3 is not served ({_msg})")
_arch = next((Path(__file__).resolve().parents[2] / "presets" / "class").glob("Architecture*SeedVR2*.json"))
_ad = json.loads(_arch.read_text(encoding="utf-8"))
for _w in {d["workflow_id"] for d in _ad["nodes"] if d.get("workflow_id") and d["type"] == "ComfyQWorkflowNode"}:
    from bl_ext.user_default.comfyq_bridge.ops.workflow import request_specs as _rq
    _rq(_w)
pump_until(lambda: all(d["workflow_id"] in SESSION.specs for d in _ad["nodes"]
                       if d["type"] == "ComfyQWorkflowNode"), 30, "architecture preset specs")
_st3 = bpy.data.node_groups.new("Arch smoke", "ComfyQGraphTree")
_m3 = _gpo.build_saved(_ad, _st3, scene)
_l3 = {(lk.from_node.bl_idname, lk.to_node.bl_idname, lk.to_socket.identifier) for lk in _st3.links}
check(("ComfyQSurfacesNode", "ComfyQWorkflowNode", "textencodeqwenimage21_prompt_459_474") in _l3
      and ("ComfyQSceneRenderNode", "ComfyQSurfacesNode", "Image") in _l3
      and len(_l3) == 6, f"Architecture class preset: Cycles → Surfaces (SAM3) → Qwen edit prompt → SeedVR2 ({len(_l3)} links)")
# every shipped preset (class + library) names workflows this ComfyQ still has (upstream removed 7 on 2026-10-05)
_have = {w.id for w in SESSION.workflows}
_presets_root = Path(__file__).resolve().parents[2] / "presets"
_dead = sorted(f"{f.relative_to(_presets_root)} → {n['workflow_id']}"
               for f in list(_presets_root.glob("class/*.json")) + list(_presets_root.glob("library/*/*.json"))
               for n in json.loads(f.read_text(encoding="utf-8"))["nodes"]
               if n.get("workflow_id") and n["workflow_id"] not in _have)
check(not _dead, f"shipped presets only use workflows ComfyQ has ({len(_have)} workflows; dead: {_dead})")
# the Four views class preset: 4 Image nodes → 4 Klein edits sharing one Prompt and one fixed seed → 4 auto Saves
_fv = json.loads(next(_presets_root.glob("class/Four views*.json")).read_text(encoding="utf-8"))
for _w in {d["workflow_id"] for d in _fv["nodes"] if d.get("workflow_id")}:
    _rq(_w)
pump_until(lambda: all(d["workflow_id"] in SESSION.specs for d in _fv["nodes"] if d.get("workflow_id")), 30,
           "four views preset specs")
_ft = bpy.data.node_groups.new("Four views smoke", "ComfyQGraphTree")
_gpo.build_saved(_fv, _ft, scene)
_fw = [n for n in _ft.nodes if n.bl_idname == "ComfyQWorkflowNode"]
_fseeds = {(it.i, it.randomize) for n in _fw for it in n.params if it.kind == "seed" and it.workflow_id == n.workflow_id}
_fimg = {lk.from_node.label for lk in _ft.links if lk.from_node.bl_idname == "ComfyQImageNode"}
_fprompt = [lk for lk in _ft.links if lk.from_node.bl_idname == "ComfyQPromptNode"]
_fsaves = [n for n in _ft.nodes if n.bl_idname == "ComfyQSaveNode"]
check(len(_fw) == 4 and _fimg == {"Front view", "Left view", "Back view", "Right view"} and len(_fprompt) == 4
      and len(_fseeds) == 1 and not next(iter(_fseeds))[1] and len(_fsaves) == 4 and all(s.auto for s in _fsaves)
      and all(lk.is_valid for lk in _ft.links),
      f"Four views preset: 4 views → 4 Klein edits, one prompt, one fixed seed {_fseeds}, 4 auto Saves")
bpy.data.node_groups.remove(_ft)
# the Batch class preset: Media Folder (subfolders on) → Klein edit (fixed seed) ← Prompt; → Save with a batch suffix
_bv = json.loads(next(_presets_root.glob("class/Batch*.json")).read_text(encoding="utf-8"))
_btp = bpy.data.node_groups.new("Batch preset smoke", "ComfyQGraphTree")
_gpo.build_saved(_bv, _btp, scene)
_bfn = [n for n in _btp.nodes if n.bl_idname == "ComfyQFolderNode"]
_bsv = [n for n in _btp.nodes if n.bl_idname == "ComfyQSaveNode"]
_bwn = [n for n in _btp.nodes if n.bl_idname == "ComfyQWorkflowNode"]
check(len(_bfn) == 1 and _bfn[0].recursive and _bfn[0].outputs["Image"].is_linked and len(_bsv) == 1
      and _bsv[0].batch_suffix == "_render" and len(_bwn) == 1
      and all(not it.randomize for it in _bwn[0].params if it.kind == "seed" and it.workflow_id == _bwn[0].workflow_id)
      and all(lk.is_valid for lk in _btp.links),
      "Batch preset: Media Folder (subfolders) → Klein edit (fixed seed) → Save with suffix _render")
bpy.data.node_groups.remove(_btp)
_n3 = next((n for n in _st3.nodes if n.bl_idname == "NodeFrame" and n.label == "How to use"), None)
check(_n3 is not None and _n3.text is not None and "Surfaces" in _n3.text.as_string(),
      "F3: the Architecture class preset shows its How to use steps")
for _t in (_st, _st2, _st3):
    bpy.data.node_groups.remove(_t)
# teacher tools (load models): hidden + refused while the preference is off; only safe calls here
err, st = mcp_tool("comfyq_server_status", workflow_id=unserved or wf)
check(not err and {s_["workflow_id"] for s_ in st["served"]} == SESSION.served_ids() and st["card_gb"]
      and "workflow" in st and st["teacher_tools"] is False,
      f"MCP comfyq_server_status: {st.get('used_gb')}/{st.get('card_gb')} GB, served "
      f"{[s_['workflow_id'] for s_ in st.get('served', [])]}, fits now {len(st.get('fits_alongside_now', []))}")
check(not {"comfyq_serve", "comfyq_unserve", "comfyq_calibrate"} & set(names),
      "MCP: teacher tools are hidden while the preference is off")
err, out = mcp_tool("comfyq_serve", workflow_id=wf)
check(err and "Teacher tools are off" in out["error"], "… and refused if called anyway")
_p = bpy.context.preferences.addons["bl_ext.user_default.comfyq_bridge"].preferences
_admin_env = os.environ.pop("COMFYQ_ADMIN_PASSWORD", None)
_cfg_env = os.environ.get("COMFYQ_BRIDGE_CONFIG")
os.environ["COMFYQ_BRIDGE_CONFIG"] = os.path.join(bpy.app.tempdir or ".", "no_profiles.toml")   # a student PC
_p.mcp_teacher = True                                    # ticked on a PC without the admin password
names_l = [t["name"] for t in mcp_rpc("tools/list")["result"]["tools"]]
check(not bmcp.STATE["teacher"] and not {"comfyq_serve", "comfyq_unserve", "comfyq_calibrate"} & set(names_l),
      "Teacher tools stay hidden without the admin password in the profile, even when ticked")
os.environ["COMFYQ_ADMIN_PASSWORD"] = "smoke"            # the teacher's PC (this process only)
_p.mcp_teacher = False
_p.mcp_teacher = True
names_t = [t["name"] for t in mcp_rpc("tools/list")["result"]["tools"]]
check(bmcp.STATE["teacher"] and {"comfyq_serve", "comfyq_unserve", "comfyq_calibrate"} <= set(names_t),
      f"Preferences › Teacher tools lists them ({len(names_t)} tools)")
_now = sorted(SESSION.served_ids())      # served right now: the call must only answer "already served"
if _now:
    err, out = mcp_tool("comfyq_serve", workflow_id=_now[0])
    check(not err and out.get("message") == "already served", f"MCP comfyq_serve on a served workflow: {out}")
err, out = mcp_tool("comfyq_serve", workflow_id="no_such_workflow")
check(err and "Unknown workflow" in out["error"], "MCP comfyq_serve refuses an unknown workflow")
_main = next((ln.workflow_id for ln in SESSION.client.lanes() if ln.primary), None)
if _main:                                # never a side lane: that call would really stop it
    err, out = mcp_tool("comfyq_unserve", workflow_id=_main)
    check(err and "main workflow" in out["error"], f"MCP comfyq_unserve refuses the main workflow ({out})")
_p.mcp_teacher = False
os.environ.pop("COMFYQ_ADMIN_PASSWORD", None)
if _cfg_env is None:
    os.environ.pop("COMFYQ_BRIDGE_CONFIG", None)
else:
    os.environ["COMFYQ_BRIDGE_CONFIG"] = _cfg_env
check(not bmcp.STATE["teacher"], "… unticking turns them off again")
bmcp.stop()
check(not bmcp.info_path().exists(), "MCP server stops and removes its connection file")
import socket as _socket

with _socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    free_port = _s.getsockname()[1]
_p = bpy.context.preferences.addons["bl_ext.user_default.comfyq_bridge"].preferences
_p.mcp_port = free_port
_p.mcp_enabled = True
check(bmcp.running() and bmcp.STATE["url"].endswith(f":{free_port}/mcp"),
      f"Preferences › AI agents (MCP server) starts it ({bmcp.STATE})")
_p.mcp_enabled = False
check(not bmcp.running(), "… and unticking stops it")
del os.environ["COMFYQ_BRIDGE_CONFIG"]

if BOOK:
    bpy.ops.comfyq.match_scene()                       # 2 s scene → duration / clip length / fps
    for s in SESSION.specs[wf]:                        # keep the GPU job short: smallest size settings
        it = param_item(scene, s.key, wf)
        low = s.label.lower()
        if s.kind == "int" and s.min is not None and any(t in low for t in ("width", "height", "resolution")):
            it.i = int(s.min)
    res = bpy.ops.comfyq.generate()
    check(res == {"FINISHED"}, "generate operator ran")
    n_before = len(scene.comfyq.jobs)
    check(pump_until(lambda: len(scene.comfyq.jobs) > n_before - 1 and scene.comfyq.jobs[-1].workflow_id == wf,
                     60, "booking"), "job booked")
    jid = scene.comfyq.jobs[-1].job_id
    done = pump_until(lambda: (v := SESSION.jobs.get(jid)) is not None and v.job.done
                      and (not v.job.ok or v.files), 900, "job")
    view = SESSION.jobs.get(jid)
    check(done and view.job.ok, f"job {jid[:8]} {view.job.phase if view else '?'} {view.job.error or ''}")
    check(bool(view and view.files), f"downloaded {[f.name for f in view.files] if view else []}")
    check(scene.comfyq.jobs[-1].imported, "result imported automatically")

SESSION.close()

# -- reinstalling the extension must really load the new code (submodules included) ---------------
if "--zip" in ARGS:
    zip_path = ARGS[ARGS.index("--zip") + 1]
    before = sys.modules.get(MODULE + ".client.api")
    bpy.ops.extensions.package_install_files(filepath=zip_path, repo="user_default", enable_on_install=True)
    after = sys.modules.get(MODULE + ".client.api")
    check(before is not None and after is not None and after is not before,
          "reinstall reloads the submodules (client/api.py is a fresh module)")
    check(MODULE in bpy.context.preferences.addons and hasattr(bpy.context.scene, "comfyq"),
          "extension enabled again after reinstall")

for f in (ply, wav):
    Path(f).unlink(missing_ok=True)
print("== SMOKE", "FAILED: " + "; ".join(failures) if failures else "PASSED", flush=True)
sys.exit(1 if failures else 0)
