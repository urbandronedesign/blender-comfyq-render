# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: who the student is and which ComfyQ to use.

Stored in Blender's user preferences, never in a .blend. Passwords and tokens
stay in the profiles file or the environment (see client/profile.py).
"""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from .client.profile import config_path, list_profiles, load_profile

_UNLOCK: dict = {"key": None, "on": False}


_PROFILES: dict = {"key": None, "names": []}


def _profiles() -> list[str]:
    """Profile names of the profiles file, re-read only when the file changes (the preferences redraw often)."""
    path = config_path()
    try:
        key = (str(path), path.stat().st_mtime if path.is_file() else 0.0)
    except OSError:
        key = (str(path), 0.0)
    if _PROFILES["key"] != key:
        try:
            _PROFILES["names"] = list_profiles()
        except Exception:  # noqa: BLE001 - a broken file: show nothing rather than break the preferences
            _PROFILES["names"] = []
        _PROFILES["key"] = key
    return _PROFILES["names"]


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
    key = (str(path), p.profile, os.environ.get("COMFYQ_ADMIN_PASSWORD", ""),
           os.environ.get("COMFYQ_BRIDGE_PROFILE", ""), mtime)
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
        description="Use this profile of the profiles file (server address, passwords). Leave empty for the "
                    "file's default profile")
    url: StringProperty(
        name="Server Address", default="",
        description="Override the profile's server address, for example http://192.168.1.10:3000")
    auto_connect: BoolProperty(name="Connect on Startup", default=False,
                                description="Connect when Blender starts (once a student name is set)")
    mcp_enabled: BoolProperty(
        name="AI Agents", default=False, update=_mcp_toggle,
        description="Let AI agents on this PC (Claude Code, Codex) render passes, book jobs, import results and "
                    "edit the ComfyQ graph. Only this PC can connect")
    mcp_port: IntProperty(name="Port", default=8790, min=1024, max=65535,
                          update=_mcp_toggle,
                          description="Network port the agents connect to on this PC")
    solo_mode: BoolProperty(
        name="Solo Mode: Load Models on Demand", default=False,
        description="Load the models a graph needs when running it (each switch can restart ComfyQ for 1 to 2 "
                    "minutes). Only while nobody else has a job; never during class")
    mcp_teacher: BoolProperty(
        name="Teacher Tools for Agents", default=False, update=_teacher_toggle,
        description="Let AI agents change which models ComfyQ serves for the whole class (load, stop, "
                    "calibrate). Agents see the change when they reconnect")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "student_name")
        row = col.row(align=True)
        row.prop(self, "profile")
        row.label(text="(" + ", ".join(_profiles()) + ")")
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
            col.operator("comfyq.copy_agent_command", icon="COPYDOWN")
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
