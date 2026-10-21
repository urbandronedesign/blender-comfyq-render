---
name: comfyq-workflow-build
description: The protocol for building a new AI-render workflow in ComfyQ Render (Blender ↔ ComfyQ) (e.g. "segment the scene, label the surfaces in the prompt, render, upscale"): check what ComfyQ offers, build the tools in layers, test on a generated scene, compare candidate workflows on the GPU by looking at the results, then ship it as a graph node + class preset + report card + docs. Use whenever the user asks for a new pipeline, a new kind of render, or "the best workflow for X".
---

# Building a workflow: the protocol

Worked example of every step: the architecture chain (2026-10-04), `docs/arch-render.md`, commits
`230c504` → `6f6beff`. Read `CLAUDE.md` first (gotchas, rules). The order below is the protocol; do not
skip the "look at it" steps.

## 0. Rules that hold throughout
- ComfyQ's code and behaviour are not changed. A missing capability = a request for b2renger (say so,
  write it in the doc's "Limits / next"); work around it in the bridge.
- Tell the user before every GPU run and every visible Blender window; say which models load (solo mode
  switches ComfyQ's model, ~30–70 s each, only when nobody else has a job).
- Ask before committing (repo identity `urbandronedesign`); install into the user's Blender only when asked,
  never `--factory-startup`. The user restarts Blender to load a build.
- Never tick the user's preferences by script. The sandbox profile (`%TEMP%\comfyq_bridge_smoke_blender`)
  may be changed freely (solo on, etc.).
- Short progress lines while working; the final message says what was verified and what was not.

## 1. Find out what ComfyQ really offers
- Read `F:\ComfyQ\workflows\<id>\<id>.meta.json` of every candidate: `exposedParameters` (key, type, label,
  min/max, `maxInputEdge`), `requirements` (VRAM, models), description, and the `.api.json` output node
  (what file comes back). List every workflow of the category (`ls | grep -iE …`), not only the first hit.
- Note the traps before designing: input kind (SAM3 = video only), limits (upload box 1920×1080; outputs
  can exceed it, Qwen = 1920×1088), one result per phrase, what is served now (`/admin/lanes`).
- Check earlier verdicts: `live_check/presets/notes.json`, `docs/preset-run.md`, `docs/workflow-matrix.md`.

## 2. Plan in one short message, then build in layers
Each layer is testable on its own, in this order:
1. **bpy-free logic** in the add-on root or `client/` (e.g. `surfaces.py`: parsing, prompt composition) →
   unit tests in `tests/unit/` (the venv has no numpy/bpy).
2. **bpy passes** in `passes/` (renders, conversions, masks; numpy is fine) — main thread only.
3. **runtime** module (e.g. `runtime/segmentation.py`): the ComfyQ jobs, runs on a worker thread; bpy parts
   go through a `hop(fn)` callback (main-thread call in the add-on, plain call in headless scripts).
4. **MCP tools** in `mcp/tools.py` (long work = background task + `blender_task_status`); update the tool
   count in `tests/blender/smoke.py`.
5. **Graph node** (when students need it in the graph): a node class in `graph/tree.py`, add it to
   `RUN_NODES` if a run books it, `run.py` (`_own_hash`, booking, `_text`/`_file` for its outputs), `outputs.py`,
   `ui.py` (menu, timer previews), `preset_ops.py` (save/load), MCP graph describe/set/status.
6. **Class preset** in `presets/class/*.json`, copied to `%APPDATA%\comfyq-bridge\presets\Class\`.

## 3. A test scene made by a script
- `tools/make_<topic>_scene.py` builds a representative scene headless (`blender -b --factory-startup
  --python … -- E:\zaki\<topic>.blend`), outside the repo. Look at a Workbench preview (Read the PNG) and fix
  the framing before any GPU work. Never test on the user's own file unless asked.

## 4. Step-by-step headless driver (GPU)
- `tools/<topic>_render.py <scene> <out> <step> [--options]`, steps like `base | segment | render
  [--only=a,b] | refine --from= | upscale --from= [--only=]`; every step writes `<out>/state.json` so the next
  step and a human can look first. Candidates live in dicts (workflow, input key, settings, prompt prefix).
- Build + install into the sandbox with `tools\smoke_test.ps1` first (it also proves nothing broke). The
  smoke test cleans the pass cache: copy renders the driver needs into `<out>`.
- Run long steps with `run_in_background` + a Monitor on the output (`grep` progress AND failure lines).
- Uploads: fit every PNG to the upload box (`passes.segment.fit_upload`) before `client.upload`.

## 5. Compare candidates — by looking
- Run 2–3 workflows per stage on the same input and the same prompt; record the time of each.
- **Read every result image.** Then make 1:1 crops of the same regions side by side with ComfyUI's Python
  (`F:\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\python_embeded\python.exe`, has cv2/av):
  fabric, wood, text, flat walls/floors. Judge layout fidelity first (does the geometry survive? do the
  masks still line up?), then texture realism (repeating patterns, invented lettering, grids), then speed.
- When the user dislikes a result, find WHICH stage causes it (crop before vs after each stage) before
  proposing fixes; offer the untested options with honest costs; test what they pick; report failures
  plainly (e.g. "Qwen depth at 4 MP: blurred, layout lost").
- Pick by evidence and say why in one line per candidate.

## 6. Ship it in the graph and prove it end to end
- Build the preset and check it in a sandbox Blender (`preset_ops.build_saved` → every link present).
  Node names in rebuilt presets are Blender defaults: match nodes by `bl_idname`, not by name.
- Run the whole preset as a real graph headless (sandbox, solo on): pump `SESSION.pump()` + `run.tick_all()`;
  read the final images.
- Smoke checks for the new pieces without GPU (fake results, e.g. found regions); GPU-only checks are
  skipped when they would change what ComfyQ serves (gotcha 32).

## 7. Report card and docs
- Add a `Chains/<label>` record to `live_check/presets/results.json` (`chain_jobs`, `results`,
  `input_files` overrides, `steps_media` = [[caption, picture]] for each step incl. rejected candidates and
  the crop comparison, a JPG of huge outputs) + a verdict in `notes.json`; screenshot the graph with
  `tools/preset_shots.py … --only=<word>` (visible sandbox window: tell the user); rebuild
  `tools/preset_report.py baselines/2026-10-04`.
- Docs: `docs/<topic>.md` (chain table with times and the reason for each choice, what to know about the
  workflows, how to use it: agent / students / headless, limits / asks for b2renger), `docs/CLASS.md`
  (preset), `docs/UI.md` (new controls + their checks), `docs/AGENTS.md` (new tools), `CLAUDE.md` (status,
  coverage count, new gotchas).

## 8. Finish
- `ruff check extension tests tools`, `pytest tests/unit`, `tools\smoke_test.ps1` all pass → summary to the
  user: what was built, what was verified (with the numbers), what was not, what ComfyQ serves now → ask to
  commit / install. After install: tell the user to restart Blender and where the new things are.

## Tool habits that saved time here
- Write code with the Write/Edit tools; Bash heredocs mangle backslashes (`\\z`, `\\a`, line continuations).
- PowerShell: keep CLI arguments ASCII; pass absolute paths to Blender scripts.
- Blender temp scenes (VSE) convert still ↔ video and read video frames; set the view transform to Standard.
