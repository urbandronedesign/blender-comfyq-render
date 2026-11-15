# SPDX-License-Identifier: GPL-3.0-or-later
r"""Phase 2 spikes of UX audit 2 (docs/ux-audit-2-plan.md): can the plugin build Draw & Mask on Blender 5.3's own
tools? Headless, sandbox profile with the extension installed (run tools/smoke_test.ps1 first):

    $env:BLENDER_USER_RESOURCES = "$env:TEMP\comfyq_bridge_smoke_blender"
    blender -b --python tools/draw_mask_spikes.py -- <out folder>

  S1  Grease Pencil sketch in the camera view → the plugin's own clay pass (sketch over the scene, sketch only)
  S2  Mask data-block (box, circle, free shape, feather) → alpha, rasterized in numpy vs Blender's Compositor
  S3  an image whose alpha was painted (Erase Alpha) → the plugin's image_pass keeps the alpha (= ComfyQ mask)
  S4  a ComfyQ workspace made from Blender's own Texture Paint workspace, saved to a .blend, appended back
  S5  pixel hash of a painted 1920×1080 image: time per call (for "painting more refreshes the result")
Writes <out>/spikes.json + pictures.
"""
import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np

OUT = Path(sys.argv[sys.argv.index("--") + 1])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {}


def log(key, **data):
    REPORT[key] = data
    print("SPIKE", key, json.dumps(data, default=str), flush=True)


