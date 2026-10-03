# SPDX-License-Identifier: GPL-3.0-or-later
"""The ComfyQ graph: a node tree in Blender's Node Editor, Moodboard-style.

Source nodes (scene render, clay animation, mask, image, video/audio files, prompt) feed workflow
nodes, whose sockets and setting chips are built from what ComfyQ says the workflow takes. Settings
are chips inside the node (no setting sockets). Running the graph comes in N2.
"""
from __future__ import annotations

import json

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


def target(op, node, key: str = "") -> None:
    """Point a node operator at this node (operators get names, never Python references)."""
    op.tree, op.node, op.key = node.id_data.name, node.name, key


def draw_text_field(layout, owner, str_attr: str, text_attr: str, node, key: str, big: bool,
                    placeholder: str) -> None:
    """A prompt: one-line field with ⤢, or a multi-line Text block (preview + Edit / ✕)."""
    from ..ui.studio import wrap
    txt = getattr(owner, text_attr)
    if txt is not None:
        col = layout.column(align=True)
        head = col.row(align=True)
        head.label(text=f"“{txt.name}”", icon="TEXT")
        target(head.operator("comfyq.graph_edit_text", text="Edit", icon="GREASEPENCIL"), node, key)
        target(head.operator("comfyq.graph_unlink_text", text="", icon="X"), node, key)
        box = col.box().column(align=True)
        for line in wrap(txt.as_string(), 58, max_lines=8):
            box.label(text=line)
        return
    row = layout.row(align=True)
    field = row.row(align=True)
    field.scale_y = 3.0 if big else 1.3
    field.prop(owner, str_attr, text="", placeholder=placeholder)
    ed = row.column(align=True)
    ed.scale_y = field.scale_y
    target(ed.operator("comfyq.graph_edit_text", text="", icon="FULLSCREEN_ENTER"), node, key)


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


FRAMES = (("START", "First frame", "The first frame of the scene's range"),
          ("END", "Last frame", "The last frame of the scene's range"),
          ("CURRENT", "Current frame", "The scene's current frame"))


def frame_number(scene, which: str) -> int:
    return {"START": scene.frame_start, "END": scene.frame_end}.get(which, scene.frame_current)


class ComfyQSceneRenderNode(_Node, bpy.types.Node):
    """A still of the scene camera (clay, matcap or material colors)"""
    bl_idname = "ComfyQSceneRenderNode"
    bl_label = "Scene Render"
    bl_icon = "SCENE"
    bl_width_default = 300

    style: EnumProperty(name="Style", items=STYLES, default="CLAY")
    frame: EnumProperty(name="Frame", items=FRAMES, default="START")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["image"], "Image")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "style", text="")
        row.prop(self, "frame", text="")
        op = layout.operator("comfyq.preview_pass", text=f"Render frame {frame_number(context.scene, self.frame)}",
                             icon="RESTRICT_VIEW_OFF")
        op.mask, op.style, op.frame = False, self.style, frame_number(context.scene, self.frame)
        _thumbnail(layout, bpy.data.images.get("ComfyQ render preview"))


class ComfyQMaskNode(_Node, bpy.types.Node):
    """The scene camera with the selected objects cut out: the area an inpaint workflow repaints"""
    bl_idname = "ComfyQMaskNode"
    bl_label = "Selection Mask"
    bl_icon = "SELECT_SET"
    bl_width_default = 300

    style: EnumProperty(name="Style", items=STYLES, default="CLAY")
    frame: EnumProperty(name="Frame", items=FRAMES, default="CURRENT")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["mask"], "Mask")

    def draw_buttons(self, context, layout):
        n = len(context.selected_objects)
        layout.label(text=f"Selected objects = area to repaint ({n} selected)",
                     icon="CHECKMARK" if n else "ERROR")
        row = layout.row(align=True)
        row.prop(self, "style", text="")
        row.prop(self, "frame", text="")
        op = layout.operator("comfyq.preview_pass", text="Render mask", icon="SELECT_SET")
        op.mask, op.style, op.frame = True, self.style, frame_number(context.scene, self.frame)
        _thumbnail(layout, bpy.data.images.get("ComfyQ mask preview"))


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
        op.force, op.play, op.style = True, True, self.style
        _thumbnail(layout, bpy.data.images.get("ComfyQ animation pass"))


