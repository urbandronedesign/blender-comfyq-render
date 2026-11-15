# SPDX-License-Identifier: GPL-3.0-or-later
"""Bring downloaded results into the .blend (main thread only).

image → Image Editor · video → Sequencer strip + Image Editor playback ·
GLB/glTF → its own collection · PLY / SPZ (Gaussian splats, point clouds) → its own collection ·
audio → Sequencer sound strip. Everything is tagged with its job and workflow.

Gaussian splats: Blender 5.3 imports them natively (`wm.ply_import` makes a PointCloud of type
GAUSSIAN_SPLAT, `wm.spz_import` reads .spz) and every engine renders them. Blender 5.2 has no splat
type: a splat .ply comes in as bare vertices and a .spz is left in the job folder.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".exr", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif"}
MODEL_EXTS = {".glb", ".gltf"}
PLY_EXTS = {".ply"}
SPZ_EXTS = {".spz"}
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}

# Blender 5.3's splat importers keep the file's axes, and ComfyQ's splats (TripoSplat, IDOL) are not Z-up:
# the .ply is in OpenCV axes (Y down, Z away from the camera) and comes in lying on its side; the .spz comes in
# upside down. Measured 2026-11-15 on an olive tree in a pot (pot → canopy direction, 262,144 splats): .ply up =
# -Y, .spz up = -Z, and ply = (x, z, -y) of spz exactly (residual 1e-4 on 1M splats). The fixes turn both into
# Blender's Z-up with the picture's camera looking from -Y, like the .glb mesh twin (glTF import converts).
PLY_SPLAT_FIX = (-math.pi / 2, 0.0, 0.0)
SPZ_FIX = (math.pi, 0.0, 0.0)


def native_splats() -> bool:
    """True when this Blender has the Gaussian splat point cloud type (5.3+)."""
    tp = bpy.types.PointCloud.bl_rna.properties.get("type")
    return tp is not None and "GAUSSIAN_SPLAT" in {i.identifier for i in tp.enum_items}


def is_splat(obj) -> bool:
    return obj.type == "POINTCLOUD" and getattr(obj.data, "type", "") == "GAUSSIAN_SPLAT"


def pick_files(files: list[Path]) -> list[Path]:
    """One splat per job: TripoSplat returns the same splat as .spz AND .ply. Keep the .ply (readable by
    5.2 too, as vertices) and drop its .spz twin; a lone .spz (IDOL) stays."""
    has_ply = any(f.suffix.lower() in PLY_EXTS for f in files)
    return [f for f in files if not (has_ply and f.suffix.lower() in SPZ_EXTS)]


def tag(idblock: bpy.types.ID, job_id: str, workflow_id: str) -> None:
    """Provenance on everything we import (no server address, no token)."""
    idblock["comfyq_job"] = job_id
    idblock["comfyq_workflow"] = workflow_id


def show_in_image_editor(img: bpy.types.Image) -> bool:
    """Show it in the ComfyQ viewer, or an Image Editor free for results (never one being painted, masked,
    pinned, or showing someone's own picture: audit 2, #6). False = nowhere to show it (it waits on its card)."""
    from ..ui import player
    area = player.viewer_area()
    if area is None:
        return False
    player.show_media(area, img)
    area.tag_redraw()
    return True


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
        if placement.where == "BESIDE":
            # never inside the camera's view: the next scene render would send the AI result back to ComfyQ
            target = _out_of_view(scene, target, dims * f, placement.on_ground, margin=max(0.5, 0.25 * max(dims * f)))
    m = Matrix.Translation(target) @ Matrix.Scale(f, 4) @ Matrix.Translation(-anchor)
    roots = [o for o in new if o.parent not in new]
    for o in roots:
        o.matrix_world = m @ o.matrix_world
    bpy.context.view_layer.update()
    for o in new:                      # where the import put it: moved by the student later = meant for the shot
        o[PLACED_KEY] = matrix_key(o)
    where = {"BESIDE": "beside the scene, out of the camera's view", "CURSOR": "at the 3D cursor",
             "ORIGIN": "at the origin"}[placement.where]
    return f", placed {where}" + (f", {max(dims) * f:.2f} long" if f != 1.0 else "")


PLACED_KEY = "comfyq_placed"


def matrix_key(obj) -> str:
    """The object's placement, rounded: compared with PLACED_KEY to tell whether the student moved it."""
    return ",".join(f"{x:.4f}" for row in obj.matrix_world for x in row)


def untouched_import(obj) -> bool:
    """An imported result still where the import put it. Such objects do not change the scene fingerprint, so
    importing a result does not make every scene-fed graph result out of date (audit 2, #4)."""
    return bool(obj.get("comfyq_job")) and obj.get(PLACED_KEY) == matrix_key(obj)


def _box_in_view(scene, centre: Vector, dims: Vector, on_ground: bool) -> bool:
    """Is any corner of the box (centre x/y, bottom on the ground or centred) in the scene camera's frame?"""
    from bpy_extras.object_utils import world_to_camera_view
    cam = scene.camera
    if cam is None:
        return False
    z0 = centre.z if on_ground else centre.z - dims.z / 2
    for dx in (-0.5, 0.5):
        for dy in (-0.5, 0.5):
            for dz in (0.0, 1.0):
                p = Vector((centre.x + dx * dims.x, centre.y + dy * dims.y, z0 + dz * dims.z))
                v = world_to_camera_view(scene, cam, p)
                if v.z > 0 and -0.05 <= v.x <= 1.05 and -0.05 <= v.y <= 1.05:
                    return True
    return False


def _out_of_view(scene, target: Vector, dims: Vector, on_ground: bool, margin: float) -> Vector:
    """The nearest spot to `target` (moving along ±X / ±Y) where the box is not in the camera's view."""
    if not _box_in_view(scene, target, dims, on_ground):
        return target
    step = max(dims.x, dims.y) + margin
    for k in range(1, 60):
        for d in (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, -1, 0)), Vector((-1, 0, 0))):  # -X: over the scene
            cand = target + d * step * k
            if not _box_in_view(scene, cand, dims, on_ground):
                return cand
    return target


