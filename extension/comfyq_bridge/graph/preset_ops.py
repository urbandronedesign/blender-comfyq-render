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
                "ComfyQVideoFileNode", "ComfyQAudioFileNode", "ComfyQPromptNode", "ComfyQFolderNode"}


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
    if preset.steps and made:
        note = steps_note(tree, preset.steps, f"How to use · {preset.label}")
        xs = [n.location.x for n in made.values()]
        note.location = (min(xs) - note.width - 60, max(n.location.y for n in made.values()))
        made["How to use"] = note
    for name, node in list(made.items()):          # sources the chosen workflows did not need
        if node.bl_idname in SOURCE_TYPES and not any(o.is_linked for o in node.outputs):
            tree.nodes.remove(node)
            del made[name]
    return list(made.values())


# -- saved presets (exact graphs) ----------------------------------------------------------------------
_NODE_PROPS = {
    "ComfyQSceneRenderNode": ("style", "frame", "cycles_samples"), "ComfyQMaskNode": ("style", "frame"),
    "ComfyQSceneAnimationNode": ("style",), "ComfyQVideoFileNode": ("filepath",),
    "ComfyQAudioFileNode": ("filepath",), "ComfyQPromptNode": ("text",),
    "ComfyQViewerNode": ("auto",), "ComfyQImportNode": ("auto", "replace", "place", "on_ground", "size"),
    "ComfyQSaveNode": ("auto", "folder", "pattern", "batch_suffix"), "ComfyQFolderNode": ("folder", "recursive"),
    "ComfyQSurfacesNode": ("intro", "outro", "threshold"),
}


def _loc(node):
    loc = getattr(node, "location_absolute", None) or node.location
    return [round(loc[0], 1), round(loc[1], 1)]


def steps_note(tree, lines, name: str):
    """A Frame showing numbered steps (a Text block): the "How to use" note of a preset."""
    txt = bpy.data.texts.new(name)
    txt.write("\n".join(lines))
    note = tree.nodes.new("NodeFrame")
    note.label = "How to use"
    note.text = txt
    note.shrink = False
    note.label_size = 20
    note.width, note.height = 560, 70 + 30 * len(lines)
    return note


def serialize(tree, label: str, description: str) -> dict:
    nodes = []
    for n in tree.nodes:
        d = {"name": n.name, "type": n.bl_idname, "location": _loc(n), "width": round(n.width, 1),
             "label": n.label, "parent": n.parent.name if n.parent else ""}
        for k in _NODE_PROPS.get(n.bl_idname, ()):
            d[k] = getattr(n, k)
        if n.bl_idname == "ComfyQPromptNode" and n.text_block is not None:
            d["text"] = n.text_block.as_string()
        elif n.bl_idname == "ComfyQSurfacesNode":
            d["text"] = n.value()                       # the surfaces list: "label: material" lines
        elif n.bl_idname == "ComfyQImageNode" and n.image is not None and n.image.filepath:
            d["image"] = bpy.path.abspath(n.image.filepath)
        elif n.bl_idname == WORKFLOW_NODE:
            d["workflow_id"] = n.workflow_id
            d["params"] = [{"key": it.key, "s": it.s, "i": it.i, "f": it.f, "b": it.b, "randomize": it.randomize,
                            "text": it.text.as_string() if it.text is not None else None}
                           for it in n.params if it.workflow_id == n.workflow_id]
        elif n.bl_idname == "NodeFrame":
            d["height"] = round(n.height, 1)
            if n.text is not None:                      # a note (e.g. "How to use" steps)
                d["text"] = n.text.as_string()
        nodes.append(d)
    links = [{"from": lk.from_node.name, "from_socket": lk.from_socket.identifier,
              "to": lk.to_node.name, "to_socket": lk.to_socket.identifier} for lk in tree.links]
    return {"format": presets.FORMAT, "label": label, "description": description,
            "made": time.strftime("%Y-%m-%d %H:%M"), "nodes": nodes, "links": links}


def build_saved(data: dict, tree, scene=None) -> list:
    """Re-create a saved graph (its workflows' parameters must be loaded). A workflow node saved with
    `"match_scene": true` (the generated library) keeps the workflow's defaults and takes duration /
    size / fps from `scene`, like the built-in presets."""
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
        elif d["type"] == "ComfyQSurfacesNode" and d.get("text"):
            if node.text_block is None:
                node.text_block = bpy.data.texts.new(f"ComfyQ surfaces · {node.name}")
            node.text_block.clear()
            node.text_block.write(d["text"])
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
            if d.get("match_scene") and scene is not None:
                _match_scene(node, SESSION.specs[node.workflow_id], scene)
            node.refresh_visibility(SESSION.specs[node.workflow_id])
        elif d["type"] == "NodeFrame" and d.get("height"):
            node.height = d["height"]
            if d.get("text"):
                node.text = bpy.data.texts.new(f"{d.get('label') or 'Note'} · {data.get('label', '')}".strip(" ·"))
                node.text.write(d["text"])
                node.shrink = False
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


