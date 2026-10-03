# SPDX-License-Identifier: GPL-3.0-or-later
"""Class presets: ready-made graphs a teacher can start a class from. No bpy here (unit-tested).

Built-in presets name *roles* ("a video-to-video workflow that takes a video"), not workflow ids, so
they adapt to what the classroom's ComfyQ serves: `pick()` prefers served workflows, then the
preferred ids in order. Presets saved by the teacher (`Save Graph as Preset…`) are exact graphs in
JSON (see `preset_ops.py`), stored in the presets folder that every student PC reads.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = 1                     # version of saved preset files


@dataclass(frozen=True)
class Role:
    """Which workflow fills a workflow node of a preset."""
    categories: tuple[str, ...]
    needs: tuple[str, ...] = ()             # media input kinds it must take (repeat a kind to need two)
    prefer: tuple[str, ...] = ()            # id fragments, best first
    avoid: tuple[str, ...] = ()             # id fragments that disqualify


@dataclass(frozen=True)
class PNode:
    name: str
    type: str                               # node bl_idname ("ComfyQWorkflowNode" for a role)
    x: float
    y: float
    role: Role | None = None
    props: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PLink:
    src: str
    dst: str
    kind: str = ""                          # the target input's kind ("" = an output node's single input)
    nth: int = 0                            # which input of that kind (0 = the first)
    enable: bool = False                    # switch the input on if the workflow's own rule hides it


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    icon: str
    nodes: tuple[PNode, ...]
    links: tuple[PLink, ...]


_V2V = Role(("video-edit",), needs=("video",),
            prefer=("ltx2_5_iclora_union", "ltx2_5_iclora_canny", "ltx2_5_iclora_pose",
                    "minimax_h3_fun_controlnet_union", "minimax_h3_fun_controlnet_depth", "wan_idv2v_restyle",
                    "bernini_r_video_editing"),
            avoid=("character_replacement", "animate", "reference", "autoprompt"))
_V2V_START = Role(("video-edit",), needs=("video", "image"),
                  prefer=("ltx2_5_iclora_union", "ltx2_5_iclora_canny", "ltx2_5_iclora_pose", "wan_idv2v_restyle",
                          "ltx2_3_3dreal"),
                  avoid=("character_replacement", "animate", "reference", "autoprompt"))
_STILL = Role(("t2i", "image-edit"), needs=("image",),
              prefer=("qwen_image_2_1_control_depth", "flux2_klein_ref_control_depth", "qwen_image_2_1_control_canny",
                      "flux2_klein_9b_image_edit", "qwen_image_2_1_image_edit", "flux2_dev_image_edit",
                      "bernini_r_image_editing"),
              avoid=("inpaint", "character", "quadview", "angles", "style_reference", "edit_ref", "reference"))
_TO_3D = Role(("3d",), needs=("image",),
              prefer=("trellis2_image_to_textured_mesh", "pixal3d_image_to_mesh", "pixal3d_trellis2"),
              avoid=("multi_views", "splat", "gaussian"))
_UPSCALE = Role(("i2i", "preprocessor", "utility"), needs=("image",),
                prefer=("seedvr2_7b_int8_image_upscale", "seedvr2_4k_upscale", "pid_image_upscale"),
                avoid=("video", "depth", "normals", "segment"))

PROMPT = "ComfyQPromptNode"
WF = "ComfyQWorkflowNode"

BUILTIN: tuple[Preset, ...] = (
    Preset("render_image", "Scene render → image",
           "AI rendering of a still: the camera view in clay drives an image workflow (depth/edit). "
           "Viewer + Save", "IMAGE_DATA",
           (PNode("Render", "ComfyQSceneRenderNode", -820, 120, props={"frame": "CURRENT"}),
            PNode("Prompt", PROMPT, -820, -260),
            PNode("Image", WF, -380, 60, role=_STILL),
            PNode("Viewer", "ComfyQViewerNode", 120, 160),
            PNode("Save", "ComfyQSaveNode", 120, -200)),
           (PLink("Render", "Image", "image"), PLink("Prompt", "Image", "text"),
            PLink("Image", "Viewer"), PLink("Image", "Save"))),
    Preset("clay_v2v", "Clay animation → video",
           "The scene's animation rendered in clay, restyled by a video-to-video workflow (first frame as "
           "start image when the workflow uses one). Viewer + Save", "RENDER_ANIMATION",
           (PNode("Clay animation", "ComfyQSceneAnimationNode", -820, 200),
            PNode("First frame", "ComfyQSceneRenderNode", -820, -120, props={"frame": "START"}),
            PNode("Prompt", PROMPT, -820, -460),
            PNode("Video", WF, -380, 60, role=_V2V),
            PNode("Viewer", "ComfyQViewerNode", 120, 160),
            PNode("Save", "ComfyQSaveNode", 120, -200)),
           (PLink("Clay animation", "Video", "video"), PLink("First frame", "Video", "image"),
            PLink("Prompt", "Video", "text"), PLink("Video", "Viewer"), PLink("Video", "Save"))),
    Preset("keyframe_v2v", "AI keyframe → video",
           "The first frame is restyled by an image workflow, then starts a video-to-video run on the clay "
           "animation. One prompt feeds both, so the look stays the same. Viewers on both + Save",
           "SHADERFX",
           (PNode("First frame", "ComfyQSceneRenderNode", -1260, 160, props={"frame": "START"}),
            PNode("Prompt", PROMPT, -1260, -260),
            PNode("Keyframe", WF, -820, 120, role=_STILL),
            PNode("Clay animation", "ComfyQSceneAnimationNode", -820, -380),
            PNode("Video", WF, -340, 0, role=_V2V_START),
            PNode("Keyframe viewer", "ComfyQViewerNode", -340, 520),
            PNode("Viewer", "ComfyQViewerNode", 160, 160),
            PNode("Save", "ComfyQSaveNode", 160, -200)),
           (PLink("First frame", "Keyframe", "image"), PLink("Prompt", "Keyframe", "text"),
            PLink("Keyframe", "Video", "image", enable=True), PLink("Clay animation", "Video", "video"),
            PLink("Prompt", "Video", "text"), PLink("Keyframe", "Keyframe viewer"),
            PLink("Video", "Viewer"), PLink("Video", "Save"))),
    Preset("image_3d", "Image → 3D model",
           "An image (a drawing, a photo, an AI image) becomes a textured 3D model, imported into the scene "
           "as soon as it is ready", "MESH_MONKEY",
           (PNode("Image", "ComfyQImageNode", -760, 60),
            PNode("3D model", WF, -340, 60, role=_TO_3D),
            PNode("Import", "ComfyQImportNode", 140, 60, props={"auto": True})),
           (PLink("Image", "3D model", "image"), PLink("3D model", "Import"))),
    Preset("image_upscale", "Image → upscale",
           "An image upscaled (and sharpened) for print or a final frame. Viewer + Save", "FULLSCREEN_ENTER",
           (PNode("Image", "ComfyQImageNode", -760, 60),
            PNode("Upscale", WF, -340, 60, role=_UPSCALE),
            PNode("Viewer", "ComfyQViewerNode", 140, 200),
            PNode("Save", "ComfyQSaveNode", 140, -160)),
           (PLink("Image", "Upscale", "image"), PLink("Upscale", "Viewer"), PLink("Upscale", "Save"))),
)


def candidates(role: Role, workflows) -> list[str]:
    """Workflow ids of the right category, not ruled out by name (inputs are checked in `pick`)."""
    return [w.id for w in workflows
            if w.category in role.categories and not any(a in w.id for a in role.avoid)]


def pick(role: Role, workflows, inputs_of: dict[str, list[str]], served: set[str]) -> str | None:
    """Best workflow for a role: takes the needed inputs; served first, then by preference."""
    need = Counter(role.needs)
    ok = []
    for wf_id in candidates(role, workflows):
        have = inputs_of.get(wf_id)
        if have is None or Counter(have) & need != need:
            continue
        rank = next((i for i, frag in enumerate(role.prefer) if frag in wf_id), len(role.prefer))
        ok.append((wf_id not in served, rank, wf_id))
    return min(ok)[2] if ok else None


def folders() -> list[Path]:
    """Where saved presets are read from: the user's presets folder, and a shared one (e.g. on the
    teacher's PC) named by COMFYQ_BRIDGE_PRESETS."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    out = [base / "comfyq-bridge" / "presets"]
    shared = os.environ.get("COMFYQ_BRIDGE_PRESETS")
    if shared:
        out.append(Path(shared).expanduser())
    return out
