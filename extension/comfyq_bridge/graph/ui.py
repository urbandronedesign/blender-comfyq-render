# SPDX-License-Identifier: GPL-3.0-or-later
"""Graph toolbar (Moodboard-style category buttons), add menus, operators, and the timer sync
that keeps workflow nodes in step with what ComfyQ serves."""
from __future__ import annotations

import json

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from .. import match as scene_match
from ..importers.media import IMAGE_EXTS, VIDEO_EXTS
from ..props import TABS, tab_of
from ..runtime.session import SESSION
from . import kinds, outputs
from .tree import (
    OUTPUT_NODES,
    SOURCE_FOR,
    SOURCE_NODES,
    TREE,
    WORKFLOW_NODE,
    animation_path,
    find_node,
    free_thumbnails,
    graph_trees,
    movie_thumbnail,
    remember_result,
    socket_kind,
    tab_workflows,
)

GRAPH_NAME = "ComfyQ Graph"


def _in_graph(context) -> bool:
    sp = context.space_data
    return sp is not None and sp.type == "NODE_EDITOR" and sp.tree_type == TREE


# -- adding nodes ------------------------------------------------------------------------------------
class COMFYQ_OT_graph_add(bpy.types.Operator):
    bl_idname = "comfyq.graph_add"
    bl_label = "Add ComfyQ Node"
    bl_description = "Add this node in the middle of the view"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    node_type: StringProperty()
    workflow_id: StringProperty()

    @classmethod
    def poll(cls, context):
        return _in_graph(context) and context.space_data.edit_tree is not None

    def execute(self, context):
        from ..ops.workflow import request_specs
        tree = context.space_data.edit_tree
        for n in tree.nodes:
            n.select = False
        node = tree.nodes.new(self.node_type)
        node.location = _view_center(context, node)
        node.select = True
        tree.nodes.active = node
        if self.workflow_id:
            node.workflow_id = self.workflow_id
            specs = SESSION.specs.get(self.workflow_id)
            if specs is not None:
                node.build(specs)
            else:
                request_specs(self.workflow_id)            # the timer builds the node once they arrive
        return {"FINISHED"}


def _view_center(context, node):
    area = context.area
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None:
        return (0.0, 0.0)
    x, y = region.view2d.region_to_view(region.width / 2, region.height / 2)
    scale = context.preferences.system.ui_scale or 1.0       # node locations are unscaled view units
    shift = 30 * sum(1 for n in context.space_data.edit_tree.nodes if n != node) % 300
    return (x / scale - node.width / 2 + shift, y / scale + 120 - shift)


def _draw_workflow_items(layout, wfs) -> None:
    from ..ops.workflow import shared_budget, vram_badge
    if SESSION.client is None:
        layout.operator("comfyq.connect", icon="LINKED")
        return
    if not wfs:
        layout.label(text="No workflow in this category")
    served = SESSION.served_ids()
    budget = shared_budget()
    for group in (True, False):
        items = [w for w in wfs if (w.id in served) == group]
        if group and items:
            layout.label(text="Served now")
        elif items:
            if any(w.id in served for w in wfs):
                layout.separator()
            layout.label(text="Not served (ask the teacher)")
        for w in items:
            badge, over = vram_badge(w, budget)
            row = layout.row()
            row.alert = over or w.unavailable
            op = row.operator(COMFYQ_OT_graph_add.bl_idname, text=f"{w.name}   ·  {badge}",
                              icon="ERROR" if w.unavailable else ("PLAY" if group else "NONE"))
            op.node_type, op.workflow_id = WORKFLOW_NODE, w.id


def _tab_menu(key: str, label: str, desc: str, icon: str):
    def draw(self, context):
        _draw_workflow_items(self.layout, tab_workflows(key))
    name = f"COMFYQ_MT_graph_{key.lower()}"
    return type(name, (bpy.types.Menu,), {"bl_idname": name, "bl_label": label, "draw": draw,
                                         "bl_description": f"Add a {label} workflow node: {desc}",
                                         "comfyq_icon": icon})


