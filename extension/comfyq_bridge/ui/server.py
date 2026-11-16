# SPDX-License-Identifier: GPL-3.0-or-later
"""The teacher's Server panel: what ComfyQ serves (and the GPU memory it uses), the class queue, who is
connected, and Load Model. Only on the teacher's PC (`prefs.teacher_unlocked`: the profile holds ComfyQ's
admin password and is not in Class mode).

The served lanes and VRAM come from `GET /admin/lanes`, read on a thread every few seconds while the panel
is open (draw code never calls the network); the queue and the users come with ComfyQ's live state.
"""
from __future__ import annotations

import threading
import time

import bpy
from bpy.props import EnumProperty

from ..runtime.session import SESSION
from ..wording import count, middle

REFRESH_S = 5.0
SERVER: dict = {"t": 0.0, "drawn": 0.0, "busy": False, "lanes": [], "card": {}, "error": "", "dirty": False}


def visible() -> bool:
    """The panel is shown (and may be refreshed): teacher's PC, connected."""
    from ..prefs import teacher_unlocked
    return SESSION.connected and teacher_unlocked()


def tick() -> bool:
    """From the add-on timer: refresh the lanes while the panel was drawn lately. True = redraw."""
    dirty, SERVER["dirty"] = SERVER["dirty"], False
    now = time.monotonic()
    if SERVER["busy"] or now - SERVER["drawn"] > 2 * REFRESH_S or now - SERVER["t"] < REFRESH_S:
        return dirty
    client = SESSION.client
    if client is None or not visible():
        return dirty
    SERVER["busy"], SERVER["t"] = True, now

    def work():
        try:
            info = client.server_lanes()
            SERVER.update(lanes=info.get("lanes") or [], card=info.get("card") or {}, error="")
        except Exception as e:  # noqa: BLE001 - shown in the panel, in plain words
            first = (str(e).splitlines() or ["no answer"])[0]
            SERVER["error"] = "Could not read the server's models: " + (first[:90] + "…" if len(first) > 90 else first)
        finally:
            SERVER["busy"], SERVER["dirty"] = False, True
    threading.Thread(target=work, daemon=True, name="comfyq-server-panel").start()
    return dirty


def _phase(job, lane_jobs) -> str:
    from ..client.queue import RUNNING
    if job.phase in RUNNING:
        return f"running {job.progress * 100:.0f}%" if job.progress is not None else "running"
    return f"waiting ({lane_jobs.index(job) + 1})"


def draw_server(layout, context) -> None:
    from ..client.queue import RUNNING
    SERVER["drawn"] = time.monotonic()
    client = SESSION.client
    names = {w.id: w.name for w in SESSION.workflows}

    card = SERVER["card"]
    from ..graph.run import solo_on
    if solo_on():                              # this PC switches the class's model: say it where the models are
        row = layout.row()
        row.alert = True
        row.label(text="Solo is on: Run Graph loads models from this PC", icon="ERROR")
    if SERVER["error"]:
        row = layout.row()
        row.alert = True
        row.label(text=SERVER["error"], icon="ERROR")
    if card.get("vramGb"):
        used, total = float(card.get("usedGb") or 0), float(card["vramGb"])
        layout.progress(factor=min(1.0, used / total), type="BAR", text=f"GPU memory {used:.1f} / {total:.0f} GB")
    elif not SERVER["t"]:
        layout.label(text="Reading the server…", icon="SORTTIME")

    box = layout.box().column(align=True)
    box.label(text="Served Models")
    for ln in SERVER["lanes"] or [{"workflowId": la.workflow_id, "name": la.name, "primary": la.primary,
                                   "busy": la.busy} for la in (client.lanes() if client else [])]:
        row = box.row(align=True)               # name on its own line (a narrow popover cut it), details under it
        name = ln.get("name") or names.get(ln.get("workflowId"), "") or "Model"
        row.label(text=middle(name, 26 if ln.get("primary") else 20), icon="CHECKMARK")   # room left by the X
        if not ln.get("primary"):              # an extra model: the teacher can free its memory
            row.operator("comfyq.server_stop_model", text="", icon="X").workflow_id = ln.get("workflowId", "")
        info = " · ".join(x for x in ("running a job" if ln.get("busy") else "", "main" if ln.get("primary") else "",
                                      f"{ln['vramGb']:.1f} GB" if ln.get("vramGb") else "") if x)
        if info:
            sub = box.row()
            sub.enabled = False
            sub.label(text=info, icon="BLANK1")
    layout.operator("comfyq.server_load_model", icon="PACKAGE")

    jobs = client.active_jobs() if client else []
    box = layout.box().column(align=True)
    box.label(text=f"Queue: {count(len(jobs), 'job')}" if jobs else "Queue: empty")
    by_lane: dict[str, list] = {}
    for j in sorted(jobs, key=lambda j: (j.time_slot or 0, j.started_at or 0, j.id)):
        by_lane.setdefault(j.workflow_id, []).append(j)
    for wf_id, lane in by_lane.items():
        waiting = [j for j in lane if j.phase not in RUNNING]
        for j in lane:
            row = box.row()
            row.label(text=j.user_id or "?", icon="USER")
            row.label(text=middle(names.get(wf_id, "a model"), 24))
            row.label(text=_phase(j, waiting))

    users = client.users() if client else []
    box = layout.box().column(align=True)
    box.label(text=f"Connected: {count(len(users), 'person', 'people')}")
    flow = box.grid_flow(columns=2, even_columns=True, align=True)
    for u in users:
        flow.label(text=u, icon="USER")


