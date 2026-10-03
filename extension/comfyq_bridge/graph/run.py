# SPDX-License-Identifier: GPL-3.0-or-later
"""Running the graph: render the sources a job needs, book workflow nodes in dependency order,
pass results downstream, reuse results that are still up to date.

`engine.py` decides what to run (no bpy); this module does it. A run lives in memory (RUNS) and is
advanced by the add-on timer (`tick_all`). Results stay on their nodes (saved with the file).
"""
from __future__ import annotations

import random
from pathlib import Path

import bpy

from ..client.params import is_disabled
from ..importers.media import IMAGE_EXTS
from ..passes import animation, render
from ..props import INT_MAX
from ..runtime.session import SESSION
from . import engine, kinds
from .tree import TREE, WORKFLOW_NODE, frame_number, remember_result, socket_kind, upstream

SCENE_SOURCES = {"ComfyQSceneRenderNode", "ComfyQMaskNode", "ComfyQSceneAnimationNode"}
RUNS: dict[str, Run] = {}            # tree name -> the run in progress


# -- the graph as plain data -------------------------------------------------------------------------
def _links_into(node) -> dict[str, object]:
    """input key -> the node linked to it (through Reroutes; enabled inputs, right kinds only)."""
    out = {}
    for sock in node.inputs:
        if sock.enabled:
            src, from_sock = upstream(sock)
            if src is not None and kinds.can_link(socket_kind(from_sock), socket_kind(sock)):
                out[sock.identifier] = src
    return out


def _own_hash(node, scene) -> str:
    """What the node's result depends on, besides its inputs."""
    idn = node.bl_idname
    if idn == WORKFLOW_NODE:
        linked = set(_links_into(node))
        vals = {it.key: it.value() for it in node.params
                if it.workflow_id == node.workflow_id and it.key not in linked
                and not (it.kind == "seed" and it.randomize)}            # a random seed is not a change
        return engine.settings_hash(node.workflow_id, vals)
    if idn in SCENE_SOURCES:
        # an edit of what the camera sees (objects, meshes, camera, animation…) makes what it feeds out of date
        frame = frame_number(scene, node.frame) if hasattr(node, "frame") else (scene.frame_start, scene.frame_end)
        sel = sorted(o.name for o in scene.objects if o.select_get()) if idn == "ComfyQMaskNode" else []
        return engine.settings_hash(idn, node.style, frame, render.scene_fingerprint(scene, node.style), sel)
    if idn == "ComfyQImageNode":
        img = node.image
        return engine.settings_hash(idn, img.name if img else "", img.filepath if img else "",
                                    img.is_dirty if img else False)
    if idn in ("ComfyQVideoFileNode", "ComfyQAudioFileNode"):
        p = Path(bpy.path.abspath(node.filepath)) if node.filepath else None
        return engine.settings_hash(idn, str(p), p.stat().st_mtime if p and p.is_file() else 0)
    if idn == "ComfyQPromptNode":
        return engine.settings_hash(idn, node.value())
    return engine.settings_hash(idn, node.name)


def describe(tree, scene) -> dict[str, engine.GNode]:
    nodes = {}
    for node in tree.nodes:
        if node.bl_idname == "NodeReroute" or node.bl_idname == "NodeFrame":
            continue
        wf = node.bl_idname == WORKFLOW_NODE
        nodes[node.name] = engine.GNode(
            name=node.name, workflow=wf, own=_own_hash(node, scene),
            inputs={key: src.name for key, src in _links_into(node).items()},
            has_result=wf and bool(node.result) and Path(node.result).is_file(),
            result_hash=node.result_hash if wf else "")
    return nodes


