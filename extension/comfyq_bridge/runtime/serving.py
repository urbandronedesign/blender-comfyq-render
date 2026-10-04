# SPDX-License-Identifier: GPL-3.0-or-later
"""Changing what ComfyQ serves: used by the MCP teacher tools and by solo mode (graph runs that load
the models they need). No bpy. ComfyQ's own admin routes do the work; the new served set arrives over
the socket (`SESSION.served_ids()`), so waiting for it is the caller's job.

how = "alongside": a lane next to the main workflow (no restart; must fit in VRAM)
      "main":      replace the main workflow (ComfyQ restarts ~1–2 min; running jobs would stop)
      "auto":      alongside if it fits, else main
"""
from __future__ import annotations

from ..client.api import ComfyQClient
from ..client.http import ComfyQError


def choose(client: ComfyQClient, wf_id: str, how: str = "auto", force: bool = False) -> str:
    """The way to serve `wf_id` now ("alongside" / "main"), or ComfyQError saying why not."""
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


def serve(client: ComfyQClient, wf_id: str, how: str, force: bool = False) -> None:
    """Ask ComfyQ to serve it (blocking HTTP: opening a lane waits for the model to load)."""
    if how == "alongside":
        client.open_lane(wf_id, force=force)
    else:
        client.activate(wf_id)


def main_workflow(client: ComfyQClient) -> str:
    """The workflow ComfyQ serves as its main one ('' if none)."""
    return next((ln.workflow_id for ln in client.lanes() if ln.primary), "")


def blockers(client: ComfyQClient) -> list[str]:
    """Jobs ComfyQ has not finished, anyone's: while there are some, nothing may be switched."""
    return [f"{j.user_id or '?'} · {j.workflow_id} ({j.phase})" for j in client.active_jobs()]
