# SPDX-License-Identifier: GPL-3.0-or-later
"""The ComfyQ graph: a node tree in Blender's Node Editor, Moodboard-style.

Source nodes (scene render, clay animation, image, prompt) feed workflow nodes, whose sockets
and setting chips are built from what ComfyQ says the workflow takes. Settings are chips inside
the node (no setting sockets). Running the graph comes in N2; this is the editable graph (N0).
"""
from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, EnumProperty, PointerProperty, StringProperty

from .. import match as scene_match
from ..client.params import is_disabled
from ..passes.render import STYLES
from ..props import ComfyQParamValue, tab_of
from ..runtime.session import SESSION
from . import kinds

TREE = "ComfyQGraphTree"
WORKFLOW_NODE = "ComfyQWorkflowNode"


# -- sockets: one type per kind (the type gives the colour and what may link) ------------------
class _Socket:
    comfyq_kind = ""

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return kinds.SOCKETS[self.comfyq_kind][1]


def _socket_class(kind: str):
    name = f"ComfyQSocket{kind.title()}"
    return type(name, (_Socket, bpy.types.NodeSocket), {
        "bl_idname": name, "bl_label": kinds.SOCKETS[kind][0], "comfyq_kind": kind})


SOCKET_CLASSES = {kind: _socket_class(kind) for kind in kinds.SOCKETS}
SOCKET_IDNAME = {kind: cls.bl_idname for kind, cls in SOCKET_CLASSES.items()}
_KIND_OF = {idname: kind for kind, idname in SOCKET_IDNAME.items()}

# True while our classes are (un)registered. Blender then updates the trees of the open file and
# calls ComfyQGraphTree.update() while the socket types are half-registered: reading the sockets
# at that moment crashes Blender (seen on reinstall with a graph in the file).
REGISTERING = [False]


def socket_kind(sock) -> str:
    return _KIND_OF.get(sock.bl_idname, "")


# -- the tree -------------------------------------------------------------------------------------
class ComfyQGraphTree(bpy.types.NodeTree):
    """ComfyQ: wire scene passes, images and prompts into the workflows served by ComfyQ"""
    bl_idname = TREE
    bl_label = "ComfyQ Graph"
    bl_icon = "NODETREE"

    def update(self):
        # Wrong kinds (an image into a video input) are drawn red and never sent.
        if REGISTERING[0]:
            return
        for link in self.links:
            ok = kinds.can_link(socket_kind(link.from_socket), socket_kind(link.to_socket))
            if link.is_valid != ok:
                try:
                    link.is_valid = ok
                except AttributeError:
                    pass


class _Node:
    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE


def _thumbnail(layout, img, scale: float = 9.0) -> None:
    """Large preview of an image/movie datablock (previews are made by the timer, not while drawing)."""
    if img is None:
        return
    prev = img.preview
    if prev is not None and prev.icon_id:
        layout.template_icon(icon_value=prev.icon_id, scale=scale)
    else:
        layout.label(text=img.name, icon="IMAGE_DATA")


# -- source nodes -----------------------------------------------------------------------------------
class ComfyQImageNode(_Node, bpy.types.Node):
    """An image from this file or from disk"""
    bl_idname = "ComfyQImageNode"
    bl_label = "Image"
    bl_icon = "IMAGE_DATA"
    bl_width_default = 300

    image: PointerProperty(type=bpy.types.Image, name="Image")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["image"], "Image")

    def draw_buttons(self, context, layout):
        layout.template_ID(self, "image", open="image.open")
        _thumbnail(layout, self.image)


