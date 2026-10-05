# SPDX-License-Identifier: GPL-3.0-or-later
"""Load a 3D Gaussian splat .ply (3DGS layout: x y z, f_dc_0..2, opacity, scale_0..2, rot_0..3) into Blender and
render it with Cycles. Blender 5.2 has no splat support, so each Gaussian becomes a point of a point cloud: colour
from the base spherical-harmonic band (0.5 + 0.2821 × f_dc), radius from its scale (exp), opacity (sigmoid) mixed
with transparency, emission shading (a splat carries its own baked light). An approximation: spheres, not Gaussian
falloff or view-dependent colour.

    blender -b --factory-startup --python tools/splat_render.py -- <splat.ply> <out prefix> [samples] [max points]
"""
import sys
import time

import bpy
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
PLY, OUT = argv[0], argv[1]
SAMPLES = int(argv[2]) if len(argv) > 2 else 64
MAX_POINTS = int(argv[3]) if len(argv) > 3 else 0


def read_splat(path):
    with open(path, "rb") as f:
        head = b""
        while not head.endswith(b"end_header\n"):
            head += f.readline()
        lines = head.decode("ascii").splitlines()
        n = next(int(ln.split()[2]) for ln in lines if ln.startswith("element vertex"))
        props = [ln.split()[2] for ln in lines if ln.startswith("property float")]
        data = np.fromfile(f, dtype=np.float32, count=n * len(props)).reshape(n, len(props))
    return {p: data[:, i] for i, p in enumerate(props)}, n


t0 = time.monotonic()
cols, n = read_splat(PLY)
alpha = 1 / (1 + np.exp(-cols["opacity"]))
keep = alpha > 0.05                                    # nearly invisible Gaussians only cost render time
if MAX_POINTS and keep.sum() > MAX_POINTS:
    idx = np.flatnonzero(keep)
    keep = np.zeros(n, bool)
    keep[np.random.default_rng(0).choice(idx, MAX_POINTS, replace=False)] = True
pos = np.stack([cols["x"], cols["y"], cols["z"]], 1)[keep]
rgb = np.clip(0.5 + 0.28209479 * np.stack([cols["f_dc_0"], cols["f_dc_1"], cols["f_dc_2"]], 1)[keep], 0, 1)
radius = np.exp(np.stack([cols["scale_0"], cols["scale_1"], cols["scale_2"]], 1)[keep]).mean(1)
radius = np.minimum(radius, np.percentile(radius, 99))  # a few huge background Gaussians would cover everything
alpha = alpha[keep]
m = len(pos)
print(f"SPLAT {n} Gaussians, {m} kept (opacity > 0.05), read in {time.monotonic() - t0:.1f} s", flush=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
mesh = bpy.data.meshes.new("splat")
mesh.vertices.add(m)
mesh.vertices.foreach_set("co", pos.astype(np.float32).ravel())
for name, arr, kind in (("splat_color", np.c_[rgb, np.ones(m)], "FLOAT_COLOR"), ("radius", radius, "FLOAT"),
                        ("splat_alpha", alpha, "FLOAT")):
    a = mesh.attributes.new(name, kind, "POINT")
    a.data.foreach_set("color" if kind == "FLOAT_COLOR" else "value", arr.astype(np.float32).ravel())
obj = bpy.data.objects.new("Gaussian splat", mesh)
sc.collection.objects.link(obj)

mat = bpy.data.materials.new("Splat")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
attr_c, attr_a = nt.nodes.new("ShaderNodeAttribute"), nt.nodes.new("ShaderNodeAttribute")
attr_c.attribute_name, attr_a.attribute_name = "splat_color", "splat_alpha"
emit, transp, mix = nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeBsdfTransparent"), nt.nodes.new("ShaderNodeMixShader")
out = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(attr_c.outputs["Color"], emit.inputs["Color"])
nt.links.new(attr_a.outputs["Fac"], mix.inputs["Fac"])
nt.links.new(transp.outputs[0], mix.inputs[1])
nt.links.new(emit.outputs[0], mix.inputs[2])
nt.links.new(mix.outputs[0], out.inputs["Surface"])
obj.data.materials.append(mat)

# Geometry nodes: mesh vertices → points with their own radius (Cycles renders points natively)
gn = bpy.data.node_groups.new("Splat to points", "GeometryNodeTree")
gn.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
gn.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
nin, nout = gn.nodes.new("NodeGroupInput"), gn.nodes.new("NodeGroupOutput")
to_pts = gn.nodes.new("GeometryNodeMeshToPoints")
rad = gn.nodes.new("GeometryNodeInputNamedAttribute")
rad.data_type, rad.inputs["Name"].default_value = "FLOAT", "radius"
gn.links.new(nin.outputs[0], to_pts.inputs["Mesh"])
gn.links.new(rad.outputs["Attribute"], to_pts.inputs["Radius"])
set_mat = gn.nodes.new("GeometryNodeSetMaterial")      # points made by nodes do not inherit the object's material
set_mat.inputs["Material"].default_value = mat
gn.links.new(to_pts.outputs["Points"], set_mat.inputs["Geometry"])
gn.links.new(set_mat.outputs["Geometry"], nout.inputs[0])
obj.modifiers.new("splat", "NODES").node_group = gn


# Cycles on the GPU when there is one
r = sc.render
r.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
    try:
        prefs.compute_device_type = kind
    except TypeError:
        continue
    prefs.get_devices()
    if any(d.type == kind for d in prefs.devices):
        for d in prefs.devices:
            d.use = d.type == kind
        sc.cycles.device = "GPU"
        break
sc.cycles.samples = SAMPLES
sc.cycles.transparent_max_bounces = 64
r.resolution_x = r.resolution_y = 900
r.image_settings.media_type = "IMAGE"
r.image_settings.file_format = "PNG"
world = bpy.data.worlds.new("bg")
world.color = (0.18, 0.18, 0.18)
sc.world = world

lo, hi = pos.min(0), pos.max(0)
centre = Vector(((lo + hi) / 2).tolist())
size = float(np.linalg.norm(hi - lo))
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
print(f"BOUNDS {np.round(lo, 2)} → {np.round(hi, 2)}", flush=True)
# splats are often Y-down (OpenCV); views around the vertical axis of the bounds, and one from each pole
for n_, d in enumerate([Vector((0, 0, 1)), Vector((0, 0, -1)), Vector((1, 0.4, 0.3)), Vector((-1, -0.4, 0.3)),
                        Vector((0.3, 1, 0.3)), Vector((0.3, -1, 0.3))]):
    cam.location = centre + d.normalized() * size * 1.6
    up = "Y" if abs(d.z) > 0.9 else "Z"
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", up).to_euler()
    r.filepath = f"{OUT}_view{n_}.png"
    t1 = time.monotonic()
    bpy.ops.render.render(write_still=True)
    print(f"RENDER view{n_} {time.monotonic() - t1:.1f} s", flush=True)
print("SPLAT DONE", flush=True)
