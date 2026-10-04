# SPDX-License-Identifier: GPL-3.0-or-later
"""The tools AI agents get: `comfyq_*` (the ComfyQ server through this add-on's connection) and
`blender_*` (the open scene and its ComfyQ graph). Scoped actions only — no "run any Python".

Tools run on the server's threads. Anything touching bpy goes through `on_main()` (queued to the
add-on timer, like every other result here); long waits (jobs) never block Blender.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import bpy

from ..client.http import ComfyQError
from ..runtime import serving
from ..runtime.session import SESSION, cache_dir
from .server import Tool, ToolError, obj

MAX_WAIT_S = 120


def on_main(fn, timeout: float = 60.0):
    """Run `fn` on Blender's main thread and return its result."""
    if threading.current_thread() is threading.main_thread():
        return fn()
    done, box = threading.Event(), {}

    def run():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001 - re-raised in the server thread
            box["e"] = e
        finally:
            done.set()
    SESSION.later(run)
    if not done.wait(timeout):
        raise ToolError("Blender did not answer in time (busy, or a dialog is open)")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _client():
    if SESSION.client is None or not SESSION.connected:
        raise ToolError("Blender is not connected to ComfyQ (press Connect in the ComfyQ bar)")
    return SESSION.client


def _job_dict(j) -> dict:
    return {"job_id": j.id, "workflow_id": j.workflow_id, "phase": j.phase, "done": j.done, "ok": j.ok,
            "progress": j.progress, "error": j.error, "outputs": [o.filename for o in j.outputs]}


# -- comfyq_* -------------------------------------------------------------------------------------------
def comfyq_list_workflows(args: dict) -> list[dict]:
    from ..graph import kinds
    from ..ops.workflow import shared_budget, vram_badge
    from ..props import tab_of
    _client()
    served, budget = SESSION.served_ids(), shared_budget()
    out = []
    for w in SESSION.workflows:
        if args.get("served_only", True) and w.id not in served:
            continue
        if args.get("category") and args["category"].lower() not in (w.category.lower(), tab_of(w).lower()):
            continue
        out.append({"id": w.id, "name": w.name, "category": w.category, "group": tab_of(w),
                    "served": w.id in served, "makes": kinds.output_kind(w.category, w.tags, w.id),
                    "vram": vram_badge(w, budget)[0], "unavailable": bool(w.unavailable),
                    "description": (w.description or "")[:300]})
    return out


def _specs(wf_id: str):
    specs = SESSION.specs.get(wf_id)
    if specs is None:
        if not any(w.id == wf_id for w in SESSION.workflows):
            raise ToolError(f"Unknown workflow: {wf_id} (see comfyq_list_workflows)")
        specs = _client().params(wf_id)
        SESSION.specs[wf_id] = specs
    return specs


def comfyq_describe_workflow(args: dict) -> dict:
    wf_id = args["workflow_id"]
    w = next((w for w in SESSION.workflows if w.id == wf_id), None)
    specs = _specs(wf_id)
    params = []
    for s in specs:
        p = {"key": s.key, "kind": s.kind, "label": s.label, "default": s.default, "required": s.required}
        if s.min is not None or s.max is not None:
            p["min"], p["max"] = s.min, s.max
        if s.step:
            p["step"] = s.step
        if s.options:
            p["options"] = list(s.options)
        if s.disabled_when:
            p["disabled_when"] = s.disabled_when
        if s.is_media:
            p["give_a_local_file_in"] = "files"
        params.append(p)
    return {"id": wf_id, "name": w.name if w else wf_id, "description": w.description if w else "",
            "served": wf_id in SESSION.served_ids(), "parameters": params}


def comfyq_submit(args: dict) -> dict:
    from ..ops.generate import book
    wf_id = args["workflow_id"]
    if wf_id not in SESSION.served_ids():
        raise ToolError(f"{wf_id} is not served right now (only served workflows run; ask the teacher)")
    specs = _specs(wf_id)
    by_key = {s.key: s for s in specs}
    values = dict(args.get("params") or {})
    files = {k: Path(v) for k, v in (args.get("files") or {}).items()}
    bad = [k for k in [*values, *files] if k not in by_key]
    if bad:
        raise ToolError(f"Unknown parameter(s) {bad}; see comfyq_describe_workflow")
    for k, p in files.items():
        if not by_key[k].is_media:
            raise ToolError(f"“{k}” is not a media input")
        if not p.is_file():
            raise ToolError(f"No such file: {p}")
    prompt_key = next((s.key for s in specs if s.kind == "prompt"), None)
    if args.get("prompt") and prompt_key:
        values.setdefault(prompt_key, args["prompt"])        # the workflow's main prompt (also labels the job)
    got, box = threading.Event(), {}

    def start():
        book(bpy.context.scene, wf_id, specs, values, files, purpose="agent", label=f"Agent · {wf_id}",
             on_booked=lambda jid: (box.update(job_id=jid), got.set()),
             on_error=lambda msg: (box.update(error=msg), got.set()))
    on_main(start)
    if not got.wait(120):
        raise ToolError("Booking did not answer within 120 s")
    if "error" in box:
        raise ToolError(box["error"])
    sent = {**values, **{k: str(v) for k, v in files.items()}}
    return {"job_id": box["job_id"], "sent": sent, "prompt_param": prompt_key,
            "note": "Unset settings use the workflow's defaults. Next: comfyq_wait, comfyq_outputs, blender_import"}


def comfyq_job_status(args: dict) -> dict:
    return _job_dict(_client().job(args["job_id"]))


def comfyq_wait(args: dict) -> dict:
    timeout = min(float(args.get("timeout_s", MAX_WAIT_S)), MAX_WAIT_S)
    client, deadline = _client(), time.monotonic() + timeout
    while True:
        j = client.job(args["job_id"])
        if j.done or time.monotonic() > deadline:
            d = _job_dict(j)
            if not j.done:
                d["note"] = f"still running after {timeout:.0f} s: call comfyq_wait again"
            return d
        time.sleep(2.0)


def comfyq_cancel(args: dict) -> dict:
    _client().cancel(args["job_id"])
    return {"cancelled": args["job_id"]}


