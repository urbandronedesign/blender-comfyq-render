# SPDX-License-Identifier: GPL-3.0-or-later
"""What a finished job card can do with each output file: show, open, save a copy, import, (re)download."""
from __future__ import annotations

import shutil
from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..importers.media import import_files
from ..runtime.session import SESSION


def default_save_dir() -> Path:
    """`//comfyq/` next to the saved .blend, else the user's Downloads folder."""
    if bpy.data.filepath:
        return Path(bpy.path.abspath("//comfyq"))
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


class COMFYQ_OT_save_result(bpy.types.Operator, ExportHelper):
    bl_idname = "comfyq.save_result"
    bl_label = "Save Copy"
    bl_description = "Save a copy of this result where you want (default: a comfyq folder next to the .blend)"
    bl_options = {"INTERNAL"}
    filename_ext = ""
    source: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    filter_glob: StringProperty(default="*", options={"HIDDEN"})

    def invoke(self, context, event):
        src = Path(self.source)
        folder = default_save_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.filepath = str(folder / src.name)
        self.filename_ext = src.suffix
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        src, dst = Path(self.source), Path(self.filepath)
        if not src.is_file():
            self.report({"ERROR"}, f"{src.name} is no longer in the cache: download it again")
            return {"CANCELLED"}
        if dst.suffix.lower() != src.suffix.lower():
            dst = dst.with_suffix(src.suffix)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        self.report({"INFO"}, f"Saved {dst}")
        return {"FINISHED"}


class COMFYQ_OT_import_file(bpy.types.Operator):
    bl_idname = "comfyq.import_file"
    bl_label = "Import into Scene"
    bl_description = "Bring this result into the scene (image → Image Editor, video/audio → Sequencer, 3D → collection)"
    bl_options = {"INTERNAL", "UNDO"}
    job_id: StringProperty()
    path: StringProperty()
    again: BoolProperty(default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        if self.again:
            return context.window_manager.invoke_confirm(
                self, event, title="Import again?", message="This result is already in the scene; import a second copy?",
                confirm_text="Import again")
        return self.execute(context)

    def execute(self, context):
        view = SESSION.jobs.get(self.job_id)
        wf = view.job.workflow_id if view else ""
        for msg in import_files([Path(self.path)], self.job_id, wf, context.scene):
            self.report({"INFO"}, f"Imported {msg}")
        for r in context.scene.comfyq.jobs:
            if r.job_id == self.job_id:
                r.imported = True
        if view:
            view.imported = True
        return {"FINISHED"}


class COMFYQ_OT_download(bpy.types.Operator):
    bl_idname = "comfyq.download"
    bl_label = "Download"
    bl_description = "Download this job's results from ComfyQ into the local cache"
    bl_options = {"INTERNAL"}
    job_id: StringProperty()

    @classmethod
    def poll(cls, context):
        if SESSION.client is None:
            cls.poll_message_set("Connect to ComfyQ first")
            return False
        return True

    def execute(self, context):
        view = SESSION.jobs.get(self.job_id)
        if view is None or not view.job.ok:
            self.report({"WARNING"}, "Nothing to download: the job has no finished result")
            return {"CANCELLED"}
        view.files = []
        SESSION._download(view)
        self.report({"INFO"}, "Downloading the result…")
        return {"FINISHED"}


def scene_has_result_strips(scene) -> bool:
    ed = scene.sequence_editor
    if ed is None or scene.name == "ComfyQ Results":
        return False
    strips = ed.strips if hasattr(ed, "strips") else ed.sequences
    return any(s.get("comfyq_job") or s.name.startswith("ComfyQ ") for s in strips)


class COMFYQ_OT_cleanup(bpy.types.Operator):
    bl_idname = "comfyq.cleanup"
    bl_label = "Clean Up"
    bl_description = ("Remove ComfyQ result strips from your 3D scenes' sequencer (they replace the camera "
                      "in renders), delete temporary renders and duplicate previews. Job results are kept")
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        from ..runtime.session import cache_dir
        ed_strips = 0
        for sc in bpy.data.scenes:
            if sc.sequence_editor is not None and sc.name != "ComfyQ Results":
                ed = sc.sequence_editor
                strips = ed.strips if hasattr(ed, "strips") else ed.sequences
                ed_strips += sum(1 for st in strips if st.get("comfyq_job") or st.name.startswith("ComfyQ "))
        passes = cache_dir() / "passes"
        n_files = sum(1 for p in passes.rglob("*") if p.is_file()) if passes.is_dir() else 0
        return context.window_manager.invoke_confirm(
            self, event, title="Clean Up?",
            message=(f"Remove {ed_strips} result strip(s) from the 3D scenes and delete {n_files} temporary "
                     "render file(s). Job results are kept. Deleted files cannot be restored."),
            confirm_text="Clean Up", icon="WARNING")

    def execute(self, context):
        from ..importers.media import remove_result_strips
        from ..passes import animation
        from ..runtime.session import cache_dir
        strips = sum(remove_result_strips(sc) for sc in bpy.data.scenes)
        passes = cache_dir() / "passes"
        n_files = sum(1 for p in passes.rglob("*") if p.is_file()) if passes.is_dir() else 0
        shutil.rmtree(passes, ignore_errors=True)
        shutil.rmtree(cache_dir() / "smoke", ignore_errors=True)
        animation.forget()
        shown = {a.spaces.active.image for w in context.window_manager.windows for a in w.screen.areas
                 if a.type == "IMAGE_EDITOR"}
        stale = [i for i in bpy.data.images
                 if i.name.startswith(("ComfyQ animation pass", "ComfyQ render preview", "ComfyQ mask preview",
                                       "ComfyQ pass preview")) and i not in shown]
        for img in stale:
            bpy.data.images.remove(img)
        self.report({"INFO"}, f"Removed {strips} strip(s) from your scenes, {n_files} temporary file(s), "
                              f"{len(stale)} old preview(s)")
        return {"FINISHED"}


class COMFYQ_OT_remove_job(bpy.types.Operator):
    bl_idname = "comfyq.remove_job"
    bl_label = "Remove from List"
    bl_description = "Remove this finished job from the list (its downloaded files stay on disk)"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    job_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        jobs = context.scene.comfyq.jobs
        idx = next((n for n, r in enumerate(jobs) if r.job_id == self.job_id), None)
        if idx is None:
            self.report({"WARNING"}, "This job is no longer in the list")
            return {"CANCELLED"}
        view = SESSION.jobs.get(self.job_id)
        if view is not None and not view.job.done:
            self.report({"WARNING"}, "Cancel the job first (✕): it is still on the server")
            return {"CANCELLED"}
        jobs.remove(idx)                                   # never keep a reference across remove (gotcha 7)
        SESSION.jobs.pop(self.job_id, None)
        self.report({"INFO"}, "Removed from the list")
        return {"FINISHED"}


classes = (COMFYQ_OT_save_result, COMFYQ_OT_import_file, COMFYQ_OT_download, COMFYQ_OT_cleanup,
           COMFYQ_OT_remove_job)
