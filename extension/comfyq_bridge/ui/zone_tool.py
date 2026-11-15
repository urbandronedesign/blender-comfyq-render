# SPDX-License-Identifier: GPL-3.0-or-later
"""The ComfyQ Mask tool (3D View toolbar, Object Mode): pick the zone an inpaint workflow repaints.

    click            add / remove the object under the mouse
    drag             draw a box             Shift+drag  a free shape (lasso)
    Ctrl+drag        cut a box out          Ctrl+Shift+drag  cut a free shape out

Shapes are drawn through the scene camera (the tool switches to the camera view when needed) and stored in frame
coordinates (`zone.py`); the zone shows in red over the 3D View. The tool's header has Grow, Feather, Invert, Show
and Clear. The mask pass is `passes/zone_scene.mask`.
"""
from __future__ import annotations

import bpy
import gpu
from bpy.props import EnumProperty, StringProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils.geometry import tessellate_polygon

from .. import zone as zmod
from ..passes import zone_scene

RED = (0.95, 0.15, 0.1, 0.35)
RED_LINE = (1.0, 0.25, 0.2, 0.9)
CUT_LINE = (0.1, 0.1, 0.1, 0.9)


# -- where the camera frame is drawn in a 3D View region --------------------------------------------------------
def camera_view(context) -> bool:
    rv3d = getattr(context.space_data, "region_3d", None) if context.space_data else None
    return rv3d is not None and rv3d.view_perspective == "CAMERA"


def frame_rect(context, region=None, rv3d=None):
    """(min, max) of the scene camera's frame in region pixels, or None (no camera)."""
    scene = context.scene
    cam = scene.camera
    region = region or context.region
    rv3d = rv3d or getattr(context.space_data, "region_3d", None)
    if cam is None or region is None or rv3d is None:
        return None
    pts = [view3d_utils.location_3d_to_region_2d(region, rv3d, cam.matrix_world @ v)
           for v in cam.data.view_frame(scene=scene)]
    if any(p is None for p in pts):
        return None
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    return (min(xs), min(ys)), (max(xs), max(ys))


def _need_camera(op, context) -> bool:
    """Shapes are drawn through the scene camera: switch to it (once) when the view is elsewhere."""
    if context.scene.camera is None:
        op.report({"ERROR"}, "The scene has no camera: add one and make it the active camera")
        return True
    if not camera_view(context):
        context.space_data.region_3d.view_perspective = "CAMERA"
        context.area.tag_redraw()
        op.report({"INFO"}, "Switched to the camera view: the zone is drawn through the scene camera. Draw again")
        return True
    return False


# -- operators ------------------------------------------------------------------------------------------------------
class COMFYQ_OT_zone_pick(bpy.types.Operator):
    bl_idname = "comfyq.zone_pick"
    bl_label = "Pick Object for the Zone"
    bl_description = "Add the object under the mouse to the zone to repaint, or take it out"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    object_name: StringProperty(options={"SKIP_SAVE"})       # scripted use (tests, agents)

    def invoke(self, context, event):
        region, rv3d = context.region, context.space_data.region_3d
        co = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
        hit, _loc, _n, _i, obj, _m = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
        if not hit or obj is None:
            self.report({"INFO"}, "Nothing under the mouse: click an object, or drag to draw a zone")
            return {"CANCELLED"}
        self.object_name = obj.original.name if hasattr(obj, "original") else obj.name
        return self.execute(context)

    def execute(self, context):
        obj = context.scene.objects.get(self.object_name)
        if obj is None:
            self.report({"WARNING"}, f"No object “{self.object_name}” in this scene")
            return {"CANCELLED"}
        now_in = zone_scene.toggle_object(context.scene, obj)
        self.report({"INFO"}, f"{obj.name} {'added to' if now_in else 'taken out of'} the zone · "
                              f"{zone_scene.describe(context.scene)}")
        _redraw(context)
        return {"FINISHED"}


