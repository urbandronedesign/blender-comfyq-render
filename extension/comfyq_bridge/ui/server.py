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
from ..wording import count

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
        except Exception as e:  # noqa: BLE001 - shown in the panel
            SERVER["error"] = str(e)
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
        row = box.row()
        row.label(text=ln.get("name") or ln.get("workflowId"), icon="CHECKMARK")
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=" · ".join(x for x in ("busy" if ln.get("busy") else "", "main" if ln.get("primary") else "",
                                              f"{ln['vramGb']:.1f} GB" if ln.get("vramGb") else "") if x))
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
            row.label(text=names.get(wf_id, wf_id))
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
                      "instead of the main one (ComfyQ restarts for 1 to 2 minutes, for everyone)")
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


classes = (COMFYQ_OT_server_load_model, COMFYQ_PT_server, COMFYQ_PT_image_server)
