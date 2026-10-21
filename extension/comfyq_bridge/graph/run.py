# SPDX-License-Identifier: GPL-3.0-or-later
"""Running the graph: render the sources a job needs, book workflow nodes in dependency order,
pass results downstream, reuse results that are still up to date.

`engine.py` decides what to run (no bpy); this module does it. A run lives in memory (RUNS) and is
advanced by the add-on timer (`tick_all`). Results stay on their nodes (saved with the file).
"""
from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

import bpy

from ..client.params import is_disabled
from ..importers.media import IMAGE_EXTS
from ..passes import animation, mesh, render, shots
from ..props import INT_MAX
from ..runtime import serving
from ..runtime.session import SESSION
from ..wording import count
from . import engine, kinds
from .tree import (
    RUN_NODES,
    SURFACES_NODE,
    TREE,
    WORKFLOW_NODE,
    frame_number,
    remember_result,
    socket_kind,
    upstream,
)

SCENE_SOURCES = {"ComfyQSceneRenderNode", "ComfyQMaskNode", "ComfyQSceneAnimationNode"}
FOLDER_NODE = "ComfyQFolderNode"
OBJECTS_NODE = "ComfyQSceneObjectsNode"
FILE_NODES = ("ComfyQVideoFileNode", "ComfyQAudioFileNode", "ComfyQModelFileNode")
RUNS: dict[str, Run | BatchRun] = {}   # tree name -> the run in progress
SWITCH_TIMEOUT_S = 900               # solo mode: a model switch restarts ComfyQ (1–3 min); give up after this


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
        samples = node.cycles_samples if getattr(node, "style", "") == "CYCLES" else None
        return engine.settings_hash(idn, node.style, frame, render.scene_fingerprint(scene, node.style), sel, samples,
                                    shots.signature(scene, getattr(node, "shot", "")))
    if idn == "ComfyQImageNode":
        img = node.image
        return engine.settings_hash(idn, img.name if img else "", img.filepath if img else "",
                                    img.is_dirty if img else False)
    if idn == FOLDER_NODE:
        p = Path(node.current) if node.current else None
        return engine.settings_hash(idn, str(p), p.stat().st_mtime if p and p.is_file() else 0)
    if idn in FILE_NODES:
        p = Path(bpy.path.abspath(node.filepath)) if node.filepath else None
        return engine.settings_hash(idn, str(p), p.stat().st_mtime if p and p.is_file() else 0)
    if idn == OBJECTS_NODE:           # the objects sent and their shape, placement, modifiers
        return engine.settings_hash(idn, mesh.fingerprint(node.objects(scene)))
    if idn == "ComfyQPromptNode":
        return engine.settings_hash(idn, node.value())
    if idn == SURFACES_NODE:          # the list (labels + materials), the text around it, the threshold
        return engine.settings_hash(idn, node.value(), node.intro, node.outro, round(node.threshold, 3))
    return engine.settings_hash(idn, node.name)


def describe(tree, scene) -> dict[str, engine.GNode]:
    nodes = {}
    for node in tree.nodes:
        if node.bl_idname == "NodeReroute" or node.bl_idname == "NodeFrame":
            continue
        wf = node.bl_idname in RUN_NODES
        nodes[node.name] = engine.GNode(
            name=node.name, workflow=wf, own=_own_hash(node, scene),
            inputs={key: src.name for key, src in _links_into(node).items()},
            has_result=wf and bool(node.result) and Path(node.result).is_file(),
            result_hash=node.result_hash if wf else "")
    return nodes


# -- a run -----------------------------------------------------------------------------------------------
class Rendering(Exception):
    """A Cycles source is still rendering (in its own process): try again at the next tick."""


