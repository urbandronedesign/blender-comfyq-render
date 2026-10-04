# SPDX-License-Identifier: GPL-3.0-or-later
"""A video player for the Image Editor of the ComfyQ window, independent of the scene timeline.

The Image Editor can show a movie, but normally only follows the scene's frame. Here we switch
the ImageUser's auto-refresh off and drive the frame ourselves from a timer: with
frame_start == scene frame, the editor shows frame (1 + frame_offset). Playing a result never
moves the scene (unless "Sync scene" is on, to compare a V2V result with the animation).
"""
from __future__ import annotations

import time
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

SPEEDS = (("0.25", "¼×", ""), ("0.5", "½×", ""), ("1", "1×", ""), ("2", "2×", ""))
_META: dict[str, tuple[int, float]] = {}        # movie path -> (frames, fps)
_PLAY = {"area": 0, "t0": 0.0, "f0": 1, "on": False, "frame": 1}


def movie_meta(img: bpy.types.Image) -> tuple[int, float]:
    """(frame count, fps) of a movie image, read once through a temporary MovieClip."""
    path = bpy.path.abspath(img.filepath)
    if path not in _META:
        frames, fps = max(1, img.frame_duration), 24.0
        try:
            clip = bpy.data.movieclips.load(path, check_existing=False)
            frames, fps = max(1, clip.frame_duration), float(clip.fps or 24.0)
            bpy.data.movieclips.remove(clip)
        except RuntimeError:
            pass
        _META[path] = (frames, fps)
    return _META[path]


_PENDING: set[str] = set()


def peek_meta(img: bpy.types.Image) -> tuple[int, float]:
    """For draw code: the cached (frames, fps), or a cheap estimate while a timer reads the real values.
    Loading a MovieClip writes ID data, which must never happen inside draw() (gotcha 17)."""
    path = bpy.path.abspath(img.filepath)
    if path in _META:
        return _META[path]
    if path not in _PENDING:
        _PENDING.add(path)
        name = img.name

        def read():
            _PENDING.discard(path)
            im = bpy.data.images.get(name)
            if im is not None:
                movie_meta(im)
                for win in bpy.context.window_manager.windows:
                    for area in win.screen.areas:
                        if area.type == "IMAGE_EDITOR":
                            area.tag_redraw()
        bpy.app.timers.register(read, first_interval=0.0)
    return max(1, img.frame_duration), 24.0


def forget_meta(img: bpy.types.Image) -> None:
    """The movie behind `img` changed (new clay render): re-read its frame count and fps."""
    _META.pop(bpy.path.abspath(img.filepath), None)


def _space(area):
    return area.spaces.active if area and area.type == "IMAGE_EDITOR" else None


def _find_area(ptr: int):
    for win in bpy.context.window_manager.windows:
        for a in win.screen.areas:
            if a.as_pointer() == ptr:
                return a
    return None


def is_movie(img) -> bool:
    return img is not None and img.source == "MOVIE"


def set_frame(area, n: int) -> int:
    """Show frame n (1-based) of the movie in `area`; returns the frame actually shown."""
    sp = _space(area)
    if sp is None or not is_movie(sp.image):
        return 0
    frames, _fps = movie_meta(sp.image)
    n = max(1, min(frames, n))
    iu = sp.image_user
    wm = bpy.context.window_manager
    if wm.comfyq_player_sync:
        sc = bpy.context.scene
        sc.frame_set(sc.frame_start + n - 1)
    iu.use_auto_refresh = False
    iu.use_cyclic = False
    iu.frame_duration = frames
    iu.frame_start = bpy.context.scene.frame_current
    iu.frame_offset = n - 1
    # Setting the offset alone does not make the editor recompute its frame (checked in Mixar 4.2);
    # re-assigning the image does, without touching the scene.
    sp.image = sp.image
    _PLAY["frame"] = n
    _PLAY["quiet"] = True                     # the slider follows playback without seeking back
    try:
        wm.comfyq_player_pos = 100.0 * (n - 1) / max(1, frames - 1)
    finally:
        _PLAY["quiet"] = False
    area.tag_redraw()
    return n


def show_media(area, img: bpy.types.Image) -> None:
    sp = _space(area)
    if sp is None:
        return
    sp.image = img
    if is_movie(img):
        set_frame(area, 1)


def _tick():
    if not _PLAY["on"]:
        return None
    area = _find_area(_PLAY["area"])
    sp = _space(area)
    if sp is None or not is_movie(sp.image):
        _PLAY["on"] = False
        return None
    wm = bpy.context.window_manager
    frames, fps = movie_meta(sp.image)
    speed = float(wm.comfyq_player_speed)
    n = _PLAY["f0"] + int((time.monotonic() - _PLAY["t0"]) * fps * speed)
    if n > frames:
        if not wm.comfyq_player_loop:
            set_frame(area, frames)
            _PLAY["on"] = False
            return None
        n = 1 + (n - 1) % frames
        _PLAY["f0"], _PLAY["t0"] = n, time.monotonic()
    set_frame(area, n)
    return 1.0 / max(1.0, fps * speed)


