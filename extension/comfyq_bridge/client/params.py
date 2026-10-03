# SPDX-License-Identifier: Apache-2.0
"""Normalize a ComfyQ bundle's exposed parameters into `ParamSpec`.

One model drives both the Blender form and the MCP tool schema. ComfyQ's
meta.json types are mapped to a small fixed set; an unknown type degrades to
`text` instead of failing, so a newer bundle never breaks an older bridge.
The server clamps values again on its side; we clamp first so the student
sees what will actually be sent.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Media kinds: the value is the filename returned by POST /upload.
MEDIA_KINDS = frozenset({"image", "video", "audio", "mask"})

_TYPE_MAP = {
    "textarea": "prompt",
    "text": "text",
    "number": "number",
    "checkbox": "bool",
    "select": "enum",
    "lora": "enum",
    "image": "image",
    "video": "video",
    "audio": "audio",
    "mask": "mask",   # LoadImage reading the alpha channel: transparent = area to change
}


@dataclass(frozen=True)
class ParamSpec:
    key: str
    kind: str                       # prompt|text|int|float|seed|bool|enum|image|video|audio|mask
    label: str
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[str, ...] = ()
    option_labels: tuple[str, ...] = ()
    max_input_edge: int | None = None
    disabled_when: dict[str, Any] | None = None
    order: int = 0
    wire_type: str = ""             # the original meta.json type
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_media(self) -> bool:
        return self.kind in MEDIA_KINDS


def _number_kind(p: dict[str, Any]) -> str:
    name = f"{p.get('key', '')} {p.get('field', '')}".lower()
    if "seed" in name:
        return "seed"
    step, default = p.get("step"), p.get("default")
    nums = [v for v in (step, default, p.get("min"), p.get("max")) if isinstance(v, (int, float))]
    if nums and all(float(v).is_integer() for v in nums) and (step is None or float(step) >= 1):
        return "int"
    return "float"


def spec_from_wire(p: dict[str, Any], lane_entry: dict[str, Any] | None = None) -> ParamSpec:
    """`p` is one item of GET /workflows/:id/parameters. `lane_entry` is the
    same key from a served lane's `parameter_map`; it carries the LoRA options
    the server scans at broadcast time."""
    wire = p.get("type") or "text"
    kind = _TYPE_MAP.get(wire, "text")
    if kind == "number":
        kind = _number_kind(p)
    options = p.get("options") or (lane_entry or {}).get("options") or ()
    labels = (lane_entry or {}).get("optionLabels") or ()
    if kind == "enum" and not options and p.get("default"):
        options = (p["default"],)
    return ParamSpec(
        key=p["key"],
        kind=kind,
        label=p.get("label") or p["key"],
        default=p.get("default"),
        required=bool(p.get("required")),
        min=p.get("min"),
        max=p.get("max"),
        step=p.get("step"),
        options=tuple(str(o) for o in options),
        option_labels=tuple(str(o) for o in labels),
        max_input_edge=p.get("maxInputEdge"),
        disabled_when=p.get("disabledWhen"),
        order=int(p.get("order") or 0),
        wire_type=wire,
    )


def specs_from_wire(params: list[dict[str, Any]], parameter_map: dict[str, Any] | None = None) -> list[ParamSpec]:
    parameter_map = parameter_map or {}
    specs = [spec_from_wire(p, parameter_map.get(p.get("key"))) for p in params if p.get("enabled", True)]
    return sorted(specs, key=lambda s: s.order)


def is_disabled(spec: ParamSpec, values: dict[str, Any], specs: list[ParamSpec]) -> bool:
    """ComfyQ `disabledWhen: {param, equals}` — hidden while another param has that value."""
    dw = spec.disabled_when
    if not dw or "param" not in dw:
        return False
    other = dw["param"]
    current = values.get(other)
    if current is None:
        current = next((s.default for s in specs if s.key == other), None)
    return current == dw.get("equals")


def clamp(spec: ParamSpec, value: Any) -> Any:
    if spec.kind in ("int", "seed", "float"):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return spec.default
        if math.isnan(v):
            return spec.default
        if spec.min is not None:
            v = max(v, float(spec.min))
        if spec.max is not None:
            v = min(v, float(spec.max))
        if spec.kind == "float" and spec.step:
            base = float(spec.min or 0)
            v = round(base + round((v - base) / float(spec.step)) * float(spec.step), 6)
        return round(v) if spec.kind in ("int", "seed") else v
    if spec.kind == "bool":
        return bool(value)
    if spec.kind == "enum":
        s = str(value)
        return s if not spec.options or s in spec.options else spec.default
    return value


def validate(specs: list[ParamSpec], values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (params to send, errors). Disabled and empty params are omitted
    so the server keeps the bundle's own default."""
    out: dict[str, Any] = {}
    errors: list[str] = []
    for s in specs:
        if is_disabled(s, values, specs):
            continue
        raw = values.get(s.key)
        if raw is None or raw == "":
            if s.required and s.default in (None, ""):
                errors.append(f"“{s.label}” is required")
            continue
        out[s.key] = clamp(s, raw)
    return out, errors


def json_schema(specs: list[ParamSpec]) -> dict[str, Any]:
    """JSON Schema for an MCP tool's `params` argument."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for s in specs:
        if s.kind in ("int", "seed"):
            prop: dict[str, Any] = {"type": "integer"}
        elif s.kind == "float":
            prop = {"type": "number"}
        elif s.kind == "bool":
            prop = {"type": "boolean"}
        else:
            prop = {"type": "string"}
        prop["description"] = s.label + (" (uploaded filename from comfyq_upload)" if s.is_media else "")
        if s.min is not None and prop["type"] in ("integer", "number"):
            prop["minimum"] = s.min
        if s.max is not None and prop["type"] in ("integer", "number"):
            prop["maximum"] = s.max
        if s.kind == "enum" and s.options:
            prop["enum"] = list(s.options)
        if s.default not in (None, ""):
            prop["default"] = s.default
        props[s.key] = prop
        if s.required and s.default in (None, ""):
            required.append(s.key)
    return {"type": "object", "properties": props, "required": required}
