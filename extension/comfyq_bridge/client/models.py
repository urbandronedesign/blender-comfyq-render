# SPDX-License-Identifier: Apache-2.0
"""Plain data models for ComfyQ objects, normalized from its wire format.

ComfyQ sends the same job in two shapes: HTTP `GET /jobs/:id` uses
`progress: {stepsDone, stepsTotal}` while the Socket.IO `state_*` messages use
`progress: {value, max}`. Everything above this module only sees `Job`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Fine-grained job phases (ComfyQ server/queue/jobStateMachine.js).
SCHEDULED = "scheduled"
UPLOADING_INPUTS = "uploading-inputs"
SUBMITTED = "submitted"
EXECUTING = "executing"
COLLECTING_OUTPUTS = "collecting-outputs"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})


@dataclass(frozen=True)
class Output:
    kind: str            # image | video | audio | model3d | json | binary | text
    filename: str
    subfolder: str = ""
    mime: str = ""
    size_bytes: int | None = None
    node_id: str | None = None

    @property
    def media_path(self) -> str:
        """Path under `/media/_any/` that serves this file."""
        return f"{self.subfolder}/{self.filename}" if self.subfolder else self.filename

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> Output:
        return cls(
            kind=d.get("kind") or "binary",
            filename=d.get("filename") or "",
            subfolder=d.get("subfolder") or "",
            mime=d.get("mime") or "",
            size_bytes=d.get("sizeBytes"),
            node_id=d.get("nodeId"),
        )


@dataclass(frozen=True)
class Job:
    id: str
    workflow_id: str
    user_id: str
    phase: str
    progress: float | None = None        # 0..1, None when unknown
    current_node: str | None = None
    outputs: tuple[Output, ...] = ()
    error: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    time_slot: int | None = None
    started_at: int | None = None
    finished_at: int | None = None

    @property
    def done(self) -> bool:
        return self.phase in TERMINAL

    @property
    def ok(self) -> bool:
        return self.phase == COMPLETED

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> Job:
        # `phase` is the fine-grained state; `status` is the coarse one.
        phase = d.get("phase") or d.get("status") or SCHEDULED
        if phase == FAILED and d.get("error_reason") == "cancelled":
            phase = CANCELLED          # ComfyQ marks a job cancelled while running as failed/"cancelled"
        return cls(
            id=d["id"],
            workflow_id=d.get("workflow_id") or "",
            user_id=d.get("user_id") or "",
            phase=phase,
            progress=_progress(d.get("progress")),
            current_node=d.get("current_node"),
            outputs=tuple(Output.from_wire(o) for o in (d.get("outputs") or [])),
            error=d.get("error_reason"),
            params=d.get("params") or {},
            time_slot=d.get("time_slot"),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
        )


def _progress(p: dict[str, Any] | None) -> float | None:
    if not p:
        return None
    done = p.get("value", p.get("stepsDone"))
    total = p.get("max", p.get("stepsTotal"))
    if done is None or not total:
        return None
    return max(0.0, min(1.0, float(done) / float(total)))


@dataclass(frozen=True)
class Workflow:
    id: str
    name: str
    category: str
    description: str = ""
    estimated_sec: float | None = None
    vram_peak_gb: float | None = None    # from calibration; None = never measured
    calibrated: bool = False             # True once the VRAM peak has been measured
    unavailable: bool = False
    unavailable_reason: str = ""
    experimental: bool = False
    tags: tuple[str, ...] = ()

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> Workflow:
        cal = d.get("calibration") or {}
        return cls(
            id=d["id"],
            name=d.get("name") or d["id"],
            category=d.get("category") or "other",
            description=d.get("description") or "",
            estimated_sec=d.get("estimatedDurationSec"),
            vram_peak_gb=cal.get("vramPeakGb"),
            # Older calibrations measured time only; the VRAM badge needs the peak.
            calibrated=cal.get("vramPeakGb") is not None,
            unavailable=bool(d.get("unavailable")),
            unavailable_reason=d.get("reason") or "",
            experimental=bool(d.get("experimental")),
            tags=tuple(d.get("tags") or ()),
        )


@dataclass(frozen=True)
class Lane:
    """A workflow this ComfyQ machine is serving right now."""
    workflow_id: str
    name: str
    state: str
    busy: bool
    primary: bool
    parameter_map: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> Lane:
        return cls(
            workflow_id=d["workflow_id"],
            name=d.get("name") or d["workflow_id"],
            state=d.get("state") or "",
            busy=bool(d.get("busy")),
            primary=bool(d.get("primary")),
            parameter_map=d.get("parameter_map") or {},
        )
