# SPDX-License-Identifier: GPL-3.0-or-later
"""Mask zone tools for students (audit 2, phase 5; docs/ux-audit-2-plan.md §3.3):

* **Find by Name**: SAM3 finds what is named ("sofa, rug") in the camera view; the found surfaces join the scene's
  zone (each can be unticked), with Grow / Feather / Invert like the rest of the zone. Until now only agents could
  (blender_segment).
* **Paint Zone**: the scene render as a picture to paint on in the Image Editor; what is painted transparent (the
  Erase brush) is repainted, a soft brush gives a soft edge. Sent by the mask source *Painted Zone*.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import bpy

from ..passes import render, zone_scene
from ..runtime.session import SESSION

PAINTED = "ComfyQ Painted Zone"
FIND = {"running": False, "message": ""}           # the Find in progress (for the bar)


def _labels(words: str) -> list[str]:
    return [w.strip() for w in words.replace(";", ",").split(",") if w.strip()]


def _cache() -> Path:
    from ..runtime.session import cache_dir
    d = cache_dir() / "segment" / time.strftime("zone_%Y%m%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _redraw():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            a.tag_redraw()


class COMFYQ_OT_zone_find(bpy.types.Operator):
    bl_idname = "comfyq.zone_find"
    bl_label = "Find"
    bl_description = ("Find what is named in the camera view with SAM3 (on the server) and add it to the zone to "
                      "repaint; each name found can be unticked")

    @classmethod
    def poll(cls, context):
        cq = context.scene.comfyq
        if FIND["running"]:
            cls.poll_message_set("Already finding: " + FIND["message"])
            return False
        if SESSION.client is None:
            cls.poll_message_set("Connect to ComfyQ first")
            return False
        if context.scene.camera is None:
            cls.poll_message_set("The scene needs a camera")
            return False
        if not _labels(cq.zone_words):
            cls.poll_message_set("Type what to find first (for example: sofa, rug)")
            return False
        return True

    def execute(self, context):
        from ..graph.run import solo_on
        from ..mcp.tools import on_main
        from ..runtime import segmentation, serving
        scene = context.scene
        labels = _labels(scene.comfyq.zone_words)
        frame = scene.frame_current
        try:      # material colours: SAM3 tells a sofa from a rug better than on grey clay
            picture = render.structure_pass(scene, "MATERIAL", None, frame)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        client, out, solo, scene_name = SESSION.client, _cache(), solo_on(), scene.name
        FIND.update(running=True, message="starting")

        def say(m):
            FIND["message"] = m
            SESSION.later(_redraw)

        def work():
            sam3 = segmentation.SAM3_ID
            if sam3 not in client.served_ids():
                if not solo:
                    raise RuntimeError("SAM3 is not loaded on the server: ask the teacher to load it")
                if serving.blockers(client):
                    raise RuntimeError("SAM3 is not loaded, and someone is using the server: try again later")
                say("loading SAM3 on the server (about a minute)")
                serving.serve(client, sam3, serving.choose(client, sam3, "auto"))
                t0 = time.monotonic()
                while sam3 not in client.served_ids():
                    if time.monotonic() - t0 > 300:
                        raise RuntimeError("SAM3 did not load in 5 minutes")
                    time.sleep(3)
            return segmentation.segment(client, picture, labels, out, hop=on_main, progress=say)

        def done(res):
            FIND.update(running=False, message="")
            sc = bpy.data.scenes.get(scene_name)
            if sc is None:
                return
            regions = segmentation.region_dicts(res["regions"])
            for r in regions:
                r["picked"] = True
            zone_scene.set_found(sc, {"words": labels, "frame": frame, "camera": sc.camera.name if sc.camera else "",
                                      "picture": str(picture), "regions": regions, "errors": res.get("errors", {})})
            missing = [lab for lab in labels if lab.lower() not in {r["label"] for r in regions}]
            SESSION.status_note = (f"Found: {', '.join(r['label'] for r in regions) or 'nothing'}"
                                   + (f" · not found: {', '.join(missing)}" if missing else ""))
            _redraw()

        def failed(msg):
            FIND.update(running=False, message="")
            SESSION.error = f"Find: {msg}"
            _redraw()
        SESSION.run(work, done, on_error=failed)
        self.report({"INFO"}, f"Finding {', '.join(labels)} with SAM3…")
        return {"FINISHED"}


class COMFYQ_OT_zone_found_toggle(bpy.types.Operator):
    bl_idname = "comfyq.zone_found_toggle"
    bl_label = "Use Found Surface"
    bl_description = "Include this found surface in the zone to repaint, or leave it out"
    bl_options = {"REGISTER", "UNDO"}
    label: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        zone_scene.toggle_found(context.scene, self.label)
        _redraw()
        return {"FINISHED"}


class COMFYQ_OT_zone_paint(bpy.types.Operator):
    bl_idname = "comfyq.zone_paint"
    bl_label = "Paint Zone"
    bl_description = ("Paint the zone to repaint on the scene render in the Image Editor: the Erase brush makes it "
                      "transparent, which is what gets repainted (send it with the source Painted Zone)")

    @classmethod
    def poll(cls, context):
        if context.scene.camera is None:
            cls.poll_message_set("The scene needs a camera")
            return False
        return True

    def execute(self, context):
        from ..ui.player import fit_view_soon
        scene = context.scene
        img = bpy.data.images.get(PAINTED)
        if img is None or self._fresh(img):
            try:
                path = render.structure_pass(scene, scene.comfyq.style if scene.comfyq.style != "CYCLES" else "CLAY")
            except RuntimeError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            img = _painting_from(path, img)
        area = _image_editor(context)
        if area is None:
            self.report({"ERROR"}, "Needs an Image Editor in this window (the ComfyQ workspace or window has one)")
            return {"CANCELLED"}
        sp = area.spaces.active
        sp.image = img
        sp.ui_mode = "PAINT"
        fit_view_soon(area)
        how = _erase_brush(context, area)
        self.report({"INFO"}, "Paint what to repaint: it turns transparent" + (f" ({how})" if how else ""))
        return {"FINISHED"}

    @staticmethod
    def _fresh(img) -> bool:
        """Start again from a new render when the painting has no transparent pixel yet (nothing to lose)."""
        import numpy as np
        if img.size[0] == 0:
            return True
        a = np.empty(img.size[0] * img.size[1] * 4, np.float32)
        img.pixels.foreach_get(a)
        return bool((a[3::4] > 0.999).all())


def _painting_from(path: Path, old) -> bpy.types.Image:
    """The render as an RGBA picture kept in the file (its pixels, gotcha 33), fully opaque to start."""
    import numpy as np
    src = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h, ch = src.size[0], src.size[1], src.channels
        buf = np.empty(w * h * ch, np.float32)
        src.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(src)
    px = np.ones((w * h, 4), np.float32)
    px[:, :min(ch, 3)] = buf.reshape(-1, ch)[:, :3] if ch >= 3 else buf.reshape(-1, 1)
    if ch < 3:
        px[:, 1] = px[:, 2] = px[:, 0]
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(PAINTED, w, h, alpha=True)
    img.pixels.foreach_set(px.ravel())
    img.pack()                                    # painted pixels live in the .blend
    return img


def _image_editor(context):
    from ..ui.workspace import _frame_area, workspace_of
    if workspace_of(context) is not None:
        return _frame_area(context.screen)
    from ..ops.workflow import comfyq_window
    for w in [comfyq_window(), context.window]:
        if w is None:
            continue
        a = next((a for a in w.screen.areas if a.type == "IMAGE_EDITOR"), None)
        if a is not None:
            return a
    return None


def activate_brush(context, area, name: str) -> bool:
    """Make one of Blender's essential texture-paint brushes (Erase Soft, Paint Hard…) the active one."""
    region = next(r for r in area.regions if r.type == "WINDOW")
    with context.temp_override(area=area, region=region):
        try:
            return bpy.ops.brush.asset_activate(
                asset_library_type="ESSENTIALS", asset_library_identifier="",
                relative_asset_identifier=f"brushes/essentials_brushes-mesh_texture.blend/Brush/{name}") == {"FINISHED"}
        except (RuntimeError, TypeError, AttributeError):
            return False