def preset_folders() -> list[Path]:
    """This PC's presets folder, then the shared ones (preference, profile, COMFYQ_BRIDGE_PRESETS)."""
    from ..prefs import shared_presets_dirs
    return presets.folders(shared_presets_dirs())


def saved_presets() -> list[tuple[Path, dict]]:
    """(file, its header) of every saved preset, this PC's folder first then the shared ones. Files in a
    subfolder (one level) are grouped: header["group"] = the subfolder's name (a submenu);
    header["shared"] = the file comes from a shared folder."""
    out = []
    for n, folder in enumerate(preset_folders()):
        if folder.is_dir():
            files = sorted(folder.glob("*.json")) + sorted(folder.glob("*/*.json"))
            for f in files:
                head = _header(f)
                if head:
                    out.append((f, {**head, "group": "" if f.parent == folder else f.parent.name,
                                    "shared": n > 0}))
    return out


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
def wrap_in_frame(tree, names: list[str], label: str):
    """Put a preset's nodes in a Frame named after it (kept where they are), so presets added beside each
    other stay apart and Run Graph can run one of them (select its Frame)."""
    nodes = [tree.nodes[n] for n in names if n in tree.nodes and tree.nodes[n].parent is None]
    if not nodes or all(n.bl_idname == "NodeFrame" for n in nodes):
        return None
    frame = tree.nodes.new("NodeFrame")
    frame.label = label
    frame.label_size = 28
    frame.shrink = True
    for n in nodes:
        loc = tuple(getattr(n, "location_absolute", n.location))
        n.parent = frame
        if hasattr(n, "location_absolute"):
            n.location_absolute = loc
    return frame


def _finish(tree_name: str, made_names: list[str], msg: str, label: str = "") -> None:
    from .ui import _frame_graph_soon
    tree = bpy.data.node_groups.get(tree_name)
    if tree is not None:
        frame = wrap_in_frame(tree, made_names, label) if label else None
        if frame is not None:
            made_names = [*made_names, frame.name]
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
        if not (_in_graph(context) and context.space_data.edit_tree is not None):
            return False
        if not SESSION.workflows:
            cls.poll_message_set("Connect to ComfyQ first: presets are built from the models it offers")
            return False
        return True

    @classmethod
    def description(cls, context, properties):
        p = next((b for b in presets.BUILTIN if b.id == properties.preset), None)
        if p is not None:
            return p.description + ". Uses what ComfyQ serves now (served workflows first)"
        head = _header(Path(properties.preset))
        return (head.get("description") or "Saved preset") + "\nWorkflows: " + ", ".join(head.get("workflows", []))

    def execute(self, context):
        label = add_preset(context.space_data.edit_tree.name, context.scene.name, self.preset)
        if label is None:
            self.report({"ERROR"}, SESSION.status_note)
            return {"CANCELLED"}
        self.report({"INFO"}, SESSION.status_note)
        return {"FINISHED"}


def add_preset(tree_name: str, scene_name: str, preset: str, done=None) -> str | None:
    """Add a preset (built-in id or saved file) once its workflows' parameters are loaded.
    `done(ok, message, node names)` is called at the end (main thread). None = could not start."""
    def finish(ok: bool, msg: str, names=()):
        SESSION.status_note = msg
        if ok:
            _finish(tree_name, list(names), msg, label)
        if done is not None:
            done(ok, msg, list(names))

    builtin = next((b for b in presets.BUILTIN if b.id == preset), None)
    if builtin is not None:
        roles = [n.role for n in builtin.nodes if n.role]
        ids = [i for r in roles for i in presets.candidates(r, SESSION.workflows)]

        def then():
            t, sc = bpy.data.node_groups.get(tree_name), bpy.data.scenes.get(scene_name)
            choice = choose(builtin)
            missing = [k for k, v in choice.items() if not v]
            if missing:
                finish(False, f"“{builtin.label}”: no workflow on this ComfyQ for " + ", ".join(missing))
                return
            made = build_builtin(builtin, t, sc, choice)
            served = SESSION.served_ids()
            note = ", ".join(f"{k}: {v}" + ("" if v in served else " (not served)") for k, v in choice.items())
            finish(True, f"Preset “{builtin.label}” — {note}", [n.name for n in made])
        label = builtin.label
    else:
        path = Path(preset)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            SESSION.status_note = f"Cannot read {path.name}: {e}"
            return None
        ids = [n.get("workflow_id", "") for n in data.get("nodes", [])]
        label = data.get("label") or path.stem

        def then():
            made = build_saved(data, bpy.data.node_groups.get(tree_name), bpy.data.scenes.get(scene_name))
            finish(True, f"Preset “{label}” added", [n.name for n in made])

    SESSION.status_note = f"Loading the workflows of “{label}”…"
    when_specs(ids, then, lambda msg: finish(False, msg))
    return label


