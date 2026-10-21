# SPDX-License-Identifier: GPL-3.0-or-later
"""Data saved in the .blend: chosen workflows, parameter values per workflow, job records.

No server address or token is ever stored here; that lives in the add-on
preferences and the profiles file.
"""
from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .client import camera
from .passes.render import STYLES
from .runtime.session import SESSION


def _relative_ok() -> set[str]:
    """Paths the user may write as "//…" (next to the .blend): Blender 5.2 warns unless the property
    says so; older versions do not know the option."""
    try:
        StringProperty(options={"PATH_SUPPORTS_BLEND_RELATIVE"})
        return {"PATH_SUPPORTS_BLEND_RELATIVE"}
    except TypeError:
        return set()


REL_PATH = _relative_ok()

# Fixed numbers so the stored value means the same source whatever list a kind shows.
_SRC = {
    "STRUCTURE": ("Scene Render", "Workbench render of the scene camera", "SCENE", 0),
    "MASK": ("Render + Selection Mask", "Scene render; the selected objects are the area to repaint", "SELECT_SET", 1),
    "ANIMATION": ("Scene Animation", "The frame range rendered through the scene camera, as a video",
                  "RENDER_ANIMATION", 2),
    "IMAGE": ("Blender Image", "An image loaded in this file", "IMAGE_DATA", 3),
    "FILE": ("File", "A file on disk", "FILEBROWSER", 4),
    "NONE": ("Leave Empty", "Send nothing; the workflow uses its own default", "X", 5),
    "KEYFRAME": ("AI Keyframe", "Restyle the scene frame with a chosen image model first",
                 "SHADERFX", 6),
    "SELECTION": ("Selected Objects", "The selected objects, sent as a GLB file (modifiers applied)",
                  "OBJECT_DATA", 7),
}
_SOURCES_BY_KIND = {
    "image": ("STRUCTURE", "KEYFRAME", "MASK", "IMAGE", "FILE", "NONE"),
    "mask": ("MASK", "STRUCTURE", "IMAGE", "FILE"),
    "video": ("ANIMATION", "FILE", "NONE"),
    "audio": ("FILE", "NONE"),
    "model": ("SELECTION", "FILE"),
}
# Built once and kept alive: Blender crashes or shows garbage if enum item strings are freed.
_SOURCE_ITEMS = {kind: [(k, *_SRC[k]) for k in keys] for kind, keys in _SOURCES_BY_KIND.items()}
_SOURCE_ITEMS[""] = [(k, *v) for k, v in _SRC.items()]

# First choice per media kind.
DEFAULT_SOURCE = {kind: keys[0] for kind, keys in _SOURCES_BY_KIND.items()}

# Blender IntProperty is 32-bit signed; seeds above this are clamped.
INT_MAX = 2**31 - 1

TABS = (
    ("IMAGE", "Image", "Text-to-image, image editing and control", "IMAGE_DATA", 0),
    ("VIDEO", "Video", "Image-to-video and video-to-video", "FILE_MOVIE", 1),
    ("MODEL", "3D", "Image to 3D model", "MESH_MONKEY", 2),
    ("SPLAT", "Splats", "Image to Gaussian splat", "POINTCLOUD_DATA", 3),
    ("AUDIO", "Audio", "Audio generation", "SOUND", 4),
    ("TOOLS", "Tools", "Upscale, depth, normals, segmentation, description", "TOOL_SETTINGS", 5),
)


def tab_of(wf) -> str:
    c = wf.category
    if c == "3d":
        return "SPLAT" if "splat" in wf.id or "gaussian" in " ".join(wf.tags) else "MODEL"
    return {"t2i": "IMAGE", "image-edit": "IMAGE", "i2i": "IMAGE", "i2v": "VIDEO", "video-edit": "VIDEO",
            "audio": "AUDIO"}.get(c, "TOOLS")


def _source_items(self, context):
    return _SOURCE_ITEMS.get(self.kind, _SOURCE_ITEMS[""])


_ENUM_CACHE: dict[tuple[str, str], list[tuple]] = {}
_EMPTY = [("", "—", "")]


def _enum_items(self, context):
    """Options of a `select`/`lora` param, cached per (workflow, key) for the reason above."""
    cache_key = (self.workflow_id, self.key)
    if cache_key not in _ENUM_CACHE:
        spec = next((s for s in SESSION.specs.get(self.workflow_id, ()) if s.key == self.key), None)
        if spec is None or not spec.options:
            return _EMPTY
        labels = spec.option_labels if len(spec.option_labels) == len(spec.options) else spec.options
        _ENUM_CACHE[cache_key] = [(o, lab, o) for o, lab in zip(spec.options, labels, strict=False)]
    return _ENUM_CACHE[cache_key]