class Run:
    def __init__(self, tree, scene, todo: list[str], nodes: dict[str, engine.GNode], selected: list[str],
                 after: list[str] | tuple = (), solo: bool = False):
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
        # solo mode: nodes whose model ComfyQ does not serve wait until nothing runs, then the run switches
        # ComfyQ to that model (one switch at a time) and goes on. The last model stays loaded: the user's
        # next preset / run decides what comes next
        self.solo = solo
        self.switch: dict | None = None                # {"wf", "state": asking|loading|failed, "t0", "error"}
        self.switched = False
        self.surface_jobs: dict[str, list[str]] = {}   # Surfaces node -> its SAM3 job ids (a Stop cancels them)
        self.quiet = False                             # a batch step: no result shown at the end
        self.cycles: dict[tuple, render.CyclesRender] = {}   # Cycles sources rendering now (a Stop kills them)

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
        if self.switch is not None and not self._switch_tick(tree):
            return True                                # ComfyQ is loading a model (and may be restarting)
        if self.solo and not SESSION.connected:
            return True                                # reconnecting after a switch
        served = SESSION.served_ids()
        unserved = []
        for name in sorted(self.pending):
            if name in self.booked or name in self.booking:
                continue
            if engine.ready(self.nodes, name, self.pending):
                if self.solo and tree.nodes[name].workflow_id not in served:
                    unserved.append(name)
                else:
                    self._book(tree, scene, name)
        if unserved and not self.booked and not self.booking:
            self._start_switch(tree, tree.nodes[unserved[0]].workflow_id, unserved)
        if not self.pending and not self.booking:
            if self.after:
                if not self.error:
                    act_outputs(tree, scene, self.after)
            elif not self.quiet:
                self._show_result(tree)
            return False
        return True

    # -- solo mode: load the model a node needs ------------------------------------------------------------
    def _start_switch(self, tree, wf_id: str, waiting: list[str]) -> None:
        client = SESSION.client
        if client is None:
            return
        name = _wf_name(wf_id)
        others = serving.blockers(client)
        if others:                                     # someone's job: never switch under it; wait
            SESSION.status_note = (f"Solo: waiting for {count(len(others), 'job')} on ComfyQ before loading “{name}” "
                                   f"({others[0]})")
            return
        self.switch = {"wf": wf_id, "state": "asking", "t0": time.monotonic(), "error": ""}
        for n in waiting:
            node = tree.nodes.get(n)
            if node is not None and node.workflow_id == wf_id:
                node.note = "Loading its model in ComfyQ…"
        SESSION.status_note = f"Solo: loading “{name}” in ComfyQ (ComfyQ restarts, 1 to 2 minutes)…"
        sw = self.switch

        def work():
            how = serving.choose(client, wf_id, "auto")
            return how, serving.serve(client, wf_id, how)

        def done(res):
            how, closed = res
            sw.update(state="loading", how=how)
            if closed:                           # said, not silent: the served set changed for everyone
                SESSION.status_note += f" Closed to make room: {', '.join(_wf_name(w) for w in closed)}"

        def failed(msg):
            sw.update(state="failed", error=msg)
        SESSION.run(work, done, on_error=failed)

    def stop(self, tree) -> int:
        """Stop the run: cancel its booked jobs (and SAM3 jobs), stop waiting for a model. Returns the
        number of jobs cancelled. A model already being loaded by ComfyQ keeps loading."""
        client = SESSION.client
        ids = list(self.booked.values()) + [j for js in self.surface_jobs.values() for j in js]
        n = 0
        for jid in ids:
            view = SESSION.jobs.get(jid)
            if client is not None and (view is None or not view.job.done):
                try:
                    client.cancel(jid)
                    n += 1
                except Exception:  # noqa: BLE001, S110 - finished meanwhile, or not ours
                    pass
        self.end_renders()
        for name in list(self.pending):
            node = tree.nodes.get(name)
            if node is not None:
                node.note = "Stopped"
        self.pending.clear()
        self.booking.clear()
        self.booked.clear()
        self.switch = None
        self.error = "Stopped"
        return n

    def end_renders(self) -> None:
        """Kill the Cycles renders this run started and has not used (Stop, or the run ended early)."""
        for job in self.cycles.values():
            if job in render.ACTIVE:
                job.cancel()
        self.cycles.clear()

    def _switch_tick(self, tree) -> bool:
        """True once the switch is over (the model is served, or it failed)."""
        sw = self.switch
        wf_id = sw["wf"]
        secs = int(time.monotonic() - sw["t0"])
        for node in tree.nodes:                        # a counter on the waiting nodes: the wait is 1–2 min
            if node.bl_idname in RUN_NODES and node.workflow_id == wf_id and node.name in self.pending:
                note = f"Loading its model in ComfyQ… {secs // 60}:{secs % 60:02d} (usually 1 to 2 min)"
                if node.note != note:
                    node.note = note
        if sw["state"] == "loading" and SESSION.connected and wf_id in SESSION.served_ids():
            self.switch, self.switched = None, True
            for node in tree.nodes:
                if node.bl_idname in RUN_NODES and node.workflow_id == wf_id and node.name in self.pending:
                    node.note = ""
            SESSION.status_note = f"Solo: “{_wf_name(wf_id)}” loaded"
            return True
        late = time.monotonic() - sw["t0"] > SWITCH_TIMEOUT_S
        if sw["state"] == "failed" or late:
            why = sw["error"] or f"not served after {SWITCH_TIMEOUT_S // 60} min (see the ComfyQ console)"
            self.switch = None
            for name in [n for n in self.pending if tree.nodes.get(n) is not None
                         and tree.nodes[n].workflow_id == wf_id]:
                self._fail(tree, name, f"Could not load its model: {why}")
            return True
        return False

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
        if node is not None and node.bl_idname == SURFACES_NODE:
            self._book_surfaces(tree, scene, node)
            return
        specs = SESSION.specs.get(node.workflow_id) if node else None
        if node is None or specs is None:
            self._fail(tree, name, "workflow settings not loaded")
            return
        try:
            values, uploads = self._collect(scene, node, specs)
        except Rendering as r:
            _set_note(node, f"Waiting for the render: {r}")
            return
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

    def _book_surfaces(self, tree, scene, node) -> None:
        """SAM3 for each label of the list, in the background (one job per phrase); then the node's
        Prompt output names what was found. Same picture, labels and threshold as last time = the masks
        are reused (a material edit only rewrites the prompt)."""
        from ..passes import segment as seg
        from ..runtime import segmentation
        from ..surfaces import parse_surfaces
        name = node.name
        src = _links_into(node).get("Image")
        if src is None:
            self._fail(tree, name, "Link a picture into Image (a Scene Render, an image, a workflow's result)")
            return
        try:
            pic = self._file(scene, src, _PICTURE)
        except Rendering as r:
            _set_note(node, f"Waiting for the render: {r}")
            return
        except RuntimeError as e:
            self._fail(tree, name, str(e))
            return
        labels = [lab for lab, _m in parse_surfaces(node.value())]
        if not labels:
            self._fail(tree, name, "Write at least one surface (label: material)")
            return
        key = engine.settings_hash(labels, round(node.threshold, 3), engine.node_hash(self.nodes, src.name))
        same = node.masks_key == key and node.found() and node.result and Path(node.result).is_file()
        if same and all(Path(r["mask"]).is_file() for r in node.found()):
            remember_result(node, "", node.result, self.hashes[name])
            node.note = ""
            self.pending.discard(name)
            return
        node.note, node.pending_hash = f"SAM3: {count(len(labels), 'label')}…", self.hashes[name]
        self.booking.add(name)
        client, thr = SESSION.client, node.threshold
        out = seg.work_dir(f"graph_{segmentation.safe(name)}_{time.strftime('%Y%m%d_%H%M%S')}")
        tree_name = tree.name

        def note(msg):
            def go():
                t = bpy.data.node_groups.get(tree_name)
                n = t.nodes.get(name) if t is not None else None
                if n is not None:
                    n.note = f"SAM3: {msg}"
            SESSION.later(go)

        def work():
            return segmentation.segment(client, pic, labels, out, _on_main, thr, progress=note,
                                        on_booked=lambda ids: self.surface_jobs.__setitem__(name, ids))

        def done(res):
            if name not in self.booking:               # stopped meanwhile
                return
            self.booking.discard(name)
            t = self.tree()
            n = t.nodes.get(name) if t is not None else None
            if n is None:
                self.pending.discard(name)
                return
            n.regions = json.dumps(segmentation.region_dicts(res["regions"]))
            n.masks_key = key
            missed = [r.label for r in res["regions"] if not r.found] + list(res["errors"])
            n.note = f"Not found: {', '.join(missed)}" if missed else ""
            if res["overlay"]:
                remember_result(n, "", res["overlay"], self.hashes[name])
                self.pending.discard(name)
            else:
                self._fail(t, name, "Failed: SAM3 found none of the surfaces")

        def failed(msg):
            t = self.tree()
            if t is not None and name in self.booking:
                self._fail(t, name, f"Failed: {msg}")
        SESSION.run(work, done, label="Finding surfaces", on_error=failed)

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
        if idn == "ComfyQSceneRenderNode" and src.style == "CYCLES":
            # its own Blender process: the run goes on ticking (Blender stays responsive) until the file exists
            job = self.cycles.get(key)
            if job is None:
                with shots.apply(scene, src.shot):     # the copy of the file is saved here, with the shot's camera
                    job = self.cycles[key] = render.CyclesRender(scene, edge, frame_number(scene, src.frame),
                                                                 src.cycles_samples, tag=f"{self.tree_name}/{src.name}")
            try:
                p = job.poll()
            except RuntimeError:
                self.cycles.pop(key, None)
                raise
            if p is None:
                raise Rendering(job.text())
            self.cycles.pop(key, None)
        elif idn == "ComfyQSceneRenderNode":
            with shots.apply(scene, src.shot):
                p = render.structure_pass(scene, src.style, edge, frame_number(scene, src.frame), src.cycles_samples)
        elif idn == "ComfyQMaskNode":
            sel = [o for o in scene.objects if o.name in self.selected]
            with shots.apply(scene, src.shot):
                p = render.mask_pass(scene, sel, src.style, edge, frame_number(scene, src.frame))
        elif idn == "ComfyQSceneAnimationNode":
            with shots.apply(scene, src.shot):
                p = animation.animation_pass(scene, src.style, edge, force=True).path
        elif idn == "ComfyQImageNode":
            if src.image is None:
                raise RuntimeError(f"“{src.name}”: choose an image")
            p = render.image_pass(src.image, edge)
        elif idn == FOLDER_NODE:
            from . import batch
            p = Path(src.current) if src.current else None
            if p is None or not p.is_file():
                raise RuntimeError(f"“{src.name}”: Run Graph renders its files one by one")
            if p.suffix.lower() in batch.IMAGE_EXTS:     # within the upload limits, like any picture
                img = bpy.data.images.load(str(p), check_existing=False)
                try:
                    p = render.image_pass(img, edge)
                finally:
                    bpy.data.images.remove(img)            # a batch of hundreds must not fill the file
        elif idn == OBJECTS_NODE:           # its collection, else what was selected when the run started
            objs = src.objects(scene) if src.collection is not None else                 [o for o in scene.objects if o.name in self.selected]
            p = mesh.mesh_pass(objs)
        elif idn in FILE_NODES:
            p = Path(bpy.path.abspath(src.filepath)) if src.filepath else None
            if p is None or not p.is_file():
                raise RuntimeError(f"“{src.name}”: choose a file")
        elif idn in RUN_NODES:
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