def px_of(img):
    a = np.empty(img.size[0] * img.size[1] * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(img.size[1], img.size[0], 4)


def save_png(arr, path):
    h, w = arr.shape[:2]
    im = bpy.data.images.new(path.stem, w, h, alpha=True)
    im.pixels.foreach_set(arr.astype(np.float32).ravel())
    im.filepath_raw, im.file_format = str(path), "PNG"
    im.save()
    bpy.data.images.remove(im)


# ---------------------------------------------------------------- scene: the default cube + camera
# (the sandbox has no startup.blend: the default scene. No read_factory_settings: it would drop the extension)
sc = bpy.context.scene
sc.render.resolution_x, sc.render.resolution_y = 1280, 720
cam = sc.camera

# ---------------------------------------------------------------- S1 Grease Pencil sketch → clay pass
try:
    from bl_ext.user_default.comfyq_bridge.passes import render
    gp = bpy.data.grease_pencils.new("ComfyQ Sketch")
    ob = bpy.data.objects.new("ComfyQ Sketch", gp)
    sc.collection.objects.link(ob)
    # in front of the camera, parented to it: drawn on a plane 2 m ahead (what Draw mode in camera view does)
    ob.parent = cam
    ob.location = (0.0, 0.0, -2.0)
    mat = bpy.data.materials.new("Sketch Ink")
    bpy.data.materials.create_gpencil_data(mat)
    mat.grease_pencil.color = (0.85, 0.1, 0.1, 1.0)
    gp.materials.append(mat)
    layer = gp.layers.new("Lines")
    drawing = layer.frames.new(sc.frame_current).drawing
    drawing.add_strokes([60, 40])
    for i, p in enumerate(drawing.strokes[0].points):      # a roof line
        t = i / 59
        p.position = (-0.6 + 1.2 * t, 0.25 + 0.25 * (1 - abs(2 * t - 1)), 0.0)
        p.radius, p.opacity = 0.012, 1.0
    for i, p in enumerate(drawing.strokes[1].points):      # a circle (a tree)
        a = 2 * math.pi * i / 39
        p.position = (0.7 + 0.15 * math.cos(a), -0.1 + 0.15 * math.sin(a), 0.0)
        p.radius, p.opacity = 0.012, 1.0
    drawing.strokes[1].cyclic = True
    t0 = time.time()
    clay = render.structure_pass(sc, "CLAY", 1280)
    t_clay = time.time() - t0
    # sketch only: everything else hidden from render, plain white world
    hidden = [o for o in sc.objects if o not in (ob, cam) and not o.hide_render]
    for o in hidden:
        o.hide_render = True
    world_col = tuple(sc.world.color)
    sc.world.color = (1.0, 1.0, 1.0)
    only = render.structure_pass(sc, "CLAY", 1280)
    for o in hidden:
        o.hide_render = False
    sc.world.color = world_col
    a = px_of(bpy.data.images.load(str(clay)))
    b = px_of(bpy.data.images.load(str(only)))
    def dark(x):
        return float(((x[..., :3].mean(-1)) < 0.25).mean())
    log("S1_sketch_pass", clay=str(clay), sketch_only=str(only), seconds=round(t_clay, 2),
        dark_px_with_scene=round(dark(a), 4), dark_px_sketch_only=round(dark(b), 4),
        grey=bool(np.abs(a[..., 0] - a[..., 1]).max() < 1e-3), note="look at both pictures")
except Exception as e:  # noqa: BLE001
    log("S1_sketch_pass", error=repr(e))

# ---------------------------------------------------------------- S2 Mask → alpha
def bezier_sample(p0, h0, h1, p1, n=16):
    t = np.linspace(0, 1, n, endpoint=False)[:, None]
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * h0 + 3 * (1 - t) * t * t * h1 + t ** 3 * p1


def raster_mask(mask, w, h, aspect):
    """Mask splines (normalised frame coords: x 0..1, y 0..1 with the frame's aspect) → coverage 0..1."""
    yy, xx = np.mgrid[0:h, 0:w]
    px = (xx + 0.5) / w
    # Blender's mask space: x 0..1 across the frame, y in the SAME units (width-normalised) around the centre:
    # frame_y = 0.5 + (y - 0.5) * w / h  →  y = 0.5 + (frame_y - 0.5) * h / w   (found by this spike)
    py = 0.5 + ((yy + 0.5) / h - 0.5) * h / w
    cover = np.zeros((h, w), np.float32)
    for layer in mask.layers:
        if layer.hide_render:
            continue
        lay = np.zeros((h, w), bool)
        for sp in layer.splines:
            pts = sp.points
            poly = []
            for i in range(len(pts)):
                a, b = pts[i], pts[(i + 1) % len(pts)]
                poly.append(bezier_sample(np.array(a.co), np.array(a.handle_right), np.array(b.handle_left),
                                          np.array(b.co)))
            poly = np.concatenate(poly)
            # even-odd fill
            inside = np.zeros((h, w), bool)
            x0, y0 = poly[:, 0], poly[:, 1]
            x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
            for ax, ay, bx, by in zip(x0, y0, x1, y1, strict=True):
                cond = (ay > py) != (by > py)
                xint = ax + (py - ay) * (bx - ax) / (by - ay + 1e-12)
                inside ^= cond & (px < xint)
            lay |= inside
        cover = np.maximum(cover, lay.astype(np.float32))
    return cover


try:
    m = bpy.data.masks.new("ComfyQ Zone")
    lay = m.layers.new(name="Zone")
    # a free shape: 5 points, with smooth handles
    sp = lay.splines.new()
    pts = [(0.2, 0.3), (0.45, 0.2), (0.55, 0.45), (0.4, 0.7), (0.18, 0.55)]
    sp.points.add(len(pts) - 1)
    for p, (x, y) in zip(sp.points, pts, strict=True):
        p.co = (x, y)
        p.handle_left = (x - 0.03, y)
        p.handle_right = (x + 0.03, y)
    sp.use_cyclic = True
    # a box
    sp2 = lay.splines.new()
    sp2.points.add(3)
    for p, (x, y) in zip(sp2.points, [(0.65, 0.2), (0.85, 0.2), (0.85, 0.5), (0.65, 0.5)], strict=True):
        p.co, p.handle_left, p.handle_right = (x, y), (x, y), (x, y)
    sp2.use_cyclic = True
    W, H = 1280, 720
    t0 = time.time()
    cov = raster_mask(m, W, H, W / H)
    t_np = time.time() - t0
    # Blender's own Compositor: a scene with a Mask node → Composite → render
    comp = bpy.data.scenes.new("mask raster")
    comp.render.resolution_x, comp.render.resolution_y = W, H
    comp.render.resolution_percentage = 100
    tree = None
    if hasattr(comp, "compositing_node_group"):
        tree = bpy.data.node_groups.new("mask comp", "CompositorNodeTree")
        comp.compositing_node_group = tree
    else:
        comp.use_nodes = True
        tree = comp.node_tree
    for n in list(tree.nodes):
        tree.nodes.remove(n)
    mn = tree.nodes.new("CompositorNodeMask")
    mn.mask = m
    out = None
    if hasattr(tree, "interface"):        # 5.x node-group compositor: a Group Output
        try:
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        except Exception as e:  # noqa: BLE001
            print("output socket:", e)
        out = tree.nodes.new("NodeGroupOutput")
        tree.links.new(mn.outputs[0], out.inputs[0])
    t0 = time.time()
    comp.render.filepath = str(OUT / "mask_compositor.png")
    with bpy.context.temp_override(scene=comp):
        bpy.ops.render.render(write_still=True, scene=comp.name)
    t_comp = time.time() - t0
    ref = px_of(bpy.data.images.load(str(OUT / "mask_compositor.png")))[..., 0]
    agree = float((np.abs((ref > 0.5).astype(np.float32) - cov) < 0.5).mean())
    rgba = np.zeros((H, W, 4), np.float32)
    rgba[..., :3] = 0.5
    rgba[..., 3] = 1.0 - cov                      # ComfyQ: transparent = repaint
    save_png(rgba, OUT / "mask_numpy_alpha.png")
    log("S2_mask_raster", numpy_seconds=round(t_np, 2), compositor_seconds=round(t_comp, 2),
        zone_fraction=round(float(cov.mean()), 4), agreement_with_compositor=round(agree, 4),
        note="compare mask_compositor.png and mask_numpy_alpha.png (y axis, aspect)")
except Exception as e:  # noqa: BLE001
    import traceback
    log("S2_mask_raster", error=repr(e), tb=traceback.format_exc()[-800:])

# ---------------------------------------------------------------- S3 painted alpha survives image_pass
try:
    from bl_ext.user_default.comfyq_bridge.passes import render
    img = bpy.data.images.new("Painted Frame", 1280, 720, alpha=True)
    a = np.ones((720, 1280, 4), np.float32)
    a[..., :3] = 0.6
    a[200:400, 300:700, 3] = 0.0                    # what Erase Alpha does
    img.pixels.foreach_set(a.ravel())
    p = render.image_pass(img, 1280)
    back = px_of(bpy.data.images.load(str(p)))
    log("S3_painted_alpha", path=str(p), alpha_zero_fraction=round(float((back[..., 3] < 0.01).mean()), 4),
        expected=round(200 * 400 / (1280 * 720), 4))
except Exception as e:  # noqa: BLE001
    log("S3_painted_alpha", error=repr(e))

# ---------------------------------------------------------------- S4 a ComfyQ workspace in a .blend
try:
    ws = bpy.data.workspaces.get("Texture Paint")
    log("S4_workspaces_in_startup", names=[w.name for w in bpy.data.workspaces],
        texture_paint_areas=[a.type for a in ws.screens[0].areas] if ws else None)
    if ws:
        cq = ws.copy() if hasattr(ws, "copy") else None
        log("S4_copy", copied=cq is not None)
except Exception as e:  # noqa: BLE001
    log("S4_workspace", error=repr(e))

# ---------------------------------------------------------------- S5 pixel hash cost
try:
    import hashlib
    img = bpy.data.images.new("hash probe", 1920, 1080, alpha=True)
    t0 = time.time()
    for _ in range(5):
        buf = np.empty(1920 * 1080 * 4, np.float32)
        img.pixels.foreach_get(buf)
        hashlib.sha1(buf[::97].tobytes()).hexdigest()
    log("S5_pixel_hash", ms_per_hash=round((time.time() - t0) / 5 * 1000, 1))
except Exception as e:  # noqa: BLE001
    log("S5_pixel_hash", error=repr(e))

(OUT / "spikes.json").write_text(json.dumps(REPORT, indent=1, default=str), encoding="utf-8")
print("DONE", flush=True)
