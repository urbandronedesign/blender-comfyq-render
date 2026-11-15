# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview pictures of the 3D results of a preset run (GLB meshes, Gaussian-splat PLY): each result imported into an
empty scene (splats through the plugin's importer, so its axis fix applies), framed by a camera at the front three
quarters, rendered with EEVEE, written next to results.json as previews/<n>.png; results.json gets "preview" (the
report shows it instead of the raw file).

    blender -b --python tools/series_3d_previews.py -- <results.json>      (Blender 5.3, sandbox profile)
"""
import json
import math
import sys
from pathlib import Path

import addon_utils
import bpy
from mathutils import Vector

RES = Path(sys.argv[sys.argv.index("--") + 1])
OUT = RES.parent / "previews"
OUT.mkdir(exist_ok=True)
addon_utils.enable("bl_ext.user_default.comfyq_bridge", default_set=True)
from bl_ext.user_default.comfyq_bridge.importers import media

recs = json.loads(RES.read_text(encoding="utf-8"))
for i, r in enumerate(recs):
    p = Path(r.get("result") or "")
    if p.suffix.lower() not in (".glb", ".ply", ".spz") or not p.is_file():
        continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    before = set(bpy.data.objects)
    if p.suffix.lower() == ".glb":
        bpy.ops.import_scene.gltf(filepath=str(p))
    else:
        spz = p.suffix.lower() == ".spz"
        (bpy.ops.wm.spz_import if spz else bpy.ops.wm.ply_import)(filepath=str(p))
        for o in set(bpy.data.objects) - before:
            o.rotation_euler = media.SPZ_FIX if spz else media.PLY_SPLAT_FIX
    obs = [o for o in set(bpy.data.objects) - before if o.type in ("MESH", "POINTCLOUD")]
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o in obs for c in o.bound_box]
    if not pts:
        continue
    lo = Vector((min(v.x for v in pts), min(v.y for v in pts), min(v.z for v in pts)))
    hi = Vector((max(v.x for v in pts), max(v.y for v in pts), max(v.z for v in pts)))
    ctr, size = (lo + hi) / 2, max((hi - lo).length, 1e-3)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    sc.collection.objects.link(cam)
    cam.location = ctr + Vector((0.9, -1.5, 0.6)).normalized() * size * 1.5
    cam.rotation_euler = (ctr - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 50
    sc.camera = cam
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sc.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    sun.data.energy = 3
    world = bpy.data.worlds.new("w")
    world.color = (0.35, 0.35, 0.37)
    sc.world = world
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = 960, 720
    sc.view_settings.view_transform = "Standard"
    out = OUT / f"{i:02d}.png"
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    faces = sum(len(o.data.polygons) for o in obs if o.type == "MESH")
    r["preview"] = str(out)
    r["preview_info"] = (f"{faces:,} faces" if faces else f"{sum(len(o.data.points) for o in obs):,} splats")
    print("PREVIEW", r["key"], r["preview_info"], flush=True)
RES.write_text(json.dumps(recs, indent=1, ensure_ascii=False), encoding="utf-8")