def _enum_update(self, context):
    self.s = self.e


class ComfyQParamValue(bpy.types.PropertyGroup):
    workflow_id: StringProperty()     # values are kept per workflow, so switching never loses tweaks
    key: StringProperty()
    kind: StringProperty()
    s: StringProperty(name="")
    text: PointerProperty(name="Prompt text", type=bpy.types.Text,
                          description="Multi-line prompt edited in a Text Editor; replaces the one-line field")
    e: EnumProperty(name="", items=_enum_items, update=_enum_update)
    i: IntProperty(name="", min=-INT_MAX, max=INT_MAX)
    f: FloatProperty(name="", precision=2)
    b: BoolProperty(name="")
    randomize: BoolProperty(name="Random", default=True, description="Pick a new random seed for every job")
    source: EnumProperty(name="", items=_source_items)
    image: PointerProperty(name="", type=bpy.types.Image)
    path: StringProperty(name="", subtype="FILE_PATH", options=REL_PATH)
    # a `camera` param: where the camera stands around the subject (ComfyUI's CameraAngle axes)
    cam_h: IntProperty(name="Around", min=0, max=360, default=0,
                       description="Turn the camera around the subject: 0 front, 90 right side, 180 back")
    cam_v: IntProperty(name="Height", min=-30, max=60, default=0,
                       description="Raise the camera above the subject (negative looks up from below)")
    cam_zoom: FloatProperty(name="Zoom", min=0.0, max=10.0, default=5.0, step=10, precision=1,
                            description="Frame the subject from a wide shot (0) to a close-up (10)")

    def value(self):
        """Current value as sent to ComfyQ (prompt text blocks win over the one-line field)."""
        if self.kind in ("prompt", "text"):
            return self.text.as_string().strip() if self.text else self.s
        if self.kind == "camera":
            return camera.dumps({"horizontal": self.cam_h, "vertical": self.cam_v, "zoom": self.cam_zoom})
        return {"bool": self.b, "int": self.i, "seed": self.i, "float": self.f}.get(self.kind, self.s)


class ComfyQJobRecord(bpy.types.PropertyGroup):
    job_id: StringProperty()
    workflow_id: StringProperty()
    label: StringProperty()
    created: FloatProperty()
    imported: BoolProperty(default=False)
    purpose: StringProperty()          # "" or "keyframe"
    inputs: StringProperty()           # JSON {param key: local file sent} — the clay animation, start image…
    values: StringProperty()           # JSON {param key: value sent} (not the files) — what Retry books again


class ComfyQSceneProps(bpy.types.PropertyGroup):
    tab: EnumProperty(name="Category", items=TABS, default="IMAGE")
    workflow_id: StringProperty(name="Workflow")
    keyframe_workflow_id: StringProperty(name="Keyframe Model",
                                         description="Image workflow that restyles the scene frame for AI keyframes")
    params: CollectionProperty(type=ComfyQParamValue)
    style: EnumProperty(name="Render Style", items=STYLES, default="CLAY",
                         description="How the scene is drawn in the passes sent to ComfyQ")
    auto_import: BoolProperty(name="Import Results", default=True,
                              description="Load finished results into this file automatically")
    jobs: CollectionProperty(type=ComfyQJobRecord)
    show_jobs: IntProperty(name="Jobs Shown", default=5, min=1, max=50,
                           description="Number of recent jobs listed")


def param_item(scene: bpy.types.Scene, key: str, workflow_id: str | None = None) -> ComfyQParamValue | None:
    wf = scene.comfyq.workflow_id if workflow_id is None else workflow_id
    for it in scene.comfyq.params:
        if it.key == key and it.workflow_id == wf:
            return it
    return None


def workflow_items(scene: bpy.types.Scene, workflow_id: str) -> list[ComfyQParamValue]:
    return [it for it in scene.comfyq.params if it.workflow_id == workflow_id]


classes = (ComfyQParamValue, ComfyQJobRecord, ComfyQSceneProps)


def register() -> None:
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.comfyq = PointerProperty(type=ComfyQSceneProps)


def unregister() -> None:
    del bpy.types.Scene.comfyq
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
