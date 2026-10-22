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
from ..wording import count
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


def _param_label(node, key: str) -> str:
    """A workflow parameter's label (never the raw key on screen)."""
    specs = SESSION.specs.get(getattr(node, "workflow_id", ""), [])
    return next((s.label for s in specs if s.key == key), key)


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
        layout.label(text="No model in this category")
    served = SESSION.served_ids()
    budget = shared_budget()
    for group in (True, False):
        items = [w for w in wfs if (w.id in served) == group]
        if group and items:
            layout.label(text="Served Now")
        elif items:
            if any(w.id in served for w in wfs):
                layout.separator()
            layout.label(text="Not Served (Ask the Teacher)")
        for w in items:
            badge, over = vram_badge(w, budget)
            row = layout.row()
            row.alert = over or w.unavailable
            op = row.operator(COMFYQ_OT_graph_add.bl_idname, text=f"{w.name}   ·  {badge}",
                              icon="ERROR" if w.unavailable else ("CHECKMARK" if group else "NONE"))
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
        self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text="Frame",
                             icon="NONE").node_type = "NodeFrame"
        self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text="Reroute", icon="DECORATE").node_type = "NodeReroute"


class COMFYQ_MT_graph_scene(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_graph_scene"
    bl_label = "Scene"
    bl_description = ("Add a source node: scene render, clay animation, selection mask, image, video, audio, "
                      "scene objects, 3D model, prompt")

    def draw(self, context):
        for cls in SOURCE_NODES:
            self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text=cls.bl_label, icon=cls.bl_icon).node_type = \
                cls.bl_idname
        self.layout.separator()
        self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text="Surfaces (SAM3) to Prompt",
                             icon="MATERIAL").node_type = "ComfyQSurfacesNode"
        self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text="Material Board",
                             icon="TEXTURE").node_type = "ComfyQBoardNode"
        self.layout.operator(COMFYQ_OT_graph_add.bl_idname, text="Join Text",
                             icon="LINENUMBERS_ON").node_type = "ComfyQJoinTextNode"


class COMFYQ_OT_graph_clear(bpy.types.Operator):
    bl_idname = "comfyq.graph_clear"
    bl_label = "Clear Graph"
    bl_description = "Remove every node of this graph"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _in_graph(context) and context.space_data.edit_tree is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Clear the Graph?",
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


_WF_ITEMS: list[tuple] = []          # kept alive: Blender keeps pointers to these strings
_TAB_LABEL = {key: label for key, label, *_rest in TABS}


def _workflow_items(self, context):
    from ..ops.workflow import shared_budget, vram_badge
    served, budget = SESSION.served_ids(), shared_budget()
    items = []
    for n, w in enumerate(sorted(SESSION.workflows, key=lambda w: (w.id not in served, tab_of(w), w.name.lower()))):
        badge, _over = vram_badge(w, budget)
        items.append((w.id, f"{w.name}  ·  {_TAB_LABEL.get(tab_of(w), '')}  ·  {badge}",
                      (w.description or w.name)[:300], "CHECKMARK" if w.id in served else "BLANK1", n))
    _WF_ITEMS[:] = items or [("", "Connect to ComfyQ first", "", "NONE", 0)]
    return _WF_ITEMS