def play(area, start: int = 1) -> None:
    """Play the movie shown in `area` from `start` (restarts if something was already playing)."""
    _PLAY.update(area=area.as_pointer(), t0=time.monotonic(), f0=start, on=True)
    set_frame(area, start)
    if not bpy.app.timers.is_registered(_tick):      # one timer only, or playback runs at double speed
        bpy.app.timers.register(_tick, first_interval=0.0)


def is_playing() -> bool:
    return bool(_PLAY["on"])


def describe(img) -> str:
    """What is on screen, in words: the clay animation that is sent, an AI result, a render…"""
    if img is None:
        return "Nothing shown"
    path = Path(bpy.path.abspath(img.filepath)) if img.filepath else None
    if img.get("comfyq_job"):
        return f"AI result · job {str(img['comfyq_job'])[:8]}"
    if path is not None and path.parent.parent.name == "passes" and is_movie(img):
        return "Clay animation (sent to ComfyQ)"            # cache/passes/<stamp>_animation/*.mp4
    if img.name.startswith("ComfyQ animation pass"):
        return "Clay animation (sent to ComfyQ)"
    if img.name.startswith(("ComfyQ render preview", "ComfyQ mask preview")) or (
            path is not None and path.parent.name == "passes"):
        return "Scene render (camera view)"
    if path is not None and path.parent.parent.name == "cache" and len(path.parent.name) == 8:
        return f"AI result · job {path.parent.name}"           # cache/<job8>/<file>
    return img.name


def _pos_update(self, context):
    if _PLAY.get("quiet"):
        return
    area = _find_area(_PLAY["area"]) or context.area
    sp = _space(area)
    if sp is None or not is_movie(sp.image):
        return
    frames, _fps = movie_meta(sp.image)
    n = 1 + round(self.comfyq_player_pos / 100.0 * (frames - 1))
    set_frame(area, n)
    _PLAY["f0"], _PLAY["t0"] = n, time.monotonic()


class COMFYQ_OT_player(bpy.types.Operator):
    bl_idname = "comfyq.player"
    bl_label = "Player"
    bl_options = {"INTERNAL"}
    action: EnumProperty(items=[(a, a.title(), "") for a in
                                ("TOGGLE", "PLAY", "FIRST", "PREV", "NEXT", "LAST")])

    @classmethod
    def description(cls, context, props):
        return {"TOGGLE": "Play / pause", "PLAY": "Play from the start", "FIRST": "First frame",
                "PREV": "Previous frame", "NEXT": "Next frame", "LAST": "Last frame"}[props.action]

    def execute(self, context):
        area = context.area
        sp = _space(area)
        if sp is None or not is_movie(sp.image):
            return {"CANCELLED"}
        frames, _fps = movie_meta(sp.image)
        cur = _PLAY["frame"]
        if self.action == "TOGGLE" and _PLAY["on"]:
            _PLAY["on"] = False
            return {"FINISHED"}
        if self.action in ("TOGGLE", "PLAY"):
            start = 1 if self.action == "PLAY" or cur >= frames else cur
            play(area, start)
            return {"FINISHED"}
        _PLAY["on"] = False
        _PLAY["area"] = area.as_pointer()
        target = {"FIRST": 1, "PREV": cur - 1, "NEXT": cur + 1, "LAST": frames}[self.action]
        set_frame(area, target)
        return {"FINISHED"}


class COMFYQ_OT_show_media(bpy.types.Operator):
    bl_idname = "comfyq.show_media"
    bl_label = "Show"
    bl_description = "Show this result in the ComfyQ window's viewer"
    bl_options = {"INTERNAL"}
    image: StringProperty()
    path: StringProperty()

    def execute(self, context):
        area = context.area if _space(context.area) else viewer_area()
        if area is None and self.path:
            from ..ops.workflow import open_file
            open_file(self.path)          # no Image Editor open: the system viewer/player
            return {"FINISHED"}
        img = bpy.data.images.get(self.image) if self.image else None
        if img is None and self.path:
            img = bpy.data.images.load(self.path, check_existing=True)
        if img is None or area is None:
            return {"CANCELLED"}
        _PLAY["on"] = False
        _PLAY["area"] = area.as_pointer()
        show_media(area, img)
        return {"FINISHED"}


def viewer_area():
    """The Image Editor of the ComfyQ window if open, else any Image Editor."""
    from ..ops.workflow import comfyq_window
    win = comfyq_window()
    windows = [win] if win else list(bpy.context.window_manager.windows)
    for w in windows:
        for a in w.screen.areas:
            if a.type == "IMAGE_EDITOR":
                return a
    return None


def media_items() -> list[bpy.types.Image]:
    """ComfyQ results, keyframes and passes loaded in this file, newest last."""
    return [i for i in bpy.data.images if i.get("comfyq_job") or i.name.startswith("ComfyQ")]