class COMFYQ_OT_zone_draw(bpy.types.Operator):
    bl_idname = "comfyq.zone_draw"
    bl_label = "Draw Zone"
    bl_description = "Draw a box or a free shape through the camera: added to the zone to repaint, or cut out of it"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    shape: EnumProperty(items=(("BOX", "Box", ""), ("LASSO", "Free Shape", "")), default="BOX")
    op: EnumProperty(items=(("ADD", "Add", ""), ("SUB", "Cut", "")), default="ADD")

    def invoke(self, context, event):
        if _need_camera(self, context):
            return {"CANCELLED"}
        self._path = [(event.mouse_region_x, event.mouse_region_y)]
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _draw(self):
        pts = self._path
        if len(pts) < 2:
            return
        if self.shape == "BOX":
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        _poly_outline(pts, RED_LINE if self.op == "ADD" else CUT_LINE)

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
            rect = frame_rect(context)
            if rect is None:
                self.report({"ERROR"}, "The scene camera's frame is not in view")
                return {"CANCELLED"}
            path = [zmod.to_frame(x, y, *rect) for x, y in self._path]
            op = "sub" if self.op == "SUB" else "add"
            shape = zmod.box(path[0], path[-1], op) if self.shape == "BOX" else zmod.lasso(path, op)
            if shape is None:
                return {"CANCELLED"}
            zone_scene.add_shape(context.scene, shape)
            self.report({"INFO"}, f"Zone: {zone_scene.describe(context.scene)}")
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _end(self):
        if getattr(self, "_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
            self._handle = None


class COMFYQ_OT_zone_add_shape(bpy.types.Operator):
    bl_idname = "comfyq.zone_add_shape"
    bl_label = "Add Zone Shape"
    bl_description = "Add a shape (frame coordinates, as JSON points) to the zone to repaint"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    points: StringProperty(options={"SKIP_SAVE"})              # "[[x, y], ...]" in frame coordinates (0..1)
    op: EnumProperty(items=(("ADD", "Add", ""), ("SUB", "Cut", "")), default="ADD")

    def execute(self, context):
        import json
        try:
            pts = [(float(x), float(y)) for x, y in json.loads(self.points)]
        except (ValueError, TypeError):
            self.report({"ERROR"}, "Points must be a JSON list of [x, y] pairs between 0 and 1")
            return {"CANCELLED"}
        shape = zmod.lasso(pts, "sub" if self.op == "SUB" else "add")
        if shape is None:
            self.report({"ERROR"}, "The shape encloses nothing")
            return {"CANCELLED"}
        zone_scene.add_shape(context.scene, shape)
        _redraw(context)
        return {"FINISHED"}


class COMFYQ_OT_zone_clear(bpy.types.Operator):
    bl_idname = "comfyq.zone_clear"
    bl_label = "Clear Zone"
    bl_description = "Remove every picked object and drawn shape from the zone (the objects stay in the scene)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not zone_scene.active(context.scene):
            cls.poll_message_set("The zone is already empty")
            return False
        return True

    def execute(self, context):
        zone_scene.clear(context.scene)
        self.report({"INFO"}, "Zone cleared: the Mask uses the selected objects again")
        _redraw(context)
        return {"FINISHED"}


class COMFYQ_OT_zone_tool(bpy.types.Operator):
    bl_idname = "comfyq.zone_tool"
    bl_label = "Mask Tool"
    bl_description = "Pick the zone to repaint in the 3D View: the ComfyQ Mask tool, through the scene camera"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if _view3d(context) is None:
            cls.poll_message_set("Needs a 3D View in this window")
            return False
        return True

    def execute(self, context):
        win, area = _view3d(context)
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(window=win, area=area, region=region):
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.wm.tool_set_by_id(name=ComfyQMaskTool.bl_idname, space_type="VIEW_3D")
            if context.scene.camera is not None:
                area.spaces.active.region_3d.view_perspective = "CAMERA"
        self.report({"INFO"}, "ComfyQ Mask: click objects, drag a box, Shift+drag a free shape (Ctrl cuts)")
        return {"FINISHED"}


def _view3d(context):
    if context.area is not None and context.area.type == "VIEW_3D":
        return context.window, context.area
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                return win, area
    return None


def _redraw(context):
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type in ("VIEW_3D", "NODE_EDITOR", "IMAGE_EDITOR"):
                area.tag_redraw()


# -- the tool -------------------------------------------------------------------------------------------------------
def draw_zone_settings(layout, scene, header: bool = False) -> None:
    cq = scene.comfyq
    if header:
        layout.label(text=f"Zone: {zone_scene.describe(scene)}", icon="SELECT_SET")
        layout.prop(cq, "zone_grow")
        layout.prop(cq, "zone_feather")
        layout.prop(cq, "zone_invert")
        layout.prop(cq, "zone_show", text="", icon="HIDE_OFF" if cq.zone_show else "HIDE_ON")
        layout.operator("comfyq.zone_clear", text="", icon="TRASH")
        return
    col = layout.column(align=True)
    col.label(text=f"Zone: {zone_scene.describe(scene)}", icon="SELECT_SET")
    row = col.row(align=True)
    row.prop(cq, "zone_grow")
    row.prop(cq, "zone_feather")
    row = col.row(align=True)
    row.prop(cq, "zone_invert")
    row.prop(cq, "zone_show")
    row = col.row(align=True)
    row.operator("comfyq.zone_tool", icon="RESTRICT_SELECT_OFF")
    row.operator("comfyq.zone_clear", icon="TRASH")


class ComfyQMaskTool(bpy.types.WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "comfyq.mask_zone"
    bl_label = "ComfyQ Mask"
    bl_description = ("Pick the zone an AI inpaint repaints: click objects, drag a box, Shift+drag a free shape; "
                      "Ctrl cuts. Drawn through the scene camera")
    bl_icon = "ops.generic.select_lasso"
    bl_keymap = (
        ("comfyq.zone_pick", {"type": "LEFTMOUSE", "value": "CLICK"}, None),
        ("comfyq.zone_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG"},
         {"properties": [("shape", "BOX"), ("op", "ADD")]}),
        ("comfyq.zone_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG", "shift": True},
         {"properties": [("shape", "LASSO"), ("op", "ADD")]}),
        ("comfyq.zone_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG", "ctrl": True},
         {"properties": [("shape", "BOX"), ("op", "SUB")]}),
        ("comfyq.zone_draw", {"type": "LEFTMOUSE", "value": "CLICK_DRAG", "ctrl": True, "shift": True},
         {"properties": [("shape", "LASSO"), ("op", "SUB")]}),
    )

    def draw_settings(context, layout, tool):
        draw_zone_settings(layout, context.scene, header=True)


