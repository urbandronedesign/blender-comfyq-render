# SPDX-License-Identifier: Apache-2.0
"""ComfyQClient — the one entry point the extension, the CLI and the MCP tools use."""
from __future__ import annotations

import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .http import ComfyQError, Http
from .models import Job, Lane, Workflow
from .params import ParamSpec, specs_from_wire, validate
from .profile import Profile
from .transport.base import ComfyQTransport

# POST /upload refuses still images outside a 1920×1080 box (either orientation).
UPLOAD_MAX_LONG, UPLOAD_MAX_SHORT = 1920, 1080


class ComfyQClient:
    def __init__(self, profile: Profile, transport: ComfyQTransport | None = None):
        self.profile = profile
        self.http = Http(profile.url, profile.token, profile.timeout_s, profile.admin_password)
        self._transport = transport

    # -- connection --------------------------------------------------------------
    def access_status(self) -> dict[str, Any]:
        return self.http.get("/access/status") or {}

    def login(self) -> None:
        """Exchange the access password for a token when the machine is locked."""
        status = self.access_status()
        if not status.get("locked") or self.http.token:
            return
        if not self.profile.password:
            raise ComfyQError("This ComfyQ requires an access password", access_required=True)
        res = self.http.post_json("/access/login", {"password": self.profile.password})
        self.http.token = res.get("token") or ""

    @property
    def transport(self) -> ComfyQTransport:
        if self._transport is None:
            from .transport.socketio_transport import SocketIOTransport
            self._transport = SocketIOTransport(self.profile.url, self.profile.user, self.http.token)
        return self._transport

    def connect(self) -> None:
        self.login()
        if not self.transport.connected:
            self.transport.connect()

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()

    # -- discovery -----------------------------------------------------------------
    def workflows(self, include_unavailable: bool = True) -> list[Workflow]:
        data = self.http.get("/workflows", **({"includeUnavailable": "1"} if include_unavailable else {}))
        return [Workflow.from_wire(w) for w in data.get("workflows") or []]

    def gpu(self) -> dict[str, Any]:
        return (self.http.get("/workflows") or {}).get("gpu") or {}

    def lanes(self) -> list[Lane]:
        return self.transport.lanes()

    def active_jobs(self) -> list[Job]:
        return self.transport.active_jobs()

    def users(self) -> list[str]:
        return self.transport.users()

    def served_ids(self) -> set[str]:
        return {l.workflow_id for l in self.lanes()}

    # -- teacher: what the GPU serves (ComfyQ admin routes; the admin password when one is set) --------
    def server_lanes(self) -> dict[str, Any]:
        """GET /admin/lanes: {lanes, card: {vramGb, usedGb}, fit: {workflow id: {ok, reason, needGb…}}}."""
        return self.http.get("/admin/lanes") or {}

    def open_lane(self, workflow_id: str, force: bool = False) -> dict[str, Any]:
        """Serve alongside the main workflow (no restart). Refused (409, with `fit`) when it does not fit."""
        return self.http.post_json("/admin/lanes", {"workflowId": workflow_id, "force": force},
                                   admin=True, timeout=1800) or {}

    def close_lane(self, workflow_id: str) -> None:
        self.http.delete(f"/admin/lanes/{workflow_id}", admin=True, timeout=300)

    def activate(self, workflow_id: str) -> dict[str, Any]:
        """Make it the main served workflow. ComfyQ restarts (~1–2 min); the socket reconnects by itself."""
        return self.http.post_json("/admin/activate-workflow", {"workflowId": workflow_id}, admin=True) or {}

    def calibrate(self, workflow_id: str) -> dict[str, Any]:
        """Measure VRAM peak / duration on ComfyQ's benchmark inputs (minutes; the GPU is busy)."""
        return self.http.post_json(f"/workflows/{workflow_id}/calibrate", {}, admin=True, timeout=3600) or {}

    def params(self, workflow_id: str) -> list[ParamSpec]:
        data = self.http.get(f"/workflows/{workflow_id}/parameters")
        lane = next((l for l in self.lanes() if l.workflow_id == workflow_id), None) if self._transport else None
        return specs_from_wire(data.get("parameters") or [], lane.parameter_map if lane else None)

    # -- jobs ------------------------------------------------------------------------
    def upload(self, path: str | Path) -> str:
        """Upload an input file; returns the name to put in a media param."""
        p = Path(path)
        size = png_size(p)
        if size and (max(size) > UPLOAD_MAX_LONG or min(size) > UPLOAD_MAX_SHORT):
            raise ComfyQError(f"{p.name} is {size[0]}×{size[1]}; ComfyQ accepts at most "
                              f"{UPLOAD_MAX_LONG}×{UPLOAD_MAX_SHORT}. Resize it first.")
        return self.http.upload("/upload", p)["filename"]

    def submit(self, workflow_id: str, values: dict[str, Any], prompt: str = "",
               specs: list[ParamSpec] | None = None, check_served: bool = True,
               scheduled_time: int | None = None) -> str:
        if check_served:
            served = self.served_ids()
            if served and workflow_id not in served:
                raise ComfyQError(f"{workflow_id} is not being served right now "
                                  f"(served: {', '.join(sorted(served))}). Ask the teacher to serve it.")
        specs = specs if specs is not None else self.params(workflow_id)
        params, errors = validate(specs, values)
        if errors:
            raise ComfyQError("; ".join(errors))
        if not prompt:
            # The text already travels in its parameter; also send it as the job's prompt so
            # ComfyQ's own pages (job cards, history) show it. ComfyQ keeps an already-set param.
            prompt = next((str(params[s.key]) for s in specs if s.kind == "prompt" and s.key in params), "")
        if prompt:
            # ComfyQ's book_job copies the job's prompt into the first unset textarea parameter whose field
            # is "text" — for workflows whose real prompt is a Primitive ("value"), that is the NEGATIVE
            # prompt (seen: Flux2 Klein t2i ran with negative = prompt). Send every prompt parameter
            # explicitly (its default when unset) so nothing is left for ComfyQ to fill.
            for s in specs:
                if s.kind == "prompt" and s.key not in params:
                    params[s.key] = "" if s.default is None else s.default
        return self.transport.book(workflow_id, params, self.profile.user, prompt, scheduled_time)

    def cancel(self, job_id: str) -> None:
        self.transport.cancel(job_id)

    def job(self, job_id: str) -> Job:
        cached = self._transport.job(job_id) if self._transport else None
        if cached is not None:
            return cached
        return Job.from_wire(self.http.get(f"/jobs/{job_id}")["job"])

    def wait(self, job_id: str, timeout_s: float = 1800, poll_s: float = 2.0,
             on_update: Callable[[Job], None] | None = None) -> Job:
        """Block until the job ends (live socket updates, HTTP poll as backstop)."""
        changed = threading.Event()

        def listener(jobs: list[Job]) -> None:
            if any(j.id == job_id for j in jobs):
                changed.set()

        if self._transport is not None:
            self._transport.on_jobs(listener)
        try:
            deadline = time.monotonic() + timeout_s
            last: Job | None = None
            while True:
                try:
                    j = self.job(job_id)
                except ComfyQError:
                    j = last
                if j is not None and j != last:
                    last = j
                    if on_update:
                        on_update(j)
                if j is not None and j.done:
                    return j
                if time.monotonic() > deadline:
                    raise ComfyQError(f"Job {job_id[:8]} still running after {timeout_s:.0f}s")
                changed.wait(poll_s)
                changed.clear()
        finally:
            if self._transport is not None:
                self._transport.off_jobs(listener)

    def download_outputs(self, job: Job, dest_dir: str | Path) -> list[Path]:
        dest = Path(dest_dir)
        return [self.http.download(o.media_path, dest / o.filename) for o in job.outputs if o.filename]


def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from a PNG header without decoding; None for other formats."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h