class ComfyQSceneRenderNode(_Node, bpy.types.Node):
    """A still of the scene camera (clay, matcap or material colors)"""
    bl_idname = "ComfyQSceneRenderNode"
    bl_label = "Scene Render"
    bl_icon = "SCENE"
    bl_width_default = 300

    style: EnumProperty(name="Style", items=STYLES, default="CLAY")
    frame: EnumProperty(name="Frame", items=(
        ("CURRENT", "Current frame", "The scene's current frame"),
        ("START", "First frame", "The first frame of the scene's range"),
        ("END", "Last frame", "The last frame of the scene's range")), default="START")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["image"], "Image")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "style", text="")
        row.prop(self, "frame", text="")
        layout.operator("comfyq.preview_pass", text="Render scene", icon="RESTRICT_VIEW_OFF").mask = False
        _thumbnail(layout, bpy.data.images.get("ComfyQ render preview"))


class ComfyQSceneAnimationNode(_Node, bpy.types.Node):
    """The scene's frame range through the camera, as a clay video (what V2V workflows restyle)"""
    bl_idname = "ComfyQSceneAnimationNode"
    bl_label = "Scene Animation"
    bl_icon = "RENDER_ANIMATION"
    bl_width_default = 300

    style: EnumProperty(name="Style", items=STYLES, default="CLAY")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["video"], "Video")

    def draw_buttons(self, context, layout):
        from ..ops.workflow import scene_facts
        sc = context.scene
        f = scene_facts(sc)
        layout.label(text=f"{f.frame_start}–{f.frame_end} · {f.fps:g} fps · {f.seconds:.1f} s · "
                          f"{sc.camera.name if sc.camera else 'NO CAMERA'}", icon="CAMERA_DATA")
        layout.prop(self, "style", text="")
        op = layout.operator("comfyq.preview_animation", text="Render & play clay animation", icon="PLAY")
        op.force, op.play = True, True
        _thumbnail(layout, bpy.data.images.get("ComfyQ animation pass"))


class ComfyQPromptNode(_Node, bpy.types.Node):
    """A prompt to share between workflow nodes"""
    bl_idname = "ComfyQPromptNode"
    bl_label = "Prompt"
    bl_icon = "TEXT"
    bl_width_default = 320

    text: StringProperty(name="Prompt")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["text"], "Text")

    def draw_buttons(self, context, layout):
        row = layout.row()
        row.scale_y = 3.0
        row.prop(self, "text", text="", placeholder="Describe what you want to create")


# -- workflow node: built from the workflow's parameters ---------------------------------------------
def workflow_of(wf_id: str):
    return next((w for w in SESSION.workflows if w.id == wf_id), None)