def comfyq_outputs(args: dict) -> dict:
    j = _client().job(args["job_id"])
    if not j.done:
        raise ToolError(f"Job is {j.phase}; wait for it first (comfyq_wait)")
    if not j.ok:
        raise ToolError(f"Job {j.phase}: {j.error or 'no result'}")
    view = SESSION.jobs.get(j.id)
    files = list(view.files) if view is not None and view.files else \
        _client().download_outputs(j, cache_dir() / j.id[:8])
    return {"job_id": j.id, "files": [str(f) for f in files]}


# -- teacher: what ComfyQ serves (behind the "Teacher tools" preference) ---------------------------------
TEACHER_OFF = ("Teacher tools are off. In Blender: Preferences › Add-ons › ComfyQ Bridge › tick "
               "“Teacher tools: agents may load models”, then reconnect the agent (/mcp in Claude Code).")
TASK: dict[str, Any] = {}          # the one serve/calibrate running in the background (one at a time)
_TASK_LOCK = threading.Lock()
SERVE_TIMEOUT_S = 900              # a main-workflow switch restarts ComfyQ; loading a model takes 1–3 min


def _teacher_on() -> bool:
    from . import STATE
    return bool(STATE.get("teacher"))


def _task_dict() -> dict | None:
    if not TASK:
        return None
    out = {k: TASK.get(k) for k in ("kind", "workflow_id", "how", "state", "message", "result")}
    out["seconds"] = round((TASK.get("ended") or time.time()) - TASK["started"])
    return out


def _start_task(kind: str, wf_id: str, work, **extra) -> None:
    with _TASK_LOCK:
        if TASK.get("state") == "running":
            raise ToolError(f"Busy: {TASK['kind']} of {TASK['workflow_id']} is still running "
                            f"(follow it with comfyq_server_status)")
        TASK.clear()
        TASK.update(kind=kind, workflow_id=wf_id, state="running", message="", result=None,
                    started=time.time(), ended=None, **extra)

    def go():
        try:
            msg, result = work()
            TASK.update(state="done", message=msg, result=result)
        except Exception as e:  # noqa: BLE001 - reported through comfyq_server_status
            TASK.update(state="failed", message=str(e))
        finally:
            TASK["ended"] = time.time()
    threading.Thread(target=go, daemon=True, name=f"comfyq-{kind}").start()


def _wait_task(wait_s: float) -> dict | None:
    t0 = time.monotonic()
    while TASK.get("state") == "running" and time.monotonic() - t0 < wait_s:
        time.sleep(1)
    return _task_dict()


def _task_reply(wait_s: float, note: str) -> dict:
    task = _wait_task(min(float(wait_s), MAX_WAIT_S))
    return {"task": task, "note": note if task and task["state"] == "running" else ""}


def _workflow(wf_id: str):
    wf = next((w for w in SESSION.workflows if w.id == wf_id), None)
    if wf is None:
        raise ToolError(f"Unknown workflow {wf_id!r} (comfyq_list_workflows served_only=false lists them)")
    if wf.unavailable:
        raise ToolError(f"{wf_id} is unavailable on this ComfyQ: {wf.unavailable_reason or 'models missing'}")
    return wf


def comfyq_server_status(args: dict) -> dict:
    client = _client()
    if TASK.get("state") == "running" and args.get("wait_s"):
        _wait_task(min(float(args["wait_s"]), MAX_WAIT_S))
    info = client.server_lanes()
    fit = info.get("fit") or {}
    card = info.get("card") or {}
    out: dict[str, Any] = {
        "card_gb": card.get("vramGb"), "used_gb": card.get("usedGb"),
        "served": [{"workflow_id": ln.get("workflowId"), "name": ln.get("name"), "main": bool(ln.get("primary")),
                    "busy": bool(ln.get("busy")), "state": ln.get("state"), "vram_gb": ln.get("vramGb")}
                   for ln in info.get("lanes") or []],
        "fits_alongside_now": sorted(k for k, v in fit.items() if v.get("ok")),
        "teacher_tools": _teacher_on(),
        "task": _task_dict(),
    }
    if args.get("workflow_id"):
        f = fit.get(args["workflow_id"]) or {}
        out["workflow"] = {"workflow_id": args["workflow_id"], "fits_alongside": bool(f.get("ok")),
                           "reason": f.get("reason"), "needs_gb": f.get("needGb"), "free_gb": f.get("freeGb")}
    return out


def comfyq_serve(args: dict) -> dict:
    wf_id, how, force = args["workflow_id"], args.get("how") or "auto", bool(args.get("force"))
    client = _client()
    _workflow(wf_id)
    if wf_id in SESSION.served_ids():
        return {"workflow_id": wf_id, "served": True, "message": "already served"}
    try:
        how = serving.choose(client, wf_id, how, force)
    except ComfyQError as e:
        raise ToolError(f"{e} (pass force=true to overrule)") from e

    def work():
        serving.serve(client, wf_id, how, force)
        if how == "main":
            time.sleep(5)                                  # ComfyQ goes down; don't read the old lanes
        deadline = time.monotonic() + SERVE_TIMEOUT_S
        while time.monotonic() < deadline:
            if SESSION.connected and wf_id in SESSION.served_ids():
                return f"{wf_id} is served ({how})", None
            time.sleep(2)
        raise ToolError(f"{wf_id} still not served after {SERVE_TIMEOUT_S // 60} min (see ComfyQ's console)")
    _start_task("serve", wf_id, work, how=how)
    return _task_reply(args.get("wait_s", 60), "Still loading: follow with comfyq_server_status (wait_s).")


def comfyq_unserve(args: dict) -> dict:
    wf_id = args["workflow_id"]
    client = _client()
    lane = next((ln for ln in client.server_lanes().get("lanes") or [] if ln.get("workflowId") == wf_id), None)
    if lane is None:
        raise ToolError(f"{wf_id} is not served")
    if lane.get("primary"):
        raise ToolError(f"{wf_id} is the main workflow: replace it with comfyq_serve how='main' instead")
    if lane.get("busy") and not args.get("force"):
        raise ToolError(f"A job is running on {wf_id}: wait for it, or pass force=true")
    client.close_lane(wf_id)
    return {"workflow_id": wf_id, "served": False, "message": "lane stopped, its VRAM is free"}