class _Picture:                     # what a Surfaces node asks of its Image input (any size: SAM3 has no limit)
    max_input_edge = None
    label = "Image"


_PICTURE = _Picture()


def _on_main(fn, timeout: float = 300.0):
    """Run `fn` on Blender's main thread (from a worker) and return its result."""
    if threading.current_thread() is threading.main_thread():
        return fn()
    done, box = threading.Event(), {}

    def go():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001 - re-raised in the worker
            box["e"] = e
        finally:
            done.set()
    SESSION.later(go)
    if not done.wait(timeout):
        raise RuntimeError("Blender did not answer in time")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _set_note(node, text: str) -> None:
    if hasattr(node, "note") and node.note != text:     # writing the same value would still redraw
        node.note = text


def _text(src) -> str:
    if src.bl_idname == "ComfyQPromptNode":
        return src.value()
    if src.bl_idname == SURFACES_NODE:
        return src.prompt()
    if src.bl_idname == WORKFLOW_NODE and src.result and Path(src.result).is_file():
        return Path(src.result).read_text(encoding="utf-8", errors="replace").strip()
    return ""


# -- starting and advancing runs -------------------------------------------------------------------------
def _wf_name(wf_id: str) -> str:
    return next((w.name for w in SESSION.workflows if w.id == wf_id), wf_id)


