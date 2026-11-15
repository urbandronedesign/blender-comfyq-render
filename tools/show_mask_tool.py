# SPDX-License-Identifier: GPL-3.0-or-later
"""Open a scene with the ComfyQ Mask tool active, looking through the camera, the zone in red (a demo for the user).

    blender <scene.blend> --python tools/show_mask_tool.py
"""
import bpy


def _setup():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height, default=None)
    if area is None:
        return 0.5
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None or region.width < 50:
        return 0.2                                 # not laid out yet
    try:
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.comfyq.zone_tool()
        sp = area.spaces.active
        sp.region_3d.view_perspective = "CAMERA"
        sp.show_region_ui = True                   # the sidebar: the ComfyQ bar shows the zone
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.view3d.view_center_camera()
    except (AttributeError, RuntimeError) as e:   # the extension is still registering
        print("show_mask_tool:", e)
        return 0.5
    area.tag_redraw()
    return None


bpy.app.timers.register(_setup, first_interval=1.0)
