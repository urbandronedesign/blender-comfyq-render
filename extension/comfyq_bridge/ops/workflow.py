# SPDX-License-Identifier: GPL-3.0-or-later
"""Choosing workflows, editing parameters, matching them to the scene, previewing passes, prompt text."""
from __future__ import annotations

import os
import random
import subprocess
import sys

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from .. import match as scene_match
from ..client.params import ParamSpec
from ..passes import animation, render
from ..props import DEFAULT_SOURCE, INT_MAX, param_item, tab_of
from ..runtime.session import SESSION

KEYFRAME_TABS = ("IMAGE",)          # workflows offered for AI keyframes


def scene_facts(scene: bpy.types.Scene) -> scene_match.SceneFacts:
    return scene_match.SceneFacts(scene.frame_start, scene.frame_end, scene.frame_current,
                                  animation.scene_fps(scene), render.scene_aspect(scene))


def current_specs(context) -> list[ParamSpec]:
    return SESSION.specs.get(context.scene.comfyq.workflow_id) or []


def vram_badge(wf, budget_gb: float | None) -> tuple[str, bool]:
    """(text, over_budget). Every workflow is listed; the badge only informs."""
    if not wf.calibrated:
        return "VRAM ?", False
    over = budget_gb is not None and wf.vram_peak_gb > budget_gb
    return f"{wf.vram_peak_gb:.0f} GB", over


def shared_budget() -> float | None:
    """VRAM left for ComfyQ when it shares this GPU with Mixar (single-machine profile)."""
    client = SESSION.client
    if client is None or client.profile.gpu_policy != "shared" or not SESSION.gpu.get("vramGb"):
        return None
    return float(SESSION.gpu["vramGb"]) - client.profile.mixar_reserve_gb


def sync_params(scene: bpy.types.Scene, wf_id: str, specs: list[ParamSpec]) -> None:
    """Make the scene's items of `wf_id` match `specs`; other workflows' values are left alone."""
    sync_items(scene.comfyq.params, wf_id, specs)


def sync_items(coll, wf_id: str, specs: list[ParamSpec]) -> None:
    """Same for any collection of ComfyQParamValue (the bar's scene store, or a graph node's)."""
    wanted = {s.key: s for s in specs}
    for idx in reversed(range(len(coll))):
        old = coll[idx]
        if old.workflow_id == wf_id and (old.key not in wanted or old.kind != wanted[old.key].kind):
            coll.remove(idx)
    have = {it.key for it in coll if it.workflow_id == wf_id}
    for s in specs:
        if s.key in have:
            continue
        it = coll.add()
        it.workflow_id, it.key, it.kind = wf_id, s.key, s.kind
        d = s.default
        if s.kind in ("prompt", "text", "enum"):
            it.s = "" if d is None else str(d)
            if s.kind == "enum" and it.s in s.options:
                it.e = it.s
        elif s.kind in ("int", "seed"):
            it.i = max(-INT_MAX, min(INT_MAX, int(d or 0)))
            it.randomize = s.kind == "seed"
        elif s.kind == "float":
            it.f = float(d or 0)
        elif s.kind == "bool":
            it.b = bool(d)
        elif s.is_media:
            it.source = DEFAULT_SOURCE.get(s.kind, "FILE")


def apply_values(scene: bpy.types.Scene, wf_id: str, values: dict) -> None:
    for key, v in values.items():
        it = param_item(scene, key, wf_id)
        if it is None:
            continue
        if it.kind in ("int", "seed"):
            it.i = int(v)
        elif it.kind == "float":
            it.f = float(v)
        elif it.kind == "enum":
            it.s = str(v)
            it.e = str(v)


def select_workflow(scene: bpy.types.Scene, wf_id: str, role: str = "main") -> None:
    """Point the bar (role "main") or the AI-keyframe step (role "keyframe") at a workflow."""
    name = scene.name
    if role == "keyframe":
        scene.comfyq.keyframe_workflow_id = wf_id
    else:
        scene.comfyq.workflow_id = wf_id
        wf = next((w for w in SESSION.workflows if w.id == wf_id), None)
        if wf:
            scene.comfyq.tab = tab_of(wf)

    def loaded(specs):
        sc = bpy.data.scenes.get(name)
        if sc is None:
            return
        sync_params(sc, wf_id, specs)
        if role == "main" and any(s.kind == "video" for s in specs):
            apply_values(sc, wf_id, scene_match.match(specs, scene_facts(sc)).values)

    SESSION.load_specs(wf_id, loaded)


