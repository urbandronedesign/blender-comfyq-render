# Runbook — Blender ↔ ComfyQ bridge (state of 2026-10-04 night, build `76a8543`)

For b2renger and the teacher. It covers what the bridge does today, how to install it, run it and demo
it, how it uses ComfyQ, what was tested, and what is missing on each side. For more detail:
[UI.md](UI.md) (every control), [CLASS.md](CLASS.md) (teacher guide), [STUDENT.md](STUDENT.md) (the students'
quickstart), [AGENTS.md](AGENTS.md) (AI agents / MCP), [arch-render.md](arch-render.md) (architecture chain),
[ux-audit-plan.md](ux-audit-plan.md) (the UI audit, phases A–F), [preset-run.md](preset-run.md) (the run of every preset), [workflow-matrix.md](workflow-matrix.md)
(the earlier per-workflow sweep), [plans/node-graph.md](plans/node-graph.md) (graph design).

---

## 1. What it is

`comfyq_bridge` is a **Blender 5.2 extension** (it also runs in Mixar, the Blender fork used before).
Students lay out a scene and an animation in Blender; ComfyQ workflows do the final look ("AI
rendering"). The bridge:

- renders **passes** of the scene through its camera: a clay or matcap still, a mask of the selected
  objects (alpha = area to repaint), and the **clay animation** as an MP4 with a sound track;
- **books** them on ComfyQ workflows (Socket.IO `book_job`, uploads via `POST /upload`) and follows the
  job;
- **brings results back**: images go to the Image Editor; video and audio go to a separate "ComfyQ
  Results" scene; GLB and PLY files go into a collection;
- does all this from a **generation bar**, from a **node graph**, or from an **AI agent** (MCP server
  inside Blender).

```
Student PC (Blender + comfyq_bridge)                 GPU PC
  bar / node graph / MCP (127.0.0.1)  ── HTTP + Socket.IO ──►  ComfyQ :3000 ── lanes ──► ComfyUI :8188
  passes: clay still, mask, clay MP4                           (one served workflow per lane)
```

**Phase 1 rule: ComfyQ's code is not changed.** Everything uses the API ComfyQ already has. Admin routes
are used only by the opt-in teacher tools and solo mode (section 5).

## 2. Install

| Step | Where |
|---|---|
| 1. Build the zip | `.\tools\smoke_test.ps1` (also runs the headless tests) → `dist\comfyq_bridge-0.1.0.zip` |
| 2. Install | Blender › Edit › Preferences › Get Extensions › ⌄ › *Install from Disk…*, or `blender -b --online-mode --command extension install-file -r user_default -e dist\comfyq_bridge-0.1.0.zip` (**not** `--factory-startup`: it resets the user's preferences) |
| 3. Allow online access | Preferences › System › *Allow Online Access* (the bridge cannot connect without it) |
| 4. Server address | Preferences › Add-ons › ComfyQ Bridge › Server › *Profile* (or *Advanced › Server Address*, for example `http://192.168.1.10:3000`; *Test Connection* checks it), or a shared `%APPDATA%\comfyq-bridge\profiles.toml` (the teacher copies one file to every PC, **without** `admin_password`, which only goes in the teacher's own file; access password / token there or in env vars, never in the .blend) |
| 5. Student name | Asked in a dialog at the first *Connect* (lab PCs share Windows logins); shown on the jobs; you can only cancel your own. Change it with the ✎ beside the name in the bar |
| 6. Connect | 3D View › sidebar › ComfyQ › *Connect* (or tick *Connect on startup*) |
| 7. Presets (optional) | Copy `presets\library\*` (46 graphs) and the teacher's own presets into `%APPDATA%\comfyq-bridge\presets\`, or put them in one shared folder named by `presets_dir` in the deployed profile (or the *Shared Presets Folder* preference) |

Health check from a terminal: `.\tools\comfyq.ps1 doctor`.

## 3. Features

### 3.1 Generation bar (Ctrl+Shift+Q, the 3D View sidebar, or the detached "ComfyQ window")
- Workflow tabs (Image, Video, 3D, Splats, Audio, Tools), with **served** workflows first and a VRAM badge
  from ComfyQ's calibration. Every workflow is listed; one that is not served shows "ask the teacher".
- Settings chips built from ComfyQ's `meta.json` parameters, saved **per workflow** in the .blend.
- Multi-line prompts.
- **Scene matching:** duration, frames, fps, size and aspect are taken from the scene where the workflow's
  labels allow it.
- Inputs: a clay or matcap still, the mask of the selected objects, the clay animation (always rendered
  again at Generate), files, and an "AI keyframe" chain (an image workflow makes the first frame of a
  video job).
- Job cards show the queue place ("2nd in line · now running: Tom"), progress, "Sent" rows (what was sent),
  cancel, save a copy, import, download and remove. **Retry** on a failed / cancelled job sends it again with the
  same settings and files (no new render). Cards are re-attached when the .blend is opened again.
- A greyed-out button always says why in its tooltip (not connected, model not served, settings loading…).
- A **?** button (bar and graph header) opens the students' one-page quickstart in the browser.
- A video player in the ComfyQ window.

### 3.2 Node graph (ComfyQ window › *Graph*, or Node Editor › ComfyQ Graph)
- **Sources:** Scene Render (frame START / END / CURRENT, clay, matcap, material or **Cycles**: a real
  render in a separate Blender process, so the GPU is free before the AI job; Blender stays usable meanwhile, the
  node shows "Cycles 40/64" and a ✕), Mask, Scene Animation, Image file, Video file, Audio file, Prompt,
  **Surfaces (SAM3)** (finds floor, walls, ceiling… in a picture and writes a prompt naming each one's material).
- **Workflow nodes:** the inputs and settings come from ComfyQ's parameters. Nodes chain: one node's
  result feeds the next, and results that are still up to date are reused.
- **Outputs:** Viewer (follows the selected node), Import, Save (folder and file name pattern), Reroute,
  Frame. Each node keeps a result history.
- **Run Graph** runs the selected chains (select a preset's Frame) or, with nothing selected, asks before running
  them all; it books in dependency order, **Stop** cancels the run. A node whose workflow is not served says so
  before anything is booked.
- **Presets** (header › Presets):
  - 5 built-in presets made from *roles*, which pick the best served workflow: scene render → image,
    clay animation → video, AI keyframe → video, image → 3D, image → upscale. Each is added beside the graph in a
    Frame, with a **How to use** note (numbered steps).
  - Class presets in `presets/class/` (Cycles → photoreal; Text → 3D; **Architecture: Cycles → SAM3 surfaces →
    Qwen edit → SeedVR2 4K**), each with its How to use note.
  - Teacher presets: *Save Graph as Preset…* writes a JSON file; subfolders become submenus. The menu lists
    **Saved on This PC** and **Shared** (a shared folder set by the *Shared Presets Folder* preference or the
    profile's `presets_dir`).
  - The **workflow library: 46 presets** (ComfyQ `be109b3`), one per t2i / image-edit / i2v / video-edit workflow, in 4
    submenus. Each one wires the scene sources, image file nodes for references, characters and styles,
    a Prompt node starting from the workflow's example prompt, and Viewer + Save. Settings follow the
    scene they are added to. It is generated by `tools/make_preset_library.py` from ComfyQ's
    parameters, so it can be regenerated whenever the workflows change.

### 3.3 AI agents (MCP server inside Blender, off by default)
Preferences › *AI agents (MCP server)*. The server listens on 127.0.0.1 only, with a new token each
start; agents connect through `mcp/launcher.py` (stdio). It offers **23 tools** (+ 3 teacher tools):
- **Scene:** `blender_scene_info`, `blender_select`, `blender_render` (still / mask / animation),
  `blender_import` (3D placed beside the scene / at the cursor, on the ground, sized), `blender_transform`,
  `blender_world` (HDRI lighting), `blender_shadow_catcher` (ground / objects that only catch shadows),
  `blender_segment` + `blender_task_status` (SAM3 surfaces of a render, in the background), `blender_mask_image`
  (a repaint mask from found surfaces).
- **Graph:** `blender_graph_describe`, `blender_graph_add_preset`, `blender_graph_set` (settings,
  prompts, files, images), `blender_graph_run`, `blender_graph_status`.
- **ComfyQ:** `comfyq_list_workflows`, `comfyq_describe_workflow`, `comfyq_submit`, `comfyq_job_status`,
  `comfyq_wait`, `comfyq_cancel`, `comfyq_outputs`, `comfyq_server_status`.
- **Teacher tools**, behind their own preference and hidden while it is off: `comfyq_serve` (as a lane
  alongside if it fits, else as the main workflow), `comfyq_unserve`, `comfyq_calibrate`.

Claude Code is set up in the repo (`.mcp.json`, skill `.claude/skills/blender-comfyq-roundtrip`) and
was verified end to end. Codex has not been tried yet.

### 3.4 Teacher controls and solo mode (preparing alone, never in class)
**Teacher controls need ComfyQ's admin password in the teacher's profile** (`admin_password` in
`%APPDATA%\comfyq-bridge\profiles.toml`, or `COMFYQ_ADMIN_PASSWORD`). Without it, Solo mode, Load Model and the agents'
teacher tools are hidden: a student PC cannot switch what ComfyQ serves. The preferences' **Teacher** section shows
"Locked" there. This only works fully once ComfyQ itself has an admin password (7.1): today ComfyQ accepts any value.
Model switches from the UI ask first ("Load N Models in ComfyQ?"). Students are asked for their name at the first
Connect (lab PCs share Windows logins). **Class mode:** `class_mode = true` in a profile turns every teacher control
off even if an admin password is there too: deploy it in the students' profiles file. On the teacher's PC the
sidebar's **Server (Teacher)** panel shows GPU memory, served models, the whole queue and who is connected, with
**Load Model…** (UX phase F2).

Preferences › Teacher › *Solo Mode*. **Run Graph** (or an agent's `blender_graph_run`) loads
the model each node needs:
- one model at a time, as a lane alongside if it fits, else as ComfyQ's main workflow (ComfyQ restarts);
- only when nothing is booked and **nobody has an unfinished job** on the server;
- the last model stays loaded; the next preset decides what comes next.

Nodes say *Loads on Run* and have a **Load Model** button. In the run of the whole library, the 48
switches took about 30 s each.

### 3.5 New in UX phase F (2026-10-04)
| What | Who | Where |
|---|---|---|
| **Retry** failed / cancelled jobs (same settings and files, no new render) | students | job cards |
| **Cycles render without freezing Blender** (samples + ✕; graph Stop kills it) | everyone | Scene Render node, agents |
| **? quickstart** + **How to use** notes on presets | students | bar, graph header, presets |
| **Shared presets folder** (preference or `presets_dir` in the profile) | teacher | Preferences › Student; Presets › Shared |
| **Server (Teacher)** panel: GPU memory, served models, queue, connected people, Load Model… | teacher PC only | 3D View sidebar › ComfyQ (closed by default) |
| **Class mode** (`class_mode = true` in the students' profile): no teacher controls at all | student PCs | profiles.toml |

## 4. Demo (about 10 min, one PC, ComfyQ serving LTX 2.5 IC-LoRA canny)

1. Open a scene with a camera and some animation. Ctrl+Shift+Q opens the bar.
2. **Video** tab › *LTX 2.5 IC-LoRA canny*: the duration chip takes the scene's length. Write a prompt
   and press *Generate*. The clay MP4 is rendered, uploaded and booked; the job card shows progress, then
   the video plays in the ComfyQ window.
3. ComfyQ window › *Graph*. Presets › Saved › **Video → video** › *LTX 2.5 IC-LoRA — canny*. Show how
   the clay animation, the first frame and the prompt are wired. *Run Graph*: the Viewer shows the result.
4. Presets › *AI keyframe → video* shows the chaining: an image workflow restyles frame 1, which then
   starts the video.
5. (Agent) In a terminal in the repo: `claude`, then *"render the scene's animation and restyle it with
   the served video workflow, keep it short"*. The agent uses the MCP tools; the job appears in Blender.
6. (Solo, if nobody else uses the server) tick Solo, then Presets › Saved › Image › *Flux2 Klein 9B —
   text to image* › Run Graph. ComfyQ switches to Klein (about 30 s) and the image appears.

## 5. How the bridge uses ComfyQ

| Use | API |
|---|---|
| List workflows, VRAM, availability | `GET /workflows?includeUnavailable=1` |
| Parameters → settings and inputs | `GET /workflows/:id/parameters` (labels, kinds, min/max, `disabledWhen`, options) |
| What is served | Socket.IO `state_update` / `state_patch` → `lanes` (+ `parameter_map` for LoRA options) |
| Book / cancel / delete | Socket.IO `register_user`, `book_job` (also sends the main prompt as top-level `prompt`), `cancel_job`, `delete_job` |
| Inputs | `POST /upload` (images ≤ 1920×1080; the bridge resizes to the parameter's `maxInputEdge`) |
| Results | job `outputs` → `GET /media/_any/<subfolder/file>` |
| Access | `/access/status`, `/access/login` (token in `x-access-token`) |
| Teacher tools / solo only | `GET /admin/lanes` (served, VRAM, `fit`), `POST /admin/lanes`, `DELETE /admin/lanes/:id`, `POST /admin/activate-workflow` (restart), `POST /workflows/:id/calibrate`; `x-admin-password` from `COMFYQ_ADMIN_PASSWORD` or the profile |

## 6. Test status

| Test | Result |
|---|---|
| Unit + contract tests (`pytest tests/unit tests/contract`) | 79 pass, 2 skipped (contract tests run against the live ComfyQ) |
| Headless smoke test in Blender 5.2 (`tools/smoke_test.ps1`) | passes (passes, imports, graph, presets, MCP, teacher tools, solo with a faked switch, image upload, Surfaces, UX phases A–F) |
| Visible GUI check, every button (`smoke_test.ps1 -Ui`) | **103 / 103** (2026-10-04, after UX phase F) |
| Agent round trip, Claude Code → MCP → render → book → import | ✅ |
| Workflow sweep (calibrate + run each workflow from Blender) | 24 / 66 (LTX 2.5, Flux 2, Qwen 2.1, Krea 2) |
| **Preset library run through the MCP (all 48, solo mode)** | **46 / 48 run**; 2 fail in ComfyUI ([preset-run.md](preset-run.md)). Visual baseline: `baselines/2026-10-04/index.html` (local, not in git: per workflow its description, the Blender graph, inputs, prompt + settings sent, the output image/video) |
| Image → 3D (2026-10-04, via MCP): Flux2 Klein supercar → TRELLIS2 (since removed from ComfyQ) → GLB imported | ✅ 461k faces + 2 × 2048 textures in ~6 min; the model was complete and textured (checked from 3 angles). It landed at the origin inside the scene's cube → Import placement added |
| Architecture chain (2026-10-04): Cycles → SAM3 surfaces → Qwen edit → SeedVR2 4K | ✅ full class preset run headless in 388 s ([arch-render.md](arch-render.md)) |
| Text → 3D (2026-10-05, `tools\text3d_check.ps1`, the plugin's client without Blender): the preset's prompt → Flux2 Klein → **Pixal3D / TRELLIS.2** | ✅ Klein 35 s, Pixal3D 220 s; a complete, clean car (694k faces, PBR textures 4096²) with a believable invented back; each model switch took 24 s |
| Stable Audio 3 Medium (2026-10-05, `tools\workflow_check.ps1`) | ✅ 20 s stereo MP3 in 48 s (level and fade-out checked by measurement, not listened to) |
| Qwen 2.1 t2i *Fix*, Qwen 2511 unblur upscale (the 2 workflows new in `be109b3`) | ❌ on this PC: ComfyUI refuses the job, their LoRA files are not installed (7.1) |
| Overall, by workflow (ComfyQ `be109b3`, **61 workflows**) | **50 of 61 run** (43 library presets + LTX deblur-upscale + SAM3 + PiD + SeedVR2 7B image + SeedVR2 4K + Pixal3D/TRELLIS.2 + Stable Audio; 2 Wan ID-V2V fail in ComfyUI); **9 never run**: Pixal3D multi-views, TripoSplat, SeedVR2 video ×2, frame interpolation, Gemma ×2, and the 2 new ones (Qwen 2.1 t2i *Fix*, Qwen 2511 unblur upscale: models missing). Removed by ComfyQ on 2026-10-05: TRELLIS2, Pixal3D image→mesh, Flux2 Dev ×2, Qwen Viggle Turbo, Marigold ×2 |

## 7. What is missing

### 7.1 For ComfyQ (b2renger), most important first
1. **Security for class:**
   - there is no admin password (`config.json` `auth.adminPasswordHash` is empty), so any PC on the LAN
     can open lanes or calibrate;
   - `POST /admin/activate-workflow` has **no admin gate at all**;
   - ComfyUI listens on 0.0.0.0:8188, so students can skip the queue.

   This is P1.0 of the plan, postponed until now.
2. **The job prompt lands in the negative prompt** (`realtime/realtimeBus.js` `book_job`, ~line 206): ComfyQ copies the
   job's top-level `prompt` into the first *unset* textarea whose field is `text`. When a workflow's real prompt is a
   Primitive (field `value`), as in Flux2 Klein t2i, that is the **Negative prompt**: every Klein t2i job from Blender ran
   with negative = prompt. The bridge now sends every prompt field explicitly (work-around); the server should only fill
   the parameter that `meta.json` marks as the prompt.
2b. **Two broken workflows:** both Wan 2.1 ID-V2V restyle workflows fail in ComfyUI 0.37 with
   `WanImageToVideo.execute() got an unexpected keyword argument 'ref_pad_image'` (node version
   mismatch). They also have no example prompt.
2c. **New workflows shipped without their LoRAs (be109b3):** on this PC `image_qwen_image_2_1_t2i_fix` needs
   `qwen-image-2.1-fix-1.0-comfy.safetensors` and `utility_qwen_2511_unblur_upscale` needs
   `Qwen-Image-Edit-Unblur-Upscale_20.safetensors`; neither is installed, yet `GET /workflows` reports both as
   available (0 unavailable): the missing-model check does not look at LoRA option values. ComfyUI then refuses
   the job ("lora_name … this ComfyUI does not offer"). The admin panel's new Model downloads may fetch them.
3. **Calibration inputs missing:** `comfyq__bench-vi__sports_car.png` and `comfyq__bench-im__sports_car.png`
   are not in ComfyUI/input, so calibrating LTX 2.5 union and Qwen 2.1 control depth fails.
4. **Names that don't match ids:**
   - "LTX 2.5 IC-LoRA — video to video (**depth control**)" is `…iclora_union_control`;
   - "MiniMax H3 ControlNet (**pose**)" is `…fun_controlnet_union`.
5. **`meta.json` metadata the clients need:**
   - which parameters are **width / height / duration / frames / fps**: today the bridge guesses from the
     labels and misses Qwen, Krea and Ideogram sizes, which come out square or portrait on a 16:9 scene;
   - the **output kind** (image, video, model…), which the bridge infers from category, tags and id;
   - an example prompt for every workflow.
6. **Server API (phase 2):**
   - `served` and `lane` fields in `GET /workflows`;
   - SSE `/events`;
   - REST `POST /jobs` + cancel, and `/capabilities`;
   - a native `/mcp` endpoint.

   The client already hides the protocol behind `ComfyQTransport`, so a REST transport plugs in with no
   UI change.
7. **"Direct control" bundles** (`preprocess: none`): Blender can give exact depth, normals, lines and ID
   masks, so the ControlNet workflows could skip their estimators (Qwen control, LTX IC-LoRA, MiniMax
   ControlNet).
8. **Fairness:** per-user queue limits (`maxQueuedPerUser`, `maxQueuedTotal`), and `job_deps` for
   server-side chains (the graph chains on the client today).
9. **Switching without dropping students:** `activate-workflow` restarts the server and every open
   student page reconnects. Lanes avoid that, but nothing heavy fits next to a 30 GB model on 32 GB.

### 7.2 On the bridge side
- **12 workflows never run from Blender** (section 6). The library covers only image and video; 3D,
  upscale, audio, describe and utility presets are next.
- **Agents:** the Codex round trip has not been tried (no Codex CLI on this PC). There has been no class
  rehearsal with several PCs.
- **Untested end to end:** the bar's AI-keyframe chain, and "image workflow → video start image" in the
  graph with a live job.
- **UI (audit 2026-10-04, `docs/ux-audit-plan.md`): phases A–F are all done and installed:**
  - safety (admin-password gate, Class mode, confirmations, student names);
  - feedback (disabled buttons say why, queue position, Stop, Remove, Retry);
  - Blender HIG wording and icons, code health;
  - layout (collapsed inputs, graph mode without the bar, presets in Frames, preference sections);
  - phase F: non-blocking Cycles, teacher Server panel, student quickstart + How to use notes, shared presets folder.

  Still open: the dialog does not redraw job progress live; the graph has not been compared with the Moodboard
  reference; nobody has used phase F in a real class yet (a rehearsal with several PCs is the next test).
- **Graph runs:** the multi-model "stage runner" with bundles across servers is only proposed. Solo mode
  covers one PC, one model at a time.
- **Example prompts:** they describe other scenes. On a bare scene they pull results away (see the ⚠️ in
  preset-run.md); class presets should ship with prompts written for the lesson.
- **The next library run** should use an animated character scene, so pose and character workflows can
  be judged on what they are made for.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bar says "Loading…", Generate greyed | Not connected, or settings still loading: press *Connect* / *Refresh* |
| "Not served right now (ask the teacher)" | Only served workflows run. Teacher: serve it in ComfyQ, or use solo mode when alone |
| Rendered input shows old AI results instead of the scene | Old strips in the scene's sequencer: *Clean up* (the bar warns) |
| "Online access is disabled" | Preferences › System › Allow Online Access |
| Open Folder / file "does nothing" | Fixed: Explorer used to open behind Blender |
| "Image … does not have any image data" | Fixed in the current build (rebuild and reinstall) |
| An agent doesn't see new tools | Reconnect it (`/mcp` in Claude Code); Blender writes a new token at each start |
| Solo mode waits forever | Someone has a scheduled or running job on ComfyQ; models never switch under a job |
| ComfyQ unreachable for about 1 min after a switch | A main-workflow switch restarts ComfyQ; the bridge reconnects by itself |
| Retry is greyed out | Its tooltip says why: the job was sent before Retry existed, an input file was deleted (Clean Up), or the model is not served |
| No Server panel / Solo on the teacher PC | The profile needs `admin_password` and must not have `class_mode = true`; the panel is closed by default (click its header) |
| A student PC shows teacher controls | Its profiles file has the admin password: deploy the class profile (with `class_mode = true`, without `admin_password`) |

## 9. Commands
```powershell
.\.venv\Scripts\python -m pytest -q tests\unit tests\contract     # unit + contract (ComfyQ running)
.\tools\smoke_test.ps1                   # build the zip + headless Blender tests (isolated profile, no GPU)
.\tools\smoke_test.ps1 -Ui               # visible Blender, every button (~3 min)
.\tools\smoke_test.ps1 -Sweep            # calibrate + run served workflows (GPU)
.\tools\comfyq.ps1 doctor                # classroom health check
blender -b --online-mode --python tools\make_preset_library.py -- presets\library   # regenerate the library
python tools\preset_run.py               # run every preset through the MCP (Blender open, Solo on; GPU, ~1.5 h)
blender <scene copy> --python tools\preset_shots.py -- <results.json> <shots dir>   # graph screenshots (sandbox, no GPU)
python tools\preset_report.py baselines\<date> [--baseline baselines\2026-10-04]      # HTML report (+ comparison)
```
