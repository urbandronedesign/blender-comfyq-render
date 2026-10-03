# SPDX-License-Identifier: GPL-3.0-or-later
"""Match a workflow's timing/size parameters to the Mixar scene (no bpy here).

ComfyQ bundles name their parameters for people, not machines, so we
recognise them by label: "Duration (seconds)", "Clip length used (seconds,
max 10 …)", "Length (frames — 81 ≈ 3.4 s …)", "Frame rate (fps)", "Width" /
"Height", "Aspect ratio". Anything unrecognised is left alone.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:   # type hints only: keeps this module importable on its own (unit tests)
    from .client.params import ParamSpec


@dataclass(frozen=True)
class SceneFacts:
    frame_start: int
    frame_end: int
    frame_current: int
    fps: float
    aspect: float          # width / height of the camera frame

    @property
    def frames(self) -> int:
        return max(1, self.frame_end - self.frame_start + 1)

    @property
    def seconds(self) -> float:
        return self.frames / self.fps if self.fps else 0.0


@dataclass
class Match:
    values: dict[str, Any] = field(default_factory=dict)      # key -> suggested value
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_SECONDS = re.compile(r"^(duration|clip length|source clip length|length used)\b", re.IGNORECASE)
_FRAMES = re.compile(r"^(length|max frames)\b.*frames", re.IGNORECASE)
_FPS = re.compile(r"frame rate|\bfps\)", re.IGNORECASE)
_ASPECT = re.compile(r"aspect ratio|shape of the video", re.IGNORECASE)
_RATIO = re.compile(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)")


def _clamp(spec: ParamSpec, v: float) -> float:
    if spec.min is not None:
        v = max(v, float(spec.min))
    if spec.max is not None:
        v = min(v, float(spec.max))
    return v


def _num(spec: ParamSpec, v: float) -> int | float:
    v = _clamp(spec, v)
    return round(v) if spec.kind in ("int", "seed") else round(v, 3)


def match(specs: list[ParamSpec], scene: SceneFacts) -> Match:
    m = Match()
    by_label = {s.key: s.label.strip() for s in specs}
    widths = [s for s in specs if s.kind in ("int", "float") and by_label[s.key].lower().startswith("width")]
    heights = [s for s in specs if s.kind in ("int", "float") and by_label[s.key].lower().startswith("height")]

    for s in specs:
        label = by_label[s.key]
        if s.kind in ("int", "float") and _SECONDS.search(label):
            want = scene.seconds
            m.values[s.key] = _num(s, math.ceil(want) if s.kind == "int" else want)
            if s.max is not None and want > float(s.max) + 1e-6:
                m.warnings.append(f"The scene lasts {want:.1f} s; “{_short(label)}” only uses the first "
                                  f"{float(s.max):g} s")
        elif s.kind in ("int", "float") and _FRAMES.search(label):
            n = scene.frames
            if s.default is not None and int(s.default) % 4 == 1:   # Wan-style models want 4n+1 frames
                n = 4 * ((n - 1) // 4) + 1
            m.values[s.key] = _num(s, n)
            if s.max is not None and scene.frames > float(s.max):
                m.warnings.append(f"The scene has {scene.frames} frames; “{_short(label)}” stops at {s.max:g}")
        elif s.kind in ("int", "float") and _FPS.search(label):
            m.values[s.key] = _num(s, scene.fps)
            if s.max is not None and scene.fps > float(s.max):
                m.warnings.append(f"Scene runs at {scene.fps:g} fps; the workflow maxes out at {s.max:g}")
        elif s.kind == "enum" and _ASPECT.search(label) and s.options:
            best = min(s.options, key=lambda o: _ratio_error(o, scene.aspect))
            if _ratio_error(best, scene.aspect) < math.inf:
                m.values[s.key] = best

    if len(widths) == 1 and len(heights) == 1:
        w, h = widths[0], heights[0]
        long_side = max(float(w.default or 0), float(h.default or 0)) or 1024
        step = 64 if ("64" in w.label + h.label or (_div(w.default, 64) and _div(h.default, 64))) else 16
        if scene.aspect >= 1:
            W, H = long_side, long_side / scene.aspect
        else:
            W, H = long_side * scene.aspect, long_side
        m.values[w.key] = _num(w, max(step, round(W / step) * step))
        m.values[h.key] = _num(h, max(step, round(H / step) * step))
        got = m.values[w.key] / m.values[h.key]
        if abs(math.log(got / scene.aspect)) > 0.05:
            m.warnings.append(f"The workflow's size limits give {m.values[w.key]}×{m.values[h.key]}, not the "
                              f"camera's shape: the image will be cropped")

    for s in specs:
        if s.key in m.values and m.values[s.key] != s.default:
            m.notes.append(f"{_short(by_label[s.key])} → {m.values[s.key]}")
    return m


def frame_for(spec: ParamSpec, scene: SceneFacts) -> int:
    """Which frame a still-image input should be rendered at."""
    label = spec.label.lower()
    if any(t in label for t in ("last frame", "(end)", "ends here")):
        return scene.frame_end
    if any(t in label for t in ("first frame", "start image", "(start)", "starts here", "keyframe", "key frame 1")):
        return scene.frame_start
    return scene.frame_current


def _ratio_error(option: str, aspect: float) -> float:
    m = _RATIO.search(option)
    if not m:
        return math.inf
    a, b = float(m.group(1)), float(m.group(2))
    return abs(math.log((a / b) / aspect)) if a and b and aspect else math.inf


def _div(v: Any, n: int) -> bool:
    try:
        return int(v) % n == 0
    except (TypeError, ValueError):
        return False


def _short(label: str) -> str:
    return short_label(label)


def short_label(label: str, limit: int = 26) -> str:
    """Chip text: 'Clip length used (seconds, max 10 — …)' → 'Clip length used'."""
    s = re.split(r"\s+[—–-]\s+|\s*\(", label, maxsplit=1)[0].strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"
