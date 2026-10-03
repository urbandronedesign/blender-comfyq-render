# SPDX-License-Identifier: GPL-3.0-or-later
"""Choosing workflows, editing parameters, matching them to the scene, previewing passes, prompt text."""
from __future__ import annotations

import os
import random
import subprocess
import sys

import bpy
from bpy.props import BoolProperty, StringProperty

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
    """Make the items of `wf_id` match `specs`; other workflows' values are left alone."""
    coll = scene.comfyq.params
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
        return bool(current_specs(context))

    def execute(self, context):
        m = scene_match.match(current_specs(context), scene_facts(context.scene))
        apply_values(context.scene, context.scene.comfyq.workflow_id, m.values)
        self.report({"INFO"}, "Matched: " + (", ".join(m.notes) or "already matching"))
        for w in m.warnings:
            self.report({"WARNING"}, w)
        return {"FINISHED"}


def open_file(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class COMFYQ_OT_preview_pass(bpy.types.Operator):
    bl_idname = "comfyq.preview_pass"
    bl_label = "Capture Viewport"
    bl_description = "Render the still pass that will be sent (scene camera) and show it in an Image Editor"
    mask: BoolProperty(default=False)

    def execute(self, context):
        from ..ui import player
        scene = context.scene
        try:
            if self.mask:
                path = render.mask_pass(scene, list(context.selected_objects), scene.comfyq.style)
            else:
                path = render.structure_pass(scene, scene.comfyq.style)
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
        self.report({"INFO"}, f"{img.size[0]}×{img.size[1]} {scene.comfyq.style.lower()} render of “{scene.camera.name}”")
        return {"FINISHED"}


class COMFYQ_OT_preview_animation(bpy.types.Operator):
    bl_idname = "comfyq.preview_animation"
    bl_label = "Render Animation Pass"
    bl_description = ("Render the frame range through the scene camera (the video that will be sent) "
                      "and play it. Generate reuses it while the scene is unchanged")
    force: BoolProperty(default=False, options={"SKIP_SAVE"})
    play: BoolProperty(default=True, options={"SKIP_SAVE"})

    def execute(self, context):
        scene = context.scene
        edge = max((s.max_input_edge or 0 for s in current_specs(context) if s.kind == "video"), default=0) or None
        try:
            p = animation.animation_pass(scene, scene.comfyq.style, edge, force=self.force)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Animation pass: {p.frames} frames, {p.seconds:.1f} s, {p.size[0]}×{p.size[1]}")
        if self.play:
            from ..ui import player
            area = player.viewer_area() if comfyq_window() is not None else None
            if area is None:
                open_file(p.path)                      # no ComfyQ window: the system player
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

_WINDOW = [0]       # pointer of the last ComfyQ window (for tests and focus)


def comfyq_window():
    return next((w for w in bpy.context.window_manager.windows if w.as_pointer() == _WINDOW[0]), None)


def open_comfyq_window(context, text_name: str = "") -> bool:
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
    state = {"tries": 0}
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
            except (TypeError, ValueError, ReferenceError):
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
    # The sidebar only knows its tabs after it has been drawn, and an early switch is silently
    # ignored: keep asking for the ComfyQ tab until it reads back.
    tries = {"n": 0}

    def focus():
        w = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == ptr), None)
        tries["n"] += 1
        if w is None or tries["n"] > 40:
            return None
        area = next((a for a in w.screen.areas if a.type == "IMAGE_EDITOR"), None)
        return None if area is not None and _focus_comfyq_tab(area) else 0.15

    bpy.app.timers.register(focus, first_interval=0.15)
    return None


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
        if not open_comfyq_window(context):
            self.report({"ERROR"}, "Could not open a window")
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (COMFYQ_OT_set_workflow, COMFYQ_MT_workflows, COMFYQ_MT_keyframe_workflows, COMFYQ_OT_random_seed,
           COMFYQ_OT_match_scene, COMFYQ_OT_preview_pass, COMFYQ_OT_preview_animation, COMFYQ_OT_edit_prompt,
           COMFYQ_OT_unlink_prompt, COMFYQ_OT_open_window)