_ENSURE_AFTER: dict[str, float] = {}       # workflow id -> earliest time to try loading it again


def ensure_loaded(scene: bpy.types.Scene) -> None:
    """The scene's chosen workflows have their settings loaded and their fields in the scene.

    Needed whenever the session's specs are missing or newer than the scene: after Connect,
    Refresh, reconnecting, or opening a file. Without it the bar shows "Loading…" forever and
    Generate stays greyed out until the model is chosen again. Called from the timer pump.
    """
    if not SESSION.connected:
        return
    cq = scene.comfyq
    known = {w.id for w in SESSION.workflows}
    for wf_id in {cq.workflow_id, cq.keyframe_workflow_id} - {""}:
        if wf_id not in known:
            continue
        specs = SESSION.specs.get(wf_id)
        if specs is not None:
            have = {(it.key, it.kind) for it in scene.comfyq.params if it.workflow_id == wf_id}
            if not {(s.key, s.kind) for s in specs} <= have:
                sync_params(scene, wf_id, specs)     # keeps the values already in the scene
            continue
        request_specs(wf_id, lambda specs, wf_id=wf_id, name=scene.name: _loaded(name, wf_id, specs))


def request_specs(wf_id: str, done=None) -> None:
    """Load a workflow's parameters once (shared by the bar and the graph); a failed load is
    retried after 10 s, not hammered."""
    import time
    if wf_id in SESSION.specs or not SESSION.connected or SESSION.loading(wf_id):
        return
    if wf_id not in {w.id for w in SESSION.workflows} or time.monotonic() < _ENSURE_AFTER.get(wf_id, 0.0):
        return
    _ENSURE_AFTER[wf_id] = time.monotonic() + 10.0

    def loaded(specs):
        _ENSURE_AFTER.pop(wf_id, None)
        if done:
            done(specs)

    SESSION.load_specs(wf_id, loaded)


def _loaded(scene_name: str, wf_id: str, specs: list[ParamSpec]) -> None:
    sc = bpy.data.scenes.get(scene_name)
    if sc is not None:
        sync_params(sc, wf_id, specs)


class COMFYQ_OT_set_workflow(bpy.types.Operator):
    bl_idname = "comfyq.set_workflow"
    bl_label = "Use Workflow"
    bl_options = {"INTERNAL", "UNDO"}
    workflow_id: StringProperty()
    role: StringProperty(default="main")

    def execute(self, context):
        select_workflow(context.scene, self.workflow_id, self.role)
        return {"FINISHED"}


def _draw_workflow_menu(layout, wfs, role) -> None:
    served = SESSION.served_ids()
    budget = shared_budget()
    if not wfs:
        layout.label(text="No workflow in this category")
    for group in (True, False):
        items = [w for w in wfs if (w.id in served) == group]
        if group and items:
            layout.label(text="Served now")
        elif not group and items and any(w.id in served for w in wfs):
            layout.separator()
            layout.label(text="Not served (ask the teacher)")
        for w in items:
            badge, over = vram_badge(w, budget)
            row = layout.row()
            row.alert = over or w.unavailable
            op = row.operator(COMFYQ_OT_set_workflow.bl_idname, text=f"{w.name}   ·  {badge}",
                              icon="ERROR" if w.unavailable else ("PLAY" if group else "NONE"))
            op.workflow_id, op.role = w.id, role


class COMFYQ_MT_workflows(bpy.types.Menu):
    """Workflows of the current tab; served ones first. Everything is listed."""
    bl_idname = "COMFYQ_MT_workflows"
    bl_label = "Workflows"

    def draw(self, context):
        tab = context.scene.comfyq.tab
        _draw_workflow_menu(self.layout, [w for w in SESSION.workflows if tab_of(w) == tab], "main")


class COMFYQ_MT_keyframe_workflows(bpy.types.Menu):
    """Image workflows that can restyle the scene frame into an AI keyframe."""
    bl_idname = "COMFYQ_MT_keyframe_workflows"
    bl_label = "Keyframe workflows"

    def draw(self, context):
        wfs = [w for w in SESSION.workflows if tab_of(w) in KEYFRAME_TABS]
        _draw_workflow_menu(self.layout, wfs, "keyframe")


