# SPDX-License-Identifier: GPL-3.0-or-later
"""Live proof of the bridge's lane-restore wait (runtime/serving.py): the race of lane_race_check.py step B, but the
lane is opened with the plugin's own serving.choose + serve right after a restart. Expected: it waits for the
restoring lane, gets its own port, the job works. Ends with ComfyQ as at the start (main + restored lane).

    <blender python> tools/lane_race_fix_check.py <main workflow> <restored lane> <test lane> <test.glb>
"""
from __future__ import annotations

import importlib
import sys
import time
import types
from pathlib import Path

import lane_race_check
from lane_race_check import (
    MAIN,
    RESTORED,
    TEST,
    connect,
    lanes,
    listening,
    note,
    report,
    restart_to,
    uv_job,
    wait_lane,
)

lane_race_check.OUT = lane_race_check.OUT / "fix"           # its own report: live_check/lane_race/fix/report.json
lane_race_check.OUT.mkdir(parents=True, exist_ok=True)

# the add-on's runtime/serving.py without bpy: a stand-in parent package so its relative imports resolve
EXT = Path(__file__).resolve().parents[1] / "extension" / "comfyq_bridge"
pkg = types.ModuleType("cqb")
pkg.__path__ = [str(EXT)]
sys.modules["cqb"] = pkg
serving = importlib.import_module("cqb.runtime.serving")

report["steps"].clear()
c = connect("lane-race-check")
note("start", served=sorted(c.served_ids()), lanes=lanes(c), listening=listening())
if TEST in c.served_ids():
    c.close_lane(TEST)
c = restart_to(c, MAIN)
restoring = serving.restoring(c)
t0 = time.monotonic()
how = serving.choose(c, TEST, "auto")
waited = round(time.monotonic() - t0)
serving.serve(c, TEST, how)
wait_lane(c, TEST)
wait_lane(c, RESTORED)
note("C plugin opened the lane right after the restart", restoring_at_start=restoring, waited_s=waited, how=how,
     lanes=lanes(c), listening=listening())
note("C job", **uv_job(c))
c.close_lane(TEST)
c = restart_to(c, MAIN)
wait_lane(c, RESTORED)
note("end", served=sorted(c.served_ids()), lanes=lanes(c), listening=listening())
c.close()
print("DONE", flush=True)