# -- a run -----------------------------------------------------------------------------------------------
class Run:
    def __init__(self, tree, scene, todo: list[str], nodes: dict[str, engine.GNode], selected: list[str],
                 after: list[str] | tuple = ()):
        self.tree_name, self.scene_name = tree.name, scene.name
        self.nodes = nodes
        self.pending = set(todo)
        self.booking: set[str] = set()
        self.booked: dict[str, str] = {}               # node -> job id
        self.selected = selected
        self.targets = [n for n in todo if not any(engine._depends_on(nodes, m, n) for m in todo if m != n)]
        self.hashes = {name: engine.node_hash(nodes, name) for name in todo}
        self.renders: dict[tuple, Path] = {}          # one render per source per run (fan-out shares it)
        self.error = ""
        self.after = list(after)                       # output nodes to act on at the end (their Generate)

    def tree(self):
        t = bpy.data.node_groups.get(self.tree_name)
        return t if t is not None and t.bl_idname == TREE else None

    def tick(self) -> bool:
        """Advance; False once finished."""
        tree, scene = self.tree(), bpy.data.scenes.get(self.scene_name)
        if tree is None or scene is None:
            return False
        for name, job_id in list(self.booked.items()):
            view = SESSION.jobs.get(job_id)
            node = tree.nodes.get(name)
            if view is None or node is None or not view.job.done:
                continue
            if view.job.ok and view.files:
                remember_result(node, job_id, str(pick_result(view.files, node.output_kind)), self.hashes[name])
                self.pending.discard(name)
                del self.booked[name]
            elif not view.job.ok:
                self._fail(tree, name, f"{view.job.phase}: {view.job.error or 'no result'}")
        for name in sorted(self.pending):
            if name in self.booked or name in self.booking:
                continue
            if engine.ready(self.nodes, name, self.pending):
                self._book(tree, scene, name)
        if not self.pending and not self.booking:
            if self.after:
                if not self.error:
                    act_outputs(tree, scene, self.after)
            else:
                self._show_result(tree)
            return False
        return True

    def _fail(self, tree, name: str, why: str) -> None:
        node = tree.nodes.get(name)
        if node is not None:
            node.note = why
        self.booked.pop(name, None)
        self.booking.discard(name)
        self.pending.discard(name)
        for other in list(self.pending):                 # what depends on it cannot run
            if engine._depends_on(self.nodes, other, name):
                self.pending.discard(other)
                n = tree.nodes.get(other)
                if n is not None:
                    n.note = f"Not run: “{name}” failed"
        self.error = f"{name}: {why}"

    def _book(self, tree, scene, name: str) -> None:
        from ..ops.generate import book
        node = tree.nodes.get(name)
        specs = SESSION.specs.get(node.workflow_id) if node else None
        if node is None or specs is None:
            self._fail(tree, name, "workflow settings not loaded")
            return
        try:
            values, uploads = self._collect(scene, node, specs)
        except RuntimeError as e:
            self._fail(tree, name, str(e))
            return
        node.note = ""
        node.job_id, node.result = "", ""
        node.pending_hash = self.hashes[name]
        self.booking.add(name)

        def booked(job_id, name=name):
            self.booking.discard(name)
            self.booked[name] = job_id
            t = self.tree()
            n = t.nodes.get(name) if t else None
            if n is not None:
                n.job_id = job_id

        def failed(msg, name=name):
            t = self.tree()
            if t is not None:
                self._fail(t, name, msg)

        book(scene, node.workflow_id, specs, values, uploads, purpose="graph",
             label=f"Graph · {node.draw_label()}", on_booked=booked, on_error=failed)

    def _collect(self, scene, node, specs) -> tuple[dict, dict[str, Path]]:
        current = node.current_values()
        linked = _links_into(node)
        values: dict = {}
        uploads: dict[str, Path] = {}
        for s in specs:
            it = node.item(s.key)
            if it is None or is_disabled(s, current, specs):
                continue
            src = linked.get(s.key)
            if s.is_media:
                if src is not None:
                    uploads[s.key] = self._file(scene, src, s)
                elif s.required:
                    raise RuntimeError(f"Link an input to “{s.label}”")
            elif s.kind in ("prompt", "text"):
                v = _text(src) if src is not None else it.value()
                if v:
                    values[s.key] = v
            elif s.kind == "seed":
                if it.randomize:
                    it.i = random.randint(0, INT_MAX)
                values[s.key] = it.i
            else:
                v = it.value()
                if v != "" and v is not None:
                    values[s.key] = v
        return values, uploads

    def _file(self, scene, src, spec) -> Path:
        """The file a source (or an upstream workflow node) gives to `spec`."""
        edge = spec.max_input_edge
        key = (src.name, edge)
        if key in self.renders:
            return self.renders[key]
        idn = src.bl_idname
        if idn == "ComfyQSceneRenderNode":
            p = render.structure_pass(scene, src.style, edge, frame_number(scene, src.frame))
        elif idn == "ComfyQMaskNode":
            sel = [o for o in scene.objects if o.name in self.selected]
            p = render.mask_pass(scene, sel, src.style, edge, frame_number(scene, src.frame))
        elif idn == "ComfyQSceneAnimationNode":
            p = animation.animation_pass(scene, src.style, edge, force=True).path
        elif idn == "ComfyQImageNode":
            if src.image is None:
                raise RuntimeError(f"“{src.name}”: choose an image")
            p = render.image_pass(src.image, edge)
        elif idn in ("ComfyQVideoFileNode", "ComfyQAudioFileNode"):
            p = Path(bpy.path.abspath(src.filepath)) if src.filepath else None
            if p is None or not p.is_file():
                raise RuntimeError(f"“{src.name}”: choose a file")
        elif idn == WORKFLOW_NODE:
            p = Path(src.result) if src.result else None
            if p is None or not p.is_file():
                raise RuntimeError(f"“{src.name}” has no result yet")
            if p.suffix.lower() in IMAGE_EXTS:        # AI images can exceed ComfyQ's upload limits
                img = bpy.data.images.load(str(p), check_existing=True)
                p = render.image_pass(img, edge)
        else:
            raise RuntimeError(f"“{src.name}” cannot feed “{spec.label}”")
        self.renders[key] = p
        return p

    def _show_result(self, tree) -> None:
        """When the run ends, show what the node you ran made (the ComfyQ viewer, if open)."""
        from ..ui import player
        node = next((tree.nodes.get(t) for t in self.targets if tree.nodes.get(t) is not None), None)
        if node is None or not node.result or not Path(node.result).is_file():
            return
        area = player.viewer_area()
        if area is not None and Path(node.result).suffix.lower() in IMAGE_EXTS | _EXTS["video"]:
            img = bpy.data.images.load(node.result, check_existing=True)
            player.show_media(area, img)


