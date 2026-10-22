# SPDX-License-Identifier: GPL-3.0-or-later
"""Output nodes: Viewer (show / play), Import to Scene, Save (copy to a folder).

They book no job: they act on what is linked into them — a workflow node's result or a source's last
render. With "… when ready" on, the add-on timer (`tick`) acts once per new result (the node keeps the
path + mtime it last handled, saved with the file, so reopening a file does not act again).
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import bpy

from ..importers.media import IMAGE_EXTS, VIDEO_EXTS
from .tree import WORKFLOW_NODE, animation_path, movie_thumbnail, socket_kind, upstream

ICONS = {"image": "IMAGE_DATA", "mask": "SELECT_SET", "video": "FILE_MOVIE", "audio": "SOUND", "text": "TEXT",
         "model": "MESH_MONKEY", "splat": "POINTCLOUD_DATA"}


@dataclass
class Media:
    kind: str
    path: str = ""                 # file on disk ("" = none yet)
    text: str | None = None        # text results / prompts
    job_id: str = ""
    workflow_id: str = ""
    source: str = ""               # node it comes from

    @property
    def ready(self) -> bool:
        return bool(self.path) or self.text is not None

    @property
    def label(self) -> str:
        what = Path(self.path).name if self.path else ("text" if self.text is not None else "no result yet")
        return f"{self.source} · {what}"

    @property
    def icon(self) -> str:
        return ICONS.get(self.kind, "QUESTION") if self.ready else "SORTTIME"

    def key(self) -> str:
        if self.path:
            p = Path(self.path)
            return f"{self.path}|{p.stat().st_mtime_ns if p.is_file() else 0}"
        return f"text|{hash(self.text)}" if self.text is not None else ""


_TEXT_CACHE: dict[str, tuple[int, str]] = {}


def _read_text(path: str) -> str:
    """Text results, cached by mtime (drawing calls this on every redraw)."""
    p = Path(path)
    mtime = p.stat().st_mtime_ns
    hit = _TEXT_CACHE.get(path)
    if hit is None or hit[0] != mtime:
        hit = (mtime, p.read_text(encoding="utf-8", errors="replace").strip()[:20000])
        _TEXT_CACHE[path] = hit
    return hit[1]


def _image_file(name: str) -> str:
    img = bpy.data.images.get(name)
    return bpy.path.abspath(img.filepath) if img is not None and img.filepath else ""


def linked(node) -> Media | None:
    """What is linked into an output node (through Reroutes); None if nothing."""
    return linked_socket(node.inputs[0]) if node.inputs else None


def linked_socket(sock) -> Media | None:
    """What is linked into one input socket (through Reroutes); None if nothing."""
    src, out = upstream(sock)
    if src is None:
        return None
    m = Media(kind=socket_kind(out),
              source=src.label or (src.draw_label() if src.bl_idname == WORKFLOW_NODE else src.name))
    idn = src.bl_idname
    if idn == WORKFLOW_NODE:
        m.job_id, m.workflow_id = src.job_id, src.workflow_id
        m.path = src.result
    elif idn == "ComfyQSurfacesNode":
        if m.kind == "text":
            m.text = src.prompt()
            return m
        m.path = src.result
    elif idn == "ComfyQPromptNode":
        m.text = src.value()
        return m
    elif idn == "ComfyQJoinTextNode":
        from .run import _text
        m.text = _text(src)
        return m
    elif idn == "ComfyQBoardNode":
        m.path = src.result
    elif idn == "ComfyQSceneRenderNode":
        m.path = _image_file("ComfyQ render preview")
    elif idn == "ComfyQMaskNode":
        m.path = _image_file("ComfyQ mask preview")
    elif idn == "ComfyQSceneAnimationNode":
        m.path = animation_path()
    elif idn == "ComfyQImageNode":
        m.path = bpy.path.abspath(src.image.filepath) if src.image is not None and src.image.filepath else ""
    elif idn in ("ComfyQVideoFileNode", "ComfyQAudioFileNode", "ComfyQModelFileNode"):
        m.path = bpy.path.abspath(src.filepath) if src.filepath else ""
    if m.path and not Path(m.path).is_file():
        m.path = ""
    if m.path and m.kind == "text":
        m.text = _read_text(m.path)
    return m


# -- acting ------------------------------------------------------------------------------------------------
def act(node, scene, force: bool = False) -> str:
    """Do what the output node does with what is linked; returns what happened ("" = nothing to do)."""
    media = linked(node)
    idn = node.bl_idname
    if idn != "ComfyQFilmNode" and (media is None or not media.ready):
        return ""
    if idn == "ComfyQFilmNode":
        msg = _film(node)
        if msg:
            node.status = msg
        node.handled = film_key(node)
        return msg
    if idn == "ComfyQViewerNode":
        msg = _show(node, media, force)
    elif idn == "ComfyQImportNode":
        msg = _import(node, scene, media)
    else:
        msg = _save(node, media)
    if msg:
        node.status = msg
    node.handled = media.key()
    return msg


def _show(node, media: Media, force: bool) -> str:
    from ..ops.workflow import _show_text, comfyq_window, open_file, text_editor_area
    from ..ui import player
    if media.text is not None and not media.path:
        txt = _text_block(node, media.text)
        area = text_editor_area(comfyq_window())
        if area is not None:
            _show_text(area, txt)
        return f"Text in “{txt.name}”"
    ext = Path(media.path).suffix.lower()
    area = player.viewer_area()
    if ext in IMAGE_EXTS | VIDEO_EXTS and area is not None:
        img = same_file_image(media.path) or bpy.data.images.load(media.path, check_existing=True)
        player._PLAY["on"] = False
        player.show_media(area, img)
        if ext in VIDEO_EXTS:
            player.play(area, 1)
        return f"Shown: {Path(media.path).name}"
    if force:                               # no viewer (or a 3D / audio file): the system app
        open_file(media.path)
        return f"Opened: {Path(media.path).name}"
    return ""


def same_file_image(path: str):
    """An image already showing this file (e.g. "ComfyQ animation pass"), so it is not loaded twice."""
    want = Path(path).resolve()
    for img in bpy.data.images:
        if img.filepath and img.source in ("FILE", "MOVIE"):
            try:
                if Path(bpy.path.abspath(img.filepath)).resolve() == want:
                    return img
            except OSError:
                continue
    return None


def _text_block(node, text: str):
    name = f"ComfyQ text · {node.name}"
    txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    txt.clear()
    txt.write(text)
    return txt


def _snapshot(scene) -> dict:
    from ..importers.media import RESULTS_SCENE, _strips
    res = bpy.data.scenes.get(RESULTS_SCENE)
    return {"strips": {s.name for s in _strips(res)} if res is not None and res.sequence_editor else set(),
            "collections": {c.name for c in bpy.data.collections},
            "objects": {o.name for o in bpy.data.objects}}


def remove_imported(node) -> int:
    """Remove what this Import node created last time (strips, objects, collections). Images stay
    (they may be used elsewhere); the new one replaces them in the viewer."""
    from ..importers.media import RESULTS_SCENE, _strips
    try:
        last = json.loads(node.imported or "{}")
    except ValueError:
        last = {}
    n = 0
    res = bpy.data.scenes.get(RESULTS_SCENE)
    if res is not None and res.sequence_editor is not None:
        strips = _strips(res)
        for name in last.get("strips", []):
            s = strips.get(name)
            if s is not None:
                strips.remove(s)
                n += 1
    for name in last.get("objects", []):
        o = bpy.data.objects.get(name)
        if o is not None:
            bpy.data.objects.remove(o)
            n += 1
    for name in last.get("collections", []):
        c = bpy.data.collections.get(name)
        if c is not None and not c.objects and not c.children:
            bpy.data.collections.remove(c)
    node.imported = ""
    return n


def _import(node, scene, media: Media) -> str:
    from ..importers.media import import_files
    if node.replace:
        remove_imported(node)
    if (media.text is not None and not media.path) or media.kind == "text":
        txt = _text_block(node, media.text or "")
        node.imported = json.dumps({"texts": [txt.name]})
        return f"Imported text “{txt.name}”"
    before = _snapshot(scene)
    msgs = import_files([Path(media.path)], media.job_id or f"graph {node.name}", media.workflow_id, scene,
                        node.placement())
    after = _snapshot(scene)
    node.imported = json.dumps({k: sorted(after[k] - before[k]) for k in after})
    return "Imported " + "; ".join(msgs)


_UNSAFE = '<>:"/\\|?*'


def file_name(node, media: Media) -> str:
    t = time.localtime()
    fields = {"node": node.name, "date": time.strftime("%Y%m%d", t), "time": time.strftime("%H%M%S", t),
              "job": (media.job_id or "")[:8], "workflow": media.workflow_id, "source": media.source}
    try:
        name = node.pattern.format(**fields)
    except (KeyError, IndexError, ValueError):
        name = "{source}_{date}_{time}".format(**fields)
    name = "".join("_" if c in _UNSAFE else c for c in name).strip(" ._") or "comfyq"
    return name.replace("__", "_")


def save_folder(node) -> Path:
    from ..ops.results import default_save_dir
    if node.folder.startswith("//") and not bpy.data.filepath:
        return default_save_dir()                   # unsaved file: Downloads
    return Path(bpy.path.abspath(node.folder or "//comfyq/"))


def _save(node, media: Media) -> str:
    folder = save_folder(node)
    folder.mkdir(parents=True, exist_ok=True)
    ext = Path(media.path).suffix if media.path else ".txt"
    dst = folder / f"{file_name(node, media)}{ext}"
    n = 2
    while dst.exists():
        dst = folder / f"{file_name(node, media)}_{n}{ext}"
        n += 1
    if media.path:
        shutil.copy2(media.path, dst)
    else:
        dst.write_text(media.text or "", encoding="utf-8")
    return f"Saved {dst}"


def save_batch(node, media: Media, root: Path, file: Path) -> str:
    """A batch step's result → the results folder next to the input one, same subfolders, the file's own name
    plus the Save node's Batch Suffix (an earlier result of the same file is replaced)."""
    from . import batch
    stem = batch.result_stem(root, file, node.batch_suffix)
    stem.parent.mkdir(parents=True, exist_ok=True)
    ext = Path(media.path).suffix if media.path else ".txt"
    dst = stem.with_name(stem.name + ext)
    if media.path:
        shutil.copy2(media.path, dst)
    else:
        dst.write_text(media.text or "", encoding="utf-8")
    return f"Saved {dst}"


