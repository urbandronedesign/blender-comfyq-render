# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene passes sent to ComfyQ as workflow inputs (main thread only).

- structure: a Workbench render from the scene camera (clay / matcap /
  material colors). Control workflows estimate depth or edges from it.
- mask: the structure render with the selected objects made transparent.
  ComfyQ's `mask` params are LoadImage nodes that read alpha:
  transparent = the area the model repaints.
- image: a Blender image datablock saved as PNG.

Every pass is sized to fit the param's maxInputEdge and ComfyQ's upload box
(1920×1080, either orientation). The user's render settings and object colors
are restored afterwards.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import bpy
import numpy as np

from ..runtime.session import cache_dir

UPLOAD_LONG, UPLOAD_SHORT = 1920, 1080
STYLES = (
    ("CLAY", "Grayscale (clay)",
     "Neutral grey studio shading on a neutral background, saved as grayscale — best for depth/edge control"),
    ("MATCAP", "Matcap", "Matcap shading — strong shape readability"),
    ("MATERIAL", "Material colors", "Workbench with material viewport colors"),
)


def fit_size(aspect: float, max_edge: int | None = None) -> tuple[int, int]:
    """Largest (w, h) with the given aspect inside ComfyQ's limits, multiple of 8."""
    long_cap = min(max_edge or UPLOAD_LONG, UPLOAD_LONG)
    short_cap = min(max_edge or UPLOAD_SHORT, UPLOAD_SHORT)
    landscape = aspect >= 1
    a = aspect if landscape else 1 / aspect
    long_ = min(long_cap, short_cap * a)
    short = long_ / a
    long_px, short_px = int(long_) // 8 * 8, max(8, int(short) // 8 * 8)
    return (long_px, short_px) if landscape else (short_px, long_px)


def scene_aspect(scene: bpy.types.Scene) -> float:
    r = scene.render
    return (r.resolution_x * r.pixel_aspect_x) / max(1, r.resolution_y * r.pixel_aspect_y)


def _out_path(kind: str) -> Path:
    d = cache_dir() / "passes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{time.strftime('%Y%m%d_%H%M%S')}_{kind}.png"


NEUTRAL_BG = (0.05, 0.05, 0.05)      # clay background: dark neutral grey, whatever the scene's world


@contextmanager
def _workbench(scene: bpy.types.Scene, size: tuple[int, int], style: str):
    r, sh, vs = scene.render, scene.display.shading, scene.view_settings
    ims = r.image_settings
    world = scene.world
    saved = dict(
        engine=r.engine, rx=r.resolution_x, ry=r.resolution_y, pct=r.resolution_percentage,
        path=r.filepath, media=getattr(ims, "media_type", None), fmt=ims.file_format, cmode=ims.color_mode,
        transparent=r.film_transparent, light=sh.light, color_type=sh.color_type,
        single=tuple(sh.single_color), studio=sh.studio_light, view=vs.view_transform, look=vs.look,
        world_color=tuple(world.color) if world else None,
        seq=r.use_sequencer, comp=r.use_compositing,
    )
    try:
        # Render the CAMERA, never the Video Sequencer's strips or the compositor: with strips in
        # the scene, Blender's final render outputs the sequencer instead of the 3D view.
        r.use_sequencer = False
        r.use_compositing = False
        r.engine = "BLENDER_WORKBENCH"
        r.resolution_x, r.resolution_y = size
        r.resolution_percentage = 100
        if saved["media"] is not None:          # Blender 5: a scene set to video output refuses PNG otherwise
            ims.media_type = "IMAGE"
        ims.file_format = "PNG"
        ims.color_mode = "RGB"
        r.film_transparent = False
        vs.view_transform = "Standard"
        vs.look = "None"
        if style == "MATCAP":
            sh.light, sh.color_type = "MATCAP", "SINGLE"
        elif style == "MATERIAL":
            sh.light, sh.color_type = "STUDIO", "MATERIAL"
        else:
            # Grayscale: grey objects, neutral studio light, neutral background, grayscale file.
            sh.light, sh.color_type = "STUDIO", "SINGLE"
            sh.single_color = (0.8, 0.8, 0.8)
            try:
                sh.studio_light = "Default"
            except TypeError:
                pass
            if world is not None:
                world.color = NEUTRAL_BG
            ims.color_mode = "BW"
        yield
    finally:
        r.use_sequencer, r.use_compositing = saved["seq"], saved["comp"]
        r.engine = saved["engine"]
        r.resolution_x, r.resolution_y, r.resolution_percentage = saved["rx"], saved["ry"], saved["pct"]
        r.filepath = saved["path"]
        if saved["media"] is not None:
            ims.media_type = saved["media"]
        ims.file_format = saved["fmt"]
        ims.color_mode = saved["cmode"]
        r.film_transparent = saved["transparent"]
        sh.light, sh.color_type = saved["light"], saved["color_type"]
        sh.single_color = saved["single"]
        if saved["studio"]:
            try:
                sh.studio_light = saved["studio"]
            except TypeError:
                pass
        vs.view_transform, vs.look = saved["view"], saved["look"]
        if world is not None and saved["world_color"] is not None:
            world.color = saved["world_color"]


