# SPDX-License-Identifier: GPL-3.0-or-later
"""Architecture AI render, step by step, headless (GPU): base render → SAM3 surfaces → labelled prompt →
candidate render workflows → per-surface inpaint → upscalers. Each step's files and choices go to
<out>/state.json so the next step (and a human) can look at them first.

Run in a sandbox profile with the extension installed (tools/smoke_test.ps1 builds and installs it):
    set BLENDER_USER_RESOURCES=%TEMP%\\comfyq_bridge_smoke_blender
    blender -b --online-mode --python tools/arch_render.py -- <scene.blend> <out dir> <step> [options]
steps: base | segment | render [--only=a,b] | refine --from=<candidate> [--labels=floor] | upscale --from=<file>
Loads the models each step needs (solo use: only when nobody else has a job on ComfyQ).
"""
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
SCENE, OUT, STEP = ARGS[0], Path(ARGS[1]).resolve(), ARGS[2]
OPT = {a[2:].split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else "1") for a in ARGS[3:] if a.startswith("--")}
OUT.mkdir(parents=True, exist_ok=True)
STATE = OUT / "state.json"
MOD = "bl_ext.user_default.comfyq_bridge"
addon_utils.enable(MOD, default_set=True)
bpy.ops.wm.open_mainfile(filepath=SCENE)
addon_utils.enable(MOD, default_set=True)

from bl_ext.user_default.comfyq_bridge.passes import render
from bl_ext.user_default.comfyq_bridge.passes import segment as seg
from bl_ext.user_default.comfyq_bridge.prefs import prefs
from bl_ext.user_default.comfyq_bridge.runtime import segmentation, serving
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION
from bl_ext.user_default.comfyq_bridge.surfaces import Region, surface_prompt

LABELS = ["floor", "wall|left wall|right wall", "ceiling", "window", "door", "sofa", "table", "rug", "shelf", "lamp"]
MATERIALS = {
    "floor": "wide-plank light oak parquet with a satin finish",
    "wall": "smooth warm-white lime plaster",
    "ceiling": "flat white plaster",
    "window": "a black steel-framed glazing looking onto a sunny garden",
    "door": "a solid walnut door",
    "sofa": "a light grey linen sofa with cushions",
    "table": "a dark walnut coffee table",
    "rug": "a cream hand-woven wool rug",
    "shelf": "a walnut bookcase with books and ceramics",
    "lamp": "a matte black metal pendant lamp",
}
INTRO = ("Photorealistic interior photograph of a modern living room, architectural photography, natural "
         "daylight through the window, soft realistic shadows.")
OUTRO = "Keep the exact layout, camera, perspective and proportions of the room."
CANDIDATES = {
    # name: (workflow, image key, extra settings, prompt prefix)
    "klein_edit": ("image_edit_flux2_klein_9b_image_edit", "loadimage_image_76", {"flux2scheduler_steps_75_62": 20},
                   "Turn this 3D render into a real photograph. "),
    "qwen_depth": ("image_qwen_image_2_1_control_depth", "loadimage_image_900",
                   {"resolutionselector_aspect_ratio_13": "16:9 (Widescreen)", "resolutionselector_megapixels_13": 2,
                    "ksampler_steps_459_458": 30}, ""),
    "qwen_depth_4mp": ("image_qwen_image_2_1_control_depth", "loadimage_image_900",
                       {"resolutionselector_aspect_ratio_13": "16:9 (Widescreen)", "resolutionselector_megapixels_13": 4,
                        "ksampler_steps_459_458": 40}, ""),     # native 2K: real detail instead of an upscale
    "qwen_edit": ("image_qwen_image_2_1_image_edit", "loadimage_image_470", {"ksampler_steps_459_458": 30},
                  "Turn <image1>, a 3D render, into a real photograph. "),
}
INPAINT = ("image_edit_flux2_klein_inpaint_prompt", "loadimage_image_1", "cliptextencode_text_6")
UPSCALERS = {
    "pid": ("utility_pid_image_upscale", "loadimage_image_1", {}),
    "seedvr2_4k": ("utility_seedvr2_4k_upscale", "source_image_16", {"resolution_10": 3840}),   # fp16 7B "sharp", ×2
    "seedvr2_7b": ("utility_seedvr2_7b_int8_image_upscale", "loadimage_image_1",
                   {"seedvr2postprocessing_color_correction_method_66_59": "lab"}),
}


def log(*a):
    print("[arch]", *a, flush=True)


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.is_file() else {}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.2)
    return False


def connect():
    p = prefs()
    SESSION.connect(p.profile, "arch-render", p.url.strip())
    if not pump_until(lambda: SESSION.connected and SESSION.workflows, 60):
        raise SystemExit("could not connect to ComfyQ")


def ensure_served(wf):
    if wf in SESSION.served_ids():
        return
    client = SESSION.client
    busy = serving.blockers(client)
    if busy:
        raise SystemExit(f"not switching models: unfinished jobs {busy}")
    how = serving.choose(client, wf, "auto")
    log("serving", wf, how)
    t0 = time.monotonic()
    serving.serve(client, wf, how)
    if how == "main":
        time.sleep(8)
        p = prefs()
        while time.monotonic() - t0 < 900:
            SESSION.close()
            SESSION.connect(p.profile, "arch-render", p.url.strip())
            if pump_until(lambda: SESSION.connected and wf in SESSION.served_ids(), 60):
                break
    elif not pump_until(lambda: wf in SESSION.served_ids(), 600):
        raise SystemExit(f"{wf} never served")
    log("served", wf, f"{round(time.monotonic() - t0)} s")


