# SPDX-License-Identifier: GPL-3.0-or-later
"""The ComfyQ graph: a node tree in Blender's Node Editor, Moodboard-style.

Source nodes (scene render, clay animation, mask, image, video/audio files, prompt) feed workflow
nodes, whose sockets and setting chips are built from what ComfyQ says the workflow takes. Settings
are chips inside the node (no setting sockets). Running the graph comes in N2.
"""
from __future__ import annotations

import functools
import json

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .. import match as scene_match
from ..client.params import is_disabled
from ..passes.render import STILL_STYLES, STYLES
from ..props import REL_PATH, ComfyQParamValue, tab_of
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


def upstream(sock):
    """(node, output socket) feeding an input socket, looking through Reroutes; (None, None) if nothing.
    Muted links feed nothing."""
    for _hop in range(64):
        if not sock.is_linked:
            return None, None
        link = sock.links[0]
        if link.is_muted:
            return None, None
        if link.from_node.bl_idname == "NodeReroute":
            sock = link.from_node.inputs[0]
            continue
        return link.from_node, link.from_socket
    return None, None


def link_source(link):
    """(node, socket) a link really carries (through Reroutes)."""
    if link.from_node.bl_idname == "NodeReroute":
        return upstream(link.from_node.inputs[0])
    return link.from_node, link.from_socket


# -- the tree -------------------------------------------------------------------------------------
class ComfyQGraphTree(bpy.types.NodeTree):
    """ComfyQ: wire scene passes, images and prompts into the workflows served by ComfyQ"""
    bl_idname = TREE
    bl_label = "ComfyQ Graph"
    bl_icon = "NODETREE"
    # No node groups here: Blender then hides Group Sockets / Color Tag / Node Width in the sidebar
    # and Make Group / Ungroup in the menus.
    bl_use_group_interface = False

    def update(self):
        # Wrong kinds (an image into a video input) are drawn red and never sent.
        if REGISTERING[0]:
            return
        for link in self.links:
            if link.to_node.bl_idname == "NodeReroute":
                continue                       # judged where the reroute's output arrives
            _src, sock = link_source(link)
            ok = sock is None or kinds.can_link(socket_kind(sock), socket_kind(link.to_socket))
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


# Videos: Blender makes no preview for a movie image, but its file-browser thumbnailer reads movies.
# The timer loads them (movie_thumbnail); drawing only looks them up.
_PREVIEWS = [None]                  # bpy.utils.previews collection, made on first use
_MOVIES: dict[str, str] = {}        # file path -> key of its current thumbnail (path + mtime + size)


def movie_thumbnail(path: str) -> None:
    """Load the thumbnail of a video file, and again whenever the file changes (timer only)."""
    import os

    import bpy.utils.previews
    try:
        st = os.stat(path)
    except OSError:
        return
    key = f"{path}|{st.st_mtime_ns}|{st.st_size}"
    if _MOVIES.get(path) == key:
        return
    if _PREVIEWS[0] is None:
        _PREVIEWS[0] = bpy.utils.previews.new()
    if key not in _PREVIEWS[0]:
        _PREVIEWS[0].load(key, path, "MOVIE")
    _MOVIES[path] = key


def free_thumbnails() -> None:
    import bpy.utils.previews
    if _PREVIEWS[0] is not None:
        bpy.utils.previews.remove(_PREVIEWS[0])
        _PREVIEWS[0] = None
    _MOVIES.clear()


def _movie(layout, path: str, scale: float = 9.0) -> bool:
    key, col = _MOVIES.get(path), _PREVIEWS[0]
    item = col.get(key) if key and col is not None else None
    if item is None or not item.icon_id:
        return False
    layout.template_icon(icon_value=item.icon_id, scale=scale)
    return True


