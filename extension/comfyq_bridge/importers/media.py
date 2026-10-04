# SPDX-License-Identifier: GPL-3.0-or-later
"""Bring downloaded results into the .blend (main thread only).

image → Image Editor · video → Sequencer strip + Image Editor playback ·
GLB/glTF → its own collection · PLY (splats / point clouds) → its own collection ·
audio → Sequencer sound strip. Everything is tagged with its job and workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".exr", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif"}
MODEL_EXTS = {".glb", ".gltf"}
PLY_EXTS = {".ply"}
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


def tag(idblock: bpy.types.ID, job_id: str, workflow_id: str) -> None:
    """Provenance on everything we import (no server address, no token)."""
    idblock["comfyq_job"] = job_id
    idblock["comfyq_workflow"] = workflow_id


def show_in_image_editor(img: bpy.types.Image) -> bool:
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = img
                area.tag_redraw()
                return True
    return False


def import_image(path: Path, job_id: str, workflow_id: str, show: bool = True) -> bpy.types.Image:
    img = bpy.data.images.load(str(path), check_existing=True)
    tag(img, job_id, workflow_id)
    if show:
        show_in_image_editor(img)
    return img


RESULTS_SCENE = "ComfyQ Results"


def _strips(scene: bpy.types.Scene):
    ed = scene.sequence_editor or scene.sequence_editor_create()
    return ed.strips if hasattr(ed, "strips") else ed.sequences   # `sequences` before Blender 4.4


def results_scene(like: bpy.types.Scene) -> bpy.types.Scene:
    """A separate scene for AI videos/audio. Strips in the 3D scene's sequencer would REPLACE its
    camera in every render (Blender renders the sequencer when it has strips) — including the clay
    animation sent to ComfyQ. So results never go into the user's scene."""
    sc = bpy.data.scenes.get(RESULTS_SCENE) or bpy.data.scenes.new(RESULTS_SCENE)
    if sc.frame_end < like.frame_end:
        sc.frame_start, sc.frame_end = like.frame_start, like.frame_end
    sc.render.fps, sc.render.fps_base = like.render.fps, like.render.fps_base
    sc.render.resolution_x, sc.render.resolution_y = like.render.resolution_x, like.render.resolution_y
    return sc


def _free_channel(scene: bpy.types.Scene) -> int:
    strips = _strips(scene)
    return max((s.channel for s in strips), default=0) + 1


def remove_result_strips(scene: bpy.types.Scene) -> int:
    """Remove ComfyQ strips from a (3D) scene's sequencer; returns how many."""
    if scene.sequence_editor is None or scene.name == RESULTS_SCENE:
        return 0
    strips = _strips(scene)
    doomed = [s for s in strips if s.get("comfyq_job") or s.name.startswith("ComfyQ ")]
    for s in doomed:
        strips.remove(s)
    return len(doomed)


def import_video(scene: bpy.types.Scene, path: Path, job_id: str, workflow_id: str) -> str:
    target = results_scene(scene)
    strips = _strips(target)
    strip = strips.new_movie(name=f"ComfyQ {job_id[:8]}", filepath=str(path),
                             channel=_free_channel(target), frame_start=target.frame_start)
    strip["comfyq_job"], strip["comfyq_workflow"] = job_id, workflow_id
    clip = bpy.data.images.load(str(path), check_existing=True)     # movie image: the ComfyQ window's player
    tag(clip, job_id, workflow_id)
    from ..ui import player
    area = player.viewer_area()
    # Never take over the viewer while something plays (e.g. the clay animation you are checking):
    # the result waits on its job card (Show).
    if area is not None and not player.is_playing():
        player.show_media(area, clip)
    return f"video “{strip.name}” in scene “{target.name}” (channel {strip.channel})"


def import_audio(scene: bpy.types.Scene, path: Path, job_id: str, workflow_id: str) -> str:
    target = results_scene(scene)
    strip = _strips(target).new_sound(name=f"ComfyQ {job_id[:8]}", filepath=str(path),
                                      channel=_free_channel(target), frame_start=target.frame_start)
    strip["comfyq_job"], strip["comfyq_workflow"] = job_id, workflow_id
    return f"sound “{strip.name}” in scene “{target.name}”"


def _job_collection(scene: bpy.types.Scene, job_id: str) -> bpy.types.Collection:
    root = bpy.data.collections.get("ComfyQ") or bpy.data.collections.new("ComfyQ")
    if root.name not in scene.collection.children:
        scene.collection.children.link(root)
    name = f"ComfyQ {job_id[:8]}"
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in root.children:
        root.children.link(coll)
    return coll


