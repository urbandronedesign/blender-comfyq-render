# SPDX-License-Identifier: GPL-3.0-or-later
"""Class presets in Blender: build a built-in preset (workflows picked from what is served), save the
current graph as a preset file, load one. The header's **Presets** menu lists them all."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty

from .. import match as scene_match
from ..client.params import is_disabled
from ..runtime.session import SESSION
from . import kinds, presets
from .tree import WORKFLOW_NODE, socket_kind

SOURCE_TYPES = {"ComfyQSceneRenderNode", "ComfyQMaskNode", "ComfyQSceneAnimationNode", "ComfyQImageNode",
                "ComfyQVideoFileNode", "ComfyQAudioFileNode", "ComfyQPromptNode"}


def _in_graph(context) -> bool:
    from .ui import _in_graph as f
    return f(context)


def _inputs_of(wf_id: str) -> list[str] | None:
    specs = SESSION.specs.get(wf_id)
    return None if specs is None else [s.kind for s in specs if s.kind in ("image", "mask", "video", "audio")]


def when_specs(ids, then, fail, timeout: float = 25.0) -> None:
    """Load the parameters of these workflows, then call `then()` on the main thread (or `fail(msg)`)."""
    from ..ops.workflow import request_specs
    ids = [i for i in dict.fromkeys(ids) if i]
    for wf_id in ids:
        if wf_id not in SESSION.specs:
            request_specs(wf_id)
    t0 = time.monotonic()

    def wait():
        missing = [i for i in ids if i not in SESSION.specs]
        if not missing:
            then()
            return None
        if time.monotonic() - t0 > timeout:
            fail("Could not load the settings of: " + ", ".join(missing))
            return None
        return 0.2
    bpy.app.timers.register(wait, first_interval=0.0)


def _origin(tree) -> tuple[float, float]:
    """Right of what the graph already holds (a preset never covers existing nodes)."""
    if not len(tree.nodes):
        return 0.0, 0.0
    return max(n.location.x + n.width for n in tree.nodes) + 1400.0, 0.0


# -- building -------------------------------------------------------------------------------------------
def choose(preset: presets.Preset) -> dict[str, str | None]:
    """node name -> workflow id for each role (parameters must be loaded for the candidates)."""
    inputs = {w.id: _inputs_of(w.id) for w in SESSION.workflows}
    served = SESSION.served_ids()
    return {n.name: presets.pick(n.role, SESSION.workflows, inputs, served) for n in preset.nodes if n.role}


def _match_scene(node, specs, scene) -> None:
    from ..ops.workflow import scene_facts
    m = scene_match.match(specs, scene_facts(scene))
    for key, v in m.values.items():
        it = node.item(key)
        if it is None:
            continue
        if it.kind in ("int", "seed"):
            it.i = int(v)
        elif it.kind == "float":
            it.f = float(v)


def _input(node, kind: str, nth: int):
    if not kind:
        return node.inputs[0] if node.inputs else None
    socks = [s for s in node.inputs if socket_kind(s) == kind]
    return socks[nth] if nth < len(socks) else None


def _enable(node, key: str) -> bool:
    """Switch an input on that the workflow's own rule hides (e.g. tick "Use a start image")."""
    specs = SESSION.specs.get(node.workflow_id, [])
    spec = next((s for s in specs if s.key == key), None)
    if spec is None or not is_disabled(spec, node.current_values(), specs):
        return True
    other = node.item(spec.disabled_when["param"])
    if other is None or other.kind != "bool":
        return False
    other.b = not spec.disabled_when.get("equals")
    node.refresh_visibility(specs)
    return True