class _FileNode(_Node):
    bl_width_default = 300
    comfyq_kind = ""

    filepath: StringProperty(name="File", subtype="FILE_PATH")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME[self.comfyq_kind], kinds.SOCKETS[self.comfyq_kind][0])

    def draw_buttons(self, context, layout):
        layout.prop(self, "filepath", text="")
        path = bpy.path.abspath(self.filepath) if self.filepath else ""
        if path:
            row = layout.row(align=True)
            row.operator("comfyq.show_media", text="Show", icon="PLAY").path = path
            row.operator("comfyq.open_media", text="Open", icon="WINDOW").path = path


class ComfyQVideoFileNode(_FileNode, bpy.types.Node):
    """A video file from disk"""
    bl_idname = "ComfyQVideoFileNode"
    bl_label = "Video File"
    bl_icon = "FILE_MOVIE"
    comfyq_kind = "video"


class ComfyQAudioFileNode(_FileNode, bpy.types.Node):
    """An audio file from disk"""
    bl_idname = "ComfyQAudioFileNode"
    bl_label = "Audio File"
    bl_icon = "SOUND"
    comfyq_kind = "audio"


class ComfyQPromptNode(_Node, bpy.types.Node):
    """A prompt to share between workflow nodes"""
    bl_idname = "ComfyQPromptNode"
    bl_label = "Prompt"
    bl_icon = "TEXT"
    bl_width_default = 340

    text: StringProperty(name="Prompt")
    text_block: PointerProperty(type=bpy.types.Text, name="Prompt text")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["text"], "Text")

    def value(self) -> str:
        return self.text_block.as_string().strip() if self.text_block else self.text

    def draw_buttons(self, context, layout):
        draw_text_field(layout, self, "text", "text_block", self, "", True, "Describe what you want to create")


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
    params: CollectionProperty(type=ComfyQParamValue)    # values of every workflow this node has shown
    signature: StringProperty()            # of the parameter list the sockets were built from
    output_kind: StringProperty()
    note: StringProperty()                 # e.g. inputs removed by a workflow update
    relink: StringProperty()               # JSON: links to restore after a model change
    job_id: StringProperty()               # last job booked for this node
    result: StringProperty(subtype="FILE_PATH")   # its downloaded result (fed to linked nodes)
    result_hash: StringProperty()          # inputs + settings the result was made with
    pending_hash: StringProperty()         # same, for the job in progress

    def init(self, context):
        self.use_custom_color = True
        try:
            self.color = (0.16, 0.16, 0.17)
        except (TypeError, ValueError):            # RGBA in some versions
            self.color = (0.16, 0.16, 0.17, 1.0)

    def draw_label(self):
        wf = workflow_of(self.workflow_id)
        return wf.name if wf else (self.workflow_id or "Choose a model")

    def item(self, key: str):
        return next((it for it in self.params if it.key == key and it.workflow_id == self.workflow_id), None)

    def current_values(self) -> dict:
        return {it.key: it.value() for it in self.params if it.workflow_id == self.workflow_id}

    def change_workflow(self, wf_id: str) -> None:
        """Switch model; links come back on inputs of the same kind once the new sockets exist."""
        from ..ops.workflow import request_specs
        if wf_id == self.workflow_id:
            return
        old = [(lk.from_node.name, lk.from_socket.identifier, socket_kind(lk.to_socket))
               for lk in self.id_data.links if lk.to_node == self]
        self.relink = json.dumps(old)
        self.workflow_id = wf_id
        self.note = ""
        specs = SESSION.specs.get(wf_id)
        if specs is not None:
            self.build(specs)
        else:
            request_specs(wf_id)                       # the timer builds the node when they arrive

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
        for i, (key, _kind, _s) in enumerate(wanted):             # same order as ComfyQ lists them
            idx = next(j for j, sock in enumerate(self.inputs) if sock.identifier == key)
            if idx != i:
                self.inputs.move(idx, i)
        wf = workflow_of(self.workflow_id)
        out = kinds.output_kind(wf.category, wf.tags, wf.id) if wf else "image"
        if self.output_kind != out or len(self.outputs) != 1:
            self.outputs.clear()
            self.outputs.new(SOCKET_IDNAME[out], kinds.SOCKETS[out][0])
            self.output_kind = out
        self.signature = kinds.spec_signature(specs)
        if self.relink:
            self._restore_links()
        elif removed:
            self.note = "Input(s) removed by the workflow update: " + ", ".join(removed)

    def _restore_links(self) -> None:
        tree = self.id_data
        old, self.relink = json.loads(self.relink), ""
        for from_node, from_id, kind in old:
            src = tree.nodes.get(from_node)
            out = next((o for o in src.outputs if o.identifier == from_id), None) if src else None
            dest = next((i for i in self.inputs if socket_kind(i) == kind and not i.is_linked), None)
            if out is not None and dest is not None:
                tree.links.new(out, dest)

    def refresh_visibility(self, specs) -> None:
        """Hide inputs the workflow's own rules switch off (e.g. start image when unticked)."""
        current = self.current_values()
        by_key = {s.key: s for s in specs}
        for sock in self.inputs:
            s = by_key.get(sock.identifier)
            enabled = s is None or not is_disabled(s, current, specs)
            if sock.enabled != enabled:
                sock.enabled = enabled

    def draw_buttons(self, context, layout):
        self._draw(context, layout, columns=2)

    def draw_buttons_ext(self, context, layout):       # sidebar › Node: every setting, one per row
        wf = workflow_of(self.workflow_id)
        if wf is not None and wf.description:
            from ..ui.studio import wrap
            col = layout.column(align=True)
            for line in wrap(wf.description, 48, max_lines=8):
                col.label(text=line)
        self._draw(context, layout, columns=1)

    def _draw(self, context, layout, columns: int) -> None:
        from ..ops.workflow import shared_budget, vram_badge
        from ..ui.studio import draw_chips
        wf = workflow_of(self.workflow_id)
        specs = SESSION.specs.get(self.workflow_id)
        head = layout.row(align=True)
        swap = head.row(align=True)
        swap.scale_y = 1.2
        target(swap.operator("comfyq.graph_set_workflow", text="Change model" if wf else "Choose a model…",
                             icon="DOWNARROW_HLT"), self)
        if SESSION.client is None:
            head.label(text="Not connected", icon="UNLINKED")
        elif self.workflow_id and wf is None:
            head.alert = True
            head.label(text="Unknown workflow", icon="ERROR")
        elif wf is not None:
            served = wf.id in SESSION.served_ids()
            sub = head.row()
            sub.alert = not served
            sub.label(text="Served" if served else "Not served", icon="PLAY" if served else "ERROR")
            head.label(text=vram_badge(wf, shared_budget())[0], icon="MEMORY")
        if self.note:
            box = layout.box()
            box.alert = True
            box.label(text=self.note, icon="ERROR")
        if not self.workflow_id:
            return
        if specs is None:
            if SESSION.client is not None:
                layout.label(text="Loading…", icon="SORTTIME")
            return
        current = self.current_values()
        visible = [s for s in specs if self.item(s.key) is not None and not is_disabled(s, current, specs)]
        grid = layout.grid_flow(row_major=True, columns=columns, even_columns=True, align=False)
        draw_chips(grid, context.scene, self.workflow_id, visible, get=self.item)
        linked = {sock.identifier for sock in self.inputs if sock.is_linked}
        for s in visible:
            if s.kind in ("prompt", "text") and s.key not in linked:
                draw_text_field(layout, self.item(s.key), "s", "text", self, s.key, s.kind == "prompt",
                                scene_match.short_label(s.label, 60) or "Describe what you want to create")
        for s in visible:                   # unlinked inputs: say so, and offer the matching source
            kind = kinds.input_kind(s.kind)
            if s.is_media and s.key not in linked:
                row = layout.row(align=True)
                row.alert = s.required
                row.label(text=f"{scene_match.short_label(s.label, 28)}: not linked"
                               + (" (needed)" if s.required else ""), icon="ERROR" if s.required else "INFO")
                target(row.operator("comfyq.graph_add_source", text=SOURCE_LABEL[kind], icon="ADD"), self, s.key)
        self._draw_run(context, layout)

    def _draw_run(self, context, layout) -> None:
        """Generate · status / progress · result (thumbnail, Show, Open, Import)."""
        from pathlib import Path

        from ..ui.studio import PHASE_TEXT
        from .run import running
        state = running(self.id_data.name, self.name)
        view = SESSION.jobs.get(self.job_id) if self.job_id else None
        job = view.job if view else None
        live = state or (job is not None and not job.done)
        row = layout.row(align=True)
        row.scale_y = 1.4
        if live and job is not None and not job.done:
            if job.progress is not None and job.phase == "executing":
                row.progress(factor=job.progress, type="BAR", text=f"Generating {job.progress * 100:.0f}%")
            else:
                row.label(text=PHASE_TEXT.get(job.phase, job.phase), icon="SORTTIME")
            row.operator("comfyq.cancel", text="", icon="X").job_id = self.job_id
        elif state in ("waiting", "sending"):
            row.label(text="Waiting for its inputs" if state == "waiting" else "Sending…", icon="SORTTIME")
        else:
            gen = row.row(align=True)
            gen.enabled = self.workflow_id in SESSION.specs
            target(gen.operator("comfyq.graph_run", text="Generate", icon="RENDER_STILL"), self)
            op = row.operator("comfyq.graph_run", text="", icon="FILE_REFRESH")
            target(op, self)
            op.force = True
        if job is not None and job.done and not job.ok and not state:
            col = layout.column(align=True)
            col.alert = True
            col.label(text=f"{PHASE_TEXT.get(job.phase, job.phase)} {job.error or ''}".strip(), icon="ERROR")
        path = Path(self.result) if self.result else None
        if path is None or not path.is_file():
            return
        img = bpy.data.images.get(path.name)
        if img is not None and img.source != "MOVIE":
            _thumbnail(layout, img, scale=10.0)
        res = layout.column(align=True)
        res.label(text=("Result · " if self.result_hash == self.pending_hash or not self.pending_hash
                        else "Older result · ") + path.name, icon="CHECKMARK")
        btn = res.row(align=True)
        btn.operator("comfyq.show_media", text="Show", icon="PLAY").path = str(path)
        btn.operator("comfyq.open_media", text="Open", icon="WINDOW").path = str(path)
        op = btn.operator("comfyq.import_file", text="Import", icon="IMPORT")
        op.job_id, op.path = self.job_id, str(path)