class COMFYQ_OT_random_seed(bpy.types.Operator):
    bl_idname = "comfyq.random_seed"
    bl_label = "Random Seed"
    bl_options = {"INTERNAL", "UNDO"}
    key: StringProperty()

    def execute(self, context):
        it = param_item(context.scene, self.key)
        if it:
            it.i = random.randint(0, INT_MAX)
        return {"FINISHED"}


class COMFYQ_OT_match_scene(bpy.types.Operator):
    bl_idname = "comfyq.match_scene"
    bl_label = "Match to Scene"
    bl_description = ("Set the workflow's duration, frame count, fps, size and aspect from the scene's "
                      "frame range, frame rate and camera framing")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not current_specs(context):
            cls.poll_message_set("Connect and choose a model first")
            return False
        return True

    def execute(self, context):
        m = scene_match.match(current_specs(context), scene_facts(context.scene))
        apply_values(context.scene, context.scene.comfyq.workflow_id, m.values)
        self.report({"INFO"}, "Matched: " + (", ".join(m.notes) or "already matching"))
        for w in m.warnings:
            self.report({"WARNING"}, w)
        return {"FINISHED"}


def open_file(path) -> None:
    if sys.platform == "win32":
        # Windows keeps a window opened by another process behind the active one (Explorer opened
        # behind Blender, looking like "nothing happened"). Blender has the focus when the button is
        # pressed, so it may hand the foreground on to whatever opens the file (ASFW_ANY).
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except (AttributeError, OSError):
            pass
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class COMFYQ_OT_preview_pass(bpy.types.Operator):
    bl_idname = "comfyq.preview_pass"
    bl_label = "Capture Viewport"
    bl_description = "Render the still pass that will be sent (scene camera) and show it in an Image Editor"
    mask: BoolProperty(default=False)
    style: StringProperty(default="", options={"SKIP_SAVE"})     # "" = the bar's style
    frame: IntProperty(default=-1, options={"SKIP_SAVE"})        # -1 = the current frame

    def execute(self, context):
        from ..ui import player
        scene = context.scene
        style = self.style or scene.comfyq.style
        frame = self.frame if self.frame >= 0 else None
        try:
            if self.mask:
                path = render.mask_pass(scene, list(context.selected_objects), style, None, frame)
            else:
                path = render.structure_pass(scene, style, None, frame)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        name = "ComfyQ mask preview" if self.mask else "ComfyQ render preview"
        img = bpy.data.images.get(name)
        if img is None:                                  # one preview image, refreshed on every render
            img = bpy.data.images.load(str(path), check_existing=False)
            img.name = name
        else:
            img.filepath = str(path)
            img.reload()
        area = player.viewer_area()
        if area is None:
            open_file(path)
        else:
            player.show_media(area, img)
        self.report({"INFO"}, f"{img.size[0]}×{img.size[1]} {style.lower()} render of “{scene.camera.name}”")
        return {"FINISHED"}


class COMFYQ_OT_preview_animation(bpy.types.Operator):
    bl_idname = "comfyq.preview_animation"
    bl_label = "Render Animation Pass"
    bl_description = ("Render the frame range through the scene camera (the video that will be sent) "
                      "and play it. Generate reuses it while the scene is unchanged")
    force: BoolProperty(default=False, options={"SKIP_SAVE"})
    play: BoolProperty(default=True, options={"SKIP_SAVE"})
    style: StringProperty(default="", options={"SKIP_SAVE"})     # "" = the bar's style

    def execute(self, context):
        scene = context.scene
        edge = max((s.max_input_edge or 0 for s in current_specs(context) if s.kind == "video"), default=0) or None
        try:
            p = animation.animation_pass(scene, self.style or scene.comfyq.style, edge, force=self.force)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Animation pass: {p.frames} frames, {p.seconds:.1f} s, {p.size[0]}×{p.size[1]}")
        if self.play:
            from ..ui import player
            area = player.viewer_area()                  # the ComfyQ window's viewer, else any Image Editor
            if area is None:
                open_file(p.path)                      # no Image Editor open: the system player
            else:
                img = bpy.data.images.get("ComfyQ animation pass")
                if img is None:                          # one preview datablock, reloaded each time
                    img = bpy.data.images.load(str(p.path), check_existing=False)
                    img.name = "ComfyQ animation pass"
                else:
                    img.filepath = str(p.path)
                    img.reload()
                player.forget_meta(img)
                player.show_media(area, img)
                player.play(area, 1)                     # always plays (never a pause toggle)
        return {"FINISHED"}


