# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender 5.3+: import splat-workflow results through the plugin's importer (native Gaussian splats) and render
each splat from four sides in Cycles, side by side with its picture. For judging TripoSplat vs IDOL by looking.

    blender -b --python tools/splat_compare.py -- <out folder> <label>=<job folder> [<label>=<job folder> ...]

Needs the extension installed (sandbox profile: run tools/smoke_test.ps1 first, BLENDER_USER_RESOURCES set).
Writes <out>/<label>_<view>.png and <out>/splat_sheet.png.
"""
import math
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.importers import media
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(argv[0])
JOBS = [a.split("=", 1) for a in argv[1:]]
OUT.mkdir(parents=True, exist_ok=True)
VIEWS = (("front", 0), ("three_quarter", 45), ("side", 90), ("back", 180))

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples = 32
sc.cycles.device = "GPU"
sc.render.resolution_x = sc.render.resolution_y = 640
sc.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w")
world.color = (0.9, 0.9, 0.9)
sc.world = world
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
print("native splats:", media.native_splats(), flush=True)

for label, folder in JOBS:
    files = sorted(Path(folder).iterdir())
    before = set(bpy.data.objects)
    t0 = time.time()
    msgs = media.import_files(files, f"compare-{label}", label, sc, media.Placement("ORIGIN", True, 2.0))
    print(label, "import", round(time.time() - t0, 2), "s:", msgs, flush=True)
    new = [o for o in bpy.data.objects if o not in before]
    splats = [o for o in new if media.is_splat(o)]
    if not splats:
        print(label, "NO SPLAT", flush=True)
        continue
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:                                # the .glb twin, placed by its own import: put it on the splat
        o.location = splats[0].location
    bpy.context.view_layer.update()
    lo, hi = media._bbox(splats)
    ctr, size = (lo + hi) / 2, max(hi - lo)
    print(label, "splats", len(splats[0].data.points), "size", tuple(round(x, 2) for x in hi - lo), flush=True)
    for row, shown in ((label, splats), (f"{label}_mesh", meshes)):
        if not shown:
            continue
        for o in new:                               # one at a time: the splat, then its mesh twin
            o.hide_render = o not in shown
        for view, deg in VIEWS:
            a = math.radians(deg)
            cam.location = ctr + Vector((math.sin(a), -math.cos(a), 0.35)) * size * 2.2
            cam.rotation_euler = (ctr - cam.location).to_track_quat("-Z", "Y").to_euler()
            sc.render.filepath = str(OUT / f"{row}_{view}.png")
            t0 = time.time()
            bpy.ops.render.render(write_still=True)
            print(row, view, "rendered", round(time.time() - t0, 1), "s", flush=True)
    for o in new:
        bpy.data.objects.remove(o)

# sheet: a row per label (4 views), each splat row followed by its mesh twin's
rows = [r for lab, _f in JOBS for r in (lab, f"{lab}_mesh") if (OUT / f"{r}_front.png").is_file()]
if rows:
    w = h = 640
    sheet = bpy.data.images.new("sheet", w * len(VIEWS), h * len(rows))
    import numpy as np
    px = np.ones((h * len(rows), w * len(VIEWS), 4), dtype=np.float32)
    for r, lab in enumerate(rows):
        for c, (view, _d) in enumerate(VIEWS):
            im = bpy.data.images.load(str(OUT / f"{lab}_{view}.png"))
            a = np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4)
            px[(len(rows) - 1 - r) * h:(len(rows) - r) * h, c * w:(c + 1) * w] = a
    sheet.pixels.foreach_set(px.ravel())
    sheet.filepath_raw = str(OUT / "splat_sheet.png")
    sheet.file_format = "PNG"
    sheet.save()
    print("SHEET", OUT / "splat_sheet.png", "rows", rows, flush=True)