TAB_MENUS = [_tab_menu(key, label, desc, icon) for key, label, desc, icon, _n in TABS]


class COMFYQ_MT_graph_output(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_graph_output"
    bl_label = "Output"
    bl_description = "Add an output node (Viewer, Import to Scene, Save) or a Frame / Reroute"

    def draw(self, context):
        from .tree import OUTPUT_CLASSES
        for cls in OUTPUT_CLASSES:
            self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text=cls.bl_label, icon=cls.bl_icon).node_type = \
                cls.bl_idname
        self.layout.separator()
        self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text="Frame (note / group)",
                             icon="OBJECT_HIDDEN").node_type = "NodeFrame"
        self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text="Reroute", icon="DECORATE").node_type = "NodeReroute"


class COMFYQ_MT_graph_scene(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_graph_scene"
    bl_label = "Scene"
    bl_description = "Add a source node: scene render, clay animation, selection mask, image, video, audio, prompt"

    def draw(self, context):
        for cls in SOURCE_NODES:
            self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text=cls.bl_label, icon=cls.bl_icon).node_type = \
                cls.bl_idname


class COMFYQ_OT_graph_clear(bpy.types.Operator):
    bl_idname = "comfyq.graph_clear"
    bl_label = "Clear Graph"
    bl_description = "Remove every node of this graph"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _in_graph(context) and context.space_data.edit_tree is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Clear the graph?",
                                                     message="Every node of this graph is removed.",
                                                     confirm_text="Clear")

    def execute(self, context):
        context.space_data.edit_tree.nodes.clear()
        return {"FINISHED"}


# -- node operators (they name their node: tree + node, never a Python reference) ------------------
class _NodeOp:
    tree: StringProperty(options={"HIDDEN"})
    node: StringProperty(options={"HIDDEN"})
    key: StringProperty(options={"HIDDEN"})

    def target_node(self):
        return find_node(self.tree, self.node)


_WF_ITEMS: list[tuple[str, str, str]] = []          # kept alive: Blender keeps pointers to these strings
_TAB_LABEL = {key: label for key, label, *_rest in TABS}


def _workflow_items(self, context):
    from ..ops.workflow import shared_budget, vram_badge
    served, budget = SESSION.served_ids(), shared_budget()
    items = []
    for w in sorted(SESSION.workflows, key=lambda w: (w.id not in served, tab_of(w), w.name.lower())):
        badge, _over = vram_badge(w, budget)
        mark = "▶ " if w.id in served else ""
        items.append((w.id, f"{mark}{w.name}  ·  {_TAB_LABEL.get(tab_of(w), '')}  ·  {badge}",
                      (w.description or w.name)[:300]))
    _WF_ITEMS[:] = items or [("", "Connect to ComfyQ first", "")]
    return _WF_ITEMS


