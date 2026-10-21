# SPDX-License-Identifier: GPL-3.0-or-later
"""ComfyQ Render — send scene passes to ComfyQ workflows and import the results.

Blender 5.x extension: public bpy only.
`client/` holds all ComfyQ protocol code and does not import bpy.
"""
from __future__ import annotations

import sys
import traceback

# Reinstalling or updating an enabled extension re-runs only this file; Python would keep the
# old submodules (ops/, client/, ...) from sys.modules and the update would silently not apply.
# Drop them so the imports below load the new code.
for _name in [m for m in sys.modules if m.startswith(__name__ + ".")]:
    del sys.modules[_name]

import bpy

from . import graph, mcp, prefs, props
from .ops import connection, generate, results, workflow
from .passes import animation
from .runtime.session import PUMP_INTERVAL_S, SESSION
from .ui import panel, player, server

_CLASSES = (*connection.classes, *workflow.classes, *generate.classes, *results.classes, *panel.classes,
            *graph.classes, *server.classes)


def _tick() -> float:
    try:
        for window in bpy.context.window_manager.windows:
            workflow.ensure_loaded(window.scene)   # settings of the chosen workflows, after (re)connects/loads
        graph.sync_all()                           # graph nodes follow the workflows' parameters
        _teacher_state()
        server_changed = server.tick()             # the teacher's Server panel (lanes, VRAM) while it is open
    except Exception:  # noqa: BLE001 - never let this stop the pump timer
        traceback.print_exc()
        server_changed = False
    if SESSION.pump() or server_changed:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in ("VIEW_3D", "IMAGE_EDITOR"):
                    area.tag_redraw()
    return PUMP_INTERVAL_S


def _auto_connect() -> None:
    try:
        p = prefs.prefs()
    except KeyError:
        return
    # no name typed yet: the student is asked at the first Connect (button), never connected as nobody
    named = p.name_confirmed and p.student_name.strip()
    if p.auto_connect and named and bpy.app.online_access and SESSION.client is None:
        SESSION.connect(p.profile, p.student_name.strip(), p.url.strip())
    return


def _start_mcp() -> None:
    """The MCP server for AI agents, when the user turned it on (preferences)."""
    try:
        p = prefs.prefs()
    except KeyError:
        return
    _teacher_state()
    if p.mcp_enabled and not mcp.running():
        mcp.start(p.mcp_port)


def _teacher_state() -> None:
    """The agents' teacher tools: ticked in the preferences AND the profile holds ComfyQ's admin password
    (read by the MCP server's threads, which must not touch bpy)."""
    try:
        p = prefs.prefs()
    except KeyError:
        return
    mcp.STATE["teacher"] = bool(p.mcp_teacher) and prefs.teacher_unlocked(p)


def _reattach_all() -> None:
    """Pick up the jobs recorded in every scene of the open file (on connect and after a file loads)."""
    records = [(r.job_id, r.workflow_id) for sc in bpy.data.scenes for r in sc.comfyq.jobs]
    SESSION.reattach(records)


@bpy.app.handlers.persistent
def _on_load(_dummy) -> None:
    SESSION.jobs.clear()
    if SESSION.client is not None:
        _reattach_all()
    # A ComfyQ window saved in the file comes back with the file: find it again, show the ComfyQ tab.
    bpy.app.timers.register(workflow.adopt_saved_window, first_interval=0.5)


def register() -> None:
    prefs.register()
    props.register()
    graph.tree.REGISTERING[0] = True
    try:
        for c in _CLASSES:
            bpy.utils.register_class(c)
    finally:
        graph.tree.REGISTERING[0] = False
    player.register()
    panel.register_extras()
    graph.register_extras()
    animation.register()
    SESSION.on_job_done = generate.on_job_done
    SESSION.on_connected = _reattach_all
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_tick, first_interval=PUMP_INTERVAL_S, persistent=True)
    # persistent: Blender opened WITH a .blend loads it after registering add-ons, and a file load drops
    # non-persistent timers (gotcha 13): neither auto-connect nor the agents' server ever started
    bpy.app.timers.register(_auto_connect, first_interval=1.0, persistent=True)
    bpy.app.timers.register(_start_mcp, first_interval=1.0, persistent=True)


def unregister() -> None:
    mcp.stop()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    animation.unregister()
    panel.unregister_extras()
    graph.unregister_extras()
    player.unregister()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    SESSION.close()
    SESSION.on_job_done = None
    SESSION.on_connected = None
    graph.tree.REGISTERING[0] = True
    try:
        for c in reversed(_CLASSES):
            bpy.utils.unregister_class(c)
    finally:
        graph.tree.REGISTERING[0] = False
    props.unregister()
    prefs.unregister()
