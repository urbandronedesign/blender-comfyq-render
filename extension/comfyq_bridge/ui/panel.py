# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the generation bar appears: View3D sidebar, a floating dialog, the header button, the Queue popover."""
from __future__ import annotations

import bpy

from ..ops.generate import generate
from ..prefs import prefs
from ..runtime.session import SESSION
from .studio import draw_jobs, draw_studio


def _columns(context) -> int:
    """Chip columns from the real width: 1 in a narrow sidebar, up to 6 in the ComfyQ window."""
    width = context.region.width if context.region else 300
    scale = context.preferences.view.ui_scale or 1.0
    return max(1, min(6, int(width / scale) // 200))


class _Main:
    bl_region_type = "UI"
    bl_category = "ComfyQ"
    bl_label = "ComfyQ"

    def draw_header_preset(self, context):
        if SESSION.client is not None:
            row = self.layout.row(align=True)
            row.operator("comfyq.refresh", text="", icon="FILE_REFRESH", emboss=False)
            row.operator("comfyq.disconnect", text="", icon="UNLINKED", emboss=False)

    def draw(self, context):
        draw_studio(self.layout, context, columns=_columns(context))


class _Jobs:
    bl_region_type = "UI"
    bl_category = "ComfyQ"
    bl_label = "Jobs"

    @classmethod
    def poll(cls, context):
        return len(context.scene.comfyq.jobs) > 0

    def draw(self, context):
        draw_jobs(self.layout, context)
        row = self.layout.row()
        row.prop(context.scene.comfyq, "show_jobs")
        row.prop(context.scene.comfyq, "auto_import")


class COMFYQ_PT_main(_Main, bpy.types.Panel):
    bl_space_type = "VIEW_3D"


class COMFYQ_PT_jobs(_Jobs, bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_parent_id = "COMFYQ_PT_main"


class COMFYQ_PT_image_main(_Main, bpy.types.Panel):
    """Same bar in the Image Editor sidebar: what the detached ComfyQ window shows (below the Player)."""
    bl_space_type = "IMAGE_EDITOR"
    bl_order = 1


class COMFYQ_PT_image_jobs(_Jobs, bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_parent_id = "COMFYQ_PT_image_main"



class COMFYQ_PT_queue(bpy.types.Panel):
    """Popover behind the bar's Queue button."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = "Queue"
    bl_ui_units_x = 18

    def draw(self, context):
        draw_jobs(self.layout, context, limit=8)


class COMFYQ_OT_studio(bpy.types.Operator):
    bl_idname = "comfyq.studio"
    bl_label = "ComfyQ"
    bl_description = "Open the ComfyQ generation bar (Ctrl+Shift+Q)"

    def invoke(self, context, event):
        if SESSION.client is None and bpy.app.online_access:
            p = prefs(context)
            SESSION.connect(p.profile, p.student_name.strip(), p.url.strip())
        return context.window_manager.invoke_props_dialog(self, width=880, title="ComfyQ",
                                                          confirm_text="Generate")

    def check(self, context):
        return True          # keep redrawing: job progress and async loads change what is shown

    def draw(self, context):
        draw_studio(self.layout, context, columns=4, dialog=True)

    def execute(self, context):
        from ..ops.generate import why_not_generate
        why = why_not_generate(context.scene, context.scene.comfyq.workflow_id)
        if why:
            self.report({"WARNING"}, why)
            return {"CANCELLED"}
        return {"FINISHED"} if generate(context, self.report) else {"CANCELLED"}


def _header_button(self, context):
    row = self.layout.row(align=True)
    row.operator(COMFYQ_OT_studio.bl_idname, text="ComfyQ", icon="RENDER_STILL")
    row.operator("comfyq.open_window", text="", icon="WINDOW")


classes = (COMFYQ_PT_main, COMFYQ_PT_jobs, COMFYQ_PT_image_main, COMFYQ_PT_image_jobs, COMFYQ_PT_queue,
           COMFYQ_OT_studio)
_keymaps: list = []


def register_extras() -> None:
    bpy.types.VIEW3D_HT_header.append(_header_button)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:                      # None in background mode
        # Ctrl+Shift+Q ("Q" for ComfyQ): free in Blender and Mixar. Ctrl+Shift+G is taken by Object
        # Mode's "Add to active collection", which runs before the 3D View keymap.
        for name, space in (("3D View", "VIEW_3D"), ("Image", "IMAGE_EDITOR")):
            km = kc.keymaps.new(name=name, space_type=space)
            kmi = km.keymap_items.new(COMFYQ_OT_studio.bl_idname, "Q", "PRESS", ctrl=True, shift=True)
            _keymaps.append((km, kmi))


def unregister_extras() -> None:
    bpy.types.VIEW3D_HT_header.remove(_header_button)
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
