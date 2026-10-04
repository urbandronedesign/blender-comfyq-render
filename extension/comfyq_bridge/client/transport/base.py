# SPDX-License-Identifier: Apache-2.0
"""The protocol seam between the bridge and ComfyQ.

Phase 1 implements it over Socket.IO (`book_job`, `state_*`), phase 2 over
REST + SSE. Both must pass the same contract tests; nothing above this
interface knows which one is in use.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from ..models import Job, Lane

# Called from the transport's own thread with the jobs that changed.
JobListener = Callable[[list[Job]], None]
LaneListener = Callable[[list[Lane]], None]


class ComfyQTransport(ABC):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._lanes: list[Lane] = []
        self._users: list[str] = []               # names of the users connected to ComfyQ now
        self._job_listeners: list[JobListener] = []
        self._lane_listeners: list[LaneListener] = []

    # -- lifecycle -------------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    # -- actions ---------------------------------------------------------------
    @abstractmethod
    def book(self, workflow_id: str, params: dict[str, Any], user_id: str,
             prompt: str = "", scheduled_time: int | None = None) -> str:
        """Book a job as soon as possible (or at `scheduled_time`, ms epoch). Returns the job id."""

    @abstractmethod
    def cancel(self, job_id: str) -> None: ...

    # -- live state --------------------------------------------------------------
    def job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def lanes(self) -> list[Lane]:
        with self._lock:
            return list(self._lanes)

    def users(self) -> list[str]:
        """Names of everyone connected to ComfyQ now (each name once, in connection order)."""
        with self._lock:
            return list(self._users)

    def active_jobs(self) -> list[Job]:
        """Every job ComfyQ has not finished, anyone's (scheduled, queued, running)."""
        with self._lock:
            return [j for j in self._jobs.values() if not j.done]

    def on_jobs(self, fn: JobListener) -> None:
        self._job_listeners.append(fn)

    def off_jobs(self, fn: JobListener) -> None:
        if fn in self._job_listeners:
            self._job_listeners.remove(fn)

    def on_lanes(self, fn: LaneListener) -> None:
        self._lane_listeners.append(fn)

    def _set_jobs(self, jobs: list[Job], removed: list[str] | None = None, replace: bool = False) -> None:
        with self._lock:
            if replace:
                self._jobs = {}
            for j in jobs:
                self._jobs[j.id] = j
            for jid in removed or ():
                self._jobs.pop(jid, None)
        if jobs:
            for fn in list(self._job_listeners):
                fn(jobs)

    def _set_lanes(self, lanes: list[Lane]) -> None:
        with self._lock:
            changed = [(l.workflow_id, l.state) for l in lanes] != [(l.workflow_id, l.state) for l in self._lanes]
            self._lanes = lanes
        if changed:
            for fn in list(self._lane_listeners):
                fn(lanes)
