# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: who the student is and which ComfyQ to use.

Stored in Blender's user preferences, never in a .blend. Passwords and tokens
stay in the profiles file or the environment (see client/profile.py).
"""
from __future__ import annotations

import getpass
from pathlib import Path

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from .client.profile import config_path, list_profiles


def _default_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "student"


def _mcp_toggle(self, context) -> None:
    # A named function: property definitions are evaluated by Blender in its own namespace
    # (string annotations), where a lambda here could not see this module's functions.
    _mcp_update(self)


class ComfyQPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    student_name: StringProperty(
        name="Student name", default=_default_user(),
        description="Shown on your jobs in ComfyQ; you can only cancel jobs booked under this name")
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