def build_builtin(preset: presets.Preset, tree, scene, choice: dict[str, str | None]) -> list:
    """Make the preset's nodes and links (the chosen workflows' parameters must be loaded)."""
    ox, oy = _origin(tree)
    made = {}
    for pn in preset.nodes:
        if pn.role is not None and not choice.get(pn.name):
            continue
        node = tree.nodes.new(pn.type)
        node.location = (ox + pn.x, oy + pn.y)
        if pn.type == WORKFLOW_NODE:
            node.workflow_id = choice[pn.name]
            specs = SESSION.specs[node.workflow_id]
            node.build(specs)
            _match_scene(node, specs, scene)
        else:
            node.label = pn.name
            for k, v in pn.props.items():
                setattr(node, k, v)
        made[pn.name] = node
    for link in preset.links:
        src, dst = made.get(link.src), made.get(link.dst)
        if src is None or dst is None or not src.outputs:
            continue
        sock = _input(dst, link.kind, link.nth)
        if sock is None:
            continue
        if dst.bl_idname == WORKFLOW_NODE and not sock.enabled and not (link.enable and _enable(dst, sock.identifier)):
            continue                              # an input the workflow does not use as set up: leave it
        if not kinds.can_link(socket_kind(src.outputs[0]), socket_kind(sock)):
            continue
        tree.links.new(src.outputs[0], sock)
        if src.bl_idname == "ComfyQPromptNode" and not src.text and dst.bl_idname == WORKFLOW_NODE:
            it = dst.item(sock.identifier)          # start from the workflow's own example prompt
            if it is not None and it.s:
                src.text = it.s
    for name, node in list(made.items()):          # sources the chosen workflows did not need
        if node.bl_idname in SOURCE_TYPES and not any(o.is_linked for o in node.outputs):
            tree.nodes.remove(node)
            del made[name]
    return list(made.values())


# -- saved presets (exact graphs) ----------------------------------------------------------------------
_NODE_PROPS = {
    "ComfyQSceneRenderNode": ("style", "frame"), "ComfyQMaskNode": ("style", "frame"),
    "ComfyQSceneAnimationNode": ("style",), "ComfyQVideoFileNode": ("filepath",),
    "ComfyQAudioFileNode": ("filepath",), "ComfyQPromptNode": ("text",),
    "ComfyQViewerNode": ("auto",), "ComfyQImportNode": ("auto", "replace"),
    "ComfyQSaveNode": ("auto", "folder", "pattern"),
}


def _loc(node):
    loc = getattr(node, "location_absolute", None) or node.location
    return [round(loc[0], 1), round(loc[1], 1)]


def serialize(tree, label: str, description: str) -> dict:
    nodes = []
    for n in tree.nodes:
        d = {"name": n.name, "type": n.bl_idname, "location": _loc(n), "width": round(n.width, 1),
             "label": n.label, "parent": n.parent.name if n.parent else ""}
        for k in _NODE_PROPS.get(n.bl_idname, ()):
            d[k] = getattr(n, k)
        if n.bl_idname == "ComfyQPromptNode" and n.text_block is not None:
            d["text"] = n.text_block.as_string()
        elif n.bl_idname == "ComfyQImageNode" and n.image is not None and n.image.filepath:
            d["image"] = bpy.path.abspath(n.image.filepath)
        elif n.bl_idname == WORKFLOW_NODE:
            d["workflow_id"] = n.workflow_id
            d["params"] = [{"key": it.key, "s": it.s, "i": it.i, "f": it.f, "b": it.b, "randomize": it.randomize,
                            "text": it.text.as_string() if it.text is not None else None}
                           for it in n.params if it.workflow_id == n.workflow_id]
        elif n.bl_idname == "NodeFrame":
            d["height"] = round(n.height, 1)
        nodes.append(d)
    links = [{"from": lk.from_node.name, "from_socket": lk.from_socket.identifier,
              "to": lk.to_node.name, "to_socket": lk.to_socket.identifier} for lk in tree.links]
    return {"format": presets.FORMAT, "label": label, "description": description,
            "made": time.strftime("%Y-%m-%d %H:%M"), "nodes": nodes, "links": links}