def animation_path() -> str:
    """The clay animation last rendered (what the Scene Animation node shows)."""
    from ..passes import animation
    last = animation.last()
    if last is not None and last.path.is_file():
        return str(last.path)
    img = bpy.data.images.get("ComfyQ animation pass")
    return bpy.path.abspath(img.filepath) if img is not None and img.filepath else ""


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


FRAMES = (("START", "First Frame", "The first frame of the scene's range"),
          ("END", "Last Frame", "The last frame of the scene's range"),
          ("CURRENT", "Current Frame", "The scene's current frame"))


def frame_number(scene, which: str) -> int:
    return {"START": scene.frame_start, "END": scene.frame_end}.get(which, scene.frame_current)


class ComfyQSceneRenderNode(_Node, bpy.types.Node):
    """A still of the scene camera (clay, matcap, material colors, or a real Cycles render)"""
    bl_idname = "ComfyQSceneRenderNode"
    bl_label = "Scene Render"
    bl_icon = "SCENE"
    bl_width_default = 300

    style: EnumProperty(name="Style", items=STILL_STYLES, default="CLAY")
    frame: EnumProperty(name="Frame", items=FRAMES, default="START")
    cycles_samples: IntProperty(name="Samples", default=64, min=1, soft_max=1024,
                                description="Set the Cycles samples (denoised). Higher is cleaner and slower; the render runs "
                                            "in a separate Blender so the GPU is free for ComfyQ")

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME["image"], "Image")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "style", text="")
        row.prop(self, "frame", text="")
        if self.style == "CYCLES":
            layout.prop(self, "cycles_samples")
        from ..passes.render import ACTIVE
        tag = f"{self.id_data.name}/{self.name}"
        job = next((j for j in ACTIVE if j.tag == tag), None)
        if job is not None:                        # rendering in its own Blender: progress + ✕
            row = layout.row(align=True)
            row.progress(factor=job.progress(), type="BAR", text=job.text())
            row.operator("comfyq.cancel_cycles", text="", icon="CANCEL")
        else:
            op = layout.operator("comfyq.preview_pass", text=f"Render Frame {frame_number(context.scene, self.frame)}",
                                 icon="RENDER_STILL")
            op.mask, op.style, op.frame = False, self.style, frame_number(context.scene, self.frame)
            op.tag = tag
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
        layout.label(text=f"Selected objects mark the area to repaint ({n} selected)",
                     icon="CHECKMARK" if n else "ERROR")
        row = layout.row(align=True)
        row.prop(self, "style", text="")
        row.prop(self, "frame", text="")
        op = layout.operator("comfyq.preview_pass", text="Render Mask", icon="SELECT_SET")
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
        op = layout.operator("comfyq.preview_animation", text="Render & Play Clay Animation", icon="RENDER_ANIMATION")
        op.force, op.play, op.style = True, True, self.style
        _movie(layout, animation_path())


class _FileNode(_Node):
    bl_width_default = 300
    comfyq_kind = ""

    filepath: StringProperty(name="File", subtype="FILE_PATH", options=REL_PATH)

    def init(self, context):
        self.outputs.new(SOCKET_IDNAME[self.comfyq_kind], kinds.SOCKETS[self.comfyq_kind][0])

    def draw_buttons(self, context, layout):
        layout.prop(self, "filepath", text="")
        path = bpy.path.abspath(self.filepath) if self.filepath else ""
        if path:
            row = layout.row(align=True)
            row.operator("comfyq.show_media", text="Show", icon="HIDE_OFF").path = path
            row.operator("comfyq.open_media", text="Open", icon="URL").path = path
            if self.comfyq_kind == "video":
                _movie(layout, path)


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
        draw_text_field(layout, self, "text", "text_block", self, "", True, "Describe the image or video to create")


# -- workflow node: built from the workflow's parameters ---------------------------------------------
def workflow_of(wf_id: str):
    return next((w for w in SESSION.workflows if w.id == wf_id), None)


