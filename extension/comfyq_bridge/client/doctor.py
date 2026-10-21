# SPDX-License-Identifier: Apache-2.0
"""Health checks for a classroom setup. Each check returns (level, message)."""
from __future__ import annotations

from urllib.parse import urlparse

import requests

from .api import ComfyQClient
from .http import ComfyQError

OK, WARN, FAIL = "ok", "warn", "fail"


def _reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        requests.get(url, timeout=timeout)
        return True
    except requests.RequestException:
        return False


def run(client: ComfyQClient, reserve_gb: float | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    p = client.profile
    try:
        status = client.access_status()
    except ComfyQError as e:
        return [(FAIL, str(e))]
    out.append((OK, f"ComfyQ reachable at {p.url}"))
    if status.get("locked"):
        out.append((OK, "access password is ON"))
    else:
        out.append((WARN, "access password is OFF: anyone on the network can book jobs"))

    try:
        client.login()
    except ComfyQError as e:
        out.append((FAIL, str(e)))
        return out

    wfs = client.workflows()
    gpu = client.gpu()
    unavailable = [w for w in wfs if w.unavailable]
    uncal = [w for w in wfs if not w.calibrated]
    out.append((OK, f"{len(wfs)} workflows, GPU {gpu.get('name')} ({gpu.get('vramGb')} GB)"))
    if unavailable:
        out.append((WARN, f"{len(unavailable)} unavailable: " + ", ".join(w.id for w in unavailable)))
    if uncal:
        out.append((WARN, f"{len(uncal)} have no VRAM measurement yet (re-calibrate them in the sweep)"))

    try:
        client.connect()
        lanes = client.lanes()
        names = ", ".join(f"{l.workflow_id} [{l.state}]" for l in lanes) or "none"
        out.append((OK if lanes else WARN, f"socket connected; served now: {names}"))
    except ComfyQError as e:
        out.append((FAIL, f"socket: {e}"))

    host = urlparse(p.url).hostname or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost") and _reachable(f"http://{host}:8188/system_stats"):
        msg = "ComfyUI :8188 is reachable from here: students can bypass the queue (set comfy_ui.lan_access=false)"
        out.append((WARN, msg))
    if _reachable(f"http://{host}:5173/"):
        out.append((WARN, "ComfyQ web client is the Vite dev server (:5173); use a production build for class"))

    if reserve_gb is not None and gpu.get("vramGb"):
        budget = float(gpu["vramGb"]) - reserve_gb
        over = [w.id for w in wfs if w.vram_peak_gb and w.vram_peak_gb > budget]
        out.append((OK, f"shared-GPU budget {budget:.0f} GB; {len(over)} calibrated workflows exceed it"))
    return out
