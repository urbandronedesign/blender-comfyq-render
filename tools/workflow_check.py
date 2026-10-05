# SPDX-License-Identifier: GPL-3.0-or-later
"""Run one ComfyQ workflow on the GPU without Blender, with the plugin's own client: serve it (a lane if it fits,
else the main workflow: ComfyQ restarts 1–2 min), book jobs, download the results. For workflows never run from
Blender. Needs the teacher profile (admin password) to switch models; refuses to switch while anyone has a job.

    .\\tools\\workflow_check.ps1 <workflow id> <out folder> [key=value | key=@file ...] [-- key=value ...]

Each `--` starts another job on the same model (e.g. the same seed with a setting at 0, to compare).
A value `@path` is uploaded and the job gets its ComfyQ file name. Numbers / true / false are typed.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from client.api import ComfyQClient
from client.profile import load_profile


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def connect(user: str = "workflow-check") -> ComfyQClient:
    prof = replace(load_profile(None), user=user)
    last = None
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
        c = connect(c.profile.user)
    while wf not in c.served_ids():
        if time.monotonic() - t0 > 900:
            raise SystemExit(f"{wf} not served after 15 min")
        time.sleep(5)
        try:
            c.close()
        except Exception:  # noqa: BLE001, S110
            pass
        c = connect(c.profile.user)
    log(f"{wf} served after {time.monotonic() - t0:.0f} s; served: {sorted(c.served_ids())}")
    return c


def run(c: ComfyQClient, wf: str, values: dict, out: Path, timeout: float = 2400) -> list[Path]:
    t0 = time.monotonic()
    job_id = c.submit(wf, values)
    log(f"booked {wf} {job_id[:8]} {json.dumps(values, ensure_ascii=False)[:160]}")
    job = c.wait(job_id, timeout_s=timeout)
    if not job.ok:
        raise SystemExit(f"{wf} {job.phase}: {job.error}")
    files = c.download_outputs(job, out)
    log(f"{wf} done in {time.monotonic() - t0:.0f} s: {[f.name for f in files]}")
    return files


def _value(c: ComfyQClient, raw: str):
    if raw.startswith("@"):
        return c.upload(Path(raw[1:]))
    if raw in ("true", "false"):
        return raw == "true"
    for kind in (int, float):
        try:
            return kind(raw)
        except ValueError:
            pass
    return raw


def main(argv: list[str]) -> None:
    wf, out, rest = argv[0], Path(argv[1]), argv[2:]
    jobs, cur = [], []
    for a in rest:
        if a == "--":
            jobs.append(cur)
            cur = []
        else:
            cur.append(a)
    jobs.append(cur)
    c = serve(connect(), wf)
    results = []
    for n, args in enumerate(jobs):
        values = {k: _value(c, v) for k, v in (a.split("=", 1) for a in args)}
        files = run(c, wf, values, out / f"job{n}")
        results.append({"values": {k: v for k, v in (a.split("=", 1) for a in args)}, "files": [str(f) for f in files]})
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps({"workflow": wf, "jobs": results,
                                                  "served_at_end": sorted(c.served_ids())}, indent=1), encoding="utf-8")
    log(f"DONE {wf}: {len(results)} job(s) → {out}")
    c.close()


if __name__ == "__main__":
    main(sys.argv[1:])
