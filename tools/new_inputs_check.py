# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU check of ComfyQ's `model3d` and `camera` inputs through the plugin's own graph (headless, sandbox profile).

    blender -b --online-mode --python tools/new_inputs_check.py -- <out folder> [mesh] [camera]

Builds Suzanne (subdivided) in the default scene, then:
  mesh:   Scene Objects (Suzanne) → Remesh & decimate → UV unwrap          (a mesh result chains into a mesh input)
  camera: Scene Render (clay) → Qwen 2511 reshoot, camera Around 90 / 180  (one job per position, same seed)
Solo mode loads each model (alongside when it fits, else as ComfyQ's main workflow: ComfyQ restarts 1–2 min).
Writes <out>/result.json; results stay in the plugin's cache (paths in the json).
"""
import importlib
import json
import sys
import time
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(ARGS[0])
WHAT = set(ARGS[1:]) or {"mesh", "camera"}
MOD = "bl_ext.user_default.comfyq_bridge"
OUT.mkdir(parents=True, exist_ok=True)
report: dict = {"started": time.strftime("%H:%M:%S"), "runs": []}


def log(text, **data):
    print(time.strftime("%H:%M:%S"), "[new_inputs]", text, json.dumps(data, default=str) if data else "", flush=True)


def save():
    (OUT / "result.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def ext(name):
    return importlib.import_module(f"{MOD}.{name}")


def pump_until(pred, timeout, what):
    S = ext("runtime.session").SESSION
    grun = ext("graph.run")
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        S.pump()
        grun.tick_all()
        if pred():
            return True
        time.sleep(0.2)
    log(f"timeout: {what}")
    return False


def scene_setup():
    sc = bpy.context.scene
    for ob in list(sc.objects):
        if ob.type == "MESH":
            bpy.data.objects.remove(ob)
    bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 0))
    m = bpy.context.active_object
    m.modifiers.new("Subdivision", "SUBSURF").levels = 2
    bpy.ops.object.shade_smooth()
    cam = sc.camera
    cam.location = (0, -4.2, 0.3)                    # straight at the face: the "front" of the picture
    cam.rotation_euler = (1.5, 0, 0)
    sc.render.resolution_x = sc.render.resolution_y = 1024
    bpy.ops.object.select_all(action="DESELECT")
    m.select_set(True)
    bpy.context.view_layer.objects.active = m
    return m


def faces(path: Path) -> int:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    n = sum(len(o.data.polygons) for o in new if o.type == "MESH")
    uv = any(o.type == "MESH" and len(o.data.uv_layers) for o in new)
    for o in new:
        bpy.data.objects.remove(o)
    return n, uv


def run_graph(tree, target, selected, what):
    grun = ext("graph.run")
    t0 = time.monotonic()
    ok, msg = grun.start(tree, bpy.context.scene, [target.name], selected)
    log(f"{what}: Run Graph", ok=ok, msg=msg)
    if not ok:
        return {"what": what, "ok": False, "error": msg}
    notes = {}

    def done():
        for n in tree.nodes:
            if getattr(n, "note", "") and notes.get(n.name) != n.note:
                notes[n.name] = n.note
                log(f"  {n.name}: {n.note}")
        return tree.name not in grun.RUNS
    pump_until(done, 3600, what)
    res = {"what": what, "seconds": round(time.monotonic() - t0), "nodes": {}}
    for n in tree.nodes:
        if n.bl_idname == "ComfyQWorkflowNode":
            res["nodes"][n.name] = {"workflow": n.workflow_id, "job": n.job_id, "result": n.result, "note": n.note}
    res["ok"] = bool(target.result) and Path(target.result).is_file()
    log(f"{what}: done", **res)
    return res


def wf_node(tree, wf_id, x):
    S = ext("runtime.session").SESSION
    n = tree.nodes.new("ComfyQWorkflowNode")
    n.workflow_id, n.location = wf_id, (x, 0)
    n.build(S.specs[wf_id])
    return n


def main():
    addon_utils.enable(MOD, default_set=True)
    S = ext("runtime.session").SESSION
    prefs = bpy.context.preferences.addons[MOD].preferences
    prefs.solo_mode = True                          # sandbox profile only
    log("solo", on=ext("graph.run").solo_on())
    monkey = scene_setup()
    S.connect(prefs.profile, "gpu-check", prefs.url.strip())
    if not pump_until(lambda: S.connected and S.workflows, 60, "connect"):
        raise SystemExit("cannot connect")
    for w in S.workflows:
        if w.id in ("3d_mesh_remesh_decimate", "3d_mesh_uv_unwrap", "image_edit_qwen_2511_multiple_angles"):
            S.specs[w.id] = S.client.params(w.id)
    report["served_before"] = sorted(S.served_ids())
    tree = bpy.data.node_groups.new("GPU check", "ComfyQGraphTree")
    set_cam = ext("ops.workflow").set_camera

    if "mesh" in WHAT:
        src = tree.nodes.new("ComfyQSceneObjectsNode")
        src.location = (-500, 0)
        remesh = wf_node(tree, "3d_mesh_remesh_decimate", 0)
        uv = wf_node(tree, "3d_mesh_uv_unwrap", 500)
        sk = ext("graph.tree").socket_kind
        tree.links.new(src.outputs[0], next(i for i in remesh.inputs if sk(i) == "model"))
        tree.links.new(remesh.outputs[0], next(i for i in uv.inputs if sk(i) == "model"))
        glb = ext("passes.mesh").mesh_pass([monkey])
        report["mesh_in"] = {"faces": faces(glb)[0]}
        r = run_graph(tree, uv, [monkey.name], "mesh chain")
        for d in r["nodes"].values():
            p = Path(d["result"]) if d["result"] else None
            if p and p.suffix.lower() in (".glb", ".gltf"):
                d["faces"], d["has_uv"] = faces(p)
            job = S.jobs.get(d["job"])
            d["files"] = [str(f) for f in job.files] if job else []
        report["runs"].append(r)
        save()
        for n in (src, remesh, uv):
            tree.nodes.remove(n)

    if "camera" in WHAT:
        wf = "image_edit_qwen_2511_multiple_angles"
        spec = next(s for s in S.specs[wf] if s.kind == "camera")
        rnd = tree.nodes.new("ComfyQSceneRenderNode")
        rnd.location, rnd.style, rnd.frame = (-500, 0), "CLAY", "CURRENT"
        node = wf_node(tree, wf, 0)
        img = next(i for i in node.inputs if ext("graph.tree").socket_kind(i) == "image")
        tree.links.new(rnd.outputs[0], img)
        seed = next(s for s in S.specs[wf] if s.kind == "seed")
        node.item(seed.key).randomize, node.item(seed.key).i = False, 7
        for h in (90, 180):
            set_cam(node.item(spec.key), {"horizontal": h, "vertical": 0, "zoom": 5}, spec)
            node.result = ""
            r = run_graph(tree, node, [monkey.name], f"camera Around {h}")
            r["camera"] = node.item(spec.key).value()
            r["shot"] = ext("client.camera").describe(spec.extra.get("camera"), json.loads(r["camera"]))
            job = S.jobs.get(node.job_id)
            r["files"] = [str(f) for f in job.files] if job else []
            report["runs"].append(r)
            save()

    report["served_after"] = sorted(S.served_ids())
    report["ended"] = time.strftime("%H:%M:%S")
    save()
    log("DONE", ok=[r.get("ok") for r in report["runs"]])
    S.close()


try:
    main()
except SystemExit as e:
    log("STOPPED", reason=str(e))
    report["stopped"] = str(e)
    save()
