# CLAUDE.md — Mixar ↔ ComfyQ Bridge

A Blender extension (`comfyq_bridge`) that runs inside **Mixar** (a Blender 5.2 fork) and plain
**Blender 5.x**. It renders scene passes (clay stills, masks, the clay *animation*), sends them to
**ComfyQ** workflows (a multi-user ComfyUI scheduler), and brings results back into the scene.
Main use: **AI rendering** of a Mixar layout/animation (ControlNet, image edit, video-to-video), not Cycles.
Used in a **class on a local network**.

Approved plan: `C:\Users\Ateliernum\.claude\plans\we-are-planning-a-effervescent-widget.md`.
Git: repo initialised, **nothing committed yet** (ask the user before committing).

## Environment (this machine)
| Thing | Where |
|---|---|
| Mixar 4.2.0 (Blender 5.2, Python 3.13.13) | `C:\Program Files\Mixar\mixar.exe`; user profile `%APPDATA%\Mixar\Mixar\5.2` (extensions in `extensions\user_default\comfyq_bridge`) |
| Blender 5.1 (Python 3.13.9) | `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` |
| ComfyQ server (Node, Socket.IO, SQLite) | `F:\ComfyQ`, http://127.0.0.1:3000 — user works with b2renger, full control of its code |
| ComfyUI 0.37, torch 2.8 cu128, RTX 5090 32 GB | `F:\ComfyUI_windows_portable_nvidia\…`, :8188 (listens on 0.0.0.0 — P1.0 issue) |
| Ollama | 127.0.0.1:11434 |
| Bridge cache | `%LOCALAPPDATA%\comfyq-bridge\cache\` — `passes\` (temporary renders), `<job8>\` (downloaded results) |
| User's test project | `Desktop\testV2V_clean.mixar` (cleaned copy, use this); `testV2V.mixar` crashes Mixar at launch (see Gotchas) |

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
  ui/panel.py            panels: View3D sidebar, Image Editor sidebar, Queue popover, dialog (Ctrl+Shift+G), header buttons
  ui/player.py           video player for the ComfyQ window (independent of the scene timeline)
tests/unit/              pytest, no bpy (params, match, models/profile, submit)
tests/contract/          against the live ComfyQ (read-only + a no-GPU booking 1 day ahead, then deleted)
tests/blender/smoke.py   headless inside Mixar/Blender (run via tools/smoke_test.ps1)
tests/fixtures/cube.glb  committed fixture (Mixar's glTF exporter crashes at exit in -b)
tools/smoke_test.ps1     build zip -> install into an ISOLATED profile -> run smoke (-Book: one real GPU job)
tools/comfyq.ps1         client CLI with Mixar's Python: doctor | workflows | params | submit | fetch
tools/live_check.py      run from Mixar's Text Editor on the user's open scene; writes live_check/report.json + window.png
tools/open_and_send.py   `mixar <file> --python tools/open_and_send.py -- [--no-send] [--cleanup]`
tools/late_open.py       start Mixar THEN open a file (workaround for the launch crash), then runs open_and_send
```

## Commands
```powershell
.\.venv\Scripts\python -m pytest -q tests\unit tests\contract     # unit + live contract (ComfyQ must run)
.\.venv\Scripts\ruff check extension tests tools
.\tools\smoke_test.ps1                  # Mixar, isolated profile, no GPU   (also: -App blender, -Book)
.\tools\comfyq.ps1 doctor               # classroom health check
# install the build into the USER's Mixar profile (only when asked):
& "C:\Program Files\Mixar\mixar.exe" -b --factory-startup --online-mode --command extension install-file -r user_default -e dist\comfyq_bridge-0.1.0.zip
# open the user's project safely (launch crash workaround):
& "C:\Program Files\Mixar\mixar.exe" --online-mode --python tools\late_open.py -- C:\Users\Ateliernum\Desktop\testV2V_clean.mixar --no-send
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
10. A new window has no laid-out areas until drawn → window setup runs step by step from a timer;
    `active_panel_category` must be retried until it reads back.
11. Mixar opens extra windows (agent bubble): identify our window by pointer, not "last window".
12. Mixar's glTF *exporter* crashes Mixar at exit in background mode (import is fine).
13. Background mode: `bpy.app.timers` don't run → tests pump `SESSION.pump()` themselves; `open_mainfile`
    drops non-persistent timers (use `persistent=True`).
14. PowerShell `Set-Content -Encoding utf8` writes a BOM → breaks `compile()`; edit Python files with the
    Edit tool or Bash, never PS Set-Content.
15. Float props are single precision: compare with tolerance (`_same()` in studio.py).
16. Visible GUI tests: run Mixar with the sandbox env vars + a script that screenshots
    (`bpy.ops.screen.screenshot`) and quits; tell the user a window will flash.

## Status
- **Done:** P1.1 client + Socket.IO transport + CLI; P1.2 extension MVP; P1.3 imports (image/video/GLB/PLY/
  audio), reattach after reload, per-workflow settings; animation → V2V (clay MP4, always re-rendered at
  Generate, scene/workflow matching, start/last frame); AI-keyframe chain (needs both workflows served —
  **never tested end-to-end on the server**); Mixar-style bar (sidebar + dialog + detached window with
  Image Editor/player/Text Editor); multi-line prompts (Text blocks); player; job cards (Sent inputs,
  Show/Open/Save as…/Import, Download); grayscale clay; Clean up; prompt also sent as job prompt.
- **Verified live:** job `78193cf8` on the user's scene — prompt + clay video traced through ComfyQ and
  ComfyUI (run `ecad3950`).
- **Not done:** P1.0 (server hardening — user postponed), P1.4 MCP (extension loopback MCP + Claude Code/Codex
  config), P1.5 workflow test sweep (66 workflows, 41 without VRAM measurement), phase 2 (ComfyQ REST/SSE/MCP,
  direct-control bundles, quotas), commit.
- **Known gaps:** dialog doesn't live-redraw job progress; "Sync scene frame" untested on screen;
  `ComfyQ animation pass.NNN` duplicates fixed in code (single datablock) but old files may hold copies.

## Next steps (when the user asks)
1. User checks "Render & play clay animation" == camera preview in `testV2V_clean.mixar`, then Generate.
2. Commit the milestone (ask first).
3. P1.4 MCP, then P1.5 sweep, then phase-2 ComfyQ PRs.
