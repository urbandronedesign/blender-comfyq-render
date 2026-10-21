# SPDX-License-Identifier: Apache-2.0
"""ComfyQ's `camera` parameter: a camera position around the subject (azimuth, elevation, zoom).

The wire value is a small JSON text, `{"horizontal": 180, "vertical": 0, "zoom": 5}`. The meta's
`camera` block declares each axis's range and the words the model is given for a position
(`phrase`: sectors around the circle, terms by threshold). This mirrors ComfyQ's
server/workers/cameraAngle.js so the bar shows the shot the model will be told.

No bpy here: `from_view` takes plain coordinates.
"""
from __future__ import annotations

import json
import math
from typing import Any

AXES = ("horizontal", "vertical", "zoom")

# ComfyUI's CameraAngle limits, used when a bundle does not narrow them.
DEFAULT_AXES: dict[str, dict[str, Any]] = {
    "horizontal": {"min": 0, "max": 360, "step": 1, "default": 0, "wrap": True},
    "vertical": {"min": -30, "max": 60, "step": 1, "default": 0},
    "zoom": {"min": 0, "max": 10, "step": 0.1, "default": 5},
}


def axis(spec: dict | None, name: str) -> dict[str, Any]:
    return {**DEFAULT_AXES[name], **(((spec or {}).get("axes") or {}).get(name) or {})}


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def parse(value, spec: dict | None) -> dict[str, float]:
    """A camera from the wire (JSON text or dict); missing or broken axes take the bundle's default."""
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    out = {}
    for name in AXES:
        a = axis(spec, name)
        v = _num(raw.get(name)) if isinstance(raw, dict) else None
        if v is None:
            v = float(a["default"])
        if a.get("wrap"):
            span = a["max"] - a["min"]          # azimuth is a circle: 370 is 10, -10 is 350
            v = a["min"] + ((v - a["min"]) % span) if span > 0 else a["min"]
        else:
            v = min(a["max"], max(a["min"], v))
        out[name] = v
    return out


def dumps(cam: dict[str, float]) -> str:
    """The wire value (ComfyQ's formatCamera: whole degrees, zoom to 0.1)."""
    return json.dumps({"horizontal": round(cam["horizontal"]), "vertical": round(cam["vertical"]),
                       "zoom": round(cam["zoom"] * 10) / 10})


def _sector(angle: float, n: int) -> int:
    # Equal sectors centred on their label: "front view" covers either side of 0.
    a = angle % 360
    return math.floor(a / (360 / n) + 0.5) % n


def _threshold(value: float, thresholds) -> int:
    for i, t in enumerate(thresholds or ()):
        if value < t:
            return i
    return len(thresholds or ())


def label_for(part: dict, cam: dict[str, float]) -> str | None:
    v = cam.get(part.get("axis"))
    if v is None:
        return None
    if part.get("sectors"):
        label = part["sectors"][_sector(v, len(part["sectors"]))]
    elif part.get("terms"):
        label = part["terms"][min(len(part["terms"]) - 1, _threshold(v, part.get("thresholds")))]
    else:
        return None
    s = part.get("suffixWhen")
    if s and isinstance(s.get("suffix"), str) and cam.get(s.get("axis"), -math.inf) >= s.get("min", math.inf):
        label += s["suffix"]
    return label


def describe(spec: dict | None, cam: dict[str, float]) -> str:
    """The shot the model is told, e.g. "front-right quarter view eye-level shot medium shot"."""
    parts = (spec or {}).get("phrase") or (spec or {}).get("targets") or []
    return " ".join(lab for lab in (label_for(p, cam) for p in parts) if lab)


def from_view(view: tuple[float, float, float], target: tuple[float, float, float], radius: float,
              front: tuple[float, float, float] | None = None, spec: dict | None = None) -> dict[str, float]:
    """The camera position of a viewpoint (Z up) looking at a subject of bounding radius `radius` at `target`.

    horizontal: the azimuth of the view around the subject, measured from `front` (the camera the input
    picture was taken with; default: the -Y side, Blender's front view), increasing as the view moves to the
    picture's right, like ComfyUI's CameraAngle (0 front, 90 right side, 180 back).
    vertical: the elevation of the view (degrees above the subject's centre).
    zoom: from the distance in subject radii: 13 radii or more = 0 (wide), 3 or less = 10 (close-up).
    """
    dx, dy, dz = (view[i] - target[i] for i in range(3))
    flat = math.hypot(dx, dy)
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    fx, fy = (front[0] - target[0], front[1] - target[1]) if front else (0.0, -1.0)
    if math.hypot(fx, fy) < 1e-9:
        fx, fy = 0.0, -1.0
    h = math.degrees(math.atan2(dy, dx) - math.atan2(fy, fx)) % 360 if flat > 1e-9 else 0.0
    v = math.degrees(math.atan2(dz, flat)) if dist > 1e-9 else 0.0
    zoom = 13.0 - dist / radius if radius > 1e-9 else 5.0
    return parse({"horizontal": h, "vertical": v, "zoom": zoom}, spec)
