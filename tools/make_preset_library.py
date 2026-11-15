# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a library of class presets: one ready-made graph per image / video workflow of ComfyQ.

Runs headless inside Blender with the extension installed (sandbox profile), connects to ComfyQ, and
for every workflow of the chosen categories builds the graph with the extension's own nodes:

    scene sources (clay render / mask / clay animation / file nodes) → the workflow → Viewer + Save

then saves it with `preset_ops.serialize` into `<out>/<group>/<name>.json` (a group = a submenu of
Presets). Workflow nodes are saved with `"match_scene": true` and no settings: they keep the
workflow's defaults and take duration / size / fps from the scene they are added to.

    blender -b --online-mode --python tools/make_preset_library.py -- <out folder> [categories]
"""
import json
import sys
import time
from pathlib import Path

import bpy
from bl_ext.user_default.comfyq_bridge.graph import preset_ops
from bl_ext.user_default.comfyq_bridge.graph.tree import WORKFLOW_NODE, socket_kind
from bl_ext.user_default.comfyq_bridge.ops.workflow import request_specs
from bl_ext.user_default.comfyq_bridge.runtime.session import SESSION

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(argv[0] if argv else "preset_library")
GROUPS = {                         # ComfyQ category -> submenu of Presets
    "t2i": "Image",
    "image-edit": "Image edit",
    "i2v": "Image → video",
    "video-edit": "Video → video",
    "i2i": "Upscale",              # picture upscalers (SeedVR2 4K / 7B, PiD, Qwen unblur)
    "preprocessor": "Upscale",     # video tools: only the upscalers (RelayVSR, SeedVR2 HD / 3B, LTX 2×)
}
UPSCALE_CATEGORIES = ("i2i", "preprocessor")
if len(argv) > 1:
    GROUPS = {k: v for k, v in GROUPS.items() if k in argv[1].split(",")}


def wanted(w) -> bool:
    return w.category in GROUPS and (w.category not in UPSCALE_CATEGORIES or "upscale" in w.id)
FILE_SAFE = str.maketrans({c: "-" for c in '<>:"/\\|?*→'})


def pump_until(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        SESSION.pump()
        if pred():
            return True
        time.sleep(0.05)
    return False


SESSION.connect("", "preset-library")
if not pump_until(lambda: SESSION.connected and SESSION.workflows, 30):
    sys.exit(f"Cannot connect to ComfyQ: {SESSION.error or SESSION.status}")
wfs = [w for w in SESSION.workflows if wanted(w) and not w.unavailable]
for w in wfs:
    request_specs(w.id)
if not pump_until(lambda: all(w.id in SESSION.specs for w in wfs), 120):
    sys.exit("Parameters did not load: " + ", ".join(w.id for w in wfs if w.id not in SESSION.specs))

scene = bpy.context.scene
COL_SRC, COL_WF, COL_OUT, STEP = -820.0, -380.0, 120.0, 330.0


PICK_A_FILE = ("reference", "style", "character", "portrait", "subject", "sheet", "stylized")

# Workflows the label rules get wrong (found by the preset series of 2026-11-15):
#   inputs     socket key -> (node type, label, props), or None to leave it free
#   prompt     the Prompt node's text instead of ComfyQ's example
#   prompt_to  more text sockets fed by the same Prompt node
#   steps      a How to use note (lines under ~45 characters, gotcha 43)
WAN_MOTION = ("Smooth, continuous camera move from one key frame to the next. The scene, the buildings and the "
              "materials stay exactly the same; no cuts, no new objects, natural light.")
OVERRIDES = {
    # three motion prompts, one per segment: only the first was linked, the other two kept ComfyQ's example
    # (a red line-art capoeira dancer, who then flew across the clip)
    "video_wan22_4_key_frames": {
        "prompt": WAN_MOTION,
        "prompt_to": ["cliptextencode_text_228_225", "cliptextencode_text_245_242"],
        "steps": ["1. Key frames 1 and 4 come from the scene", "2. Pick pictures for key frames 2 and 3",
                  "3. The Prompt describes the motion", "   (used for all three segments)"],
    },
    # Image 2 is the object; Image 3 an optional lighting reference ("reference" in its label took the file node)
    "image_edit_qwen_2_1_relight_composite": {
        "inputs": {"loadimage_image_475": ("ComfyQImageNode", "Object to place", {}), "loadimage_image_476": None},
        "prompt": ("Place the object from <image2> into the scene of <image1>, standing naturally in the scene at a "
                   "believable size. Apply consistent lighting to the object, enhance specular highlights, keep the "
                   "front-back spatial relationship, add a natural contact shadow, and blend it seamlessly into the "
                   "background."),
        "steps": ["1. The scene render is the background", "   (<image1> in the prompt)",
                  "2. Pick the object picture (<image2>)", "3. Say where the object goes in the Prompt"],
    },
    # plain text comes back as a grey "Image blocked by safety filter" picture: keep ComfyQ's JSON example
    "image_ideogram4_t2i": {
        "steps": ["1. The prompt must stay in this JSON form", "2. Change only the texts in quotes",
                  "3. bbox = [top, left, bottom, right],", "   each from 0 to 1000",
                  "4. Plain text gives a grey blocked picture"],
    },
}


def short(label: str) -> str:
    """'Reference image (what to insert)' -> 'Reference image'."""
    return label.split("(")[0].split(" - ")[0].split("—")[0].strip(" –-") or label


def source_for(kind: str, nth: int, category: str, spec):
    """(node type, label, props) feeding an input, chosen from its kind and ComfyQ's label; None = leave
    it unlinked. A picture the student must choose (a character, a style, a reference) gets an image
    file node; the scene's clay render feeds the rest (current frame for images, first / last frame
    for videos); optional extra images stay free."""
    label = spec.label.lower() if spec is not None else ""
    required = spec is None or spec.required
    if category in UPSCALE_CATEGORIES:
        # an upscaler enlarges a finished picture / video (a render, an AI result) the student picks, not the clay
        if kind == "video":
            return ("ComfyQVideoFileNode", "Video to upscale", {}) if nth == 0 else (None, "", {})
        if kind == "image":
            return ("ComfyQImageNode", "Picture to upscale", {}) if nth == 0 and required else (None, "", {})
    if kind == "video":
        if nth == 0 and "face" not in label:
            return "ComfyQSceneAnimationNode", "Clay animation", {}
        return "ComfyQVideoFileNode", short(spec.label) if spec else "Video file", {}
    if kind == "image":
        if "last frame" in label or "(end)" in label:
            return "ComfyQSceneRenderNode", "Last frame", {"frame": "END"}
        if any(w in label for w in PICK_A_FILE):
            return ("ComfyQImageNode", short(spec.label), {}) if required or nth == 0 else (None, "", {})
        # from here `nth` counts only the scene-like images (not references / last frames)
        if nth > 0 and not required:
            return None, "", {}
        if nth > 0:
            return "ComfyQImageNode", short(spec.label), {}
        if category in ("t2i", "image-edit"):
            return "ComfyQSceneRenderNode", "Scene render", {"frame": "CURRENT"}
        return "ComfyQSceneRenderNode", "First frame", {"frame": "START"}
    if kind == "mask":
        return "ComfyQMaskNode", "Mask (selected objects)", {"frame": "CURRENT"}
    if kind == "audio":
        return "ComfyQAudioFileNode", short(spec.label) if spec else "Audio file", {}
    if kind == "text":
        return ("ComfyQPromptNode", "Prompt", {}) if nth == 0 else (None, "", {})
    return None, "", {}


def build(tree, wf):
    specs = SESSION.specs[wf.id]
    node = tree.nodes.new(WORKFLOW_NODE)
    node.workflow_id = wf.id
    node.build(specs)
    node.refresh_visibility(specs)
    node.location = (COL_WF, 0.0)
    socks = [s for s in node.inputs if s.enabled]
    by_key = {sp.key: sp for sp in specs}
    seen: dict[str, int] = {}
    y, used = 0.0, []
    ov = OVERRIDES.get(wf.id, {})
    prompt_node = None
    for sock in socks:
        kind = socket_kind(sock)
        spec = by_key.get(sock.identifier)
        lab = spec.label.lower() if spec is not None else ""
        if kind == "image" and (any(w in lab for w in PICK_A_FILE) or "last frame" in lab or "(end)" in lab):
            kind_count = "image-file"                  # references don't take the scene render's turn
        else:
            kind_count = kind
        nth = seen.get(kind_count, 0)
        seen[kind_count] = nth + 1
        if sock.identifier in ov.get("prompt_to", ()):
            continue                                   # linked to the Prompt node below
        if sock.identifier in ov.get("inputs", {}):
            ntype, label, props = ov["inputs"][sock.identifier] or (None, "", {})
        else:
            ntype, label, props = source_for(kind, nth, wf.category, spec)
        if ntype is None:
            continue
        src = tree.nodes.new(ntype)
        src.label = label
        for k, v in props.items():
            setattr(src, k, v)
        src.location = (COL_SRC, y)
        y -= STEP
        tree.links.new(src.outputs[0], sock)
        if ntype == "ComfyQPromptNode":
            it = node.item(sock.identifier)            # the workflow's own example prompt
            src.text = ov.get("prompt") or (it.s if it is not None else "") or ""
            prompt_node = prompt_node or src
        used.append(label)
    for key in ov.get("prompt_to", ()):
        sock = next((s for s in socks if s.identifier == key), None)
        if prompt_node is not None and sock is not None:
            tree.links.new(prompt_node.outputs[0], sock)
    if ov.get("steps"):
        note = preset_ops.steps_note(tree, ov["steps"], f"How to use · {wf.name}")
        note.location = (COL_SRC - note.width - 60, 0.0)
    viewer = tree.nodes.new("ComfyQViewerNode")
    viewer.location = (COL_OUT, 160.0)
    save = tree.nodes.new("ComfyQSaveNode")
    save.location = (COL_OUT, -200.0)
    tree.links.new(node.outputs[0], viewer.inputs[0])
    tree.links.new(node.outputs[0], save.inputs[0])
    return used


made = []
for wf in sorted(wfs, key=lambda w: (list(GROUPS).index(w.category), w.name.lower())):
    tree = bpy.data.node_groups.new("library", "ComfyQGraphTree")
    used = build(tree, wf)
    desc = (wf.description or wf.name).strip().replace("\n", " ")
    if len(desc) > 220:
        desc = desc[:217].rstrip() + "…"
    sources = ", ".join(dict.fromkeys(used)) or "a prompt only"
    how = ""
    if any(s.kind == "mask" for s in SESSION.specs[wf.id]):
        # ComfyQ's text says "paint over the part to replace", written for its web page; in Blender the zone is
        # the SELECTED OBJECTS until the Mask Zone tool ships (audit 2, #17)
        desc = (desc.replace("Upload an image, paint over the part you want to replace", "Select the objects to "
                             "replace in the scene")
                .replace("Upload a scene, paint where new content should go", "Select the objects where new "
                         "content should go")
                .replace("painted area", "selected area").replace("paint over", "select"))
        how = " In Blender: select the objects to repaint; the scene render with them cut out is sent."
    data = preset_ops.serialize(tree, wf.name, f"{desc}\nFrom: {sources}. Viewer + Save" + (f".{how}" if how else ""))
    for d in data["nodes"]:
        if d["type"] == WORKFLOW_NODE:
            d["params"] = []                           # the workflow's defaults…
            d["match_scene"] = True                    # …and the scene's duration / size / fps
    data["library"] = {"workflow_id": wf.id, "category": wf.category}
    folder = OUT / GROUPS[wf.category]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (wf.name.translate(FILE_SAFE).strip()[:80] + ".json")
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    made.append({"group": GROUPS[wf.category], "label": wf.name, "workflow_id": wf.id, "from": sources,
                 "file": str(path)})
    bpy.data.node_groups.remove(tree)

for m in made:
    print(f"LIB {m['group']:14} {m['workflow_id']:52} ← {m['from']}")
print(f"LIBRARY DONE: {len(made)} presets in {OUT}")
SESSION.close()
