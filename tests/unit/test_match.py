import importlib.util
import sys
from pathlib import Path

from client.params import spec_from_wire

# match.py sits in the extension package next to bpy code; load just that file.
_PATH = Path(__file__).resolve().parents[2] / "extension" / "comfyq_bridge" / "match.py"
_spec = importlib.util.spec_from_file_location("comfyq_match", _PATH)
match_mod = importlib.util.module_from_spec(_spec)
sys.modules["comfyq_match"] = match_mod     # dataclasses look the module up while building classes
_spec.loader.exec_module(match_mod)
SceneFacts, match, frame_for, short_label = (match_mod.SceneFacts, match_mod.match, match_mod.frame_for,
                                             match_mod.short_label)

# 1..120 @ 24 fps = 5 s, 16:9 camera
SCENE = SceneFacts(frame_start=1, frame_end=120, frame_current=40, fps=24.0, aspect=16 / 9)


def p(key, typ, label, **kw):
    return spec_from_wire({"key": key, "type": typ, "label": label, **kw})


def test_ltx_i2v_duration_fps_size():
    specs = [
        p("w", "number", "Width (px, multiple of 64 — your image is center-cropped to this shape)",
          default=1280, min=512, max=1920, step=64),
        p("h", "number", "Height (px, multiple of 64)", default=704, min=512, max=1920, step=64),
        p("d", "number", "Duration (seconds)", default=5, min=1, max=10),
        p("fps", "number", "Frame rate (fps)", default=24, min=8, max=30),
    ]
    m = match(specs, SceneFacts(1, 240, 1, 24.0, 16 / 9))
    assert m.values == {"w": 1280, "h": 704, "d": 10, "fps": 24}
    assert not m.warnings


def test_clip_length_warns_when_scene_is_longer():
    specs = [p("c", "number", "Clip length used (seconds, max 10 — only the start of your video is processed)",
               default=5, min=1, max=10)]
    m = match(specs, SceneFacts(1, 300, 1, 24.0, 16 / 9))   # 12.5 s
    assert m.values == {"c": 10}
    assert "only uses the first 10 s" in m.warnings[0]


def test_wan_frames_snap_to_4n_plus_1():
    specs = [p("n", "number", "Length (frames — 81 ≈ 3.4 s at 24 fps)", default=81, min=17, max=121)]
    assert match(specs, SCENE).values == {"n": 117}          # 120 → 4·29+1


def test_aspect_select_picks_closest():
    opts = ["1:1 (Square)", "4:3 (Standard)", "16:9 (Widescreen)", "9:16 (Portrait Widescreen)"]
    specs = [p("a", "select", "Aspect ratio (pick the one closest to your video)", options=opts,
               default="1:1 (Square)")]
    assert match(specs, SCENE).values == {"a": "16:9 (Widescreen)"}
    assert match(specs, SceneFacts(1, 10, 1, 24, 9 / 16)).values == {"a": "9:16 (Portrait Widescreen)"}


def test_size_limits_warn_about_crop():
    specs = [p("w", "number", "Width", default=896, min=320, max=1280),
             p("h", "number", "Height", default=512, min=320, max=1280)]
    m = match(specs, SceneFacts(1, 10, 1, 24, 4.0))           # very wide camera
    assert m.values["h"] == 320 and m.warnings


def test_unrelated_params_untouched():
    specs = [p("s", "number", "Seed", default=0, min=0, max=4294967295),
             p("k", "number", "Control strength (1 = follow the video closely)", default=1, min=0, max=2)]
    assert match(specs, SCENE).values == {}


def test_frame_for_still_inputs():
    assert frame_for(p("a", "image", "Start image (first frame)"), SCENE) == 1
    assert frame_for(p("b", "image", "Last frame — optional (the clip ends here)"), SCENE) == 120
    assert frame_for(p("c", "image", "Character image (who)"), SCENE) == 40
    assert frame_for(p("d", "image", "Key frame 4 (end)"), SCENE) == 120


def test_short_label():
    assert short_label("Clip length used (seconds, max 10 — only the start)") == "Clip length used"
    assert short_label("Edge threshold - low (lower keeps fainter edges)") == "Edge threshold"
    assert short_label("Duration in seconds") == "Duration in seconds"
