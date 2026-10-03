"""P1.5 workflow sweep (headless Blender, run by tools/smoke_test.ps1 -Sweep): every SERVED workflow is
calibrated by ComfyQ (VRAM / time, admin route) and then run through the bridge's own job path with
inputs made from the scene (clay still / animation, mask, a test sound) and its cheapest settings;
the result is downloaded and imported. One JSON per workflow in <out>/, read by tools/sweep_matrix.py.

  blender -b --online-mode --python tests/blender/sweep.py -- <scene> <out> [--only a,b] [--no-calibrate]

The teacher chooses what is served (ComfyQ's interface): this never changes lanes.
"""
import json
import math
import os
import struct
import sys
import time
import traceback
import wave
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
SCENE, OUT = ARGS[0], Path(ARGS[1])
ONLY = next((a.split("=", 1)[1].split(",") for a in ARGS if a.startswith("--only=")), None)
CALIBRATE = "--no-calibrate" not in ARGS
# --lanes=a,b: serve each of these alongside the main lane (ComfyQ admin "serve alongside", no restart),
# test it, close the lane again. Only with the teacher's permission; never forced.
LANES = next((a.split("=", 1)[1].split(",") for a in ARGS if a.startswith("--lanes=")), None)
# --switch=a,b --restore=c: make each the MAIN served workflow in turn (ComfyQ's "serve this workflow":
# restarts the ComfyQ server), test it, and serve `c` again at the end. Only with the teacher's permission.
SWITCH = next((a.split("=", 1)[1].split(",") for a in ARGS if a.startswith("--switch=")), None)
RESTORE = next((a.split("=", 1)[1] for a in ARGS if a.startswith("--restore=")), None)
OUT.mkdir(parents=True, exist_ok=True)
MOD = "bl_ext.user_default.comfyq_bridge"
addon_utils.enable(MOD, default_set=True)
bpy.ops.wm.open_mainfile(filepath=SCENE)
addon_utils.enable(MOD, default_set=True)

from bl_ext.user_default.comfyq_bridge import match as scene_match
from bl_ext.user_default.comfyq_bridge.client.params import is_disabled
from bl_ext.user_default.comfyq_bridge.importers.media import import_files
from bl_ext.user_default.comfyq_bridge.ops.generate import book
from bl_ext.user_default.comfyq_bridge.ops.workflow import scene_facts
from bl_ext.user_default.comfyq_bridge.passes import animation, render
from bl_ext.user_default.comfyq_bridge.prefs import prefs
from bl_ext.user_default.comfyq_bridge.props import tab_of
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION

PROMPTS = {
    "VIDEO": "claymation city street at dusk, warm window lights, gentle camera move, handmade look",
    "IMAGE": "a claymation city street at dusk, warm window lights, handmade miniature look, soft studio light",
    "MODEL": "a small clay house", "SPLAT": "a small clay house",
    "AUDIO": "soft ambient city at dusk, distant traffic, gentle wind, 5 seconds",
    "TOOLS": "a claymation city street",
}
CLIP_S = 2.0                                     # animation inputs: the first 2 s of the scene


def log(*a):
    print("[sweep]", *a, flush=True)


def pump_until(pred, timeout, what=""):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.2)
    log("timeout:", what)
    return False


def test_sound(path: Path, seconds: float = 3.0) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / 44100)))
                               for i in range(int(44100 * seconds))))
    return path


def short_animation(scene, edge):
    keep = scene.frame_end
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    scene.frame_end = min(keep, scene.frame_start + round(CLIP_S * fps) - 1)
    try:
        return animation.animation_pass(scene, "CLAY", edge, force=True).path
    finally:
        scene.frame_end = keep


def cheap_values(specs, group):
    """Smallest sizes / shortest clips / extras off; prompts per group; everything else = defaults."""
    values, notes = {}, []
    for s in specs:
        low = (s.label or s.key).lower()
        if s.kind == "prompt":
            values[s.key] = PROMPTS.get(group, PROMPTS["IMAGE"])
        elif s.kind == "seed":
            values[s.key] = 1234
        elif s.kind == "bool" and any(w in low for w in ("enhance", "upscal", "refine", "start image",
                                                          "interpolat", "auto prompt", "autoprompt")):
            values[s.key] = False
        elif s.kind in ("int", "float") and s.min is not None:
            if any(w in low for w in ("width", "height", "resolution", "size", "megapixel", "long edge")):
                v = max(float(s.min), 256.0) if (s.max is None or float(s.max) >= 256) else float(s.min)
                if s.kind == "float" and "megapixel" in low:
                    v = float(s.min)
                values[s.key] = int(v) if s.kind == "int" else v
            elif any(w in low for w in ("duration", "length", "seconds", "frames", "frame count", "clip")):
                v = max(float(s.min), 1.0)
                values[s.key] = int(v) if s.kind == "int" else v
    if values:
        notes.append("cheap settings: " + ", ".join(f"{k}={v}" for k, v in values.items() if not isinstance(v, str)))
    return values, notes