def solo_on() -> bool:
    """Solo mode: ticked AND this is the teacher's PC (the profile holds ComfyQ's admin password)."""
    from ..prefs import prefs, teacher_unlocked
    try:
        p = prefs()
    except KeyError:
        return False
    return bool(p.solo_mode) and teacher_unlocked(p)


def graph_targets(tree, scene) -> tuple[list[str], bool]:
    """What Run Graph runs: the chains in the selection (selected nodes, and everything inside a selected
    Frame) — or, with nothing usable selected, every last node of the graph. (targets, from_selection)."""
    nodes = describe(tree, scene)
    sel = {n.name for n in tree.nodes if n.select}
    if sel:
        frames = {n.name for n in tree.nodes if n.select and n.bl_idname == "NodeFrame"}
        for n in tree.nodes:
            p = n.parent
            while p is not None:
                if p.name in frames:
                    sel.add(n.name)
                    break
                p = p.parent
        leaves = engine.leaves({k: v for k, v in nodes.items() if k in sel})
        if leaves:
            return leaves, True
    return engine.leaves(nodes), False


def chain_name(tree, name: str) -> str:
    """A chain's name for messages: its Frame's label (a preset), else the node's label."""
    node = tree.nodes.get(name)
    p = node.parent if node is not None else None
    while p is not None:
        if p.label:
            return p.label
        p = p.parent
    return node.draw_label() if node is not None and hasattr(node, "draw_label") else name


