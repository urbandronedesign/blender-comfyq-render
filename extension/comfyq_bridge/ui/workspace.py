# SPDX-License-Identifier: GPL-3.0-or-later
"""The ComfyQ workspace (audit 2, phase 3; docs/ux-audit-2-plan.md §3.1): a workspace tab appended from
workspace.blend (authored once in a visible Blender by tools/make_workspace_blend.py, gotcha 63):

    ┌──────────────────────────────┬──────────────────────────┐
    │ 3D Viewport (camera view)    │ Image Editor: the FRAME  │
    │  sidebar = the ComfyQ bar    │  view / paint, the bar   │
    ├──────────────────────────────┴──────────────┬───────────┤
    │ Node Editor: the ComfyQ graph               │ Timeline  │
    └─────────────────────────────────────────────┴───────────┘

and the bar's mode strip Frame · Draw · Mask · Graph, each switching the editors in one click. The mode is read
from the editors themselves (no stored state, nothing written while drawing).
"""
from __future__ import annotations

from pathlib import Path

import bpy

NAME = "ComfyQ"
MARK = "comfyq_workspace"
BLEND = Path(__file__).resolve().parents[1] / "workspace.blend"
MODES = (
    ("FRAME", "Frame", "Show the frame: the latest render or result, the scene seen through its camera",
     "IMAGE_DATA"),
    ("DRAW", "Draw", ("Paint on the frame in the Image Editor (a white canvas when there is no frame yet), sent "
                      "as a Blender Image input; or sketch in the camera view, sent as a Sketch input"), "TPAINT_HLT"),
    ("MASK", "Mask", "Pick the zone to repaint in the camera view with the ComfyQ Mask tool", "RESTRICT_SELECT_OFF"),
    ("GRAPH", "Graph", "Show the node graph across the whole workspace (Back to Previous, or Graph again, returns)",
     "NODETREE"),
)
HINT = {                     # one line under the strip: short enough for a narrow sidebar (~32 characters)
    "FRAME": "Render or Generate to see it",
    "DRAW": "Paint, then send it as an input",
    "MASK": "Click objects or drag a zone",
    "GRAPH": "Back to Previous to return",
}


def workspace_of(context):
    ws = getattr(context, "workspace", None)
    return ws if ws is not None and (ws.get(MARK) or ws.name == NAME) else None


def _areas(screen, kind):
    return [a for a in screen.areas if a.type == kind]


def _frame_area(screen):
    return next(iter(_areas(screen, "IMAGE_EDITOR")), None)


def _view3d(screen):
    return max(_areas(screen, "VIEW_3D"), key=lambda a: a.width * a.height, default=None)


def _graph_area(screen):
    return next(iter(_areas(screen, "NODE_EDITOR")), None)


def current_mode(context) -> str:
    """The mode the editors are in (read only: safe in draw code)."""
    screen = context.screen
    if screen is None:
        return "FRAME"
    if screen.show_fullscreen and _graph_area(screen) is not None and len(screen.areas) == 1:
        return "GRAPH"
    frame = _frame_area(screen)
    if (frame is not None and frame.spaces.active.mode == "PAINT") or context.mode == "PAINT_GREASE_PENCIL":
        return "DRAW"
    try:
        tool = context.workspace.tools.from_space_view3d_mode("OBJECT", create=False)
    except (AttributeError, TypeError):
        tool = None
    if tool is not None and tool.idname == "comfyq.mask_zone":
        return "MASK"
    return "FRAME"


