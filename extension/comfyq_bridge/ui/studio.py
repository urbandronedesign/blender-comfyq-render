# SPDX-License-Identifier: GPL-3.0-or-later
"""The generation bar, laid out like Mixar's own (tabs · chips · prompt · references · Generate).

Drawn in the View3D sidebar, the Image Editor sidebar (the detached ComfyQ
window) and a floating dialog — all from this code. Drawing only reads data.
"""
from __future__ import annotations

import bpy

from .. import match as scene_match
from ..client.params import ParamSpec, is_disabled
from ..ops import workflow as wfops
from ..passes import animation
from ..props import param_item, workflow_items
from ..runtime.session import SESSION

PHASE_TEXT = {
    "scheduled": "Waiting in queue", "uploading-inputs": "Preparing inputs", "submitted": "Starting",
    "executing": "Generating", "collecting-outputs": "Collecting result", "completed": "Done",
    "failed": "Failed", "cancelled": "Cancelled",
}
MEDIA_ICON = {"video": "FILE_MOVIE", "audio": "SOUND", "image": "IMAGE_DATA", "mask": "SELECT_SET"}


def _visible(scene, wf_id: str, specs: list[ParamSpec]) -> list[ParamSpec]:
    current = {it.key: it.value() for it in workflow_items(scene, wf_id)}
    return [s for s in specs if param_item(scene, s.key, wf_id) is not None and not is_disabled(s, current, specs)]


def _prop(layout, data, attr, text="", placeholder=""):
    try:
        layout.prop(data, attr, text=text, placeholder=placeholder)
    except TypeError:            # Blender without placeholder support
        layout.prop(data, attr, text=text)


_CHARS = [60]      # characters per wrapped line, from the width of the region being drawn


def _chars_for(context) -> int:
    width = context.region.width if context.region else 400
    scale = context.preferences.view.ui_scale or 1.0
    return max(24, int(width / scale / 8.0))   # ~8 px per character keeps lines from being clipped


def draw_studio(layout: bpy.types.UILayout, context, columns: int = 2, dialog: bool = False) -> None:
    scene = context.scene
    cq = scene.comfyq
    _CHARS[0] = 110 if dialog else _chars_for(context)

    # -- tabs · window · library · queue ------------------------------------------------
    narrow = columns == 1
    top = layout.row()
    top.prop(cq, "tab", expand=True, icon_only=narrow)       # narrow sidebar: icons, names as tooltips
    if narrow:                       # tools on their own row
        top = layout.row()
    right = top.row(align=True)
    right.alignment = "RIGHT"
    if not dialog and context.window != wfops.comfyq_window():
        right.operator("comfyq.open_window", text="", icon="WINDOW")
    right.operator("comfyq.open_graph", text="", icon="NODETREE")
    right.operator("comfyq.open_folder", text="Library", icon="FILE_FOLDER").job_id = ""
    active = sum(1 for v in SESSION.jobs.values() if not v.job.done)
    right.popover("COMFYQ_PT_queue", text=f"Queue {active}" if active else "Queue", icon="SORTTIME")

    from ..ops.results import scene_has_result_strips
    if scene_has_result_strips(scene):
        warn = layout.box()
        warn.alert = True
        warn.label(text="AI results sit in this scene's sequencer: your own renders show them, not the camera",
                   icon="ERROR")
        warn.operator("comfyq.cleanup", text="Clean up", icon="BRUSH_DATA")

    if SESSION.client is None:
        row = layout.row()
        row.operator("comfyq.connect", icon="LINKED")
        if SESSION.status != "Not connected":
            row.label(text=SESSION.status)
        _messages(layout)
        return

    wf = next((w for w in SESSION.workflows if w.id == cq.workflow_id), None)
    served = SESSION.served_ids()
    box = layout.box()

    # -- model + settings chips ----------------------------------------------------------
    grid = box.grid_flow(row_major=True, columns=columns, even_columns=True, align=False)
    chip = grid.row(align=True)
    chip.alert = wf is not None and wf.id not in served
    chip.menu("COMFYQ_MT_workflows", text=wf.name if wf else "Choose a model…",
              icon="PLAY" if wf and wf.id in served else "DOWNARROW_HLT")
    specs = SESSION.specs.get(cq.workflow_id) if wf else None
    if wf and specs is None:
        grid.label(text="Loading…", icon="SORTTIME")
    visible = _visible(scene, cq.workflow_id, specs or [])
    draw_chips(grid, scene, cq.workflow_id, visible)
    if wf and wf.id not in served:
        box.label(text="Not served right now — ask the teacher to serve this workflow", icon="ERROR")

    # -- prompt ---------------------------------------------------------------------------------
    draw_prompts(box, scene, cq.workflow_id, visible, big=True)

    # -- scene ↔ workflow (timing, framing, animation pass) ---------------------------------
    if any(s.kind == "video" for s in visible) or scene_match.match(visible, wfops.scene_facts(scene)).values:
        _draw_scene_strip(box, context, visible)

    # -- references (+ AI keyframe step) and actions ---------------------------------------------
    draw_media(box, context, cq.workflow_id, visible, columns)
    actions = box.row(align=True)
    media_items = [param_item(scene, s.key) for s in visible if s.is_media]
    actions.prop(cq, "style", text="")
    actions.operator("comfyq.preview_pass", text="Render scene", icon="RESTRICT_VIEW_OFF").mask = False
    if any(it.source == "MASK" for it in media_items):
        actions.operator("comfyq.preview_pass", text="Mask", icon="SELECT_SET").mask = True
    if not dialog:
        gen = box.row() if narrow else actions.row()       # narrow: Generate gets its own full row
        gen.scale_x, gen.scale_y = 1.6, 1.3
        gen.operator("comfyq.generate", icon="RENDER_STILL")
    _messages(layout)


