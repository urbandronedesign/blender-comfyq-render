# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-wide bridge state and the thread rule.

Network work runs on a small thread pool; its results are queued and applied
by `pump()`, a bpy.app.timers callback on the main thread. Worker code never
touches bpy; `pump()` is the only place results reach bpy data and the UI.
"""
from __future__ import annotations

import os
import queue
import sys
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..client import ComfyQClient, ComfyQError, Job, ParamSpec, Workflow, load_profile

PUMP_INTERVAL_S = 0.2


@dataclass
class JobView:
    """What the panel shows for one job (lives only in this session)."""
    job: Job
    files: list[Path] = field(default_factory=list)
    imported: bool = False
    downloading: bool = False
    # Called on the main thread once the result is downloaded (keyframe chains use this).
    callbacks: list[Callable[[JobView], None]] = field(default_factory=list)


class Session:
    def __init__(self) -> None:
        self.client: ComfyQClient | None = None
        self.status = "Not connected"
        self.error = ""
        self.busy = ""                        # current blocking activity, shown in the panel
        self.status_note = ""                 # last user-facing event ("Booked 1a2b3c4d")
        self.workflows: list[Workflow] = []
        self.specs: dict[str, list[ParamSpec]] = {}
        self._loading: dict[str, list[Callable[[list[ParamSpec]], None]]] = {}
        self.jobs: dict[str, JobView] = {}
        self.gpu: dict[str, Any] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="comfyq")
        self._main: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.on_job_done: Callable[[JobView], None] | None = None
        self.on_connected: Callable[[], None] | None = None

    # -- threading ---------------------------------------------------------------
    def run(self, work: Callable[[], Any], done: Callable[[Any], None] | None = None,
            label: str = "", on_error: Callable[[str], None] | None = None) -> None:
        """Run `work` off the main thread; then `done(result)` on the main thread."""
        if label:
            self.busy = label

        def task() -> None:
            try:
                result = work()
            except ComfyQError as e:
                msg = str(e)          # `e` is unbound once the except block ends
                self.later(lambda: self._fail(msg, label, on_error))
                return
            except Exception as e:  # noqa: BLE001 - shown to the user, full trace in the console
                traceback.print_exc()
                msg = f"{e.__class__.__name__}: {e}"
                self.later(lambda: self._fail(msg, label, on_error))
                return
            def finish() -> None:
                if label and self.busy == label:
                    self.busy = ""
                if done:
                    done(result)
            self.later(finish)

        self._pool.submit(task)

    def later(self, fn: Callable[[], None]) -> None:
        self._main.put(fn)

    def _fail(self, msg: str, label: str, on_error: Callable[[str], None] | None = None) -> None:
        self.error = msg
        if label and self.busy == label:
            self.busy = ""
        if on_error:
            on_error(msg)

    def pump(self) -> bool:
        """Apply queued results; return True when the UI should redraw."""
        changed = False
        while True:
            try:
                fn = self._main.get_nowait()
            except queue.Empty:
                return changed
            try:
                fn()
            except Exception:  # noqa: BLE001 - one bad callback must not kill the pump
                traceback.print_exc()
            changed = True

    # -- connection ----------------------------------------------------------------
    def connect(self, profile_name: str, user: str, url_override: str = "") -> None:
        self.close()
        profile = load_profile(profile_name or None)
        if url_override:
            profile = _replace(profile, url=url_override.rstrip("/"))
        if user:
            profile = _replace(profile, user=user)
        client = ComfyQClient(profile)
        self.error = ""

        def work():
            client.connect()
            return client.workflows(), client.gpu()

        def done(result):
            self.client = client
            self.workflows, self.gpu = result
            self.specs.clear()
            self._loading.clear()
            self.status = f"Connected to {profile.url} as {profile.user or '?'}"
            client.transport.on_jobs(lambda jobs: self.later(lambda: self._jobs_changed(jobs)))
            client.transport.on_lanes(lambda lanes: self.later(lambda: None))  # just redraw
            if self.on_connected:
                self.on_connected()

        self.status = f"Connecting to {profile.url}…"
        self.run(work, done, label="Connecting")

    def close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            self._pool.submit(client.close)
        self.status = "Not connected"

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.transport.connected

    def served_ids(self) -> set[str]:
        return self.client.served_ids() if self.client else set()

    def load_specs(self, workflow_id: str, done: Callable[[list[ParamSpec]], None]) -> None:
        if workflow_id in self.specs:
            done(self.specs[workflow_id])
            return
        waiting = self._loading.get(workflow_id)
        if waiting is not None:              # already on its way: just wait for it too
            waiting.append(done)
            return
        self._loading[workflow_id] = [done]
        client = self.client

        def finish(specs):
            self.specs[workflow_id] = specs
            for cb in self._loading.pop(workflow_id, []):
                cb(specs)

        def failed(msg):
            self._loading.pop(workflow_id, None)

        self.run(lambda: client.params(workflow_id), finish, label="Loading parameters", on_error=failed)

    def loading(self, workflow_id: str) -> bool:
        return workflow_id in self._loading

    # -- jobs ------------------------------------------------------------------------
    def track(self, job_id: str, workflow_id: str, callback: Callable[[JobView], None] | None = None) -> None:
        if job_id not in self.jobs:
            self.jobs[job_id] = JobView(Job(id=job_id, workflow_id=workflow_id, user_id="", phase="scheduled"))
        if callback is not None:
            self.jobs[job_id].callbacks.append(callback)
        # A fast job may already have finished before we started tracking it.
        known = self.client.transport.job(job_id) if self.client else None
        if known is not None:
            self._jobs_changed([known])

    def _jobs_changed(self, jobs: list[Job]) -> None:
        for j in jobs:
            view = self.jobs.get(j.id)
            if view is None:
                continue
            view.job = j
            if j.ok and not view.files and not view.downloading:
                self._download(view)

    def _download(self, view: JobView) -> None:
        view.downloading = True
        client = self.client
        dest = cache_dir() / view.job.id[:8]

        def work():
            # Already downloaded in an earlier session? Reuse the cached files.
            cached = [dest / o.filename for o in view.job.outputs if o.filename]
            if cached and all(p.is_file() for p in cached):
                return cached
            return client.download_outputs(view.job, dest)

        def done(files):
            view.files, view.downloading = files, False
            for cb in list(view.callbacks):
                cb(view)
            view.callbacks.clear()
            if self.on_job_done:
                self.on_job_done(view)

        self.run(work, done, label="Downloading result")

    def reattach(self, records: list[tuple[str, str]]) -> None:
        """Pick up jobs recorded in the .blend: (job_id, workflow_id) pairs."""
        client = self.client
        todo = [(j, w) for j, w in records if j and j not in self.jobs]
        if client is None or not todo:
            return
        for job_id, wf_id in todo:
            self.jobs[job_id] = JobView(Job(id=job_id, workflow_id=wf_id, user_id="", phase="scheduled"))

        def work():
            found = []
            for job_id, _ in todo:
                try:
                    found.append(client.job(job_id))
                except ComfyQError:
                    pass          # deleted on the server: leave it as "not found"
            return found

        def done(jobs):
            for j in jobs:
                self.jobs[j.id].job = j
            self._jobs_changed(jobs)

        self.run(work, done, label="Reconnecting jobs")


def _replace(profile, **kw):
    from dataclasses import replace
    return replace(profile, **kw)


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    d = base / "comfyq-bridge" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


SESSION = Session()
