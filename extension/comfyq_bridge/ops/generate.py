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
    sent_values = json.dumps(values, default=str)

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
            rec.inputs, rec.values = sent, sent_values
            name =next((w.name for w in SESSION.workflows if w.id == wf_id), wf_id)
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


def why_not_generate(scene, wf_id: str) -> str:
    """'' when a job on `wf_id` can be booked now, else the reason (shown on the greyed-out button)."""
    if not SESSION.connected:
        return "Connect to ComfyQ first"
    if not wf_id:
        return "Choose a model first"
    if wf_id not in SESSION.specs:
        return "The model's settings are still loading"
    served = SESSION.served_ids()
    if served and wf_id not in served:
        name = next((w.name for w in SESSION.workflows if w.id == wf_id), wf_id)
        return f"“{name}” is not served right now: ask the teacher to serve it"
    return ""


def _record(scene, job_id: str):
    return next((r for r in scene.comfyq.jobs if r.job_id == job_id), None)


def why_not_retry(scene, job_id: str) -> str:
    """'' when the job `job_id` can be booked again with the same settings and files, else the reason."""
    rec = _record(scene, job_id)
    if rec is None:
        return "This job is no longer in the list"
    if not rec.values:
        return "This job was sent before Retry existed: use Generate"
    why = why_not_generate(scene, rec.workflow_id)
    if why:
        return why
    view = SESSION.jobs.get(job_id)
    if view is not None and not view.job.done:
        return "The job is still on the server"
    try:
        files = json.loads(rec.inputs) if rec.inputs else {}
    except ValueError:
        files = {}
    gone = [Path(p).name for p in files.values() if not Path(p).is_file()]
    if gone:
        return f"{gone[0]} is no longer on disk (Clean Up deletes temporary renders): use Generate"
    return ""


def retry(scene, job_id: str) -> None:
    """Book `job_id` again: same workflow, same values, same files (uploaded again), no new render."""
    rec = _record(scene, job_id)
    wf_id = rec.workflow_id
    values = json.loads(rec.values)
    uploads = {k: Path(p) for k, p in (json.loads(rec.inputs) if rec.inputs else {}).items()}
    label = rec.label if rec.label.startswith("Retry · ") else "Retry · " + rec.label
    book(scene, wf_id, SESSION.specs[wf_id], values, uploads, label=label)


def can_generate(context) -> bool:
    return not why_not_generate(context.scene, context.scene.comfyq.workflow_id)


def generate(context, report: Report) -> bool:
    return start_job(context.scene, list(context.selected_objects), context.scene.comfyq.workflow_id, report)


class COMFYQ_OT_generate(bpy.types.Operator):
    bl_idname = "comfyq.generate"
    bl_label = "Generate"
    bl_description = "Render the inputs, send the job to ComfyQ and import the result when it is done"

    @classmethod
    def poll(cls, context):
        why = why_not_generate(context.scene, context.scene.comfyq.workflow_id)
        if why:
            cls.poll_message_set(why)
        return not why

    def execute(self, context):
        return {"FINISHED"} if generate(context, self.report) else {"CANCELLED"}


class COMFYQ_OT_generate_keyframe(bpy.types.Operator):
    bl_idname = "comfyq.generate_keyframe"
    bl_label = "Generate Keyframe"
    bl_description = "Restyle the scene frame with the keyframe model; the result becomes this input"
    key: StringProperty()

    @classmethod
    def poll(cls, context):
        why = why_not_generate(context.scene, context.scene.comfyq.keyframe_workflow_id)
        if why:
            cls.poll_message_set("Keyframe model: " + why[0].lower() + why[1:])
        return not why

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
    bl_description = "Cancel this job on the ComfyQ server"
    bl_options = {"INTERNAL"}
    job_id: StringProperty()

    def execute(self, context):
        client = SESSION.client
        if client is None:
            self.report({"WARNING"}, "Not connected: connect to ComfyQ to cancel the job")
            return {"CANCELLED"}
        job_id = self.job_id          # the lambda runs after this operator is freed
        SESSION.run(lambda: client.cancel(job_id), label="Cancelling")
        self.report({"INFO"}, "Cancelling the job…")
        return {"FINISHED"}


class COMFYQ_OT_retry(bpy.types.Operator):
    bl_idname = "comfyq.retry"
    bl_label = "Retry"
    bl_description = "Send this job again with the same settings and files (no new render)"
    bl_options = {"INTERNAL"}
    job_id: StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, context, props):
        why = why_not_retry(context.scene, props.job_id) if props else ""
        return cls.bl_description if not why else f"{cls.bl_description}. Not now: {why}"

    def execute(self, context):
        why = why_not_retry(context.scene, self.job_id)
        if why:
            self.report({"WARNING"}, why)
            return {"CANCELLED"}
        retry(context.scene, self.job_id)
        self.report({"INFO"}, "Sending the job again…")
        return {"FINISHED"}


class COMFYQ_OT_import(bpy.types.Operator):
    bl_idname = "comfyq.import_result"
    bl_label = "Import Result"
    bl_description = "Bring this job's result into the scene"
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
    bl_description = "Open the folder of downloaded results in the file explorer"
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
        # keyframes and graph results feed other jobs; graph nodes show and import their own results;
        # agents import with blender_import when they want to
        if rec is None or rec.imported or rec.purpose in ("keyframe", "graph", "agent") or not sc.comfyq.auto_import:
            continue
        msgs = import_files(view.files, view.job.id, view.job.workflow_id, sc)
        rec.imported, view.imported = True, True
        SESSION.status_note = "Imported " + "; ".join(msgs)


classes = (COMFYQ_OT_generate, COMFYQ_OT_generate_keyframe, COMFYQ_OT_clear_keyframe, COMFYQ_OT_cancel,
           COMFYQ_OT_retry, COMFYQ_OT_import, COMFYQ_OT_open_folder)