# -- multi-line prompts -----------------------------------------------------------------
def text_editor_area(window=None):
    windows = [window] if window else list(bpy.context.window_manager.windows)
    for win in windows:
        for area in win.screen.areas:
            if area.type == "TEXT_EDITOR":
                return area
    return None


class COMFYQ_OT_edit_prompt(bpy.types.Operator):
    bl_idname = "comfyq.edit_prompt"
    bl_label = "Edit Prompt"
    bl_description = "Edit this prompt with several lines in a Text Editor (opens the ComfyQ window if needed)"
    bl_options = {"INTERNAL", "UNDO"}
    key: StringProperty()
    workflow_id: StringProperty()

    def execute(self, context):
        it = param_item(context.scene, self.key, self.workflow_id or None)
        if it is None:
            return {"CANCELLED"}
        if it.text is None:
            txt = bpy.data.texts.new(f"ComfyQ prompt · {self.key[:20]}")
            txt.write(it.s)
            it.text = txt
        area = text_editor_area()
        if area is not None:
            _show_text(area, it.text)
        # Otherwise the ComfyQ window gets its Text Editor a moment after it opens and shows the text then.
        elif context.window is not None and not open_comfyq_window(context, it.text.name):
            self.report({"WARNING"}, f"Open a Text Editor to edit “{it.text.name}”")
        return {"FINISHED"}


def _show_text(area, text) -> None:
    sp = area.spaces.active
    sp.text, sp.show_word_wrap, sp.show_line_numbers, sp.show_syntax_highlight = text, True, False, False
    area.tag_redraw()


class COMFYQ_OT_unlink_prompt(bpy.types.Operator):
    bl_idname = "comfyq.unlink_prompt"
    bl_label = "Back to One-Line Prompt"
    bl_description = "Copy the text back into the one-line field and stop using the Text Editor"
    bl_options = {"INTERNAL", "UNDO"}
    key: StringProperty()
    workflow_id: StringProperty()

    def execute(self, context):
        it = param_item(context.scene, self.key, self.workflow_id or None)
        if it is not None and it.text is not None:
            it.s = " ".join(it.text.as_string().split())
            it.text = None
        return {"FINISHED"}


# -- detached window ------------------------------------------------------------------------
# Layout:  ┌──────────────────────┬────────────┐
#          │ Image Editor         │ ComfyQ bar │   passes, keyframes, results (videos play here)
#          │                      │ (sidebar:  │   drag its edge to widen it — the bar goes from
#          ├──────────────────────┴────────────┤   1 to 6 columns, and Blender keeps the width)
#          │ Text Editor: multi-line prompt    │
#          └───────────────────────────────────┘
# Blender offers no full-width host for add-on panels (Properties tabs mix in their own panels),
# so the bar lives in the Image Editor sidebar. A brand-new window has no usable areas until it
# has been drawn, so the split runs from a timer, one step per tick.

_WINDOW = [0]       # pointer of the ComfyQ window (changes when a file is reopened)
_MARK = "comfyq_window"   # custom property on the window's screen: survives saving and reopening the file


def comfyq_window():
    windows = list(bpy.context.window_manager.windows)
    win = next((w for w in windows if w.as_pointer() == _WINDOW[0]), None)
    if win is None:
        win = next((w for w in windows if w.screen.get(_MARK)), None) or _legacy_window(windows)
        if win is not None:
            _WINDOW[0] = win.as_pointer()
    return win


def _legacy_window(windows):
    """A ComfyQ window saved before windows were marked: a second window with just an Image Editor
    above a Text Editor (Mixar's own extra windows never have that pair)."""
    for w in windows[1:]:
        types = sorted(a.type for a in w.screen.areas)
        if types == ["IMAGE_EDITOR", "TEXT_EDITOR"]:
            return w            # not marked here: this also runs while drawing, where data is read-only
    return None


def adopt_saved_window() -> None:
    """After a file loads: recognise its ComfyQ window and show the ComfyQ tab in its sidebar."""
    _WINDOW[0] = 0
    win = comfyq_window()
    if win is not None:
        win.screen[_MARK] = True
        focus_comfyq_tab_soon(win.as_pointer())


def graph_pane(win):
    """The window's Node Editor showing the ComfyQ graph (graph mode), or None (bar mode)."""
    from ..graph.tree import TREE
    if win is None:
        return None
    return next((a for a in win.screen.areas
                 if a.type == "NODE_EDITOR" and a.spaces.active.tree_type == TREE), None)