# -- the red overlay ------------------------------------------------------------------------------------------------
_SHADER = None
_MESH_CACHE: dict = {}


def _shader():
    global _SHADER
    if _SHADER is None:
        _SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _SHADER


def _poly_outline(pts, color):
    sh = _shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(2.0)
    loop = [*pts, pts[0]]
    b = batch_for_shader(sh, "LINE_STRIP", {"pos": [(x, y) for x, y in loop]})
    sh.bind()
    sh.uniform_float("color", color)
    b.draw(sh)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set("NONE")


def _poly_fill(pts, color):
    tris = tessellate_polygon([[(x, y, 0.0) for x, y in pts]])
    if not tris:
        return
    sh = _shader()
    gpu.state.blend_set("ALPHA")
    b = batch_for_shader(sh, "TRIS", {"pos": [(x, y) for x, y in pts]}, indices=tris)
    sh.bind()
    sh.uniform_float("color", color)
    b.draw(sh)
    gpu.state.blend_set("NONE")


def _draw_shapes():
    ctx = bpy.context
    scene = ctx.scene
    if scene is None or not scene.comfyq.zone_show or not camera_view(ctx):
        return
    z = zone_scene.shapes(scene)
    if not z.shapes:
        return
    rect = frame_rect(ctx)
    if rect is None:
        return
    for s in z.shapes:
        pts = [zmod.to_region(x, y, *rect) for x, y in s.points]
        if s.op == "add":
            _poly_fill(pts, RED)
            _poly_outline(pts, RED_LINE)
        else:
            _poly_outline(pts, CUT_LINE)


def _mesh_batch(obj, depsgraph):
    """The object's evaluated triangles in world space (cached while its shape and place stay the same)."""
    ev = obj.evaluated_get(depsgraph)
    try:
        mesh = ev.to_mesh()
    except RuntimeError:
        return None
    try:
        key = (obj.name, len(mesh.vertices), len(mesh.polygons), tuple(round(v, 4) for r in obj.matrix_world for v in r))
        if key in _MESH_CACHE:
            return _MESH_CACHE[key]
        mesh.calc_loop_triangles()
        mw = obj.matrix_world
        pos = [tuple(mw @ v.co) for v in mesh.vertices]
        idx = [tuple(t.vertices) for t in mesh.loop_triangles]
        b = batch_for_shader(_shader(), "TRIS", {"pos": pos}, indices=idx) if idx else None
        if len(_MESH_CACHE) > 64:
            _MESH_CACHE.clear()
        _MESH_CACHE[key] = b
        return b
    finally:
        ev.to_mesh_clear()


def _draw_objects():
    ctx = bpy.context
    scene = ctx.scene
    if scene is None or not scene.comfyq.zone_show:
        return
    objs = [o for o in zone_scene.objects(scene) if o.type in ("MESH", "CURVE", "SURFACE", "FONT", "META")
            and o.visible_get()]
    if not objs:
        return
    dg = ctx.evaluated_depsgraph_get()
    sh = _shader()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")            # seen through what is in front: the zone is what matters here
    sh.bind()
    sh.uniform_float("color", RED)
    for o in objs:
        b = _mesh_batch(o, dg)
        if b is not None:
            b.draw(sh)
    gpu.state.blend_set("NONE")


_HANDLES: list = []
classes = (COMFYQ_OT_zone_pick, COMFYQ_OT_zone_draw, COMFYQ_OT_zone_add_shape, COMFYQ_OT_zone_clear,
           COMFYQ_OT_zone_tool)


def register() -> None:
    for c in classes:
        bpy.utils.register_class(c)
    if not bpy.app.background:
        bpy.utils.register_tool(ComfyQMaskTool, after={"builtin.select_lasso"}, separator=True, group=False)
    _HANDLES.append(bpy.types.SpaceView3D.draw_handler_add(_draw_objects, (), "WINDOW", "POST_VIEW"))
    _HANDLES.append(bpy.types.SpaceView3D.draw_handler_add(_draw_shapes, (), "WINDOW", "POST_PIXEL"))


def unregister() -> None:
    for h in _HANDLES:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
        except (ValueError, RuntimeError):
            pass
    _HANDLES.clear()
    _MESH_CACHE.clear()
    if not bpy.app.background:
        try:
            bpy.utils.unregister_tool(ComfyQMaskTool)
        except (ValueError, RuntimeError):
            pass
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