class ComfyQResultRecord(bpy.types.PropertyGroup):
    """One result a workflow node has made (its history, newest last)."""
    job_id: StringProperty()
    path: StringProperty(subtype="FILE_PATH")
    made_hash: StringProperty()             # inputs + settings it was made with
    when: FloatProperty()


HISTORY_MAX = 20


def remember_result(node, job_id: str, path: str, made_hash: str) -> None:
    """Set a node's current result and keep it in its history."""
    import time
    node.result, node.result_hash = path, made_hash
    if any(r.path == path for r in node.history):
        return
    rec = node.history.add()
    rec.job_id, rec.path, rec.made_hash, rec.when = job_id, path, made_hash, time.time()
    while len(node.history) > HISTORY_MAX:
        node.history.remove(0)


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
    history: CollectionProperty(type=ComfyQResultRecord)    # earlier results (sidebar › Use this result)
    extra_inputs: IntProperty(default=0, min=0)  # optional inputs revealed by "+ Add …" (when there are many)

    def init(self, context):
        self.use_custom_color = True
        try:
            self.color = (0.16, 0.16, 0.17)
        except (TypeError, ValueError):            # RGBA in some versions
            self.color = (0.16, 0.16, 0.17, 1.0)

    def draw_label(self):
        wf = workflow_of(self.workflow_id)
        return wf.name if wf else (self.workflow_id or "Choose Model")

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
        """Hide inputs the workflow's own rules switch off (e.g. start image when unticked), and the free
        optional inputs collapsed behind "+ Add …" (a workflow with 16 reference images shows one at a time)."""
        current = self.current_values()
        by_key = {s.key: s for s in specs}
        shown = self.shown_optional(specs)
        for sock in self.inputs:
            s = by_key.get(sock.identifier)
            enabled = s is None or not is_disabled(s, current, specs)
            if sock.enabled != enabled:
                sock.enabled = enabled
            hide = s is not None and s.is_media and not s.required and not sock.is_linked                 and sock.identifier not in shown
            if sock.hide != hide:
                sock.hide = hide

    COLLAPSE_OVER = 3            # more free optional inputs than this: show the next one, the rest behind "+ Add"

    def shown_optional(self, specs) -> set[str]:
        """Keys of the free (unlinked) optional media inputs to show."""
        current = self.current_values()
        linked = {sock.identifier for sock in self.inputs if sock.is_linked}
        free = [s.key for s in specs if s.is_media and not s.required and s.key not in linked
                and self.item(s.key) is not None and not is_disabled(s, current, specs)]
        if len(free) <= self.COLLAPSE_OVER:
            return set(free)
        return set(free[:1 + self.extra_inputs])

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
        target(swap.operator("comfyq.graph_set_workflow", text="Change Model..." if wf else "Choose Model...",
                             icon="DOWNARROW_HLT"), self)
        if SESSION.client is None:
            head.label(text="Not connected", icon="UNLINKED")
        elif self.workflow_id and wf is None:
            head.alert = True
            head.label(text="Unknown workflow", icon="ERROR")
        elif wf is not None:
            from .run import solo_on
            served = wf.id in SESSION.served_ids()
            solo = not served and solo_on()
            sub = head.row()
            sub.alert = not served and not solo
            sub.label(text="Served" if served else ("Loads on Run" if solo else "Not Served"),
                      icon="CHECKMARK" if served else ("PACKAGE" if solo else "ERROR"))
            head.label(text=vram_badge(wf, shared_budget())[0], icon="MEMORY")
            if solo:
                head.operator("comfyq.graph_load_model", text="", icon="PACKAGE").workflow_id = wf.id
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
                                scene_match.short_label(s.label, 60) or "Describe the image or video to create")
        shown = self.shown_optional(specs)
        hidden = [s for s in visible if s.is_media and not s.required and s.key not in linked and s.key not in shown]
        for s in visible:                   # unlinked inputs: say so, and offer the matching source
            kind = kinds.input_kind(s.kind)
            if s.is_media and s.key not in linked and (s.required or s.key in shown):
                row = layout.row(align=True)
                row.alert = s.required
                row.label(text=f"{scene_match.short_label(s.label, 28)}: not linked"
                               + (" (needed)" if s.required else ""), icon="ERROR" if s.required else "INFO")
                target(row.operator("comfyq.graph_add_source", text=SOURCE_LABEL[kind], icon="ADD"), self, s.key)
        if hidden:
            noun = {"image": "Reference Image", "video": "Video", "audio": "Audio", "mask": "Mask"}.get(
                hidden[0].kind, "Input")
            target(layout.operator("comfyq.graph_more_inputs", text=f"Add {noun} ({len(hidden)} Free)", icon="ADD"),
                   self)
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
                from ..ui.studio import queue_text
                row.label(text=queue_text(job), icon="SORTTIME")
            row.operator("comfyq.cancel", text="", icon="CANCEL").job_id = self.job_id
        elif state in ("waiting", "sending"):
            row.label(text="Waiting for its inputs" if state == "waiting" else "Sending…", icon="SORTTIME")
        elif state == "rendering":
            row.label(text="Waiting for the Cycles render", icon="SORTTIME")
            row.operator("comfyq.graph_stop", text="", icon="CANCEL").tree = self.id_data.name
        else:
            gen = row.row(align=True)
            gen.enabled = self.workflow_id in SESSION.specs
            target(gen.operator("comfyq.graph_run", text="Generate", icon="PLAY"), self)
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
        if not _movie(layout, str(path), scale=10.0):
            img = bpy.data.images.get(path.name)
            if img is not None and img.source != "MOVIE":
                _thumbnail(layout, img, scale=10.0)
        res = layout.column(align=True)
        res.label(text=("Result · " if self.result_hash == self.pending_hash or not self.pending_hash
                        else "Older result · ") + path.name, icon="CHECKMARK")
        btn = res.row(align=True)
        btn.operator("comfyq.show_media", text="Show", icon="HIDE_OFF").path = str(path)
        btn.operator("comfyq.open_media", text="Open", icon="URL").path = str(path)
        op = btn.operator("comfyq.import_file", text="Import", icon="IMPORT")
        op.job_id, op.path = self.job_id, str(path)


