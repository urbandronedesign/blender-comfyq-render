# SPDX-License-Identifier: GPL-3.0-or-later
"""Picture Mask: editing the zone ON THE PICTURE (Image Editor) and making the mask.

*Edit on Picture* (node button) shows the picture with its zone in red in an Image Editor and turns on the
**ComfyQ Picture Mask** tool there:

    click            tick / untick the found object under the mouse
    drag             add a box             Shift+drag  add a free shape
    Ctrl+drag        cut a box             Ctrl+Shift+drag  cut a free shape

Coordinates are the picture's own (0..1, y up), the same frame coordinates `zone.py` uses for the 3D tool.
"""
from __future__ import annotations

import time
from pathlib import Path

import bpy
import gpu
from bpy.props import EnumProperty, StringProperty
from gpu_extras.batch import batch_for_shader

from .. import zone as zmod
from ..passes import picture_mask as pm
from ..passes import segment as seg

TARGET: dict = {"tree": "", "node": ""}          # the Picture Mask node the Image Editor tool edits


def _node(tree_name: str, node_name: str):
    t = bpy.data.node_groups.get(tree_name)
    return t.nodes.get(node_name) if t is not None else None


def target_node():
    return _node(TARGET["tree"], TARGET["node"])


def _settings(node):
    return node.grow / 100.0, node.feather / 100.0, node.invert


def make_mask(node) -> Path:
    """The inpaint input of `node` (its picture with the zone transparent). RuntimeError when it cannot."""
    pic = Path(node.picture) if node.picture else None
    if pic is None or not pic.is_file():
        raise RuntimeError("No picture yet: link one into Image and press Make Mask")
    out = seg.work_dir("picture_mask") / f"mask_{time.strftime('%Y%m%d_%H%M%S')}_{abs(hash(node.name)) % 10000}.png"
    path = pm.compose(pic, node.found(), zmod.loads(node.shapes), *_settings(node), out)
    refresh_preview(node)
    return path


def _preview_name(node) -> str:
    return f"ComfyQ picture mask · {node.name}"


def refresh_preview(node) -> None:
    """Redraw the picture + zone preview (node thumbnail and the Image Editor the tool works in)."""
    pic = Path(node.picture) if node.picture else None
    if pic is None or not pic.is_file():
        return
    out = seg.work_dir("picture_mask") / f"preview_{abs(hash((node.id_data.name, node.name))) % 100000}.png"
    try:
        pm.preview(pic, node.found(), zmod.loads(node.shapes), *_settings(node), out)
    except RuntimeError:
        return
    if node.preview != str(out):
        node.preview = str(out)
    img = bpy.data.images.get(_preview_name(node))
    if img is None:
        img = bpy.data.images.load(str(out), check_existing=False)
        img.name = _preview_name(node)
    else:
        img.filepath = str(out)
        img.reload()
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type in ("IMAGE_EDITOR", "NODE_EDITOR"):
                area.tag_redraw()


def _picture_now(node) -> Path | None:
    """The picture linked into the node, without a run, when it already exists (an image, a result)."""
    from ..passes import render
    from .tree import RUN_NODES, upstream
    up, _out = upstream(node.inputs["Image"])
    if up is None:
        return None
    if up.bl_idname == "ComfyQImageNode" and up.image is not None:
        return render.image_pass(up.image, None)
    if up.bl_idname in RUN_NODES and up.result and Path(up.result).is_file():
        return Path(up.result)
    if up.bl_idname in ("ComfyQImageFileNode",) and getattr(up, "filepath", ""):
        p = Path(bpy.path.abspath(up.filepath))
        return p if p.is_file() else None
    return None


def _image_editor(context):
    """The Image Editor to edit in: the ComfyQ viewer, else any Image Editor not painting / masking."""
    from ..ui import player
    area = player.viewer_area()
    if area is not None:
        return area
    for win in context.window_manager.windows:
        for a in win.screen.areas:
            if a.type == "IMAGE_EDITOR" and getattr(a.spaces.active, "mode", "VIEW") == "VIEW":
                return a
    return None


# -- operators ------------------------------------------------------------------------------------------------------
class _NodeOp:
    tree: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    node: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def get(self):
        return _node(self.tree, self.node) if self.tree else target_node()


