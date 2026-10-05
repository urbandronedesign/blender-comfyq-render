import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from client.params import specs_from_wire

_PATH = Path(__file__).resolve().parents[2] / "extension" / "comfyq_bridge" / "graph" / "presets.py"
_spec = importlib.util.spec_from_file_location("comfyq_graph_presets", _PATH)
presets = importlib.util.module_from_spec(_spec)
sys.modules["comfyq_graph_presets"] = presets
_spec.loader.exec_module(presets)

WORKFLOWS = Path("F:/ComfyQ/workflows")


@dataclass
class W:
    id: str
    category: str


def catalogue():
    wfs, inputs = [], {}
    for f in sorted(WORKFLOWS.glob("*/*.meta.json")):
        meta = json.loads(f.read_text(encoding="utf-8"))
        wfs.append(W(meta["id"], meta.get("category") or ""))
        specs = specs_from_wire(meta.get("exposedParameters") or [])
        inputs[meta["id"]] = [s.kind for s in specs if s.kind in ("image", "mask", "video", "audio")]
    return wfs, inputs


def test_pick_prefers_served_then_preference():
    role = presets.Role(("video-edit",), needs=("video",), prefer=("union", "canny"))
    wfs = [W("v_canny", "video-edit"), W("v_union", "video-edit"), W("v_other", "video-edit"), W("img", "t2i")]
    inputs = {"v_canny": ["video"], "v_union": ["video"], "v_other": ["video"], "img": []}
    assert presets.pick(role, wfs, inputs, served=set()) == "v_union"
    assert presets.pick(role, wfs, inputs, served={"v_other"}) == "v_other"
    assert presets.pick(role, wfs, inputs, served={"v_canny", "v_union"}) == "v_union"


def test_pick_needs_the_inputs_and_respects_avoid():
    role = presets.Role(("video-edit",), needs=("video", "image"), avoid=("animate",))
    wfs = [W("a", "video-edit"), W("b_animate", "video-edit"), W("c", "video-edit")]
    inputs = {"a": ["video"], "b_animate": ["video", "image"], "c": ["image", "video"]}
    assert presets.pick(role, wfs, inputs, served=set()) == "c"
    assert presets.pick(role, wfs, {"a": ["video"]}, served=set()) is None      # specs not loaded = no


def test_builtin_presets_are_consistent():
    for p in presets.BUILTIN:
        names = {n.name for n in p.nodes}
        assert len(names) == len(p.nodes), p.id
        for link in p.links:
            assert link.src in names and link.dst in names, (p.id, link)
        assert any(n.role for n in p.nodes), p.id


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="ComfyQ workflows not on this machine")
def test_every_builtin_role_finds_a_workflow_in_the_comfyq_catalogue():
    wfs, inputs = catalogue()
    picked = {}
    for p in presets.BUILTIN:
        for n in p.nodes:
            if n.role:
                wf = presets.pick(n.role, wfs, inputs, served=set())
                assert wf, (p.id, n.name)
                picked[(p.id, n.name)] = wf
    assert picked[("clay_v2v", "Video")] == "video_edit_ltx2_5_iclora_union_control"
    assert picked[("keyframe_v2v", "Keyframe")] == "image_qwen_image_2_1_control_depth"
    assert picked[("image_3d", "3D model")] == "3d_pixal3d_trellis2_image_to_model"   # TRELLIS2 alone removed upstream
    assert picked[("image_upscale", "Upscale")] == "utility_seedvr2_4k_upscale"   # int8 left a grid (arch tests)
    # what this classroom serves right now wins
    assert presets.pick(presets.BUILTIN[1].nodes[3].role, wfs, inputs,
                        served={"video_edit_ltx2_5_iclora_canny_control"}) == "video_edit_ltx2_5_iclora_canny_control"


def test_folders_this_pc_first_then_shared_once(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    monkeypatch.setenv("COMFYQ_BRIDGE_PRESETS", str(tmp_path / "env"))
    out = presets.folders([str(tmp_path / "pref"), "", str(tmp_path / "pref"), str(tmp_path / "profile")])
    assert out[0] == tmp_path / "appdata" / "comfyq-bridge" / "presets"
    assert out[1:] == [tmp_path / "pref", tmp_path / "profile", tmp_path / "env"]