def models_to_load(tree, scene, targets: list[str], force: bool = False) -> list[str]:
    """Names of the models a solo run of `targets` would load (what ComfyQ does not serve now)."""
    try:
        todo = engine.plan(describe(tree, scene), targets, force=force)
    except engine.CycleError:
        return []
    served = SESSION.served_ids()
    wfs = dict.fromkeys(tree.nodes[n].workflow_id for n in todo if tree.nodes.get(n) is not None)
    return [_wf_name(w) for w in wfs if w and w not in served]


def act_outputs(tree, scene, names) -> list[str]:
    from . import outputs
    msgs = []
    for name in names:
        node = tree.nodes.get(name)
        if node is not None:
            msgs.append(outputs.act(node, scene, force=True) or f"{name}: nothing to {node.verb} yet")
    return msgs


def _check(tree, todo: list[str]) -> tuple[str, bool]:
    """('' or why it cannot run, solo): settings loaded, models served (or Solo may load them), inputs linked."""
    served = SESSION.served_ids()
    unloaded = [n for n in todo if tree.nodes[n].bl_idname == WORKFLOW_NODE
                and tree.nodes[n].workflow_id not in SESSION.specs]
    if unloaded:
        return "Still loading the settings of: " + ", ".join(unloaded), False
    unserved = [tree.nodes[n].draw_label() for n in todo if tree.nodes[n].workflow_id not in served]
    solo = bool(unserved) and solo_on()
    if unserved and not solo:
        return "Not served right now (ask the teacher): " + ", ".join(unserved), False
    if solo:
        others = serving.blockers(SESSION.client)
        if others:
            return (f"Solo mode: {count(len(others), 'job')} on ComfyQ ({others[0]}); models are only switched "
                    f"when nobody is using the server"), False
    missing = []
    for name in todo:
        node = tree.nodes[name]
        if node.bl_idname == SURFACES_NODE:
            if "Image" not in _links_into(node):
                missing.append(f"{name} › Image")
            continue
        specs = SESSION.specs[node.workflow_id]
        cur = node.current_values()
        linked = set(_links_into(node))
        missing += [f"{name} › {s.label}" for s in specs
                    if s.is_media and s.required and s.key not in linked and not is_disabled(s, cur, specs)]
    if missing:
        return "Link these inputs first: " + "; ".join(missing), False
    return "", solo


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
        chain = engine.plan(nodes, targets, force=True)
    except engine.CycleError as e:
        return False, f"The graph loops: {e}"
    folders = [n for n in nodes if tree.nodes[n].bl_idname == FOLDER_NODE
               and any(engine._depends_on(nodes, t, n) for t in chain)]
    if folders:                                    # a chain fed by a Media Folder: once per file
        return _start_batch(tree, scene, targets, selected, nodes, folders)
    todo = engine.plan(nodes, targets, force=force, reuse_targets=bool(after))
    if not todo and after:
        return True, "Up to date: " + "; ".join(act_outputs(tree, scene, after))
    if not todo:
        return False, "Nothing to run: add a workflow node"
    why, solo = _check(tree, todo)
    if why:
        return False, why
    served = SESSION.served_ids()
    run = Run(tree, scene, todo, nodes, selected, after, solo=solo)
    RUNS[tree.name] = run
    run.tick()
    reused = [n for n in engine.order(nodes, targets) if n not in todo]
    msg = f"Running {count(len(todo), 'job')}: " + " → ".join(todo)
    if solo:
        n_models = len({tree.nodes[n].workflow_id for n in todo if tree.nodes[n].workflow_id not in served})
        msg += f" (solo: loads {count(n_models, 'model')} in ComfyQ; each switch restarts it, 1 to 2 minutes)"
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
            run.end_renders()
            RUNS.pop(name, None)
            summary = getattr(run, "summary", "")
            SESSION.status_note = (f"Graph: {run.error}" if run.error else summary or "Graph run finished")