def comfyq_calibrate(args: dict) -> dict:
    wf_id = args["workflow_id"]
    client = _client()
    _workflow(wf_id)

    def work():
        res = client.calibrate(wf_id)
        if not res.get("ok", True):
            raise ToolError(res.get("error") or "calibration failed")
        wfs = client.workflows()                            # new VRAM badge / what fits, for the bar too
        SESSION.later(lambda: setattr(SESSION, "workflows", wfs))
        return f"{wf_id} calibrated", res.get("runtime")
    _start_task("calibrate", wf_id, work)
    return _task_reply(args.get("wait_s", 60), "The GPU is busy for minutes: follow with comfyq_server_status.")


# -- blender_* --------------------------------------------------------------------------------------------
def blender_scene_info(args: dict) -> dict:
    def get():
        from ..ops.workflow import scene_facts
        sc = bpy.context.scene
        f = scene_facts(sc)
        return {"file": bpy.data.filepath or "(unsaved)", "scene": sc.name,
                "frames": [f.frame_start, f.frame_end], "fps": f.fps, "seconds": round(f.seconds, 2),
                "resolution": [sc.render.resolution_x, sc.render.resolution_y],
                "camera": sc.camera.name if sc.camera else None,
                "selected": [o.name for o in bpy.context.selected_objects] if bpy.context.view_layer else [],
                "objects": [{"name": o.name, "type": o.type, "location": [round(v, 3) for v in o.location],
                             "animated": bool(o.animation_data and o.animation_data.action)}
                            for o in sc.objects][:200],
                "comfyq": {"connected": SESSION.connected, "bar_workflow": sc.comfyq.workflow_id}}
    return on_main(get)


def blender_select(args: dict) -> dict:
    def sel():
        names = args["names"]
        missing = [n for n in names if n not in bpy.context.scene.objects]
        if missing:
            raise ToolError(f"No such object(s) in the scene: {missing}")
        for o in bpy.context.scene.objects:
            o.select_set(o.name in names)
        return {"selected": names}
    return on_main(sel)


def _cycles(edge: int | None, frame: int | None, samples: int, progress=None) -> Path:
    """A Cycles still of the open scene: started on Blender's main thread, waited for here (the agent's
    thread), so Blender stays responsive while the separate Blender renders."""
    from ..passes import render
    job = on_main(lambda: render.CyclesRender(bpy.context.scene, edge, frame, samples, tag="agent"))
    while True:
        out = job.poll()
        if out is not None:
            return out
        if progress is not None:
            progress(job.text())
        time.sleep(0.5)


def blender_render(args: dict) -> dict:
    from ..passes import animation, render
    kind, style = args.get("kind", "still"), args.get("style", "CLAY")
    styles = render.STILL_STYLES if kind == "still" else render.STYLES
    if style not in {s[0] for s in styles}:
        raise ToolError(f"style for a {kind} must be one of {[s[0] for s in styles]}")
    edge = int(args.get("max_edge", 1280))
    if kind == "still" and style == "CYCLES":
        frame = args.get("frame")
        try:
            return {"path": str(_cycles(edge, None if frame is None else int(frame), int(args.get("samples", 64))))}
        except RuntimeError as e:
            raise ToolError(str(e)) from e

    def go():
        sc = bpy.context.scene
        frame = args.get("frame")
        if kind == "animation":
            keep = sc.frame_end
            if args.get("max_seconds"):                  # a short test: only the first seconds
                fps = sc.render.fps / (sc.render.fps_base or 1.0)
                sc.frame_end = min(keep, sc.frame_start + max(1, round(float(args["max_seconds"]) * fps)) - 1)
            try:
                p = animation.animation_pass(sc, style, edge, force=True)
            finally:
                sc.frame_end = keep
            return {"path": str(p.path), "frames": p.frames, "seconds": round(p.seconds, 2), "size": list(p.size)}
        if kind == "mask":
            sel = list(bpy.context.selected_objects)
            if not sel:
                raise ToolError("Select the object(s) to repaint first (blender_select)")
            return {"path": str(render.mask_pass(sc, sel, style, edge, frame))}
        return {"path": str(render.structure_pass(sc, style, edge, frame, int(args.get("samples", 64))))}
    return on_main(go, timeout=1900)


def blender_import(args: dict) -> dict:
    from ..importers.media import Placement, import_files
    p = Path(args["path"])
    if not p.is_file():
        raise ToolError(f"No such file: {p}")
    placement = Placement(args.get("place", "BESIDE"), bool(args.get("on_ground", True)), float(args.get("size", 0)))

    def go():
        view = SESSION.jobs.get(args.get("job_id", ""))
        return {"imported": import_files([p], args.get("job_id", "") or "agent",
                                         view.job.workflow_id if view else "", bpy.context.scene, placement)}
    return on_main(go)


def _builtin_hdri(name: str) -> Path | None:
    folder = Path(bpy.utils.system_resource("DATAFILES", path="studiolights/world"))
    p = folder / f"{name}.exr"
    return p if p.is_file() else None


