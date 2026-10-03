# SPDX-License-Identifier: GPL-3.0-or-later
"""Graph toolbar (Moodboard-style category buttons), add menus, operators, and the timer sync
that keeps workflow nodes in step with what ComfyQ serves."""
from __future__ import annotations

import bpy
from bpy.props import StringProperty

from ..props import TABS
from ..runtime.session import SESSION
from . import kinds
from .tree import SOURCE_NODES, TREE, WORKFLOW_NODE, graph_trees, tab_workflows

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
            elif node.bl_idname == "ComfyQImageNode" and node.image is not None and node.image.preview is None:
                node.image.preview_ensure()
    for name in ("ComfyQ render preview", "ComfyQ animation pass"):
        img = bpy.data.images.get(name)
        if img is not None and img.preview is None and graph_trees():
            img.preview_ensure()


classes = (COMFYQ_OT_graph_add, *TAB_MENUS, COMFYQ_MT_graph_scene, COMFYQ_OT_graph_clear, COMFYQ_OT_open_graph)


def register_extras() -> None:
    bpy.types.NODE_HT_header.append(_header)
    bpy.types.NODE_MT_add.append(_add_menu)


def unregister_extras() -> None:
    bpy.types.NODE_HT_header.remove(_header)
    bpy.types.NODE_MT_add.remove(_add_menu)