def _render_to(scene: bpy.types.Scene, path: Path) -> Path:
    if scene.camera is None:
        raise RuntimeError("The scene has no active camera")
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    if not path.is_file():
        raise RuntimeError(f"Render did not write {path}")
    return path


@contextmanager
def _at_frame(scene: bpy.types.Scene, frame: int | None):
    if frame is None or frame == scene.frame_current:
        yield
        return
    saved = scene.frame_current
    scene.frame_set(frame)
    try:
        yield
    finally:
        scene.frame_set(saved)


def structure_pass(scene: bpy.types.Scene, style: str = "CLAY", max_edge: int | None = None,
                   frame: int | None = None) -> Path:
    size = fit_size(scene_aspect(scene), max_edge)
    with _at_frame(scene, frame), _workbench(scene, size, style):
        return _render_to(scene, _out_path("structure" if frame is None else f"structure_f{frame}"))


def mask_pass(scene: bpy.types.Scene, selected: list[bpy.types.Object], style: str = "CLAY",
              max_edge: int | None = None, frame: int | None = None) -> Path:
    """Structure RGB + alpha 0 where `selected` objects are visible (occlusion-correct)."""
    if not selected:
        raise RuntimeError("Select the object(s) to repaint")
    size = fit_size(scene_aspect(scene), max_edge)
    rgb_path = _out_path("mask_rgb")
    matte_path = _out_path("mask_matte")
    with _at_frame(scene, frame), _workbench(scene, size, style):
        _render_to(scene, rgb_path)
        sh, r = scene.display.shading, scene.render
        colors = {o.name: tuple(o.color) for o in scene.objects}
        chosen = {o.name for o in selected}
        try:
            # Flat object colors: selection white, everything else black, so an
            # occluder in front of the selection stays black. A transparent film
            # makes the (world-colored) background black in RGB too.
            sh.light, sh.color_type = "FLAT", "OBJECT"
            r.film_transparent = True
            r.image_settings.color_mode = "RGBA"
            for o in scene.objects:
                o.color = (1, 1, 1, 1) if o.name in chosen else (0, 0, 0, 1)
            _render_to(scene, matte_path)
        finally:
            for o in scene.objects:
                if o.name in colors:
                    o.color = colors[o.name]
    out = _out_path("mask")
    _merge_alpha(rgb_path, matte_path, out)
    return out


def _pixels(path: Path) -> tuple[np.ndarray, int, int]:
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        return buf.reshape(h, w, 4), w, h
    finally:
        bpy.data.images.remove(img)


def _merge_alpha(rgb_path: Path, matte_path: Path, out: Path) -> None:
    rgb, w, h = _pixels(rgb_path)
    matte, _, _ = _pixels(matte_path)
    selected = np.clip(matte[..., 0] * matte[..., 3], 0.0, 1.0)
    rgb[..., 3] = 1.0 - selected
    img = bpy.data.images.new("comfyq_mask_tmp", w, h, alpha=True)
    try:
        img.pixels.foreach_set(rgb.ravel())
        img.filepath_raw = str(out)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    rgb_path.unlink(missing_ok=True)
    matte_path.unlink(missing_ok=True)


def image_pass(image: bpy.types.Image, max_edge: int | None = None) -> Path:
    """Save a Blender image as a PNG within the upload limits (never modifies `image`)."""
    w, h = image.size
    if not w or not h:
        raise RuntimeError(f"Image “{image.name}” has no pixels (render it or reload it first)")
    tw, th = fit_size(w / h, max_edge)
    copy = image.copy()
    try:
        if (tw, th) != (w, h) and (w > tw or h > th):
            copy.scale(tw, th)
        out = _out_path("image")
        copy.filepath_raw = str(out)
        copy.file_format = "PNG"
        copy.save()
        return out
    finally:
        bpy.data.images.remove(copy)