def draw_chips(grid, scene, wf_id: str, visible: list[ParamSpec], get=None) -> None:
    """Setting chips; `get(key)` returns the value item (default: the scene's store, i.e. the bar)."""
    for s in visible:
        if s.is_media or s.kind in ("prompt", "text"):
            continue
        it = get(s.key) if get else param_item(scene, s.key, wf_id)
        if it is None:
            continue
        short = scene_match.short_label(s.label)
        if s.kind == "seed":
            r = grid.row(align=True)
            r.prop(it, "i", text="Seed")
            r.prop(it, "randomize", text="", icon="FILE_REFRESH", toggle=True)
        elif s.kind == "int":
            grid.prop(it, "i", text=short)
        elif s.kind == "float":
            grid.prop(it, "f", text=short)
        elif s.kind == "bool":
            grid.prop(it, "b", text=short, toggle=True)
        elif s.kind == "enum":
            grid.prop(it, "e", text=short)


def draw_prompts(layout, scene, wf_id: str, visible: list[ParamSpec], big: bool) -> None:
    prompts = [s for s in visible if s.kind in ("prompt", "text")]
    for s in prompts:
        it = param_item(scene, s.key, wf_id)
        col = layout.column(align=True)
        if it.text is not None:
            # Multi-line prompt: show it wrapped, edit it in the Text Editor.
            head = col.row(align=True)
            head.label(text=f"{scene_match.short_label(s.label, 40)} — “{it.text.name}”", icon="TEXT")
            op = head.operator("comfyq.edit_prompt", text="Edit", icon="GREASEPENCIL")
            op.key, op.workflow_id = s.key, wf_id
            op = head.operator("comfyq.unlink_prompt", text="", icon="X")
            op.key, op.workflow_id = s.key, wf_id
            preview = col.box().column(align=True)
            for line in wrap(it.text.as_string(), _CHARS[0], max_lines=10):
                preview.label(text=line)
        else:
            row = col.row(align=True)
            field = row.row(align=True)
            field.scale_y = 2.0 if big and s.kind == "prompt" else 1.0
            _prop(field, it, "s", placeholder=scene_match.short_label(s.label, 60) or "Describe your scene here…")
            ed = row.column(align=True)
            ed.scale_y = field.scale_y
            op = ed.operator("comfyq.edit_prompt", text="", icon="FULLSCREEN_ENTER")
            op.key, op.workflow_id = s.key, wf_id
    if big and SESSION.specs.get(wf_id) and not any(s.kind == "prompt" for s in visible):
        layout.label(text="This workflow takes no prompt", icon="INFO")