# Graph mode of the ComfyQ window (plan §8): the viewer area is split, the graph takes the left part;
# the viewer (with the bar in its sidebar) and the prompt editor stay as they are.
#          ┌──────────────────────┬────────────────┐
#          │ Node Editor          │ Viewer + bar   │
#          ├──────────────────────┴────────────────┤
#          │ Text Editor: multi-line prompt        │
#          └───────────────────────────────────────┘
GRAPH_SHARE = 0.62
_SPLITTING: set[int] = set()      # windows whose split waits for its layout


def set_graph_mode(win, on: bool) -> bool:
    """Show (on) or close (off) the graph in the ComfyQ window. True when the window is in that mode
    (or getting there: the split areas get their positions on the next draw, then the larger one
    becomes the graph)."""
    pane = graph_pane(win)
    if not on:
        if pane is not None:
            with bpy.context.temp_override(window=win, screen=win.screen, area=pane):
                bpy.ops.screen.area_close()
        return True
    ptr = win.as_pointer()
    if pane is not None or ptr in _SPLITTING:
        return True
    viewer = next((a for a in win.screen.areas if a.type == "IMAGE_EDITOR"), None)
    if viewer is None or viewer.width < 50:
        return False
    _split(win, viewer, "VERTICAL", GRAPH_SHARE)
    _SPLITTING.add(ptr)
    tries = {"n": 0}

    def assign():
        from ..graph.ui import ensure_graph, show_graph
        w = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == ptr), None)
        tries["n"] += 1
        eds = [a for a in w.screen.areas if a.type == "IMAGE_EDITOR"] if w is not None else []
        if len(eds) < 2:
            _SPLITTING.discard(ptr)
            return None
        if eds[0].x == eds[1].x and tries["n"] < 40:            # not laid out yet
            return 0.05
        _SPLITTING.discard(ptr)
        graph = max(eds, key=lambda a: a.width)        # the viewer + bar keep the narrower part
        show_graph(graph, ensure_graph())
        graph.spaces.active.show_region_ui = False
        focus_comfyq_tab_soon(ptr)
        return None
    bpy.app.timers.register(assign, first_interval=0.05)
    return True


def open_comfyq_window(context, text_name: str = "", graph: bool = False) -> bool:
    before = {w.as_pointer() for w in context.window_manager.windows}
    area = context.area or next((a for a in context.window.screen.areas if a.type == "VIEW_3D"), None)
    if area is None:
        area = context.window.screen.areas[0]
    source_type = area.type
    try:
        with context.temp_override(window=context.window, area=area):
            bpy.ops.wm.window_new()
    except RuntimeError as e:          # e.g. background mode: no window can be opened
        print("ComfyQ window:", e)
        return False
    # Mixar opens windows of its own (the agent bubble), so take the new window that holds the copied area.
    new = [w for w in context.window_manager.windows if w.as_pointer() not in before]
    new = [w for w in new if any(a.type == source_type for a in w.screen.areas)] or new
    if not new:
        return False
    ptr = new[0].as_pointer()
    _WINDOW[0] = ptr
    state = {"tries": 0, "graph": graph}
    bpy.app.timers.register(lambda: _setup_step(ptr, text_name, state), first_interval=0.05)
    return True


def _split(win, area, direction: str, factor: float) -> None:
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        bpy.ops.screen.area_split(direction=direction, factor=factor)


def _focus_comfyq_tab(area) -> bool:
    """True once the sidebar really shows the ComfyQ tab (the setter can be silently ignored)."""
    for region in area.regions:
        if region.type == "UI":
            try:
                region.active_panel_category = "ComfyQ"
                area.tag_redraw()
                return region.active_panel_category == "ComfyQ"
            except (AttributeError, TypeError, ValueError, ReferenceError):
                # "read-only" until the sidebar has been drawn once and knows its tabs: try again
                area.tag_redraw()
                return False
    return False