def build_saved(data: dict, tree) -> list:
    """Re-create a saved graph (its workflows' parameters must be loaded)."""
    ox, oy = _origin(tree)
    made, placed = {}, []
    for d in data.get("nodes", []):
        try:
            node = tree.nodes.new(d["type"])
        except RuntimeError:
            continue                                   # a node type this version does not know
        made[d["name"]] = node
        node.label = d.get("label", "")
        if d.get("width"):
            node.width = d["width"]
        for k in _NODE_PROPS.get(d["type"], ()):
            if k in d:
                setattr(node, k, d[k])
        if d["type"] == "ComfyQPromptNode" and "\n" in (d.get("text") or ""):
            txt = bpy.data.texts.new(f"ComfyQ prompt · {node.name}")
            txt.write(d["text"])
            node.text, node.text_block = "", txt
        elif d["type"] == "ComfyQImageNode" and d.get("image") and Path(d["image"]).is_file():
            node.image = bpy.data.images.load(d["image"], check_existing=True)
        elif d["type"] == WORKFLOW_NODE and d.get("workflow_id") in SESSION.specs:
            node.workflow_id = d["workflow_id"]
            node.build(SESSION.specs[node.workflow_id])
            for p in d.get("params", []):
                it = node.item(p["key"])
                if it is None:
                    continue
                it.s, it.i, it.f, it.b, it.randomize = p["s"], p["i"], p["f"], p["b"], p["randomize"]
                if p.get("text"):
                    it.text = bpy.data.texts.new(f"ComfyQ prompt · {node.name}")
                    it.text.write(p["text"])
            node.refresh_visibility(SESSION.specs[node.workflow_id])
        elif d["type"] == "NodeFrame" and d.get("height"):
            node.height = d["height"]
        placed.append((node, d))
    for node, d in placed:                              # parents first, then absolute positions
        parent = made.get(d.get("parent", ""))
        if parent is not None:
            node.parent = parent
    for node, d in placed:
        x, y = d.get("location", (0, 0))
        if hasattr(node, "location_absolute"):
            node.location_absolute = (ox + x, oy + y)
        else:
            node.location = (ox + x, oy + y)
    for lk in data.get("links", []):
        a, b = made.get(lk["from"]), made.get(lk["to"])
        out = next((s for s in a.outputs if s.identifier == lk["from_socket"]), None) if a else None
        inp = next((s for s in b.inputs if s.identifier == lk["to_socket"]), None) if b else None
        if out is not None and inp is not None:
            tree.links.new(out, inp)
    return list(made.values())


def saved_presets() -> list[tuple[Path, dict]]:
    """(file, its header) of every saved preset, user folder first then the shared one."""
    out = []
    for folder in presets.folders():
        if folder.is_dir():
            for f in sorted(folder.glob("*.json")):
                out.append((f, _header(f)))
    return [(f, h) for f, h in out if h]


_HEADERS: dict[str, tuple[int, dict]] = {}


def _header(path: Path) -> dict:
    """Label + description of a preset file (cached by mtime: menus redraw often)."""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _HEADERS.get(str(path))
    if hit is None or hit[0] != mtime:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            head = {"label": d.get("label") or path.stem, "description": d.get("description", ""),
                    "workflows": sorted({n.get("workflow_id") for n in d.get("nodes", []) if n.get("workflow_id")})}
        except (OSError, ValueError):
            head = {}
        hit = (mtime, head)
        _HEADERS[str(path)] = hit
    return hit[1]


# -- operators -----------------------------------------------------------------------------------------
def _finish(tree_name: str, made_names: list[str], msg: str) -> None:
    from .ui import _frame_graph_soon
    tree = bpy.data.node_groups.get(tree_name)
    if tree is not None:
        for n in tree.nodes:
            n.select = n.name in made_names
    SESSION.status_note = msg
    _frame_graph_soon(selected=True)


class COMFYQ_OT_graph_preset(bpy.types.Operator):
    bl_idname = "comfyq.graph_preset"
    bl_label = "Add Preset"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    preset: StringProperty(options={"HIDDEN"})        # a built-in id, or the path of a saved preset

    @classmethod
    def poll(cls, context):
        return _in_graph(context) and context.space_data.edit_tree is not None and bool(SESSION.workflows)

    @classmethod
    def description(cls, context, properties):
        p = next((b for b in presets.BUILTIN if b.id == properties.preset), None)
        if p is not None:
            return p.description + ". Uses what ComfyQ serves now (served workflows first)"
        head = _header(Path(properties.preset))
        return (head.get("description") or "Saved preset") + "\nWorkflows: " + ", ".join(head.get("workflows", []))

    def execute(self, context):
        tree, scene = context.space_data.edit_tree, context.scene
        tree_name, scene_name = tree.name, scene.name
        builtin = next((b for b in presets.BUILTIN if b.id == self.preset), None)
        if builtin is not None:
            roles = [n.role for n in builtin.nodes if n.role]
            ids = [i for r in roles for i in presets.candidates(r, SESSION.workflows)]

            def then():
                t, sc = bpy.data.node_groups.get(tree_name), bpy.data.scenes.get(scene_name)
                choice = choose(builtin)
                missing = [k for k, v in choice.items() if not v]
                if missing:
                    SESSION.status_note = f"“{builtin.label}”: no workflow on this ComfyQ for " + ", ".join(missing)
                    return
                made = build_builtin(builtin, t, sc, choice)
                served = SESSION.served_ids()
                note = ", ".join(f"{k}: {v}" + ("" if v in served else " (not served)") for k, v in choice.items())
                _finish(tree_name, [n.name for n in made], f"Preset “{builtin.label}” — {note}")
            label = builtin.label
        else:
            path = Path(self.preset)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self.report({"ERROR"}, f"Cannot read {path.name}: {e}")
                return {"CANCELLED"}
            ids = [n.get("workflow_id", "") for n in data.get("nodes", [])]

            def then():
                made = build_saved(data, bpy.data.node_groups.get(tree_name))
                _finish(tree_name, [n.name for n in made], f"Preset “{data.get('label') or path.stem}” added")
            label = data.get("label") or path.stem

        def fail(msg):
            SESSION.status_note = msg
        SESSION.status_note = f"Loading the workflows of “{label}”…"
        when_specs(ids, then, fail)
        self.report({"INFO"}, SESSION.status_note)
        return {"FINISHED"}


