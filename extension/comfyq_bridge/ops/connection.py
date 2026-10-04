# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import bpy
from bpy.props import StringProperty

from ..prefs import prefs
from ..runtime.session import SESSION


def _name_ok(name: str) -> bool:
    return bool(name.strip())


class COMFYQ_OT_connect(bpy.types.Operator):
    bl_idname = "comfyq.connect"
    bl_label = "Connect"
    bl_description = "Connect to the ComfyQ server set in the add-on preferences"

    student_name: StringProperty(name="Student Name", options={"SKIP_SAVE"},
                                 description="Name shown on jobs in ComfyQ, so everyone can tell whose job is whose")

    def invoke(self, context, event):
        p = prefs(context)
        if not (p.name_confirmed and _name_ok(p.student_name)):
            # Asked once: lab PCs often share one Windows login, so a default name would mix everyone's jobs
            self.student_name = p.student_name if p.name_confirmed else ""
            return context.window_manager.invoke_props_dialog(self, title="Name Shown on Your Jobs",
                                                              confirm_text="Connect")
        return self.execute(context)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "student_name", text="")
        col.label(text="The class sees this name on the server's queue", icon="INFO")

    def execute(self, context):
        if not bpy.app.online_access:
            self.report({"ERROR"}, "Online access is disabled (Preferences › System › Network)")
            return {"CANCELLED"}
        p = prefs(context)
        if _name_ok(self.student_name):
            p.student_name, p.name_confirmed = self.student_name.strip(), True
        if not _name_ok(p.student_name):
            self.report({"ERROR"}, "Type the student name first")
            return {"CANCELLED"}
        SESSION.connect(p.profile, p.student_name.strip(), p.url.strip())
        return {"FINISHED"}


class COMFYQ_OT_set_student_name(bpy.types.Operator):
    bl_idname = "comfyq.set_student_name"
    bl_label = "Change Name..."
    bl_description = "Change the name shown on jobs in ComfyQ (reconnects)"

    student_name: StringProperty(name="Student Name", options={"SKIP_SAVE"})

    def invoke(self, context, event):
        self.student_name = prefs(context).student_name
        return context.window_manager.invoke_props_dialog(self, title="Name Shown on Jobs", confirm_text="Save")

    def execute(self, context):
        if not _name_ok(self.student_name):
            self.report({"ERROR"}, "The name cannot be empty")
            return {"CANCELLED"}
        p = prefs(context)
        p.student_name, p.name_confirmed = self.student_name.strip(), True
        if SESSION.client is not None:                 # the server knows us by name: register again
            SESSION.connect(p.profile, p.student_name, p.url.strip())
        self.report({"INFO"}, f"Jobs are now booked as “{p.student_name}”")
        return {"FINISHED"}


class COMFYQ_OT_disconnect(bpy.types.Operator):
    bl_idname = "comfyq.disconnect"
    bl_label = "Disconnect"
    bl_description = "Disconnect from the ComfyQ server (jobs keep running there)"

    def execute(self, context):
        SESSION.close()
        self.report({"INFO"}, "Disconnected from ComfyQ")
        return {"FINISHED"}


class COMFYQ_OT_refresh(bpy.types.Operator):
    bl_idname = "comfyq.refresh"
    bl_label = "Refresh"
    bl_description = "Reload the workflow list and what the server is serving"

    @classmethod
    def poll(cls, context):
        if SESSION.client is None:
            cls.poll_message_set("Connect to ComfyQ first")
            return False
        return True

    def execute(self, context):
        client = SESSION.client
        SESSION.specs.clear()

        def work():
            if hasattr(client.transport, "refresh"):
                client.transport.refresh()
            return client.workflows(), client.gpu()

        def done(result):
            SESSION.workflows, SESSION.gpu = result

        SESSION.run(work, done, label="Refreshing")
        return {"FINISHED"}


classes = (COMFYQ_OT_connect, COMFYQ_OT_set_student_name, COMFYQ_OT_disconnect, COMFYQ_OT_refresh)
