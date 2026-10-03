# SPDX-License-Identifier: GPL-3.0-or-later
"""Graph toolbar (Moodboard-style category buttons), add menus, operators, and the timer sync
that keeps workflow nodes in step with what ComfyQ serves."""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from .. import match as scene_match
from ..importers.media import IMAGE_EXTS
from ..props import TABS, tab_of
from ..runtime.session import SESSION
from . import kinds
from .tree import SOURCE_FOR, SOURCE_NODES, TREE, WORKFLOW_NODE, find_node, graph_trees, socket_kind, tab_workflows

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


def _tab_menu(key: str, label: str, icon: str):
    def draw(self, context):
        _draw_workflow_items(self.layout, tab_workflows(key))
    name = f"COMFYQ_MT_graph_{key.lower()}"
    return type(name, (bpy.types.Menu,), {"bl_idname": name, "bl_label": label, "draw": draw,
                                         "comfyq_icon": icon})


TAB_MENUS = [_tab_menu(key, label, icon) for key, label, _desc, icon, _n in TABS]


class COMFYQ_MT_graph_scene(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_graph_scene"
    bl_label = "Scene"

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
        targets = [self.node] if self.node else engine.leaves(run.describe(tree, scene))
        ok, msg = run.start(tree, scene, targets, [o.name for o in context.selected_objects], force=self.force)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        SESSION.status_note = msg
        return {"FINISHED"} if ok else {"CANCELLED"}


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


class COMFYQ_OT_open_graph(bpy.types.Operator):
    bl_idname = "comfyq.open_graph"
    bl_label = "ComfyQ Graph"
    bl_description = "Open the ComfyQ node graph in its own window (wire scene passes into workflows)"

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def execute(self, context):
        tree = ensure_graph()
        for w in context.window_manager.windows:          # already open somewhere: show it there
            for a in w.screen.areas:
                if a.type == "NODE_EDITOR" and a.spaces.active.tree_type == TREE:
                    a.spaces.active.node_tree = tree
                    return {"FINISHED"}
        before = {w.as_pointer() for w in context.window_manager.windows}
        area = context.area or context.window.screen.areas[0]
        try:
            with context.temp_override(window=context.window, area=area):
                bpy.ops.wm.window_new()
        except RuntimeError as e:
            self.report({"ERROR"}, f"Could not open a window: {e}")
            return {"CANCELLED"}
        new = [w for w in context.window_manager.windows if w.as_pointer() not in before]
        new = [w for w in new if any(a.type == area.type for a in w.screen.areas)] or new
        if not new:
            return {"CANCELLED"}
        ptr, tries = new[0].as_pointer(), {"n": 0}

        def setup():
            w = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == ptr), None)
            tries["n"] += 1
            if w is None or tries["n"] > 40:
                return None
            a = max(w.screen.areas, key=lambda a: a.width * a.height)
            if a.width < 50:                              # not laid out yet
                return 0.05
            show_graph(a, ensure_graph())
            return None

        bpy.app.timers.register(setup, first_interval=0.05)
        return {"FINISHED"}


# -- header toolbar + Shift+A --------------------------------------------------------------------------
def _header(self, context):
    if not _in_graph(context):
        return
    layout = self.layout
    row = layout.row(align=True)
    row.menu(COMFYQ_MT_graph_scene.bl_idname, text="Scene", icon="SCENE_DATA")
    for m in TAB_MENUS:
        row.menu(m.bl_idname, text=m.bl_label, icon=m.comfyq_icon)
    layout.separator()
    run = layout.row()
    run.scale_x = 1.3
    run.operator(COMFYQ_OT_graph_run.bl_idname, text="Run Graph", icon="PLAY")
    layout.operator(COMFYQ_OT_graph_clear.bl_idname, text="Clear Graph", icon="X")


def _add_menu(self, context):
    if not _in_graph(context):
        return
    layout = self.layout
    layout.menu(COMFYQ_MT_graph_scene.bl_idname, icon="SCENE_DATA")
    for m in TAB_MENUS:
        layout.menu(m.bl_idname, icon=m.comfyq_icon)


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
    for name in ("ComfyQ render preview", "ComfyQ animation pass"):
        img = bpy.data.images.get(name)
        if img is not None and img.preview is None and graph_trees():
            img.preview_ensure()


def _adopt_result(tree, node) -> None:
    """A node's job finished outside a run (e.g. the file was reopened meanwhile): take its result.
    Image results get a thumbnail."""
    from pathlib import Path

    from . import run
    if node.job_id and not run.running(tree.name, node.name):
        view = SESSION.jobs.get(node.job_id)
        if view is not None and view.job.ok and view.files and not node.result:
            node.result = str(run.pick_result(view.files, node.output_kind))
            node.result_hash = node.pending_hash
    if node.result:
        p = Path(node.result)
        if p.suffix.lower() in IMAGE_EXTS and p.is_file():
            img = bpy.data.images.get(p.name)
            if img is None:
                img = bpy.data.images.load(str(p), check_existing=True)
            if img.preview is None:
                img.preview_ensure()


classes = (COMFYQ_OT_graph_add, *TAB_MENUS, COMFYQ_MT_graph_scene, COMFYQ_OT_graph_clear, COMFYQ_OT_open_graph,
           COMFYQ_OT_graph_set_workflow, COMFYQ_OT_graph_add_source, COMFYQ_OT_graph_edit_text,
           COMFYQ_OT_graph_unlink_text, COMFYQ_OT_graph_run)


def register_extras() -> None:
    bpy.types.NODE_HT_header.append(_header)
    bpy.types.NODE_MT_add.append(_add_menu)


def unregister_extras() -> None:
    bpy.types.NODE_HT_header.remove(_header)
    bpy.types.NODE_MT_add.remove(_add_menu)