class COMFYQ_OT_graph_set_workflow(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_set_workflow"
    bl_label = "Change Model"
    bl_description = ("Choose this node's workflow (type to search; ▶ = served now). Settings are kept per "
                      "model, links come back on inputs of the same kind")
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    bl_property = "workflow"
    workflow: EnumProperty(name="Workflow", items=_workflow_items)

    def invoke(self, context, event):
        if not SESSION.workflows:
            self.report({"WARNING"}, "Connect to ComfyQ first")
            return {"CANCELLED"}
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        node = self.target_node()
        if node is None or not self.workflow:
            return {"CANCELLED"}
        node.change_workflow(self.workflow)
        return {"FINISHED"}


class COMFYQ_OT_graph_add_search(bpy.types.Operator):
    bl_idname = "comfyq.graph_add_search"
    bl_label = "Add Any Workflow"
    bl_description = "Search every ComfyQ workflow and add it as a node (type to search; ▶ = served now)"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    bl_property = "workflow"
    workflow: EnumProperty(name="Workflow", items=_workflow_items)

    @classmethod
    def poll(cls, context):
        return COMFYQ_OT_graph_add.poll(context)

    def invoke(self, context, event):
        if not SESSION.workflows:
            self.report({"WARNING"}, "Connect to ComfyQ first")
            return {"CANCELLED"}
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.workflow:
            return {"CANCELLED"}
        return bpy.ops.comfyq.graph_add(node_type=WORKFLOW_NODE, workflow_id=self.workflow)


class COMFYQ_OT_graph_add_source(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_add_source"
    bl_label = "Add Source"
    bl_description = "Add the matching source node (scene render, clay animation, mask, prompt…) and link it here"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        node = self.target_node()
        sock = next((i for i in node.inputs if i.identifier == self.key), None) if node else None
        if sock is None:
            return {"CANCELLED"}
        kind = socket_kind(sock)
        tree = node.id_data
        src = tree.nodes.new(SOURCE_FOR[kind])
        index = [i.identifier for i in node.inputs].index(self.key)
        src.location = (node.location.x - src.width - 80, node.location.y - 230 * index)
        spec = next((s for s in SESSION.specs.get(node.workflow_id, ()) if s.key == self.key), None)
        sc = context.scene
        if spec is not None and src.bl_idname == "ComfyQSceneRenderNode":
            # start/first-frame inputs render the first frame, end/last-frame inputs the last one
            last = scene_match.frame_for(spec, _facts(sc)) == sc.frame_end and sc.frame_end != sc.frame_start
            src.frame = "END" if last else "START"
        if kind == "text":
            it = node.item(self.key)
            if it is not None:
                src.text = it.s
                src.text_block = it.text
        tree.links.new(src.outputs[0], sock)
        return {"FINISHED"}


def _facts(scene):
    from ..ops.workflow import scene_facts
    return scene_facts(scene)


def _text_owner(node, key):
    """(owner, one-line attribute, Text attribute) of a node's prompt."""
    if key:
        return node.item(key), "s", "text"
    return node, "text", "text_block"


class COMFYQ_OT_graph_edit_text(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_edit_text"
    bl_label = "Edit Prompt"
    bl_description = "Edit this prompt with several lines in the ComfyQ window's Text Editor"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        from ..ops.workflow import _show_text, open_comfyq_window, text_editor_area
        node = self.target_node()
        owner, str_attr, text_attr = _text_owner(node, self.key) if node else (None, "", "")
        if owner is None:
            return {"CANCELLED"}
        txt = getattr(owner, text_attr)
        if txt is None:
            txt = bpy.data.texts.new(f"ComfyQ prompt · {node.name}")
            txt.write(getattr(owner, str_attr))
            setattr(owner, text_attr, txt)
        area = text_editor_area()
        if area is not None:
            _show_text(area, txt)
        elif context.window is not None and not open_comfyq_window(context, txt.name):
            self.report({"WARNING"}, f"Open a Text Editor to edit “{txt.name}”")
        return {"FINISHED"}


class COMFYQ_OT_graph_unlink_text(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_unlink_text"
    bl_label = "Back to One-Line Prompt"
    bl_description = "Copy the text back into the one-line field and stop using the Text Editor"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        node = self.target_node()
        owner, str_attr, text_attr = _text_owner(node, self.key) if node else (None, "", "")
        txt = getattr(owner, text_attr) if owner is not None else None
        if txt is None:
            return {"CANCELLED"}
        setattr(owner, str_attr, " ".join(txt.as_string().split()))
        setattr(owner, text_attr, None)
        return {"FINISHED"}


class COMFYQ_OT_graph_run(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_run"
    bl_label = "Generate"
    bl_description = ("Run this node: renders the scene sources it needs, books its job, and first books "
                      "any upstream workflow node without an up-to-date result. Without a node: run the graph's "
                      "last nodes. ⟳ = re-run everything upstream")
    bl_options = {"REGISTER", "INTERNAL"}
    force: BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return SESSION.connected

    def execute(self, context):
        from . import engine, run
        tree = bpy.data.node_groups.get(self.tree) if self.tree else (
            context.space_data.edit_tree if _in_graph(context) else None)
        if tree is None:
            return {"CANCELLED"}
        scene = context.scene
        node = tree.nodes.get(self.node) if self.node else None
        after: list[str] = []
        if node is not None and node.bl_idname in OUTPUT_NODES:
            # an output node: run (only) what is out of date upstream, then show / import / save
            after = [node.name]
            targets = engine.feeding_workflows(run.describe(tree, scene), node.name)
            if not targets:
                msgs = run.act_outputs(tree, scene, after)
                self.report({"INFO"}, "; ".join(msgs))
                return {"FINISHED"}
        else:
            targets = [self.node] if self.node else engine.leaves(run.describe(tree, scene))
        ok, msg = run.start(tree, scene, targets, [o.name for o in context.selected_objects], force=self.force,
                            after=after)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        SESSION.status_note = msg
        return {"FINISHED"} if ok else {"CANCELLED"}


class COMFYQ_OT_graph_output(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_output"
    bl_label = "Act"
    bl_description = "Viewer: show it in the ComfyQ viewer · Import: bring it into the scene · Save: copy it to the folder"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        from . import outputs
        node = self.target_node()
        if node is None:
            return {"CANCELLED"}
        try:
            msg = outputs.act(node, context.scene, force=True)
        except (OSError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, msg or "Nothing linked yet")
        return {"FINISHED"} if msg else {"CANCELLED"}


class COMFYQ_OT_graph_use_result(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_use_result"
    bl_label = "Use This Result"
    bl_description = ("Make this earlier result the node's current one (what it shows and feeds downstream). "
                      "If the node's settings changed since, Generate makes a new one")
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        node = self.target_node()
        rec = next((r for r in node.history if r.path == self.key), None) if node else None
        if rec is None:
            return {"CANCELLED"}
        node.result, node.result_hash, node.job_id = rec.path, rec.made_hash, rec.job_id
        return {"FINISHED"}


# -- sidebar › ComfyQ: the selected node in full ---------------------------------------------------------
class COMFYQ_PT_graph_node(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "ComfyQ"
    bl_label = "Selected Node"

    @classmethod
    def poll(cls, context):
        return _in_graph(context) and context.space_data.edit_tree is not None

    def draw(self, context):
        import time
        from pathlib import Path

        from ..ui.studio import PHASE_TEXT, wrap
        from .tree import target, workflow_of
        layout = self.layout
        node = context.space_data.edit_tree.nodes.active
        if node is None or node.bl_idname in ("NodeFrame", "NodeReroute"):
            layout.label(text="Select a ComfyQ node", icon="INFO")
            return
        layout.label(text=node.label or (node.draw_label() if hasattr(node, "draw_label") else node.name),
                     icon=getattr(node, "bl_icon", "NODE"))
        if node.bl_idname != WORKFLOW_NODE:
            node.draw_buttons(context, layout)
            return
        wf = workflow_of(node.workflow_id)
        if wf is not None and wf.description:
            col = layout.box().column(align=True)
            for line in wrap(wf.description, 44, max_lines=12):
                col.label(text=line)
        node._draw(context, layout, columns=1)
        # the job
        view = SESSION.jobs.get(node.job_id) if node.job_id else None
        if node.job_id:
            box = layout.box().column(align=True)
            box.label(text=f"Job {node.job_id[:8]}", icon="SORTTIME")
            if view is not None:
                j = view.job
                box.label(text=PHASE_TEXT.get(j.phase, j.phase) + (f" · {j.error}" if j.error else ""))
                if j.started_at and j.finished_at:
                    box.label(text=f"Took {(j.finished_at - j.started_at) / 1000:.0f} s")
            rec = next((r for r in context.scene.comfyq.jobs if r.job_id == node.job_id), None)
            if rec is not None and rec.inputs:
                try:
                    sent = json.loads(rec.inputs)
                except ValueError:
                    sent = {}
                for key, path in sent.items():
                    box.label(text=f"Sent {key}: {Path(path).name}", icon="EXPORT")
        # earlier results
        older = [r for r in reversed(node.history) if Path(r.path).is_file()]
        if older:
            layout.label(text=f"Results of this node ({len(older)})", icon="FILE_FOLDER")
            col = layout.column(align=True)
            for r in older:
                row = col.row(align=True)
                current = r.path == node.result
                when = time.strftime("%H:%M", time.localtime(r.when)) if r.when else ""
                row.label(text=f"{when}  {Path(r.path).name}", icon="CHECKMARK" if current else "BLANK1")
                row.operator("comfyq.show_media", text="", icon="PLAY").path = r.path
                if not current:
                    target(row.operator(COMFYQ_OT_graph_use_result.bl_idname, text="Use", icon="LOOP_BACK"),
                           node, r.path)


# -- opening the graph ------------------------------------------------------------------------------
def ensure_graph() -> bpy.types.NodeTree:
    tree = next(iter(graph_trees()), None)
    if tree is None:
        tree = bpy.data.node_groups.new(GRAPH_NAME, TREE)
        tree.use_fake_user = True              # a node tree nobody uses would be dropped on save
    return tree


def show_graph(area, tree) -> None:
    area.ui_type = TREE
    area.spaces.active.node_tree = tree


def graph_editor():
    """(window, area) of an open Node Editor showing the ComfyQ graph, or (None, None)."""
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == "NODE_EDITOR" and a.spaces.active.tree_type == TREE:
                return w, a
    return None, None


class COMFYQ_OT_open_graph(bpy.types.Operator):
    bl_idname = "comfyq.open_graph"
    bl_label = "ComfyQ Graph"
    bl_description = ("Show the ComfyQ node graph next to the viewer in the ComfyQ window (opens the window "
                      "if needed). In the ComfyQ window: switch between bar and graph")
    toggle: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def execute(self, context):
        from ..ops.workflow import comfyq_window, graph_pane, open_comfyq_window, set_graph_mode
        tree = ensure_graph()
        win = comfyq_window()
        if self.toggle and win is not None and graph_pane(win) is not None:
            set_graph_mode(win, False)                     # back to bar mode
            return {"FINISHED"}
        _w, area = graph_editor()
        if area is not None:                               # already open somewhere: show it there
            area.spaces.active.node_tree = tree
            return {"FINISHED"}
        try:
            if win is not None and set_graph_mode(win, True):
                return {"FINISHED"}
        except RuntimeError as e:
            print("ComfyQ graph mode:", e)
        if not open_comfyq_window(context, graph=True):
            self.report({"ERROR"}, "Could not open a window")
            return {"CANCELLED"}
        return {"FINISHED"}


# -- the bar's setup as nodes ---------------------------------------------------------------------------
def _frame_choice(scene, spec, default: str = "START") -> str:
    f = scene_match.frame_for(spec, _facts(scene))
    if scene.frame_end != scene.frame_start and f == scene.frame_end:
        return "END"
    return "START" if f == scene.frame_start else default


def node_from_bar(tree, scene, wf_id: str, x: float, y: float, keyframe_ok: bool = True):
    """A workflow node with the bar's settings for `wf_id`, its media inputs fed by source nodes that
    do what the bar's sources do (scene render, clay animation, mask, image, file, AI keyframe)."""
    from ..client.params import is_disabled
    from ..props import DEFAULT_SOURCE, param_item
    specs = SESSION.specs[wf_id]
    node = tree.nodes.new(WORKFLOW_NODE)
    node.location = (x, y)
    node.workflow_id = wf_id
    node.build(specs)
    for s in specs:
        bar, it = param_item(scene, s.key, wf_id), node.item(s.key)
        if bar is None or it is None or s.is_media:
            continue
        it.s, it.i, it.f, it.b, it.randomize = bar.s, bar.i, bar.f, bar.b, bar.randomize
        if s.options and bar.s in s.options:
            it.e = bar.s
        if bar.text is not None:                           # a copy: editing the node leaves the bar alone
            it.text = bar.text.copy()
    current = node.current_values()
    row = 0
    for s in specs:
        sock = next((i for i in node.inputs if i.identifier == s.key), None)
        if sock is None or not s.is_media or is_disabled(s, current, specs):
            continue
        kind = socket_kind(sock)
        bar = param_item(scene, s.key, wf_id)
        out = _source_from_bar(tree, scene, s, kind, bar, bar.source if bar else DEFAULT_SOURCE.get(kind, "NONE"),
                               keyframe_ok, x - 400, y - 300 * row)
        if out is not None:
            tree.links.new(out, sock)
            row += 1
    return node


def _source_from_bar(tree, scene, spec, kind, bar, src, keyframe_ok, x, y):
    style = scene.comfyq.style
    if src == "KEYFRAME":
        kf = scene.comfyq.keyframe_workflow_id
        if keyframe_ok and kf in SESSION.specs:
            return node_from_bar(tree, scene, kf, x - 100, y, keyframe_ok=False).outputs[0]
        src = "STRUCTURE"
    node = None
    if src == "ANIMATION":
        node = tree.nodes.new("ComfyQSceneAnimationNode")
        node.style = style
    elif src in ("STRUCTURE", "MASK"):
        node = tree.nodes.new("ComfyQSceneRenderNode" if src == "STRUCTURE" else "ComfyQMaskNode")
        node.style = style
        node.frame = _frame_choice(scene, spec, "START" if src == "STRUCTURE" else "CURRENT")
    elif src == "IMAGE" and bar is not None and bar.image is not None:
        node = tree.nodes.new("ComfyQImageNode")
        node.image = bar.image
    elif src == "FILE" and bar is not None and bar.path:
        if kind in ("video", "audio"):
            node = tree.nodes.new("ComfyQVideoFileNode" if kind == "video" else "ComfyQAudioFileNode")
            node.filepath = bar.path
        else:
            node = tree.nodes.new("ComfyQImageNode")
            try:
                node.image = bpy.data.images.load(bpy.path.abspath(bar.path), check_existing=True)
            except RuntimeError:
                pass
    if node is None:
        return None
    node.location = (x, y)
    return node.outputs[0]


class COMFYQ_OT_open_as_graph(bpy.types.Operator):
    bl_idname = "comfyq.open_as_graph"
    bl_label = "Open as Graph"
    bl_description = ("Turn the bar's current setup (model, settings, prompt and what feeds each input) into "
                      "nodes in the ComfyQ graph, then show the graph")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.comfyq.workflow_id in SESSION.specs

    def execute(self, context):
        scene = context.scene
        tree = ensure_graph()
        right = max((n.location.x + n.width for n in tree.nodes), default=-200.0)
        for n in tree.nodes:
            n.select = False
        node = node_from_bar(tree, scene, scene.comfyq.workflow_id, right + 700, 0.0)
        node.select = True
        tree.nodes.active = node
        if bpy.ops.comfyq.open_graph.poll():
            bpy.ops.comfyq.open_graph()
        _frame_graph_soon()
        return {"FINISHED"}


def _frame_graph_soon() -> None:
    """Fit the view to the graph once its editor is open and laid out."""
    tries = {"n": 0}

    def step():
        tries["n"] += 1
        w, area = graph_editor()
        region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
        if region is None or region.width < 50:
            return 0.1 if tries["n"] < 40 else None
        with bpy.context.temp_override(window=w, screen=w.screen, area=area, region=region):
            bpy.ops.node.view_all()
        return None
    bpy.app.timers.register(step, first_interval=0.1)


# -- header toolbar + Shift+A --------------------------------------------------------------------------
# Room for our toolbar. Blender centres the tree selector, so in a wide editor we get the right half
# minus the selector and the overlay buttons: about (width - 200) / 2 - 200 px (measured in Blender 5.2).
# Rather than letting Blender truncate every label ("Sce…", "Ima…"), the toolbar steps down:
# labels → icons (names in the tooltips) → one Add menu.
_LABELS_NEED = 770          # px at UI scale 1: 8 labelled menus + search + Run Graph + Clear (610 truncated)
_RUN_LABEL_NEED = 300       # 9 icon menus (~19 px each) + search + labelled Run Graph + Clear
_ICONS_NEED = 180           # same with an icon-only Run


def header_room(context) -> float:
    scale = context.preferences.system.ui_scale or 1.0
    width = context.region.width / scale if context.region else 1000.0
    return (width - 200) / 2 - 200


class COMFYQ_MT_graph_all(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_graph_all"
    bl_label = "Add"
    bl_description = "Add a ComfyQ node: scene sources and the workflows of each category"

    def draw(self, context):
        _add_menu(self, context)


def _header(self, context):
    if not _in_graph(context):
        return
    layout = self.layout
    room = header_room(context)
    row = layout.row(align=True)
    if room < _ICONS_NEED:
        row.menu(COMFYQ_MT_graph_all.bl_idname, text="", icon="ADD")
    else:
        labels = room >= _LABELS_NEED
        row.menu(COMFYQ_MT_graph_scene.bl_idname, text="Scene" if labels else "", icon="SCENE_DATA")
        row.menu(COMFYQ_MT_graph_output.bl_idname, text="Output" if labels else "", icon="HIDE_OFF")
        for m in TAB_MENUS:
            row.menu(m.bl_idname, text=m.bl_label if labels else "", icon=m.comfyq_icon)
        row.operator(COMFYQ_OT_graph_add_search.bl_idname, text="", icon="VIEWZOOM")
    layout.separator()
    run = layout.row()
    run.scale_x = 1.2
    run.operator(COMFYQ_OT_graph_run.bl_idname, text="Run Graph" if room >= _RUN_LABEL_NEED else "", icon="PLAY")
    layout.operator(COMFYQ_OT_graph_clear.bl_idname, text="", icon="TRASH")


def _add_menu(self, context):
    if not _in_graph(context):
        return
    layout = self.layout
    layout.menu(COMFYQ_MT_graph_scene.bl_idname, icon="SCENE_DATA")
    layout.menu(COMFYQ_MT_graph_output.bl_idname, icon="HIDE_OFF")
    for m in TAB_MENUS:
        layout.menu(m.bl_idname, icon=m.comfyq_icon)
    layout.operator(COMFYQ_OT_graph_add_search.bl_idname, text="Any Workflow…", icon="VIEWZOOM")


# -- timer sync -------------------------------------------------------------------------------------
def sync_all() -> None:
    """Called from the add-on timer: build/rebuild workflow nodes from their parameters, hide inputs
    the workflow switches off, make thumbnails. Never runs inside a draw."""
    from ..ops.workflow import request_specs
    from . import run
    run.tick_all()
    for tree in graph_trees():
        for node in tree.nodes:
            if node.bl_idname == WORKFLOW_NODE and node.workflow_id:
                specs = SESSION.specs.get(node.workflow_id)
                if specs is None:
                    request_specs(node.workflow_id)
                elif node.signature != kinds.spec_signature(specs):
                    node.build(specs)
                else:
                    node.refresh_visibility(specs)
                _adopt_result(tree, node)
            elif node.bl_idname == "ComfyQImageNode" and node.image is not None and node.image.preview is None:
                node.image.preview_ensure()
            elif node.bl_idname == "ComfyQVideoFileNode" and node.filepath:
                movie_thumbnail(bpy.path.abspath(node.filepath))
            elif node.bl_idname == "ComfyQSceneAnimationNode" and animation_path():
                movie_thumbnail(animation_path())
        _follow_active(tree)
        outputs.tick(tree, bpy.context.scene)
    for name in ("ComfyQ render preview", "ComfyQ mask preview"):
        img = bpy.data.images.get(name)
        if img is not None and img.preview is None and graph_trees():
            img.preview_ensure()


_ACTIVE: dict[str, str] = {}        # tree name -> active node last shown in the viewer


def _follow_active(tree) -> None:
    """Graph mode: selecting a node shows what it makes in the ComfyQ window's viewer, and its
    multi-line prompt in the prompt editor."""
    from ..ops.workflow import _show_text, comfyq_window, graph_pane, text_editor_area
    node = tree.nodes.active
    name = node.name if node is not None else ""
    if _ACTIVE.get(tree.name) == name:
        return
    _ACTIVE[tree.name] = name
    win = comfyq_window()
    if node is None or graph_pane(win) is None:
        return
    media = _node_media(node)
    if media is not None:
        from ..ui.player import _PLAY, show_media, viewer_area
        area = viewer_area()
        if area is not None:
            _PLAY["on"] = False
            show_media(area, media)
    text = None
    if node.bl_idname == "ComfyQPromptNode":
        text = node.text_block
    elif node.bl_idname == WORKFLOW_NODE:
        text = next((it.text for it in node.params if it.workflow_id == node.workflow_id and it.text is not None),
                    None)
    area = text_editor_area(win)
    if text is not None and area is not None:
        _show_text(area, text)


def _animation_image():
    """The clay animation as the viewer's "ComfyQ animation pass" movie (made again after a Clean up)."""
    from ..ui.player import forget_meta
    path = animation_path()
    img = bpy.data.images.get("ComfyQ animation pass")
    if not path:
        return img
    if img is None:
        img = bpy.data.images.load(path, check_existing=False)
        img.name = "ComfyQ animation pass"
    elif bpy.path.abspath(img.filepath) != path:
        img.filepath = path
        img.reload()
        forget_meta(img)
    return img


def _node_media(node):
    """The image/movie datablock a node shows (its result, render, file), loading files if needed."""
    from pathlib import Path
    path = ""
    if node.bl_idname == WORKFLOW_NODE:
        path = node.result
    elif node.bl_idname == "ComfyQImageNode":
        return node.image
    elif node.bl_idname == "ComfyQSceneRenderNode":
        return bpy.data.images.get("ComfyQ render preview")
    elif node.bl_idname == "ComfyQMaskNode":
        return bpy.data.images.get("ComfyQ mask preview")
    elif node.bl_idname == "ComfyQSceneAnimationNode":
        return _animation_image()
    elif node.bl_idname == "ComfyQVideoFileNode" and node.filepath:
        path = bpy.path.abspath(node.filepath)
    p = Path(path) if path else None
    if p is None or not p.is_file() or p.suffix.lower() not in IMAGE_EXTS | VIDEO_EXTS:
        return None
    try:
        return bpy.data.images.load(str(p), check_existing=True)
    except RuntimeError:
        return None


def _adopt_result(tree, node) -> None:
    """A node's job finished outside a run (e.g. the file was reopened meanwhile): take its result.
    Image results get a thumbnail."""
    from pathlib import Path

    from . import run
    if node.job_id and not run.running(tree.name, node.name):
        view = SESSION.jobs.get(node.job_id)
        if view is not None and view.job.ok and view.files and not node.result:
            remember_result(node, node.job_id, str(run.pick_result(view.files, node.output_kind)), node.pending_hash)
    if node.result:
        p = Path(node.result)
        if p.suffix.lower() in VIDEO_EXTS and p.is_file():
            movie_thumbnail(str(p))
        elif p.suffix.lower() in IMAGE_EXTS and p.is_file():
            img = bpy.data.images.get(p.name)
            if img is None:
                img = bpy.data.images.load(str(p), check_existing=True)
            if img.preview is None:
                img.preview_ensure()


classes = (COMFYQ_OT_graph_add, *TAB_MENUS, COMFYQ_MT_graph_scene, COMFYQ_OT_graph_clear, COMFYQ_OT_open_graph,
           COMFYQ_OT_open_as_graph, COMFYQ_OT_graph_add_search, COMFYQ_MT_graph_all, COMFYQ_OT_graph_set_workflow,
           COMFYQ_OT_graph_add_source, COMFYQ_OT_graph_edit_text,
           COMFYQ_OT_graph_unlink_text, COMFYQ_OT_graph_run, COMFYQ_OT_graph_output, COMFYQ_OT_graph_use_result,
           COMFYQ_MT_graph_output, COMFYQ_PT_graph_node)


def register_extras() -> None:
    bpy.types.NODE_HT_header.append(_header)
    bpy.types.NODE_MT_add.append(_add_menu)


def unregister_extras() -> None:
    bpy.types.NODE_HT_header.remove(_header)
    bpy.types.NODE_MT_add.remove(_add_menu)
    free_thumbnails()
    _ACTIVE.clear()