def draw_media(layout, context, wf_id: str, visible: list[ParamSpec], columns: int) -> None:
    scene = context.scene
    media = [s for s in visible if s.is_media]
    if not media:
        return
    refs = layout.grid_flow(row_major=True, columns=max(1, min(columns, len(media))), even_columns=True)
    for s in media:
        it = param_item(scene, s.key, wf_id)
        col = refs.column(align=True)
        col.label(text=scene_match.short_label(s.label, 34), icon=MEDIA_ICON.get(s.kind, "FILE"))
        col.prop(it, "source")
        if it.source == "IMAGE":
            col.template_ID(it, "image", open="image.open")
        elif it.source == "FILE":
            col.prop(it, "path", text="")
        elif it.source == "MASK":
            col.label(text="Selected objects = area to repaint")
        elif it.source == "STRUCTURE":
            col.label(text=f"Camera view at frame {scene_match.frame_for(s, wfops.scene_facts(scene))}")
    for s in media:
        it = param_item(scene, s.key, wf_id)
        if it.source == "KEYFRAME":
            _draw_keyframe(layout, context, s, it, columns)


def _draw_keyframe(layout, context, spec: ParamSpec, it, columns: int) -> None:
    """The AI-keyframe step: its own model, settings and prompt, run on the right scene frame."""
    scene = context.scene
    cq = scene.comfyq
    box = layout.box()
    frame = scene_match.frame_for(spec, wfops.scene_facts(scene))
    head = box.row(align=True)
    head.label(text=f"AI keyframe for “{scene_match.short_label(spec.label, 30)}” · scene frame {frame}",
               icon="SHADERFX")
    kf = next((w for w in SESSION.workflows if w.id == cq.keyframe_workflow_id), None)
    grid = box.grid_flow(row_major=True, columns=columns, even_columns=True, align=False)
    chip = grid.row(align=True)
    chip.alert = kf is not None and kf.id not in SESSION.served_ids()
    chip.menu("COMFYQ_MT_keyframe_workflows", text=kf.name if kf else "Choose the style model…",
              icon="DOWNARROW_HLT")
    kf_specs = SESSION.specs.get(cq.keyframe_workflow_id) if kf else None
    if kf and kf_specs is None:
        grid.label(text="Loading…", icon="SORTTIME")
    if kf_specs:
        visible = _visible(scene, kf.id, kf_specs)
        draw_chips(grid, scene, kf.id, visible)
        draw_prompts(box, scene, kf.id, visible, big=False)
        sub = [s for s in visible if s.is_media]
        if sub:
            refs = box.grid_flow(row_major=True, columns=max(1, min(columns, len(sub))), even_columns=True)
            for s in sub:
                sit = param_item(scene, s.key, kf.id)
                col = refs.column(align=True)
                col.label(text=scene_match.short_label(s.label, 34), icon=MEDIA_ICON.get(s.kind, "FILE"))
                col.prop(sit, "source")
                if sit.source == "IMAGE":
                    col.template_ID(sit, "image", open="image.open")
                elif sit.source == "FILE":
                    col.prop(sit, "path", text="")
    row = box.row(align=True)
    if it.image is not None:
        row.label(text=f"Using “{it.image.name}”", icon="CHECKMARK")
        row.operator("comfyq.clear_keyframe", text="", icon="X").key = it.key
        row.operator("comfyq.generate_keyframe", text="Regenerate", icon="FILE_REFRESH").key = it.key
    else:
        row.label(text="Not made yet — Generate makes it first, then the main job", icon="INFO")
        row.operator("comfyq.generate_keyframe", text="Generate keyframe", icon="RENDER_STILL").key = it.key


