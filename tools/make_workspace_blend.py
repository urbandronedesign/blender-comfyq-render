# SPDX-License-Identifier: GPL-3.0-or-later
"""Author the ComfyQ workspace once, in a VISIBLE Blender (area types do not change in background mode, gotcha 63),
and save it as extension/comfyq_bridge/workspace.blend, which the extension appends (`comfyq.workspace`):

    ┌──────────────────────────────┬──────────────────────────┐
    │ 3D Viewport (camera view)    │ Image Editor: the FRAME  │
    │  sidebar = the ComfyQ bar    │  view / paint, sidebar   │
    ├──────────────────────────────┴──────────────┬───────────┤
    │ Node Editor: the ComfyQ graph               │ Timeline  │
    └─────────────────────────────────────────────┴───────────┘

    blender --factory-startup --python tools/make_workspace_blend.py -- <out.blend> [<screenshot.png>]

Factory startup: the Layout workspace is the starting point (renamed ComfyQ; the other factory workspaces stay in
the file, unused), the scene is emptied. What
the editors SHOW (camera view, the graph, the ComfyQ tabs) is set by the extension when the workspace opens.
Each layout change waits for a draw (gotcha 26: split areas keep stale positions until then).
"""
import sys
from pathlib import Path

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(ARGS[0]).resolve()
SHOT = Path(ARGS[1]).resolve() if len(ARGS) > 1 else None
NAME = "ComfyQ"
state = {"step": 0, "tries": 0, "split": None}


def win():
    return bpy.context.window_manager.windows[0]


def areas():
    return list(win().screen.areas)


def over(area, **kw):
    region = next(r for r in area.regions if r.type == "WINDOW")
    return bpy.context.temp_override(window=win(), screen=win().screen, area=area, region=region, **kw)


def split(area, direction, factor):
    """Split, then wait for the layout; returns the new pair on a later tick (see step())."""
    before = {a.as_pointer() for a in areas()}
    with over(area):
        bpy.ops.screen.area_split(direction=direction, factor=factor)
    new = next(a for a in areas() if a.as_pointer() not in before)
    state["split"] = (area.as_pointer(), new.as_pointer(), direction)


def pair():
    """The two areas of the last split once laid out: (first, second) = (left, right) or (top, bottom)."""
    a_ptr, b_ptr, direction = state["split"]
    a = next(x for x in areas() if x.as_pointer() == a_ptr)
    b = next(x for x in areas() if x.as_pointer() == b_ptr)
    if (direction == "VERTICAL" and a.x == b.x) or (direction == "HORIZONTAL" and a.y == b.y):
        return None
    if direction == "VERTICAL":
        return (a, b) if a.x < b.x else (b, a)
    return (a, b) if a.y > b.y else (b, a)


def step():
    s = state["step"]
    state["tries"] += 1
    if state["tries"] > 400:
        print("WORKSPACE FAILED at step", s, flush=True)
        bpy.ops.wm.quit_blender()
        return None
    w = win()
    if s == 0:                                   # one workspace, renamed
        if not areas() or max(a.width for a in areas()) < 50:
            return 0.1
        # the other factory workspaces stay in the file: the extension appends only this one, by name
        # (workspace.delete under temp_override deleted the ACTIVE workspace, not the one passed)
        keep = w.workspace
        keep.name = NAME
        keep["comfyq_workspace"] = 1
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob)
        state["step"] = 1
        return 0.2
    if s == 1:                                   # only the 3D Viewport stays
        v3d = max((a for a in areas() if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
        others = [a for a in areas() if a != v3d]
        if others:
            with over(others[0]):
                bpy.ops.screen.area_close()
            return 0.2
        split(v3d, "HORIZONTAL", 0.36)
        state["step"] = 2
        return 0.2
    if s == 2:                                   # bottom = graph; then the top splits for the frame
        p = pair()
        if p is None:
            return 0.1
        top, bottom = p
        bottom.type = "NODE_EDITOR"
        split(top, "VERTICAL", 0.58)
        state["step"] = 3
        return 0.2
    if s == 3:                                   # top right = the frame; then the bottom splits for the timeline
        p = pair()
        if p is None:
            return 0.1
        _left, right = p
        right.type = "IMAGE_EDITOR"
        right.spaces.active.show_region_ui = True
        node = next(a for a in areas() if a.type == "NODE_EDITOR")
        split(node, "VERTICAL", 0.8)
        state["step"] = 4
        return 0.2
    if s == 4:
        p = pair()
        if p is None:
            return 0.1
        _graph, tl = p
        tl.type = "DOPESHEET_EDITOR"
        tl.ui_type = "TIMELINE"
        for a in areas():
            if a.type == "VIEW_3D":
                a.spaces.active.show_region_ui = True
                a.spaces.active.region_3d.view_perspective = "CAMERA"
            a.tag_redraw()
        state["step"] = 5
        return 0.5
    if s == 5:
        if SHOT is not None:
            bpy.ops.screen.screenshot(filepath=str(SHOT))
        print("WORKSPACE AREAS", sorted((a.type, a.ui_type, a.x, a.y, a.width, a.height) for a in areas()), flush=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT), compress=True, copy=True)
        print("WORKSPACE SAVED", OUT, OUT.stat().st_size, flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(step, first_interval=1.0)
