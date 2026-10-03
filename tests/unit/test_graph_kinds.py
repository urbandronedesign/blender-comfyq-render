import importlib.util
import json
import sys
from pathlib import Path

import pytest
from client.params import spec_from_wire

# graph/kinds.py sits next to bpy code; load just that file.
_PATH = Path(__file__).resolve().parents[2] / "extension" / "comfyq_bridge" / "graph" / "kinds.py"
_spec = importlib.util.spec_from_file_location("comfyq_graph_kinds", _PATH)
kinds = importlib.util.module_from_spec(_spec)
sys.modules["comfyq_graph_kinds"] = kinds
_spec.loader.exec_module(kinds)

WORKFLOWS = Path("F:/ComfyQ/workflows")


def p(key, typ, label, **kw):
    return spec_from_wire({"key": key, "type": typ, "label": label, **kw})


def test_inputs_become_sockets_settings_stay_chips():
    assert kinds.input_kind("image") == "image"
    assert kinds.input_kind("mask") == "mask"
    assert kinds.input_kind("video") == "video"
    assert kinds.input_kind("prompt") == "text"
    for setting in ("int", "float", "seed", "bool", "enum"):
        assert kinds.input_kind(setting) is None


@pytest.mark.parametrize(("category", "tags", "wf_id", "kind"), [
    ("t2i", (), "image_krea2_turbo", "image"),
    ("image-edit", (), "image_edit_flux2_klein", "image"),
    ("i2v", (), "video_ltx2_5_i2v", "video"),
    ("video-edit", (), "video_edit_ltx2_5_iclora_canny_control", "video"),
    ("3d", (), "3d_pixal3d_image_to_mesh", "model"),
    ("3d", ("gaussian",), "3d_triposplat", "splat"),
    ("audio", (), "audio_ace_step", "audio"),
    ("utility", (), "utility_video_frame_interpolation", "video"),
    ("utility", (), "utility_seedvr2_image_upscale", "image"),
    ("utility", (), "utility_describe_image", "text"),
])
def test_output_kind(category, tags, wf_id, kind):
    assert kinds.output_kind(category, tags, wf_id) == kind


def test_links_follow_kinds():
    assert kinds.can_link("image", "image")
    assert kinds.can_link("image", "mask")          # a mask input reads the alpha of any image
    assert not kinds.can_link("image", "video")
    assert not kinds.can_link("video", "image")
    assert kinds.can_link("text", "text")


def test_signature_changes_only_with_the_parameter_list():
    a = [p("prompt", "textarea", "Prompt"), p("video", "video", "Source video")]
    same = [p("prompt", "textarea", "Prompt"), p("video", "video", "Source video")]
    renamed = [p("prompt", "textarea", "Prompt"), p("video", "video", "Input video")]
    assert kinds.spec_signature(a) == kinds.spec_signature(same)
    assert kinds.spec_signature(a) != kinds.spec_signature(renamed)


@pytest.mark.skipif(not WORKFLOWS.is_dir(), reason="ComfyQ workflows not on this machine")
def test_every_comfyq_workflow_gets_a_node_shape():
    """All workflows of the local ComfyQ: every input maps to a socket or a chip, every output to a kind."""
    from client.params import specs_from_wire
    for meta_file in sorted(WORKFLOWS.glob("*/*.meta.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        specs = specs_from_wire(meta.get("exposedParameters") or [])
        out = kinds.output_kind(meta.get("category") or "", tuple(meta.get("tags") or ()), meta["id"])
        assert out in kinds.SOCKETS, meta["id"]
        for s in specs:
            sock = kinds.input_kind(s.kind)
            assert sock is None or sock in kinds.SOCKETS, (meta["id"], s.key, s.kind)


def test_output_nodes_take_any_result():
    for kind in ("image", "mask", "video", "audio", "text", "model", "splat"):
        assert kinds.can_link(kind, "any")
    assert not kinds.can_link("any", "image")       # an output node has no output
