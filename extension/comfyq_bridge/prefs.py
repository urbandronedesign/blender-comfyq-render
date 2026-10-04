# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: who the student is and which ComfyQ to use.

Stored in Blender's user preferences, never in a .blend. Passwords and tokens
stay in the profiles file or the environment (see client/profile.py).
"""
from __future__ import annotations

import os
from dataclasses import replace

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from .client.profile import Profile, config_path, list_profiles, load_profile

_ACTIVE: dict = {"key": None, "profile": None}


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


def active_profile(p=None) -> Profile | None:
    """The profile chosen in the preferences, read from the profiles file. Cached; re-read when the file,
    the chosen profile or the environment changes (draw code calls this often). None when the file is broken."""
    try:
        p = p or prefs()
    except KeyError:
        return None
    path = config_path()
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    key = (str(path), p.profile, os.environ.get("COMFYQ_ADMIN_PASSWORD", ""),
           os.environ.get("COMFYQ_BRIDGE_PROFILE", ""), mtime)
    if _ACTIVE["key"] != key:
        try:
            _ACTIVE["profile"] = load_profile(p.profile or None)
        except Exception:  # noqa: BLE001 - a broken profiles file = no profile (teacher controls locked)
            _ACTIVE["profile"] = None
        _ACTIVE["key"] = key
    return _ACTIVE["profile"]


def teacher_unlocked(p=None) -> bool:
    """True on the teacher's PC: the active profile carries ComfyQ's admin password (profiles file or
    COMFYQ_ADMIN_PASSWORD). Solo mode, Load Model and the agents' teacher tools need it, so a student
    cannot switch what ComfyQ serves for the whole class. A profile with `class_mode = true` (deployed to
    the student PCs) keeps them off even if an admin password is there too."""
    prof = active_profile(p)
    return bool(prof and prof.admin_password and not prof.class_mode)


def shared_presets_dirs(p=None) -> list[str]:
    """Shared class presets folders: the preference, then the profile's `presets_dir`."""
    try:
        p = p or prefs()
    except KeyError:
        return []
    prof = active_profile(p)
    out = [bpy.path.abspath(p.presets_dir)] if p.presets_dir.strip() else []
    if prof and prof.presets_dir:
        out.append(prof.presets_dir)
    return out


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
    header_buttons: BoolProperty(name="3D View Header Buttons", default=True,
                                 description="Show the ComfyQ and ComfyQ Window buttons in the 3D View header "
                                             "(Ctrl+Shift+Q opens the bar either way)")
    presets_dir: StringProperty(
        name="Shared Presets Folder", default="", subtype="DIR_PATH",
        description="A folder of class presets shared by the class (for example a network share on the "
                    "teacher's PC). The profiles file can set it for every PC with presets_dir")
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
        from . import mcp
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        if not bpy.app.online_access:
            row = layout.row()
            row.alert = True
            row.label(text="Online access is disabled (Preferences › System): the bridge cannot connect",
                      icon="ERROR")

        def section(idname: str, title: str, closed: bool = False):
            header, body = layout.panel(idname, default_closed=closed)
            header.label(text=title)
            return body

        body = section("comfyq_prefs_student", "Student")
        if body is not None:
            body.prop(self, "student_name", text="Name")
            body.prop(self, "header_buttons")
            body.prop(self, "presets_dir")
            prof = active_profile(self)
            if prof and prof.presets_dir:
                body.label(text=f"The profile also shares {prof.presets_dir}", icon="FILE_FOLDER")

        body = section("comfyq_prefs_server", "Server")
        if body is not None:
            split = body.split(factor=0.4, align=True)          # a label column like the properties' own
            lab = split.row()
            lab.alignment = "RIGHT"
            lab.label(text="Profile")
            split.menu("COMFYQ_MT_profiles", text=self.profile or f"Default ({_default_profile()})")
            body.prop(self, "auto_connect")
            body.operator("comfyq.test_connection", icon="LINKED")
            if _TEST["msg"]:
                sub = body.row()
                sub.alert = not _TEST["ok"]
                sub.label(text=_TEST["msg"], icon="CHECKMARK" if _TEST["ok"] else "ERROR")
            header, adv = body.panel("comfyq_prefs_advanced", default_closed=True)
            header.label(text="Advanced")
            if adv is not None:
                adv.prop(self, "url")
                adv.label(text=f"Profiles file: {config_path()}", icon="FILE_TEXT")

        body = section("comfyq_prefs_agents", "AI Agents")
        if body is not None:
            body.prop(self, "mcp_enabled", text="Allow AI Agents")
            sub = body.row()
            sub.enabled = not self.mcp_enabled
            sub.prop(self, "mcp_port")
            if mcp.STATE["error"]:
                body.label(text=mcp.STATE["error"], icon="ERROR")
            elif mcp.running():
                body.label(text=f"Running on {mcp.STATE['url']}", icon="CHECKMARK")
                body.operator("comfyq.copy_agent_command", icon="COPYDOWN")

        body = section("comfyq_prefs_teacher", "Teacher")
        if body is not None:
            if teacher_unlocked(self):
                body.prop(self, "solo_mode", text="Solo Mode")
                if self.solo_mode:
                    sub = body.row()
                    sub.alert = True
                    sub.label(text="This PC switches the model ComfyQ serves for everyone", icon="ERROR")
                sub = body.row()
                sub.enabled = self.mcp_enabled
                sub.prop(self, "mcp_teacher", text="Agents May Load Models")
            elif (prof := active_profile(self)) is not None and prof.class_mode:
                body.label(text="Class mode (set by the profile): teacher controls are off", icon="LOCKED")
            else:
                body.label(text="Locked: needs ComfyQ's admin password in this PC's profile", icon="LOCKED")


