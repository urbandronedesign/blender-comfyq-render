# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproduce ComfyQ's lane-port race with ComfyQ's admin API (no ComfyQ code changed). Teacher profile needed.

    .\\tools\\workflow_check.ps1 is the runner pattern; run this one with:
    & "<blender python>" tools/lane_race_check.py <main workflow> <restored lane> <test lane> <test.glb>

After a restart ComfyQ restores its remembered lanes in the background (restoreRemembered() is not awaited);
LaneManager._nextPort() only counts lanes already registered, and a lane registers only after its ComfyUI has
started (~1 min). A lane opened during that window gets the same port as the one being restored.

  clean : test lane closed, <main> made main (restart), wait until <restored lane> is back
  A     : open <test lane> after the restore -> expect its own port, a job works
  B     : close it, restart, open <test lane> WHILE <restored lane> restores -> expect the same port twice
  end   : close <test lane>, restart once more so the machine is as at the start (main + restored lane)
Writes live_check/lane_race/report.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from workflow_check import connect, log

MAIN, RESTORED, TEST, GLB = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
OUT = Path(__file__).resolve().parents[1] / "live_check" / "lane_race"
OUT.mkdir(parents=True, exist_ok=True)
LANES_DIR = Path(r"F:\ComfyQ\.comfyq-lanes")
report: dict = {"main": MAIN, "restored": RESTORED, "test": TEST, "steps": []}


def note(step, **data):
    log(f"{step} {json.dumps(data, default=str)}")
    report["steps"].append({"t": time.strftime("%H:%M:%S"), "step": step, **data})
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def lanes(c):
    return [{"wf": ln["workflowId"], "port": ln["port"], "primary": ln.get("primary"), "state": ln.get("state")}
            for ln in c.server_lanes().get("lanes") or []]


def listening():
    out = subprocess.run(["netstat", "-ano"], capture_output=True, check=False).stdout.decode("ascii", "replace")
    return sorted({int(m.group(1)) for m in re.finditer(r":(81[89]\d)\s+\S+\s+LISTENING", out)})


def port_errors(wf, since):
    hits = []
    for f in (LANES_DIR / wf / "user").glob("comfyui_*.log"):
        if f.stat().st_mtime >= since:
            hits += [ln.strip() for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                     if "already in use" in ln]
    return hits


def restart_to(c, wf):
    """Make `wf` main (ComfyQ restarts); return a fresh client once the main lane serves it."""
    c.activate(wf)
    c.close()
    time.sleep(8)
    t0 = time.monotonic()
    while True:
        c = connect("lane-race-check")
        if wf in c.served_ids():
            note("main up", wf=wf, after_s=round(time.monotonic() - t0), lanes=lanes(c))
            return c
        c.close()
        if time.monotonic() - t0 > 900:
            raise SystemExit("main lane not back after 15 min")
        time.sleep(3)


def wait_lane(c, wf, timeout=600):
    t0 = time.monotonic()
    while wf not in c.served_ids() or any(ln["wf"] == wf and ln["state"] not in ("idle", "busy") for ln in lanes(c)):
        if time.monotonic() - t0 > timeout:
            return False
        time.sleep(3)
    return True


def uv_job(c):
    t0 = time.monotonic()
    name = c.upload(GLB)
    key = next(s.key for s in c.params(TEST) if s.kind == "model")
    job = c.wait(c.submit(TEST, {key: name}), timeout_s=900)
    return {"ok": job.ok, "phase": job.phase, "error": job.error, "s": round(time.monotonic() - t0)}



def main():
    c = connect("lane-race-check")
    busy = [f"{j.user_id}·{j.workflow_id}" for j in c.active_jobs()]
    if busy:
        raise SystemExit(f"jobs on ComfyQ, not touching it: {busy}")
    note("start", served=sorted(c.served_ids()), lanes=lanes(c), listening=listening())

    # clean
    if TEST in c.served_ids():
        c.close_lane(TEST)
    c = restart_to(c, MAIN)
    ok = wait_lane(c, RESTORED)
    note("clean", restored_back=ok, lanes=lanes(c), listening=listening())

    # A: no race
    t = time.time()
    c.open_lane(TEST)
    wait_lane(c, TEST)
    note("A lane opened after the restore", lanes=lanes(c), listening=listening(), port_errors=port_errors(TEST, t))
    note("A job", **uv_job(c))

    # B: race
    c.close_lane(TEST)
    c = restart_to(c, MAIN)
    restoring = RESTORED not in c.served_ids()
    t = time.time()
    try:
        c.open_lane(TEST)
        opened = "ok"
    except Exception as e:  # noqa: BLE001
        opened = repr(e)
    wait_lane(c, RESTORED)
    wait_lane(c, TEST, 120)
    note("B lane opened while the restore ran", restore_still_running_when_opened=restoring, open=opened,
         lanes=lanes(c), listening=listening(), port_errors=port_errors(TEST, t) + port_errors(RESTORED, t))
    note("B job", **uv_job(c))

    # end: as at the start
    c.close_lane(TEST)
    c = restart_to(c, MAIN)
    wait_lane(c, RESTORED)
    note("end", served=sorted(c.served_ids()), lanes=lanes(c), listening=listening())
    c.close()
    log("DONE")


if __name__ == "__main__":
    main()
