# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU check of the Text → 3D class preset without Blender: the plugin's own ComfyQ client serves each model,
books the job and downloads the result, exactly the chain of the preset (its prompt → Flux2 Klein t2i → Pixal3D /
TRELLIS.2 built-in nodes, default settings). Needs the teacher profile (admin password) to switch models;
each main-workflow switch restarts ComfyQ (1–2 min). Refuses to switch while anyone has a job.

    .\\tools\\text3d_check.ps1 <out folder>        (runs this with Blender's Python + the plugin's wheels)
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from client.api import ComfyQClient
from client.profile import load_profile

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "live_check" / "text3d")
T2I, TO_3D = "image_flux2_klein_9b_t2i", "3d_pixal3d_trellis2_image_to_model"
PRESET = next((ROOT / "presets" / "class").glob("Text - 3D*.json"))


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def connect() -> ComfyQClient:
    prof = replace(load_profile(None), user="text3d-check")
    for _ in range(90):                                    # ComfyQ may be restarting
        try:
            c = ComfyQClient(prof)
            c.connect()
            time.sleep(2)                                  # first state_update (lanes, jobs)
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5)
    raise SystemExit(f"cannot connect: {last}")


def serve(c: ComfyQClient, wf: str) -> ComfyQClient:
    if wf in c.served_ids():
        log(f"{wf} already served")
        return c
    others = [f"{j.user_id}·{j.workflow_id}" for j in c.active_jobs()]
    if others:
        raise SystemExit(f"jobs on ComfyQ, not switching: {others}")
    fit = (c.server_lanes().get("fit") or {}).get(wf) or {}
    t0 = time.monotonic()
    if fit.get("ok"):
        log(f"loading {wf} alongside")
        c.open_lane(wf)
    else:
        log(f"loading {wf} as the main workflow (ComfyQ restarts; fit: {fit.get('reason')})")
        c.activate(wf)
        c.close()
        time.sleep(8)
        c = connect()
    while wf not in c.served_ids():
        if time.monotonic() - t0 > 900:
            raise SystemExit(f"{wf} not served after 15 min")
        time.sleep(5)
        try:
            c.close()
        except Exception:  # noqa: BLE001, S110
            pass
        c = connect()
    log(f"{wf} served after {time.monotonic() - t0:.0f} s; served: {sorted(c.served_ids())}")
    return c


def run(c: ComfyQClient, wf: str, values: dict, timeout: float) -> list[Path]:
    t0 = time.monotonic()
    job_id = c.submit(wf, values)
    log(f"booked {wf} {job_id[:8]}")
    job = c.wait(job_id, timeout_s=timeout)
    if not job.ok:
        raise SystemExit(f"{wf} {job.phase}: {job.error}")
    files = c.download_outputs(job, OUT / wf)
    log(f"{wf} done in {time.monotonic() - t0:.0f} s: {[f.name for f in files]}")
    return files


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    preset = json.loads(PRESET.read_text(encoding="utf-8"))
    prompt = next(n["text"] for n in preset["nodes"] if n["type"] == "ComfyQPromptNode")
    link = next(lk for lk in preset["links"] if lk["to"] != "Viewer" and lk["from"] == "Workflow")
    image_key = link["to_socket"]
    t2i_prompt = next(lk["to_socket"] for lk in preset["links"] if lk["from"] == "Prompt")
    log(f"preset {preset['label']!r}: prompt → {t2i_prompt}, picture → {TO_3D}.{image_key}")

    c = connect()
    c = serve(c, T2I)
    pic = next(f for f in run(c, T2I, {t2i_prompt: prompt}, 600) if f.suffix.lower() in (".png", ".jpg", ".webp"))
    c = serve(c, TO_3D)
    uploaded = c.upload(pic)
    files = run(c, TO_3D, {image_key: uploaded}, 2400)
    result = {"picture": str(pic), "files": [str(f) for f in files], "served_at_end": sorted(c.served_ids())}
    (OUT / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    log(f"DONE {result}")
    c.close()


if __name__ == "__main__":
    main()
