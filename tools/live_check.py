"""Live check on the scene open in Mixar — run it from Mixar's Text Editor (Open → Run Script).
VERSION 2 (if the report has no "version": 2, Mixar ran an older copy of this text)

Reinstalls the latest build from dist/, connects, selects the served workflow, matches it to this
scene, renders the animation pass, opens the ComfyQ window and screenshots it. Books nothing.
Writes live_check/report.json and live_check/window.png next to this file's repo.
"""
import importlib
import json
import os
import sys
import traceback
from pathlib import Path

import bpy

REPO = Path(r"E:\zaki\MixarAI")
OUT = REPO / "live_check"
OUT.mkdir(exist_ok=True)
ZIP = next(iter(sorted((REPO / "dist").glob("comfyq_bridge-*.zip"))), None)
MOD = "bl_ext.user_default.comfyq_bridge"
report: dict = {"steps": []}


def log(msg, **data):
    print("[live_check]", msg, data or "")
    report["steps"].append({"msg": msg, **data})
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def guarded(fn):
    def run():
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - everything goes to the report
            log("ERROR in " + fn.__name__, error=repr(e), trace=traceback.format_exc())
            return None
    return run


def ext(name=""):
    full = MOD + (("." + name) if name else "")
    return sys.modules.get(full) or importlib.import_module(full)


def main_window_and_view3d():
    win = bpy.context.window_manager.windows[0]
    area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None) or max(
        win.screen.areas, key=lambda a: a.width * a.height)
    return win, area


@guarded
def step_install():
    if ZIP is None:
        log("no zip in dist/: using the installed version")
    else:
        bpy.ops.extensions.package_install_files(filepath=str(ZIP), repo="user_default", enable_on_install=True)
        log("reinstalled", zip=ZIP.name)
    # Blender re-registers the reinstalled extension itself; use its modules as they are
    # (re-importing would create a second, unconnected session object).
    bpy.app.timers.register(step_connect, first_interval=2.0)


@guarded
def step_connect():
    S = ext("runtime.session").SESSION
    prefs = bpy.context.preferences.addons[MOD].preferences
    if S.client is None:
        S.connect(prefs.profile, prefs.student_name.strip() or "live-check", prefs.url.strip())
    log("connecting", user=prefs.student_name, url=prefs.url or "(profile)", online=bpy.app.online_access)
    state = {"n": 0}

    @guarded
    def wait():
        state["n"] += 1
        S.pump()        # harmless if the extension's own timer pumps it too
        if S.connected and S.served_ids():
            bpy.app.timers.register(step_workflow, first_interval=0.1)
            return None
        if state["n"] > 60:
            log("not connected after 30 s", status=S.status, error=S.error)
            return None
        return 0.5
    bpy.app.timers.register(wait, first_interval=0.5)


@guarded
def step_workflow():
    S = ext("runtime.session").SESSION
    wfops = ext("ops.workflow")
    scene = bpy.context.scene
    served = sorted(S.served_ids())
    wf = scene.comfyq.workflow_id if scene.comfyq.workflow_id in served else served[0]
    wfops.select_workflow(scene, wf)
    facts = wfops.scene_facts(scene)
    log("scene", name=scene.name,
        seconds=round(facts.seconds, 3), aspect=round(facts.aspect, 4),
        resolution=f"{scene.render.resolution_x}x{scene.render.resolution_y}",
        camera=scene.camera.name if scene.camera else None, workflow=wf, served=served)

    @guarded
    def wait():
        S.pump()
        if wf in S.specs:
            bpy.app.timers.register(step_match, first_interval=0.3)
            return None
        return 0.3
    bpy.app.timers.register(wait, first_interval=0.3)


@guarded
def step_match():
    S = ext("runtime.session").SESSION
    wfops = ext("ops.workflow")
    match = ext("match")
    props = ext("props")
    scene = bpy.context.scene
    wf = scene.comfyq.workflow_id
    specs = S.specs[wf]
    m = match.match(specs, wfops.scene_facts(scene))
    values = {s.key: {"label": s.label, "kind": s.kind,
                      "value": (it.value() if (it := props.param_item(scene, s.key, wf)) else None),
                      "source": it.source if it and s.is_media else None,
                      "suggested": m.values.get(s.key)} for s in specs}
    log("workflow params", workflow=wf, params=values, warnings=m.warnings, notes=m.notes)
    bpy.app.timers.register(step_animation, first_interval=0.2)


@guarded
def step_animation():
    anim = ext("passes.animation")
    wfops = ext("ops.workflow")
    scene = bpy.context.scene
    specs = wfops.current_specs(bpy.context)
    edge = max((s.max_input_edge or 0 for s in specs if s.kind == "video"), default=0) or None
    if any(s.kind == "video" for s in specs):
        p = anim.animation_pass(scene, scene.comfyq.style, edge, force=True)
        log("animation pass", path=str(p.path), frames=p.frames, seconds=round(p.seconds, 3), size=p.size,
            mb=round(p.path.stat().st_size / 1e6, 2))
    bpy.app.timers.register(step_window, first_interval=0.2)


@guarded
def step_window():
    wfops = ext("ops.workflow")
    win, area = main_window_and_view3d()
    with bpy.context.temp_override(window=win, area=area):
        ok = wfops.open_comfyq_window(bpy.context)
    log("window opened", ok=ok)
    bpy.app.timers.register(step_gray, first_interval=3.0)


