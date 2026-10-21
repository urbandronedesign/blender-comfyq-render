# SPDX-License-Identifier: GPL-3.0-or-later
"""Animation pass: the scene's frame range, seen through the scene camera, as an MP4.

This is what video-to-video workflows receive as their source video. It is
rendered with Workbench (seconds, not minutes) at the scene's aspect, sized to
the workflow input's maxInputEdge.

The last render is reused while it is still valid: same settings key and no
edit to objects, cameras, actions, materials… since it was made. Changes to the
Scene datablock alone (what our own render-setting swap produces) do not
invalidate it; frame range / fps / resolution changes are part of the key.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from ..runtime.session import cache_dir
from .render import (  # noqa: F401 - QUIET_S re-exported
    QUIET_S,
    _workbench,
    fit_size,
    is_own_change,
    own_changes,
    scene_aspect,
)


@dataclass
class AnimPass:
    path: Path
    key: tuple
    rendered_at: float
    frames: int
    fps: float
    size: tuple[int, int]

    @property
    def seconds(self) -> float:
        return self.frames / self.fps if self.fps else 0.0


_last: AnimPass | None = None
_last_edit = 0.0
last_edit_by = ""          # what the last counted edit was (for messages and tests)
edit_log: list[tuple] = []  # the last counted edits: (time, what, transform?, geometry?)


def scene_fps(scene: bpy.types.Scene) -> float:
    return scene.render.fps / (scene.render.fps_base or 1.0)


def frame_count(scene: bpy.types.Scene) -> int:
    return max(1, scene.frame_end - scene.frame_start + 1)


def settings_key(scene: bpy.types.Scene, style: str, max_edge: int | None) -> tuple:
    r = scene.render
    return (scene.name, scene.camera.name if scene.camera else None, scene.frame_start, scene.frame_end,
            r.fps, r.fps_base, r.resolution_x, r.resolution_y, r.pixel_aspect_x, r.pixel_aspect_y,
            style, max_edge)


def current(scene: bpy.types.Scene, style: str, max_edge: int | None) -> AnimPass | None:
    """The cached pass if it still matches the scene, else None."""
    p = _last
    if p and p.key == settings_key(scene, style, max_edge) and p.rendered_at >= _last_edit and p.path.is_file():
        return p
    return None


def last_edit() -> float:
    """When the user last edited something in the scene (our own renders excluded)."""
    return _last_edit


def last() -> AnimPass | None:
    return _last


def forget() -> None:
    """Drop the cached pass (its file may have been deleted)."""
    global _last
    _last = None


def animation_pass(scene: bpy.types.Scene, style: str = "CLAY", max_edge: int | None = None,
                   force: bool = False) -> AnimPass:
    global _last
    if not force and (cached := current(scene, style, max_edge)):
        return cached
    if scene.camera is None:
        raise RuntimeError("The scene has no active camera")
    size = fit_size(scene_aspect(scene), max_edge)
    size = (size[0] // 2 * 2, size[1] // 2 * 2)          # H.264 needs even dimensions
    out_dir = cache_dir() / "passes" / f"{time.strftime('%Y%m%d_%H%M%S')}_animation"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    r = scene.render
    ims = r.image_settings
    saved = dict(media=getattr(ims, "media_type", None), fmt=ims.file_format, ff_format=r.ffmpeg.format,
                 codec=r.ffmpeg.codec, crf=r.ffmpeg.constant_rate_factor, audio=r.ffmpeg.audio_codec)
    with own_changes(), _workbench(scene, size, style):
        try:
            if saved["media"] is not None:                 # Blender 5.0+: video is its own media type
                ims.media_type = "VIDEO"
            ims.file_format = "FFMPEG"
            ims.color_mode = "RGB"          # H.264 wants RGB frames; clay content is already neutral grey
            r.ffmpeg.format, r.ffmpeg.codec = "MPEG4", "H264"
            r.ffmpeg.constant_rate_factor = "HIGH"
            # Always a sound track: the scene's own sound, or silence. Some workflows keep the source
            # video's audio and refuse a clip without one (LTX 2.5 deblur & upscale).
            r.ffmpeg.audio_codec = "AAC"
            r.filepath = str(out_dir / "animation_")
            bpy.ops.render.render(animation=True, scene=scene.name)
        finally:
            if saved["media"] is not None:
                ims.media_type = saved["media"]
            ims.file_format = saved["fmt"]
            r.ffmpeg.format, r.ffmpeg.codec = saved["ff_format"], saved["codec"]
            r.ffmpeg.constant_rate_factor, r.ffmpeg.audio_codec = saved["crf"], saved["audio"]
    movies = sorted(out_dir.glob("*.mp4"))
    if not movies:
        raise RuntimeError(f"The animation render wrote no MP4 in {out_dir}")
    _last = AnimPass(path=movies[0], key=settings_key(scene, style, max_edge), rendered_at=time.time(),
                     frames=frame_count(scene), fps=scene_fps(scene), size=size)
    return _last


# Updates that never change what the camera sees: the Scene datablock itself (our own render-setting
# swaps), images and texts (results loaded into the viewer, prompts), UI data, and the ComfyQ graph
# (its nodes receive their results). Counting them made every finished job look like a scene edit.
_NOT_SCENE_CONTENT = tuple(t for t in (getattr(bpy.types, n, None) for n in (
    "Scene", "Image", "Text", "Screen", "WindowManager", "WorkSpace", "Brush", "Palette", "Library",
    "MovieClip", "Sound")) if t is not None)


def _is_scene_edit(update) -> bool:
    idblock = update.id
    if isinstance(idblock, _NOT_SCENE_CONTENT):
        return False
    if isinstance(idblock, bpy.types.NodeTree) and idblock.bl_idname == "ComfyQGraphTree":
        return False
    if isinstance(idblock, bpy.types.Object):
        # The UI can re-tag objects while it draws (seen live: "Object Cube", no transform, no
        # geometry, every few seconds). Moving, deforming or reshading an object sets one of these;
        # mesh/camera/material/action/collection edits arrive as their own (non-Object) updates.
        return bool(update.is_updated_transform or update.is_updated_geometry or update.is_updated_shading)
    return True


@persistent
def _on_depsgraph(scene, depsgraph):
    """An edit of the scene's content (objects, meshes, cameras, materials, animation…) makes the
    pass stale — and every graph result made from the scene."""
    global _last_edit, last_edit_by
    if is_own_change():
        return
    for u in depsgraph.updates:
        if _is_scene_edit(u):
            _last_edit = time.time()
            last_edit_by = f"{type(u.id).__name__} “{u.id.name}”"
            edit_log.append((round(_last_edit, 2), last_edit_by, bool(u.is_updated_transform),
                             bool(u.is_updated_geometry)))
            del edit_log[:-20]
            return


def register() -> None:
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister() -> None:
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
