# SPDX-License-Identifier: GPL-3.0-or-later
"""Shots: Blender's timeline markers bound to cameras (Timeline › Marker › Bind Camera to Markers, Ctrl+B).

A shot = a marker with a camera; it runs from the marker's frame to the frame before the next camera-bound marker
(the last one: to the scene's end). One scene then holds every shot of a film. Source nodes with a Shot render that
camera over those frames (`apply`); without one they use the scene camera and the scene's frame range as before.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import bpy


@dataclass(frozen=True)
class Shot:
    name: str
    camera: bpy.types.Object
    start: int
    end: int

    @property
    def frames(self) -> int:
        return self.end - self.start + 1


def bound_markers(scene: bpy.types.Scene) -> list:
    return sorted((m for m in scene.timeline_markers if m.camera is not None), key=lambda m: m.frame)


def resolve(scene: bpy.types.Scene, name: str) -> Shot | None:
    """The shot of marker `name`; None for "" (the scene camera and range); RuntimeError when it is not a shot."""
    if not name:
        return None
    marker = scene.timeline_markers.get(name)
    if marker is None:
        raise RuntimeError(f"No timeline marker “{name}”")
    if marker.camera is None:
        raise RuntimeError(f"Marker “{name}” has no camera: select a camera, then Timeline › Marker › Bind Camera "
                           "to Markers (Ctrl+B)")
    later = [m.frame for m in bound_markers(scene) if m.frame > marker.frame]
    end = (min(later) - 1) if later else scene.frame_end
    return Shot(name, marker.camera, marker.frame, max(marker.frame, end))


def signature(scene: bpy.types.Scene, name: str) -> tuple:
    """What a shot-fed result depends on, for the graph's change detection (no scene changes while hashing)."""
    try:
        s = resolve(scene, name)
    except RuntimeError as e:
        return ("invalid", str(e))
    return () if s is None else (s.name, s.camera.name, s.start, s.end)


def describe(scene: bpy.types.Scene, name: str) -> str:
    """One short line for a node: camera · frames · seconds, or why the shot cannot be used (fits a node's width;
    `resolve` raises the full instructions)."""
    try:
        s = resolve(scene, name)
    except RuntimeError:
        marker = scene.timeline_markers.get(name)
        return "No camera bound: Timeline › Ctrl+B" if marker is not None else "No marker with this name"
    if s is None:
        return ""
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    return f"{s.camera.name} · {s.start}–{s.end} · {s.frames / fps:.1f} s"


@contextmanager
def apply(scene: bpy.types.Scene, name: str):
    """Make the shot's camera and frames the scene's while the block runs (the passes read the scene camera and
    range); restored afterwards. Yields the Shot, or None when `name` is empty (nothing changes)."""
    s = resolve(scene, name)
    if s is None:
        yield None
        return
    saved = (scene.camera, scene.frame_start, scene.frame_end, scene.frame_current)
    try:
        scene.camera = s.camera
        _set_range(scene, s.start, s.end)
        yield s
    finally:
        scene.camera = saved[0]
        _set_range(scene, saved[1], saved[2])
        scene.frame_set(saved[3])


def _set_range(scene, start: int, end: int) -> None:
    # Blender pushes the end when the start passes it: widen first, then narrow
    scene.frame_end = max(scene.frame_end, end)
    scene.frame_start = start
    scene.frame_end = end