_EXTS = {"image": IMAGE_EXTS, "video": {".mp4", ".mov", ".webm", ".mkv", ".gif"}, "model": {".glb", ".gltf"},
         "splat": {".ply", ".splat", ".spz"}, "audio": {".wav", ".mp3", ".flac", ".ogg", ".m4a"},
         "text": {".txt", ".json", ".md"}}


def pick_result(files: list[Path], kind: str) -> Path:
    """The output that matches what the node produces (a video job may also return a preview image)."""
    return next((f for f in files if f.suffix.lower() in _EXTS.get(kind, ())), files[0])


def _text(src) -> str:
    if src.bl_idname == "ComfyQPromptNode":
        return src.value()
    if src.bl_idname == WORKFLOW_NODE and src.result and Path(src.result).is_file():
        return Path(src.result).read_text(encoding="utf-8", errors="replace").strip()
    return ""


# -- starting and advancing runs -------------------------------------------------------------------------
def act_outputs(tree, scene, names) -> list[str]:
    from . import outputs
    msgs = []
    for name in names:
        node = tree.nodes.get(name)
        if node is not None:
            msgs.append(outputs.act(node, scene, force=True) or f"{name}: nothing to {node.verb} yet")
    return msgs


def start(tree, scene, targets: list[str], selected: list[str], force: bool = False,
          after: list[str] | tuple = ()) -> tuple[bool, str]:
    """Plan and start a run; (ok, message). `after`: output nodes to act on once it is done (their
    Generate: upstream results still up to date are reused, and then they just act)."""
    if not SESSION.connected:
        return False, "Connect to ComfyQ first"
    if tree.name in RUNS:
        return False, "This graph is already running (cancel its jobs first)"
    nodes = describe(tree, scene)
    try:
        todo = engine.plan(nodes, targets, force=force, reuse_targets=bool(after))
    except engine.CycleError as e:
        return False, f"The graph loops: {e}"
    if not todo and after:
        return True, "Up to date: " + "; ".join(act_outputs(tree, scene, after))
    if not todo:
        return False, "Nothing to run: add a workflow node"
    served = SESSION.served_ids()
    unloaded = [n for n in todo if tree.nodes[n].workflow_id not in SESSION.specs]
    if unloaded:
        return False, "Still loading the settings of: " + ", ".join(unloaded)
    unserved = [tree.nodes[n].draw_label() for n in todo if tree.nodes[n].workflow_id not in served]
    if unserved:
        return False, "Not served right now (ask the teacher): " + ", ".join(unserved)
    missing = []
    for name in todo:
        node = tree.nodes[name]
        specs = SESSION.specs[node.workflow_id]
        cur = node.current_values()
        linked = set(_links_into(node))
        missing += [f"{name} › {s.label}" for s in specs
                    if s.is_media and s.required and s.key not in linked and not is_disabled(s, cur, specs)]
    if missing:
        return False, "Link these inputs first: " + "; ".join(missing)
    run = Run(tree, scene, todo, nodes, selected, after)
    RUNS[tree.name] = run
    run.tick()
    reused = [n for n in engine.order(nodes, targets) if n not in todo]
    msg = f"Running {len(todo)} job(s): " + " → ".join(todo)
    if reused:
        msg += f" (reusing {', '.join(reused)})"
    return True, msg


def tick_all() -> None:
    for name, run in list(RUNS.items()):
        try:
            alive = run.tick()
        except Exception as e:  # noqa: BLE001 - a broken run must not stop the timer
            import traceback
            traceback.print_exc()
            run.error, alive = str(e), False
        if not alive:
            RUNS.pop(name, None)
            SESSION.status_note = f"Graph: {run.error}" if run.error else "Graph run finished"


def running(tree_name: str, node_name: str) -> str:
    """'' if not part of a run; else what it is doing ("waiting", "sending", "booked")."""
    run = RUNS.get(tree_name)
    if run is None or node_name not in run.pending:
        return ""
    if node_name in run.booked:
        return "booked"
    if node_name in run.booking:
        return "sending"
    return "waiting"
