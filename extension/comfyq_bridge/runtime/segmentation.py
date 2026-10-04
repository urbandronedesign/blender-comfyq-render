# SPDX-License-Identifier: GPL-3.0-or-later
"""Segment a picture by name with ComfyQ's SAM3 workflow: one job per label on the same uploaded clip,
all booked at once (ComfyQ queues them), then each mask read back and measured.

Runs on a worker thread; `hop(fn)` runs the bpy parts (video conversions, masks) on Blender's main
thread — `mcp.tools.on_main` in the add-on, a plain call in headless scripts.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..client.api import ComfyQClient
from ..client.http import ComfyQError
from ..surfaces import Region

SAM3_ID = "utility_video_segment_sam3"
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".gif")


def sam3_keys(specs) -> tuple[str, str, str | None]:
    """(video key, text key, threshold key) of the SAM3 workflow, found by kind/label, not hard-coded."""
    video = next((s.key for s in specs if s.kind == "video"), None)
    text = next((s.key for s in specs if s.kind in ("text", "prompt")), None)
    thr = next((s.key for s in specs if "threshold" in (s.label or s.key).lower()), None)
    if not video or not text:
        raise ComfyQError(f"{SAM3_ID} has no video/text input any more: its parameters changed")
    return video, text, thr


def safe(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "label"


def segment(client: ComfyQClient, image: Path, labels: list[str], out_dir: Path, hop: Callable[[Callable], Any],
            threshold: float | None = None, progress: Callable[[str], None] | None = None,
            timeout_s: float = 900) -> dict[str, Any]:
    """`labels` like "floor" or "wall|left wall|right wall": ComfyQ's SAM3 returns ONE surface per phrase
    (the best match), so a label made of several phrases gets one job per phrase and the union mask."""
    from ..passes import segment as seg
    say = progress or (lambda _m: None)
    specs = client.params(SAM3_ID)
    vkey, tkey, thkey = sam3_keys(specs)
    groups = {name: phrases for name, phrases in (split_label(lab) for lab in labels)}
    say("making the clip for SAM3")
    video = hop(lambda: seg.still_to_video(image, out_dir / "input.mp4"))
    name = client.upload(video)
    jobs: dict[str, str] = {}
    for phrase in dict.fromkeys(p for ps in groups.values() for p in ps):
        values: dict[str, Any] = {vkey: name, tkey: phrase}
        if thkey and threshold is not None:
            values[thkey] = threshold
        jobs[phrase] = client.submit(SAM3_ID, values, prompt=f"SAM3 · {phrase}", specs=specs)
    say(f"{len(jobs)} SAM3 jobs booked")
    clips: dict[str, Path] = {}
    errors: dict[str, str] = {}
    for n, (phrase, jid) in enumerate(jobs.items(), 1):
        j = client.wait(jid, timeout_s=timeout_s)
        if not j.ok:
            errors[phrase] = j.error or j.phase
            continue
        files = client.download_outputs(j, out_dir / safe(phrase))
        vid = next((f for f in files if f.suffix.lower() in VIDEO_EXT), None)
        if vid is None:
            errors[phrase] = f"no mask video in {[f.name for f in files]}"
            continue
        clips[phrase] = vid
        say(f"{n}/{len(jobs)} {phrase}")

    def finish():
        frames = {ph: seg.video_frame(v, out_dir / f"phrase_{safe(ph)}.png") for ph, v in clips.items()}
        regions = []
        for label, phrases in groups.items():
            got = [frames[ph] for ph in phrases if ph in frames]
            if got:
                regions.append(seg.region(label, seg.union_masks(got, out_dir / f"mask_{safe(label)}.png")))
        ov = seg.overlay(image, regions, out_dir / "overlay.png") if regions else None
        return regions, ov
    regions, ov = hop(finish)
    return {"image": str(image), "labels": list(groups), "phrases": groups, "jobs": jobs, "errors": errors,
            "regions": regions, "overlay": str(ov) if ov else ""}


def split_label(label: str) -> tuple[str, list[str]]:
    """'wall|left wall|right wall' -> ('wall', ['wall', 'left wall', 'right wall'])."""
    phrases = [p.strip().lower() for p in label.split("|") if p.strip()]
    return (phrases[0] if phrases else label.strip().lower()), phrases or [label.strip().lower()]


def region_dicts(regions: list[Region]) -> list[dict[str, Any]]:
    return [{"label": r.label, "found": r.found, "coverage_pct": round(100 * r.coverage, 2), "where": r.where,
             "bbox": [round(v, 3) for v in r.bbox], "mask": r.mask, "colour": r.extra.get("colour", "")}
            for r in sorted(regions, key=lambda r: -r.coverage)]