class COMFYQ_OT_picture_mask_pick(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.picture_mask_pick"
    bl_label = "Tick Object"
    bl_description = "Tick or untick this found object: ticked objects are repainted"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    label: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        import json
        node = self.get()
        if node is None:
            return {"CANCELLED"}
        regions = node.found()
        state = zmod.toggle(regions, self.label)
        if state is None:
            self.report({"WARNING"}, f"“{self.label}” was not found on this picture")
            return {"CANCELLED"}
        node.regions = json.dumps(regions)
        refresh_preview(node)
        self.report({"INFO"}, f"{self.label}: {'repainted' if state else 'kept'}")
        return {"FINISHED"}


class COMFYQ_OT_picture_mask_clear_shapes(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.picture_mask_clear_shapes"
    bl_label = "Clear Drawn Shapes"
    bl_description = "Remove the shapes drawn on the picture (the found objects stay)"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        node = self.get()
        if node is None:
            return {"CANCELLED"}
        node.shapes = ""
        refresh_preview(node)
        self.report({"INFO"}, "Drawn shapes cleared")
        return {"FINISHED"}


class COMFYQ_OT_picture_mask_add_shape(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.picture_mask_add_shape"
    bl_label = "Add Shape on the Picture"
    bl_description = "Add a shape (picture coordinates, JSON points) to the Picture Mask's zone"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    points: StringProperty(options={"SKIP_SAVE"})
    op: EnumProperty(items=(("ADD", "Add", ""), ("SUB", "Cut", "")), default="ADD")

    def execute(self, context):
        import json
        node = self.get()
        if node is None:
            return {"CANCELLED"}
        try:
            pts = [(float(x), float(y)) for x, y in json.loads(self.points)]
        except (ValueError, TypeError):
            self.report({"ERROR"}, "Points must be a JSON list of [x, y] pairs between 0 and 1")
            return {"CANCELLED"}
        shape = zmod.lasso(pts, "sub" if self.op == "SUB" else "add")
        if shape is None:
            return {"CANCELLED"}
        _add_shape(node, shape)
        return {"FINISHED"}


def _add_shape(node, shape) -> None:
    z = zmod.loads(node.shapes)
    z.shapes.append(shape)
    node.shapes = zmod.dumps(z)
    refresh_preview(node)


class COMFYQ_OT_picture_mask_edit(_NodeOp, bpy.types.Operator):
    bl_idname = "comfyq.picture_mask_edit"
    bl_label = "Edit on Picture"
    bl_description = ("Show the picture with its zone in red in an Image Editor and turn on the Picture Mask tool: "
                      "click found objects, drag a box, Shift+drag a free shape, Ctrl cuts")
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        node = self.get()
        if node is None:
            return {"CANCELLED"}
        if not (node.picture and Path(node.picture).is_file()):
            pic = _picture_now(node)
            if pic is None:
                self.report({"WARNING"}, "No picture yet: link one into Image, or press Make Mask once")
                return {"CANCELLED"}
            node.picture = str(pic)
        TARGET.update(tree=node.id_data.name, node=node.name)
        refresh_preview(node)
        area = _image_editor(context)
        img = bpy.data.images.get(_preview_name(node))
        if area is None or img is None:
            self.report({"WARNING"}, "Open an Image Editor (or the ComfyQ window) to draw on the picture")
            return {"CANCELLED"}
        from ..ui import player
        player.show_media(area, img)
        win = next(w for w in context.window_manager.windows if any(a == area for a in w.screen.areas))
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(window=win, area=area, region=region):
            try:
                bpy.ops.wm.tool_set_by_id(name=PictureMaskTool.bl_idname, space_type="IMAGE_EDITOR")
            except RuntimeError as e:
                print("ComfyQ Picture Mask tool:", e)
        self.report({"INFO"}, "Picture Mask: click objects, drag a box, Shift+drag a free shape (Ctrl cuts)")
        return {"FINISHED"}


def _uv(context, x: int, y: int) -> tuple[float, float]:
    return tuple(context.region.view2d.region_to_view(x, y))


class COMFYQ_OT_picture_mask_click(bpy.types.Operator):
    bl_idname = "comfyq.picture_mask_click"
    bl_label = "Tick Object on the Picture"
    bl_description = "Tick or untick the found object under the mouse"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def invoke(self, context, event):
        node = target_node()
        if node is None or not node.picture:
            self.report({"WARNING"}, "Press Edit on Picture on a Picture Mask node first")
            return {"CANCELLED"}
        u, v = _uv(context, event.mouse_region_x, event.mouse_region_y)
        w, h = _picture_size(node)
        label = pm.region_at(node.found(), u, v, w, h)
        if label is None:
            self.report({"INFO"}, "No found object here: drag to draw a zone, or add its name to Find")
            return {"CANCELLED"}
        return bpy.ops.comfyq.picture_mask_pick(tree=node.id_data.name, node=node.name, label=label)


def _picture_size(node) -> tuple[int, int]:
    img = bpy.data.images.get(_preview_name(node))
    if img is not None and img.size[0]:
        return int(img.size[0]), int(img.size[1])
    return 1024, 1024


class COMFYQ_OT_picture_mask_draw(bpy.types.Operator):
    bl_idname = "comfyq.picture_mask_draw"
    bl_label = "Draw on the Picture"
    bl_description = "Draw a box or a free shape on the picture: added to the zone to repaint, or cut out of it"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    shape: EnumProperty(items=(("BOX", "Box", ""), ("LASSO", "Free Shape", "")), default="BOX")
    op: EnumProperty(items=(("ADD", "Add", ""), ("SUB", "Cut", "")), default="ADD")

    def invoke(self, context, event):
        if target_node() is None:
            self.report({"WARNING"}, "Press Edit on Picture on a Picture Mask node first")
            return {"CANCELLED"}
        self._path = [(event.mouse_region_x, event.mouse_region_y)]
        self._handle = bpy.types.SpaceImageEditor.draw_handler_add(self._draw, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _draw(self):
        pts = self._path
        if len(pts) < 2:
            return
        if self.shape == "BOX":
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        sh = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(2.0)
        b = batch_for_shader(sh, "LINE_STRIP", {"pos": [*pts, pts[0]]})
        sh.bind()
        sh.uniform_float("color", (1.0, 0.25, 0.2, 0.9) if self.op == "ADD" else (0.1, 0.1, 0.1, 0.9))
        b.draw(sh)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type == "MOUSEMOVE":
            p = (event.mouse_region_x, event.mouse_region_y)
            if self.shape == "LASSO" or len(self._path) == 1:
                self._path.append(p)
            else:
                self._path[-1] = p
            return {"RUNNING_MODAL"}
        if event.type in {"RIGHTMOUSE", "ESC"}:
            self._end()
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._end()
            node = target_node()
            if node is None:
                return {"CANCELLED"}
            path = [_uv(context, x, y) for x, y in self._path]
            op = "sub" if self.op == "SUB" else "add"
            shape = zmod.box(path[0], path[-1], op) if self.shape == "BOX" else zmod.lasso(path, op)
            if shape is None:
                return {"CANCELLED"}
            _add_shape(node, shape)
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _end(self):
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(self._handle, "WINDOW")
            self._handle = None


class PictureMaskTool(bpy.types.WorkSpaceTool):
    bl_space_type = "IMAGE_EDITOR"
    bl_context_mode = "VIEW"
    bl_idname = "comfyq.picture_mask"
    bl_label = "ComfyQ Picture Mask"
    bl_description = ("Pick what an AI inpaint repaints on this picture: click a found object, drag a box, "
                      "Shift+drag a free shape; Ctrl cuts")
    bl_icon = "ops.generic.select_lasso"
    bl_keymap = (
        ("comfyq.picture_mask_click", {"type": "LEFTMOUSE", "value": "CLICK"}, None),
        ("comfyq.picture_mask_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG"},
         {"properties": [("shape", "BOX"), ("op", "ADD")]}),
        ("comfyq.picture_mask_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG", "shift": True},
         {"properties": [("shape", "LASSO"), ("op", "ADD")]}),
        ("comfyq.picture_mask_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG", "ctrl": True},
         {"properties": [("shape", "BOX"), ("op", "SUB")]}),
        ("comfyq.picture_mask_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG", "ctrl": True, "shift": True},
         {"properties": [("shape", "LASSO"), ("op", "SUB")]}),
    )

    def draw_settings(context, layout, tool):
        node = target_node()
        if node is None:
            layout.label(text="Press Edit on Picture on a Picture Mask node", icon="INFO")
            return
        picked = sum(1 for r in node.found() if r.get("picked"))
        layout.label(text=f"{node.draw_label()}: {zmod.describe(picked, zmod.loads(node.shapes))}", icon="SELECT_SET")
        layout.prop(node, "grow")
        layout.prop(node, "feather")
        layout.prop(node, "invert")
        op = layout.operator("comfyq.picture_mask_clear_shapes", text="", icon="TRASH")
        op.tree, op.node = node.id_data.name, node.name


classes = (COMFYQ_OT_picture_mask_pick, COMFYQ_OT_picture_mask_clear_shapes, COMFYQ_OT_picture_mask_add_shape,
           COMFYQ_OT_picture_mask_edit, COMFYQ_OT_picture_mask_click, COMFYQ_OT_picture_mask_draw)


_REGISTERED: list = []


def register() -> None:
    """Idempotent: register_extras may run twice (a failed reload); a second call does nothing."""
    if _REGISTERED:
        return
    for c in classes:
        bpy.utils.register_class(c)
    if not bpy.app.background:
        bpy.utils.register_tool(PictureMaskTool, separator=True, group=False)
    _REGISTERED.append(True)


def unregister() -> None:
    if not _REGISTERED:
        return
    if not bpy.app.background:
        try:
            bpy.utils.unregister_tool(PictureMaskTool)
        except (ValueError, RuntimeError):
            pass
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    _REGISTERED.clear()