# -- surfaces: SAM3 segmentation by name → a prompt naming each surface and its material ----------------
SURFACES_NODE = "ComfyQSurfacesNode"
SAM3_ID = "utility_video_segment_sam3"
_INTRO = ("Photorealistic architectural photograph, natural daylight, soft realistic shadows, realistic "
          "materials.")
_OUTRO = "Keep the exact layout, camera, perspective and proportions."


@functools.lru_cache(maxsize=64)
def _surfaces_prompt(regions_json: str, surfaces: str, intro: str, outro: str) -> str:
    from ..surfaces import Region, materials_of, parse_surfaces, surface_prompt
    try:
        found = json.loads(regions_json) if regions_json else []
    except ValueError:
        found = []
    regions = [Region(r["label"], r["coverage_pct"] / 100, tuple(r["bbox"]), r.get("mask", "")) for r in found]
    return surface_prompt(regions, materials_of(parse_surfaces(surfaces)), intro, outro)


class ComfyQSurfacesNode(_Node, bpy.types.Node):
    """SAM3 finds each named surface (floor, walls, window…) in the picture: Prompt = the surfaces it found,
    each with its material; Overlay = the picture with every surface tinted"""
    bl_idname = SURFACES_NODE
    bl_label = "Surfaces (SAM3)"
    bl_icon = "MATERIAL"
    bl_width_default = 400
    bl_width_min = 300

    workflow_id: StringProperty(default=SAM3_ID)     # what ComfyQ must serve (solo mode loads it)
    text: StringProperty(name="Surfaces", description="List the surfaces to find, one per line as "
                                                       "label: material. Separate phrases with | to merge them "
                                                       "into one label")
    text_block: PointerProperty(type=bpy.types.Text, name="Surfaces text")
    intro: StringProperty(name="Before", default=_INTRO, description="Put this text before the surfaces (style, light)")
    outro: StringProperty(name="After", default=_OUTRO, description="Put this text after the surfaces")
    threshold: FloatProperty(name="Detection Threshold", default=0.5, min=0.1, max=0.9, step=5,
                             description="Set how sure SAM3 must be. Lower finds more, higher is stricter")
    note: StringProperty()
    job_id: StringProperty()
    result: StringProperty(subtype="FILE_PATH")       # the overlay
    result_hash: StringProperty()
    pending_hash: StringProperty()
    masks_key: StringProperty()                       # picture + labels + threshold the masks were made from
    regions: StringProperty()                         # JSON: label, coverage, bbox, mask file
    history: CollectionProperty(type=ComfyQResultRecord)

    def init(self, context):
        from ..surfaces import DEFAULT_SURFACES
        self.inputs.new(SOCKET_IDNAME["image"], "Image")
        self.outputs.new(SOCKET_IDNAME["text"], "Prompt")
        self.outputs.new(SOCKET_IDNAME["image"], "Overlay")
        txt = bpy.data.texts.new("ComfyQ surfaces")
        txt.write(DEFAULT_SURFACES)
        self.text_block = txt

    def value(self) -> str:
        return self.text_block.as_string() if self.text_block else self.text

    def draw_label(self):
        return self.label or self.bl_label

    def found(self) -> list[dict]:
        try:
            return json.loads(self.regions) if self.regions else []
        except ValueError:
            return []

    def prompt(self) -> str:
        """The surfaces SAM3 found, named with the materials of the list as it is NOW (editing a material
        needs no new segmentation). Cached on its inputs: the node draws it on every redraw."""
        return _surfaces_prompt(self.regions, self.value(), self.intro, self.outro)

    def draw_buttons(self, context, layout):
        from pathlib import Path

        from ..ui.studio import wrap
        from .run import running, solo_on
        served = self.workflow_id in SESSION.served_ids()
        row = layout.row()
        row.alert = not served and not solo_on()
        row.label(text="SAM3 served" if served else ("SAM3 loads on Run" if solo_on() else
                                                     "SAM3 not served: ask the teacher"),
                  icon="CHECKMARK" if served else ("IMPORT" if solo_on() else "ERROR"))
        layout.label(text="One surface per line, as label: material", icon="MATERIAL")
        draw_text_field(layout, self, "text", "text_block", self, "", True,
                        "floor: oak parquet; wall|left wall|right wall: white plaster")
        col = layout.column(align=True)
        col.prop(self, "intro", text="Before")
        col.prop(self, "outro", text="After")
        layout.prop(self, "threshold")
        if self.note:
            box = layout.box()
            box.alert = self.note.startswith(("Failed", "Not run", "Could not", "Link"))
            for line in wrap(self.note, 60, max_lines=3):
                box.label(text=line)
        row = layout.row(align=True)
        row.scale_y = 1.4
        state = running(self.id_data.name, self.name)
        if state:
            row.label(text={"booked": "Segmenting…", "sending": "Segmenting…",
                                "rendering": "Waiting for the Cycles render"}.get(state, "Waiting for its inputs"),
                      icon="SORTTIME")
            row.operator("comfyq.graph_stop", text="", icon="CANCEL").tree = self.id_data.name
        else:
            target(row.operator("comfyq.graph_run", text="Find Surfaces", icon="PLAY"), self)
            op = row.operator("comfyq.graph_run", text="", icon="FILE_REFRESH")
            target(op, self)
            op.force = True
        found = self.found()
        if not found:
            return
        path = Path(self.result) if self.result else None
        img = bpy.data.images.get(path.name) if path is not None and path.is_file() else None
        if img is not None:
            _thumbnail(layout, img, scale=10.0)
        box = layout.box().column(align=True)
        box.label(text=f"Found {sum(1 for r in found if r.get('found', True))}: " +
                  ", ".join(f"{r['label']} {r['coverage_pct']:.0f}%" for r in found if r.get("found", True)))
        for line in wrap(self.prompt(), 64, max_lines=6):
            box.label(text=line)
        if path is not None and path.is_file():
            btn = layout.row(align=True)
            btn.operator("comfyq.show_media", text="Show Overlay", icon="HIDE_OFF").path = str(path)
            btn.operator("comfyq.open_media", text="Open", icon="URL").path = str(path)


