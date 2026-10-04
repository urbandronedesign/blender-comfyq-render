# SPDX-License-Identifier: GPL-3.0-or-later
"""Run every preset of the library on the scene open in Blender, through the MCP server (an agent's path).

Needs: Blender open with the extension, Preferences › AI agents (MCP server) on, connected to ComfyQ,
and **Solo: load models on demand** on (each preset's model is loaded by its run; ComfyQ restarts for
each switch, so only when nobody else uses the server). GPU for hours with the whole library.

For each preset (presets/library order: Image, Image edit, Image → video, Video → video):
  blender_graph_add_preset → fill its file nodes with test assets → shorten videos (2 s / 49 frames)
  → blender_graph_run(node) → poll blender_graph_status until done → record.
Test assets are made first by presets too (Flux2 Klein t2i: a character, a portrait, a reference sheet;
LTX 2.5 t2v: a talking face) + Windows text-to-speech (speech.wav, made beforehand).

    python tools/preset_run.py [--only=substring,…] [--fresh]
Results: live_check/presets/results.json (resumable: presets already ok are skipped).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "presets" / "library"
OUT = ROOT / "live_check" / "presets"
ASSETS = OUT / "assets"
RESULTS = OUT / "results.json"
GROUPS = ["Image", "Image edit", "Image → video", "Video → video"]
RUN_TIMEOUT_S = 45 * 60                      # model switch (ComfyQ restart) + the job
SHORT_S, SHORT_FRAMES, DRIVING_FRAMES = 2, 49, 48

ASSET_RUNS = [   # (asset file, preset label, prompt)
    ("character.png", "Flux2 Klein 9B — text to image",
     ("Full-body 3D clay figurine of a friendly cartoon explorer character standing in a neutral pose, front "
     "view, whole body visible from head to feet, plain white background, soft studio light")),
    ("portrait.png", "Flux2 Klein 9B — text to image",
     ("Head-and-shoulders photo portrait of a smiling young adult looking straight at the camera, face fully "
     "visible, neutral grey background, soft light, sharp focus")),
    ("sheet.png", "Flux2 Klein 9B — text to image",
     ("Product photo of three objects standing side by side on a pure black background, evenly spaced: a "
     "small orange clay robot on the left, a red umbrella in the middle, a small wooden cabin on the right. "
     "No text, no frames, no borders")),
    ("face.mp4", "LTX 2.5 — text to video + audio",
     ("Close-up of a person facing the camera, talking calmly and nodding gently, plain grey background, "
     "steady camera, soft light. They say: hello and welcome.")),
]
FALLBACK_PROMPT = ("A colorful hand-painted watercolor animation of the same scene; the camera and the objects "
                   "stay where they are, soft light, gentle paper texture")
KEYFRAME_FROM = ("Flux2 Klein 9B — image edit",
                 ("Repaint this image as a colorful hand-painted watercolor illustration; keep the composition, "
                 "the objects and the camera angle exactly"))


# -- MCP ----------------------------------------------------------------------------------------------------
def _info() -> dict:
    base = Path(os.environ.get("APPDATA") or Path.home()) / "comfyq-bridge" / "mcp.json"
    return json.loads(base.read_text(encoding="utf-8"))


def tool(name: str, **args):
    """Call an MCP tool; (ok, result)."""
    for attempt in range(5):
        info = _info()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": name, "arguments": args}}).encode()
        req = urllib.request.Request(info["url"], body, {"Content-Type": "application/json",
                                                          "Authorization": "Bearer " + info["token"]})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                res = json.load(r)["result"]
            text = res["content"][0]["text"]
            try:
                data = json.loads(text)
            except ValueError:
                data = text
            return not res["isError"], data
        except OSError as e:
            log(f"   MCP {name}: {e} (retry {attempt + 1})")
            time.sleep(10)
    return False, {"error": f"MCP {name} unreachable"}


def log(*a) -> None:
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# -- one preset -----------------------------------------------------------------------------------------------
def presets() -> list[tuple[str, str, Path]]:
    out = []
    for g in GROUPS:
        for f in sorted((LIB / g).glob("*.json")):
            out.append((g, json.loads(f.read_text(encoding="utf-8"))["label"], f))
    return out


def asset_for(label: str) -> Path | None:
    lab = label.lower()
    pick = ("portrait.png" if "portrait" in lab else "sheet.png" if "sheet" in lab else
            "style.png" if "style" in lab else "keyframe.png" if "keyframe" in lab or "key frame" in lab else
            "character.png")
    p = ASSETS / pick
    return p if p.is_file() else None


def wait_connected(timeout: float = 600) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        ok, info = tool("blender_scene_info")
        if ok and info.get("comfyq", {}).get("connected"):
            return True
        time.sleep(10)
    return False


def shorten(node: str, wf_id: str) -> dict:
    ok, desc = tool("comfyq_describe_workflow", workflow_id=wf_id)
    if not ok:
        return {}
    done = {}
    for p in desc.get("parameters", []):
        lab = (p.get("label") or "").lower()
        if p.get("kind") not in ("int", "float") or lab.startswith(("frame rate", "fps")):
            continue
        lo, hi = p.get("min"), p.get("max")
        if "driving video" in lab and "frames" in lab:
            v = DRIVING_FRAMES
        elif "second" in lab or "duration" in lab:
            v = SHORT_S
        elif "length" in lab and "frame" in lab:
            v = SHORT_FRAMES
        else:
            continue
        if lo is not None:
            v = max(v, lo)
        if hi is not None:
            v = min(v, hi)
        if tool("blender_graph_set", node=node, key=p["key"], value=v)[0]:
            done[p["label"][:40]] = v
    return done


def run_preset(group: str, label: str, prompt: str | None = None) -> dict:
    rec = {"group": group, "label": label, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    t0 = time.monotonic()
    if not wait_connected():
        return {**rec, "ok": False, "error": "Blender not connected to ComfyQ"}
    ok, added = tool("blender_graph_add_preset", preset=label)
    if not ok:
        return {**rec, "ok": False, "error": f"add preset: {added}"}
    names = set(added["nodes"])
    ok, g = tool("blender_graph_describe")
    nodes = [n for n in g["nodes"] if n["name"] in names]
    wnode = next(n for n in nodes if n["type"] == "Workflow")
    rec["workflow_id"] = wnode["workflow_id"]
    rec["inputs"], rec["settings"] = {}, {}
    if any(n["type"] == "Mask" for n in nodes):        # the area an inpaint repaints = the selected objects
        ok, info = tool("blender_scene_info")
        mesh = [o["name"] for o in info.get("objects", []) if o["type"] == "MESH"][:1]
        tool("blender_select", names=mesh)
        rec["inputs"]["selected"] = mesh
    for n in nodes:
        if n["type"] == "Image":
            a = asset_for(n.get("label") or "")
            if a is None:
                return {**rec, "ok": False, "error": f"no test asset for “{n.get('label')}”"}
            tool("blender_graph_set", node=n["name"], key="image", value=str(a))
            rec["inputs"][n.get("label") or n["name"]] = a.name
        elif n["type"] in ("VideoFile", "AudioFile"):
            a = ASSETS / ("face.mp4" if n["type"] == "VideoFile" else "speech.wav")
            if not a.is_file():
                return {**rec, "ok": False, "error": f"no test asset {a.name}"}
            tool("blender_graph_set", node=n["name"], key="filepath", value=str(a))
            rec["inputs"][n.get("label") or n["name"]] = a.name
        elif n["type"] == "Prompt" and (prompt or not (n.get("text") or "").strip()):
            # a workflow without an example prompt leaves the preset's Prompt node empty (required)
            tool("blender_graph_set", node=n["name"], key="text", value=prompt or FALLBACK_PROMPT)
            rec["prompt"] = "given" if prompt else "fallback (no example prompt)"
        elif n["type"] in ("SceneRender", "SceneAnimation", "Mask"):
            rec["inputs"][n.get("label") or n["name"]] = n["type"]
    rec["settings"] = shorten(wnode["name"], wnode["workflow_id"])
    ok, msg = tool("blender_graph_run", node=wnode["name"])
    if not ok:
        return {**rec, "ok": False, "error": f"run: {msg}"}
    log(f"   run: {msg.get('message', msg) if isinstance(msg, dict) else msg}")
    last_note, loaded_at = "", None
    while time.monotonic() - t0 < RUN_TIMEOUT_S:
        time.sleep(10)
        ok, st = tool("blender_graph_status")
        if not ok:
            continue
        me = next((n for n in st["nodes"] if n["node"] == wnode["name"]), None)
        if me is None:
            continue
        note = me.get("note") or ""
        if note != last_note:
            log(f"   note: {note}")
            last_note = note
        job = me.get("job") or {}
        if job and loaded_at is None:
            loaded_at = time.monotonic()
        if me["state"] == "idle":
            rec.update(seconds=round(time.monotonic() - t0),
                       load_seconds=round((loaded_at or time.monotonic()) - t0),
                       job_id=job.get("job_id"), phase=job.get("phase"), error=job.get("error") or note or None,
                       result=me.get("result"), ok=bool(me.get("result") and job.get("ok")))
            return rec
    return {**rec, "ok": False, "error": f"timeout after {RUN_TIMEOUT_S // 60} min"}


# -- main -------------------------------------------------------------------------------------------------------
def main() -> None:
    only = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("--only=")), None)
    OUT.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    results = [] if "--fresh" in sys.argv or not RESULTS.is_file() else \
        json.loads(RESULTS.read_text(encoding="utf-8"))
    done = {r["key"] for r in results if r.get("ok")}

    def save(rec):
        results[:] = [r for r in results if r["key"] != rec["key"]] + [rec]
        RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        log(f"{'OK  ' if rec.get('ok') else 'FAIL'} {rec['key']} ({rec.get('seconds', '?')} s) "
            f"{rec.get('error') or ''}")

    ok, st = tool("comfyq_server_status")
    log("start; ComfyQ serves", [s["workflow_id"] for s in st.get("served", [])] if ok else st)
    # test assets (by presets themselves)
    for fname, label, prompt in ASSET_RUNS:
        key = f"asset:{fname}"
        if (ASSETS / fname).is_file() and key in done:
            continue
        log(f"ASSET {fname} ← {label}")
        rec = run_preset("assets", label, prompt)
        rec["key"] = key
        if rec.get("ok"):
            shutil.copyfile(rec["result"], ASSETS / fname)
        save(rec)
    style = ASSETS / "style.png"
    if not style.is_file():                             # any picture with a strong look; the Flux Klein t2i
        made = [r["result"] for r in results if r.get("ok") and r["key"].startswith("asset:")
                and r["result"].endswith(".png")]         # example (neon diner) if the assets made one
        if made:
            shutil.copyfile(made[-1], style)
    for group, label, _f in presets():
        key = f"{group}/{label}"
        if key in done or (only and not any(o.lower() in key.lower() for o in only)):
            continue
        if any(label == a[1] for a in ASSET_RUNS):
            prior = next((r for r in results if r["key"].startswith("asset:") and r["label"] == label
                          and r.get("ok")), None)
            if prior:                                   # already run for the assets: same preset, same path
                save({**prior, "key": key, "group": group, "note": "run while making the test assets"})
                continue
        prompt = KEYFRAME_FROM[1] if label == KEYFRAME_FROM[0] else None
        log(f"PRESET {key}")
        rec = run_preset(group, label, prompt)
        rec["key"] = key
        if rec.get("ok") and label == KEYFRAME_FROM[0]:
            shutil.copyfile(rec["result"], ASSETS / "keyframe.png")
            shutil.copyfile(rec["result"], style)
        save(rec)
    ok_n = sum(1 for r in results if r.get("ok") and not r["key"].startswith("asset:"))
    log(f"DONE {ok_n}/{len(presets())} presets ok")


if __name__ == "__main__":
    main()
