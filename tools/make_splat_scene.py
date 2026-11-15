# SPDX-License-Identifier: GPL-3.0-or-later
"""A Blender scene from a captured Gaussian splat (Blender 5.3+), with the camera placed EXACTLY where one of the
capture's photos was taken (COLMAP Images.txt / Cameras.txt): inside a captured scene the splats only look right
near the original viewpoints (from outside you see the floaters around it).

    blender -b --python tools/make_splat_scene.py -- <splat.ply> <colmap text folder> <photo name> <out.blend>

The splat and the camera are turned together from COLMAP's axes (y down) to Blender's Z-up. Cycles, resolution from
the photo's camera ×2, Standard view transform (the splat's colours as captured).
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector

PLY, COLMAP, PHOTO, OUT = sys.argv[sys.argv.index("--") + 1:][:4]
COLMAP = Path(COLMAP)


_PARAMS = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12}   # COLMAP camera model → params


def colmap_pose(name: str):
    """([qw, qx, qy, qz, tx, ty, tz], camera_id) of image `name`: binary images.bin (the model a splat is usually
    trained from) or text Images.txt."""
    import struct
    if COLMAP.joinpath("images.bin").is_file():
        with open(COLMAP / "images.bin", "rb") as f:
            for _ in range(struct.unpack("<Q", f.read(8))[0]):
                f.read(4)
                vals = list(struct.unpack("<7d", f.read(56)))
                cid = struct.unpack("<I", f.read(4))[0]
                raw = b""
                while (c := f.read(1)) != b"\0":
                    raw += c
                f.seek(struct.unpack("<Q", f.read(8))[0] * 24, 1)
                if raw.decode() == name:
                    return vals, cid
        raise SystemExit(f"{name} is not in images.bin")
    lines = [ln for ln in COLMAP.joinpath("Images.txt").read_text(encoding="utf-8", errors="replace").splitlines()
             if ln and not ln.startswith("#")]
    for ln in lines[::2]:                               # image line, then its 2D points line
        f = ln.split()
        if f[-1] == name:
            return [float(x) for x in f[1:8]], int(f[8])
    raise SystemExit(f"{name} is not in Images.txt")


def colmap_camera(cam_id: int):
    """(width, height, focal px) — the first focal of the model (fx for PINHOLE)."""
    import struct
    if COLMAP.joinpath("cameras.bin").is_file():
        with open(COLMAP / "cameras.bin", "rb") as f:
            for _ in range(struct.unpack("<Q", f.read(8))[0]):
                cid, model, w, h = struct.unpack("<iiQQ", f.read(24))
                params = struct.unpack("<" + "d" * _PARAMS[model], f.read(8 * _PARAMS[model]))
                if cid == cam_id:
                    return int(w), int(h), float(params[0])
        raise SystemExit(f"camera {cam_id} not in cameras.bin")
    for ln in COLMAP.joinpath("Cameras.txt").read_text(encoding="utf-8").splitlines():
        f = ln.split()
        if f and f[0] == str(cam_id):
            return int(f[2]), int(f[3]), float(f[4])
    raise SystemExit(f"camera {cam_id} not in Cameras.txt")


bpy.ops.wm.read_homefile(use_empty=True)
sc = bpy.context.scene
bpy.ops.wm.ply_import(filepath=PLY)
splat = sc.objects[0]
splat.name = "Captured splat"

(qw, qx, qy, qz, tx, ty, tz), cam_id = colmap_pose(PHOTO)
w, h, fpx = colmap_camera(cam_id)
R = Quaternion((qw, qx, qy, qz)).to_matrix()           # world → camera (OpenCV axes)
C = -(R.transposed() @ Vector((tx, ty, tz)))           # camera centre in world
cam_to_world = R.transposed() @ Matrix.Diagonal((1, -1, -1))   # OpenCV camera axes → Blender camera axes
cam_m = Matrix.Translation(C) @ cam_to_world.to_4x4()

Z_UP = Matrix.Rotation(-math.pi / 2, 4, "X")           # COLMAP y-down world → Blender z-up (as the plugin's importer)
splat.matrix_world = Z_UP @ splat.matrix_world
cam = bpy.data.objects.new("Camera (photo " + PHOTO + ")", bpy.data.cameras.new("Capture camera"))
sc.collection.objects.link(cam)
cam.matrix_world = Z_UP @ cam_m
cam.data.sensor_fit = "HORIZONTAL"
cam.data.sensor_width = 36.0
cam.data.lens = fpx * 36.0 / w
cam.data.clip_start, cam.data.clip_end = 0.05, 1000.0
sc.camera = cam
scale = 2 if w < 1200 else 1                           # about 2K wide
sc.render.resolution_x, sc.render.resolution_y = w * scale, h * scale
sc.render.engine = "CYCLES"
sc.cycles.samples = 64
sc.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("World")
world.color = (0.8, 0.8, 0.8)
sc.world = world
print("CAMERA", PHOTO, "at", tuple(round(v, 2) for v in cam.location), "lens", round(cam.data.lens, 2), "mm",
      "res", w * scale, h * scale, flush=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT)
print("SAVED", OUT, flush=True)
