# SPDX-License-Identifier: GPL-3.0-or-later
"""HTML report of a preset run: one card per workflow with its description, the Blender graph
(screenshot), the inputs actually uploaded, the prompt and settings actually sent, and the output
(image, or a playable video), plus time and a verdict. Self-contained folder: the media are copied next
to index.html. Nothing is run again: everything comes from the run's records, ComfyQ's job records and
the bridge cache.

    python tools/preset_report.py <out folder> [--baseline <earlier report folder>]
        [--results live_check/presets/results.json] [--shots live_check/presets/shots]
        [--notes live_check/presets/notes.json]

With --baseline, each card also shows the earlier run's output for the same workflow (comparison).
The report writes manifest.json, which a later report reads as its baseline.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SERVER = os.environ.get("COMFYQ_URL", "http://127.0.0.1:3000")
CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "comfyq-bridge" / "cache"
GROUPS = ["Image", "Image edit", "Image → video", "Video → video", "Chains"]   # Chains: multi-step class presets
IMG = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VID = {".mp4", ".webm", ".mov", ".mkv"}
AUD = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


def arg(name: str, default: str | None = None) -> str | None:
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a else default


OUT = Path(sys.argv[1]).resolve()
RESULTS = Path(arg("--results", str(ROOT / "live_check/presets/results.json"))).resolve()
SHOTS = Path(arg("--shots", str(ROOT / "live_check/presets/shots"))).resolve()
NOTES = Path(arg("--notes", str(ROOT / "live_check/presets/notes.json"))).resolve()
BASE = Path(arg("--baseline")).resolve() if arg("--baseline") else None
ASSETS = RESULTS.parent / "assets"


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def media(path: Path, rel: str, title: str = "") -> str:
    ext = path.suffix.lower()
    t = f' title="{esc(title)}"' if title else ""
    if ext in IMG:
        return f'<a href="{esc(rel)}" target="_blank"><img loading="lazy" src="{esc(rel)}" alt="{esc(title)}"{t}></a>'
    if ext in VID:
        return f'<video controls loop muted preload="metadata" src="{esc(rel)}"{t}></video>'
    if ext in AUD:
        return f'<audio controls preload="none" src="{esc(rel)}"{t}></audio>'
    return f'<a href="{esc(rel)}">{esc(path.name)}</a>'


def find_input(name: str, before_ms: int | None) -> Path | None:
    """The local file a job uploaded (bridge cache, or the run's test assets). Same names (the clay
    animation is always animation_0001-0250.mp4) → the newest one made before the job."""
    if (ASSETS / name).is_file():
        return ASSETS / name
    hits = [p for p in CACHE.rglob(name) if p.is_file()]
    if before_ms:
        early = [p for p in hits if p.stat().st_mtime * 1000 <= before_ms + 5000]
        hits = early or hits
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def copy(src: Path, dest_dir: Path, stem: str) -> str:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}{src.suffix.lower()}"
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        shutil.copyfile(src, dest)
    return dest.relative_to(OUT).as_posix()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = json.loads(RESULTS.read_text(encoding="utf-8"))
    notes = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.is_file() else {}
    wfs = {w["id"]: w for w in requests.get(f"{SERVER}/workflows?includeUnavailable=1", timeout=30).json()["workflows"]}
    base = json.loads((BASE / "manifest.json").read_text(encoding="utf-8")) if BASE else {}
    labels: dict[str, dict[str, str]] = {}
    manifest, cards, assets_html = {}, {g: [] for g in GROUPS}, []
    counts = {"ok": 0, "warn": 0, "fail": 0}

    for n, rec in enumerate(records):
        wf_id = rec.get("workflow_id") or ""
        job = {}
        if rec.get("job_id"):
            try:
                job = requests.get(f"{SERVER}/jobs/{rec['job_id']}", timeout=30).json()
                job = job.get("job", job)
            except (requests.RequestException, ValueError):
                job = {}
        if wf_id and wf_id not in labels:
            try:
                ps = requests.get(f"{SERVER}/workflows/{wf_id}/parameters", timeout=30).json().get("parameters") or []
            except (requests.RequestException, ValueError):
                ps = []
            labels[wf_id] = {p["key"]: p.get("label") or p["key"] for p in ps}
        lab = labels.get(wf_id, {})
        mdir = OUT / "media" / f"{n:02d}"
        result = Path(rec["result"]) if rec.get("result") else None
        out_rel = copy(result, mdir, "output") if result and result.is_file() else ""

        if rec["key"].startswith("asset:"):
            if out_rel:
                assets_html.append(f'<figure>{media(result, out_rel)}<figcaption><b>{esc(rec["key"][6:])}</b> '
                                   f'— {esc(rec["label"])}<br><small>{esc(job.get("prompt") or "")}</small>'
                                   f'</figcaption></figure>')
            continue
        if out_rel:   # by preset key: two presets can use the same workflow (a chain and the library one)
            manifest[rec["key"]] = {"output": out_rel, "date": rec.get("started"), "seconds": rec.get("seconds")}

        ins = []
        for f in job.get("input_files") or []:
            alt = (rec.get("input_files") or {}).get(f["name"])     # e.g. an intermediate copy no longer cached
            p = Path(alt) if alt and Path(alt).is_file() else find_input(f["name"], job.get("started_at")
                                                                         or job.get("time_slot"))
            if p is None:
                ins.append(f'<div class="in"><span class="lab">{esc(lab.get(f["param"], f["param"]))}</span>'
                           f'<span class="missing">{esc(f["name"])} (not found)</span></div>')
                continue
            rel = copy(p, mdir, f"in_{f['param']}")
            ins.append(f'<div class="in"><span class="lab">{esc(lab.get(f["param"], f["param"]))}</span>'
                       f'{media(p, rel, f["name"])}</div>')
        media_keys = {f["param"] for f in job.get("input_files") or []}
        # "prompt" = ComfyQ's copy of the job prompt (shown once, in its parameter); media = file names
        shown = {k: v for k, v in (job.get("params") or {}).items() if k not in media_keys and k != "prompt"}
        sent = [(lab.get(k, k), v) for k, v in shown.items()]
        prompt_keys = [k for k, v in shown.items() if isinstance(v, str) and len(v) > 60]
        sent_rows = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in sent
                            if not (isinstance(v, str) and len(v) > 60))
        prompts = "".join(f'<blockquote><span class="lab">{esc(lab.get(k, k))}</span>{esc(job["params"][k])}'
                          f'</blockquote>' for k in prompt_keys)
        for step, cj in enumerate(rec.get("chain_jobs") or [], 1):       # a chain: every step's job
            if cj == rec.get("job_id"):
                continue
            try:
                sj = requests.get(f"{SERVER}/jobs/{cj}", timeout=30).json()
                sj = sj.get("job", sj)
            except (requests.RequestException, ValueError):
                continue
            sw = sj.get("workflow_id", "")
            if sw not in labels:
                try:
                    sps = requests.get(f"{SERVER}/workflows/{sw}/parameters", timeout=30).json().get("parameters") or []
                except (requests.RequestException, ValueError):
                    sps = []
                labels[sw] = {p["key"]: p.get("label") or p["key"] for p in sps}
            sl = labels[sw]
            smedia = {f["param"] for f in sj.get("input_files") or []}
            rows = "".join(f"<tr><td>{esc(sl.get(k, k))}</td><td>{esc(v)}</td></tr>"
                           for k, v in (sj.get("params") or {}).items() if k != "prompt" and k not in smedia)
            secs = round(((sj.get("finished_at") or 0) - (sj.get("started_at") or 0)) / 1000)
            prompts = (f'<h4>Step {step}: {esc(wfs.get(sw, {}).get("name", sw))} · {secs} s · job '
                       f'<code>{esc(cj[:8])}</code></h4><table>{rows}</table><h4>Step {step + 1}: '
                       f'{esc(wfs.get(wf_id, {}).get("name", wf_id))}</h4>') + prompts
        shot = SHOTS / f"{n:02d}.png"
        shot_html = (f'<a href="{esc(copy(shot, mdir, "blender"))}" target="_blank"><img loading="lazy" class="shot" '
                     f'src="{esc((mdir / "blender.png").relative_to(OUT).as_posix())}" alt="Blender graph"></a>'
                     if shot.is_file() else '<p class="missing">no screenshot</p>')
        verdict, note = notes.get(rec["key"]) or notes.get(wf_id) or ["ok" if rec.get("ok") else "fail", ""]
        counts[verdict] = counts.get(verdict, 0) + 1
        w = wfs.get(wf_id, {})
        desc = (w.get("description") or "").strip()
        vram = w.get("vramPeakGb") or (w.get("runtime") or {}).get("vramPeakGb")
        b = base.get(rec["key"]) or (base.get(wf_id) if rec["group"] != "Chains" else None)
        base_html = ""
        if b and BASE:
            bp = BASE / b["output"]
            rel = os.path.relpath(bp, OUT).replace("\\", "/")
            base_html = (f'<div class="col"><h4>Baseline · {esc(b.get("date"))} · {esc(b.get("seconds"))} s</h4>'
                         f'{media(bp, rel) if bp.is_file() else "<p class=missing>missing</p>"}</div>')
        if out_rel and result.suffix.lower() not in IMG | VID | AUD and rec.get("preview"):
            pv = (ROOT / rec["preview"]) if not Path(rec["preview"]).is_absolute() else Path(rec["preview"])
            out_html = (media(pv, copy(pv, mdir, "preview")) + f'<p class="dim">Rendered views of the result · '
                        f'<a href="{esc(out_rel)}" download>download {esc(result.suffix[1:].upper())}</a></p>')
        elif out_rel:
            out_html = media(result, out_rel)
        else:
            out_html = f'<p class="missing">{esc(rec.get("error") or "no result")}</p>'
        meta = (f'{esc(rec.get("seconds"))} s total · model loaded in {esc(rec.get("load_seconds"))} s · '
                f'job <code>{esc((rec.get("job_id") or "—")[:8])}</code> · {esc(rec.get("started"))}'
                + (f' · VRAM peak {vram:.1f} GB' if isinstance(vram, (int, float)) else ""))
        cards[rec["group"]].append(f'''
<article class="card v-{verdict}" id="{esc(rec["key"])}" data-verdict="{verdict}">
 <header><span class="badge {verdict}">{ {"ok": "OK", "warn": "Check", "fail": "Failed"}.get(verdict, verdict)}</span>
  <h3>{esc(rec["label"])}</h3><code>{esc(wf_id)}</code></header>
 {f'<p class="note">{esc(note)}</p>' if note else ""}
 <details><summary>What this workflow does</summary><p class="desc">{esc(desc)}</p></details>
 <div class="row">
  <div class="col"><h4>Blender graph</h4>{shot_html}</div>
  <div class="col"><h4>Inputs sent</h4>{"".join(ins) or '<p class="dim">none (prompt only)</p>'}</div>
  <div class="col out"><h4>Output</h4>{out_html}</div>
  {base_html}
 </div>
 <details><summary>Prompt and settings sent</summary>{prompts}<table>{sent_rows}</table></details>
 <p class="meta">{meta}</p>
</article>''')

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in cards.values())
    nav = "".join(f'<a href="#g{i}">{esc(g)} ({len(cards[g])})</a>' for i, g in enumerate(GROUPS))
    body = "".join(f'<section id="g{i}"><h2>{esc(g)}</h2>{"".join(cards[g])}</section>' for i, g in enumerate(GROUPS))
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preset Run Baseline</title>
<style>
:root{{--bg:#f6f6f4;--fg:#1d1f1e;--dim:#6b6f6c;--card:#fff;--line:#dcdcd6;--ok:#2f7d4f;--warn:#b07a12;--fail:#b3352f;--accent:#3d4640}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151716;--fg:#e8e9e6;--dim:#9a9e9a;--card:#1f2220;--line:#333833;--ok:#5fbf86;--warn:#e0aa45;--fail:#ec6f67;--accent:#8fa596}}}}
:root[data-theme="dark"]{{--bg:#151716;--fg:#e8e9e6;--dim:#9a9e9a;--card:#1f2220;--line:#333833;--ok:#5fbf86;--warn:#e0aa45;--fail:#ec6f67;--accent:#8fa596}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif}}
.wrap{{max-width:1500px;margin:0 auto;padding:16px}}
h1{{margin:.2em 0}} h2{{border-bottom:2px solid var(--accent);padding-bottom:4px;margin-top:2em}}
h3{{margin:0;font-size:16px}} h4{{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}}
nav{{position:sticky;top:0;background:var(--bg);padding:8px 0;display:flex;flex-wrap:wrap;gap:12px;align-items:center;border-bottom:1px solid var(--line);z-index:2}}
nav a{{color:var(--fg)}} nav label{{color:var(--dim)}}
.summary{{color:var(--dim)}} .card{{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--line);border-radius:8px;padding:12px 14px;margin:14px 0}}
.v-ok{{border-left-color:var(--ok)}} .v-warn{{border-left-color:var(--warn)}} .v-fail{{border-left-color:var(--fail)}}
.card header{{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline}} code{{color:var(--dim);font-size:12px}}
.badge{{font-size:11px;font-weight:700;padding:2px 7px;border-radius:10px;color:#fff}}
.badge.ok{{background:var(--ok)}} .badge.warn{{background:var(--warn)}} .badge.fail{{background:var(--fail)}}
.note{{margin:6px 0;font-weight:500}} .desc{{white-space:pre-wrap;color:var(--dim)}}
.row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:10px 0}}
.col img,.col video{{width:100%;max-height:420px;object-fit:contain;background:#000;border-radius:4px}}
.shot{{background:transparent!important}} .in{{margin-bottom:8px}} .in img,.in video{{max-height:200px}}
.lab{{display:block;font-size:12px;color:var(--dim)}} audio{{width:100%}}
blockquote{{margin:6px 0;padding:6px 10px;border-left:3px solid var(--line);white-space:pre-wrap}}
table{{border-collapse:collapse;font-size:12px}} td{{border-top:1px solid var(--line);padding:3px 8px;vertical-align:top}}
.meta,.dim{{color:var(--dim);font-size:12px}} .missing{{color:var(--fail)}} details summary{{cursor:pointer;color:var(--dim)}}
.assets{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}} figure{{margin:0}}
.assets img,.assets video{{width:100%;border-radius:4px}} figcaption{{font-size:12px}}
.hide-ok .v-ok{{display:none}}
</style></head><body><div class="wrap">
<h1>Preset library run — baseline</h1>
<p class="summary">{total} presets · <b style="color:var(--ok)">{counts.get("ok", 0)} OK</b> ·
<b style="color:var(--warn)">{counts.get("warn", 0)} to check</b> · <b style="color:var(--fail)">{counts.get("fail", 0)} failed</b> ·
generated {time.strftime("%Y-%m-%d %H:%M")}{f" · compared with <code>{esc(BASE.name)}</code>" if BASE else ""}.
Scene: Blender default cube (nothing animated), 1920×1080 · 24 fps; videos capped at 2 s; each workflow's own example prompt;
models loaded by solo mode. Details: docs/preset-run.md.</p>
<nav>{nav}<a href="#assets">Test assets</a>
<label><input type="checkbox" onchange="document.body.classList.toggle('hide-ok',this.checked)"> hide OK</label></nav>
{body}
<section id="assets"><h2>Test assets (made by presets, used as inputs)</h2><div class="assets">{"".join(assets_html)}</div></section>
</div></body></html>'''
    (OUT / "index.html").write_text(page, encoding="utf-8")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) / 1e6
    print(f"{OUT / 'index.html'}: {total} presets ({counts}), {size:.0f} MB")


if __name__ == "__main__":
    main()
