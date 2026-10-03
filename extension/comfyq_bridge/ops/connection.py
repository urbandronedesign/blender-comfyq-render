# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import bpy

from ..prefs import prefs
from ..runtime.session import SESSION


class COMFYQ_OT_connect(bpy.types.Operator):
    bl_idname = "comfyq.connect"
    bl_label = "Connect"
    bl_description = "Connect to the ComfyQ server set in the add-on preferences"

    def execute(self, context):
        if not bpy.app.online_access:
            self.report({"ERROR"}, "Online access is disabled (Preferences › System › Network)")
            return {"CANCELLED"}
        p = prefs(context)
        SESSION.connect(p.profile, p.student_name.strip(), p.url.strip())
        return {"FINISHED"}


class COMFYQ_OT_disconnect(bpy.types.Operator):
    bl_idname = "comfyq.disconnect"
    bl_label = "Disconnect"

    def execute(self, context):
        SESSION.close()
        return {"FINISHED"}


class COMFYQ_OT_refresh(bpy.types.Operator):
    bl_idname = "comfyq.refresh"
    bl_label = "Refresh"
    bl_description = "Reload the workflow list and what the server is serving"

    @classmethod
    def poll(cls, context):
        return SESSION.client is not None

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


classes = (COMFYQ_OT_connect, COMFYQ_OT_disconnect, COMFYQ_OT_refresh)