def media_inputs(scene, specs, values, wf_id):
    uploads, notes = {}, []
    cur = dict(values)
    for s in specs:
        if not s.is_media or is_disabled(s, cur, specs):
            continue
        edge = s.max_input_edge or 768
        if s.kind == "video":
            uploads[s.key] = short_animation(scene, min(edge, 768))
        elif s.kind == "audio":
            uploads[s.key] = test_sound(OUT / "test_sound.wav")
        elif s.kind == "mask":
            sel = [o for o in scene.objects if o.type == "MESH"][:1]
            uploads[s.key] = render.mask_pass(scene, sel, "CLAY", min(edge, 1024),
                                              scene_match.frame_for(s, scene_facts(scene)))
        else:
            uploads[s.key] = render.structure_pass(scene, "CLAY", min(edge, 1024),
                                                   scene_match.frame_for(s, scene_facts(scene)))
        notes.append(f"{s.key} ← {uploads[s.key].name}")
    return uploads, notes


SERVER = {}            # base URL + headers of the first connection (the session drops during restarts)


def _admin_headers():
    headers = dict(SERVER.get("headers") or {})
    if os.environ.get("COMFYQ_ADMIN_PASSWORD"):
        headers["x-admin-password"] = os.environ["COMFYQ_ADMIN_PASSWORD"]
    return headers


def open_lane(wf_id):
    """(opened, message). Waits until ComfyQ lists it as served."""
    import requests
    base = SERVER["base"]
    try:
        r = requests.post(f"{base}/admin/lanes", json={"workflowId": wf_id}, headers=_admin_headers(), timeout=1800)
        body = r.json() if r.content else {}
    except (requests.RequestException, ValueError) as e:
        return False, f"lane: {e}"
    if r.status_code != 200:
        fit = body.get("fit") or {}
        extra = f" (needs {fit.get('needGb')} GB, free {fit.get('freeGb')} GB)" if fit else ""
        return False, f"lane refused: {body.get('error') or r.status_code}{extra}"
    if not pump_until(lambda: wf_id in SESSION.served_ids(), 600, f"{wf_id} served"):
        close_lane(wf_id)
        return False, "lane opened but never listed as served"
    return True, "lane opened"


def close_lane(wf_id):
    import requests
    try:
        r = requests.delete(f"{SERVER['base']}/admin/lanes/{wf_id}", headers=_admin_headers(), timeout=300)
        log(wf_id, "lane closed", r.status_code)
    except requests.RequestException as e:
        log(wf_id, "lane close failed:", e)


def serve_main(wf_id, timeout=900):
    """Make wf_id ComfyQ's main served workflow (server restart) and reconnect. True once it is served."""
    import requests
    base = SERVER["base"]
    try:
        r = requests.post(f"{base}/admin/activate-workflow", json={"workflowId": wf_id},
                          headers=_admin_headers(), timeout=60)
    except requests.RequestException as e:
        log(wf_id, "activate failed:", e)
        return False
    if r.status_code != 200:
        log(wf_id, "activate refused:", r.status_code, r.text[:200])
        return False
    log(wf_id, "activated; ComfyQ restarts…")
    time.sleep(8)
    p = prefs()
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.close()
        SESSION.connect(p.profile, p.student_name.strip(), p.url.strip())
        if pump_until(lambda: SESSION.connected and wf_id in SESSION.served_ids(), 60, f"{wf_id} served"):
            log(wf_id, f"served after {round(time.monotonic() - t0)} s")
            return True
    return False


def calibrate(wf_id):
    import requests
    headers = _admin_headers()
    t0 = time.monotonic()
    try:
        r = requests.post(f"{SERVER['base']}/workflows/{wf_id}/calibrate", headers=headers, timeout=3600)
        body = r.json() if r.content else {}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": str(e)}
    out = {"ok": r.status_code == 200 and body.get("ok", False), "seconds": round(time.monotonic() - t0)}
    out.update({"runtime": body.get("runtime")} if out["ok"] else {"error": body.get("error") or r.status_code})
    return out