# Source node offered by the "+" of an unlinked input
SOURCE_FOR = {"image": "ComfyQSceneRenderNode", "video": "ComfyQSceneAnimationNode", "mask": "ComfyQMaskNode",
              "audio": "ComfyQAudioFileNode", "text": "ComfyQPromptNode"}
SOURCE_LABEL = {"image": "Scene render", "video": "Scene animation", "mask": "Selection mask",
                "audio": "Audio file", "text": "Prompt"}

NODE_CLASSES = (ComfyQImageNode, ComfyQSceneRenderNode, ComfyQMaskNode, ComfyQSceneAnimationNode,
                ComfyQVideoFileNode, ComfyQAudioFileNode, ComfyQPromptNode, ComfyQWorkflowNode)
SOURCE_NODES = (ComfyQSceneAnimationNode, ComfyQSceneRenderNode, ComfyQMaskNode, ComfyQImageNode,
                ComfyQVideoFileNode, ComfyQAudioFileNode, ComfyQPromptNode)


def graph_trees():
    return [t for t in bpy.data.node_groups if t.bl_idname == TREE]


def tab_workflows(tab: str):
    return [w for w in SESSION.workflows if tab_of(w) == tab]


def find_node(tree_name: str, node_name: str):
    tree = bpy.data.node_groups.get(tree_name)
    return tree.nodes.get(node_name) if tree is not None and tree.bl_idname == TREE else None