def _draw_scene_strip(box, context, specs) -> None:
    scene = context.scene
    facts = wfops.scene_facts(scene)
    m = scene_match.match(specs, facts)
    current = {it.key: it.value() for it in workflow_items(scene, scene.comfyq.workflow_id)}
    mismatched = [k for k, v in m.values.items() if not _same(current.get(k), v)]
    strip = box.box()
    cam = scene.camera.name if scene.camera else "NO CAMERA"
    text = (f"Scene {facts.frame_start}–{facts.frame_end} · {facts.fps:g} fps · {facts.seconds:.1f} s · "
            f"{scene.render.resolution_x}×{scene.render.resolution_y} · {cam}")
    narrow = _CHARS[0] < 60
    if narrow:                       # the facts on their own (wrapped) lines, the button below
        _wrapped(strip, text, "CAMERA_DATA")
        head = strip.row(align=True)
    else:
        head = strip.row(align=True)
        head.label(text=text, icon="CAMERA_DATA")
    sub = head.row(align=True)
    sub.alert = bool(mismatched)
    sub.operator("comfyq.match_scene", text="Match to scene" if mismatched else "Matched",
                 icon="LINKED" if mismatched else "CHECKMARK")
    for w in m.warnings:
        strip.label(text=w, icon="ERROR")
    if not any(s.kind == "video" for s in specs):
        return
    edge = max((s.max_input_edge or 0 for s in specs if s.kind == "video"), default=0) or None
    ready = animation.current(scene, scene.comfyq.style, edge)
    col = strip.column(align=True)
    _wrapped(col, "Clay animation (what is sent) — rendered fresh on every Generate", "RENDER_ANIMATION")
    row = col.row(align=True)
    op = row.operator("comfyq.preview_animation", text="Render & play clay animation", icon="PLAY")
    op.force, op.play = True, True
    if ready:
        op = row.operator("comfyq.preview_animation", text="Replay", icon="LOOP_BACK")
        op.force, op.play = False, True


def _wrapped(layout, text: str, icon: str) -> None:
    """A label that wraps to the width being drawn instead of being cut off."""
    for n, line in enumerate(wrap(text, _CHARS[0], max_lines=3)):
        layout.label(text=line, icon=icon if n == 0 else "BLANK1")


def _same(a, b) -> bool:
    """Equal, with float tolerance: Blender stores floats in single precision (5.042 → 5.0419998)."""
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-3
        except (TypeError, ValueError):
            return False
    return a == b


def _messages(layout) -> None:
    if SESSION.busy:
        layout.label(text=SESSION.busy + "…", icon="SORTTIME")
    if SESSION.error:
        col = layout.column()
        col.alert = True
        for n, line in enumerate(wrap(SESSION.error, _CHARS[0])):
            col.label(text=line, icon="ERROR" if n == 0 else "BLANK1")
    elif SESSION.status_note:
        for n, line in enumerate(wrap(SESSION.status_note, _CHARS[0], max_lines=3)):
            layout.label(text=line, icon="INFO" if n == 0 else "BLANK1")


def draw_jobs(layout, context, limit: int | None = None) -> None:
    cq = context.scene.comfyq
    recs = list(cq.jobs)[-(limit or cq.show_jobs):]
    if not recs:
        layout.label(text="No jobs yet")
        return
    for rec in reversed(recs):
        box = layout.box()
        view = SESSION.jobs.get(rec.job_id)
        job = view.job if view else None
        head = box.row()
        head.label(text=f"{rec.label}  ({rec.job_id[:8]})")
        if job is None:
            head.label(text="connect to see it")
            continue
        phase = PHASE_TEXT.get(job.phase, job.phase)
        _draw_sent(box, rec)
        if not job.done:
            if job.progress is not None and job.phase == "executing":
                box.progress(factor=job.progress, type="BAR", text=f"{phase} {job.progress * 100:.0f}%")
            else:
                box.label(text=phase, icon="SORTTIME")
            head.operator("comfyq.cancel", text="", icon="X").job_id = rec.job_id
        elif job.ok:
            head.operator("comfyq.open_folder", text="", icon="FILE_FOLDER").job_id = rec.job_id
            box.label(text="Done · " + _when(rec, job), icon="CHECKMARK")
            if view.downloading:
                box.label(text="Downloading…", icon="IMPORT")
            elif not view.files:
                box.operator("comfyq.download", text="Download result", icon="IMPORT").job_id = rec.job_id
            for f in view.files:
                _draw_file(box, rec, f)
        else:
            box.alert = True
            box.label(text=phase, icon="ERROR")
            for line in wrap(job.error or "", 60):
                box.label(text=line)