def _model_items(self, context):
    served = SESSION.served_ids()
    items = [(w.id, w.name, w.description or w.id, "CHECKMARK" if w.id in served else "NONE", n)
             for n, w in enumerate(sorted((w for w in SESSION.workflows if not w.unavailable),
                                          key=lambda w: (w.id not in served, w.name)))]
    _model_items.cache = items                 # Blender needs the strings kept alive
    return items or [("", "No models", "")]


class COMFYQ_OT_server_load_model(bpy.types.Operator):
    bl_idname = "comfyq.server_load_model"
    bl_label = "Load Model..."
    bl_description = ("Choose a model for ComfyQ to serve: beside the current ones when it fits, otherwise "
                      "instead of the main one (jobs wait while it loads, about a minute, for everyone)")
    bl_options = {"INTERNAL"}
    bl_property = "workflow_id"
    workflow_id: EnumProperty(name="Model", items=_model_items)

    @classmethod
    def poll(cls, context):
        if not SESSION.connected:
            cls.poll_message_set("Connect to ComfyQ first")
            return False
        if not visible():
            cls.poll_message_set("Only on the teacher's PC")
            return False
        return True

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.workflow_id:
            return {"CANCELLED"}
        if self.workflow_id in SESSION.served_ids():
            self.report({"INFO"}, "This model is served already")
            return {"CANCELLED"}
        # the switch itself asks first (Load Model confirmation), like everywhere else
        return bpy.ops.comfyq.graph_load_model("INVOKE_DEFAULT", workflow_id=self.workflow_id)


class COMFYQ_OT_server_stop_model(bpy.types.Operator):
    bl_idname = "comfyq.server_stop_model"
    bl_label = "Stop Serving..."
    bl_description = "Stop serving this extra model and free its GPU memory (the main model stays)"
    bl_options = {"INTERNAL"}
    workflow_id: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        if not visible():
            cls.poll_message_set("Only on the teacher's PC")
            return False
        return True

    def invoke(self, context, event):
        name = next((w.name for w in SESSION.workflows if w.id == self.workflow_id), "this model")
        busy = any(ln.get("workflowId") == self.workflow_id and ln.get("busy") for ln in SERVER["lanes"])
        msg = ("A job is running on it and will fail" if busy else
               "Students' jobs on it are refused until it is loaded again")
        return context.window_manager.invoke_confirm(self, event, title=f"Stop Serving {middle(name, 40)}?",
                                                     message=msg, confirm_text="Stop Serving", icon="WARNING")

    def execute(self, context):
        client, wf_id = SESSION.client, self.workflow_id
        if client is None:
            self.report({"ERROR"}, "Not connected")
            return {"CANCELLED"}
        lane = next((ln for ln in SERVER["lanes"] if ln.get("workflowId") == wf_id), None)
        if lane is not None and lane.get("primary"):
            self.report({"ERROR"}, "This is the main model: load another one instead (Load Model...)")
            return {"CANCELLED"}

        def done(_r):
            SERVER["t"] = 0.0                      # read the lanes again now
            SESSION.status_note = "Stopped serving a model: its GPU memory is free"
        SESSION.run(lambda: client.close_lane(wf_id), done, label="Stopping the model")
        self.report({"INFO"}, "Stopping the model…")
        return {"FINISHED"}


class _Server:
    bl_region_type = "UI"
    bl_category = "ComfyQ"
    bl_label = "Server (Teacher)"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return visible()

    def draw(self, context):
        draw_server(self.layout, context)


class COMFYQ_PT_server(_Server, bpy.types.Panel):
    bl_space_type = "VIEW_3D"


class COMFYQ_PT_image_server(_Server, bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"


classes = (COMFYQ_OT_server_load_model, COMFYQ_OT_server_stop_model, COMFYQ_PT_server, COMFYQ_PT_image_server)