@dataclass(frozen=True)
class Placement:
    """Where an imported 3D model / splat goes. Generators put it at the origin at their own scale
    (TRELLIS2: ~1 unit), i.e. inside whatever is there: by default it is set beside the scene instead."""
    where: str = "BESIDE"        # BESIDE (right of the scene's objects) | CURSOR | ORIGIN | ASIS (as the file says)
    on_ground: bool = True       # lowest point at z = 0 (the cursor's z for CURSOR)
    size: float = 0.0            # longest side in scene units; 0 = as made


DEFAULT_PLACEMENT = Placement()


def _bbox(objs) -> tuple[Vector, Vector] | None:
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type in ("MESH", "POINTCLOUD", "CURVE", "CURVES")
           for c in o.bound_box]
    if not pts:
        return None
    return (Vector([min(p[i] for p in pts) for i in range(3)]), Vector([max(p[i] for p in pts) for i in range(3)]))


def place(scene: bpy.types.Scene, new: list, placement: Placement) -> str:
    """Move / scale what an import created (its top-level objects) as `placement` says."""
    if placement.where == "ASIS" or not new:
        return ""
    bpy.context.view_layer.update()
    box = _bbox(new)
    if box is None:
        return ""
    lo, hi = box
    dims = hi - lo
    f = placement.size / max(dims) if placement.size > 0 and max(dims) > 0 else 1.0
    anchor = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z if placement.on_ground else (lo.z + hi.z) / 2))
    if placement.where == "CURSOR":
        target = scene.cursor.location.copy()
    else:
        target = Vector((0.0, 0.0, 0.0))
        others = _bbox([o for o in scene.objects if o not in new and o.visible_get()])
        if placement.where == "BESIDE" and others is not None:
            olo, ohi = others
            margin = max(0.5, 0.25 * max(dims * f))
            target = Vector((ohi.x + margin + dims.x * f / 2, (olo.y + ohi.y) / 2, 0.0))
        if not placement.on_ground:
            target.z = dims.z * f / 2
    m = Matrix.Translation(target) @ Matrix.Scale(f, 4) @ Matrix.Translation(-anchor)
    roots = [o for o in new if o.parent not in new]
    for o in roots:
        o.matrix_world = m @ o.matrix_world
    where = {"BESIDE": "beside the scene", "CURSOR": "at the 3D cursor", "ORIGIN": "at the origin"}[placement.where]
    return f", placed {where}" + (f", {max(dims) * f:.2f} long" if f != 1.0 else "")


def _import_objects(scene, op, path: Path, job_id: str, workflow_id: str,
                    placement: Placement = DEFAULT_PLACEMENT) -> str:
    before = set(bpy.data.objects)
    ctx = {}
    win = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
    if win is not None:
        ctx = {"window": win, "scene": scene}
    with bpy.context.temp_override(**ctx):
        op(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    coll = _job_collection(scene, job_id)
    for o in new:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
        tag(o, job_id, workflow_id)
    tag(coll, job_id, workflow_id)
    return f"{len(new)} object(s) in collection “{coll.name}”" + place(scene, new, placement)


def import_files(files: list[Path], job_id: str, workflow_id: str, scene: bpy.types.Scene | None = None,
                 placement: Placement = DEFAULT_PLACEMENT) -> list[str]:
    """Import what we can; return a short description per file."""
    scene = scene or bpy.context.scene
    done: list[str] = []
    for f in files:
        ext = f.suffix.lower()
        try:
            if ext in IMAGE_EXTS:
                done.append(f"image “{import_image(f, job_id, workflow_id).name}”")
            elif ext in VIDEO_EXTS:
                done.append(import_video(scene, f, job_id, workflow_id))
            elif ext in MODEL_EXTS:
                done.append(_import_objects(scene, bpy.ops.import_scene.gltf, f, job_id, workflow_id,
                                            placement))
            elif ext in PLY_EXTS:
                done.append(_import_objects(scene, bpy.ops.wm.ply_import, f, job_id, workflow_id, placement))
            elif ext in AUDIO_EXTS:
                done.append(import_audio(scene, f, job_id, workflow_id))
            else:
                done.append(f"{f.name} (kept in the job folder; no importer for {ext})")
        except (RuntimeError, AttributeError) as e:
            done.append(f"{f.name}: import failed ({e})")
    return done