def _slug(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip().replace(" ", "_")[:60] or "preset"


class COMFYQ_OT_graph_save_preset(bpy.types.Operator):
    bl_idname = "comfyq.graph_save_preset"
    bl_label = "Save Graph as Preset"
    bl_description = ("Save this graph (nodes, models, settings, prompts, links) as a class preset. "
                      "Copy the file to the students' presets folder, or set COMFYQ_BRIDGE_PRESETS to a shared folder")
    bl_options = {"REGISTER", "INTERNAL"}
    name: StringProperty(name="Name", default="My class preset")
    description: StringProperty(name="Description", default="")
    shared: BoolProperty(name="Save in the shared folder", default=False,
                         description="Save in the folder named by COMFYQ_BRIDGE_PRESETS (e.g. on the teacher's PC)")

    @classmethod
    def poll(cls, context):
        return _in_graph(context) and context.space_data.edit_tree is not None and len(context.space_data.edit_tree.nodes)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "name")
        col.prop(self, "description")
        folders = presets.folders()
        if len(folders) > 1:
            col.prop(self, "shared")
        col.label(text=str(folders[1] if self.shared and len(folders) > 1 else folders[0]), icon="FILE_FOLDER")

    def execute(self, context):
        folders = presets.folders()
        folder = folders[1] if self.shared and len(folders) > 1 else folders[0]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_slug(self.name)}.json"
        data = serialize(context.space_data.edit_tree, self.name, self.description)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        self.report({"INFO"}, f"Saved {path}")
        return {"FINISHED"}


class COMFYQ_OT_graph_presets_folder(bpy.types.Operator):
    bl_idname = "comfyq.graph_presets_folder"
    bl_label = "Open Presets Folder"
    bl_description = "Open the folder of saved presets (copy preset files here on every student PC)"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from ..ops.workflow import open_file
        folder = presets.folders()[0]
        folder.mkdir(parents=True, exist_ok=True)
        open_file(folder)
        return {"FINISHED"}


class COMFYQ_MT_graph_presets(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_graph_presets"
    bl_label = "Presets"
    bl_description = "Class presets: ready-made graphs to start from (built in, or saved by the teacher)"

    def draw(self, context):
        layout = self.layout
        if not SESSION.workflows:
            layout.operator("comfyq.connect", icon="LINKED")
            return
        layout.label(text="Class presets")
        for p in presets.BUILTIN:
            layout.operator(COMFYQ_OT_graph_preset.bl_idname, text=p.label, icon=p.icon).preset = p.id
        saved = saved_presets()
        if saved:
            layout.separator()
            layout.label(text="Saved")
            for path, head in saved:
                layout.operator(COMFYQ_OT_graph_preset.bl_idname, text=head["label"],
                                icon="FILE").preset = str(path)
        layout.separator()
        layout.operator(COMFYQ_OT_graph_save_preset.bl_idname, icon="FILE_TICK")
        layout.operator(COMFYQ_OT_graph_presets_folder.bl_idname, icon="FILE_FOLDER")


classes = (COMFYQ_OT_graph_preset, COMFYQ_OT_graph_save_preset, COMFYQ_OT_graph_presets_folder,
           COMFYQ_MT_graph_presets)