# -- output nodes: act on what is linked into them (no job of their own) ------------------------------
class _OutputNode(_Node):
    bl_width_default = 300
    auto_label = ""

    handled: StringProperty()              # the result last acted on (path + mtime): acts once per result
    status: StringProperty()               # what it last did

    def init(self, context):
        self.inputs.new(SOCKET_IDNAME["any"], "Result")

    def draw_buttons(self, context, layout):
        from . import outputs
        media = outputs.linked(self)
        if media is None:
            layout.label(text="Link a result or a source here", icon="INFO")
        else:
            layout.label(text=media.label, icon=media.icon)
        self.draw_body(context, layout, media)
        row = layout.row(align=True)
        row.prop(self, "auto", text=self.auto_label)
        act = row.row(align=True)
        act.enabled = media is not None
        target(act.operator("comfyq.graph_output", text=self.verb.title(), icon=self.bl_icon), self)
        target(layout.operator("comfyq.graph_run", text="Generate Upstream, then " + self.verb.title(), icon="PLAY"),
               self)
        if self.status:
            layout.label(text=self.status, icon="CHECKMARK")

    def draw_body(self, context, layout, media) -> None:
        pass


class ComfyQViewerNode(_OutputNode, bpy.types.Node):
    """Shows what is linked here and plays it in the ComfyQ window's viewer"""
    bl_idname = "ComfyQViewerNode"
    bl_label = "Viewer"
    bl_icon = "HIDE_OFF"
    auto_label = "Show when ready"
    verb = "show"

    auto: BoolProperty(name="Show When Ready", default=True,
                       description="Show each new result in the ComfyQ viewer as soon as it arrives")

    def draw_body(self, context, layout, media) -> None:
        if media is None:
            return
        if media.text is not None:
            from ..ui.studio import wrap
            box = layout.box().column(align=True)
            for line in wrap(media.text, 48, max_lines=12) or ["(empty)"]:
                box.label(text=line)
        elif media.kind == "video" and media.path:
            _movie(layout, media.path)
        elif media.kind in ("image", "mask") and media.path:
            from pathlib import Path
            _thumbnail(layout, bpy.data.images.get(Path(media.path).name))