def running(tree_name: str, node_name: str) -> str:
    """'' if not part of a run; else what it is doing ("waiting", "sending", "booked")."""
    run = RUNS.get(tree_name)
    if isinstance(run, BatchRun):                  # a batch: what its current step is doing
        run = run.inner
    if run is None or node_name not in run.pending:
        return ""
    if node_name in run.booked:
        return "booked"
    if node_name in run.booking:
        return "sending"
    if run.cycles and any(engine._depends_on(run.nodes, node_name, k[0]) for k in run.cycles):
        return "rendering"
    return "waiting"


# -- batch renders: a chain fed by a Media Folder, once per file -----------------------------------------------
def _start_batch(tree, scene, targets, selected, nodes, folders) -> tuple[bool, str]:
    from . import batch
    from .tree import folder_files
    if len(folders) > 1:
        return False, "Use one Media Folder per run (" + ", ".join(folders) + ")"
    fnode = tree.nodes[folders[0]]
    if not fnode.folder or not Path(bpy.path.abspath(fnode.folder)).is_dir():
        return False, f"“{fnode.name}”: choose a folder of pictures or videos"
    linked = [o.name.lower() for o in fnode.outputs if o.is_linked]
    if len(linked) != 1:
        return False, f"“{fnode.name}”: link its Image output or its Video output (one of them)"
    kind = linked[0]
    root = Path(bpy.path.abspath(fnode.folder))
    files = folder_files(fnode, max_age=0)[kind]
    if not files:
        return False, f"No {kind}s in {root}" + (" or its subfolders" if fnode.recursive else "")
    saves = [n for n in nodes if tree.nodes[n].bl_idname == "ComfyQSaveNode" and engine._depends_on(nodes, n, fnode.name)]
    if not saves:
        return False, "Add a Save node after the workflow: the batch writes each result there"
    todo_files = batch.remaining(files, root, [tree.nodes[n].batch_suffix for n in saves])
    if not todo_files:
        return True, f"Nothing to do: all {count(len(files), kind)} are already in {batch.out_root(root).name}"
    fnode.current = str(todo_files[0])            # check the chain as the first file will run it
    first = describe(tree, scene)
    todo = engine.plan(first, targets, force=True)
    why, solo = _check(tree, todo)
    if why:
        fnode.current = ""
        return False, why
    run = BatchRun(tree, scene, fnode, root, todo_files, len(files), targets, selected, saves, solo)
    RUNS[tree.name] = run
    run.tick()
    done_before = len(files) - len(todo_files)
    msg = f"Batch: {count(len(todo_files), kind)} to render, one after the other, into {batch.out_root(root).name}"
    if done_before:
        msg += f" ({done_before} already done, skipped)"
    return True, msg


