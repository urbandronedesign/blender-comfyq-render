# SPDX-License-Identifier: GPL-3.0-or-later
"""What flows through the ComfyQ graph: socket kinds, their colours, what a workflow produces.

No bpy here (unit-tested on its own, like match.py).
"""
from __future__ import annotations

import hashlib
import json

# kind -> (label, RGBA). Colours follow Mixar's Moodboard where it has one (image = cyan, audio = purple).
SOCKETS: dict[str, tuple[str, tuple[float, float, float, float]]] = {
    "image": ("Image", (0.30, 0.80, 0.95, 1.0)),
    "mask": ("Mask", (0.70, 0.70, 0.70, 1.0)),
    "video": ("Video", (0.15, 0.65, 0.55, 1.0)),
    "audio": ("Audio", (0.62, 0.48, 0.95, 1.0)),
    "text": ("Text", (0.95, 0.80, 0.30, 1.0)),
    "model": ("3D Model", (0.95, 0.55, 0.25, 1.0)),
    "splat": ("Splat", (0.95, 0.45, 0.70, 1.0)),
}

# Which kinds a socket of a given kind accepts (a mask input takes any image: the alpha is used).
ACCEPTS: dict[str, set[str]] = {
    "image": {"image"},
    "mask": {"mask", "image"},
    "video": {"video"},
    "audio": {"audio"},
    "text": {"text"},
    "model": {"model"},
    "splat": {"splat"},
}


def input_kind(param_kind: str) -> str | None:
    """Socket kind of a workflow parameter; None = a setting drawn as a chip, not a socket."""
    if param_kind in ("image", "mask", "video", "audio"):
        return param_kind
    if param_kind in ("prompt", "text"):
        return "text"
    return None


def output_kind(category: str, tags: tuple[str, ...] | list[str], workflow_id: str) -> str:
    """What a workflow produces. ComfyQ's meta.json does not declare it yet (phase 2), so it is
    inferred from the category, tags and id."""
    words = " ".join([workflow_id, *tags]).lower()
    if category == "3d":
        return "splat" if ("splat" in words or "gaussian" in words) else "model"
    if category in ("i2v", "video-edit"):
        return "video"
    if category == "audio":
        return "audio"
    if category in ("t2i", "image-edit", "i2i"):
        return "image"
    if any(w in words for w in ("describe", "caption", "prompt_gen")):
        return "text"
    if "video" in words or "interpolation" in words:
        return "video"
    return "image"


def can_link(from_kind: str, to_kind: str) -> bool:
    return from_kind in ACCEPTS.get(to_kind, set())


def spec_signature(specs) -> str:
    """Changes when a workflow's parameters change in a way that changes the node's sockets/chips."""
    data = [(s.key, s.kind, s.label, tuple(s.options)) for s in specs]
    return hashlib.sha1(json.dumps(data).encode()).hexdigest()[:12]