# -- the timer -----------------------------------------------------------------------------------------------
def tick(tree, scene) -> None:
    """Thumbnails for Viewers, and "… when ready": act once per new result."""
    from .tree import OUTPUT_NODES, batch_source
    for node in tree.nodes:
        if node.bl_idname not in OUTPUT_NODES:
            continue
        if node.bl_idname == "ComfyQFilmNode":
            key = film_key(node)
            if node.auto and key and key != node.handled:
                try:
                    act(node, scene)
                except (OSError, RuntimeError) as e:
                    node.status = f"Failed: {e}"
                    node.handled = key
            continue
        media = linked(node)
        if media is None or not media.ready:
            continue
        if node.bl_idname == "ComfyQViewerNode" and media.path:
            ext = Path(media.path).suffix.lower()
            if ext in VIDEO_EXTS:
                movie_thumbnail(media.path)
            elif ext in IMAGE_EXTS:
                img = bpy.data.images.load(media.path, check_existing=True)
                if img.preview is None:
                    img.preview_ensure()
        if node.bl_idname == "ComfyQSaveNode" and batch_source(node) is not None:
            continue                   # fed by a Media Folder: the batch saves each file (mirrored folder)
        if node.auto and media.key() != node.handled:
            try:
                act(node, scene)
            except (OSError, RuntimeError) as e:
                node.status = f"Failed: {e}"
                node.handled = media.key()