class BatchRun:
    """A chain fed by a Media Folder, run once per file, one job after the other (one student's batch never
    floods the class queue). Each step is an ordinary `Run`; after it, each Save node copies the result to the
    mirrored results folder. A failed file is noted and the batch goes on; Stop ends it (a new Run Graph resumes
    with the files that have no result yet)."""

    def __init__(self, tree, scene, fnode, root: Path, files: list[Path], total: int, targets, selected, saves,
                 solo: bool):
        self.tree_name, self.scene_name, self.folder_name = tree.name, scene.name, fnode.name
        self.root, self.files, self.total = root, files, total
        self.targets, self.selected, self.saves, self.solo = targets, selected, saves, solo
        self.i = 0
        self.inner: Run | None = None
        self.t_file = 0.0
        self.durations: list[float] = []
        self.failed: list[str] = []
        self.saved = 0
        self.error = ""
        self.summary = ""

    def tree(self):
        t = bpy.data.node_groups.get(self.tree_name)
        return t if t is not None and t.bl_idname == TREE else None

    def tick(self) -> bool:
        tree, scene = self.tree(), bpy.data.scenes.get(self.scene_name)
        if tree is None or scene is None or tree.nodes.get(self.folder_name) is None:
            return False
        if self.inner is not None:
            if self.inner.tick():
                return True
            self._finish_file(tree)
        while self.i < len(self.files):
            if self._start_file(tree, scene):
                return True
        self._end(tree)
        return False

    def _start_file(self, tree, scene) -> bool:
        """Start the run of the current file; False when there was nothing to run (then it is saved as is)."""
        f = self.files[self.i]
        tree.nodes[self.folder_name].current = str(f)
        nodes = describe(tree, scene)
        todo = engine.plan(nodes, self.targets, force=False)
        self.t_file = time.monotonic()
        if not todo:                                # the chain's results are already this file's
            self.inner = None
            self._save(tree, f)
            self.i += 1
            return False
        self.inner = Run(tree, scene, todo, nodes, self.selected, (), solo=self.solo)
        self.inner.quiet = True
        self.inner.tick()
        return True

    def _finish_file(self, tree) -> None:
        inner, f = self.inner, self.files[self.i]
        self.inner = None
        if inner.error:
            self.failed.append(f"{f.name}: {inner.error}")
        else:
            self._save(tree, f)
            self.durations.append(time.monotonic() - self.t_file)
        self.i += 1

    def _save(self, tree, f: Path) -> None:
        from . import outputs
        for name in self.saves:
            node = tree.nodes.get(name)
            media = outputs.linked(node) if node is not None else None
            if media is None or not media.ready:
                continue
            node.status = outputs.save_batch(node, media, self.root, f)
            node.handled = media.key()
            self.saved += 1

    def stop(self, tree) -> int:
        n = self.inner.stop(tree) if self.inner is not None else 0
        self.inner = None
        self.i = len(self.files)
        self.error = f"Batch stopped after {count(len(self.durations), 'file')}"
        self._end(tree)
        return n

    def end_renders(self) -> None:
        if self.inner is not None:
            self.inner.end_renders()

    def _end(self, tree) -> None:
        from . import batch
        node = tree.nodes.get(self.folder_name) if tree is not None else None
        if node is not None:
            node.current = ""
        done = len(self.durations)
        self.summary = (f"Batch done: {count(done, 'file')} rendered into {batch.out_root(self.root).name}"
                        + (f", {len(self.failed)} failed ({self.failed[0]})" if self.failed else ""))

    def status(self) -> dict:
        """What the Media Folder node shows: progress, the file now, time left."""
        from . import batch
        n = len(self.files)
        i = min(self.i, n)
        f = self.files[i] if i < n else None
        text = f"{i + 1 if f else n} / {n}"
        left = batch.eta(self.durations, n - i)
        if left:
            text += f" · {left}"
        if self.failed:
            text += f" · {len(self.failed)} failed"
        return {"factor": i / n if n else 1.0, "text": text,
                "file": str(f.relative_to(self.root)) if f else ""}


def batch_status(tree_name: str, node_name: str) -> dict | None:
    run = RUNS.get(tree_name)
    if isinstance(run, BatchRun) and run.folder_name == node_name:
        return run.status()
    return None