def run_job(wf, values, files, dest: Path, prompt=""):
    ensure_served(wf)
    client = SESSION.client
    specs = client.params(wf)
    values = dict(values)
    for k, f in files.items():
        if f.suffix.lower() == ".png":
            f = seg.fit_upload(Path(shutil.copy(f, OUT / f"upload_{f.name}")))
        values[k] = client.upload(f)
    t0 = time.monotonic()
    jid = client.submit(wf, values, prompt=prompt, specs=specs)
    log("booked", wf, jid[:8])
    j = client.wait(jid, timeout_s=1800)
    secs = round(time.monotonic() - t0)
    if not j.ok:
        log("FAILED", wf, j.error)
        return {"workflow": wf, "job": jid, "error": j.error or j.phase, "seconds": secs}
    got = client.download_outputs(j, dest)
    log("done", wf, secs, "s", [g.name for g in got])
    return {"workflow": wf, "job": jid, "files": [str(g) for g in got], "seconds": secs, "values": values,
            "prompt": prompt}


def regions_of(st):
    return [Region(r["label"], r["coverage_pct"] / 100, tuple(r["bbox"]), r["mask"]) for r in st["segment"]["regions"]]


def main():
    st = load_state()
    sc = bpy.context.scene
    if STEP == "base":
        p = render.structure_pass(sc, "CYCLES", 1920, None, int(OPT.get("samples", 96)))
        clay = render.structure_pass(sc, "CLAY", 1920)
        # the bridge's pass cache gets cleaned up: keep copies
        p, clay = Path(shutil.copy(p, OUT / "base_cycles.png")), Path(shutil.copy(clay, OUT / "base_clay.png"))
        st["base"] = {"cycles": str(p), "clay": str(clay)}
        log("base", p, clay)
    if STEP != "base":
        connect()
    if STEP == "segment":
        img = Path(st["base"][OPT.get("on", "cycles")])
        ensure_served(segmentation.SAM3_ID)
        t0 = time.monotonic()
        res = segmentation.segment(SESSION.client, img, OPT.get("labels", ",".join(LABELS)).split(","),
                                   seg.work_dir(OUT.name), lambda fn: fn(),
                                   float(OPT["threshold"]) if "threshold" in OPT else None, progress=log)
        res["regions"] = segmentation.region_dicts(res["regions"])
        res["seconds"] = round(time.monotonic() - t0)
        st["segment"] = res
        regions = regions_of(st)
        st["prompt"] = surface_prompt(regions, MATERIALS, INTRO, OUTRO)
        log("found", [(r["label"], r["coverage_pct"]) for r in res["regions"]], res["errors"])
        log("prompt", st["prompt"])
    if STEP == "render":
        only = OPT.get("only", ",".join(CANDIDATES)).split(",")
        st.setdefault("renders", {})
        for name in only:
            wf, key, extra, pre = CANDIDATES[name]
            prompt = pre + st["prompt"]
            values = {**extra}
            pk = next(s.key for s in SESSION.client.params(wf) if s.kind == "prompt")
            values[pk] = prompt
            st["renders"][name] = run_job(wf, values, {key: Path(st["base"]["cycles"])}, OUT / name, prompt)
            save_state(st)
    if STEP == "refine":
        src = Path(OPT["from"])
        labels = OPT.get("labels", "floor").split(",")
        st.setdefault("refine", {})
        for lab in labels:
            reg = next(r for r in st["segment"]["regions"] if r["label"] == lab)
            masked = seg.repaint_mask(src, [Path(reg["mask"])], OUT / f"mask_{lab}_{src.stem}.png",
                                      int(OPT.get("grow", 6)))
            wf, key, pk = INPAINT
            text = f"{OPT.get('material', MATERIALS[lab])}, photorealistic, matching the light and perspective of the room"
            r = run_job(wf, {pk: text}, {key: masked}, OUT / f"refine_{lab}", text)
            st["refine"][f"{lab}:{src.name}"] = {**r, "mask": str(masked)}
            save_state(st)
            src = Path(r["files"][0]) if r.get("files") else src
    if STEP == "upscale":
        src = Path(OPT["from"])
        st.setdefault("upscale", {})
        for name in OPT.get("only", ",".join(UPSCALERS)).split(","):
            wf, key, extra = UPSCALERS[name]
            st["upscale"][f"{name}:{src.name}"] = run_job(wf, extra, {key: src}, OUT / f"up_{name}")
            save_state(st)
    save_state(st)
    log("STEP DONE", STEP)


try:
    main()
except SystemExit as e:
    log("STOP", e)
except Exception:  # noqa: BLE001 - reported, then Blender quits
    traceback.print_exc()
    log("STEP FAILED")
SESSION.close()
time.sleep(1)
bpy.ops.wm.quit_blender()