_TEST: dict = {"ok": False, "msg": ""}


def _default_profile() -> str:
    try:
        return load_profile(None).name
    except Exception:  # noqa: BLE001
        return "local"


class COMFYQ_MT_profiles(bpy.types.Menu):
    bl_idname = "COMFYQ_MT_profiles"
    bl_label = "Profile"
    bl_description = "Choose the profile of the profiles file (server address, passwords)"

    def draw(self, context):
        layout = self.layout
        layout.operator("comfyq.set_profile", text=f"Default ({_default_profile()})").name = ""
        for name in _profiles():
            layout.operator("comfyq.set_profile", text=name).name = name


class COMFYQ_OT_set_profile(bpy.types.Operator):
    bl_idname = "comfyq.set_profile"
    bl_label = "Use Profile"
    bl_description = "Use this profile (reconnect to apply it)"
    bl_options = {"INTERNAL"}
    name: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        prefs(context).profile = self.name
        _TEST["msg"] = ""
        return {"FINISHED"}


class COMFYQ_OT_test_connection(bpy.types.Operator):
    bl_idname = "comfyq.test_connection"
    bl_label = "Test Connection"
    bl_description = "Ask the ComfyQ server of this profile whether it answers, and what it serves"

    def execute(self, context):
        from .client.api import ComfyQClient
        from .client.http import ComfyQError
        p = prefs(context)
        try:
            prof = load_profile(p.profile or None)
            prof = replace(prof, url=p.url.strip().rstrip("/") or prof.url, timeout_s=4.0)
            client = ComfyQClient(prof)
            n = len(client.workflows())
            _TEST.update(ok=True, msg=f"{prof.url} answers: {n} models")
        except (ComfyQError, OSError, ValueError) as e:
            _TEST.update(ok=False, msg=str(e))
        self.report({"INFO"} if _TEST["ok"] else {"ERROR"}, _TEST["msg"])
        return {"FINISHED"}


def _mcp_update(p) -> None:
    from . import mcp
    if p.mcp_enabled:
        mcp.start(p.mcp_port)
    else:
        mcp.stop()


def prefs(context: bpy.types.Context | None = None) -> ComfyQPreferences:
    ctx = context or bpy.context
    return ctx.preferences.addons[__package__].preferences


_CLASSES = (COMFYQ_MT_profiles, COMFYQ_OT_set_profile, COMFYQ_OT_test_connection, ComfyQPreferences)


def register() -> None:
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister() -> None:
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