_FILE_ICON = {".mp4": "FILE_MOVIE", ".mov": "FILE_MOVIE", ".webm": "FILE_MOVIE", ".gif": "FILE_MOVIE",
              ".glb": "MESH_DATA", ".gltf": "MESH_DATA", ".ply": "POINTCLOUD_DATA",
              ".wav": "SOUND", ".mp3": "SOUND", ".flac": "SOUND", ".txt": "TEXT", ".json": "TEXT"}
_VIEWABLE = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".exr", ".tif", ".tiff", ".mp4", ".mov", ".webm", ".gif"}


def _when(rec, job) -> str:
    import time as _t
    parts = [_t.strftime("%H:%M", _t.localtime(rec.created))] if rec.created else []
    if job.started_at and job.finished_at:
        parts.append(f"{(job.finished_at - job.started_at) / 1000:.0f} s")
    return " · ".join(parts)


def _draw_sent(box, rec) -> None:
    """What this job was given (clay animation, start image…), each with a Show button."""
    import json as _json
    from pathlib import Path as _Path
    try:
        sent = _json.loads(rec.inputs) if rec.inputs else {}
    except ValueError:
        sent = {}
    if not sent:
        return
    specs = {s.key: s for s in SESSION.specs.get(rec.workflow_id, ())}
    col = box.column(align=True)
    for key, path in sent.items():
        p = _Path(path)
        spec = specs.get(key)
        label = scene_match.short_label(spec.label, 28) if spec else key
        kind = "animation" if p.suffix.lower() in (".mp4", ".mov", ".webm") else "image"
        row = col.row(align=True)
        row.label(text=f"Sent · {label} ({kind})", icon="FILE_MOVIE" if kind == "animation" else "IMAGE_DATA")
        if p.is_file():
            row.operator("comfyq.show_media", text="Show", icon="PLAY").path = str(p)
        else:
            row.label(text="(cleared)")


def _draw_file(box, rec, path) -> None:
    """One output file: name, then Show · Open · Save copy · Import."""
    ext = path.suffix.lower()
    col = box.column(align=True)
    name = path.name if len(path.name) <= 40 else path.name[:18] + "…" + path.name[-18:]
    col.label(text=name, icon=_FILE_ICON.get(ext, "IMAGE_DATA"))
    row = col.grid_flow(row_major=True, columns=2, even_columns=True, align=True)   # 2×2: labels fit
    if ext in _VIEWABLE:
        row.operator("comfyq.show_media", text="Show", icon="PLAY").path = str(path)
    row.operator("comfyq.open_media", text="Open", icon="WINDOW").path = str(path)
    row.operator("comfyq.save_result", text="Save as…", icon="FILE_TICK").source = str(path)
    op = row.operator("comfyq.import_file", text="In scene" if rec.imported else "Import",
                      icon="CHECKMARK" if rec.imported else "IMPORT")
    op.job_id, op.path, op.again = rec.job_id, str(path), rec.imported


def wrap(text: str, width: int, max_lines: int = 6) -> list[str]:
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        cur = ""
        for w in para.split():
            if len(cur) + len(w) + 1 > width and cur:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        lines.append(cur)
    lines = [ln for ln in lines if ln] or [""]
    if len(lines) > max_lines:
        lines = [*lines[: max_lines - 1], lines[max_lines - 1] + " …"]
    return lines