class COMFYQ_OT_graph_set_workflow(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_set_workflow"
    bl_label = "Change Model..."
    bl_description = ("Choose this node's model (type to search; a check mark means served now). Settings are kept per "
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
    bl_label = "Add Any Model..."
    bl_description = "Search every ComfyQ model and add it as a node (type to search; a check mark means served now)"
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
    bl_description = "Run this node, after the nodes before it that have no up-to-date result"

    @classmethod
    def description(cls, context, props):
        if props.force:
            return "Run this node and everything before it again, even what is up to date"
        if props.node:
            return ("Run this node: render the scene sources it needs, then book its job (nodes before it "
                    "without an up-to-date result run first)")
        return "Run the graph's last nodes (and what they need)"
    bl_options = {"REGISTER", "INTERNAL"}
    force: BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if not SESSION.connected:
            cls.poll_message_set("Connect to ComfyQ first")
            return False
        return True

    def _targets(self, context):
        """(tree, targets, after) of this press; targets None = nothing to run (outputs act at once)."""
        from . import engine, run
        tree = bpy.data.node_groups.get(self.tree) if self.tree else (
            context.space_data.edit_tree if _in_graph(context) else None)
        if tree is None:
            return None, None, []
        node = tree.nodes.get(self.node) if self.node else None
        if node is not None and node.bl_idname in OUTPUT_NODES:
            return tree, engine.feeding_workflows(run.describe(tree, context.scene), node.name), [node.name]
        return tree, ([self.node] if self.node else run.graph_targets(tree, context.scene)[0]), []

    def invoke(self, context, event):
        # Ask first when the press does more than it seems: several chains at once (nothing selected), or
        # solo mode loading models (which switches what ComfyQ serves for everyone)
        from . import run
        tree, targets, _after = self._targets(context)
        if tree is None or not targets:
            return self.execute(context)
        lines, title = [], ""
        if not self.node:
            _t, from_sel = run.graph_targets(tree, context.scene)
            if not from_sel and len(targets) > 1:
                names = list(dict.fromkeys(run.chain_name(tree, t) for t in targets))
                title = f"Run {count(len(targets), 'Chain')}?"
                lines.append("Nothing is selected, so every chain of the graph runs: " + ", ".join(names[:6])
                             + ("…" if len(names) > 6 else "") + ". Select a Frame to run only that one.")
        if run.solo_on():
            models = run.models_to_load(tree, context.scene, targets, self.force)
            if models:
                title = title or f"Load {count(len(models), 'Model')} in ComfyQ?"
                lines.append("Solo mode loads " + ", ".join(models) + ". Each switch can restart ComfyQ "
                             "(1 to 2 minutes) for everyone connected.")
        if lines:
            return context.window_manager.invoke_confirm(self, event, title=title, message=" ".join(lines),
                                                         confirm_text="Run", icon="WARNING")
        return self.execute(context)

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
            targets = [self.node] if self.node else run.graph_targets(tree, scene)[0]
        ok, msg = run.start(tree, scene, targets, [o.name for o in context.selected_objects], force=self.force,
                            after=after)
        self.report({"INFO"} if ok else {"ERROR"}, msg)
        SESSION.status_note = msg
        return {"FINISHED"} if ok else {"CANCELLED"}


class COMFYQ_OT_graph_more_inputs(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_more_inputs"
    bl_label = "Add Input"
    bl_description = "Show one more optional input of this model (the others stay hidden until needed)"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        node = self.target_node()
        if node is None:
            return {"CANCELLED"}
        node.extra_inputs += 1
        specs = SESSION.specs.get(node.workflow_id)
        if specs is not None:
            node.refresh_visibility(specs)
        return {"FINISHED"}


class COMFYQ_OT_graph_stop(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_stop"
    bl_label = "Stop"
    bl_description = ("Stop the graph run: cancel its jobs on the server (also SAM3 surface jobs) and stop waiting "
                      "for a model. A model ComfyQ is already loading keeps loading")
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        from .run import RUNS
        tree = bpy.data.node_groups.get(self.tree) if self.tree else (
            context.space_data.edit_tree if _in_graph(context) else None)
        run = RUNS.get(tree.name) if tree is not None else None
        if run is None:
            self.report({"INFO"}, "Nothing is running in this graph")
            return {"CANCELLED"}
        n = run.stop(tree)
        SESSION.status_note = f"Graph stopped ({count(n, 'job')} cancelled)"
        self.report({"INFO"}, SESSION.status_note)
        return {"FINISHED"}


class COMFYQ_OT_graph_output(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.graph_output"
    bl_label = "Show, Import, Save or Assemble"
    bl_description = ("Viewer: show it in the ComfyQ viewer · Import: bring it into the scene · Save: copy it to the "
                      "folder · Film: put the shots' clips end to end in the ComfyQ Film scene")
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


class COMFYQ_OT_film_render(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.film_render"
    bl_label = "Render Film..."
    bl_description = ("Render the assembled film (the ComfyQ Film scene) to an MP4 with sound in the Film node's "
                      "folder. Blender is busy while it renders")
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        from .outputs import FILM_SCENE
        if bpy.data.scenes.get(FILM_SCENE) is None:
            cls.poll_message_set("Assemble the film first (the Film node's Assemble)")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Render Film", message="Render the assembled film to an MP4? Blender is busy "
            "until it is done (a few seconds per second of film).", confirm_text="Render", icon="RENDER_ANIMATION")

    def execute(self, context):
        from . import outputs
        node = self.target_node()
        if node is None:
            return {"CANCELLED"}
        try:
            out = outputs.render_film(node)
        except (OSError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        node.status = f"Rendered {out.name}"
        self.report({"INFO"}, f"Film: {out}")
        return {"FINISHED"}


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
            self.report({"WARNING"}, "This result is no longer in the node's history")
            return {"CANCELLED"}
        node.result, node.result_hash, node.job_id = rec.path, rec.made_hash, rec.job_id
        self.report({"INFO"}, f"Current result: {bpy.path.basename(rec.path)}")
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
        draw_selected_node(self.layout, context, context.space_data.edit_tree)


class COMFYQ_PT_viewer_graph_node(bpy.types.Panel):
    """Graph mode of the ComfyQ window: the viewer's sidebar shows the selected node instead of the bar
    (the bar's own model / prompt / Generate do not act on the graph)."""
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "ComfyQ"
    bl_label = "Selected Node"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return graph_mode_tree(context) is not None

    def draw(self, context):
        from ..ui.studio import _messages, _who
        layout = self.layout
        if SESSION.client is None:
            layout.operator("comfyq.connect", icon="LINKED")
            _messages(layout)
            return
        _who(layout)
        tree = graph_mode_tree(context)
        draw_selected_node(layout, context, tree)
        _messages(layout)


def graph_mode_tree(context):
    """The ComfyQ graph shown next to this viewer when the ComfyQ window is in graph mode, else None."""
    from ..ops.workflow import comfyq_window, graph_pane
    win = context.window
    if win is None or win != comfyq_window():
        return None
    pane = graph_pane(win)
    return pane.spaces.active.edit_tree if pane is not None else None


def draw_selected_node(layout, context, tree) -> None:
    """The selected node in full: description, every setting, Generate, its job and its earlier results."""
    import time
    from pathlib import Path

    from ..ui.studio import PHASE_TEXT, wrap
    from .tree import target, workflow_of
    if tree is None:
        return
    node = tree.nodes.active
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
        box.label(text="Last Job", icon="SORTTIME")
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
                box.label(text=f"Sent {_param_label(node, key)}: {Path(path).name}", icon="EXPORT")
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
            row.operator("comfyq.show_media", text="", icon="HIDE_OFF").path = r.path
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
        it.cam_h, it.cam_v, it.cam_zoom = bar.cam_h, bar.cam_v, bar.cam_zoom
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
    if src == "SELECTION":
        node = tree.nodes.new("ComfyQSceneObjectsNode")
    elif src == "ANIMATION":
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
        if kind in ("video", "audio", "model"):
            node = tree.nodes.new({"video": "ComfyQVideoFileNode", "audio": "ComfyQAudioFileNode",
                                   "model": "ComfyQModelFileNode"}[kind])
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
        if context.scene.comfyq.workflow_id not in SESSION.specs:
            cls.poll_message_set("Connect and choose a model in the bar first")
            return False
        return True

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


def _frame_graph_soon(selected: bool = False) -> None:
    """Fit the view to the graph (or to the selected nodes) once its editor is open and laid out."""
    tries = {"n": 0}

    def step():
        tries["n"] += 1
        w, area = graph_editor()
        region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
        if region is None or region.width < 50:
            return 0.1 if tries["n"] < 40 else None
        with bpy.context.temp_override(window=w, screen=w.screen, area=area, region=region):
            if selected:
                bpy.ops.node.view_selected()
            else:
                bpy.ops.node.view_all()
        return None
    bpy.app.timers.register(step, first_interval=0.1)


# -- header toolbar + Shift+A --------------------------------------------------------------------------
# Room for our toolbar. Blender centres the tree selector, so in a wide editor we get the right half
# minus the selector and the overlay buttons: about (width - 200) / 2 - 200 px (measured in Blender 5.2).
# Rather than letting Blender truncate every label ("Sce…", "Ima…"), the toolbar steps down:
# labels → icons (names in the tooltips) → one Add menu.
_LABELS_NEED = 850          # px at UI scale 1: 8 labelled menus + search + Run Graph + Clear (610 truncated)
_RUN_LABEL_NEED = 320       # 9 icon menus (~19 px each) + search + labelled Run Graph + Clear
_ICONS_NEED = 200           # same with an icon-only Run


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
        row.menu(COMFYQ_MT_graph_output.bl_idname, text="Output" if labels else "", icon="OUTPUT")
        row.menu("COMFYQ_MT_graph_presets", text="Presets" if labels else "", icon="PRESET")
        for m in TAB_MENUS:
            row.menu(m.bl_idname, text=m.bl_label if labels else "", icon=m.comfyq_icon)
        row.operator(COMFYQ_OT_graph_add_search.bl_idname, text="", icon="VIEWZOOM")
    layout.separator()
    row = layout.row()
    row.scale_x = 1.2
    tree = context.space_data.edit_tree
    from .run import RUNS
    if tree is not None and tree.name in RUNS:      # running: the same place stops it
        row.alert = True
        row.operator(COMFYQ_OT_graph_stop.bl_idname, text="Stop" if room >= _RUN_LABEL_NEED else "",
                     icon="CANCEL").tree = tree.name
    else:
        row.operator(COMFYQ_OT_graph_run.bl_idname, text="Run Graph" if room >= _RUN_LABEL_NEED else "",
                     icon="PLAY")
    layout.operator(COMFYQ_OT_graph_clear.bl_idname, text="", icon="TRASH")
    layout.operator("comfyq.help", text="", icon="HELP")
    from .run import solo_on
    if solo_on():
        sub = layout.row()
        sub.alert = True
        sub.label(text="Solo", icon="ERROR")


def _add_menu(self, context):
    if not _in_graph(context):
        return
    layout = self.layout
    layout.menu(COMFYQ_MT_graph_scene.bl_idname, icon="SCENE_DATA")
    layout.menu(COMFYQ_MT_graph_output.bl_idname, icon="OUTPUT")
    layout.menu("COMFYQ_MT_graph_presets", icon="PRESET")
    for m in TAB_MENUS:
        layout.menu(m.bl_idname, icon=m.comfyq_icon)
    layout.operator(COMFYQ_OT_graph_add_search.bl_idname, text="Any Model...", icon="VIEWZOOM")


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
            elif node.bl_idname == "ComfyQSurfacesNode" and node.result:
                _overlay_preview(node.result)
            elif node.bl_idname == "ComfyQBoardNode" and node.result:
                from pathlib import Path as _P
                board_img = bpy.data.images.get(_P(node.result).name)
                if board_img is not None and board_img.preview is None:
                    board_img.preview_ensure()
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


def _overlay_preview(path: str) -> None:
    from pathlib import Path
    p = Path(path)
    if not p.is_file():
        return
    img = bpy.data.images.get(p.name)
    if img is None:
        try:
            img = bpy.data.images.load(str(p), check_existing=True)
        except RuntimeError:
            return
    if img.preview is None:
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
    if node.bl_idname in ("ComfyQPromptNode", "ComfyQSurfacesNode"):
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
    if node.bl_idname in (WORKFLOW_NODE, "ComfyQSurfacesNode"):
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


class COMFYQ_OT_graph_load_model(bpy.types.Operator):
    bl_idname = "comfyq.graph_load_model"
    bl_label = "Load Model"
    bl_description = ("Make ComfyQ serve this model now: beside the current one when it fits, otherwise "
                      "instead of it (ComfyQ restarts for 1 to 2 minutes). Only while nobody has a job")
    bl_options = {"INTERNAL"}
    workflow_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        from ..prefs import teacher_unlocked
        if not SESSION.connected:
            cls.poll_message_set("Connect to ComfyQ first")
            return False
        if not teacher_unlocked():
            cls.poll_message_set("Only on the teacher's PC (its profile holds ComfyQ's admin password)")
            return False
        return True

    def invoke(self, context, event):
        from .run import _wf_name
        return context.window_manager.invoke_confirm(
            self, event, title="Load This Model in ComfyQ?",
            message=(f"“{_wf_name(self.workflow_id)}” is loaded beside the current model when it fits, otherwise "
                     "it replaces it: ComfyQ then restarts (1 to 2 minutes) for everyone connected."),
            confirm_text="Load", icon="WARNING")

    def execute(self, context):
        from ..runtime import serving
        from .run import _wf_name
        client, wf_id = SESSION.client, self.workflow_id
        others = serving.blockers(client)
        if others:
            self.report({"WARNING"}, f"{count(len(others), 'job')} on ComfyQ ({others[0]}): models are only switched "
                                     f"when nobody is using the server")
            return {"CANCELLED"}
        name = _wf_name(wf_id)

        def work():
            how = serving.choose(client, wf_id, "auto")
            return how, serving.serve(client, wf_id, how)

        def done(res):
            how, closed = res
            SESSION.status_note = (f"“{name}” loading alongside" if how == "alongside" else
                                   f"ComfyQ restarts with “{name}” (1 to 2 minutes)")
            if closed:
                SESSION.status_note += f"; closed to make room: {', '.join(_wf_name(w) for w in closed)}"
        SESSION.status_note = f"Asking ComfyQ to load “{name}”…"
        SESSION.run(work, done, on_error=lambda msg: setattr(SESSION, "status_note", f"Load Model: {msg}"))
        self.report({"INFO"}, SESSION.status_note)
        return {"FINISHED"}


classes = (COMFYQ_OT_graph_load_model, COMFYQ_OT_graph_add, *TAB_MENUS, COMFYQ_MT_graph_scene, COMFYQ_OT_graph_clear, COMFYQ_OT_open_graph,
           COMFYQ_OT_open_as_graph, COMFYQ_OT_graph_add_search, COMFYQ_MT_graph_all, COMFYQ_OT_graph_set_workflow,
           COMFYQ_OT_graph_add_source, COMFYQ_OT_graph_edit_text,
           COMFYQ_OT_graph_unlink_text, COMFYQ_OT_graph_run, COMFYQ_OT_graph_stop, COMFYQ_OT_graph_more_inputs, COMFYQ_OT_graph_output, COMFYQ_OT_film_render, COMFYQ_OT_graph_use_result,
           COMFYQ_MT_graph_output, COMFYQ_PT_graph_node, COMFYQ_PT_viewer_graph_node)


def register_extras() -> None:
    from ..ui import append_once
    append_once(bpy.types.NODE_HT_header, _header)
    append_once(bpy.types.NODE_MT_add, _add_menu)


def unregister_extras() -> None:
    bpy.types.NODE_HT_header.remove(_header)
    bpy.types.NODE_MT_add.remove(_add_menu)
    free_thumbnails()
    _ACTIVE.clear()
