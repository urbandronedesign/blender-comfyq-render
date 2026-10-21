# SPDX-License-Identifier: GPL-3.0-or-later
"""Run one saved class preset end to end, headless in the sandbox profile (Solo on), on the open scene: build it with
preset_ops.build_saved, run its last workflow node(s) as Run Graph does, report every node's result.

    blender -b <scene.blend> --online-mode --python tools/preset_e2e.py -- <preset.json> <out folder>
"""
import importlib
import json
import sys
import time
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
PRESET, OUT = Path(ARGS[0]), Path(ARGS[1])
MOD = "bl_ext.user_default.comfyq_bridge"
OUT.mkdir(parents=True, exist_ok=True)


def ext(name):
    return importlib.import_module(f"{MOD}.{name}")


def log(text, **data):
    print(time.strftime("%H:%M:%S"), "[e2e]", text, json.dumps(data, default=str) if data else "", flush=True)


def pump_until(pred, timeout):
    S, grun = ext("runtime.session").SESSION, ext("graph.run")
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        S.pump()
        grun.tick_all()
        if pred():
            return True
        time.sleep(0.2)
    return False


addon_utils.enable(MOD, default_set=True)
S, grun, preset_ops = ext("runtime.session").SESSION, ext("graph.run"), ext("graph.preset_ops")
prefs = bpy.context.preferences.addons[MOD].preferences
prefs.solo_mode = True                                              # sandbox profile only
S.connect(prefs.profile, "preset-e2e", prefs.url.strip())
if not pump_until(lambda: S.connected and S.workflows, 60):
    sys.exit("cannot connect")
data = json.loads(PRESET.read_text(encoding="utf-8"))
for d in data["nodes"]:
    if d.get("workflow_id"):
        S.specs[d["workflow_id"]] = S.client.params(d["workflow_id"])
tree = bpy.data.node_groups.new("E2E", "ComfyQGraphTree")
preset_ops.build_saved(data, tree, bpy.context.scene)
targets = grun.graph_targets(tree, bpy.context.scene)[0]
t0 = time.monotonic()
ok, msg = grun.start(tree, bpy.context.scene, targets, [])
log("Run Graph", ok=ok, msg=msg, targets=targets)
seen = {}


def done():
    for n in tree.nodes:
        note = getattr(n, "note", "")
        if note and seen.get(n.name) != note and "Loading its model" not in note:
            seen[n.name] = note
            log(f"  {n.label or n.name}: {note}")
    return tree.name not in grun.RUNS


pump_until(done, 3600)
report = {"preset": data["label"], "seconds": round(time.monotonic() - t0), "nodes": {}}
for n in tree.nodes:
    if n.bl_idname == "ComfyQWorkflowNode":
        report["nodes"][n.label or n.name] = {"workflow": n.workflow_id, "result": n.result, "note": n.note,
                                              "settings": n.current_values()}
report["ok"] = all(v["result"] and Path(v["result"]).is_file() for v in report["nodes"].values())
(OUT / "e2e_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
log("DONE", ok=report["ok"], seconds=report["seconds"],
    results={k: Path(v["result"]).name if v["result"] else v["note"] for k, v in report["nodes"].items()})
S.close()