def run_one(scene, wf):
    rec = {"id": wf.id, "name": wf.name, "category": wf.category, "group": tab_of(wf),
           "started": time.strftime("%Y-%m-%d %H:%M"), "notes": []}
    specs = SESSION.client.params(wf.id)
    SESSION.specs[wf.id] = specs
    rec["inputs"] = [s.kind for s in specs if s.is_media]
    if CALIBRATE:
        log(wf.id, "calibrating…")
        rec["calibration"] = calibrate(wf.id)
        log(wf.id, "calibration", rec["calibration"])
    values, notes = cheap_values(specs, rec["group"])
    rec["notes"] += notes
    try:
        uploads, notes = media_inputs(scene, specs, values, wf.id)
    except Exception as e:  # noqa: BLE001
        rec.update(result="inputs failed", error=f"{e.__class__.__name__}: {e}")
        return rec
    rec["notes"] += notes
    box = {}
    t0 = time.monotonic()
    book(scene, wf.id, specs, values, uploads, purpose="graph", label=f"Sweep · {wf.name}",
         on_booked=lambda jid: box.update(job=jid), on_error=lambda m: box.update(error=m))
    if not pump_until(lambda: box, 300, "booking") or "error" in box:
        rec.update(result="booking failed", error=box.get("error", "timeout"))
        return rec
    jid = rec["job_id"] = box["job"]
    log(wf.id, "booked", jid)
    done = pump_until(lambda: (v := SESSION.jobs.get(jid)) is not None and v.job.done
                      and (not v.job.ok or v.files), 3600, "job")
    v = SESSION.jobs.get(jid)
    rec["seconds"] = round(time.monotonic() - t0)
    if not done or v is None:
        rec.update(result="timeout", error="no answer within 60 min")
        return rec
    rec["phase"] = v.job.phase
    if not v.job.ok:
        rec.update(result="failed", error=v.job.error or v.job.phase)
        return rec
    rec["outputs"] = [{"name": f.name, "bytes": f.stat().st_size} for f in v.files]
    try:
        rec["imported"] = import_files(list(v.files), jid, wf.id, scene)
    except Exception as e:  # noqa: BLE001
        rec["imported"] = [f"import crashed: {e}"]
    bad = [m for m in rec["imported"] if "failed" in m or "no importer" in m or "crashed" in m]
    rec["result"] = "ok" if not bad else "ok (import issue)"
    return rec


def save(rec):
    (OUT / f"{rec['id']}.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    log(rec["id"], "→", rec.get("result"), rec.get("error", ""))


def sweep_switch(scene):
    by_id = {w.id: w for w in SESSION.workflows}
    try:
        for wf_id in SWITCH:
            wf = by_id.get(wf_id)
            if wf is None:
                continue
            if not serve_main(wf_id):
                save({"id": wf_id, "name": wf.name, "category": wf.category, "group": tab_of(wf),
                      "result": "skipped", "error": "could not be served as the main workflow (see ComfyQ's log)"})
                continue
            try:
                rec = run_one(scene, by_id.get(wf_id, wf))
                rec["notes"].append("served as the main workflow for the test")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                rec = {"id": wf_id, "result": "crashed", "error": f"{e.__class__.__name__}: {e}"}
            save(rec)
    finally:
        if RESTORE:
            log("restoring", RESTORE)
            log("restored" if serve_main(RESTORE) else "RESTORE FAILED: serve it again in ComfyQ", RESTORE)


def main():
    p = prefs()
    SESSION.connect(p.profile, p.student_name.strip(), p.url.strip())
    if not pump_until(lambda: SESSION.connected and SESSION.workflows, 60, "connect"):
        log("not connected:", SESSION.error)
        return
    SERVER.update(base=SESSION.client.http.base, headers=dict(SESSION.client.http._headers()))
    pump_until(lambda: bool(SESSION.served_ids()), 15, "served lanes")
    served = sorted(SESSION.served_ids())
    if SWITCH:
        sweep_switch(scene=bpy.context.scene)
        SESSION.close()
        log("DONE")
        return
    if LANES:
        todo = [w for w in SESSION.workflows if w.id in LANES]
    else:
        todo = [w for w in SESSION.workflows if w.id in served and (ONLY is None or w.id in ONLY)]
    log("served:", served, "→ testing", [w.id for w in todo])
    scene = bpy.context.scene
    for wf in todo:
        opened = False
        try:
            if wf.id not in SESSION.served_ids():
                log(wf.id, "opening a lane…")
                opened, msg = open_lane(wf.id)
                log(wf.id, msg)
                if not opened:
                    rec = {"id": wf.id, "name": wf.name, "category": wf.category, "group": tab_of(wf),
                           "result": "skipped", "error": msg}
                    (OUT / f"{wf.id}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
                    continue
            rec = run_one(scene, wf)
            if opened:
                rec["notes"].append("served alongside the main lane for the test")
        except Exception as e:  # noqa: BLE001 - one workflow must not stop the sweep
            traceback.print_exc()
            rec = {"id": wf.id, "result": "crashed", "error": f"{e.__class__.__name__}: {e}"}
        finally:
            if opened:
                close_lane(wf.id)
        (OUT / f"{wf.id}.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
        log(wf.id, "→", rec.get("result"), rec.get("error", ""))
    SESSION.close()
    log("DONE")


main()
