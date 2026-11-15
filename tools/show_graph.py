# SPDX-License-Identifier: GPL-3.0-or-later
"""Open a scene with its ComfyQ graph shown (the ComfyQ window, graph mode), every node in view. A demo helper.

    blender <scene.blend> --python tools/show_graph.py
"""
import bpy

STATE = {"opened": False, "tries": 0}


def _graph_area():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == "NODE_EDITOR" and a.spaces.active.tree_type == "ComfyQGraphTree":
                return w, a
    return None, None


def _step():
    STATE["tries"] += 1
    if STATE["tries"] > 60:
        return None
    try:
        if not STATE["opened"]:
            win = bpy.context.window_manager.windows[0]
            area = max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.comfyq.open_graph()
            STATE["opened"] = True
            return 1.0
        w, a = _graph_area()
        if a is None or a.width < 50:
            return 0.5
        tree = next((t for t in bpy.data.node_groups if t.bl_idname == "ComfyQGraphTree"), None)
        if tree is not None and a.spaces.active.node_tree != tree:
            a.spaces.active.node_tree = tree
            return 0.3
        region = next(r for r in a.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=w, area=a, region=region):
            bpy.ops.node.view_all()
        return None
    except (AttributeError, RuntimeError) as e:   # the extension is still registering, the window is laying out
        print("show_graph:", e)
        return 0.5


bpy.app.timers.register(_step, first_interval=1.5)
