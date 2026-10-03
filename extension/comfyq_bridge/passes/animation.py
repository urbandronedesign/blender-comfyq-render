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
from .render import _workbench, fit_size, scene_aspect


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
# Our own renders step frames and swap settings; updates right after them are not user edits.
_quiet_until = 0.0
QUIET_S = 1.5


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


def last() -> AnimPass | None:
    return _last


def forget() -> None:
    """Drop the cached pass (its file may have been deleted)."""
    global _last
    _last = None


def animation_pass(scene: bpy.types.Scene, style: str = "CLAY", max_edge: int | None = None,
                   force: bool = False) -> AnimPass:
    global _last, _quiet_until
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
    with _workbench(scene, size, style):
        try:
            if saved["media"] is not None:                 # Blender 5.0+: video is its own media type
                ims.media_type = "VIDEO"
            ims.file_format = "FFMPEG"
            ims.color_mode = "RGB"          # H.264 wants RGB frames; clay content is already neutral grey
            r.ffmpeg.format, r.ffmpeg.codec = "MPEG4", "H264"
            r.ffmpeg.constant_rate_factor = "HIGH"
            r.ffmpeg.audio_codec = "NONE"
            r.filepath = str(out_dir / "animation_")
            bpy.ops.render.render(animation=True, scene=scene.name)
        finally:
            if saved["media"] is not None:
                ims.media_type = saved["media"]
            ims.file_format = saved["fmt"]
            r.ffmpeg.format, r.ffmpeg.codec = saved["ff_format"], saved["codec"]
            r.ffmpeg.constant_rate_factor, r.ffmpeg.audio_codec = saved["crf"], saved["audio"]
    _quiet_until = time.time() + QUIET_S
    movies = sorted(out_dir.glob("*.mp4"))
    if not movies:
        raise RuntimeError(f"The animation render wrote no MP4 in {out_dir}")
    _last = AnimPass(path=movies[0], key=settings_key(scene, style, max_edge), rendered_at=time.time(),
                     frames=frame_count(scene), fps=scene_fps(scene), size=size)
    return _last


@persistent
def _on_depsgraph(scene, depsgraph):
    """Any edit to something other than the Scene datablock makes the pass stale."""
    global _last_edit
    if time.time() < _quiet_until:
        return
    for u in depsgraph.updates:
        if not isinstance(u.id, bpy.types.Scene):
            _last_edit = time.time()
            return


def register() -> None:
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister() -> None:
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
