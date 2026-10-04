"""Headless smoke test, run inside Mixar or Blender by tools/smoke_test.ps1.

  mixar -b --factory-startup --python tests/blender/smoke.py -- [--book]

bpy.app.timers do not fire in background mode, so this script pumps the
session itself. Without --book it stops before sending a job (no GPU use).
The detached window and the dialog need a real screen: check those by hand.
"""
import json
import os
import shutil
import struct
import sys
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
# The GLB is a committed fixture: Mixar 4.2's glTF *exporter* crashes Mixar at exit in background mode
# (import, which is all the bridge uses, is fine).
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

# -- connect + workflow --------------------------------------------------------------
check(bpy.app.online_access, "online access enabled (--online-mode)")
SESSION.connect("", "smoke-test")
ok = pump_until(lambda: SESSION.connected and SESSION.workflows, 20, "connection")
check(ok, f"connected; {len(SESSION.workflows)} workflows")
served = sorted(SESSION.served_ids())
check(bool(served), f"served: {served}")
if not served:
    sys.exit(1)
wf = served[0]
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
# found Mixar's UI re-tagging objects): results arriving, images, custom properties, the timeline.
wnode.job_id = "x"
probe = bpy.data.images.new("ComfyQ probe", 8, 8)
cube["mixar_ui_tag"] = 1
keep_frame = scene.frame_current
scene.frame_set(scene.frame_start + 10)
bpy.context.view_layer.update()
check(_reuses_a(), "graph run: results arriving, custom properties and scrubbing the timeline do not "
                   "invalidate the scene input")
scene.frame_set(keep_frame)
bpy.data.images.remove(probe)
del cube["mixar_ui_tag"]
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
bar_prompt =next(s for s in SESSION.specs[wf] if s.kind == "prompt")
param_item(scene, bar_prompt.key, wf).s = "bar prompt for the graph"
bar_num = next((s for s in SESSION.specs[wf] if s.kind == "float"), None)
if bar_num is not None:
    param_item(scene, bar_num.key, wf).f = 0.61
tmp_tree = bpy.data.node_groups.new("smoke open as graph", gtree_mod.TREE)
from_bar = gui.node_from_bar(tmp_tree, scene, wf, 0.0, 0.0)
check(from_bar.workflow_id == wf and from_bar.item(bar_prompt.key).s == "bar prompt for the graph"
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
check(len(names) == 17 and "comfyq_submit" in names and "blender_graph_run" in names, f"MCP: {len(names)} tools")
err, info = mcp_tool("blender_scene_info")
check(not err and info["camera"] and info["frames"] == [scene.frame_start, scene.frame_end],
      f"MCP blender_scene_info: {info.get('scene')} {info.get('frames')} camera {info.get('camera')}")
err, wfs = mcp_tool("comfyq_list_workflows")
check(not err and {w["id"] for w in wfs} == SESSION.served_ids() and all(w["served"] for w in wfs),
      f"MCP comfyq_list_workflows: served {[w['id'] for w in wfs]}")
err, desc = mcp_tool("comfyq_describe_workflow", workflow_id=wf)
check(not err and any(p["kind"] == "prompt" for p in desc["parameters"]), "MCP comfyq_describe_workflow")
err, still = mcp_tool("blender_render", kind="still", max_edge=512)
check(not err and Path(still["path"]).is_file(), f"MCP blender_render still → {Path(still['path']).name}")
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
bpy.data.node_groups["ComfyQ Graph"].nodes.remove(_img_node)
err, st = mcp_tool("blender_graph_status")
check(not err and not st["running"] and st["nodes"], "MCP blender_graph_status")
err, imp = mcp_tool("blender_import", path=still["path"])
check(not err and imp["imported"], f"MCP blender_import: {imp.get('imported')}")
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
_p.mcp_teacher = True
names_t = [t["name"] for t in mcp_rpc("tools/list")["result"]["tools"]]
check(bmcp.STATE["teacher"] and {"comfyq_serve", "comfyq_unserve", "comfyq_calibrate"} <= set(names_t),
      f"Preferences › Teacher tools lists them ({len(names_t)} tools)")
err, out = mcp_tool("comfyq_serve", workflow_id=wf)
check(not err and out.get("message") == "already served", f"MCP comfyq_serve on a served workflow: {out}")
err, out = mcp_tool("comfyq_serve", workflow_id="no_such_workflow")
check(err and "Unknown workflow" in out["error"], "MCP comfyq_serve refuses an unknown workflow")
err, out = mcp_tool("comfyq_unserve", workflow_id=wf)
check(err and "main workflow" in out["error"], f"MCP comfyq_unserve refuses the main workflow ({out})")
_p.mcp_teacher = False
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