def setup_soon(ptr: int) -> None:
    """Point the workspace's editors at ComfyQ: camera view, the graph, the ComfyQ tabs, the latest frame. From a
    timer: an appended workspace has no laid-out areas until it is drawn (gotcha 10)."""
    from ..graph.ui import TREE, ensure_graph, show_graph
    from ..ops.workflow import _focus_comfyq_tab, _latest_comfyq_image
    from .player import free_for_results, show_media
    tries = {"n": 0, "graph": False}

    def step():
        win = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == ptr), None)
        tries["n"] += 1
        if win is None or tries["n"] > 150 or workspace_of(win) is None:
            return None
        screen = win.screen
        v3d, frame, graph = _view3d(screen), _frame_area(screen), _graph_area(screen)
        if v3d is None or frame is None or v3d.width < 50:
            return 0.1
        if not tries["graph"]:
            if graph is not None and graph.spaces.active.tree_type != TREE:
                show_graph(graph, ensure_graph())
            sp = v3d.spaces.active
            sp.show_region_ui = True
            if win.scene.camera is not None:
                sp.region_3d.view_perspective = "CAMERA"
            frame.spaces.active.show_region_ui = True
            img = _latest_comfyq_image()
            if img is not None and free_for_results(frame, comfyq_viewer=True):
                show_media(frame, img)
            tries["graph"] = True
        ok = _focus_comfyq_tab(v3d) & _focus_comfyq_tab(frame)      # both tabs, every try (not short-circuit)
        return None if ok else 0.15

    bpy.app.timers.register(step, first_interval=0.1)


class COMFYQ_OT_workspace(bpy.types.Operator):
    bl_idname = "comfyq.workspace"
    bl_label = "ComfyQ Workspace"
    bl_description = ("Switch to the ComfyQ workspace: the camera view, the frame (results, painting) and the node "
                      "graph side by side. Added to this file the first time")

    @classmethod
    def poll(cls, context):
        if context.window is None:
            cls.poll_message_set("Needs a Blender window")
            return False
        return True

    def execute(self, context):
        ws = next((w for w in bpy.data.workspaces if w.get(MARK)), None)
        if ws is not None:
            context.window.workspace = ws
        else:
            if not BLEND.is_file():
                self.report({"ERROR"}, f"The workspace file is missing from the extension ({BLEND.name})")
                return {"CANCELLED"}
            before = set(bpy.data.workspaces)
            bpy.ops.workspace.append_activate(idname=NAME, filepath=str(BLEND))
            new = next(iter(set(bpy.data.workspaces) - before), None)
            if new is None:
                self.report({"ERROR"}, "Could not add the ComfyQ workspace")
                return {"CANCELLED"}
            new[MARK] = 1
        setup_soon(context.window.as_pointer())
        return {"FINISHED"}


class COMFYQ_OT_workspace_mode(bpy.types.Operator):
    bl_idname = "comfyq.workspace_mode"
    bl_label = "ComfyQ Mode"
    bl_options = {"REGISTER"}
    mode: bpy.props.EnumProperty(items=[(m[0], m[1], m[2], m[3], i) for i, m in enumerate(MODES)])

    @classmethod
    def description(cls, context, props):
        return next(m[2] for m in MODES if m[0] == props.mode)

    @classmethod
    def poll(cls, context):
        if workspace_of(context) is None:
            cls.poll_message_set("Only in the ComfyQ workspace")
            return False
        return True

    def execute(self, context):
        mode, screen, win = self.mode, context.screen, context.window
        if (screen.show_fullscreen and mode != "GRAPH") or (mode == "GRAPH" and current_mode(context) == "GRAPH"):
            self._toggle_full(context, screen.areas[0])          # back from the maximized graph
            if mode == "GRAPH":
                self.report({"INFO"}, "Back to the workspace")
                return {"FINISHED"}
            screen = win.screen
        frame, v3d = _frame_area(screen), _view3d(screen)
        if mode == "GRAPH":
            graph = _graph_area(screen)
            if graph is None:
                self.report({"WARNING"}, "This workspace has no Node Editor")
                return {"CANCELLED"}
            self._toggle_full(context, graph)
        elif mode == "MASK":
            self._paint(frame, False)
            bpy.ops.comfyq.zone_tool()
        elif mode == "DRAW":
            if frame is None:
                self.report({"WARNING"}, "This workspace has no Image Editor")
                return {"CANCELLED"}
            self._select_tool(context, v3d)
            from .player import fit_view_soon
            sp = frame.spaces.active
            sp.image = _canvas(context.scene, sp.image)
            sp.ui_mode = "PAINT"
            fit_view_soon(frame)
        else:
            self._paint(frame, False)
            self._select_tool(context, v3d)
            if context.mode == "PAINT_GREASE_PENCIL":          # leave the sketch's Draw mode
                bpy.ops.object.mode_set(mode="OBJECT")
            if v3d is not None and context.scene.camera is not None:
                v3d.spaces.active.region_3d.view_perspective = "CAMERA"
        self.report({"INFO"}, HINT[mode])
        return {"FINISHED"}

    @staticmethod
    def _paint(frame, on: bool) -> None:
        if frame is not None and frame.spaces.active.ui_mode != "VIEW" and not on:
            frame.spaces.active.ui_mode = "VIEW"

    @staticmethod
    def _select_tool(context, v3d) -> None:
        if v3d is None or current_mode(context) != "MASK":
            return
        region = next(r for r in v3d.regions if r.type == "WINDOW")
        with context.temp_override(area=v3d, region=region):
            bpy.ops.wm.tool_set_by_id(name="builtin.select_box", space_type="VIEW_3D")

    @staticmethod
    def _toggle_full(context, area) -> None:
        region = next(r for r in area.regions if r.type == "WINDOW")
        with context.temp_override(area=area, region=region):
            bpy.ops.screen.screen_full_area()


