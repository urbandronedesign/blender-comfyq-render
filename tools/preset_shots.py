# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshots of each preset's graph in Blender, for the run report (no GPU, nothing booked).

For every preset of a run (live_check/presets/results.json): rebuild its graph from the library file in
a clean ComfyQ graph, put the same test pictures in its image nodes, point the workflow node at the run's
result (so the node shows it as it did after the run), frame it and screenshot the node editor.

Run in a VISIBLE Blender with the extension installed (sandbox profile), on a copy of the run's scene:
    set BLENDER_USER_RESOURCES=<sandbox>
    blender <scene copy>.blend --online-mode --python tools/preset_shots.py -- <results.json> <out dir>
Writes <out dir>/<n>.png for the n-th record and quits.
"""
import json
import sys
import time
import traceback
from pathlib import Path

import bpy

MOD = "bl_ext.user_default.comfyq_bridge"
argv = sys.argv[sys.argv.index("--") + 1:]
RESULTS, OUT = Path(argv[0]).resolve(), Path(argv[1]).resolve()    # Blender reads relative paths its own way
ROOT = Path(__file__).resolve().parents[1]
LIBS = (ROOT / "presets" / "library", ROOT / "presets" / "class")
ONLY = next((a.split("=", 1)[1] for a in argv[2:] if a.startswith("--only=")), None)   # a substring of keys
ASSETS = RESULTS.parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)


def ext(name):
    return sys.modules[f"{MOD}.{name}"]


def redraw():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            a.tag_redraw()
        with bpy.context.temp_override(window=w):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def editor():
    """The main window's biggest area, turned into a Node Editor on our tree."""
    win = bpy.context.window_manager.windows[0]
    area = max(win.screen.areas, key=lambda a: a.width * a.height)
    if area.type != "NODE_EDITOR":
        area.type = "NODE_EDITOR"
    area.ui_type = "ComfyQGraphTree"
    return win, area


def preset_file(label: str) -> Path | None:
    for lib in LIBS:
        for f in lib.rglob("*.json"):
            if json.loads(f.read_text(encoding="utf-8")).get("label") == label:
                return f
    return None


def steps():
    S = ext("runtime.session").SESSION
    po = ext("graph.preset_ops")
    p = bpy.context.preferences.addons[MOD].preferences
    S.connect(p.profile, "report-shots", p.url.strip())
    t0 = time.monotonic()
    while not (S.connected and S.workflows) and time.monotonic() - t0 < 30:
        yield 0.5
    records = json.loads(RESULTS.read_text(encoding="utf-8"))
    tree = bpy.data.node_groups.get("Report") or bpy.data.node_groups.new("Report", "ComfyQGraphTree")
    win, area = editor()
    area.spaces.active.node_tree = tree
    yield 1.0
    for n, rec in enumerate(records):
        f = preset_file(rec["label"])
        if f is None or rec["key"].startswith("asset:") or (ONLY and ONLY not in rec["key"]):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            ids = [d.get("workflow_id") for d in data["nodes"] if d.get("workflow_id")]
            for wf in ids:
                if wf not in S.specs:
                    ext("ops.workflow").request_specs(wf)
            t0 = time.monotonic()
            while any(wf not in S.specs for wf in ids) and time.monotonic() - t0 < 20:
                yield 0.3
            for node in list(tree.nodes):
                tree.nodes.remove(node)
            made = po.build_saved(data, tree, bpy.context.scene)
            for node in made:
                if node.bl_idname == "ComfyQImageNode":
                    asset = (rec.get("inputs") or {}).get(node.label)
                    if asset and (ASSETS / asset).is_file():
                        node.image = bpy.data.images.load(str(ASSETS / asset), check_existing=True)
                elif node.bl_idname in ("ComfyQVideoFileNode", "ComfyQAudioFileNode"):
                    asset = (rec.get("inputs") or {}).get(node.label)
                    if asset:
                        node.filepath = str(ASSETS / asset)
                elif node.bl_idname == "ComfyQWorkflowNode" and rec.get("result"):
                    node.result = rec["result"]
                    node.job_id = rec.get("job_id") or ""
                elif node.bl_idname == "ComfyQWorkflowNode" and rec.get("error"):
                    node.note = rec["error"][:120]
            for node in made:
                node.select = False
            yield 1.5                                   # the add-on timer: thumbnails, visibility
            with bpy.context.temp_override(window=win, screen=win.screen, area=area,
                                           region=next(r for r in area.regions if r.type == "WINDOW")):
                bpy.ops.node.view_all()
            yield 1.0
            redraw()
            with bpy.context.temp_override(window=win, screen=win.screen, area=area):
                bpy.ops.screen.screenshot_area(filepath=str(OUT / f"{n:02d}.png"), check_existing=False)
            print(f"SHOT {n:02d} {rec['key']}", flush=True)
        except Exception:  # noqa: BLE001 - one preset must not stop the others
            traceback.print_exc()
            print(f"SHOT FAILED {n:02d} {rec['key']}", flush=True)
    print("SHOTS DONE", flush=True)
    bpy.ops.wm.quit_blender()


_gen = None


def tick():
    global _gen
    if _gen is None:
        _gen = steps()
    try:
        return next(_gen)
    except StopIteration:
        return None


bpy.app.timers.register(tick, first_interval=3.0)
