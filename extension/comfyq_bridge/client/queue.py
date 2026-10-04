# SPDX-License-Identifier: Apache-2.0
"""Where a job stands in ComfyQ's queue, from the jobs the server sends to every client (no bpy).

Each served workflow is its own lane on the GPU: a job waits behind the unfinished jobs of the same
workflow — the running one first, then by booking time.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import EXECUTING, Job

RUNNING = frozenset({EXECUTING, "collecting-outputs", "submitted", "uploading-inputs"})


@dataclass(frozen=True)
class Place:
    position: int          # 0 = running now; 1 = next; n = n-th in line
    running_for: str       # who the lane's running job belongs to ("" when nothing runs)
    waiting: int           # jobs waiting in this lane (all users)


def place(jobs: list[Job], job_id: str) -> Place | None:
    me = next((j for j in jobs if j.id == job_id), None)
    if me is None or me.done:
        return None
    lane = [j for j in jobs if j.workflow_id == me.workflow_id and not j.done]
    running = [j for j in lane if j.phase in RUNNING]
    waiting = sorted((j for j in lane if j.phase not in RUNNING),
                     key=lambda j: (j.time_slot or 0, j.started_at or 0, j.id))
    who = running[0].user_id if running else ""
    if me in running:
        return Place(0, me.user_id, len(waiting))
    return Place(waiting.index(me) + 1, who, len(waiting))


def describe(p: Place, me: str = "") -> str:
    """'Running on the GPU' / 'Next in line · now running: Tom' / '3rd in line · now running: Tom'."""
    if p.position == 0:
        return "Running on the GPU"
    who = "" if not p.running_for else (f" · now running: {p.running_for}" +
                                        (" (this PC)" if p.running_for == me else ""))
    if p.position == 1:
        return "Next in line" + who
    return f"{_ordinal(p.position)} in line" + who


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