def _slug(name: str) -> str:
    return re.sub(r"[^\w\- ]+", "", name).strip().replace(" ", "_")[:60] or "preset"


class COMFYQ_OT_graph_save_preset(bpy.types.Operator):
    bl_idname = "comfyq.graph_save_preset"
    bl_label = "Save Graph as Preset..."
    bl_description = ("Save this graph (nodes, models, settings, prompts, links) as a class preset. "
                      "Copy the file to the students' presets folder, or save it in the Shared Presets Folder")
    bl_options = {"REGISTER", "INTERNAL"}
    name: StringProperty(name="Name", default="My class preset")
    description: StringProperty(name="Description", default="")
    shared: BoolProperty(name="Save in the shared folder", default=False,
                         description="Save in the shared presets folder (Preferences › Student, or the profile's presets_dir)")

    @classmethod
    def poll(cls, context):
        if not (_in_graph(context) and context.space_data.edit_tree is not None):
            return False
        if not len(context.space_data.edit_tree.nodes):
            cls.poll_message_set("The graph is empty")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "name")
        col.prop(self, "description")
        folders = preset_folders()
        if len(folders) > 1:
            col.prop(self, "shared")
        col.label(text=str(folders[1] if self.shared and len(folders) > 1 else folders[0]), icon="FILE_FOLDER")

    def execute(self, context):
        folders = preset_folders()
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
    shared: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, props):
        if props and props.shared:
            return "Open the shared presets folder (every PC that uses it sees its presets)"
        return cls.bl_description

    def execute(self, context):
        from ..ops.workflow import open_file
        folders = preset_folders()
        folder = folders[1] if self.shared and len(folders) > 1 else folders[0]
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.report({"ERROR"}, f"Cannot open {folder}: {e.strerror or e}")
            return {"CANCELLED"}
        open_file(folder)
        saved = len(list(folder.glob("*.json")))
        self.report({"INFO"}, f"Presets folder: {folder} ({saved} saved preset{'s' if saved != 1 else ''}"
                              f"{'; use Save Graph as Preset' if not saved else ''})")
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
        groups = list(dict.fromkeys(h["group"] for _p, h in saved if h["group"]))
        _GROUPS[:] = groups[:len(GROUP_MENUS)]
        for shared, title in ((False, "Saved on This PC"), (True, "Shared")):
            mine = [(p, h) for p, h in saved if h["shared"] == shared]
            if not mine:
                continue
            layout.separator()
            layout.label(text=title)
            for path, head in mine:
                if not head["group"]:
                    layout.operator(COMFYQ_OT_graph_preset.bl_idname, text=head["label"],
                                    icon="FILE").preset = str(path)
            for i, name in enumerate(_GROUPS):
                if any(h["group"] == name for _p, h in mine):
                    layout.menu(GROUP_MENUS[i].bl_idname, text=name, icon="FILE_FOLDER")
        layout.separator()
        layout.operator(COMFYQ_OT_graph_save_preset.bl_idname, icon="FILE_TICK")
        layout.operator(COMFYQ_OT_graph_presets_folder.bl_idname, icon="FILE_FOLDER")
        if len(preset_folders()) > 1:
            layout.operator(COMFYQ_OT_graph_presets_folder.bl_idname, text="Open Shared Presets Folder",
                            icon="FILE_FOLDER").shared = True


# Subfolders of the presets folders are submenus. Menus are classes, so a fixed pool is registered;
# the Presets menu assigns the group names when it draws (before any submenu can open).
_GROUPS: list[str] = []


def _group_menu(i: int):
    def draw(self, context):
        if i >= len(_GROUPS):
            return
        for path, head in saved_presets():
            if head["group"] == _GROUPS[i]:
                self.layout.operator(COMFYQ_OT_graph_preset.bl_idname, text=head["label"],
                                     icon="FILE").preset = str(path)
    return type(f"COMFYQ_MT_graph_preset_group_{i}", (bpy.types.Menu,),
                {"bl_idname": f"COMFYQ_MT_graph_preset_group_{i}", "bl_label": "Presets", "draw": draw})


GROUP_MENUS = tuple(_group_menu(i) for i in range(12))

classes = (COMFYQ_OT_graph_preset, COMFYQ_OT_graph_save_preset, COMFYQ_OT_graph_presets_folder,
           COMFYQ_MT_graph_presets, *GROUP_MENUS)