@guarded
def step_gray():
    """Grayscale clay render of this scene through its camera, checked pixel by pixel, shown in the viewer."""
    import numpy as np
    wfops = ext("ops.workflow")
    player = ext("ui.player")
    scene = bpy.context.scene
    scene.comfyq.style = "CLAY"
    w = wfops.comfyq_window()
    area = player.viewer_area()
    with bpy.context.temp_override(window=w, area=area):
        res = bpy.ops.comfyq.preview_pass(mask=False)
    img = bpy.data.images.get("ComfyQ render preview")
    px = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    spread = float(np.abs(px[:, 0] - px[:, 1]).max() + np.abs(px[:, 1] - px[:, 2]).max())
    log("grayscale render", result=list(res), size=tuple(img.size), file=img.filepath,
        channel_spread=spread, grayscale=spread < 1e-6, mean=round(float(px[:, 0].mean()), 3),
        shown=area.spaces.active.image.name if area else None)
    bpy.app.timers.register(step_book, first_interval=0.5)


@guarded
def step_book():
    """Generate with the scene's current settings, then trace the prompt and the video end to end."""
    S = ext("runtime.session").SESSION
    gen = ext("ops.generate")
    props = ext("props")
    scene = bpy.context.scene
    wf = scene.comfyq.workflow_id
    specs = S.specs[wf]
    prompt_spec = next((s for s in specs if s.kind == "prompt"), None)
    sent_prompt = props.param_item(scene, prompt_spec.key, wf).value() if prompt_spec else None
    n_before = len(scene.comfyq.jobs)
    msgs = []
    ok = gen.start_job(scene, list(bpy.context.selected_objects), wf, lambda lvl, m: msgs.append(m))
    log("booking", workflow=wf, ok=ok, messages=msgs, prompt_in_ui=sent_prompt,
        prompt_from_text_editor=bool(prompt_spec and props.param_item(scene, prompt_spec.key, wf).text))
    state = {"n": 0, "jid": None}

    @guarded
    def wait():
        state["n"] += 1
        S.pump()
        if state["jid"] is None:
            if len(scene.comfyq.jobs) > n_before:
                state["jid"] = scene.comfyq.jobs[-1].job_id
                log("booked", job=state["jid"])
            elif state["n"] > 240 or S.error:
                log("booking failed", error=S.error)
                bpy.app.timers.register(step_shot, first_interval=0.1)
                return None
            return 0.5
        v = S.jobs.get(state["jid"])
        if v is not None and v.job.done:
            bpy.app.timers.register(lambda: step_trace(state["jid"], prompt_spec, sent_prompt), first_interval=0.5)
            return None
        if state["n"] % 20 == 0 and v is not None:
            log("job running", phase=v.job.phase, progress=v.job.progress)
        return None if state["n"] > 2400 else 0.5
    bpy.app.timers.register(wait, first_interval=0.5)


@guarded
def step_trace(job_id, prompt_spec, sent_prompt):
    import requests
    S = ext("runtime.session").SESSION
    job = S.client.http.get(f"/jobs/{job_id}")["job"]
    params = job.get("params") or {}
    video_keys = [k for k, v in params.items() if isinstance(v, str) and v.lower().endswith((".mp4", ".mov", ".webm"))]
    still_keys = [k for k, v in params.items() if isinstance(v, str) and v.lower().endswith((".png", ".jpg"))]
    log("ComfyQ stored", status=job["status"], error=job.get("error_reason"),
        job_prompt=job.get("prompt"), job_prompt_matches=(job.get("prompt") == sent_prompt),
        prompt_param=params.get(prompt_spec.key) if prompt_spec else None,
        prompt_param_matches=(params.get(prompt_spec.key) == sent_prompt) if prompt_spec else None,
        video_params={k: params[k] for k in video_keys}, still_params={k: params[k] for k in still_keys},
        outputs=[o.get("filename") for o in job.get("outputs") or []])
    try:
        hist = requests.get("http://127.0.0.1:8188/history?max_items=30", timeout=5).json()
        # The run of THIS job: its outputs carry the job id (an older run may share the prompt text).
        hist = {k: e for k, e in hist.items() if job_id[:8] in json.dumps(e.get("outputs", {}))}
        for pid, entry in hist.items():
            graph = entry["prompt"][2]
            text_nodes = {nid: n["class_type"] for nid, n in graph.items()
                          if sent_prompt and any(v == sent_prompt for v in n.get("inputs", {}).values())}
            if not text_nodes:
                continue
            video_nodes = {nid: (n["class_type"], n["inputs"].get("file") or n["inputs"].get("video"))
                           for nid, n in graph.items() if "video" in n["class_type"].lower()
                           and (n["inputs"].get("file") or n["inputs"].get("video"))}
            log("ComfyUI executed", prompt_id=pid[:8], prompt_nodes=text_nodes, video_nodes=video_nodes,
                status=entry.get("status", {}).get("status_str"))
            break
        else:
            log("ComfyUI history: no graph with this exact prompt text in the last 10 runs")
    except Exception as e:  # noqa: BLE001
        log("ComfyUI history not reachable from here", error=repr(e))
    bpy.app.timers.register(step_shot, first_interval=1.0)


@guarded
def step_shot():
    wfops = ext("ops.workflow")
    w = wfops.comfyq_window()
    if w is None:
        log("ComfyQ window not found")
        return
    areas = [(a.type, a.x, a.y, a.width, a.height,
              next((r.active_panel_category for r in a.regions if r.type == "UI"), None)) for a in w.screen.areas]
    log("window areas", areas=areas)
    with bpy.context.temp_override(window=w, screen=w.screen):
        bpy.ops.screen.screenshot(filepath=str(OUT / "window.png"))
    log("DONE", screenshot=str(OUT / "window.png"))


report["version"] = 2
report["started"] =__import__("time").strftime("%H:%M:%S")
bpy.app.timers.register(step_install, first_interval=0.1)
print("[live_check] started — results in", OUT, os.linesep)
