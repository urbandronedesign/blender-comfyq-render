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
| **Blender 5.2.2 LTS** (Python 3.13.13) — the host | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (winget `BlenderFoundation.Blender`, upgraded from 5.1 on 2026-10-03; the old 5.1 folder is still on disk and runs). Plugin installed in `%APPDATA%\Blender Foundation\Blender\5.2` (N3 build), Allow Online Access on. Theme = Mixar's **"Mixar Forest"** with Input node colour changed from #ff284d to #3d4640 (calmer graph headers) (exported from `%APPDATA%\Mixar\Mixar\5.2\config\mixar_userpref.blend`, preset `scripts\presets\interface_theme\mixar_forest.xml`; previous prefs backed up as `config\userpref.before_mixar_theme.blend`) Default of `smoke_test.ps1` and `comfyq.ps1` |
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
27. Property definitions are evaluated by Blender in its own namespace (string annotations with
    `from __future__ import annotations`): a `lambda` as `update=` cannot see the module's functions
    (NameError at the first change). Use a named function defined before the class.
26. After `screen.area_split` both areas keep stale `x`/`width` until the next draw: decide which part is
    which from a timer (`ops.workflow.set_graph_mode`).

## Status (2026-10-03, last commit `d4f3e42`)
- **Host:** Blender 5.2.2 LTS (user's profile has the latest build, Allow Online Access on). Mixar uninstalled.
- **Done — bar & tools:** P1.1 client + Socket.IO transport + CLI; P1.2 extension MVP; P1.3 imports
  (image/video/GLB/PLY/audio), reattach after reload, per-workflow settings; animation → V2V (clay MP4,
  always re-rendered at Generate, scene/workflow matching, start/last frame); AI-keyframe chain in the bar
  (**never run end-to-end**: needs an image + a video workflow served together); Moodboard-style bar
  (sidebar + Ctrl+Shift+Q dialog + detached ComfyQ window: Image Editor/player/Text Editor); multi-line
  prompts; player; job cards; grayscale clay; Clean up; prompt also sent as the job's prompt; cancelled
  jobs read as Cancelled. Every control documented in **docs/UI.md** with the check that covers it.
- **Done — node graph N0–N2** (docs/plans/node-graph.md §11–13): graph in the Node Editor (bar icon),
  category toolbar, source nodes (Scene Animation / Scene Render / Selection Mask / Image / Video File /
  Audio File / Prompt), workflow nodes built from ComfyQ's parameters (sockets = inputs, chips = settings,
  Change model, "+" sources, multi-line prompts), **running** (Generate per node, Run Graph, chains, reuse
  of up-to-date results via `render.scene_fingerprint`, cancel, results on the node).
- **Done — N3 look** (plan §14, committed + installed; not yet compared with Moodboard by the user): graph mode of the ComfyQ
  window (graph left, viewer + bar right, prompt below; **Graph** switch in the window's bar; the viewer
  follows the selected node), **Open as Graph** (bar › Actions), video thumbnails, no group panels, header
  steps labels → icons → + menu, 🔍 search of all workflows. Headers stay maroon (Blender limit, gotcha 25).
  `smoke_test.ps1 -Ui` 86/86 in Blender 5.2.
- **Verified live:** bar job `78193cf8` traced through ComfyQ + ComfyUI; the user's 5 s project from a
  node (job `f71fdd3a`, user: "it's working"); in Blender 5.2: `smoke_test.ps1 -Ui` 78/78 + live part
  (chain A → B, *Generate again* reuses A, cancel). Headless smoke passes on Blender 5.2 (and passed on
  Mixar 4.2 / Blender 5.1).
- **Done — N4** (plan §15, committed + installed): Viewer / Import to Scene / Save output nodes
  (… when ready, Replace previous import, Generate upstream then act), Reroute + Frame, result history +
  sidebar **ComfyQ** tab (job details, *Use* an earlier result). `smoke_test.ps1 -Ui -Book` green in
  Blender 5.2 (chain A → B → Viewer / Import / Save, Generate on the Viewer books nothing when up to date).
- **Done — N5** (plan §16): class presets (built in by role: render → image, clay → video, AI keyframe →
  video, image → 3D, image → upscale; saved by the teacher as JSON, shared folder via
  `COMFYQ_BRIDGE_PRESETS`), header **Presets** menu, teacher guide **docs/CLASS.md**. `-Ui` 92/92.
- **Not verified live:** image workflow → V2V start image in the graph (no image workflow served).
- **Done — P1.4 MCP** (docs/AGENTS.md): MCP server inside the extension (off by default, preferences;
  127.0.0.1 + token in `%APPDATA%\comfyq-bridge\mcp.json`), 16 tools (`blender_*`, `blender_graph_*`,
  `comfyq_*`), stdio launcher `mcp/launcher.py`, `.mcp.json`, skill `.claude/skills/blender-comfyq-roundtrip`.
  Round trip by **Claude Code** verified (`tools\agent_roundtrip.ps1`, job `86924b46`). Codex not tested.
- **P1.5 started** (docs/workflow-matrix.md, `tests/blender/sweep.py`): LTX 2.5 family done 2026-10-03 —
  canny / pose / union / i2v / t2v / flf2v ok from Blender (VRAM ~30 GB each, 12–33 s with the cheapest
  settings); deblur-upscale ok too since the clay MP4 has a sound track (AAC, scene sound or silence);
  union's calibration fails in ComfyQ (missing bench input `comfyq__bench-vi__sports_car.png`: tell b2renger).
  Two LTX models never fit side by side (32 GB card): test them by switching the main workflow.
  Flux 2 family done 2026-10-03: all 8 ok (t2i Klein/Dev, edit, edit+ref, depth control, inpaint prompt/ref; mask
  convention verified visually). VRAM: Klein 17–20 GB, Dev 29.5–30.2 GB. Results looked at, none black.
  Switching the main workflow for a sweep: done when the user names a family to test (LTX 2.5, Flux 2 so far);
  canny is always restored at the end.
- **Not done:** the rest of P1.5 (51 workflows); Codex / `codex --oss` round trip (needs the Codex CLI);
  P1.0 (server hardening — user postponed); phase 2 (ComfyQ REST/SSE/MCP, direct-control bundles,
  quotas, `outputs` in meta.json).
- **Known gaps:** the dialog doesn't live-redraw job progress; job cards can't be removed from the list;
  videos have no thumbnail in nodes (Blender makes no movie previews).

## Next steps
1. **Graph sign-off (N3–N5 done):** ask the user to try graph mode, output nodes and presets, and to
   compare with their Moodboard screenshot; adjust what they point at.
2. Then (user's choice): P1.5 sweep of the 66 workflows (also fills the VRAM badges agents see), P1.0 server
   hardening, a Codex round trip, phase 2 (a native ComfyQ /mcp replaces the comfyq_* proxy tools).
3. After any UI change: `smoke_test.ps1 -Ui` and update docs/UI.md. Install into the user's Blender 5.2
   profile only when asked, with the command above (not `--factory-startup`). Ask before committing.
