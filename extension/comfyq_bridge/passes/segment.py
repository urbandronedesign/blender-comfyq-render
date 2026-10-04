# SPDX-License-Identifier: GPL-3.0-or-later
"""Segmentation helpers around ComfyQ's SAM3 workflow (main thread only).

ComfyQ's SAM3 workflow segments a VIDEO by name and returns a black-and-white mask video. For a still,
the picture is turned into a 3-frame MP4, and the middle frame of each mask video is read back as a PNG.
Both conversions go through a temporary scene's Video Sequencer (Blender's own FFmpeg), "Standard"
view transform so colours are not changed. Then: mask statistics, a colour overlay of all surfaces,
and RGBA repaint masks (transparent = the surface) for the inpaint workflows.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import bpy
import numpy as np

from ..runtime.session import cache_dir
from ..surfaces import Region

VIDEO_FRAMES = 3


def work_dir(name: str = "") -> Path:
    d = cache_dir() / "segment" / (name or time.strftime("%Y%m%d_%H%M%S"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _temp_scene(w: int, h: int, frames: int, fps: int = 24):
    sc = bpy.data.scenes.new("comfyq_segment_tmp")
    try:
        r = sc.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
        r.fps, r.fps_base = fps, 1.0
        sc.frame_start, sc.frame_end = 1, frames
        r.use_sequencer, r.use_compositing = True, False
        sc.view_settings.view_transform = "Standard"
        sc.view_settings.look = "None"
        sc.sequence_editor_create()
        yield sc
    finally:
        bpy.data.scenes.remove(sc)


def _strips(sc):
    ed = sc.sequence_editor
    return ed.strips if hasattr(ed, "strips") else ed.sequences


def _size(path: Path) -> tuple[int, int]:
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        return tuple(img.size)
    finally:
        bpy.data.images.remove(img)


def still_to_video(png: Path, out: Path, frames: int = VIDEO_FRAMES) -> Path:
    """A short MP4 that shows `png` on every frame (H.264 needs even sizes: rounded down)."""
    w, h = _size(png)
    w, h = w // 2 * 2, h // 2 * 2
    with _temp_scene(w, h, frames) as sc:
        s = _strips(sc).new_image("still", str(png), 1, 1)
        s.frame_final_duration = frames
        ims = sc.render.image_settings
        ims.media_type = "VIDEO"
        ims.file_format = "FFMPEG"
        ff = sc.render.ffmpeg
        ff.format, ff.codec, ff.constant_rate_factor = "MPEG4", "H264", "PERC_LOSSLESS"
        sc.render.filepath = str(out.with_suffix("")) + "_"
        bpy.ops.render.render(animation=True, scene=sc.name)
    made = sorted(out.parent.glob(out.with_suffix("").name + "_*.mp4"), key=lambda p: p.stat().st_mtime)
    if not made:
        raise RuntimeError("Could not write the video for SAM3")
    made[-1].replace(out)
    return out


def video_frame(video: Path, out: Path, frame: int = 2) -> Path:
    """Frame `frame` (1-based) of a video as a PNG, at the video's own size."""
    with _temp_scene(8, 8, VIDEO_FRAMES) as sc:
        m = _strips(sc).new_movie("mv", str(video), 1, 1)
        el = m.elements[0] if len(m.elements) else None
        w, h = (el.orig_width, el.orig_height) if el is not None and el.orig_width else _size_from_movie(video)
        sc.render.resolution_x, sc.render.resolution_y = w, h
        sc.frame_end = max(1, m.frame_final_duration)
        ims = sc.render.image_settings
        ims.media_type, ims.file_format, ims.color_mode = "IMAGE", "PNG", "RGB"
        sc.frame_set(min(frame, sc.frame_end))
        sc.render.filepath = str(out)
        bpy.ops.render.render(write_still=True, scene=sc.name)
    if not out.is_file():
        raise RuntimeError(f"Could not read a frame of {video.name}")
    return out


def _size_from_movie(video: Path) -> tuple[int, int]:
    clip = bpy.data.movieclips.load(str(video))
    try:
        return tuple(clip.size)
    finally:
        bpy.data.movieclips.remove(clip)


def read_rgba(path: Path) -> np.ndarray:
    """(h, w, 4) float32, row 0 = TOP of the picture."""
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        return buf.reshape(h, w, 4)[::-1].copy()
    finally:
        bpy.data.images.remove(img)


def write_rgba(arr: np.ndarray, out: Path) -> Path:
    h, w = arr.shape[:2]
    img = bpy.data.images.new("comfyq_segment_out", w, h, alpha=True)
    try:
        img.pixels.foreach_set(np.ascontiguousarray(arr[::-1], dtype=np.float32).ravel())
        img.filepath_raw = str(out)
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return out


def matte(path: Path) -> np.ndarray:
    """A mask PNG/frame as bool (h, w): white = the surface (video compression noise thresholded)."""
    px = read_rgba(path)
    return px[..., :3].mean(axis=2) > 0.5


