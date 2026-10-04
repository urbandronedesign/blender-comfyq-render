# SPDX-License-Identifier: Apache-2.0
"""Phase-1 transport: ComfyQ's existing Socket.IO API, unchanged.

Wire contract (ComfyQ server/realtime/realtimeBus.js):
  out: register_user(name), request_state(), book_job({...}, ack), cancel_job(jobId)
  in:  state_update (full snapshot, has `seq`), state_patch ({seq, jobs?,
       removed_jobs?, lanes?, ...}), access_revoked, error
A patch whose seq is not previous+1 means we missed one: ask for a snapshot.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import socketio

from ..http import ComfyQError
from ..models import Job, Lane
from .base import ComfyQTransport

log = logging.getLogger(__name__)


class SocketIOTransport(ComfyQTransport):
    def __init__(self, base_url: str, user_id: str, token: str = "", ack_timeout_s: float = 20.0):
        super().__init__()
        self.base_url = base_url
        self.user_id = user_id
        self.token = token
        self.ack_timeout = ack_timeout_s
        self._seq: int | None = None
        self._synced = threading.Event()
        self.last_error: str = ""
        self.sio = socketio.Client(reconnection=True, reconnection_delay=1, reconnection_delay_max=30)
        self._wire()

    # -- lifecycle -------------------------------------------------------------
    def connect(self, wait_state_s: float = 10.0) -> None:
        try:
            self.sio.connect(
                self.base_url,
                auth={"accessToken": self.token} if self.token else None,
                transports=["websocket", "polling"],
                wait_timeout=10,
            )
        except socketio.exceptions.ConnectionError as e:
            raise ComfyQError(f"Socket connection to {self.base_url} failed: {e}",
                              access_required="access" in str(e).lower()) from e
        if not self._synced.wait(wait_state_s):
            raise ComfyQError("Connected, but ComfyQ sent no state")

    def close(self) -> None:
        if self.sio.connected:
            self.sio.disconnect()

    @property
    def connected(self) -> bool:
        return bool(self.sio.connected)

    # -- actions ---------------------------------------------------------------
    def book(self, workflow_id: str, params: dict[str, Any], user_id: str,
             prompt: str = "", scheduled_time: int | None = None) -> str:
        payload = {
            "workflow_id": workflow_id,
            "params": params,
            "user_id": user_id or self.user_id,
            "prompt": prompt,
            # Empty/past → ComfyQ puts the job in the next free slot.
            "scheduledTime": scheduled_time,
        }
        try:
            ack = self.sio.call("book_job", payload, timeout=self.ack_timeout)
        except socketio.exceptions.TimeoutError as e:
            raise ComfyQError("ComfyQ did not answer the booking") from e
        if not ack or not ack.get("ok"):
            raise ComfyQError((ack or {}).get("error") or "Booking refused")
        return ack["jobId"]

    def cancel(self, job_id: str) -> None:
        # Authorized as our own job because register_user ran on connect.
        self.sio.emit("cancel_job", job_id)

    def delete(self, job_id: str) -> None:
        """Remove our own job (and its outputs) from ComfyQ."""
        self.sio.emit("delete_job", job_id)

    def refresh(self) -> None:
        self.sio.emit("request_state")

    # -- inbound ---------------------------------------------------------------
    def _wire(self) -> None:
        sio = self.sio

        @sio.event
        def connect():
            if self.user_id:
                sio.emit("register_user", self.user_id)

        @sio.event
        def connect_error(data):
            self.last_error = str(data)

        @sio.on("state_update")
        def on_full(state):
            self._seq = state.get("seq")
            self._apply(state, replace=True)
            self._synced.set()

        @sio.on("state_patch")
        def on_patch(patch):
            seq = patch.get("seq")
            if self._seq is not None and seq is not None and seq != self._seq + 1:
                log.info("state_patch gap (%s → %s), resyncing", self._seq, seq)
                self._seq = seq
                self.refresh()
            self._seq = seq
            self._apply(patch, replace=False)

        @sio.on("access_revoked")
        def on_revoked(data):
            self.last_error = (data or {}).get("message") or "access revoked"

        @sio.on("error")
        def on_error(data):
            self.last_error = (data or {}).get("message") or str(data)
            log.warning("ComfyQ: %s", self.last_error)

    def _apply(self, msg: dict[str, Any], replace: bool) -> None:
        if "lanes" in msg:
            self._set_lanes([Lane.from_wire(l) for l in msg.get("lanes") or []])
        if "connected_users" in msg:
            names = [str(u.get("userId") or "") for u in msg.get("connected_users") or [] if isinstance(u, dict)]
            with self._lock:
                self._users = list(dict.fromkeys(n for n in names if n))
        jobs = [Job.from_wire(j) for j in msg.get("jobs") or []]
        if jobs or replace or msg.get("removed_jobs"):
            self._set_jobs(jobs, msg.get("removed_jobs"), replace=replace)
