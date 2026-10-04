# CLAUDE.md — Mixar ↔ ComfyQ Bridge

A Blender extension (`comfyq_bridge`) that runs inside **Mixar** (a Blender 5.2 fork) and plain
**Blender 5.x**. It renders scene passes (clay stills, masks, the clay *animation*), sends them to
**ComfyQ** workflows (a multi-user ComfyUI scheduler), and brings results back into the scene.
Main use: **AI rendering** of a Mixar layout/animation (ControlNet, image edit, video-to-video), not Cycles.
Used in a **class on a local network**.

**Since 2026-10-03 the user works in Blender 5.2 LTS only: Mixar was uninstalled** (MSI + leftover
program folder removed; its user data in `%APPDATA%\Mixar` / `%LOCALAPPDATA%\Mixar` was left). The code
stays Mixar-compatible (`smoke_test.ps1 -App mixar` if Mixar is reinstalled); Mixar-only gotchas below are history.

Approved plan: `C:\Users\Ateliernum\.claude\plans\we-are-planning-a-effervescent-widget.md`.
Git: local repo, branch `main`, no remote. Commit author = repo-local `urbandronedesign <urbandronedesign@users.noreply.github.com>`
(never the global identity). Ask the user before committing. **UI reference: `docs/UI.md`** (every button + its check).

## Environment (this machine)
| Thing | Where |
|---|---|
| **Blender 5.2.2 LTS** (Python 3.13.13) — the host | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (winget `BlenderFoundation.Blender`, upgraded from 5.1 on 2026-10-03; the old 5.1 folder is still on disk and runs). Plugin installed in `%APPDATA%\Blender Foundation\Blender\5.2` (build of commit `0537d86`: library, solo mode, image-upload fix, MCP image nodes), Allow Online Access on, **AI agents (MCP server) ON** (port 8790), **Teacher tools ON** (agents may load models; set 2026-10-04 by a startup script + `save_userpref`). Theme = Mixar's **"Mixar Forest"** with Input node colour changed from #ff284d to #3d4640 (calmer graph headers) (exported from `%APPDATA%\Mixar\Mixar\5.2\config\mixar_userpref.blend`, preset `scripts\presets\interface_theme\mixar_forest.xml`; previous prefs backed up as `config\userpref.before_mixar_theme.blend`) Default of `smoke_test.ps1` and `comfyq.ps1` |
| Mixar 4.2.0 | **uninstalled 2026-10-03** (was `C:\Program Files\Mixar\mixar.exe`, profile `%APPDATA%\Mixar\Mixar\5.2`) |
| ComfyQ server (Node, Socket.IO, SQLite) | `F:\ComfyQ`, http://127.0.0.1:3000 — user works with b2renger, full control of its code |
| ComfyUI 0.37, torch 2.8 cu128, RTX 5090 32 GB | `F:\ComfyUI_windows_portable_nvidia\…`, :8188 (listens on 0.0.0.0 — P1.0 issue) |
| Ollama | 127.0.0.1:11434 |
| Bridge cache | `%LOCALAPPDATA%\comfyq-bridge\cache\` — `passes\` (temporary renders), `<job8>\` (downloaded results) |
| User's test project | `Desktop\testV2V_clean.mixar` (cleaned copy, use this). `.mixar` = zstd-compressed .blend: **Blender 5.2 opens it** (scene 0–120 @ 24 fps, camera, ComfyQ settings intact) |

**User constraints:** do not change the live ComfyQ/ComfyUI server behaviour (P1.0 hardening is postponed).
Every workflow stays visible (never hide uncalibrated ones). Update check = manual smoke script.

## Layout
```
extension/comfyq_bridge/
  __init__.py            register/unregister, timer pump (_tick), load_post reattach; purges own submodules on reload
  blender_manifest.toml  id comfyq_bridge, GPL-3.0-or-later, vendored wheels
  wheels/                python-socketio, python-engineio, bidict, websocket-client (pure py; requests is bundled by hosts)
  client/                NO bpy. ComfyQ protocol only (Apache-2.0)
    api.py               ComfyQClient: login, workflows, params, upload, submit (also sends top-level prompt), wait, download
    transport/base.py    ComfyQTransport interface (phase 2 adds RestTransport)
    transport/socketio_transport.py  phase 1: book_job / cancel_job / delete_job, state_update/state_patch cache
    params.py            meta.json params -> ParamSpec (prompt/text/int/float/seed/bool/enum/image/video/audio/mask)
    models.py http.py profile.py doctor.py cli.py
  match.py               scene <-> workflow matching (duration/frames/fps/size/aspect by label), frame_for() start/last frame
  props.py               Scene.comfyq: tab, workflow_id, keyframe_workflow_id, params (per workflow!), jobs (inputs JSON)
  prefs.py               student name, profile, URL override (never tokens)
  runtime/session.py     SESSION: worker pool + main-thread queue (pump), connect, specs cache, JobView, reattach, download
  passes/render.py       Workbench passes: structure (clay = true grayscale), mask (RGBA alpha = repaint area), image
  passes/animation.py    clay animation MP4 (frame range through scene camera), cache + depsgraph staleness
  ops/generate.py        start_job (render inputs -> upload -> book), AI-keyframe chain, cancel/import/open folder
  ops/workflow.py        select workflow, menus, match scene, previews, multi-line prompt (Text), detached ComfyQ window
  ops/results.py         job-card buttons (save copy, import file, download) + Clean up
  ops/connection.py      connect / disconnect / refresh
  importers/media.py     image -> Image Editor; video/audio -> separate "ComfyQ Results" scene; GLB/PLY -> collection
  ui/studio.py           the Mixar-style bar (tabs, chips, prompt, scene strip, references, job cards with "Sent" rows)
  ui/panel.py            panels: View3D sidebar, Image Editor sidebar, Queue popover, dialog (Ctrl+Shift+Q), header buttons
  ui/player.py           video player for the ComfyQ window (independent of the scene timeline)
  graph/                 node graph (Moodboard-style, docs/plans/node-graph.md): kinds.py + engine.py (bpy-free),
                         tree.py (nodes), ui.py (toolbar/operators/timer sync), run.py (running the graph),
                         outputs.py (Viewer/Import/Save), presets.py (bpy-free) + preset_ops.py (class presets)
  mcp/                   MCP server for AI agents (P1.4, off by default): server.py (bpy-free JSON-RPC over
                         HTTP, 127.0.0.1 + token), tools.py (blender_* / comfyq_* tools, on_main), launcher.py
                         (stdio launcher agents run; ships with the extension)