class ComfyQImportNode(_OutputNode, bpy.types.Node):
    """Brings what is linked here into the scene: image → Image Editor, video/audio → “ComfyQ Results”
    scene, 3D model / splat → a collection"""
    bl_idname = "ComfyQImportNode"
    bl_label = "Import to Scene"
    bl_icon = "IMPORT"
    auto_label = "Import when ready"
    verb = "import"

    auto: BoolProperty(name="Import When Ready", default=False,
                       description="Import each new result as soon as it arrives")
    replace: BoolProperty(name="Replace Previous Import", default=True,
                          description="Remove what this node imported last time (strips, objects) before importing")
    imported: StringProperty()             # JSON: what the last import created (to replace it)
    # 3D models / splats: generators put them at the origin at their own scale (inside the scene)
    place: EnumProperty(
        name="Place", default="BESIDE",
        items=(("BESIDE", "Beside the Scene", "Place it to the right of the scene's objects, never hidden inside them"),
               ("CURSOR", "At the 3D Cursor", "Place it at the 3D cursor"),
               ("ORIGIN", "At the Origin", "Place it at the world origin"),
               ("ASIS", "As Made", "Keep the position and size stored in the file")),
        description="Choose where an imported 3D model or splat goes")
    on_ground: BoolProperty(name="On the Ground", default=True,
                            description="Put its lowest point on the ground (z = 0, or the 3D cursor's height)")
    size: FloatProperty(name="Size", default=0.0, min=0.0, soft_max=50.0, subtype="DISTANCE",
                        description="Scale it so its longest side is this long, for example 4.5 m for a car. 0 keeps "
                                    "the size it was made at")

    def placement(self):
        from ..importers.media import Placement
        return Placement(self.place, self.on_ground, self.size)

    def draw_body(self, context, layout, media) -> None:
        layout.prop(self, "replace")
        if media is None or media.kind in ("model", "splat"):
            col = layout.column(align=True)
            col.prop(self, "place", text="")
            row = col.row(align=True)
            row.enabled = self.place != "ASIS"
            row.prop(self, "on_ground", toggle=True)
            row.prop(self, "size")


