"""Open a saved Mixar file, show its clay animation in the ComfyQ window, send one job, trace it.

  mixar <file.mixar> --online-mode --python tools/open_and_send.py

Mixar stays open afterwards. Progress goes to live_check/send_report.json.
"""
import json
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

REPO = Path(r"E:\zaki\MixarAI")
OUT = REPO / "live_check"
OUT.mkdir(exist_ok=True)
MOD = "bl_ext.user_default.comfyq_bridge"
report: dict = {"file": bpy.data.filepath, "started": time.strftime("%H:%M:%S"), "steps": []}


def log(msg, **data):
    print("[open_and_send]", msg, data or "")
    report["steps"].append({"t": time.strftime("%H:%M:%S"), "msg": msg, **data})
    (OUT / "send_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def guarded(fn):
    def run(*a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001
            log("ERROR in " + fn.__name__, error=repr(e), trace=traceback.format_exc())
            return None
    run.__name__ = fn.__name__
    return run


def ext(name):
    return sys.modules[f"{MOD}.{name}"]


@guarded
def step_enable():
    addon_utils.enable(MOD, default_set=True, persistent=True)
    S = ext("runtime.session").SESSION
    prefs = bpy.context.preferences.addons[MOD].preferences
    if "--cleanup" in sys.argv:
        strips_before = {sc.name: len(sc.sequence_editor.strips) if sc.sequence_editor else 0
                         for sc in bpy.data.scenes}
        res = bpy.ops.comfyq.cleanup()
        strips_after = {sc.name: len(sc.sequence_editor.strips) if sc.sequence_editor else 0
                        for sc in bpy.data.scenes}
        bpy.ops.wm.save_mainfile()
        log("cleanup + saved", result=list(res), strips_before=strips_before, strips_after=strips_after,
            use_sequencer=bpy.context.scene.render.use_sequencer)
    if S.client is None:
        S.connect(prefs.profile, prefs.student_name.strip(), prefs.url.strip())
    sc = bpy.context.scene
    log("file opened", scene=sc.name, frames=f"{sc.frame_start}-{sc.frame_end}", fps=sc.render.fps,
        camera=sc.camera.name if sc.camera else None, saved_workflow=sc.comfyq.workflow_id, user=prefs.student_name)
    state = {"n": 0}

    @guarded
    def wait():
        state["n"] += 1
        S.pump()
        if S.connected and S.served_ids():
            step_workflow()
            return None
        return None if state["n"] > 60 else 0.5
    bpy.app.timers.register(wait, first_interval=0.5)


@guarded
def step_workflow():
    S = ext("runtime.session").SESSION
    wfops = ext("ops.workflow")
    sc = bpy.context.scene
    served = sorted(S.served_ids())
    wf = sc.comfyq.workflow_id if sc.comfyq.workflow_id in served else served[0]
    wfops.select_workflow(sc, wf)
    log("workflow", workflow=wf, served=served)

    @guarded
    def wait():
        S.pump()
        if wf in S.specs:
            step_window()
            return None
        return 0.3
    bpy.app.timers.register(wait, first_interval=0.3)


@guarded
def step_window():
    wfops = ext("ops.workflow")
    win = bpy.context.window_manager.windows[0]
    area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None) or max(
        win.screen.areas, key=lambda a: a.width * a.height)
    with bpy.context.temp_override(window=win, area=area):
        log("ComfyQ window", opened=wfops.open_comfyq_window(bpy.context))
    bpy.app.timers.register(step_clay, first_interval=3.0)


@guarded
def step_clay():
    """Fresh clay render of the current animation, playing in the ComfyQ window's player."""
    wfops = ext("ops.workflow")
    player = ext("ui.player")
    w = wfops.comfyq_window()
    area = player.viewer_area()
    with bpy.context.temp_override(window=w, area=area):
        res = bpy.ops.comfyq.preview_animation(force=True, play=True)
    last = ext("passes.animation").last()
    log("clay animation", result=list(res), file=str(last.path) if last else None,
        frames=last.frames if last else None, size=last.size if last else None,
        playing_in_viewer=area.spaces.active.image.name if area and area.spaces.active.image else None,
        label=player.describe(area.spaces.active.image) if area else None, playing=player.is_playing())
    if "--no-send" in sys.argv:
        log("READY (no job sent: press Generate when ready)")
        return
    bpy.app.timers.register(step_send, first_interval=2.0)


@guarded
def step_send():
    S = ext("runtime.session").SESSION
    gen = ext("ops.generate")
    props = ext("props")
    sc = bpy.context.scene
    wf = sc.comfyq.workflow_id
    prompt_spec = next((s for s in S.specs[wf] if s.kind == "prompt"), None)
    if "--prompt-file" in sys.argv and prompt_spec:      # use this prompt instead of the saved one
        text = Path(sys.argv[sys.argv.index("--prompt-file") + 1]).read_text(encoding="utf-8").strip()
        it = props.param_item(sc, prompt_spec.key, wf)
        it.text, it.s = None, text
    if "--match" in sys.argv:                           # duration / fps / size from the scene
        wfops = ext("ops.workflow")
        m = ext("match").match(S.specs[wf], wfops.scene_facts(sc))
        wfops.apply_values(sc, wf, m.values)
        log("matched to scene", notes=m.notes)
    prompt = props.param_item(sc, prompt_spec.key, wf).value() if prompt_spec else ""
    n = len(sc.comfyq.jobs)
    msgs = []
    ok = gen.start_job(sc, [], wf, lambda lvl, m: msgs.append(m))
    log("generate", ok=ok, messages=msgs, prompt=prompt)
    state = {"n": 0, "jid": None, "last": None}

    @guarded
    def wait():
        state["n"] += 1
        S.pump()
        if state["jid"] is None:
            if len(sc.comfyq.jobs) > n:
                rec = sc.comfyq.jobs[-1]
                state["jid"] = rec.job_id
                log("booked", job=rec.job_id, sent=json.loads(rec.inputs or "{}"))
            elif S.error or state["n"] > 240:
                log("booking failed", error=S.error)
                return None
            return 0.5
        v = S.jobs.get(state["jid"])
        if v is not None and v.job.phase != state["last"]:
            state["last"] = v.job.phase
            log("job", phase=v.job.phase)
        if v is not None and v.job.done and (v.files or not v.job.ok):
            step_trace(state["jid"], prompt_spec, prompt)
            return None
        return None if state["n"] > 2400 else 0.5
    bpy.app.timers.register(wait, first_interval=0.5)


@guarded
def step_trace(job_id, prompt_spec, prompt):
    import requests
    S = ext("runtime.session").SESSION
    job = S.client.http.get(f"/jobs/{job_id}")["job"]
    params = job.get("params") or {}
    log("ComfyQ stored", status=job["status"], error=job.get("error_reason"),
        job_prompt_matches=job.get("prompt") == prompt,
        prompt_param_matches=(params.get(prompt_spec.key) == prompt) if prompt_spec else None,
        videos={k: v for k, v in params.items() if isinstance(v, str) and v.lower().endswith(".mp4")},
        outputs=[o.get("filename") for o in job.get("outputs") or []],
        local_files=[str(f) for f in S.jobs[job_id].files])
    try:
        hist = requests.get("http://127.0.0.1:8188/history?max_items=30", timeout=5).json()
        # The run of THIS job: its outputs carry the job id (an older run may share the prompt text).
        hist = {k: e for k, e in hist.items() if job_id[:8] in json.dumps(e.get("outputs", {}))}
        for entry in hist.values():
            graph = entry["prompt"][2]
            hit = {nid: n["class_type"] for nid, n in graph.items()
                   if prompt and any(v == prompt for v in n.get("inputs", {}).values())}
            if hit:
                vids = {nid: n["inputs"].get("file") for nid, n in graph.items()
                        if "video" in n["class_type"].lower() and n["inputs"].get("file")}
                log("ComfyUI executed", prompt_nodes=hit, video_nodes=vids,
                    status=entry.get("status", {}).get("status_str"))
                break
        else:
            log("ComfyUI: prompt text not found in the last 10 runs")
    except Exception as e:  # noqa: BLE001
        log("ComfyUI history not reachable", error=repr(e))
    log("DONE")


log("starting")
bpy.app.timers.register(step_enable, first_interval=6.0)