CANVAS = "ComfyQ Frame"


def _canvas(scene, shown) -> bpy.types.Image:
    """The picture Draw paints on. ComfyQ's own pictures (a render preview, a result) are replaced by the next
    render or job, so painting goes on a copy, "ComfyQ Frame" (its pixels, gotcha 33: never image.copy()); a
    picture of the student's own is painted as it is; nothing shown = a white canvas at the camera's shape."""
    from ..passes.render import scene_aspect
    from .player import is_movie
    if shown is not None and shown.name != CANVAS and not (shown.get("comfyq_job") or shown.name.startswith("ComfyQ")):
        return shown
    if shown is not None and shown.name == CANVAS:
        return shown
    copy_from = shown if shown is not None and not is_movie(shown) and shown.size[0] > 0 else None
    if copy_from is not None:
        w, h = copy_from.size
    else:
        aspect = scene_aspect(scene) or 16 / 9
        w, h = (1280, round(1280 / aspect)) if aspect >= 1 else (round(1280 * aspect), 1280)
    old = bpy.data.images.get(CANVAS)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(CANVAS, w, h, alpha=True)
    if copy_from is not None:
        img.pixels.foreach_set(_pixels(copy_from))
        img.update()
    else:
        img.generated_color = (1.0, 1.0, 1.0, 1.0)
    return img


def _pixels(img):
    import numpy as np
    buf = np.empty(img.size[0] * img.size[1] * img.channels, dtype=np.float32)
    img.pixels.foreach_get(buf)
    if img.channels == 4:
        return buf
    out = np.ones((img.size[0] * img.size[1], 4), dtype=np.float32)
    out[:, :img.channels] = buf.reshape(-1, img.channels)
    return out.ravel()


def draw_strip(layout, context) -> None:
    """The mode strip at the top of the bar, in the ComfyQ workspace; elsewhere nothing (the tab button is in
    the bar's tools row)."""
    if workspace_of(context) is None:
        return
    now = current_mode(context)
    col = layout.column(align=True)
    row = col.row(align=True)
    row.scale_y = 1.2
    for m in MODES:
        row.operator(COMFYQ_OT_workspace_mode.bl_idname, text=m[1], icon=m[3], depress=m[0] == now).mode = m[0]
    hint = col.row()
    hint.enabled = False
    hint.label(text=HINT[now])
    if now == "DRAW":                    # the other way to draw: a sketch in the camera view (Grease Pencil)
        row = col.row(align=True)
        row.operator("comfyq.sketch_start", text="Sketch in Camera View", icon="OUTLINER_OB_GREASEPENCIL")


classes = (COMFYQ_OT_workspace, COMFYQ_OT_workspace_mode)