class COMFYQ_MT_results(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_results"
    bl_label = "Results"

    def draw(self, context):
        layout = self.layout
        from ..passes import animation
        last = animation.last()
        if last is not None and last.path.is_file():
            op = layout.operator(COMFYQ_OT_show_media.bl_idname, text="Animation Pass (What Is Sent)",
                                 icon="RENDER_ANIMATION")
            op.path = str(last.path)
            layout.separator()
        items = media_items()
        if not items:
            layout.label(text="No results yet")
        for img in reversed(items[-25:]):
            op = layout.operator(COMFYQ_OT_show_media.bl_idname, text=img.name,
                                 icon="FILE_MOVIE" if is_movie(img) else "IMAGE_DATA")
            op.image = img.name


class COMFYQ_PT_player(bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "ComfyQ"
    bl_label = "Player"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        sp = context.space_data
        img = sp.image
        render_row = layout.row(align=True)          # a camera render of the scene, any time
        render_row.prop(context.scene.comfyq, "style", text="")
        render_row.operator("comfyq.preview_pass", text="Render Scene", icon="RENDER_STILL").mask = False
        row = layout.row(align=True)
        row.menu(COMFYQ_MT_results.bl_idname, text=describe(img) if img else "Choose Result",
                 icon="RENDER_ANIMATION" if img is not None and not img.get("comfyq_job") else "IMAGE_DATA")
        if not is_movie(img):
            if img is not None:
                layout.label(text=f"Still image · {img.size[0]}×{img.size[1]}")
            return
        frames, fps = peek_meta(img)
        wm = context.window_manager
        playing = _PLAY["on"] and _PLAY["area"] == context.area.as_pointer()
        ctl = layout.row(align=True)
        ctl.scale_y = 1.3
        ctl.operator(COMFYQ_OT_player.bl_idname, text="", icon="REW").action = "FIRST"
        ctl.operator(COMFYQ_OT_player.bl_idname, text="", icon="FRAME_PREV").action = "PREV"
        ctl.operator(COMFYQ_OT_player.bl_idname, text="", icon="PAUSE" if playing else "PLAY").action = "TOGGLE"
        ctl.operator(COMFYQ_OT_player.bl_idname, text="", icon="FRAME_NEXT").action = "NEXT"
        ctl.operator(COMFYQ_OT_player.bl_idname, text="", icon="FF").action = "LAST"
        ctl.prop(wm, "comfyq_player_loop", text="", icon="LOOP_FORWARDS", toggle=True)
        layout.prop(wm, "comfyq_player_pos", text=f"Frame {_PLAY['frame']} / {frames}", slider=True)
        row = layout.row(align=True)
        row.prop(wm, "comfyq_player_speed", expand=True)
        row = layout.row()
        row.label(text=f"{frames} frames · {fps:g} fps · {frames / fps:.1f} s · {img.size[0]}×{img.size[1]}")
        row = layout.row(align=True)
        row.prop(wm, "comfyq_player_sync", text="Sync Scene Frame", toggle=True, icon="TIME")
        row.operator("comfyq.open_media", text="", icon="URL").path = bpy.path.abspath(img.filepath)


class COMFYQ_OT_open_media(bpy.types.Operator):
    bl_idname = "comfyq.open_media"
    bl_label = "Open in External App"
    bl_description = "Open the file in the default app of this PC"
    bl_options = {"INTERNAL"}
    path: StringProperty()

    def execute(self, context):
        from ..ops.workflow import open_file
        if not Path(self.path).is_file():
            self.report({"ERROR"}, f"The file is no longer there: {Path(self.path).name}")
            return {"CANCELLED"}
        open_file(self.path)
        return {"FINISHED"}


classes = (COMFYQ_OT_player, COMFYQ_OT_show_media, COMFYQ_OT_open_media, COMFYQ_MT_results, COMFYQ_PT_player)


def register() -> None:
    for c in classes:
        bpy.utils.register_class(c)
    wm = bpy.types.WindowManager
    wm.comfyq_player_pos = FloatProperty(name="Position", min=0.0, max=100.0, default=0.0, subtype="PERCENTAGE",
                                         precision=0, update=_pos_update)
    wm.comfyq_player_loop = BoolProperty(name="Loop", default=True, description="Play the result again from the start when it ends")
    wm.comfyq_player_speed = EnumProperty(name="Speed", items=SPEEDS, default="1",
                                          description="Playback speed of the result")
    wm.comfyq_player_sync = BoolProperty(
        name="Sync Scene Frame", default=False,
        description="Move the scene to the matching frame while playing (compare a result with the animation)")


def unregister() -> None:
    _PLAY["on"] = False
    wm = bpy.types.WindowManager
    for p in ("comfyq_player_pos", "comfyq_player_loop", "comfyq_player_speed", "comfyq_player_sync"):
        if hasattr(wm, p):
            delattr(wm, p)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