def _import_objects(scene, op, path: Path, job_id: str, workflow_id: str,
                    placement: Placement = DEFAULT_PLACEMENT, rotate: tuple | None = None) -> str:
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
        fix = rotate if rotate is not None else (PLY_SPLAT_FIX if path.suffix.lower() in PLY_EXTS else None)
        if fix and o.parent is None and is_splat(o):
            # Object rotation only: Apply Transform does not handle splat scale / rotation / SH (5.3 notes).
            o.rotation_euler = fix
    tag(coll, job_id, workflow_id)
    splats = sum(is_splat(o) for o in new)
    what = (f"Gaussian splat ({len(new[0].data.points):,} splats)" if splats == len(new) == 1
            else f"{len(new)} object(s)")
    return f"{what} in collection “{coll.name}”" + place(scene, new, placement)


@dataclass
class ImportReport:
    """What an import did, per file: brought in, kept in the job folder (nothing to import it with), failed."""
    imported: list[str]
    kept: list[str]
    failed: list[str]

    @property
    def all(self) -> list[str]:
        return [*self.imported, *self.kept, *self.failed]

    def report_to(self, op) -> None:
        """Operator reports: failures as warnings, kept files as info (audit 2, #8: failures read "Imported")."""
        for m in self.imported:
            op.report({"INFO"}, f"Imported {m}")
        for m in self.kept:
            op.report({"INFO"}, m)
        for m in self.failed:
            op.report({"WARNING"}, m)


def import_report(files: list[Path], job_id: str, workflow_id: str, scene: bpy.types.Scene | None = None,
                  placement: Placement = DEFAULT_PLACEMENT) -> ImportReport:
    """Import what we can, saying for each file whether it came in, stayed in the job folder, or failed."""
    scene = scene or bpy.context.scene
    rep = ImportReport([], [], [])
    for f in pick_files(files):
        ext = f.suffix.lower()
        try:
            if ext in IMAGE_EXTS:
                rep.imported.append(f"image “{import_image(f, job_id, workflow_id).name}”")
            elif ext in VIDEO_EXTS:
                rep.imported.append(import_video(scene, f, job_id, workflow_id))
            elif ext in MODEL_EXTS:
                rep.imported.append(_import_objects(scene, bpy.ops.import_scene.gltf, f, job_id, workflow_id,
                                                    placement))
            elif ext in PLY_EXTS:
                rep.imported.append(_import_objects(scene, bpy.ops.wm.ply_import, f, job_id, workflow_id, placement))
            elif ext in SPZ_EXTS:
                if hasattr(bpy.ops.wm, "spz_import") and native_splats():
                    rep.imported.append(_import_objects(scene, bpy.ops.wm.spz_import, f, job_id, workflow_id,
                                                        placement, rotate=SPZ_FIX))
                else:
                    rep.kept.append(f"{f.name} is kept in the job folder: Blender 5.3 or later imports Gaussian "
                                    f"splats")
            elif ext in AUDIO_EXTS:
                rep.imported.append(import_audio(scene, f, job_id, workflow_id))
            else:
                rep.kept.append(f"{f.name} is kept in the job folder (Blender cannot import {ext} files)")
        except (RuntimeError, AttributeError) as e:
            rep.failed.append(f"Could not import {f.name}: {e}")
    return rep


def import_files(files: list[Path], job_id: str, workflow_id: str, scene: bpy.types.Scene | None = None,
                 placement: Placement = DEFAULT_PLACEMENT) -> list[str]:
    """Import what we can; return a short description per file (`import_report` says which kind each is)."""
    return import_report(files, job_id, workflow_id, scene, placement).all
