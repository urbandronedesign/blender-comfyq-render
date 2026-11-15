import importlib.util
import sys
from pathlib import Path

# zone.py sits next to bpy code; load just that file.
_PATH = Path(__file__).resolve().parents[2] / "extension" / "comfyq_bridge" / "zone.py"
_spec = importlib.util.spec_from_file_location("comfyq_zone", _PATH)
zone = importlib.util.module_from_spec(_spec)
sys.modules["comfyq_zone"] = zone
_spec.loader.exec_module(zone)


def test_round_trip_and_bad_text():
    z = zone.Zone([zone.box((0.1, 0.2), (0.4, 0.6)), zone.box((0.2, 0.3), (0.3, 0.4), "sub")])
    back = zone.loads(zone.dumps(z))
    assert [s.op for s in back.shapes] == ["add", "sub"] and back.shapes[0].points[2] == (0.4, 0.6)
    assert zone.loads("").empty and zone.loads("not json").empty and zone.loads('[{"points": [[0, 0]]}]').empty


def test_region_frame_conversion():
    lo, hi = (100.0, 50.0), (900.0, 500.0)              # the camera frame as drawn in the 3D View region
    assert zone.to_frame(100, 50, lo, hi) == (0.0, 0.0)
    assert zone.to_frame(500, 275, lo, hi) == (0.5, 0.5)
    fx, fy = zone.to_frame(260, 140, lo, hi)
    assert zone.to_region(fx, fy, lo, hi) == (260.0, 140.0)


def test_box_is_clamped_and_a_click_is_not_a_zone():
    s = zone.box((-0.2, 0.5), (0.3, 1.4))
    assert s.points == [(0.0, 0.5), (0.3, 0.5), (0.3, 1.0), (0.0, 1.0)]
    assert zone.box((0.5, 0.5), (0.501, 0.7)) is None


def test_lasso_simplified():
    import math
    path = [(0.5 + 0.2 * math.cos(t / 2000 * 6.2832), 0.5 + 0.2 * math.sin(t / 2000 * 6.2832)) for t in range(2000)]
    s = zone.lasso(path)
    assert s is not None and 3 <= len(s.points) <= zone.MAX_POINTS
    assert abs(abs(zone.area(s.points)) - math.pi * 0.04) < 0.01        # still the circle
    assert zone.lasso([(0.1, 0.1), (0.1005, 0.1), (0.1, 0.1004)]) is None


def test_contains_applies_shapes_in_order():
    z = zone.Zone([zone.box((0.1, 0.1), (0.9, 0.9)), zone.box((0.4, 0.4), (0.6, 0.6), "sub"),
                   zone.box((0.45, 0.45), (0.5, 0.5))])
    assert zone.contains(z, 0.2, 0.2)
    assert not zone.contains(z, 0.55, 0.55)               # cut out
    assert zone.contains(z, 0.47, 0.47)                   # added back on top of the cut
    assert not zone.contains(z, 0.95, 0.5)


def test_describe_and_grow():
    z = zone.Zone([zone.box((0.1, 0.1), (0.2, 0.2)), zone.box((0.1, 0.1), (0.15, 0.15), "sub")])
    assert zone.describe(2, z) == "2 objects · 1 shape · 1 cut"
    assert zone.describe(0, zone.Zone()) == "Empty"
    assert zone.grow_px(0.02, 1280) == 26
