# SPDX-License-Identifier: GPL-3.0-or-later
"""Changing what ComfyQ serves: used by the MCP teacher tools and by solo mode (graph runs that load
the models they need). No bpy. ComfyQ's own admin routes do the work; the new served set arrives over
the socket (`SESSION.served_ids()`), so waiting for it is the caller's job.

how = "alongside": a lane next to the main workflow (no restart; must fit in VRAM)
      "main":      replace the main workflow (ComfyQ restarts ~1–2 min; running jobs would stop)
      "auto":      alongside if it fits, else main
"""
from __future__ import annotations

import time

from ..client.api import ComfyQClient
from ..client.http import ComfyQError

RESTORE_TIMEOUT_S = 300


def restoring(client: ComfyQClient) -> list[str]:
    """Lanes ComfyQ remembers but has not brought back yet. After a restart it reopens them in the background,
    ~15 s each, and until one is back its port is free in ComfyQ's eyes: a lane opened then gets the SAME port
    (both report it, one ComfyUI runs, the new lane's first job fails "comfyui-process-exited"), and the fit
    check ignores its VRAM (docs/RUNBOOK.md §7.1, 1b)."""
    try:
        info = client.server_lanes()
        if info.get("available") is False:            # not serving at all: nothing is being restored
            return []
        cfg = (client.admin_config().get("config") or {}).get("workflows") or {}
    except ComfyQError:
        return []
    have = {ln.get("workflowId") for ln in info.get("lanes") or []}
    return [w for w in cfg.get("extraLaneWorkflowIds") or [] if w not in have]


def wait_restored(client: ComfyQClient, timeout: float = RESTORE_TIMEOUT_S, sleep=time.sleep,
                  clock=time.monotonic) -> list[str]:
    """Block (worker thread) until ComfyQ has brought its remembered lanes back; returns those still missing
    at the timeout (a lane that fails to come back is dropped from ComfyQ's list, so this normally ends)."""
    t0 = clock()
    while True:
        left = restoring(client)
        if not left or clock() - t0 >= timeout:
            return left
        sleep(2)


def choose(client: ComfyQClient, wf_id: str, how: str = "auto", force: bool = False) -> str:
    """The way to serve `wf_id` now ("alongside" / "main"), or ComfyQError saying why not. Waits first for the
    lanes ComfyQ is still restoring after a restart (their ports and VRAM are not counted until they are back)."""
    wait_restored(client)
    info = client.server_lanes()
    fit = (info.get("fit") or {}).get(wf_id) or {}
    if how == "auto":
        how = "alongside" if fit.get("ok") else "main"
    if how == "alongside" and not fit.get("ok") and not force:
        raise ComfyQError(f"{wf_id} does not fit alongside ({fit.get('reason')}: needs {fit.get('needGb')} GB, "
                          f"{fit.get('freeGb')} GB free). Use how='main' (replaces the main workflow, ComfyQ "
                          f"restarts), stop a lane first, or force when the size is unknown.")
    busy = [ln.get("workflowId") for ln in info.get("lanes") or [] if ln.get("busy")]
    if how == "main" and busy and not force:
        raise ComfyQError(f"A job is running on {', '.join(busy)}: switching the main workflow restarts "
                          f"ComfyQ and would stop it.")
    return how


HEADROOM_GB = 2.0                                 # ComfyQ's own margin: free = card − used − 2


def make_room_for_main(client: ComfyQClient, wf_id: str) -> list[str]:
    """Close extra lanes that would not fit beside `wf_id` as the main model (largest first). ComfyQ restores its
    remembered lanes after the restart with `force`, so a lane left open beside a big model (a 19 GB Qwen lane next to
    a 30 GB LTX) puts the main one in low-VRAM mode: 10–12 min per clip instead of 2.5–4 (2026-10-22). Unknown size:
    close them all. Returns the lanes closed."""
    info = client.server_lanes()
    card = float((info.get("card") or {}).get("vramGb") or 0)
    need = ((info.get("fit") or {}).get(wf_id) or {}).get("needGb")
    extras = sorted(((ln["workflowId"], float(ln.get("vramGb") or 0)) for ln in info.get("lanes") or []
                     if not ln.get("primary") and ln.get("workflowId") != wf_id), key=lambda x: -x[1])
    closed = []
    while extras and (need is None or not card or float(need) + sum(v for _w, v in extras) > card - HEADROOM_GB):
        wf, _v = extras.pop(0)
        client.close_lane(wf)
        closed.append(wf)
    return closed


def serve(client: ComfyQClient, wf_id: str, how: str, force: bool = False) -> list[str]:
    """Ask ComfyQ to serve it (blocking HTTP: opening a lane waits for the model to load). Returns the extra lanes
    closed to make room for a new main model."""
    if how == "alongside":
        wait_restored(client)                      # a lane opened during the restore takes a restoring lane's port
        client.open_lane(wf_id, force=force)
        return []
    closed = make_room_for_main(client, wf_id)
    client.activate(wf_id)
    return closed


def blockers(client: ComfyQClient) -> list[str]:
    """Jobs ComfyQ has not finished, anyone's: while there are some, nothing may be switched."""
    return [f"{j.user_id or '?'} · {j.workflow_id} ({j.phase})" for j in client.active_jobs()]
