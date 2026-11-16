# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU check of Find by Name (audit 2, phase 5): SAM3 finds what is named in the camera view, the found surface is
the zone, and the inpaint library preset repaints only it. Hosted by preset_run_headless.py (main() runs on a worker
thread; Blender work goes through the MCP server's on_main).

    blender -b <scene.blend> --online-mode --python tools/preset_run_headless.py -- <out> --module=mask_find_check
        --words=sofa --prompt="..."
"""
import json
import shutil
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.mcp.tools import on_main
from bl_ext.user_default.comfyq_bridge.ops import mask as mk
from bl_ext.user_default.comfyq_bridge.passes import zone_scene

tool = None
OUT = Path(".")
ASSETS = RESULTS = None
WORDS = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--words=")), "sofa")
PROMPT = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--prompt=")),
              "a deep emerald green velvet sofa with brass legs, same size and place, soft daylight, photorealistic")
PRESET = "Flux2 Klein 9B — inpaint (paint & prompt)"


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 600:
        ok, info = tool("blender_scene_info")
        if ok and info.get("comfyq", {}).get("connected"):
            break
        time.sleep(5)
    time.sleep(5)

    def start():
        sc = bpy.context.scene
        zone_scene.clear(sc)
        for o in sc.objects:
            o.select_set(False)
        sc.comfyq.zone_words = WORDS
        return bpy.ops.comfyq.zone_find()
    log("find:", WORDS, on_main(start))
    t1 = time.monotonic()
    while mk.FIND["running"] and time.monotonic() - t1 < 900:
        time.sleep(3)
    info = on_main(lambda: zone_scene.found_info(bpy.context.scene))
    found = [(r["label"], r.get("coverage_pct")) for r in info.get("regions", [])]
    log("found:", found, "errors:", info.get("errors"), f"({round(time.monotonic() - t1)} s)")
    rec = {"words": WORDS, "found": found, "find_seconds": round(time.monotonic() - t1), "prompt": PROMPT}
    if info.get("picture"):
        shutil.copyfile(info["picture"], OUT / "find_picture.png")
    if not found:
        rec["ok"] = False
        (OUT / "find.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        return

    def mask_files():
        sc = bpy.context.scene
        m = zone_scene.mask(sc, [], "MATERIAL", 1280)
        return str(m), str(mk.mask_preview(m))
    m, prev = on_main(mask_files)
    shutil.copyfile(m, OUT / "find_mask.png")
    shutil.copyfile(prev, OUT / "find_mask_preview.png")
    ok, added = tool("blender_graph_add_preset", preset=PRESET)
    names = list(added["nodes"])
    ok, g = tool("blender_graph_status")
    wf = next(n for n in g["nodes"] if n["node"] in names and n.get("workflow_id"))
    prompt = next(n for n in names if n.startswith("Prompt"))
    tool("blender_graph_set", node=prompt, key="text", value=PROMPT)
    mnode = next((n for n in names if n.startswith("Mask")), None)
    if mnode:
        tool("blender_graph_set", node=mnode, key="style", value="MATERIAL")
    ok, msg = tool("blender_graph_run", node=wf["node"], force=True)
    log("run:", msg)
    t2 = time.monotonic()
    while time.monotonic() - t2 < 1800:
        time.sleep(5)
        ok, st = tool("blender_graph_status")
        if not ok or st.get("running"):
            continue
        me = next(n for n in st["nodes"] if n["node"] == wf["node"])
        if me.get("result") or me.get("error"):
            rec.update(ok=bool(me.get("result")), result=me.get("result"), error=me.get("error"),
                       inpaint_seconds=round(time.monotonic() - t2))
            if me.get("result"):
                shutil.copyfile(me["result"], OUT / f"find_result{Path(me['result']).suffix}")
            break
    log("RESULT", json.dumps(rec, ensure_ascii=False))
    (OUT / "find.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
