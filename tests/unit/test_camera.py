import json
from pathlib import Path

import pytest
from client import camera
from client.params import MEDIA_KINDS, clamp, json_schema, spec_from_wire

# Shapes copied from ComfyQ's image_edit_qwen_2511_multiple_angles and 3d_mesh_remesh_decimate bundles.
CAMERA_SPEC = {
    "axes": {"horizontal": {"min": 0, "max": 360, "step": 1, "default": 180, "wrap": True},
             "vertical": {"min": -30, "max": 60, "step": 1, "default": 0},
             "zoom": {"min": 0, "max": 10, "step": 0.1, "default": 5}},
    "targets": [{"field": "horizontal_angle", "axis": "horizontal"}, {"field": "vertical_angle", "axis": "vertical"},
                {"field": "zoom", "axis": "zoom"}],
    "phrase": [
        {"axis": "horizontal", "sectors": ["front view", "front-right quarter view", "right side view",
                                           "back-right quarter view", "back view", "back-left quarter view",
                                           "left side view", "front-left quarter view"]},
        {"axis": "vertical", "terms": ["low-angle shot", "eye-level shot", "elevated shot", "high-angle shot"],
         "thresholds": [-15, 15, 45]},
        {"axis": "zoom", "terms": ["wide shot", "medium shot", "close-up"], "thresholds": [2, 6]},
    ],
}
CAMERA = {"key": "cameraangle_221", "nodeId": "221", "field": "horizontal_angle", "type": "camera",
          "label": "Where the camera stands", "default": '{"horizontal": 180, "vertical": 0, "zoom": 5}',
          "camera": CAMERA_SPEC, "required": False, "order": 1, "enabled": True}
MESH = {"key": "load3dadvanced_model_file_6", "nodeId": "6", "field": "model_file", "type": "model3d",
        "label": "3D model (GLB, GLTF, OBJ, STL, PLY, FBX)", "default": "", "required": True, "order": 0}

# ComfyUI's describe_camera_angle over a 5110-point grid, committed by ComfyQ (read when present).
FIXTURE = Path(r"F:\ComfyQ\server\workers\cameraAngle.fixture.json")


def test_kinds():
    assert spec_from_wire(MESH).kind == "model"
    assert "model" in MEDIA_KINDS and spec_from_wire(MESH).is_media
    cam = spec_from_wire(CAMERA)
    assert cam.kind == "camera" and not cam.is_media
    assert cam.extra["camera"]["axes"]["horizontal"]["default"] == 180


def test_parse_wraps_clamps_and_defaults():
    assert camera.parse('{"horizontal": 370, "vertical": 99, "zoom": -1}', CAMERA_SPEC) == \
        {"horizontal": 10, "vertical": 60, "zoom": 0}
    assert camera.parse({"horizontal": -10}, CAMERA_SPEC)["horizontal"] == 350
    assert camera.parse("not json", CAMERA_SPEC) == {"horizontal": 180, "vertical": 0, "zoom": 5}
    assert camera.parse(None, None) == {"horizontal": 0, "vertical": 0, "zoom": 5}


def test_clamp_sends_comfyq_format():
    spec = spec_from_wire(CAMERA)
    assert json.loads(clamp(spec, '{"horizontal": 90.4, "vertical": 12.6, "zoom": 6.66}')) == \
        {"horizontal": 90, "vertical": 13, "zoom": 6.7}
    assert "horizontal" in json_schema([spec])["properties"]["cameraangle_221"]["description"]


def test_describe_matches_comfyui():
    d = lambda h, v, z: camera.describe(CAMERA_SPEC, {"horizontal": h, "vertical": v, "zoom": z})
    assert d(0, 0, 5) == "front view eye-level shot medium shot"
    assert d(22.4, -15, 2) == "front view eye-level shot medium shot"
    assert d(22.5, -16, 1.9) == "front-right quarter view low-angle shot wide shot"
    assert d(337.5, 45, 6) == "front view high-angle shot close-up"
    assert d(270, 30, 10) == "left side view elevated shot close-up"


@pytest.mark.skipif(not FIXTURE.is_file(), reason="ComfyQ's fixture is not on this PC")
def test_describe_matches_comfyui_fixture():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    wrong = [r for r in rows
             if camera.describe(CAMERA_SPEC, {"horizontal": r["h"], "vertical": r["v"], "zoom": r["z"]}) != r["phrase"]]
    assert len(rows) > 1000 and not wrong[:5]


def test_suffix_when():
    spec = {"phrase": [{"axis": "vertical", "terms": ["eye-level", "elevated"], "thresholds": [15],
                        "suffixWhen": {"axis": "zoom", "min": 6, "suffix": " close-up"}}]}
    assert camera.describe(spec, {"horizontal": 0, "vertical": 30, "zoom": 7}) == "elevated close-up"
    assert camera.describe(spec, {"horizontal": 0, "vertical": 30, "zoom": 5}) == "elevated"


def test_from_view():
    target, radius = (0.0, 0.0, 0.0), 1.0
    front = (0.0, -10.0, 0.0)                         # the scene camera, on Blender's front (-Y) side
    # same side as the scene camera, 8 radii away, level: front view, medium shot
    cam = camera.from_view((0.0, -8.0, 0.0), target, radius, front)
    assert (cam["horizontal"], cam["vertical"], cam["zoom"]) == (0, 0, 5)
    # moving to the picture's right (+X seen from -Y) is the right side (90), behind is the back (180)
    assert round(camera.from_view((8.0, 0.0, 0.0), target, radius, front)["horizontal"]) == 90
    assert round(camera.from_view((0.0, 8.0, 0.0), target, radius, front)["horizontal"]) == 180
    assert round(camera.from_view((-8.0, 0.0, 0.0), target, radius, front)["horizontal"]) == 270
    # angles are measured from the scene camera, not the world: a camera on +X makes +X the front
    assert round(camera.from_view((8.0, 0.0, 0.0), target, radius, (10.0, 0.0, 0.0))["horizontal"]) == 0
    # 45 degrees up; far away = wide (clamped to 0), very close = close-up (clamped to 10)
    assert round(camera.from_view((0.0, -5.0, 5.0), target, radius, front)["vertical"]) == 45
    assert camera.from_view((0.0, -50.0, 0.0), target, radius, front)["zoom"] == 0
    assert camera.from_view((0.0, -2.0, 0.0), target, radius, front)["zoom"] == 10
    # the bundle's own limits apply (vertical at most 60)
    assert camera.from_view((0.0, -0.1, 9.0), target, radius, front, CAMERA_SPEC)["vertical"] == 60