# -- Film: the shots' clips end to end in one sequencer scene --------------------------------------------------
FILM_SCENE = "ComfyQ Film"


def film_media(node) -> tuple[list[Media], Media | None]:
    """The linked Shot inputs in order (linked ones only) and the Soundtrack."""
    clips = [linked_socket(s) for s in node.inputs if s.name.startswith("Shot") and s.is_linked]
    sound_sock = next((s for s in node.inputs if s.name == "Soundtrack"), None)
    sound = linked_socket(sound_sock) if sound_sock is not None and sound_sock.is_linked else None
    return [c for c in clips if c is not None], sound


def film_key(node) -> str:
    clips, sound = film_media(node)
    keys = [c.key() for c in clips if c.path] + ([sound.key()] if sound is not None and sound.path else [])
    return "|".join(keys)


def _film(node) -> str:
    """Rebuild the "ComfyQ Film" scene: the ready clips back to back (channel 2), each clip's sound (channel 1) only
    with Keep Clip Sound, the Soundtrack (channel 3) trimmed to the film. Returns what was done."""
    from ..importers.media import _strips
    clips, sound = film_media(node)
    paths = [Path(c.path) for c in clips if c.path and Path(c.path).is_file()]
    if not paths:
        return ""
    sc = bpy.data.scenes.get(FILM_SCENE) or bpy.data.scenes.new(FILM_SCENE)
    strips = _strips(sc)
    for s in list(strips):
        strips.remove(s)
    frame = 1
    for n, path in enumerate(paths, 1):
        mv = strips.new_movie(name=f"Shot {n}", filepath=str(path), channel=2, frame_start=frame)
        if n == 1:                                       # the film takes the first clip's rate and size
            fps = getattr(mv, "fps", 0) or 24
            sc.render.fps, sc.render.fps_base = round(fps), 1.0
            el = mv.elements[0] if len(mv.elements) else None
            if el is not None and el.orig_width:
                sc.render.resolution_x, sc.render.resolution_y = el.orig_width, el.orig_height
                sc.render.resolution_percentage = 100
        if node.keep_sound:
            try:
                strips.new_sound(name=f"Shot {n} sound", filepath=str(path), channel=1, frame_start=frame)
            except RuntimeError:
                pass                                     # a clip without sound
        frame = mv.frame_final_end
    if sound is not None and sound.path and Path(sound.path).is_file():
        st = strips.new_sound(name="Soundtrack", filepath=sound.path, channel=3, frame_start=1)
        st.frame_final_end = min(st.frame_final_end, frame)
    sc.frame_start, sc.frame_end = 1, max(1, frame - 1)
    sc.render.use_sequencer = True
    seconds = (frame - 1) / (sc.render.fps or 24)
    return f"Film: {len(paths)} shots, {seconds:.1f} s in scene “{FILM_SCENE}”"


def render_film(node) -> Path:
    """Render the "ComfyQ Film" scene to an MP4 with sound in the node's folder (blocking)."""
    sc = bpy.data.scenes.get(FILM_SCENE)
    if sc is None:
        raise RuntimeError("Assemble the film first")
    folder = Path(bpy.path.abspath(node.folder)) if node.folder else Path.home() / "Downloads"
    if not bpy.data.filepath and node.folder.startswith("//"):
        folder = Path.home() / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"film_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    r = sc.render
    r.filepath = str(out)
    r.image_settings.media_type = "VIDEO"            # gotcha 5: the media type before the file format
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format, r.ffmpeg.codec = "MPEG4", "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.ffmpeg.audio_codec = "AAC"
    r.use_sequencer = True
    bpy.ops.render.render(animation=True, scene=sc.name)
    if not out.is_file():
        raise RuntimeError("The film render wrote no file")
    node.last_render = str(out)
    return out
