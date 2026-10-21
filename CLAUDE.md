# CLAUDE.md — ComfyQ Render (Blender ↔ ComfyQ)

**ComfyQ Render** is a Blender 5.x extension (id `comfyq_bridge`, kept on purpose: renaming it resets the users'
add-on preferences). It renders scene passes (clay stills, masks, the clay *animation*), sends them to
**ComfyQ** workflows (a multi-user ComfyUI scheduler), and brings results back into the scene.
Main use: **AI rendering** of a Blender layout/animation (ControlNet, image edit, video-to-video), not Cycles.
Used in a **class on a local network**.

**History:** the project started inside Mixar (a Blender fork). Since 2026-10-03 it targets Blender 5.2 LTS only;
renamed from "Mixar ↔ ComfyQ bridge" to **ComfyQ Render** on 2026-10-21 (Mixar tools and `-App mixar` removed).
Gotchas 2, 3, 4, 11, 12 below were Mixar-only (kept for the numbering).

Approved plan: `C:\Users\Ateliernum\.claude\plans\we-are-planning-a-effervescent-widget.md`.
Git: branch `main`, remote `origin` = public GitHub `urbandronedesign/blender-comfyq-render`. Commit author = repo-local `urbandronedesign <urbandronedesign@users.noreply.github.com>`
(never the global identity). Ask the user before committing. **UI reference: `docs/UI.md`** (every button + its check).
**Building a new workflow / pipeline: follow `.claude/skills/comfyq-workflow-build/SKILL.md`** (the user's standing protocol).

## Environment (this machine)
| Thing | Where |
|---|---|
| **Blender 5.2.2 LTS** (Python 3.13.13) — the host | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (winget `BlenderFoundation.Blender`, upgraded from 5.1 on 2026-10-03; the old 5.1 folder is still on disk and runs). Plugin installed in `%APPDATA%\Blender Foundation\Blender\5.2` (build of commit `cb6571b`, 2026-10-21: + 3D model / camera inputs, lane-restore wait), Allow Online Access on, **AI agents (MCP server) ON** (port 8790), **Teacher tools ON** (agents may load models; set 2026-10-04 by a startup script + `save_userpref`). Theme = Mixar's **"Mixar Forest"** with Input node colour changed from #ff284d to #3d4640 (calmer graph headers) (exported from `%APPDATA%\Mixar\Mixar\5.2\config\mixar_userpref.blend`, preset `scripts\presets\interface_theme\mixar_forest.xml`; previous prefs backed up as `config\userpref.before_mixar_theme.blend`) Default of `smoke_test.ps1` and `comfyq.ps1` |
| ComfyQ server (Node, Socket.IO, SQLite) | `F:\ComfyQ`, http://127.0.0.1:3000 — user works with b2renger, full control of its code |
| ComfyUI 0.37, torch 2.8 cu128, RTX 5090 32 GB | `F:\ComfyUI_windows_portable_nvidia\…`, :8188 (listens on 0.0.0.0 — P1.0 issue) |
| Ollama | 127.0.0.1:11434 |
| Bridge cache | `%LOCALAPPDATA%\comfyq-bridge\cache\` — `passes\` (temporary renders), `<job8>\` (downloaded results) |
| User's test project | `Desktop\testV2V_clean.blend` (cleaned copy, use this; same file as the older `testV2V_clean.mixar`, a zstd .blend) (scene 0–120 @ 24 fps, camera, ComfyQ settings intact) |

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
    params.py            meta.json params -> ParamSpec (prompt/text/int/float/seed/bool/enum/camera/image/video/audio/
                         mask/model; ComfyQ `model3d` = kind "model", a mesh file uploaded like media)
    camera.py            ComfyQ `camera` param (JSON {horizontal, vertical, zoom}): parse/clamp, the shot words
                         (port of ComfyQ cameraAngle.js, checked against ComfyUI's 5110-point fixture), from_view
    models.py http.py profile.py doctor.py cli.py
  match.py               scene <-> workflow matching (duration/frames/fps/size/aspect by label), frame_for() start/last frame
  props.py               Scene.comfyq: tab, workflow_id, keyframe_workflow_id, params (per workflow!), jobs (inputs JSON)
  prefs.py               student name, profile, URL override (never tokens)
  runtime/session.py     SESSION: worker pool + main-thread queue (pump), connect, specs cache, JobView, reattach, download
  passes/render.py       Workbench passes: structure (clay = true grayscale), mask (RGBA alpha = repaint area), image
  passes/animation.py    clay animation MP4 (frame range through scene camera), cache + depsgraph staleness
  passes/mesh.py         3D-model inputs: selected objects → one GLB (modifiers applied), size warning (remesh
                         returns an empty mesh at ~80 units), fingerprint for the graph's Scene Objects node
  ops/generate.py        start_job (render inputs -> upload -> book), AI-keyframe chain, cancel/import/open folder
  ops/workflow.py        select workflow, menus, match scene, previews, multi-line prompt (Text), detached ComfyQ window
  ops/results.py         job-card buttons (save copy, import file, download) + Clean up
  ops/connection.py      connect / disconnect / refresh
  importers/media.py     image -> Image Editor; video/audio -> separate "ComfyQ Results" scene; GLB/PLY -> collection
  ui/studio.py           the generation bar (tabs, chips, prompt, scene strip, references, job cards with "Sent" rows)
  ui/panel.py            panels: View3D sidebar, Image Editor sidebar, Queue popover, dialog (Ctrl+Shift+Q), header buttons
  ui/player.py           video player for the ComfyQ window (independent of the scene timeline)
  ui/server.py           teacher's Server panel (lanes/VRAM via a thread every 5 s while open, queue, people, Load Model...)
  help/student.html      students' quickstart, shipped in the zip, opened by ? (comfyq.help); docs/STUDENT.md points to it
  surfaces.py            bpy-free SAM3 surface labels ("wall|left wall: material") → prompt (arch chain)
  passes/segment.py runtime/segmentation.py   SAM3 on stills (3-frame MP4), union masks, overlay, fit_upload
  graph/batch.py         bpy-free: Media Folder scan (recursive, pictures / videos), `<folder>_renders` mirrored
                         paths + batch suffix, resume (`remaining`), ETA. `run.BatchRun` = one `Run` per file
  graph/                 node graph (Moodboard-style, docs/plans/node-graph.md): kinds.py + engine.py (bpy-free),
                         tree.py (nodes), ui.py (toolbar/operators/timer sync), run.py (running the graph),
                         outputs.py (Viewer/Import/Save), presets.py (bpy-free) + preset_ops.py (class presets)
  mcp/                   MCP server for AI agents (P1.4, off by default): server.py (bpy-free JSON-RPC over
                         HTTP, 127.0.0.1 + token), tools.py (blender_* / comfyq_* tools, on_main), launcher.py
                         (stdio launcher agents run; ships with the extension)
tests/unit/              pytest, no bpy (params, match, models/profile, submit)
tests/contract/          against the live ComfyQ (read-only + a no-GPU booking 1 day ahead, then deleted)
tests/blender/smoke.py   headless inside Blender (run via tools/smoke_test.ps1)
tests/blender/ui_check.py VISIBLE Blender: presses every button in its real window/area/region,
                         graph steps, live chain with -Book (smoke_test.ps1 -Ui [-Book])
docs/UI.md               user-facing reference of every control, mapped to the ui_check checks
docs/CLASS.md            teacher guide: class setup, presets, sharing presets
docs/AGENTS.md           AI agents: turning the MCP server on, Claude Code / Codex config, the tools
tests/fixtures/cube.glb  committed fixture (no glTF export at test time)
tools/smoke_test.ps1     build zip -> install into an ISOLATED profile -> run smoke (-Book: one real GPU job)
tools/comfyq.ps1         client CLI with Blender's Python: doctor | workflows | params | submit | fetch
tools/live_check.py      run from Blender's Text Editor on the user's open scene; writes live_check/report.json + window.png
tools/blender_mcp.py     repo wrapper of mcp/launcher.py (used by .mcp.json)
tools/agent_roundtrip.ps1  sandbox Blender + MCP on + `claude -p` doing render → submit → wait → import
tools/graph_send.py      build Scene Animation → served V2V node on a real file, Generate on the node, trace it
tools/make_preset_library.py  headless (sandbox profile): one saved preset per image/video workflow →
                         presets/library/<Image|Image edit|Image → video|Video → video>/*.json (docs/CLASS.md §3)
tools/preset_run.py      run every library preset through the MCP on the open scene (Solo on), resumable
tools/contact_sheet.py   review sheets of results (start/mid/end frames, audio) — ComfyUI's python (cv2, av)
tools/preset_shots.py    visible sandbox Blender: each preset's graph rebuilt with its run result → screenshots
tools/workflow_check.ps1 run any workflow on the GPU with the plugin's client, no Blender (serve, book, download;
                         `next` = another job on the same model, `key=@file` uploads); switches models
tools/text3d_check.ps1   GPU check of the Text → 3D preset chain (uses workflow_check)
tools/museum_blockout.py blockout museum + 4 turnaround views by the plugin's clay pass (the BUILDING turns, camera
                         and light stay: identical lighting; shadows + cavity on) → AI restyle → Pixal3D Multi-View
tools/make_batch_preset.py  headless: builds the Batch class preset (Media Folder → Klein edit, seed 7 → Save _render)
tools/make_views_preset.py  headless: builds the Four views class preset (4 Image → 4 Klein edits, one Prompt,
                         seed 7, auto Save) from ComfyQ's live Klein parameters
tools/splat_render.py    load a 3DGS .ply (no splat support in Blender 5.2) as a coloured point cloud and render it
                         with Cycles (colour = 0.5 + 0.2821·f_dc, radius = exp(scale), opacity = sigmoid, emission)
tools/preset_report.py   HTML report of a run (description, graph shot, inputs, prompt/settings sent, output,
                         verdict from live_check/presets/notes.json) → baselines/<date>/ (gitignored);
                         --baseline <dir> shows the earlier output side by side. Baseline: baselines/2026-10-04
presets/library/         the generated workflow library (46 presets since ComfyQ be109b3, committed; copied to the user's presets folder)
```

## Commands
```powershell
.\.venv\Scripts\python -m pytest -q tests\unit tests\contract     # unit + live contract (ComfyQ must run)
.\.venv\Scripts\ruff check extension tests tools
.\tools\smoke_test.ps1                  # Blender 5.2, isolated profile, no GPU   (also: -Book)
.\tools\smoke_test.ps1 -Ui [-Book]      # VISIBLE Blender (~3 min, tell the user): every button on a copy of
                                        # testV2V_clean.blend -> live_check\ui\report.json + screenshots
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
`dist\comfyq_bridge-0.1.0.zip` is rebuilt by every smoke run. Installing it into a running Blender does not
change that session: the user must reinstall from the UI or restart Blender.

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
16. Visible GUI tests: run Blender with the sandbox env vars + a script that screenshots
    (`bpy.ops.screen.screenshot`) and quits; tell the user a window will flash. `smoke_test.ps1 -Ui` does it.
    Screenshots may show the user's account email (it was in Mixar's top bar): never commit or publish them.
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
    animated camera, mask recolouring) and the UI (Mixar re-tagged objects with *shading* updates while
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
31. **ComfyQ fills an unset textarea with the job prompt**: `book_job` copies the top-level `prompt` into the first unset
    textarea with field `text` — for Primitive-prompt workflows (Flux2 Klein t2i) the NEGATIVE prompt. `client.submit`
    now sends every prompt-kind param explicitly (default when unset). Check job params (`GET /jobs/:id`) when a
    result ignores its prompt.
32. **Smoke tests run against the live ComfyQ, whose served set changes** (solo mode, presets). Never assume the
    served workflow (pick one with the needed params) and never call a teacher tool on a side lane: a smoke
    run once really stopped a lane (`comfyq_unserve` on a non-main workflow). They only touch the main lane now.
33. **`image.copy()` of a file image can have no pixels** (loaded by a script / MCP / from a saved .blend):
    saving it fails "does not have any image data". `image_pass` builds a new image from `pixels`.
34. Blender resolves relative script arguments its own way (`screenshot_area` wrote nowhere): pass absolute
    paths. In PowerShell, a non-ASCII argument (→) got the whole command refused: keep CLI args ASCII.
35. **Shader to RGB is EEVEE-only (black in Cycles)**: a material using it needs a separate Cycles output
    (`ShaderNodeOutputMaterial.target`). Cycles lights objects through their material even as shadow catchers.

36. **ComfyQ's SAM3 is video-only and returns ONE surface per phrase** ("wall" = the back wall only; threshold
    0.3 did not help). Stills go through a 3-frame MP4; labels are phrase groups `"wall|left wall|right wall"`
    (union mask). Qwen 2.1 outputs 1920×1088, over the upload box: `segment.fit_upload` shrinks to 1904×1080.
37. **The sandbox Blender reads the REAL profiles file** (`%APPDATA%\comfyq-bridge\profiles.toml`, not under
    BLENDER_USER_RESOURCES): on the teacher PC the smoke test is "unlocked". Student-PC checks set
    `COMFYQ_BRIDGE_CONFIG` to a missing file (and restore it); `prefs.teacher_unlocked` caches on path + mtime + env.
38. **Preferences can only be drawn by Blender** (a proxy object as `prop()` data raises "expected AnyType"): the GUI
    check opens the real Preferences window (`screen.userpref_show`, `preferences.addon_expand`) and screenshots it.
    `layout.panel(idname)` gives collapsible sections in panels and preferences, not in operator popups (the
    Ctrl+Shift+Q dialog draws plain blocks: `studio._section(dialog=True)`).
39. **Greyed-out buttons must say why**: `cls.poll_message_set(...)` in every poll that can fail (Blender HIG);
    `invoke_confirm(title=, message=, confirm_text=, icon=)` for anything that deletes files or switches models.
40. **Bash heredocs mangle backslashes**: a doubled backslash reaches Python as a single one, so `\a` becomes BEL,
    `\t` a tab, and `\` + newline joins lines. Write code with the Write/Edit tools, or build paths with `chr(92)`.
    In PowerShell run `Set-Location <repo>` first: after a `cd` into a subfolder, `.\tools\…` fails.
    Even a quoted heredoc (`<<'EOF'`) halved `\\` in this Bash tool (2026-10-04, three times): write edit scripts
    to the scratchpad with the Write tool, then run them.
41. **A render started from Python logs no progress** (`bpy.ops.render.render` in `-b`: no "Sample n/m" lines, unlike
    `-f`). The Cycles child script prints them from a `bpy.app.handlers.render_stats` handler. Pipe output arrives
    live; `poll()` joins the reader thread at exit so the last counts / error lines are complete.
42. **GUI checks: an open popup covers every later screenshot** (`wm.call_menu` / `call_panel` stay open in the
    scripted run). Open menus and popovers in the LAST steps (`step_server_panel`, `step_presets_menu`).
    `node.view_selected` needs `temp_override(window, area, region=<WINDOW region>)`, not just the area.
43. **Look at the screenshots, not only the check results**: the GUI run passed while the presets' How to use note
    cut its last step (frame too small) and a menu covered a shot. Frames show a Text block (`NodeFrame.text`) but
    wrap long lines: keep note lines under ~45 characters, size `560 × (70 + 30 × lines)`.
44. **Points made by Geometry Nodes do not inherit the object's material**: Cycles draws them with its default grey
    (seen with splats as a point cloud). Add a *Set Material* node after *Mesh to Points*. `Object.evaluated_geometry()`
    shows the point cloud (the evaluated object still reports its empty mesh).
45. **Workbench orthographic views of the 4 sides of an object**: turning the camera and the light gives each side a
    different shade (studio light and shadow direction follow different rules). Turn the OBJECT (0/90/180/270°) under
    a fixed camera and light. Straight-on faces all get one grey: enable shadows + cavity so recesses read.
46. **Pass file names were the time to the second + kind**: a graph that prepares several inputs in one tick (four
    Image nodes, two references) wrote them to ONE file, so a job got another view's picture (the Four views preset
    rendered the left view as the front, 2026-10-06). `render._out_path` adds a counter; smoke checks two passes.


## Status (2026-10-06, last commit `f5b79ae`, installed build `5c70c06`; ComfyQ pulled to `be109b3`) — **hand-over for b2renger / the teacher: docs/RUNBOOK.md**
- **Host:** Blender 5.2.2 LTS; the user's profile has build `5c70c06` (MCP ON, Teacher tools ON, **Solo ON**).
  **This PC is the teacher PC**: `%APPDATA%\comfyq-bridge\profiles.toml` = profile `teacher` (default,
  127.0.0.1:3000, random `admin_password`; ComfyQ itself has no admin password yet, so any value works). Without it
  Solo / Load Model / the agents' teacher tools are locked (UX phase A). Never copy that file to student PCs.
  **ComfyQ serves whatever solo mode / the last preset loaded** (at stop 2026-10-06: main
  `utility_video_frame_interpolation`, lanes `image_edit_flux2_klein_9b_image_edit` + `utility_video_segment_sam3`)
  — no fixed "state to restore" (user's choice: the next preset decides; before class, serve the lesson's model).
  **Never touch the ComfyQ repo** (F:\ComfyQ; its `git stash` from before the pull stays). We may download model
  files into ComfyUI's `models\` when the user agrees (done once for 5 LoRAs, 2026-10-05).
- **Bar & tools (P1.1–P1.3):** client + Socket.IO transport + CLI; extension (bar: sidebar / Ctrl+Shift+Q dialog /
  detached ComfyQ window); passes (clay/matcap/material stills, masks, clay MP4 with sound); imports (image →
  Image Editor, video/audio → "ComfyQ Results" scene, GLB/PLY → collection **placed beside the scene, on the
  ground, sized**); reattach; per-workflow settings; AI-keyframe chain (**never run end-to-end**). docs/UI.md.
- **Node graph N0–N5** (docs/plans/node-graph.md): sources (Scene Render — Clay / Matcap / Material /
  **Cycles** —, Mask, Scene Animation, files, Prompt), workflow nodes from ComfyQ params, chains/reuse/cancel,
  outputs (Viewer / Import with Place·On ground·Size / Save), presets (5 built-in roles; saved JSON; subfolders =
  submenus). **Library** `presets/library/` (46 generated, one per t2i / image-edit / i2v / video-edit workflow,
  `tools/make_preset_library.py`); **class presets** `presets/class/` (5: Cycles render → photoreal (Klein); Text →
  3D model (Klein + **Pixal3D**, TRELLIS2 was removed from ComfyQ); Architecture (Cycles → SAM3 → Qwen edit →
  SeedVR2 4K); **Four views** (4 Image → 4 Klein edits, one seed); **Batch: folder → rendering** (Media Folder →
  Klein edit → Save)), all copied to the user's `%APPDATA%\comfyq-bridge\presets\{Image,…,Class}`.
- **Batch renders** (2026-10-06): **Media Folder** node (`graph/tree.py ComfyQFolderNode`; folder, Include
  Subfolders, Image / Video outputs, counts, progress + ✕) → `run.BatchRun` (one `Run` per file, one ComfyQ job at a
  time, a failed file is noted and the batch goes on) → Save **Batch Suffix** (`outputs.save_batch`) into
  `<folder>_renders` with the same subfolders; files with a result are skipped (resume); `graph/batch.py` (bpy-free:
  scan, paths, resume, ETA; `X_renders` next to `X` is never read). Agents: `blender_graph_set` folder / recursive /
  batch_suffix, `blender_graph_status.batch`.
- **Solo mode** (pref, off by default, never in class): Run Graph / `blender_graph_run` loads each node's model
  (`runtime/serving.py`: alongside if it fits else main = ComfyQ restart; only when nothing is booked and no one
  has an unfinished job; last model stays loaded). Verified live many times (~30 s per switch).
- **Cycles source:** `render.cycles_pass` renders a temp copy in a separate `blender -b --factory-startup`
  (OptiX→CUDA→…→CPU) so the GPU is free again before the ComfyQ job (nvidia-smi checked). **Non-blocking since UX F1**
  (`render.CyclesRender`: Popen + reader thread; the child prints "Sample n/m" from a `render_stats` handler because a
  render started from Python logs no progress; graph runs poll it via `run.Rendering`, MCP waits off the main thread,
  ✕ = `comfyq.cancel_cycles`; `cycles_pass` = the blocking wrapper). Stills only. Fingerprint adds materials, lights, shadow catchers, world/HDRI.
- **MCP (P1.4, docs/AGENTS.md): 23 tools + 3 teacher tools** (new: blender_segment / task_status / mask_image). blender_scene_info/select/render (style CYCLES +
  samples)/import (place/on_ground/size)/transform/world (HDRI file or builtin:<name>)/shadow_catcher (Cycles flag
  + EEVEE material, `eevee_lit`)/graph_describe/add_preset/set (incl. Image node `image`)/run/status;
  comfyq_list/describe/submit/job_status/wait/cancel/outputs/server_status; teacher: comfyq_serve/unserve/
  calibrate. Claude Code round trip verified; Codex not tested.
- **Tests run (2026-10-06):** unit+contract 85 pass (2 skipped); headless smoke passes (incl. solo with faked switch,
  placement, world, shadow catcher, Cycles still, F1–F5, shipped presets use existing workflows, Four views + Batch
  presets, batch with faked jobs, two passes in one second); GUI `-Ui` 104/104 (screenshots looked at).
- **Workflow coverage (ComfyQ `be109b3`, 61 workflows, pulled 2026-10-05):** **56/61 run** — library
  presets 43 run + 2 failing (❌ Wan 2.1 ID-V2V ×2 = ComfyUI `WanImageToVideo … 'ref_pad_image'`, docs/preset-run.md),
  LTX deblur-upscale (sweep), SAM3 + PiD + SeedVR2 7B image + SeedVR2 4K (arch chain), **Pixal3D/TRELLIS.2** (Text → 3D
  chain, 2026-10-05, `tools/text3d_check.ps1`: Klein 35 s → Pixal3D 220 s, 694k faces, PBR 4096², complete car
  with an invented back; renders in `live_check/text3d/`), **Stable Audio 3** (2026-10-05, `tools/workflow_check.ps1`:
  20 s stereo MP3 in 48 s, healthy level, fades out; not listened to), **Qwen 2.1 t2i Fix** (works; changes the
  composition rather than fixing it: straight-on, crisper text vs the plain model's cinematic angle, same seed) and
  **Qwen 2511 unblur** (works, but returns ~1 MP (1392×752 from 1904×1080) and softer: NOT an upscaler for our
  renders, trained for blurred faces; SeedVR2 4K stays). Comparisons in `live_check/never_run/compare_*.jpg`.
  Their 5 LoRAs were downloaded by us (user's OK, 2026-10-05) from their public Hugging Face repos into ComfyUI's
  `models\loras` (size + safetensors header checked). **SeedVR2 HD video** (848×480 5 s AI clip → 1908×1080 in 183 s,
  sound kept, sharp detail, frame-to-frame change +11 % = no flicker; its "longest edge" setting is really the
  SHORTEST edge, like the 4K one before b2renger's fix) and **FILM frame interpolation** (2× real time: 48 fps in
  29 s, sound kept, clean in-betweens; 4× slow motion: 20 s of video but the sound stays 5 s, as the workflow
  warns), `live_check/never_run/video/`. **Pixal3D Multi-View** (189 s) and **TripoSplat** (47 s: .glb 4.9M faces with
  vertex colours + .ply 1,048,576 Gaussian splats + .spz) run, but on a Herzog & de Meuron-style museum made from a
  FLAT FRONT ELEVATION both failed as buildings: Qwen "character camera angle" cannot turn an elevation (left / back /
  right came out as more elevations), so Multi-View built an L-shaped roofless shell; TripoSplat kept the white
  backdrop as a room around a hollow roofless box. Lesson: 3D needs a 3/4 view (two façades + roof) or views that
  really agree (e.g. 4 renders of a Blender blockout). `live_check/never_run/multiview/`. Then plan A (2026-10-06,
  `tools/museum_blockout.py`): 4 clay views of a blockout → Klein edit (same prompt + seed: 4 consistent copper views,
  windows kept) → Multi-View (256 s): a CLOSED building with roof, windows in place, glazed ground floor — but the plan
  came out square with chamfered corners (each view is framed separately, so 36 m vs 20 m is lost), plinth missing,
  copper drifted olive. `live_check/never_run/museum/`. **Splats in Blender:** `tools/splat_render.py` renders
  TripoSplat's 1M-Gaussian .ply in Cycles at ~1.3 s / view (64 samples, 900²) with true colours; the plugin's PLY
  import is still plain `wm.ply_import` (bare vertices, no colour): worth replacing. **Never run (3):** SeedVR2
  3B int8 video, Gemma ×2. Removed upstream on 2026-10-05: TRELLIS2, Pixal3D image→mesh, Flux2 Dev t2i + edit, Qwen Viggle
  Turbo, Marigold depth + normals. Earlier per-family sweep: docs/workflow-matrix.md (24/66, historical).
- **Visual baseline:** `baselines/2026-10-04/index.html` (local, gitignored, ~120 MB): per workflow description,
  Blender graph screenshot, inputs uploaded, prompt + settings sent, output (images, videos, GLB views),
  verdict (`live_check/presets/notes.json`); sections Image / Image edit / Image → video / Video → video /
  **Chains**. New run → compare: `preset_run.py` → `preset_shots.py` → `preset_report.py baselines/<date>
  --baseline baselines/2026-10-04`.
- **For b2renger (RUNBOOK §7.1):** no admin password + `activate-workflow` ungated; **job prompt copied into the
  negative prompt** (book_job fills the first unset textarea with field "text"; bridge work-around in
  `client.submit`); Wan ID-V2V broken in ComfyUI 0.37; calibration bench images missing; names ≠ ids (LTX
  "depth" = union, MiniMax "pose" = union); meta.json should declare size/duration params, output kind,
  example prompts; phase-2 API (served flag, SSE, REST, /mcp), direct-control bundles, quotas, job_deps.
- **User's scene** `E:\zaki\test1_AI.blend`: supercar (TRELLIS2 GLB, `world.001` + `geometry_0.001`) on the
  driveway HDRI (`Desktop\tree_lined_driveway_8k.hdr` via blender_world), user's Plane = shadow catcher, an Area
  light; old car hidden at the origin (`world`/`geometry_0`); graph holds ~60 preset groups from the tests.
  AI renders in `live_check/ai_render/` (Cycles + Klein photoreal).
- **Architecture chain (docs/arch-render.md, 2026-10-04):** blocked-out room (`tools/make_arch_scene.py` →
  `E:\zaki\arch_room.blend`) → Cycles → SAM3 surfaces (`blender_segment`, `surfaces.py`, `passes/segment.py`,
  `runtime/segmentation.py`) → labelled prompt → **Qwen edit** (best of Klein edit / Qwen depth / Qwen edit) →
  Klein inpaint of one surface (`blender_mask_image`) → **SeedVR2 4K ×2** (user rejected PiD ×4: squiggle pattern; SeedVR2 int8: grid; Qwen depth at 4 MP: blurred, layout lost). **Graph node `Surfaces (SAM3)`** (`graph/tree.py ComfyQSurfacesNode`, booked by `run._book_surfaces` as a
  background task; Prompt output = found surfaces + the list's materials, masks reused on material edits; full
  preset run verified headless 388 s). Driver
  `tools/arch_render.py` (steps); class preset *Architecture: Cycles → photoreal → 4K upscale (Qwen edit + SeedVR2)*. Not in the user's
  Blender yet (sandbox only) until installed.
- **Other:** theme Mixar Forest; multi-model orchestration page https://claude.ai/artifact/C8EPx3vgnJVsRXvZNjB7ug
  (local copy docs/private/); P1.0 hardening postponed; phase 2 not started.
- **Known gaps:** dialog doesn't live-redraw job progress; EEVEE shadow catcher needs `eevee_lit` tuning;
  `blender_graph_describe` output is huge on big graphs (the user's graph: 136k chars); batch is sequential only (no
  "jobs at once": one result per node); the plugin imports a splat `.ply` as bare vertices (no colour: see
  `tools/splat_render.py`); 4 views → Pixal3D Multi-View loses the building's proportions (the user stopped that
  route: "it's not working", 2026-10-06).

- **UX/UI audit (2026-10-04):** phases A, B, C, E, D DONE and installed (`e968ba9`, `6ad752c`, `d057b3e`, `8040216`,
  `aa8c133`) and **F** (`76a8543`). Plan `docs/ux-audit-plan.md` (phases A→B→C→E→D→F, decisions answered), skill
  `.claude/skills/blender-ui-audit`. **Phase A done**: teacher controls (Solo, Teacher tools, Load Model) locked
  unless the profile holds ComfyQ's admin password (`prefs.teacher_unlocked`); model switches ask first; student
  name asked at the first Connect (`name_confirmed`), "name · server" row; Clean Up asks first; GUI -Ui 91/91.
  **Phase B done**: `poll_message_set` reasons; Generate greyed + reason when not served (`why_not_generate`);
  queue place `client/queue.py`; job card Remove; graph Stop (`Run.stop`, cancels SAM3 jobs too); model-load timer;
  plain connection errors. **Phase C done**: Title Case labels, "..." only on dialogs, no "you"/abbreviations
  (`wording.count`), verb-first tooltips, one icon one meaning (table in docs/UI.md), Copy Agent Command, model
  tooltips = ComfyQ descriptions. **Phase E done**: Player draw no longer loads a MovieClip (`peek_meta`,
  timer reads it); per-draw parsing cached; header/menu draw functions appended once (`ui.append_once`).
  **Phase D done**: optional inputs collapsed (`shown_optional`, "Add Reference Image (N Free)"); graph mode =
  viewer's Selected Node panel (`graph_mode_tree`); presets wrapped in a Frame, Run Graph = selection (`graph_targets`)
  or asks "Run N Chains?"; preferences in sections + Profile menu + Test Connection; bar in collapsible sections;
  Ctrl+Shift+Q in the Node Editor. GUI -Ui 97/97.
  **Phase F done (2026-10-04, commit `76a8543`, installed):** F5 **Retry** on failed / cancelled cards
  (`rec.values` stored by `generate.book`, `why_not_retry`, `comfyq.retry`); F4 **Shared Presets Folder** preference +
  profile `presets_dir` (`prefs.shared_presets_dirs`, `preset_ops.preset_folders`; menu sections *Saved on This PC* /
  *Shared*, Open Shared Presets Folder); F1 **non-blocking Cycles** (see Cycles source); F3 **student help**
  (`help/student.html` shipped in the extension, **?** = `comfyq.help` in the bar and graph header, docs/STUDENT.md;
  *How to use* Frames with a Text block on the 5 built-in presets (`Preset.steps`) and the 3 class presets — saved
  presets keep frame text); F2 **Server (Teacher)** sidebar panel (`ui/server.py`: GPU memory, served models, queue,
  connected people from `connected_users` in ComfyQ's state, **Load Model...** → `graph_load_model` confirm, whose poll
  is now "teacher's PC" instead of "Solo on") + profile **`class_mode = true`** (teacher controls off even with an admin
  password). `prefs.active_profile()` is the one cached profile read. GUI -Ui 103/103.

## Work of 2026-10-05 / 06 (done, for context)
- ComfyQ pulled to `be109b3` (61 workflows; 7 removed, 2 added); plugin followed (`d0e8da4`): library regenerated (46),
  Text → 3D on Pixal3D, upscale role prefers SeedVR2 4K, smoke checks shipped presets against ComfyQ's workflows.
- Never-run workflows tested (`tools/workflow_check.ps1`, no Blender): Pixal3D/TRELLIS.2, Stable Audio, Qwen 2.1 Fix
  (frontal compositions: good for façades, worse for mood; `live_check/never_run/compare_qwen_fix_*.jpg`), Qwen 2511
  unblur (not an upscaler for renders), SeedVR2 HD video, FILM, Pixal3D Multi-View, TripoSplat → **56/61 run**.
- 3D from a building: front elevation (failed), Blender blockout → 4 clay views → Klein → Multi-View (closed but
  square). Gaussian splats render in Cycles (`tools/splat_render.py`). Four views preset; pass-name bug fixed
  (gotcha 46); batch renders from a folder.

## Work of 2026-10-21 (commit `cb6571b`, pushed, installed in the user's Blender 5.2)
- Folder renamed MixarAI → ComfyQRender: `.venv` rebuilt (it pointed at the old path), `requirements-dev.txt` added (`685d4cf`).
- ComfyQ is now 68 commits past `be109b3` (65 workflows). New param types supported: **`model3d`** (Remesh & decimate,
  UV unwrap: input source *Selected Objects* / *File*; graph nodes **Scene Objects** (collection or selection) and
  **3D Model File**; MCP `blender_render kind=objects`) and **`camera`** (Qwen 2511 reshoot from another angle:
  Around / Height / Zoom + the shot words + **From 3D View** = the 3D Viewport's view around the selection,
  measured from the scene camera). Library: the two removed Qwen camera-angle presets replaced by the 2511 one.
  Verified: unit + contract 93, smoke PASSED, GUI -Ui 110/110 (screenshots looked at). **GPU (2026-10-21,
  `tools/new_inputs_check.py`, headless sandbox, Solo):** Qwen 2511 from a clay Suzanne, Around 90 → a true profile
  (87 s incl. model switch), 180 → the back (12 s); graph Scene Objects → Remesh (15,744 → 1,999 faces) → UV unwrap
  (UVs + layout + AO PNGs) in 24 s. Results in `live_check/new_inputs*/`. **Rerun after the lane-restore wait
  (22:25, `live_check/new_inputs_rerun/`): all 3 pass first time** (mesh chain 85 s incl. loading both models,
  2,000 faces + UVs; Qwen 90 → profile 51 s, 180 → back 12 s); ComfyQ then put back to Klein t2i + Klein edit lane.
- **ComfyQ now has an admin password** (set by the user in its admin page): lanes, model downloads etc. need it;
  `activate-workflow` (main switch) is still ungated. The teacher profile's `admin_password` was updated by the user
  (checked with `POST /admin/verify-password`, never printed). Restart Blender to pick up a changed profile.
- **ComfyQ lane-port race (for b2renger), reproduced 2026-10-21 with `tools/lane_race_check.py`:** after a restart
  `restoreRemembered()` reopens the remembered lanes in the background (not awaited, ~15 s per lane), and
  `LaneManager._nextPort()` only counts lanes already in `this.lanes` (a lane registers after its ComfyUI started).
  A lane opened in that window (Solo / Load Model right after a main switch) gets the restoring lane's port:
  A (opened after the restore) → 8190, job ok 31 s; B (opened during it) → both lanes on 8189, one process, job
  `comfyui-process-exited` in 7 s. Fix on their side: reserve the port before `await worker.start()` (or count
  lanes being opened). `live_check/lane_race/report.json`. **Bridge workaround done:** `runtime/serving.choose()` and `serve(alongside)`
  first wait (up to 5 min, worker thread) until every lane in `GET /admin/config` `workflows.extraLaneWorkflowIds`
  is in `GET /admin/lanes` (`serving.restoring` / `wait_restored`) — also keeps the fit check honest. Live proof
  (`tools/lane_race_fix_check.py`): opened right after a restart, it waited 10 s, got 8190, job ok in 26 s.
- Library still lacks the other new ComfyQ workflows (FLUX.3 Klein edit ×2, Qwen 2.1 relight, …): run
  `make_preset_library.py` per category when the user asks for the sync.

## Next steps
1. Wait for the user's feedback on the **Batch** and **Four views** presets in their Blender (installed `5c70c06`).
2. Candidates the user has not chosen yet: a plugin splat importer (coloured point cloud, as `tools/splat_render.py`);
   "jobs at once" for batches (needs per-file results, not node results); scale a 3D result back to the blockout's
   size; run the Text → 3D preset from the graph UI; the 3 never-run workflows (SeedVR2 3B int8 video, Gemma ×2);
   re-run the library on an animated character scene; `blender_graph_describe` filtering.
3. For b2renger (RUNBOOK §7.1): admin password; job prompt → negative prompt; Wan ID-V2V broken; missing-LoRA check;
   SeedVR2 HD size label; meta.json size/duration params.
4. Rules: follow `.claude/skills/comfyq-workflow-build` for new workflows and `.claude/skills/blender-ui-audit` for UI
   changes; after UI changes `smoke_test.ps1 -Ui` + docs/UI.md, and **look at the screenshots** (gotcha 43); install
   into the user's profile only when asked (not `--factory-startup`); ask before committing; never tick the user's
   prefs by script; tell the user before GPU runs / visible windows; smoke tests must not change what ComfyQ serves
   (gotcha 32); never touch the ComfyQ repo; write edit scripts to files, not heredocs (gotcha 40).
