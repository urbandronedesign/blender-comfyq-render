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

import hashlib
import time
from contextlib import contextmanager
from pathlib import Path

import bpy
import numpy as np

from ..runtime.session import cache_dir

UPLOAD_LONG, UPLOAD_SHORT = 1920, 1080
STYLES = (
    ("CLAY", "Grayscale (Clay)",
     "Neutral grey studio shading on a neutral background, saved as grayscale — best for depth/edge control"),
    ("MATCAP", "Matcap", "Matcap shading — strong shape readability"),
    ("MATERIAL", "Material Colors", "Workbench with the materials' viewport colors"),
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


QUIET_S = 1.5
_OWN = {"depth": 0, "until": 0.0}


@contextmanager
def own_changes():
    """Everything changed inside (frame steps, render settings, temporary object colours) is ours,
    not a user edit of the scene: scene-fed results must not look out of date because of our own
    renders. The updates we caused are flushed before the bracket closes; a short tail covers any
    that arrive later."""
    _OWN["depth"] += 1
    try:
        yield
    finally:
        try:
            bpy.context.evaluated_depsgraph_get().update()
        except (AttributeError, RuntimeError):
            pass
        _OWN["depth"] -= 1
        _OWN["until"] = time.time() + QUIET_S


def is_own_change() -> bool:
    return _OWN["depth"] > 0 or time.time() < _OWN["until"]


def _fcurves(action):
    """F-curves of an action, layered (Blender 4.4+/5.x) or legacy."""
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                yield from bag.fcurves
    yield from getattr(action, "fcurves", ())


def scene_fingerprint(scene: bpy.types.Scene, style: str) -> str:
    """A hash of what a camera render of the scene depends on: camera, frame range, resolution, every
    renderable object's placement (keyframes when animated), mesh shape, modifiers, materials (Material
    style). Not the current frame, selection, custom properties or UI state — so neither scrubbing the
    timeline nor Mixar's own UI activity makes scene-fed graph results look out of date."""
    h = hashlib.sha1()

    def add(*v):
        h.update(repr(v).encode())

    r = scene.render
    add(style, scene.frame_start, scene.frame_end, r.fps, r.fps_base, r.resolution_x, r.resolution_y,
        r.pixel_aspect_x, r.pixel_aspect_y, scene.camera.name if scene.camera else None)
    actions, datas = set(), set()
    for ob in sorted(scene.objects, key=lambda o: o.name):
        if ob.hide_render:
            add(ob.name, "hidden")
            continue
        ad = ob.animation_data
        animated = ad is not None and ad.action is not None
        if animated:
            actions.add(ad.action)
        add(ob.name, ob.type, ob.parent.name if ob.parent else None, ob.data.name if ob.data else None,
            None if animated else [tuple(round(x, 5) for x in row) for row in ob.matrix_basis],
            [(m.name, m.type, m.show_render) for m in getattr(ob, "modifiers", ())],
            [(c.name, c.type, c.mute) for c in ob.constraints])
        if ob.data is not None:
            datas.add(ob.data)
        if style in ("MATERIAL", "CYCLES"):
            add([(sl.material.name, tuple(sl.material.diffuse_color)) for sl in ob.material_slots if sl.material])
        if style == "CYCLES" and ob.type == "LIGHT":
            add(ob.data.type, ob.data.energy, tuple(ob.data.color), getattr(ob, "is_shadow_catcher", False))
        elif style == "CYCLES":
            add(getattr(ob, "is_shadow_catcher", False))
    if style == "CYCLES" and scene.world is not None:      # the HDRI / world light the Cycles render
        w = scene.world
        nodes = w.node_tree.nodes if w.node_tree else []
        add(w.name, [(n.name, n.bl_idname, getattr(getattr(n, "image", None), "filepath", None),
                      [tuple(i.default_value) if hasattr(i.default_value, "__len__") else i.default_value
                       for i in n.inputs if hasattr(i, "default_value")]) for n in nodes])
    for data in sorted(datas, key=lambda d: (type(d).__name__, d.name)):
        dad = getattr(data, "animation_data", None)
        if dad is not None and dad.action is not None:
            actions.add(dad.action)
        if isinstance(data, bpy.types.Mesh):
            co = np.empty(len(data.vertices) * 3, dtype=np.float32)
            data.vertices.foreach_get("co", co)
            add(data.name, len(data.polygons), hashlib.sha1(co.tobytes()).hexdigest())
        elif isinstance(data, bpy.types.Camera):
            add(data.name, data.type, data.lens, data.sensor_width, data.sensor_fit, data.shift_x, data.shift_y,
                data.clip_start, data.clip_end, data.ortho_scale)
    for act in sorted(actions, key=lambda a: a.name):
        for fc in _fcurves(act):
            co = np.empty(len(fc.keyframe_points) * 2, dtype=np.float32)
            fc.keyframe_points.foreach_get("co", co)
            add(act.name, fc.data_path, fc.array_index, fc.mute, hashlib.sha1(co.tobytes()).hexdigest(),
                [k.interpolation for k in fc.keyframe_points])
    return h.hexdigest()[:16]


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


CYCLES_STYLE = ("CYCLES", "Cycles (final render)",
                ("A real Cycles render of the scene (materials, lights, HDRI, shadow catchers), made in a separate "
                "Blender process so the GPU is free again before the ComfyQ job starts"))
STILL_STYLES = (*STYLES, CYCLES_STYLE)       # stills only: clay animations and masks stay Workbench

# Runs in the separate Blender (factory startup: no add-ons, so no MCP server / ComfyQ connection there).
_CYCLES_SCRIPT = r'''
import bpy, sys
a = sys.argv[sys.argv.index("--") + 1:]
name, out, w, h, frame, samples = a[0], a[1], int(a[2]), int(a[3]), int(a[4]), int(a[5])
sc = bpy.data.scenes[name]
prefs = bpy.context.preferences.addons["cycles"].preferences
device = "CPU"
for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
    try:
        prefs.compute_device_type = kind
    except TypeError:
        continue
    prefs.get_devices()
    if any(d.type == kind for d in prefs.devices):
        for d in prefs.devices:
            d.use = d.type == kind
        device = "GPU"
        break
r = sc.render
r.engine = "CYCLES"
sc.cycles.device = device
sc.cycles.samples = samples
sc.cycles.use_denoising = True
r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
r.use_sequencer = r.use_compositing = False
r.image_settings.media_type = "IMAGE"
r.image_settings.file_format = "PNG"
r.image_settings.color_mode = "RGB"
sc.frame_set(frame)
r.filepath = out
bpy.ops.render.render(write_still=True, scene=sc.name)
print("COMFYQ_CYCLES", device, flush=True)
'''


def cycles_pass(scene: bpy.types.Scene, max_edge: int | None = None, frame: int | None = None,
                samples: int = 64, timeout_s: float = 1800) -> Path:
    """A Cycles render through the scene camera, in a separate background Blender: the scene is saved as a
    temporary copy (the open file is untouched) and rendered there. When that process exits its GPU
    memory is released, so a ComfyQ job on the same GPU starts with the card free. Blocks until done."""
    import subprocess
    import sys
    if scene.camera is None:
        raise RuntimeError("The scene has no active camera")
    w, h = fit_size(scene_aspect(scene), max_edge)
    work = cache_dir() / "passes" / "cycles"
    work.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    blend, script = work / f"scene_{stamp}.blend", work / "render_cycles.py"
    script.write_text(_CYCLES_SCRIPT, encoding="utf-8")
    out = _out_path("cycles" if frame is None else f"cycles_f{frame}")
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True, check_existing=False)
    cmd = [bpy.app.binary_path, "-b", "--factory-startup", str(blend), "--python", str(script), "--",
           scene.name, str(out), str(w), str(h), str(scene.frame_current if frame is None else frame), str(samples)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout_s, creationflags=flags, check=False)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Cycles render took more than {int(timeout_s)} s") from e
    finally:
        for p in (blend, blend.with_suffix(".blend1")):
            p.unlink(missing_ok=True)
    if not out.is_file():
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
        raise RuntimeError(f"Cycles render failed: {tail}")
    return out