tests/unit/              pytest, no bpy (params, match, models/profile, submit)
tests/contract/          against the live ComfyQ (read-only + a no-GPU booking 1 day ahead, then deleted)
tests/blender/smoke.py   headless inside Mixar/Blender (run via tools/smoke_test.ps1)
tests/blender/ui_check.py VISIBLE Blender (or Mixar): presses every button in its real window/area/region,
                         graph steps, live chain with -Book (smoke_test.ps1 -Ui [-Book])
docs/UI.md               user-facing reference of every control, mapped to the ui_check checks
docs/CLASS.md            teacher guide: class setup, presets, sharing presets
docs/AGENTS.md           AI agents: turning the MCP server on, Claude Code / Codex config, the tools
tests/fixtures/cube.glb  committed fixture (Mixar's glTF exporter crashes at exit in -b)
tools/smoke_test.ps1     build zip -> install into an ISOLATED profile -> run smoke (-Book: one real GPU job)
tools/comfyq.ps1         client CLI with Mixar's Python: doctor | workflows | params | submit | fetch
tools/live_check.py      run from Mixar's Text Editor on the user's open scene; writes live_check/report.json + window.png
tools/open_and_send.py   `mixar <file> --python tools/open_and_send.py -- [--no-send] [--cleanup]`
tools/late_open.py       start Mixar THEN open a file (workaround for the launch crash), then runs open_and_send
tools/blender_mcp.py     repo wrapper of mcp/launcher.py (used by .mcp.json)
tools/agent_roundtrip.ps1  sandbox Blender + MCP on + `claude -p` doing render → submit → wait → import
tools/graph_send.py      build Scene Animation → served V2V node on a real file, Generate on the node, trace it
tools/make_preset_library.py  headless (sandbox profile): one saved preset per image/video workflow →
                         presets/library/<Image|Image edit|Image → video|Video → video>/*.json (docs/CLASS.md §3)
tools/preset_run.py      run every library preset through the MCP on the open scene (Solo on), resumable
tools/contact_sheet.py   review sheets of results (start/mid/end frames, audio) — ComfyUI's python (cv2, av)
tools/preset_shots.py    visible sandbox Blender: each preset's graph rebuilt with its run result → screenshots
tools/preset_report.py   HTML report of a run (description, graph shot, inputs, prompt/settings sent, output,
                         verdict from live_check/presets/notes.json) → baselines/<date>/ (gitignored);
                         --baseline <dir> shows the earlier output side by side. Baseline: baselines/2026-10-04
presets/library/         the generated workflow library (48 presets, committed; copied to the user's presets folder)
```

## Commands
```powershell
.\.venv\Scripts\python -m pytest -q tests\unit tests\contract     # unit + live contract (ComfyQ must run)
.\.venv\Scripts\ruff check extension tests tools
.\tools\smoke_test.ps1                  # Blender 5.2, isolated profile, no GPU   (also: -Book, -App mixar)
.\tools\smoke_test.ps1 -Ui [-Book]      # VISIBLE Mixar (~3 min, tell the user): every button on a copy of
                                        # testV2V_clean.mixar -> live_check\ui\report.json + screenshots
.\tools\smoke_test.ps1 -Sweep           # P1.5 (GPU): calibrate + run every SERVED workflow from the scene,
                                        # -> live_check\sweep\*.json + docs\workflow-matrix.md. With the user's OK:
                                        # -SweepArgs "--lanes=a,b" (serve alongside) or "--switch=a,b --restore=c"
                                        # (main workflow; ComfyQ restarts under nodemon each time)
.\tools\comfyq.ps1 doctor               # classroom health check
# install the build into the USER's Blender 5.2 profile (only when asked). NOT --factory-startup: it
# saves factory preferences and turns the user's "Allow Online Access" off (seen 2026-10-03)
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --online-mode --command extension install-file -r user_default -e dist\comfyq_bridge-0.1.0.zip
# send a job from the node graph on a real project (visible window; use a sandbox profile for unreleased code):
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --online-mode --python tools\graph_send.py -- <file> <prompt.txt>
```
`dist\comfyq_bridge-0.1.0.zip` is rebuilt by every smoke run. Installing it into a running Mixar does not
change that session: the user must reinstall from the UI or restart Mixar.

## ComfyQ contract used (phase 1 = ComfyQ code unchanged)
- HTTP: `GET /workflows[?includeUnavailable=1]`, `GET /workflows/:id/parameters`, `POST /upload` (field `file`,
  images ≤1920×1080), `GET /jobs?…`, `GET /jobs/:id`, `GET /media/_any/<subfolder/filename>`, `/access/status|login`.
- Socket.IO: `register_user(name)`, `book_job({workflow_id, params, user_id, prompt, scheduledTime}, ack)` →
  `{ok, jobId}`; `cancel_job`/`delete_job(jobId)` (own jobs only); `state_update`/`state_patch` (`seq`, `jobs`,
  `lanes` = served workflows with `parameter_map`).
- Only **served** workflows (lanes) can be booked; others are refused ("not being served").
- Media param value = filename returned by `/upload`. `mask` params = LoadImage alpha (transparent = repaint).
- ComfyQ's job cards show the top-level `prompt` → the client sends the main prompt param there too.

## Gotchas (all learned the hard way — keep)
1. **Sequencer replaces the camera in renders.** If a scene's VSE has strips and `render.use_sequencer` is on,
   Blender renders the strips. Old AI results imported into the 3D scene's VSE were being sent as the "clay"
   input. Fix: passes disable `use_sequencer`/`use_compositing` (restored after); results go to the
   "ComfyQ Results" scene; `comfyq.cleanup` removes strips; the bar warns. Smoke test covers it.
2. **Mixar env vars are renamed:** `MIXAR_USER_RESOURCES` (not `BLENDER_USER_RESOURCES`). Sandboxes must set
   both; smoke_test.ps1 aborts if the real profile is touched.
3. **Mixar launch crash:** `Desktop\testV2V.mixar` (17:41 save) crashes Mixar *when launched with the file*
   (even `--factory-startup`), but opens fine via File › Open after startup. Not ComfyQ data, not workspace,
   frame, texts, VSE strips, missing movies (all bisected). Workaround: `tools/late_open.py`. Unresolved —
   likely a Mixar 4.2 bug to report.
4. `bpy.app.version_string` returns Mixar's "4.2.0"; use `bpy.app.version`.
5. Blender 5: set `image_settings.media_type` ("IMAGE"/"VIDEO") before `file_format` (PNG / FFMPEG).
6. ImageUser frame: setting `frame_offset` doesn't redraw a movie; re-assign `space.image = space.image`.
7. Never keep Python refs to collection items across add/remove (Blender reallocates → crash); re-fetch.
8. Operator callbacks run later: capture `self.x` into locals before scheduling (`ReferenceError` otherwise).
9. Region (sidebar) width is read-only in Blender 5.2; the user drags it. Properties-editor tabs can't host
   a clean full-width add-on panel (tried Scene/Tool tabs).
10. A new window has no laid-out areas until drawn → window setup runs step by step from a timer.
    `region.active_panel_category = "ComfyQ"` raises **AttributeError "read-only"** until the sidebar
    has been drawn once (RNA says writable) → catch it and retry until it reads back.
11. Mixar opens extra windows (agent bubble): identify our window by pointer, not "last window".
12. Mixar's glTF *exporter* crashes Mixar at exit in background mode (import is fine).
13. Background mode: `bpy.app.timers` don't run → tests pump `SESSION.pump()` themselves; `open_mainfile`
    drops non-persistent timers (use `persistent=True`).
14. PowerShell `Set-Content -Encoding utf8` writes a BOM → breaks `compile()`; edit Python files with the
    Edit tool or Bash, never PS Set-Content.
15. Float props are single precision: compare with tolerance (`_same()` in studio.py).
16. Visible GUI tests: run Mixar with the sandbox env vars + a script that screenshots
    (`bpy.ops.screen.screenshot`) and quits; tell the user a window will flash. `smoke_test.ps1 -Ui` does it.
    Screenshots show the user's account email (Mixar top bar): never commit or publish them.
17. **Windows are saved in the .blend.** The ComfyQ window comes back on reopen with a new pointer →
    it is marked by a custom property on its screen (`comfyq_window`); older files are matched by layout
    (Image Editor + Text Editor). Never write ID data from draw code (marking happens in a timer).
18. **Session state != file state.** Specs live in the session, param items in the .blend: after Connect,
    Refresh, reconnect or file load, `ensure_loaded()` (called from the timer) reloads/syncs them, or the
    bar shows "Loading…" and Generate stays greyed out.
19. Shortcuts: Object Mode's keymap runs before "3D View" → Ctrl+Shift+G (add to collection) never
    reached us. Use Ctrl+Shift+Q. Background mode has no populated keyconfigs: check clashes in the GUI.
20. Bash heredoc → `python -` reads stdin in the ANSI code page: non-ASCII (⤢, ✕) breaks string matching.
    Use `PYTHONUTF8=1` or a script file; Windows paths in such scripts need raw strings.
21. **Custom node trees + (re)registration crash Blender**: registering node classes updates the open
    file's trees and calls our `NodeTree.update()` with half-registered socket types; touching sockets
    there = access violation (seen on reinstall). Guard with `graph.tree.REGISTERING`; read socket kinds
    from `bl_idname`, not Python attributes.
22. **Don't infer "the scene changed" from depsgraph updates.** Our own renders (frame steps of an
    animated camera, mask recolouring) and Mixar's UI (re-tags objects with *shading* updates while
    drawing) produce them constantly. The graph hashes the scene content instead
    (`render.scene_fingerprint`). The depsgraph tracker (bar's animation cache) ignores our passes
    (`render.own_changes`), images/texts/UI and flagless Object updates; `animation.edit_log` shows why.
23. Panel/Menu `draw` methods can't be monkeypatched with a plain wrapper (Blender calls them its own
    way); tests read panel tracebacks from the console log instead.
24. **Never remove a `load_post` handler from inside itself.** Blender is iterating the list; removing an
    entry makes it skip the next handler (in Blender 5.2's order: the add-on's `_on_load`, so a saved ComfyQ
    window was not adopted). Remove it from a timer right after (`_unhook` in our tools).
25. **Add-on node headers can't be coloured**: every Python node is class *Input* (the theme's maroon)
    and `Node.color_tag` is read-only in 5.2 (probed: class attributes and NodeCustomGroup change nothing).
    `bl_use_group_interface = False` on the tree hides Blender's group panels/menus. Movie thumbnails:
    `bpy.utils.previews` `load(name, path, "MOVIE")` (works; `icon_id` stays 0 in background mode).
26. After `screen.area_split` both areas keep stale `x`/`width` until the next draw: decide which part is
    which from a timer (`ops.workflow.set_graph_mode`).
27. Property definitions are evaluated by Blender in its own namespace (string annotations with
    `from __future__ import annotations`): a `lambda` as `update=` cannot see the module's functions
    (NameError at the first change). Use a named function defined before the class.
28. **Sweeps / long GPU runs:** run `smoke_test.ps1 -Sweep …` with `run_in_background` and NO `*>` redirect
    (PowerShell 5.1 writes UTF-16 and `Out-Host` lines never reach the file); watch the task's output file
    with Monitor (`grep "\] <id> → |restored|RESTORE|DONE"`). `Get-Process blender` once missed a running
    headless Blender: check with `Get-CimInstance Win32_Process` (command line contains sweep.py).
29. **Switching ComfyQ's main workflow** (`POST /admin/activate-workflow`) restarts the ComfyQ server: it
    touches its own index.js so **nodemon** respawns it; port 3000 is down ~1–2 min while ComfyUI loads the
    model. The sweep closes and reconnects its session (`serve_main`) and keeps base URL/headers from the
    first connection. Extra lanes (`POST /admin/lanes`) need peaks to fit in card − 2 GB: on the 5090
    nothing heavy pairs (LTX/Flux Dev ~30 GB each; even Qwen t2i 14.6 + Klein 17.3 is over).
30. **"ok" in the sweep = job completed + result downloaded + imported.** It is not "looks right": look at
    the images (Read tool) — black outputs happen with some ComfyUI flag mixes (the sweep flags mean < 0.02),
    and a workflow given an unsuitable input can echo it back (Krea character sheet on a cubes scene).

## Status (2026-10-04 afternoon) — **for b2renger and the teacher: docs/RUNBOOK.md**
- **Host:** Blender 5.2.2 LTS (user's profile has the latest build). Mixar uninstalled. ComfyQ serves
  **LTX 2.5 IC-LoRA canny** (`video_edit_ltx2_5_iclora_canny_control`) — the state to restore after any test.
- **Done — bar & tools:** P1.1 client + Socket.IO transport + CLI; P1.2 extension MVP; P1.3 imports
  (image/video/GLB/PLY/audio), reattach after reload, per-workflow settings; animation → V2V (clay MP4 with
  a sound track, always re-rendered at Generate, scene/workflow matching, start/last frame); AI-keyframe
  chain in the bar (**never run end-to-end**); Moodboard-style bar (sidebar + Ctrl+Shift+Q dialog +
  detached ComfyQ window); multi-line prompts; player; job cards; Clean up. Every control in **docs/UI.md**.
- **Done — node graph N0–N5** (docs/plans/node-graph.md §11–16): Node Editor graph (sources, workflow
  nodes from ComfyQ's parameters, running with chains/reuse/cancel), N3 look (graph mode of the ComfyQ
  window, Open as Graph, video thumbnails, adaptive header), N4 outputs (Viewer / Import / Save, Reroute,
  Frame, result history, sidebar ComfyQ tab), N5 class presets (5 built in by role + teacher presets in
  JSON, shared via `COMFYQ_BRIDGE_PRESETS`; teacher guide **docs/CLASS.md**). **Workflow library**: 48
  generated presets (one per t2i / image-edit / i2v / video-edit workflow) in `presets/library/`, copied to
  the user's `%APPDATA%\comfyq-bridge\presets` (2026-10-04); subfolders = submenus of Presets › Saved;
  saved workflow nodes with `match_scene` keep defaults + take the scene's duration/size/fps. All 48 load
  in a sandbox with every link; **none run on the GPU from the library yet**.
- **Solo mode** (Preferences "Solo: load models on demand", off; never in class): Run Graph / MCP
  `blender_graph_run` loads the models nodes need (`runtime/serving.py`, shared with the MCP teacher tools):
  one switch at a time, only when nothing is booked and **no job of anyone** is unfinished on ComfyQ
  (`client.active_jobs()`); the last model stays loaded (user's choice 2026-10-04: no restore, the next
  preset decides). Nodes show *Loads on Run* +
  a Load Model button. Smoke-tested with ComfyQ's switch faked. **Verified live 2026-10-04** (build `3435437`,
  user pressed Run Graph on the library preset Flux2 Klein 9B t2i): ComfyQ switched to Klein, job `80f306d2`
  made a 1024×576 image (checked), then LTX canny was served again (the restore step, removed since); Blender's socket reconnected by itself
  (~10 s after ComfyQ was back). Turning Solo on is the user's own choice (a script ticking it was refused). `smoke_test.ps1 -Ui` 92/92,
  `-Ui -Book` green. **Not yet compared with the Moodboard screenshot by the user.**
- **Done — P1.4 MCP** (docs/AGENTS.md): server inside the extension (off by default; ON in the user's
  prefs), 127.0.0.1 + token in `%APPDATA%\comfyq-bridge\mcp.json`, 17 tools (+3 **teacher tools** behind the
  preference "Teacher tools: agents may load models", off/hidden by default: `comfyq_serve` alongside/main,
  `comfyq_unserve`, `comfyq_calibrate`; `comfyq_server_status` for everyone), stdio launcher
  `mcp/launcher.py` (shipped), repo `.mcp.json`, skill `.claude/skills/blender-comfyq-roundtrip`. Round trip
  by **Claude Code** verified (`tools\agent_roundtrip.ps1`). Codex not tested (no Codex CLI on this PC).
- **P1.5 in progress — 24 / 66 tested** (`docs/workflow-matrix.md`, generated by `tools/sweep_matrix.py`
  from `live_check/sweep/*.json`; sweep = `tests/blender/sweep.py` via `smoke_test.ps1 -Sweep`):
  - **LTX 2.5** 7/7 ok (VRAM 29.6–30.1 GB; deblur-upscale needed the clay MP4 sound track, fixed).
  - **Flux 2** 8/8 ok (Klein 17.3–20.4 GB, Dev 29.5–30.2 GB; inpaint mask convention verified visually).
  - **Qwen Image 2.1** 5/5 ok (14.6–23.4 GB; image edit not black despite Sage).
  - **Krea 2** 4/4 run (17.3–21.9 GB); the character sheet echoed its input (cubes scene, no character):
    retest with a character image.
  - ComfyQ has **no admin password** and `/admin/activate-workflow` is not admin-gated even with one: any
    LAN PC can switch models. Fix = admin password (P1.0) + ask b2renger to gate it (docs/AGENTS.md).
  - ComfyQ bugs for b2renger: calibration of LTX union and Qwen control depth fails — benchmark inputs
    `comfyq__bench-vi__sports_car.png` / `comfyq__bench-im__sports_car.png` missing in ComfyUI/input.
  - Method (user-approved per family): `--switch=<ids> --restore=video_edit_ltx2_5_iclora_canny_control`
    (calibrate + run each as the main workflow, canny restored at the end). Lanes side by side don't fit.
- **Preset library run (2026-10-04, docs/preset-run.md): 46 / 48 run** on the user's `E:\zaki\test1_AI.blend`
  (default cube, nothing animated) through the MCP with Solo on (`tools/preset_run.py`, resumable;
  assets in `live_check/presets/assets`, contact sheets `tools/contact_sheet.py` with ComfyUI's python).
  ~92 min GPU, ~30 s per model switch. 2 ❌ = Wan 2.1 ID-V2V ×2: ComfyUI `WanImageToVideo … 'ref_pad_image'`
  (b2renger). Fixed during the run: `image_pass` builds the upload from pixels (image.copy() of a file
  image had no pixels), `blender_graph_set` loads a file into an Image node. Overall 47/66 workflows run
  from Blender; 17 never (3D, upscalers, SeedVR2 video, Marigold, SAM3, interpolation, Gemma, audio).
- **Image → 3D verified (2026-10-04, MCP):** Flux2 Klein "supercar on white" → preset image_3d → TRELLIS2
  (`3d_trellis2_image_to_textured_mesh`, ~6 min, 461k faces, 2×2048 textures) → Import node → scene. Models
  came in at the origin at ~1 m (inside the cube) → **placement** added: importers/media.py `Placement`
  (BESIDE default / CURSOR / ORIGIN / ASIS, on_ground, size) on the Import node + MCP `blender_import`,
  new MCP `blender_transform`, then `blender_world` (HDRI file or Blender's builtin:<name>, strength,
  rotation, background, clear; named nodes updated in place) and `blender_shadow_catcher` (Cycles flag +
  EEVEE material with separate Cycles/EEVEE outputs — Shader to RGB is black in Cycles; EEVEE needs `eevee_lit`
  ≈ lit-ground brightness, ~1.5 under HDRI+sun) → 20 tools.
- **Cycles source (2026-10-04):** Scene Render style `CYCLES` (+ samples) / MCP `blender_render style=CYCLES`:
  `render.cycles_pass` saves a temp copy (`save_as_mainfile(copy=True)`) and renders it in a separate
  `blender -b --factory-startup` (OptiX→CUDA→…→CPU), so the GPU is free when it exits (checked with
  nvidia-smi: VRAM back to the pre-render level). Fingerprint for CYCLES adds materials, lights, shadow
  catchers and the world/HDRI nodes. Blocks Blender while rendering. First AI render from Cycles: the
  user's supercar scene → Flux2 Klein image edit → photoreal, same composition (live_check/ai_render/). The user's test1_AI.blend has the car at the origin (old build).
- **Multi-model orchestration page** (for the user and b2renger): https://claude.ai/artifact/C8EPx3vgnJVsRXvZNjB7ug
  (private until the user shares it), local copy `docs/private/multi-model-orchestration.html` (gitignored).
  Proposes: stage runner in the graph (group nodes by model, switch between stages), ComfyQ bundles for
  fixed recipes, server-side chains via ComfyQ's `chainedInputs`, routing across several servers.
- **Other:** Blender theme = Mixar Forest (calmer Input-node colour). P1.0 hardening postponed by the user.
  Phase 2 not started (ComfyQ REST/SSE/MCP, direct-control bundles, quotas, `outputs` in meta.json).
- **Known gaps:** the dialog doesn't live-redraw job progress; job cards can't be removed from the list;
  "image workflow → V2V start image" never run live in the graph.

## Next steps
00. State at the end of 2026-10-04 (all committed, `0537d86`): ComfyQ serves `video_edit_wan_idv2v_restyle_hires`
   (main) + `image_qwen_image_2_1_t2i` (lane); Solo is ON in the user's prefs. Hand-over: docs/RUNBOOK.md.
0. **User tests the MCP teacher tools tomorrow (2026-10-05)** from an agent connected to their Blender (choice made:
   "agent drives Blender", not a chat panel inside Blender). Status at stop: Blender 5.2 running with the
   `a3786b2` build, connected, 20 tools answering over HTTP (checked with a direct JSON-RPC call:
   `comfyq_server_status` = LTX canny main, 29.96/32 GB, nothing fits alongside). Claude Code must reconnect
   `blender-comfyq` (`/mcp`) to list the new tools. Every load = how='main' → ComfyQ restarts ~1–2 min for
   everyone: say so first; offer to restore `video_edit_ltx2_5_iclora_canny_control` after. Never verified
   live yet: a real `comfyq_serve` (main or alongside), `comfyq_unserve`, `comfyq_calibrate`, and the bar
   following the switch after ComfyQ's restart (socket auto-reconnect).
1. **Continue P1.5** with the family the user names next. Untested: Wan (animate2, idv2v restyle ×2,
   wan21 scail2, wan22 4 key frames), MiniMax H3 (fun controlnet ×3, v2v reference, i2v 4-step, t2v turbo),
   FastVideo FastH3 (i2v, t2v), LTX 2.3 (3dreal vid2vid, ia2v iclora, ic lora ingredients), LivePortrait,
   Bernini (image edit ×2, video edit ×3), Ideogram 4 t2i, 3D (Pixal3D ×3, Trellis2, TripoSplat), SeedVR2
   (image ×2, video ×2), PID upscale, Marigold depth/normals, SAM3 video segment, frame interpolation,
   Gemma describe image/video, Stable Audio 3. Per family: list ids + inputs, `--switch=… --restore=<canny>`,
   look at results, update CLAUDE.md status, ask to commit. Some need special inputs (audio for ia2v,
   a character image for LivePortrait / Wan animate / character sheets, 4 key frames for wan22).
2. Graph sign-off: the user tries graph mode / outputs / presets and compares with the Moodboard screenshot.
3. Possible builds (from the orchestration page, user's choice): stage runner, bundles with b2renger,
   server-side chains, multi-server routing; a Codex round trip when a Codex CLI exists.
4. After any UI change: `smoke_test.ps1 -Ui` and update docs/UI.md. Install into the user's Blender 5.2
   profile only when asked (command above, not `--factory-startup`). Ask before committing.
