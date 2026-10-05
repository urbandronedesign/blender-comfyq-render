# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU check of the Text → 3D class preset without Blender: the plugin's own ComfyQ client serves each model,
books the job and downloads the result, exactly the chain of the preset (its prompt → Flux2 Klein t2i → Pixal3D /
TRELLIS.2 built-in nodes, default settings). Needs the teacher profile (admin password) to switch models;
each main-workflow switch restarts ComfyQ (1–2 min). Refuses to switch while anyone has a job.

    .\\tools\\text3d_check.ps1 <out folder>        (runs this with Blender's Python + the plugin's wheels)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from workflow_check import connect, log, run, serve

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "live_check" / "text3d")
T2I, TO_3D = "image_flux2_klein_9b_t2i", "3d_pixal3d_trellis2_image_to_model"
PRESET = next((ROOT / "presets" / "class").glob("Text - 3D*.json"))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    preset = json.loads(PRESET.read_text(encoding="utf-8"))
    prompt = next(n["text"] for n in preset["nodes"] if n["type"] == "ComfyQPromptNode")
    image_key = next(lk for lk in preset["links"] if lk["to"] != "Viewer" and lk["from"] == "Workflow")["to_socket"]
    t2i_prompt = next(lk["to_socket"] for lk in preset["links"] if lk["from"] == "Prompt")
    log(f"preset {preset['label']!r}: prompt → {t2i_prompt}, picture → {TO_3D}.{image_key}")

    c = serve(connect("text3d-check"), T2I)
    pic = next(f for f in run(c, T2I, {t2i_prompt: prompt}, OUT / T2I, 600)
               if f.suffix.lower() in (".png", ".jpg", ".webp"))
    c = serve(c, TO_3D)
    files = run(c, TO_3D, {image_key: c.upload(pic)}, OUT / TO_3D, 2400)
    result = {"picture": str(pic), "files": [str(f) for f in files], "served_at_end": sorted(c.served_ids())}
    (OUT / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    log(f"DONE {result}")
    c.close()


if __name__ == "__main__":
    main()
