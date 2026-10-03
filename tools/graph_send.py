"""Send a job from the ComfyQ node graph on a real project (visible Mixar), and follow it.

  mixar --online-mode --python tools/graph_send.py -- <file.mixar> <prompt.txt>

Opens the file after startup (launch-crash workaround), builds
  Scene Animation ──► <served V2V workflow> ◄── Scene Render (first frame, start image)
with the prompt from <prompt.txt>, the bar's settings of that workflow and the scene's timing,
presses Generate on the node, and logs every step to live_check/graph_send_report.json.
Mixar stays open on the graph and the result.
"""
import json
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy
from bpy.app.handlers import persistent

ARGS = sys.argv[sys.argv.index("--") + 1:]
FILE, PROMPT = ARGS[0], Path(ARGS[1]).read_text(encoding="utf-8").strip()
OUT = Path(r"E:\zaki\MixarAI\live_check")
OUT.mkdir(exist_ok=True)
MOD = "bl_ext.user_default.comfyq_bridge"
report: dict = {"file": FILE, "steps": []}


def log(msg, **data):
    print("[graph_send]", msg, data or "", flush=True)
    report["steps"].append({"t": time.strftime("%H:%M:%S"), "msg": msg, **data})
    (OUT / "graph_send_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def ext(name):
    return sys.modules[f"{MOD}.{name}"]


def S():
    return ext("runtime.session").SESSION


def every(fn, interval=0.5):
    """Run fn from a timer until it returns True (errors are logged and stop it)."""
    def tick():
        try:
            return None if fn() else interval
        except Exception as e:  # noqa: BLE001
            log("ERROR", error=repr(e), trace=traceback.format_exc())
            return None
    bpy.app.timers.register(tick, first_interval=interval)


def graph_area():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == "NODE_EDITOR" and a.spaces.active.tree_type == "ComfyQGraphTree":
                return w, a
    return None, None


def connect():
    prefs = bpy.context.preferences.addons[MOD].preferences
    S().connect(prefs.profile, prefs.student_name.strip(), prefs.url.strip())
    log("connecting", user=prefs.student_name)
    every(lambda: S().connected and S().served_ids() and (load() or True))


def load():
    wf = min(S().served_ids())
    report["workflow"] = wf
    log("served workflow", workflow=wf)
    ext("ops.workflow").request_specs(wf)
    every(lambda: wf in S().specs and (open_graph(wf) or True))


def open_graph(wf):
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.comfyq.open_graph()
    every(lambda: graph_area()[1] is not None and (build(wf) or True))


def build(wf):
    gtree, props, match = ext("graph.tree"), ext("props"), ext("match")
    sc = bpy.context.scene
    specs = S().specs[wf]
    _w, area = graph_area()
    tree = area.spaces.active.node_tree
    tree.nodes.clear()
    anim = tree.nodes.new("ComfyQSceneAnimationNode")
    node = tree.nodes.new("ComfyQWorkflowNode")
    anim.location, node.location = (-420, 0), (0, 0)
    node.workflow_id = wf
    node.build(specs)
    copied = []
    for s in specs:                                    # the bar's settings of this workflow
        bar, it = props.param_item(sc, s.key, wf), node.item(s.key)
        if bar is None or it is None or s.is_media:
            continue
        it.s, it.i, it.f, it.b, it.randomize = bar.s, bar.i, bar.f, bar.b, bar.randomize
        if s.kind == "enum" and bar.s in s.options:
            it.e = bar.s
        copied.append(s.key)
    m = match.match(specs, ext("ops.workflow").scene_facts(sc))           # the scene's timing
    for key, v in m.values.items():
        it = node.item(key)
        if it.kind in ("int", "seed"):
            it.i = int(v)
        elif it.kind == "float":
            it.f = float(v)
    prompt = next(s for s in specs if s.kind == "prompt")
    it = node.item(prompt.key)
    it.text, it.s = None, PROMPT
    vid = next(x for x in node.inputs if gtree.socket_kind(x) == "video")
    tree.links.new(anim.outputs[0], vid)
    start = next((x for x in node.inputs if gtree.socket_kind(x) == "image" and x.enabled), None)
    if start is not None:                              # like the bar: the first frame as the start image
        bpy.ops.comfyq.graph_add_source(tree=tree.name, node=node.name, key=start.identifier)
    tree.nodes.active = node
    win, area = graph_area()
    with bpy.context.temp_override(window=win, area=area,
                                   region=next(r for r in area.regions if r.type == "WINDOW")):
        bpy.ops.node.view_all()
    log("graph built", nodes=[n.name for n in tree.nodes], links=[f"{lk.from_node.name} → {lk.to_socket.name}"
                                                                  for lk in tree.links],
        matched=m.notes, settings_from_bar=copied,
        settings={it.key: it.value() for it in node.params if it.workflow_id == wf and it.kind != "prompt"})
    bpy.app.timers.register(lambda: generate(tree.name, node.name) and None, first_interval=1.0)


def generate(tree_name, node_name):
    win, area = graph_area()
    with bpy.context.temp_override(window=win, area=area,
                                   region=next(r for r in area.regions if r.type == "WINDOW")):
        res = bpy.ops.comfyq.graph_run(tree=tree_name, node=node_name)
    log("Generate on the node", result=list(res), message=S().status_note)
    grun = ext("graph.run")
    state = {"phase": None, "jid": ""}

    def follow():
        tree = bpy.data.node_groups.get(tree_name)
        node = tree.nodes.get(node_name)
        if node.job_id and node.job_id != state["jid"]:
            state["jid"] = node.job_id
            log("booked", job=node.job_id)
        v = S().jobs.get(node.job_id) if node.job_id else None
        if v is not None and v.job.phase != state["phase"]:
            state["phase"] = v.job.phase
            log("job", phase=v.job.phase)
        if tree_name in grun.RUNS:
            return False
        trace(node)
        return True
    every(follow, 1.0)


def trace(node):
    job = S().client.http.get(f"/jobs/{node.job_id}")["job"] if node.job_id else {}
    params = job.get("params") or {}
    log("ComfyQ stored", status=job.get("status"), error=job.get("error_reason"),
        prompt_matches=job.get("prompt") == PROMPT,
        inputs={k: v for k, v in params.items() if isinstance(v, str) and v.lower().endswith((".mp4", ".png"))},
        result=node.result, note=node.note)
    log("DONE")


def _unhook():
    if _after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_after_load)


@persistent
def _after_load(_dummy):
    # Not removed here: Blender is walking the load_post list right now, and removing an entry
    # makes it skip the next handler (the add-on's own, in Blender 5.2's order). Remove it just after.
    bpy.app.timers.register(_unhook, first_interval=0.0)
    bpy.app.timers.register(lambda: connect() and None, first_interval=1.5)


addon_utils.enable(MOD, default_set=True)
bpy.app.handlers.load_post.append(_after_load)
bpy.app.timers.register(lambda: bpy.ops.wm.open_mainfile(filepath=FILE) and None, first_interval=4.0)
log("starting")
