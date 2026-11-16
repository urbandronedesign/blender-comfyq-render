# SPDX-License-Identifier: GPL-3.0-or-later
"""Sketch operators (audit 2, phase 4): Start Sketch (Grease Pencil Draw mode in the camera view), Render Sketch
(the pass that will be sent), Clear Sketch."""
from __future__ import annotations

import bpy

from ..passes import render, sketch


def _view3d(context):
    win = context.window
    areas = [a for a in win.screen.areas if a.type == "VIEW_3D"] if win is not None else []
    if not areas:
        return None, None
    return win, max(areas, key=lambda a: a.width * a.height)


class COMFYQ_OT_sketch_start(bpy.types.Operator):
    bl_idname = "comfyq.sketch_start"
    bl_label = "Start Sketch"
    bl_description = ("Draw in the camera view with Grease Pencil: the drawing stays in front of the camera and can "
                      "be sent as an image input (Sketch)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.scene.camera is None:
            cls.poll_message_set("The scene needs a camera: the sketch is drawn through it")
            return False
        if _view3d(context)[1] is None:
            cls.poll_message_set("Needs a 3D View in this window")
            return False
        return True

    def execute(self, context):
        scene = context.scene
        try:
            ob = sketch.ensure(scene)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        win, area = _view3d(context)
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(window=win, area=area, region=region):
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for o in context.selected_objects:
                o.select_set(False)
            ob.hide_set(False)
            ob.select_set(True)
            context.view_layer.objects.active = ob
            area.spaces.active.region_3d.view_perspective = "CAMERA"
            bpy.ops.object.mode_set(mode="PAINT_GREASE_PENCIL")
        ts = scene.tool_settings
        ts.gpencil_stroke_placement_view3d = "ORIGIN"      # on the view plane through the sketch, in front of the scene
        try:
            ts.gpencil_sculpt.lock_axis = "VIEW"
        except (AttributeError, TypeError):
            pass
        self.report({"INFO"}, "Draw in the camera view; Tab (Object Mode) when done")
        return {"FINISHED"}


class COMFYQ_OT_sketch_preview(bpy.types.Operator):
    bl_idname = "comfyq.sketch_preview"
    bl_label = "Render Sketch"
    bl_description = "Render the sketch as it will be sent (over the clay scene, or alone on white) and show it"
    only: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    frame: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    tag: bpy.props.StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})   # "<tree>/<node>": its thumbnail

    @classmethod
    def poll(cls, context):
        if not sketch.objects(context.scene):
            cls.poll_message_set("No sketch yet: press Start Sketch first")
            return False
        return True

    def execute(self, context):
        from .workflow import show_preview
        try:
            path = render.sketch_pass(context.scene, self.only, None, self.frame if self.frame >= 0 else None)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        img = show_preview(path, name=PREVIEW)
        if self.tag:
            from ..graph.tree import set_node_preview, tagged_node
            node = tagged_node(self.tag)
            if node is not None:
                set_node_preview(node, path)
        self.report({"INFO"}, f"{img.size[0]}×{img.size[1]} sketch ({sketch.describe(context.scene)})")
        return {"FINISHED"}


class COMFYQ_OT_sketch_clear(bpy.types.Operator):
    bl_idname = "comfyq.sketch_clear"
    bl_label = "Delete Sketch"
    bl_description = "Delete the sketch and all its strokes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not sketch.objects(context.scene):
            cls.poll_message_set("There is no sketch")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Delete Sketch?",
                                                     message=f"{sketch.describe(context.scene)} will be deleted",
                                                     confirm_text="Delete", icon="WARNING")

    def execute(self, context):
        if context.mode != "OBJECT" and context.active_object is not None and context.active_object.get(sketch.MARK):
            bpy.ops.object.mode_set(mode="OBJECT")
        sketch.remove(context.scene)
        self.report({"INFO"}, "Sketch deleted")
        return {"FINISHED"}


PREVIEW = "ComfyQ sketch preview"


def draw_sketch_source(col, context, only_owner, only_prop: str = "sketch_only", frame: int = -1,
                       tag: str = "") -> None:
    """What a Sketch input sends, and its buttons (bar and graph node)."""
    scene = context.scene
    has = bool(sketch.objects(scene))
    row = col.row()
    row.alert = has and not sketch.has_strokes(scene)
    row.label(text=sketch.describe(scene), icon="OUTLINER_OB_GREASEPENCIL")
    row = col.row(align=True)
    row.operator(COMFYQ_OT_sketch_start.bl_idname, text="Draw" if has else "Start Sketch", icon="OUTLINER_OB_GREASEPENCIL")
    if has:
        op = row.operator(COMFYQ_OT_sketch_preview.bl_idname, text="", icon="RENDER_STILL")
        op.only, op.frame, op.tag = getattr(only_owner, only_prop), frame, tag
        row.operator(COMFYQ_OT_sketch_clear.bl_idname, text="", icon="TRASH")
    col.prop(only_owner, only_prop)


classes = (COMFYQ_OT_sketch_start, COMFYQ_OT_sketch_preview, COMFYQ_OT_sketch_clear)
