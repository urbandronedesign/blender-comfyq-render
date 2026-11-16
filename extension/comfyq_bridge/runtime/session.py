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
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..client import ComfyQClient, ComfyQError, Job, ParamSpec, Workflow, load_profile
from ..client.models import LOST

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


NOTE_TTL_S = 120.0     # a note is news: it leaves the bar after 2 minutes (audit 2, #25: "Cycles render done: …png"
                       # from an old graph run stayed for ever, even disconnected)


class Session:
    @property
    def status_note(self) -> str:
        """The last user-facing event, while it is recent."""
        return self._note if self._note and time.monotonic() - self._note_t < NOTE_TTL_S else ""

    @status_note.setter
    def status_note(self, value: str) -> None:
        self._note, self._note_t = value or "", time.monotonic()

    def __init__(self) -> None:
        self.client: ComfyQClient | None = None
        self.status = "Not connected"
        self.error = ""
        self.busy = ""                        # current blocking activity, shown in the panel
        self._note, self._note_t = "", 0.0     # last user-facing event ("Booked …"), see status_note
        self._conn_gen = 0                     # bumped by connect / close: a running wait for serving stops
        self.waiting_serving = False
        self.workflows: list[Workflow] = []
        self.specs: dict[str, list[ParamSpec]] = {}
        self._loading: dict[str, list[Callable[[list[ParamSpec]], None]]] = {}
        self.jobs: dict[str, JobView] = {}
        self.gpu: dict[str, Any] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="comfyq")
        self._main: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.on_job_done: Callable[[JobView], None] | None = None
        self.on_connected: Callable[[], None] | None = None
        # What the last Connect ran into, so the bar can offer the way out (audit 2, #1–2):
        self.need_password = False            # ComfyQ is locked and this PC has no access password
        self.no_server_set = False            # no profiles file: it tried this PC itself (127.0.0.1)
        self.access_password = ""             # typed in Access Password...; memory only, never in a file
        self.lost_since = 0.0                 # when the socket was first seen down (0 = connected)

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
        gen = self._conn_gen
        profile = load_profile(profile_name or None)
        if url_override:
            profile = _replace(profile, url=url_override.rstrip("/"))
        if user:
            profile = _replace(profile, user=user)
        if self.access_password:
            profile = _replace(profile, password=self.access_password, token="")
        client = ComfyQClient(profile)
        self.error = ""
        self.need_password = False
        self.no_server_set = False
        self.lost_since = 0.0
        from ..client.profile import DEFAULT_URL, config_path
        default_address = profile.url == DEFAULT_URL and not config_path().is_file()

        why = {"need": False, "unset": False}

        def work():
            try:
                client.connect()
            except ComfyQError as e:
                why.update(need=e.access_required, unset=default_address and e.status is None)
                raise
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
        def failed(msg: str) -> None:
            self._connect_problem(why["need"], why["unset"])      # after the raw error
            if "serves no model" in msg:                         # admin mode: wait for the teacher, then connect
                self._wait_for_serving(profile, gen, (profile_name, user, url_override))
        self.run(work, done, label="Connecting", on_error=failed)

    def _wait_for_serving(self, profile, gen: int, again: tuple) -> None:
        """ComfyQ is up but not serving (admin mode, gotcha 59): ask it every 10 s for up to 30 min and connect as
        soon as the teacher starts serving (audit 2, #42: a dead end before). Connect / Disconnect cancel it."""
        self.waiting_serving = True
        self.error = "ComfyQ serves no model yet: waiting for the teacher to start serving (connects by itself)"

        def work():
            probe = ComfyQClient(profile)
            t0 = time.monotonic()
            while gen == self._conn_gen and time.monotonic() - t0 < 1800:
                time.sleep(10)
                if probe.mode() not in ("admin", ""):
                    return True
            return False

        def done(ok):
            if gen != self._conn_gen:
                return
            self.waiting_serving = False
            if ok and self.client is None:
                self.connect(*again)
            elif not ok:
                self.error = "ComfyQ still serves no model after 30 minutes: press Connect when the teacher is ready"
        self.run(work, done)

    def _connect_problem(self, need_password: bool, no_server_set: bool) -> None:
        self.need_password = need_password
        self.no_server_set = no_server_set and not need_password
        if self.no_server_set:
            self.error = ("No class server is set on this PC, so it tried this PC itself. Ask the teacher for the "
                          "class profiles file, or type the server's address (Server Address...)")
        elif need_password:
            self.error = "This class server is locked: type its access password (Access Password...)"

    def lost_for(self) -> float:
        """Seconds the socket has been down while a client exists (0 when connected or not connecting)."""
        import time
        if self.client is None or self.client.transport.connected:
            self.lost_since = 0.0
            return 0.0
        if not self.lost_since:
            self.lost_since = time.monotonic()
        return time.monotonic() - self.lost_since

    def close(self) -> None:
        self._conn_gen += 1                    # a wait for serving (see _wait_for_serving) stops
        self.waiting_serving = False
        if self.client is not None:
            client, self.client = self.client, None
            self._pool.submit(client.close)
        self.status = "Not connected"
        self.status_note = ""

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
            for job_id, wf_id in todo:
                try:
                    found.append(client.job(job_id))
                except ComfyQError as e:
                    if e.status in (404, 410):
                        # deleted on the server: a "lost" job, finished and removable (audit 2, #9: these
                        # stayed "Waiting in queue" for ever, could not be cancelled nor removed)
                        found.append(Job(id=job_id, workflow_id=wf_id, user_id="", phase=LOST,
                                         error="Not on the server any more"))
            return found                      # other errors (network): keep waiting, a later reattach retries

        def done(jobs):
            for j in jobs:
                self.jobs[j.id].job = j
            self._jobs_changed([j for j in jobs if j.phase != LOST])

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
