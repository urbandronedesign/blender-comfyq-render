"""Headless smoke test, run inside Mixar or Blender by tools/smoke_test.ps1.

  mixar -b --factory-startup --python tests/blender/smoke.py -- [--book]

bpy.app.timers do not fire in background mode, so this script pumps the
session itself. Without --book it stops before sending a job (no GPU use).
The detached window and the dialog need a real screen: check those by hand.
"""
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