def structure_pass(scene: bpy.types.Scene, style: str = "CLAY", max_edge: int | None = None,
                   frame: int | None = None, samples: int = 64) -> Path:
    if style == "CYCLES":
        return cycles_pass(scene, max_edge, frame, samples)
    size = fit_size(scene_aspect(scene), max_edge)
    with own_changes(), _at_frame(scene, frame), _workbench(scene, size, style):
        return _render_to(scene, _out_path("structure" if frame is None else f"structure_f{frame}"))


def mask_pass(scene: bpy.types.Scene, selected: list[bpy.types.Object], style: str = "CLAY",
              max_edge: int | None = None, frame: int | None = None) -> Path:
    """Structure RGB + alpha 0 where `selected` objects are visible (occlusion-correct)."""
    if not selected:
        raise RuntimeError("Select the object(s) to repaint")
    size = fit_size(scene_aspect(scene), max_edge)
    rgb_path = _out_path("mask_rgb")
    matte_path = _out_path("mask_matte")
    with own_changes(), _at_frame(scene, frame), _workbench(scene, size, style):
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
    # A new image filled with the pixels, not `image.copy()`: the copy of a file image (loaded by a
    # script, the MCP or from a saved .blend) can come without its pixel buffer ("does not have any
    # image data" at save). Reading `pixels` also loads a file image that nothing displayed yet.
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    copy = bpy.data.images.new("comfyq upload", w, h, alpha=True, float_buffer=image.is_float)
    try:
        copy.colorspace_settings.name = image.colorspace_settings.name
    except TypeError:
        pass
    copy.pixels.foreach_set(px)
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