class ComfyQWorkflowNode(_Node, bpy.types.Node):
    """A ComfyQ workflow: its inputs are sockets, its settings chips"""
    bl_idname = WORKFLOW_NODE
    bl_label = "Workflow"
    bl_icon = "NODETREE"
    bl_width_default = 380
    bl_width_min = 280

    workflow_id: StringProperty(name="Workflow")
    params: CollectionProperty(type=ComfyQParamValue)
    signature: StringProperty()            # of the parameter list the sockets were built from
    output_kind: StringProperty()
    note: StringProperty()                 # e.g. inputs removed by a workflow update

    def init(self, context):
        self.use_custom_color = True
        try:
            self.color = (0.16, 0.16, 0.17)
        except (TypeError, ValueError):            # RGBA in some versions
            self.color = (0.16, 0.16, 0.17, 1.0)

    def draw_label(self):
        wf = workflow_of(self.workflow_id)
        return wf.name if wf else (self.workflow_id or "Workflow")

    def item(self, key: str):
        return next((it for it in self.params if it.key == key), None)

    def build(self, specs) -> None:
        """Make sockets and stored values match `specs`, keeping links on inputs that still exist."""
        from ..ops.workflow import sync_items
        sync_items(self.params, self.workflow_id, specs)
        wanted = [(s.key, kinds.input_kind(s.kind), s) for s in specs if kinds.input_kind(s.kind)]
        want = {(k, kind) for k, kind, _s in wanted}
        removed = []
        for sock in list(self.inputs):
            if (sock.identifier, socket_kind(sock)) not in want:
                if sock.is_linked:
                    removed.append(sock.name)
                self.inputs.remove(sock)
        have = {sock.identifier for sock in self.inputs}
        for key, kind, s in wanted:
            if key not in have:
                self.inputs.new(SOCKET_IDNAME[kind], scene_match.short_label(s.label, 32), identifier=key)
        for target, (key, _kind, _s) in enumerate(wanted):        # same order as ComfyQ lists them
            idx = next(i for i, sock in enumerate(self.inputs) if sock.identifier == key)
            if idx != target:
                self.inputs.move(idx, target)
        wf = workflow_of(self.workflow_id)
        out = kinds.output_kind(wf.category, wf.tags, wf.id) if wf else "image"
        if self.output_kind != out or len(self.outputs) != 1:
            self.outputs.clear()
            self.outputs.new(SOCKET_IDNAME[out], kinds.SOCKETS[out][0])
            self.output_kind = out
        self.signature = kinds.spec_signature(specs)
        if removed:
            self.note = "Input(s) removed by the workflow update: " + ", ".join(removed)

    def refresh_visibility(self, specs) -> None:
        """Hide inputs the workflow's own rules switch off (e.g. start image when unticked)."""
        current = {it.key: it.value() for it in self.params}
        by_key = {s.key: s for s in specs}
        for sock in self.inputs:
            s = by_key.get(sock.identifier)
            enabled = s is None or not is_disabled(s, current, specs)
            if sock.enabled != enabled:
                sock.enabled = enabled

    def draw_buttons(self, context, layout):
        from ..ops.workflow import shared_budget, vram_badge
        from ..ui.studio import draw_chips
        wf = workflow_of(self.workflow_id)
        specs = SESSION.specs.get(self.workflow_id)
        head = layout.row(align=True)
        if SESSION.client is None:
            head.label(text="Not connected", icon="UNLINKED")
        elif wf is None:
            head.alert = True
            head.label(text=f"Unknown workflow {self.workflow_id}", icon="ERROR")
        else:
            served = wf.id in SESSION.served_ids()
            sub = head.row()
            sub.alert = not served
            sub.label(text="Served" if served else "Not served — ask the teacher",
                      icon="PLAY" if served else "ERROR")
            head.label(text=vram_badge(wf, shared_budget())[0], icon="MEMORY")
        if self.note:
            box = layout.box()
            box.alert = True
            box.label(text=self.note, icon="ERROR")
        if specs is None:
            if SESSION.client is not None:
                layout.label(text="Loading…", icon="SORTTIME")
            return
        current = {it.key: it.value() for it in self.params}
        visible = [s for s in specs if self.item(s.key) is not None and not is_disabled(s, current, specs)]
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=False)
        draw_chips(grid, context.scene, self.workflow_id, visible, get=self.item)
        linked = {sock.identifier for sock in self.inputs if sock.is_linked}
        for s in visible:
            if s.kind in ("prompt", "text") and s.key not in linked:
                row = layout.row()
                row.scale_y = 3.0 if s.kind == "prompt" else 1.3
                row.prop(self.item(s.key), "s", text="",
                         placeholder=scene_match.short_label(s.label, 60) or "Describe what you want to create")
        missing = [s for s in visible if s.is_media and s.required and s.key not in linked]
        for s in missing:
            layout.label(text=f"Link an input to “{scene_match.short_label(s.label, 30)}”", icon="INFO")
        layout.label(text="Generate from the graph: next step (N2) — use the bar meanwhile", icon="TIME")


NODE_CLASSES = (ComfyQImageNode, ComfyQSceneRenderNode, ComfyQSceneAnimationNode, ComfyQPromptNode,
                ComfyQWorkflowNode)
SOURCE_NODES = (ComfyQSceneAnimationNode, ComfyQSceneRenderNode, ComfyQImageNode, ComfyQPromptNode)


def graph_trees():
    return [t for t in bpy.data.node_groups if t.bl_idname == TREE]


def tab_workflows(tab: str):
    return [w for w in SESSION.workflows if tab_of(w) == tab]