def region(label: str, mask_png: Path) -> Region:
    m = matte(mask_png)
    h, w = m.shape
    cov = float(m.mean())
    if not m.any():
        return Region(label, 0.0, (0.0, 0.0, 0.0, 0.0), str(mask_png))
    ys, xs = np.nonzero(m)
    return Region(label, cov, (xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h), str(mask_png))


def union_masks(paths: list[Path], out: Path) -> Path:
    """One white-on-black mask: white wherever any of `paths` is white (resized to the first)."""
    first = matte(paths[0])
    h, w = first.shape
    acc = first.copy()
    for p in paths[1:]:
        acc |= _fit(matte(p), w, h)
    arr = np.zeros((h, w, 4), dtype=np.float32)
    arr[acc, :3] = 1.0
    arr[..., 3] = 1.0
    return write_rgba(arr, out)


# a distinct colour per label, in order (overlay + legend)
PALETTE = ((0.90, 0.30, 0.25), (0.25, 0.60, 0.95), (0.95, 0.80, 0.20), (0.30, 0.80, 0.40), (0.75, 0.40, 0.90),
           (0.95, 0.55, 0.15), (0.20, 0.85, 0.85), (0.95, 0.40, 0.70), (0.55, 0.35, 0.20), (0.60, 0.90, 0.20),
           (0.45, 0.45, 0.95), (0.85, 0.85, 0.85), (0.15, 0.50, 0.30), (0.95, 0.65, 0.55), (0.50, 0.20, 0.50))


def overlay(base: Path, regions: list[Region], out: Path, alpha: float = 0.55) -> Path:
    """The base picture with each found surface tinted in its colour (biggest first, so smaller
    surfaces stay visible on top of the ones they overlap)."""
    img = read_rgba(base)
    h, w = img.shape[:2]
    colours = {r.label: PALETTE[i % len(PALETTE)] for i, r in enumerate(regions)}
    for r in sorted((r for r in regions if r.found and r.mask), key=lambda r: -r.coverage):
        m = _fit(matte(Path(r.mask)), w, h)
        img[m, :3] = img[m, :3] * (1 - alpha) + np.array(colours[r.label], dtype=np.float32) * alpha
        r.extra["colour"] = "#" + "".join(f"{int(c * 255):02x}" for c in colours[r.label])
    img[..., 3] = 1.0
    return write_rgba(img, out)


def _fit(m: np.ndarray, w: int, h: int) -> np.ndarray:
    """Nearest-neighbour resize of a bool mask to (h, w)."""
    if m.shape == (h, w):
        return m
    ys = (np.arange(h) * m.shape[0] / h).astype(int)
    xs = (np.arange(w) * m.shape[1] / w).astype(int)
    return m[ys][:, xs]


def _grow(m: np.ndarray, px: int) -> np.ndarray:
    """Dilate (px > 0) or erode (px < 0) a bool mask by |px| pixels (square neighbourhood)."""
    if px == 0:
        return m
    src = m if px > 0 else ~m
    acc = src.copy()
    for axis in (0, 1):                              # separable: rows, then columns (no wrap-around)
        base = acc.copy()
        n = base.shape[axis]
        for k in range(1, min(abs(px), n - 1) + 1):
            if axis == 0:
                acc[k:] |= base[:-k]
                acc[:-k] |= base[k:]
            else:
                acc[:, k:] |= base[:, :-k]
                acc[:, :-k] |= base[:, k:]
    return acc if px > 0 else ~acc


def repaint_mask(image: Path, masks: list[Path], out: Path, grow_px: int = 6, invert: bool = False) -> Path:
    """`image` as RGBA with alpha 0 where any of `masks` is white (ComfyQ's mask params: transparent =
    repaint). Masks are resized to the image; `grow_px` widens them so the seams are repainted too."""
    img = read_rgba(image)
    h, w = img.shape[:2]
    sel = np.zeros((h, w), dtype=bool)
    for p in masks:
        sel |= _fit(matte(Path(p)), w, h)
    sel = _grow(sel, grow_px)
    if invert:
        sel = ~sel
    img[..., 3] = np.where(sel, 0.0, 1.0)
    return fit_upload(write_rgba(img, out))


def fit_upload(path: Path) -> Path:
    """Shrink a PNG in place to ComfyQ's upload box (1920×1080 either way); e.g. Qwen's 1920×1088."""
    from .render import UPLOAD_LONG, UPLOAD_SHORT, fit_size
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
        if max(w, h) <= UPLOAD_LONG and min(w, h) <= UPLOAD_SHORT:
            return path
        tw, th = fit_size(w / h)
        img.scale(tw, th)
        img.filepath_raw = str(path)
        img.file_format = "PNG"
        img.save()
        return path
    finally:
        bpy.data.images.remove(img)