def blender_world(args: dict) -> dict:
    """Light the scene with an HDRI (world environment), or clear it. Our nodes are named so a new call
    updates them instead of piling up."""
    import math
    src = str(args.get("hdri") or "")
    if args.get("clear"):
        path = None
    elif src.startswith("builtin:"):
        path = _builtin_hdri(src.split(":", 1)[1])
        if path is None:
            folder = Path(bpy.utils.system_resource("DATAFILES", path="studiolights/world"))
            raise ToolError(f"No built-in HDRI {src!r}: {sorted(p.stem for p in folder.glob('*.exr'))}")
    else:
        path = Path(src).expanduser()
        if not path.is_file():
            raise ToolError(f"No HDRI file at {path} (or use builtin:forest, builtin:city…)")

    def go():
        scene = bpy.context.scene
        world = scene.world or bpy.data.worlds.new("World")
        scene.world = world
        try:                                                # (always on, and deprecated, in Blender 5)
            if not world.use_nodes:
                world.use_nodes = True
        except AttributeError:
            pass
        nt = world.node_tree
        out = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputWorld"), None) or \
            nt.nodes.new("ShaderNodeOutputWorld")
        bg = nt.nodes.get("ComfyQ Background") or next(
            (n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground"), None) or nt.nodes.new("ShaderNodeBackground")
        bg.name = "ComfyQ Background"
        nt.links.new(bg.outputs[0], out.inputs["Surface"])
        if path is None:                                    # back to a plain grey world
            for name in ("ComfyQ HDRI", "ComfyQ Mapping", "ComfyQ Coordinates"):
                if nt.nodes.get(name):
                    nt.nodes.remove(nt.nodes[name])
            bg.inputs["Color"].default_value = (0.05, 0.05, 0.05, 1.0)
            bg.inputs["Strength"].default_value = 1.0
            scene.render.film_transparent = False
            return {"world": world.name, "hdri": None}
        env = nt.nodes.get("ComfyQ HDRI") or nt.nodes.new("ShaderNodeTexEnvironment")
        env.name = env.label = "ComfyQ HDRI"
        env.image = bpy.data.images.load(str(path), check_existing=True)
        mapping = nt.nodes.get("ComfyQ Mapping") or nt.nodes.new("ShaderNodeMapping")
        mapping.name = mapping.label = "ComfyQ Mapping"
        coords = nt.nodes.get("ComfyQ Coordinates") or nt.nodes.new("ShaderNodeTexCoord")
        coords.name = coords.label = "ComfyQ Coordinates"
        nt.links.new(coords.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
        mapping.inputs["Rotation"].default_value[2] = math.radians(float(args.get("rotation_deg", 0)))
        bg.inputs["Strength"].default_value = float(args.get("strength", 1.0))
        scene.render.film_transparent = not bool(args.get("background", True))
        for n, x in ((coords, -900), (mapping, -700), (env, -450), (bg, -150), (out, 100)):
            n.location = (x, 300)
        return {"world": world.name, "hdri": str(path), "size": list(env.image.size),
                "strength": bg.inputs["Strength"].default_value, "rotation_deg": float(args.get("rotation_deg", 0)),
                "background_in_renders": not scene.render.film_transparent,
                "note": "Seen in Material Preview / Rendered viewport and EEVEE / Cycles renders. The bridge's "
                        "clay / matcap passes (what ComfyQ gets) are Workbench and do not use it."}
    return on_main(go)


CATCHER = "ComfyQ Shadow Catcher"


def _catcher_material(strength: float, lit: float = 1.0):
    """Two outputs. Cycles: a plain white diffuse — the object's Shadow Catcher flag does the rest (exact).
    EEVEE has no such flag: transparent where lit, black (with alpha) in shadow: lit diffuse → Shader to
    RGB → darkness = 1 − brightness / `lit` (how bright the fully lit ground is) → × strength → mix
    Transparent / black. Shader to RGB is EEVEE-only (black in Cycles), hence the separate outputs."""
    mat = bpy.data.materials.get(CATCHER) or bpy.data.materials.new(CATCHER)
    try:
        if not mat.use_nodes:
            mat.use_nodes = True
    except AttributeError:
        pass
    for attr, value in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(mat, attr, value)
            break
        except (AttributeError, TypeError):
            continue
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.target = "EEVEE"
    cycles_out = nt.nodes.new("ShaderNodeOutputMaterial")
    cycles_out.target = "CYCLES"
    cycles_diffuse = nt.nodes.new("ShaderNodeBsdfDiffuse")
    nt.links.new(cycles_diffuse.outputs[0], cycles_out.inputs["Surface"])
    cycles_diffuse.location, cycles_out.location = (600, -400), (900, -400)
    diffuse = nt.nodes.new("ShaderNodeBsdfDiffuse")
    to_rgb = nt.nodes.new("ShaderNodeShaderToRGB")
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    ratio = nt.nodes.new("ShaderNodeMath")
    ratio.name = "Lit level"
    ratio.operation = "DIVIDE"
    ratio.inputs[1].default_value = max(lit, 1e-3)
    dark = nt.nodes.new("ShaderNodeMath")
    dark.operation, dark.use_clamp = "SUBTRACT", True
    dark.inputs[0].default_value = 1.0
    amount = nt.nodes.new("ShaderNodeMath")
    amount.name = "Strength"
    amount.operation, amount.use_clamp = "MULTIPLY", True
    amount.inputs[1].default_value = strength
    mix = nt.nodes.new("ShaderNodeMixShader")
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    black = nt.nodes.new("ShaderNodeEmission")
    black.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    links = nt.links
    links.new(diffuse.outputs[0], to_rgb.inputs[0])
    links.new(to_rgb.outputs["Color"], bw.inputs[0])
    links.new(bw.outputs[0], ratio.inputs[0])
    links.new(ratio.outputs[0], dark.inputs[1])
    links.new(dark.outputs[0], amount.inputs[0])
    links.new(amount.outputs[0], mix.inputs[0])
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(black.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    for x, n in enumerate((diffuse, to_rgb, bw, ratio, dark, amount, mix, out)):
        n.location = (x * 200 - 600, 0)
    transparent.location, black.location = (400, 200), (400, -200)
    return mat


def blender_shadow_catcher(args: dict) -> dict:
    """Make objects (or a new ground plane) catch shadows only: Cycles' Shadow Catcher flag + an EEVEE
    material. `remove` undoes it (flag off, our material taken off)."""
    def go():
        scene = bpy.context.scene
        objs = []
        for name in args.get("names") or []:
            o = bpy.data.objects.get(name)
            if o is None:
                raise ToolError(f"No object {name!r} (see blender_scene_info)")
            objs.append(o)
        if args.get("collection"):
            coll = bpy.data.collections.get(args["collection"])
            if coll is None:
                raise ToolError(f"No collection {args['collection']!r}: {[c.name for c in bpy.data.collections]}")
            objs += [o for o in coll.all_objects if o.type == "MESH" and o not in objs]
        if args.get("plane") and not args.get("remove"):
            from ..importers.media import _bbox
            box = _bbox([o for o in scene.objects if o.type == "MESH" and o.visible_get()])
            lo, hi = box if box else ((-5, -5, 0), (5, 5, 0))
            size = max(10.0, 4 * max(hi[0] - lo[0], hi[1] - lo[1]))
            mesh = bpy.data.meshes.new(CATCHER)
            h = size / 2
            mesh.from_pydata([(-h, -h, 0), (h, -h, 0), (h, h, 0), (-h, h, 0)], [], [(0, 1, 2, 3)])
            plane = bpy.data.objects.new(CATCHER, mesh)
            plane.location = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, min(lo[2], 0.0) if box else 0.0)
            scene.collection.objects.link(plane)
            objs.append(plane)
        if not objs:
            raise ToolError("Give `names`, a `collection`, or `plane: true`")
        mat = None if args.get("remove") else _catcher_material(float(args.get("strength", 1.0)),
                                                                 float(args.get("eevee_lit", 1.0)))
        done = []
        for o in objs:
            if o.type != "MESH":
                continue
            o.is_shadow_catcher = not args.get("remove")
            o.visible_shadow = bool(args.get("remove"))           # a catcher casts no shadow itself
            slots = o.data.materials
            if args.get("remove"):
                for i in reversed(range(len(slots))):
                    if slots[i] is not None and slots[i].name == CATCHER:
                        slots.pop(index=i)
            else:
                slots.clear()
                slots.append(mat)
            done.append(o.name)
        return {"objects": done, "shadow_catcher": not args.get("remove"),
                "note": "Cycles: Blender's Shadow Catcher flag (exact). EEVEE: an approximating material; if no "
                        "shadow shows, call again with a higher eevee_lit (e.g. 1.5 under a bright HDRI + sun); if "
                        "the whole ground darkens, lower it. The objects' own materials are replaced; remove: "
                        "true takes ours off."}
    return on_main(go)


def blender_transform(args: dict) -> dict:
    """Move / rotate / scale objects (e.g. an imported model and its parent empty)."""
    import math

    def go():
        out = {}
        for name in args["names"]:
            o = bpy.data.objects.get(name)
            if o is None:
                raise ToolError(f"No object {name!r} (see blender_scene_info)")
            if "location" in args:
                o.location = args["location"]
            if "rotation_deg" in args:
                o.rotation_euler = [math.radians(a) for a in args["rotation_deg"]]
            if "scale" in args:
                s = args["scale"]
                o.scale = (s, s, s) if isinstance(s, (int, float)) else s
            if args.get("hide") is not None:
                o.hide_set(bool(args["hide"]))
                o.hide_render = bool(args["hide"])
            out[name] = {"location": [round(v, 3) for v in o.location],
                         "rotation_deg": [round(math.degrees(v), 1) for v in o.rotation_euler],
                         "scale": [round(v, 3) for v in o.scale], "hidden": o.hide_get()}
        return out
    return on_main(go)


# -- blender_graph_* ------------------------------------------------------------------------------------
def blender_segment(args: dict) -> dict:
    """SAM3 by name on a render of the scene (or a given picture), in the background: one job per label."""
    from ..passes import render
    from ..passes import segment as seg
    from ..runtime import segmentation
    from ..surfaces import labels_from, surface_prompt
    client = _client()
    if segmentation.SAM3_ID not in SESSION.served_ids():
        raise ToolError(f"{segmentation.SAM3_ID} is not served (teacher: comfyq_serve it first)")
    labels = labels_from(args.get("labels"))
    image = Path(args["image"]) if args.get("image") else None
    if image is not None and not image.is_file():
        raise ToolError(f"No such file: {image}")
    style, samples = args.get("style", "CYCLES"), int(args.get("samples", 64))
    if image is None and style not in {s[0] for s in render.STILL_STYLES}:
        raise ToolError(f"style must be one of {[s[0] for s in render.STILL_STYLES]}")
    threshold = args.get("threshold")

    def work():
        out = seg.work_dir()
        edge = int(args.get("max_edge", 1920))
        if image is not None:
            pic = image
        elif style == "CYCLES":
            pic = _cycles(edge, None, samples, progress=lambda m: TASK.update(message=m))
        else:
            pic = on_main(lambda: render.structure_pass(bpy.context.scene, style, edge, None, samples), timeout=300)
        res = segmentation.segment(client, Path(pic), labels, out, lambda fn: on_main(fn, timeout=300),
                                   None if threshold is None else float(threshold),
                                   progress=lambda m: TASK.update(message=m))
        regions = res.pop("regions")
        res["regions"] = segmentation.region_dicts(regions)
        res["found"] = [r.label for r in sorted(regions, key=lambda r: -r.coverage) if r.found]
        res["prompt"] = surface_prompt(regions, args.get("materials"), args.get("intro", ""), args.get("outro", ""))
        if args.get("show", True) and res["overlay"]:
            from ..importers.media import import_files
            on_main(lambda: import_files([Path(res["overlay"])], "segment", segmentation.SAM3_ID, bpy.context.scene))
        return f"{len(res['found'])}/{len(labels)} labels found", res
    _start_task("segment", segmentation.SAM3_ID, work)
    return _task_reply(args.get("wait_s", 60), "Still segmenting: follow with blender_task_status.")


def blender_task_status(args: dict) -> dict:
    if TASK.get("state") == "running" and args.get("wait_s"):
        _wait_task(min(float(args["wait_s"]), MAX_WAIT_S))
    return {"task": _task_dict()}


def blender_mask_image(args: dict) -> dict:
    """An inpaint input: the picture with the surfaces' masks made transparent (= what gets repainted)."""
    from ..passes import segment as seg
    image = Path(args["image"])
    masks = [Path(m) for m in args["masks"]]
    for p in (image, *masks):
        if not p.is_file():
            raise ToolError(f"No such file: {p}")

    def go():
        out = seg.work_dir("masks") / f"{image.stem}_{'_'.join(m.stem for m in masks)[:60]}.png"
        return {"path": str(seg.repaint_mask(image, masks, out, int(args.get("grow_px", 6)),
                                             bool(args.get("invert", False))))}
    return on_main(go)


def _tree():
    from ..graph.ui import ensure_graph
    return ensure_graph()


def _node_dict(n) -> dict:
    from ..graph.tree import WORKFLOW_NODE, upstream
    d: dict[str, Any] = {"name": n.name, "type": n.bl_idname.replace("ComfyQ", "").replace("Node", ""),
                         "label": n.label}
    ins = {}
    for s in n.inputs:
        src, _sock = upstream(s)
        if src is not None:
            ins[s.identifier] = src.name
    if ins:
        d["inputs"] = ins
    if n.bl_idname == WORKFLOW_NODE:
        d.update(workflow_id=n.workflow_id, result=n.result or None, job_id=n.job_id or None,
                 settings={it.key: it.value() for it in n.params if it.workflow_id == n.workflow_id},
                 note=n.note or None)
    elif n.bl_idname == "ComfyQPromptNode":
        d["text"] = n.value()
    elif n.bl_idname == "ComfyQSurfacesNode":
        d.update(surfaces=n.value(), intro=n.intro, outro=n.outro, threshold=round(n.threshold, 3),
                 found=[f"{r['label']} {r['coverage_pct']}%" for r in n.found()], prompt=n.prompt() or None,
                 overlay=n.result or None, note=n.note or None)
    for k in ("style", "frame", "filepath", "auto", "folder", "status"):
        if hasattr(n, k) and k in n.bl_rna.properties:
            d[k] = getattr(n, k)
    return d


def blender_graph_describe(args: dict) -> dict:
    def go():
        t = _tree()
        from ..graph.run import RUNS
        return {"graph": t.name, "running": t.name in RUNS,
                "nodes": [_node_dict(n) for n in t.nodes if n.bl_idname not in ("NodeFrame",)]}
    return on_main(go)


def blender_graph_add_preset(args: dict) -> dict:
    from ..graph import preset_ops, presets
    key = args["preset"]
    if not any(p.id == key for p in presets.BUILTIN) and not Path(key).is_file():
        saved = {h["label"]: str(f) for f, h in preset_ops.saved_presets()}
        if key not in saved:
            raise ToolError(f"Unknown preset {key!r}: built in {[p.id for p in presets.BUILTIN]}, "
                            f"saved {sorted(saved)}")
        key = saved[key]
    got, box = threading.Event(), {}

    def start():
        t = _tree()
        preset_ops.add_preset(t.name, bpy.context.scene.name, key,
                              lambda ok, msg, names: (box.update(ok=ok, msg=msg, names=names), got.set()))
    on_main(start)
    if not got.wait(60):
        raise ToolError("The preset's workflows did not load within 60 s")
    if not box["ok"]:
        raise ToolError(box["msg"])
    return {"message": box["msg"], "nodes": box["names"]}


def blender_graph_set(args: dict) -> dict:
    """Set a node setting: a workflow node's parameter (key or "prompt"), or a source/output property."""
    def go():
        from ..graph.tree import WORKFLOW_NODE
        n = _tree().nodes.get(args["node"])
        if n is None:
            raise ToolError(f"No node {args['node']!r} (see blender_graph_describe)")
        key, value = args["key"], args["value"]
        if n.bl_idname == WORKFLOW_NODE:
            specs = SESSION.specs.get(n.workflow_id, [])
            if key == "prompt":
                key = next((s.key for s in specs if s.kind == "prompt"), key)
            it = n.item(key)
            if it is None:
                raise ToolError(f"{n.name} has no setting {key!r}: {[s.key for s in specs]}")
            if it.kind in ("int", "seed"):
                it.i = int(value)
                if it.kind == "seed":
                    it.randomize = False
            elif it.kind == "float":
                it.f = float(value)
            elif it.kind == "bool":
                it.b = bool(value)
            else:
                it.text = None
                it.s = str(value)
                spec = next((s for s in specs if s.key == key), None)
                if spec is not None and spec.options and it.s in spec.options:
                    it.e = it.s
            return {n.name: {key: it.value()}}
        if n.bl_idname == "ComfyQPromptNode" and key in ("text", "prompt"):
            n.text_block, n.text = None, str(value)
            return {n.name: {"text": n.text}}
        if n.bl_idname == "ComfyQSurfacesNode" and key in ("text", "surfaces"):
            if n.text_block is None:
                n.text_block = bpy.data.texts.new(f"ComfyQ surfaces · {n.name}")
            n.text_block.clear()
            n.text_block.write(str(value))
            return {n.name: {"surfaces": n.value(), "prompt_now": n.prompt() or None}}
        if n.bl_idname == "ComfyQImageNode" and key in ("image", "filepath"):
            path = Path(str(value)).expanduser()
            if not path.is_file():
                raise ToolError(f"No image file at {path}")
            n.image = bpy.data.images.load(str(path), check_existing=True)
            return {n.name: {"image": n.image.filepath}}
        if key not in n.bl_rna.properties or key in ("name", "location", "handled", "imported"):
            raise ToolError(f"{n.name}: cannot set {key!r}")
        setattr(n, key, value)
        return {n.name: {key: getattr(n, key)}}
    return on_main(go)


def blender_graph_run(args: dict) -> dict:
    def go():
        from ..graph import engine, run
        t, sc = _tree(), bpy.context.scene
        name = args.get("node") or ""
        if name and t.nodes.get(name) is None:
            raise ToolError(f"No node {name!r}")
        from ..graph.tree import OUTPUT_NODES
        after, targets = [], [name] if name else engine.leaves(run.describe(t, sc))
        if name and t.nodes[name].bl_idname in OUTPUT_NODES:
            after, targets = [name], engine.feeding_workflows(run.describe(t, sc), name)
            if not targets:
                return {"ok": True, "message": "; ".join(run.act_outputs(t, sc, after))}
        ok, msg = run.start(t, sc, targets, [o.name for o in bpy.context.selected_objects],
                            force=bool(args.get("force")), after=after)
        SESSION.status_note = msg
        if not ok:
            raise ToolError(msg)
        return {"ok": True, "message": msg, "note": "Follow it with blender_graph_status"}
    return on_main(go)


def blender_graph_status(args: dict) -> dict:
    def go():
        from ..graph.run import RUNS, running
        from ..graph.tree import WORKFLOW_NODE
        t = _tree()
        out = []
        for n in t.nodes:
            if n.bl_idname == "ComfyQSurfacesNode":
                out.append({"node": n.name, "workflow_id": n.workflow_id, "state": running(t.name, n.name) or "idle",
                            "found": [r["label"] for r in n.found()], "result": n.result or None,
                            "note": n.note or None})
                continue
            if n.bl_idname != WORKFLOW_NODE:
                continue
            v = SESSION.jobs.get(n.job_id) if n.job_id else None
            out.append({"node": n.name, "workflow_id": n.workflow_id, "state": running(t.name, n.name) or "idle",
                        "job": _job_dict(v.job) if v else None, "result": n.result or None, "note": n.note or None})
        run = RUNS.get(t.name)
        return {"running": run is not None, "error": run.error if run else None, "nodes": out,
                "status": SESSION.status_note}
    return on_main(go)


_S = {"type": "string"}
TOOLS = [
    Tool("comfyq_list_workflows", "List ComfyQ workflows (served ones by default: only those can run). "
         "Each has id, name, category/group, what it makes (image/video/model/…), VRAM badge, description.",
         obj({"served_only": {"type": "boolean", "default": True},
              "category": {"type": "string", "description": "t2i, image-edit, i2v, video-edit, 3d, audio… "
                                                            "or IMAGE/VIDEO/MODEL/SPLAT/AUDIO/TOOLS"}}),
         comfyq_list_workflows),
    Tool("comfyq_describe_workflow", "Parameters of a workflow: key, kind, label, default, min/max, options. "
         "Media inputs (image/video/mask/audio) take a local file path in comfyq_submit's `files`.",
         obj({"workflow_id": _S}, ("workflow_id",)), comfyq_describe_workflow),
    Tool("comfyq_submit", "Book a job on a served workflow. `params` = {key: value} settings, `files` = "
         "{key: local file} for media inputs (use blender_render outputs), `prompt` = fills the workflow's "
         "main prompt parameter (unless `params` sets it) and labels the job. Unset settings keep their "
         "defaults. Returns job_id and the values sent. The job also appears in Blender's job list.",
         obj({"workflow_id": _S, "params": {"type": "object"}, "files": {"type": "object"}, "prompt": _S},
             ("workflow_id",)), comfyq_submit),
    Tool("comfyq_job_status", "Phase, progress, error and output names of a job.",
         obj({"job_id": _S}, ("job_id",)), comfyq_job_status),
    Tool("comfyq_wait", f"Wait for a job to end (at most {MAX_WAIT_S} s per call; call again if still running).",
         obj({"job_id": _S, "timeout_s": {"type": "number", "maximum": MAX_WAIT_S}}, ("job_id",)), comfyq_wait),
    Tool("comfyq_cancel", "Cancel one of this student's jobs.", obj({"job_id": _S}, ("job_id",)), comfyq_cancel),
    Tool("comfyq_outputs", "Download a finished job's results; returns local file paths (for blender_import).",
         obj({"job_id": _S}, ("job_id",)), comfyq_outputs),
    Tool("comfyq_server_status", "What the GPU serves now: served workflows (main + lanes, busy, VRAM), card/"
         "used GB, which workflows fit alongside, the running serve/calibrate task. `workflow_id` = also say "
         "whether that one fits; `wait_s` = first wait (≤120 s) for the running task to end.",
         obj({"workflow_id": _S, "wait_s": {"type": "number", "maximum": MAX_WAIT_S}}), comfyq_server_status),
    Tool("comfyq_serve", "TEACHER — load a workflow's models so it can run. how='alongside' opens a lane next "
         "to the main workflow (no restart; must fit in VRAM); how='main' replaces the main workflow (ComfyQ "
         "restarts ~1–2 min for the whole class; refused while a job runs unless force); 'auto' (default) = "
         "alongside if it fits, else main. Waits up to `wait_s` (≤120, default 60), then keeps going in the "
         "background: follow with comfyq_server_status.",
         obj({"workflow_id": _S, "how": {"type": "string", "enum": ["auto", "alongside", "main"]},
              "force": {"type": "boolean"}, "wait_s": {"type": "number", "maximum": MAX_WAIT_S}},
             ("workflow_id",)), comfyq_serve, _teacher_on, TEACHER_OFF),
    Tool("comfyq_unserve", "TEACHER — stop a lane opened alongside the main workflow (frees its VRAM). Not "
         "the main workflow.", obj({"workflow_id": _S, "force": {"type": "boolean"}}, ("workflow_id",)),
         comfyq_unserve, _teacher_on, TEACHER_OFF),
    Tool("comfyq_calibrate", "TEACHER — measure a workflow's VRAM peak and duration on ComfyQ's benchmark "
         "inputs (GPU busy for minutes). Fills its VRAM badge and what fits alongside.",
         obj({"workflow_id": _S, "wait_s": {"type": "number", "maximum": MAX_WAIT_S}}, ("workflow_id",)),
         comfyq_calibrate, _teacher_on, TEACHER_OFF),
    Tool("blender_scene_info", "The open Blender scene: file, frame range, fps, duration, resolution, camera, "
         "objects (animated or not), selection, ComfyQ connection.", obj(), blender_scene_info),
    Tool("blender_select", "Select these objects (e.g. the area an inpaint mask repaints).",
         obj({"names": {"type": "array", "items": _S}}, ("names",)), blender_select),
    Tool("blender_render", "Render a pass through the scene camera: `still` (clay/matcap/material image), "
         "`mask` (still with the selected objects transparent = area to repaint) or `animation` (the frame "
         "range as an MP4, what video-to-video workflows restyle). Returns the file path.",
         obj({"kind": {"type": "string", "enum": ["still", "mask", "animation"]},
              "style": {"type": "string", "enum": ["CLAY", "MATCAP", "MATERIAL", "CYCLES"],
                        "description": "CYCLES (stills only) = a real Cycles render (materials, lights, HDRI, "
                                       "shadow catchers) in a separate Blender: the GPU is free again after it"},
              "samples": {"type": "integer", "minimum": 1, "description": "Cycles samples (64)"},
              "frame": {"type": "integer"}, "max_edge": {"type": "integer", "default": 1280},
              "max_seconds": {"type": "number", "description": "animation: render only the first seconds "
                                                                  "(short tests; the scene is not changed)"}}),
         blender_render),
    Tool("blender_import", "Bring a result file into Blender: image → Image Editor, video/audio → the "
         "“ComfyQ Results” scene, GLB/PLY → a collection, placed `place` = BESIDE the scene (default) / CURSOR "
         "/ ORIGIN / ASIS, `on_ground` (default true), `size` = longest side in m (0 = as made).",
         obj({"path": _S, "job_id": _S, "place": {"type": "string", "enum": ["BESIDE", "CURSOR", "ORIGIN", "ASIS"]},
              "on_ground": {"type": "boolean"}, "size": {"type": "number", "minimum": 0}}, ("path",)),
         blender_import),
    Tool("blender_world", "Light the scene with an HDRI (world environment): `hdri` = a local .hdr/.exr file or "
         "`builtin:<name>` (Blender's city, courtyard, forest, interior, night, studio, sunrise, sunset), "
         "`strength` (1), `rotation_deg` around Z (turns the sun), `background` (true = the HDRI is seen behind "
         "the scene in renders; false = transparent). `clear: true` = back to a plain world.",
         obj({"hdri": _S, "strength": {"type": "number", "minimum": 0}, "rotation_deg": {"type": "number"},
              "background": {"type": "boolean"}, "clear": {"type": "boolean"}}), blender_world),
    Tool("blender_shadow_catcher", "Make objects catch shadows only (invisible except the shadows on them), "
         "e.g. a ground under an imported model over an HDRI: `names` (objects), `collection` (all its meshes), "
         "or `plane: true` (a new ground plane under the scene). Cycles: Blender's Shadow Catcher flag (exact). "
         "EEVEE: an approximating material; `strength` = shadow darkness (1), `eevee_lit` = how bright the fully "
         "lit ground is (1): raise it (e.g. 1.5 under a bright HDRI + sun) when no shadow shows, lower it when "
         "the whole ground darkens. `remove: true` undoes it.",
         obj({"names": {"type": "array", "items": _S}, "collection": _S, "plane": {"type": "boolean"},
              "strength": {"type": "number", "minimum": 0}, "eevee_lit": {"type": "number", "minimum": 0.01},
              "remove": {"type": "boolean"}}),
         blender_shadow_catcher),
    Tool("blender_transform", "Move / rotate / scale / hide objects by name (e.g. an imported model's parent "
         "empty, or the object hiding it). `location` [x,y,z] m, `rotation_deg` [x,y,z], `scale` number or "
         "[x,y,z], `hide` true/false.",
         obj({"names": {"type": "array", "items": _S}, "location": {"type": "array", "items": {"type": "number"}},
              "rotation_deg": {"type": "array", "items": {"type": "number"}}, "scale": {},
              "hide": {"type": "boolean"}}, ("names",)), blender_transform),
    Tool("blender_segment", "Segment a render by name with SAM3 (architecture: which part is floor, wall, "
         "ceiling, window…): renders the scene camera (`style`, default CYCLES) or takes `image`, runs one "
         "SAM3 job per label (`labels`, default floor/wall/ceiling/window/door/stairs/column/sofa/table/rug/shelf/"
         "lamp). SAM3 returns ONE surface per phrase: write 'wall|left wall|right wall' to name one label from "
         "several phrases (their masks are merged; the default wall does this). Returns each label's mask PNG, % of the picture, where it is, a colour overlay (shown in the "
         "Image Editor unless show=false) and `prompt`: the found surfaces named with `materials` "
         "({label: 'light oak parquet', …}) between `intro` and `outro`. Needs SAM3 served. Runs in the "
         "background: waits up to `wait_s` (≤120, default 60), then follow with blender_task_status.",
         obj({"labels": {"type": "array", "items": _S}, "image": _S,
              "style": {"type": "string", "enum": ["CLAY", "MATCAP", "MATERIAL", "CYCLES"]},
              "samples": {"type": "integer", "minimum": 1}, "max_edge": {"type": "integer"},
              "threshold": {"type": "number", "minimum": 0.1, "maximum": 0.9},
              "materials": {"type": "object"}, "intro": _S, "outro": _S, "show": {"type": "boolean"},
              "wait_s": {"type": "number", "maximum": MAX_WAIT_S}}), blender_segment),
    Tool("blender_task_status", "The background task (segmentation, serve, calibrate): state, message, "
         "result when done. `wait_s` = first wait (≤120 s) for it to end.",
         obj({"wait_s": {"type": "number", "maximum": MAX_WAIT_S}}), blender_task_status),
    Tool("blender_mask_image", "Make an inpaint input from a picture and surface masks (blender_segment's "
         "`mask` files): the picture with those surfaces transparent = the area an inpaint workflow repaints "
         "(e.g. Flux2 Klein inpaint: only the floor gets the new material). Masks are resized to the picture; "
         "`grow_px` (6) widens them over the seams; `invert` repaints everything else.",
         obj({"image": _S, "masks": {"type": "array", "items": _S}, "grow_px": {"type": "integer"},
              "invert": {"type": "boolean"}}, ("image", "masks")), blender_mask_image),
    Tool("blender_graph_describe", "The ComfyQ node graph: nodes (type, workflow, settings, prompt, links "
         "into each input, result) — what the student sees in the graph editor.", obj(), blender_graph_describe),
    Tool("blender_graph_add_preset", "Add a class preset to the graph: built in `render_image`, `clay_v2v`, "
         "`keyframe_v2v`, `image_3d`, `image_upscale` (workflows picked from what is served), or a saved "
         "preset's label.", obj({"preset": _S}, ("preset",)), blender_graph_add_preset),
    Tool("blender_graph_set", "Change a node: a workflow node's setting by key (or `prompt`), a Prompt "
         "node's `text`, a Surfaces node's `surfaces` ('label: material' lines), a source's `style`/`frame`/"
         "`filepath` (an Image node's `image` = a local image file), "
         "an output's `auto`/`folder`/`pattern`.",
         obj({"node": _S, "key": _S, "value": {}}, ("node", "key", "value")), blender_graph_set),
    Tool("blender_graph_run", "Run the graph (or one node and what it needs). Up-to-date results are reused; "
         "`force` re-runs upstream. Returns at once: follow with blender_graph_status.",
         obj({"node": _S, "force": {"type": "boolean"}}), blender_graph_run),
    Tool("blender_graph_status", "Progress of the graph: each workflow node's state, job phase/progress, "
         "result file.", obj(), blender_graph_status),
]

INSTRUCTIONS = (
    "Tools for a Blender scene and the classroom's ComfyQ server (AI rendering). Typical round trip: "
    "blender_scene_info → comfyq_list_workflows → comfyq_describe_workflow → blender_render (still or "
    "animation) → comfyq_submit (files = the rendered path) → comfyq_wait → comfyq_outputs → blender_import. "
    "Or work in the student's node graph: blender_graph_add_preset → blender_graph_set (prompt) → "
    "blender_graph_run → blender_graph_status. Only served workflows run; GPU time is shared by the class: "
    "keep jobs short and cancel what is not needed. comfyq_server_status shows what is served and what fits. "
    "With teacher tools on, comfyq_serve loads another workflow: alongside if it fits, else as the main one, "
    "which restarts ComfyQ for the whole class (say so before doing it).")
