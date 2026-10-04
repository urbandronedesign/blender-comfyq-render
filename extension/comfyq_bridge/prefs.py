# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: who the student is and which ComfyQ to use.

Stored in Blender's user preferences, never in a .blend. Passwords and tokens
stay in the profiles file or the environment (see client/profile.py).
"""
from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from .client.profile import config_path, list_profiles, load_profile

_UNLOCK: dict = {"key": None, "on": False}


def teacher_unlocked(p=None) -> bool:
    """True on the teacher's PC: the active profile carries ComfyQ's admin password (profiles file or
    COMFYQ_ADMIN_PASSWORD). Solo mode, Load Model and the agents' teacher tools need it, so a student
    cannot switch what ComfyQ serves for the whole class. Cached; re-read when the profiles file changes."""
    try:
        p = p or prefs()
    except KeyError:
        return False
    path = config_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    key = (p.profile, os.environ.get("COMFYQ_ADMIN_PASSWORD", ""), os.environ.get("COMFYQ_BRIDGE_PROFILE", ""), mtime)
    if _UNLOCK["key"] != key:
        try:
            _UNLOCK["on"] = bool(load_profile(p.profile or None).admin_password)
        except Exception:  # noqa: BLE001 - a broken profiles file = locked
            _UNLOCK["on"] = False
        _UNLOCK["key"] = key
    return _UNLOCK["on"]


def _mcp_toggle(self, context) -> None:
    # A named function: property definitions are evaluated by Blender in its own namespace
    # (string annotations), where a lambda here could not see this module's functions.
    _mcp_update(self)


def _teacher_toggle(self, context) -> None:
    from . import mcp
    mcp.STATE["teacher"] = bool(self.mcp_teacher) and teacher_unlocked(self)


class ComfyQPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    student_name: StringProperty(
        name="Student Name", default="",
        description="Name shown on jobs in ComfyQ. Only jobs booked under this name can be cancelled")
    name_confirmed: BoolProperty(default=False, options={"HIDDEN"},
                                 description="The student typed this name (asked once, at the first Connect)")
    profile: StringProperty(
        name="Profile", default="",
        description="Profile from the profiles file (empty = its default, or the local machine)")
    url: StringProperty(
        name="Server URL override", default="",
        description="e.g. http://192.168.1.10:3000 — overrides the profile's address")
    auto_connect: BoolProperty(name="Connect on startup", default=False)
    mcp_enabled: BoolProperty(
        name="AI agents (MCP server)", default=False, update=_mcp_toggle,
        description="Let AI agents on this PC (Claude Code, Codex…) use this scene and ComfyQ through scoped "
                    "tools (render passes, book jobs, import results, edit the ComfyQ graph). Local only, "
                    "with a token")
    mcp_port: IntProperty(name="Port", default=8790, min=1024, max=65535,
                          update=_mcp_toggle,
                          description="Local port of the MCP server (127.0.0.1 only)")
    solo_mode: BoolProperty(
        name="Solo: load models on demand", default=False,
        description="When you are the only one using ComfyQ: Run Graph loads the models a node needs (switching "
                    "what ComfyQ serves, one model at a time; ComfyQ restarts ~1–2 min per switch). Refused while "
                    "anyone else has a job on the server. Never in class: the queue needs one fixed model")
    mcp_teacher: BoolProperty(
        name="Teacher tools: agents may load models", default=False, update=_teacher_toggle,
        description="Also give agents the tools that change what ComfyQ serves for the whole class: serve "
                    "a workflow (alongside, or as the main one — ComfyQ restarts), stop a lane, calibrate. "
                    "Leave off on student PCs. Agents see the change when they reconnect")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "student_name")
        row = col.row(align=True)
        row.prop(self, "profile")
        row.label(text="(" + ", ".join(list_profiles()) + ")")
        col.prop(self, "url")
        col.prop(self, "auto_connect")
        col.separator()
        from . import mcp
        row = col.row(align=True)
        row.prop(self, "mcp_enabled")
        sub = row.row(align=True)
        sub.enabled = not self.mcp_enabled
        sub.prop(self, "mcp_port")
        if mcp.STATE["error"]:
            col.label(text=mcp.STATE["error"], icon="ERROR")
        elif mcp.running():
            col.label(text=f"Running on {mcp.STATE['url']} · connection file {mcp.info_path()}", icon="CHECKMARK")
            launcher = Path(__file__).parent / "mcp" / "launcher.py"
            col.label(text="Agents run:  python " + str(launcher).replace("\\", "/"), icon="CONSOLE")
        col.separator()
        box = col.box().column()
        box.label(text="Teacher", icon="COMMUNITY")
        if teacher_unlocked(self):
            box.prop(self, "solo_mode")
            if self.solo_mode:
                box.label(text="Solo is on: this PC switches the model ComfyQ serves for everyone", icon="ERROR")
            row = box.row()
            row.enabled = self.mcp_enabled
            row.prop(self, "mcp_teacher")
        else:
            box.label(text="Solo mode and the agents' teacher tools appear when the profile holds ComfyQ's "
                           "admin password (admin_password in the profiles file)", icon="LOCKED")
        col.separator()
        col.label(text=f"Profiles file: {config_path()}", icon="FILE_TEXT")
        if not bpy.app.online_access:
            col.label(text="Online access is disabled (Preferences › System); the bridge cannot connect",
                      icon="ERROR")


def _mcp_update(p) -> None:
    from . import mcp
    if p.mcp_enabled:
        mcp.start(p.mcp_port)
    else:
        mcp.stop()


def prefs(context: bpy.types.Context | None = None) -> ComfyQPreferences:
    ctx = context or bpy.context
    return ctx.preferences.addons[__package__].preferences


def register() -> None:
    bpy.utils.register_class(ComfyQPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(ComfyQPreferences)