def _setup_step(ptr: int, text_name: str, state: dict):
    win = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == ptr), None)
    state["tries"] += 1
    if win is None or state["tries"] > 60:
        return None
    areas = list(win.screen.areas)
    try:
        if len(areas) == 1:
            main = areas[0]
            if main.width < 50:                      # not laid out yet
                return 0.05
            main.type = "IMAGE_EDITOR"
            _split(win, main, "HORIZONTAL", 0.3)
            return 0.05
    except RuntimeError as e:                         # context not ready yet: try again next tick
        print("ComfyQ window setup:", e)
        return 0.1
    top, bottom = max(areas, key=lambda a: a.y), min(areas, key=lambda a: a.y)
    bottom.type = "TEXT_EDITOR"
    top.type = "IMAGE_EDITOR"
    top.spaces.active.show_region_ui = True
    img = _latest_comfyq_image()
    if img is not None:
        top.spaces.active.image = img
    sp = bottom.spaces.active
    sp.show_word_wrap, sp.show_line_numbers, sp.show_syntax_highlight = True, False, False
    text = bpy.data.texts.get(text_name) if text_name else _current_prompt_text()
    if text is not None:
        sp.text = text
    for a in areas:
        a.tag_redraw()
    win.screen[_MARK] = True
    focus_comfyq_tab_soon(ptr)
    if state.get("graph"):
        tries = {"n": 0}

        def graph_step():              # the areas just set up are laid out on the next draw
            w = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == ptr), None)
            tries["n"] += 1
            if w is None or tries["n"] > 40:
                return None
            try:
                return None if set_graph_mode(w, True) else 0.05
            except RuntimeError as e:
                print("ComfyQ window graph mode:", e)
                return 0.1
        bpy.app.timers.register(graph_step, first_interval=0.05)
    return None


FOCUS_LOG: list[tuple] = []          # last tab-focus attempts (diagnostics for tests)


def focus_comfyq_tab_soon(ptr: int) -> None:
    """Show the ComfyQ tab in the window's Image Editor sidebar. The sidebar only knows its tabs
    after it has been drawn (until then the setter fails or is ignored): retry until it reads back.
    Up to 30 s: on a first start (fresh profile, shaders compiling) a window can take seconds to draw;
    it stops at the first success, so a tab the user picks afterwards is never overridden."""
    tries = {"n": 0}
    FOCUS_LOG.clear()

    def focus():
        w = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == ptr), None)
        tries["n"] += 1
        if w is None or tries["n"] > 200:
            FOCUS_LOG.append(("stop", tries["n"], "window gone" if w is None else "gave up"))
            return None
        area = next((a for a in w.screen.areas if a.type == "IMAGE_EDITOR"), None)
        if area is not None:
            area.spaces.active.show_region_ui = True
        ok = area is not None and _focus_comfyq_tab(area)
        if tries["n"] <= 3 or ok or tries["n"] % 20 == 0:
            reg = next((r for r in area.regions if r.type == "UI"), None) if area else None
            FOCUS_LOG.append((tries["n"], ok, reg.width if reg else None, reg.active_panel_category if reg else None))
        return None if ok else 0.15

    bpy.app.timers.register(focus, first_interval=0.15)


def _latest_comfyq_image():
    """Most recent result/keyframe/pass preview, to show when the window opens."""
    imgs = [i for i in bpy.data.images if i.get("comfyq_job") or i.name.startswith("ComfyQ")]
    return imgs[-1] if imgs else None


def _current_prompt_text():
    scene = bpy.context.scene
    wf = scene.comfyq.workflow_id
    return next((it.text for it in scene.comfyq.params
                 if it.workflow_id == wf and it.kind == "prompt" and it.text is not None), None)


class COMFYQ_OT_open_window(bpy.types.Operator):
    bl_idname = "comfyq.open_window"
    bl_label = "ComfyQ Window"
    bl_description = ("Open ComfyQ in its own window (place it anywhere, e.g. a second screen): "
                      "the full-width ComfyQ bar, a preview of passes and results, and the prompt editor")

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def execute(self, context):
        old = comfyq_window()
        if old is not None and old != context.window:
            # Blender cannot bring a window to the front: close the old one, open a fresh one on top.
            with context.temp_override(window=old):
                bpy.ops.wm.window_close()
        if not open_comfyq_window(context):
            self.report({"ERROR"}, "Could not open a window")
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (COMFYQ_OT_set_workflow, COMFYQ_MT_workflows, COMFYQ_MT_keyframe_workflows, COMFYQ_OT_random_seed,
           COMFYQ_OT_match_scene, COMFYQ_OT_preview_pass, COMFYQ_OT_preview_animation, COMFYQ_OT_edit_prompt,
           COMFYQ_OT_unlink_prompt, COMFYQ_OT_open_window)