class ComfyQSaveNode(_OutputNode, bpy.types.Node):
    """Copies what is linked here to a folder"""
    bl_idname = "ComfyQSaveNode"
    bl_label = "Save"
    bl_icon = "FILE_TICK"
    auto_label = "Save when ready"
    verb = "save"

    auto: BoolProperty(name="Save When Ready", default=False,
                       description="Save each new result as soon as it arrives")
    folder: StringProperty(name="Folder", subtype="DIR_PATH", default="//comfyq/", options=REL_PATH,
                           description="Where copies go (// = next to the .blend)")
    pattern: StringProperty(name="Name", default="{source}_{date}_{time}",
                            description="File name: {node} {date} {time} {job} {workflow} {source}; "
                                        "the extension is the result's")

    def draw_body(self, context, layout, media) -> None:
        col = layout.column(align=True)
        col.prop(self, "folder", text="")
        col.prop(self, "pattern", text="")


OUTPUT_NODES = ("ComfyQViewerNode", "ComfyQImportNode", "ComfyQSaveNode")


# Source node offered by the "+" of an unlinked input
SOURCE_FOR = {"image": "ComfyQSceneRenderNode", "video": "ComfyQSceneAnimationNode", "mask": "ComfyQMaskNode",
              "audio": "ComfyQAudioFileNode", "text": "ComfyQPromptNode"}
SOURCE_LABEL = {"image": "Scene render", "video": "Scene animation", "mask": "Selection mask",
                "audio": "Audio file", "text": "Prompt"}

NODE_CLASSES = (ComfyQImageNode, ComfyQSceneRenderNode, ComfyQMaskNode, ComfyQSceneAnimationNode,
                ComfyQVideoFileNode, ComfyQAudioFileNode, ComfyQPromptNode, ComfyQWorkflowNode,
                ComfyQSurfacesNode, ComfyQViewerNode, ComfyQImportNode, ComfyQSaveNode)
RUN_NODES = (WORKFLOW_NODE, SURFACES_NODE)       # nodes a graph run books (a ComfyQ job / a SAM3 segmentation)
OUTPUT_CLASSES = (ComfyQViewerNode, ComfyQImportNode, ComfyQSaveNode)
SOURCE_NODES = (ComfyQSceneAnimationNode, ComfyQSceneRenderNode, ComfyQMaskNode, ComfyQImageNode,
                ComfyQVideoFileNode, ComfyQAudioFileNode, ComfyQPromptNode)


def graph_trees():
    return [t for t in bpy.data.node_groups if t.bl_idname == TREE]


def tab_workflows(tab: str):
    return [w for w in SESSION.workflows if tab_of(w) == tab]


def find_node(tree_name: str, node_name: str):
    tree = bpy.data.node_groups.get(tree_name)
    return tree.nodes.get(node_name) if tree is not None and tree.bl_idname == TREE else None