def _erase_brush(context, area) -> str:
    """Blender's own Erase Soft brush (soft edge = a feathered zone); else the current brush set to Erase Alpha.
    Returns what was done (for the report)."""
    if activate_brush(context, area, "Erase Soft"):
        return "brush Erase Soft"
    brush = getattr(context.tool_settings.image_paint, "brush", None)
    if brush is not None:
        try:
            brush.blend = "ERASE_ALPHA"
            return "Erase Alpha"
        except (AttributeError, TypeError, RuntimeError):
            pass
    return "set the brush's Blend to Erase Alpha in the header"


def mask_preview(mask: Path) -> Path:
    """The mask pass as a student reads it: the picture, the zone (transparent in the mask) in red."""
    import numpy as np

    from ..passes import segment as seg
    img = seg.read_rgba(mask).copy()
    zone = (1.0 - img[..., 3])[..., None]
    red = np.array((0.95, 0.12, 0.08), np.float32)
    img[..., :3] = img[..., :3] * (1 - 0.55 * zone) + red * (0.55 * zone)
    img[..., 3] = 1.0
    return seg.write_rgba(img, mask.with_name(mask.stem + "_preview.png"))


def found_summary(scene) -> str:
    try:
        info = json.loads(scene.comfyq.zone_found or "{}")
    except ValueError:
        return ""
    errs = info.get("errors") or {}
    return f"Not found: {', '.join(errs)}" if errs else ""


classes = (COMFYQ_OT_zone_find, COMFYQ_OT_zone_found_toggle, COMFYQ_OT_zone_paint)
