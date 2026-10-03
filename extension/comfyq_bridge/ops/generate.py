# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate: render passes (main thread) → upload + book (worker) → track → import.

AI keyframes chain two jobs: an image workflow restyles the scene frame first,
its result becomes the image input of the main (e.g. video-to-video) job.
"""
from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from pathlib import Path

import bpy
from bpy.props import StringProperty

from .. import match as scene_match
from ..client.params import ParamSpec, is_disabled
from ..importers.media import IMAGE_EXTS, import_files, import_image
from ..passes import animation, render
from ..props import INT_MAX, param_item, workflow_items
from ..runtime.session import SESSION, JobView, cache_dir
from .workflow import open_file, scene_facts

Report = Callable[[set, str], None]


class NeedsKeyframe(Exception):
    def __init__(self, key: str, spec: ParamSpec):
        super().__init__(key)
        self.key, self.spec = key, spec


def _collect(scene, selected, wf_id: str, specs: list[ParamSpec], frame: int | None,
             keyframe_ok: bool) -> tuple[dict, dict[str, Path]]:
    """Form values of `wf_id` + rendered/saved media. Returns (values, {key: file to upload})."""
    style = scene.comfyq.style
    current = {it.key: it.value() for it in workflow_items(scene, wf_id)}
    values: dict = {}
    uploads: dict[str, Path] = {}
    rendered: dict[str, Path] = {}             # one render per source/frame per job
    for s in specs:
        it = param_item(scene, s.key, wf_id)
        if it is None or is_disabled(s, current, specs):
            continue
        if s.kind == "seed":
            if it.randomize:
                it.i = random.randint(0, INT_MAX)
            values[s.key] = it.i
        elif s.is_media:
            path = _media(scene, selected, it, s, rendered, style, frame, keyframe_ok)
            if path is not None:
                uploads[s.key] = path
        else:
            v = it.value()
            if v != "" and v is not None:
                values[s.key] = v
    return values, uploads


def _media(scene, selected, it, spec, rendered, style, frame, keyframe_ok) -> Path | None:
    src = it.source
    if src == "NONE":
        return None
    if src == "ANIMATION":
        # Always a fresh render at Generate: the job must use the animation as it is now
        # (a few seconds of Workbench), whatever edit the change detection might miss.
        return animation.animation_pass(scene, style, spec.max_input_edge, force=True).path
    if src == "KEYFRAME":
        if it.image is None:
            if not keyframe_ok:
                raise RuntimeError(f"“{spec.label}”: an AI keyframe cannot itself use an AI keyframe")
            raise NeedsKeyframe(it.key, spec)
        return render.image_pass(it.image, spec.max_input_edge)
    if src in ("STRUCTURE", "MASK"):
        # Start/first-frame inputs render frame_start, last-frame inputs frame_end.
        f = frame if frame is not None else scene_match.frame_for(spec, scene_facts(scene))
        cache_key = f"{src}:{spec.max_input_edge}:{f}"
        if cache_key not in rendered:
            if src == "MASK":
                rendered[cache_key] = render.mask_pass(scene, selected, style, spec.max_input_edge, f)
            else:
                rendered[cache_key] = render.structure_pass(scene, style, spec.max_input_edge, f)
        return rendered[cache_key]
    if src == "IMAGE":
        if it.image is None:
            raise RuntimeError(f"Choose an image for “{spec.label}”")
        return render.image_pass(it.image, spec.max_input_edge)
    p = Path(bpy.path.abspath(it.path)) if it.path else None
    if p is None or not p.is_file():
        raise RuntimeError(f"Choose a file for “{spec.label}”")
    return p


def start_job(scene, selected, wf_id: str, report: Report, *, frame: int | None = None, purpose: str = "",
              then: Callable[[JobView], None] | None = None) -> bool:
    """Render inputs now (main thread), then upload + book in the background."""
    specs = SESSION.specs.get(wf_id)
    if not specs or SESSION.client is None:
        report({"ERROR"}, "Connect and choose a workflow first")
        return False
    try:
        values, uploads = _collect(scene, selected, wf_id, specs, frame, keyframe_ok=purpose != "keyframe")
    except NeedsKeyframe as nk:
        return _keyframe_then(scene, selected, wf_id, nk, report)
    except RuntimeError as e:
        report({"ERROR"}, str(e))
        return False
    book(scene, wf_id, specs, values, uploads, purpose=purpose, then=then)
    return True


def book(scene, wf_id: str, specs: list[ParamSpec], values: dict, uploads: dict[str, Path], *, purpose: str = "",
         then: Callable[[JobView], None] | None = None, label: str = "",
         on_booked: Callable[[str], None] | None = None, on_error: Callable[[str], None] | None = None) -> None:
    """Upload `uploads` and book the job in the background; record it in the scene's job list.
    Shared by the bar (start_job) and the graph."""
    client = SESSION.client
    scene_name = scene.name
    sent = json.dumps({key: str(path) for key, path in uploads.items()})
    values = dict(values)

    def work():
        for key, path in uploads.items():
            values[key] = client.upload(path)
        return client.submit(wf_id, values, specs=specs)

    def done(job_id):
        SESSION.track(job_id, wf_id, then)
        sc = bpy.data.scenes.get(scene_name)
        if sc is not None:
            rec = sc.comfyq.jobs.add()
            rec.job_id, rec.workflow_id, rec.created, rec.purpose = job_id, wf_id, time.time(), purpose
            rec.inputs = sent
            name = next((w.name for w in SESSION.workflows if w.id == wf_id), wf_id)
            rec.label = label or (f"Keyframe · {name}" if purpose == "keyframe" else name)
        SESSION.status_note = f"Booked {job_id[:8]}" + (" (AI keyframe)" if purpose == "keyframe" else "")
        if on_booked:
            on_booked(job_id)

    SESSION.error = ""
    SESSION.run(work, done, label="Sending keyframe job" if purpose == "keyframe" else "Sending job",
                on_error=on_error)


def keyframe_frame(scene, spec: ParamSpec) -> int:
    return scene_match.frame_for(spec, scene_facts(scene))


def run_keyframe(scene, selected, target_wf: str, key: str, report: Report,
                 then: Callable[[], None] | None = None) -> bool:
    """Restyle the scene frame for input `key` of `target_wf` with the chosen keyframe workflow."""
    kf_wf = scene.comfyq.keyframe_workflow_id
    if not kf_wf or kf_wf not in SESSION.specs:
        report({"ERROR"}, "Choose the keyframe model first (AI keyframe › Model)")
        return False
    spec = next(s for s in SESSION.specs[target_wf] if s.key == key)
    scene_name = scene.name

    def attach(view: JobView) -> None:
        sc = bpy.data.scenes.get(scene_name)
        img_file = next((f for f in view.files if f.suffix.lower() in IMAGE_EXTS), None)
        if sc is None or img_file is None:
            SESSION.error = "The keyframe workflow returned no image"
            return
        img = import_image(img_file, view.job.id, view.job.workflow_id)
        img.name = f"ComfyQ keyframe {view.job.id[:8]}"
        it = param_item(sc, key, target_wf)
        if it is not None:
            it.image = img
        _mark_imported(sc, view.job.id)
        if then:
            then()

    return start_job(scene, selected, kf_wf, report, frame=keyframe_frame(scene, spec), purpose="keyframe",
                     then=attach)


def _keyframe_then(scene, selected, wf_id, nk: NeedsKeyframe, report: Report) -> bool:
    """The main job needs an AI keyframe that does not exist yet: make it, then continue."""
    scene_name = scene.name
    selected_names = [o.name for o in selected]

    def continue_main():
        sc = bpy.data.scenes.get(scene_name)
        if sc is not None:
            sel = [bpy.data.objects[n] for n in selected_names if n in bpy.data.objects]
            start_job(sc, sel, wf_id, lambda _lvl, msg: setattr(SESSION, "error", msg))

    report({"INFO"}, "Making the AI keyframe first; the main job follows automatically")
    return run_keyframe(scene, selected, wf_id, nk.key, report, then=continue_main)


def _mark_imported(scene, job_id: str) -> None:
    for r in scene.comfyq.jobs:
        if r.job_id == job_id:
            r.imported = True


def can_generate(context) -> bool:
    cq = context.scene.comfyq
    return SESSION.connected and bool(cq.workflow_id) and cq.workflow_id in SESSION.specs


def generate(context, report: Report) -> bool:
    return start_job(context.scene, list(context.selected_objects), context.scene.comfyq.workflow_id, report)


class COMFYQ_OT_generate(bpy.types.Operator):
    bl_idname = "comfyq.generate"
    bl_label = "Generate"
    bl_description = "Render the inputs, send the job to ComfyQ and import the result when it is done"

    @classmethod
    def poll(cls, context):
        return can_generate(context)

    def execute(self, context):
        return {"FINISHED"} if generate(context, self.report) else {"CANCELLED"}


class COMFYQ_OT_generate_keyframe(bpy.types.Operator):
    bl_idname = "comfyq.generate_keyframe"
    bl_label = "Generate Keyframe"
    bl_description = "Restyle the scene frame with the keyframe model; the result becomes this input"
    key: StringProperty()

    @classmethod
    def poll(cls, context):
        return SESSION.connected and context.scene.comfyq.keyframe_workflow_id in SESSION.specs

    def execute(self, context):
        sc = context.scene
        ok = run_keyframe(sc, list(context.selected_objects), sc.comfyq.workflow_id, self.key, self.report)
        return {"FINISHED"} if ok else {"CANCELLED"}


class COMFYQ_OT_clear_keyframe(bpy.types.Operator):
    bl_idname = "comfyq.clear_keyframe"
    bl_label = "Clear Keyframe"
    bl_description = "Forget this AI keyframe; the next Generate makes a new one"
    bl_options = {"INTERNAL", "UNDO"}
    key: StringProperty()

    def execute(self, context):
        it = param_item(context.scene, self.key)
        if it is not None:
            it.image = None
        return {"FINISHED"}


class COMFYQ_OT_cancel(bpy.types.Operator):
    bl_idname = "comfyq.cancel"
    bl_label = "Cancel Job"
    bl_options = {"INTERNAL"}
    job_id: StringProperty()

    def execute(self, context):
        client = SESSION.client
        if client is None:
            return {"CANCELLED"}
        job_id = self.job_id          # the lambda runs after this operator is freed
        SESSION.run(lambda: client.cancel(job_id))
        return {"FINISHED"}


class COMFYQ_OT_import(bpy.types.Operator):
    bl_idname = "comfyq.import_result"
    bl_label = "Import Result"
    bl_options = {"INTERNAL", "UNDO"}
    job_id: StringProperty()

    def execute(self, context):
        view = SESSION.jobs.get(self.job_id)
        if view is None or not view.files:
            self.report({"WARNING"}, "Result not downloaded yet")
            return {"CANCELLED"}
        for msg in import_files(view.files, self.job_id, view.job.workflow_id, context.scene):
            self.report({"INFO"}, f"Imported {msg}")
        view.imported = True
        _mark_imported(context.scene, self.job_id)
        return {"FINISHED"}


class COMFYQ_OT_open_folder(bpy.types.Operator):
    bl_idname = "comfyq.open_folder"
    bl_label = "Open Folder"
    bl_options = {"INTERNAL"}
    job_id: StringProperty()

    def execute(self, context):
        folder = cache_dir() / self.job_id[:8] if self.job_id else cache_dir()
        folder.mkdir(parents=True, exist_ok=True)
        open_file(folder)
        return {"FINISHED"}


def on_job_done(view: JobView) -> None:
    """Session hook (main thread): auto-import into scenes that booked this job, once."""
    for sc in bpy.data.scenes:
        rec = next((r for r in sc.comfyq.jobs if r.job_id == view.job.id), None)
        # keyframes and graph results feed other jobs; graph nodes show and import their own results
        if rec is None or rec.imported or rec.purpose in ("keyframe", "graph") or not sc.comfyq.auto_import:
            continue
        msgs = import_files(view.files, view.job.id, view.job.workflow_id, sc)
        rec.imported, view.imported = True, True
        SESSION.status_note = "Imported " + "; ".join(msgs)


classes = (COMFYQ_OT_generate, COMFYQ_OT_generate_keyframe, COMFYQ_OT_clear_keyframe, COMFYQ_OT_cancel,
           COMFYQ_OT_import, COMFYQ_OT_open_folder)
