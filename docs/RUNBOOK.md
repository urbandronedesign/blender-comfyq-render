# Runbook — ComfyQ Render (Blender ↔ ComfyQ) (state of 2026-10-06, build `5c70c06`, ComfyQ `be109b3`)

For b2renger and the teacher. It covers what the bridge does today, how to install it, run it and demo
it, how it uses ComfyQ, what was tested, and what is missing on each side. For more detail:
[UI.md](UI.md) (every control), [CLASS.md](CLASS.md) (teacher guide), [STUDENT.md](STUDENT.md) (the students'
quickstart), [AGENTS.md](AGENTS.md) (AI agents / MCP), [arch-render.md](arch-render.md) (architecture chain),
[ux-audit-plan.md](ux-audit-plan.md) (the UI audit, phases A–F), [preset-run.md](preset-run.md) (the run of every preset), [workflow-matrix.md](workflow-matrix.md)
(the earlier per-workflow sweep), [plans/node-graph.md](plans/node-graph.md) (graph design).

---

## 1. What it is

**ComfyQ Render** (extension id `comfyq_bridge`) is a **Blender 5.2 extension**.
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
| 4. Server address | Preferences › Add-ons › ComfyQ Render › Server › *Profile* (or *Advanced › Server Address*, for example `http://192.168.1.10:3000`; *Test Connection* checks it), or a shared `%APPDATA%\comfyq-bridge\profiles.toml` (the teacher copies one file to every PC, **without** `admin_password`, which only goes in the teacher's own file; access password / token there or in env vars, never in the .blend) |
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
  node shows "Cycles 40/64" and a ✕), Mask, Scene Animation, Image file, Video file, **Media Folder** (batch:
  section 3.6), Audio file, Prompt,
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
  - Class presets in `presets/class/`, each with its How to use note: Cycles → photoreal (Klein); Text → 3D model
    (Klein + Pixal3D); **Architecture: Cycles → SAM3 surfaces → Qwen edit → SeedVR2 4K**; **Four views: clay or
    photo → rendering** (4 Image nodes → 4 Klein edits with one prompt and one seed, so the materials match);
    **Batch: folder → rendering** (a Media Folder → Klein edit → Save).
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

### 3.6 Batch renders from a folder (2026-10-06)
A **Media Folder** node (graph header › Scene) takes a folder of pictures or videos, subfolders included. Put it where
a chain's Image or Video File node was, and a **Save** node at the end. **Run Graph** then renders each file in turn,
**one ComfyQ job at a time** (one student's batch never floods the class queue), with progress, time left and a ✕ on
the folder node. The results go to a new folder next to the input one, with the same subfolders and each file's name
plus the Save node's **Batch Suffix**:

```
photos/facades/front.png   →   photos_renders/facades/front_render.png
clips/set2/short.mp4       →   clips_renders/set2/short_smooth.mp4
```

A failed file is noted and the batch goes on. After a Stop or a crash, Run Graph again renders only what has no
result yet. Ready-made: the class preset *Batch: folder → rendering (Flux2 Klein edit)* (fixed seed: a matching series).
Measured: 4 pictures ≈ 100 s with Klein edit; 2 clips ≈ 60 s with FILM interpolation.

### 3.7 New on 2026-10-21 (ComfyQ's new parameter types, blockout video)
- **3D model inputs** (ComfyQ `model3d`: Remesh & decimate, UV unwrap): source *Selected Objects* (one GLB, modifiers
  applied) or *File*; graph nodes **Scene Objects** and **3D Model File**; a mesh result chains into another mesh input.
  Remesh returns an empty mesh for models ~80 units across (warned); UV unwrap replaces the model's own UVs/textures.
- **Camera position** (ComfyQ `camera`: Qwen 2511 reshoot from another angle): Around / Height / Zoom, the shot words
  the model gets, **From 3D View** (orbit the viewport around the subject, measured from the scene camera).
- **Class presets** (10 now): Remesh, UV unwrap, Clean up a model (both), Reshoot from another angle (Qwen 2511), and
  **Blockout animation → photoreal video (FLUX.3 keyframe + LTX canny)** ([blockout-video.md](blockout-video.md)).
- **Multi-shot films** (2026-10-22, [multi-shot.md](multi-shot.md)): a *Shot* field on scene sources (timeline markers bound to cameras), the **Material Board**, **Join Text** and **Film** nodes, and the class preset *Multi-shot film* (3 shots, one look: FLUX.3 hero keyframe → board → Qwen 2.1 keyframes → LTX canny → Film). Tested end to end: 3 shots, 14 s film, 25 min. Solo closes lanes that would not fit and loads the model most waiting nodes need.
- **Fixes:** the bridge waits for ComfyQ's restoring lanes before loading a model alongside (§7.1 1b); auto-connect
  and the AI-agents server start when Blender is opened with a .blend; agents open the graph so the user sees it.

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
| Qwen 2.1 t2i *Fix*, Qwen 2511 unblur upscale (the 2 workflows new in `be109b3`) | first ❌ (LoRA files missing, 7.1 §2c); after downloading the 5 LoRAs into ComfyUI's `models\loras` from their public Hugging Face repos: ✅ both run. *Fix* (69 s, same seed): a different, straight-on composition with crisper lettering, not a clear improvement. *Unblur*: 1392×752 out of a 1904×1080 render, softer; for blurred faces, not an upscaler for renders (SeedVR2 4K stays). Fast mode 11 s ≈ full 152 s |
| SeedVR2 HD video upscale (2026-10-05, a 848×480 5 s AI clip) | ✅ 1908×1080 in 183 s, sound kept; clearly sharper (eyes, edges), no added flicker (frame-to-frame change +11 %). Its *longest edge* setting is really the shortest edge (7.1 §5) |
| FILM frame interpolation (same clip) | ✅ 2× at real-time speed: 48 fps in 29 s, sound kept, clean invented frames. 4× slow motion: 20 s of video in 21 s, but the sound stays 5 s long (as the workflow warns). The clip had little motion: fast action not tested |
| Pixal3D Multi-View + TripoSplat (2026-10-05, a Herzog & de Meuron-style copper museum made with Qwen 2.1 Fix) | ⚠️ both run (189 s / 47 s; TripoSplat returns a 4.9M-face .glb, a 1M-splat .ply and an .spz), but the building failed: the input was a flat front elevation. Qwen "character camera angle" could not turn it (the other 3 views were more elevations), so Multi-View made an L-shaped roofless shell, and TripoSplat kept the white backdrop as a room around a hollow box. 3D from a building needs a 3/4 view, or 4 views that truly agree |
| Plan A for 3D (2026-10-06): a Blender blockout of the museum → 4 clay views by the plugin (`tools/museum_blockout.py`) → Klein edit → Pixal3D Multi-View | ✅ the 4 AI views agree (windows in place, copper skin, glazed ground floor). 3D (256 s): a closed building with a roof and the windows in place, but a square plan with chamfered corners (Multi-View frames each view separately, so the 36 m façade vs 20 m side is lost), no plinth, olive-tinted copper |
| Class preset *Four views: clay or photo → rendering* (2026-10-06), run as a real graph with the museum's 4 clay views | ✅ 4 consistent copper views in 99 s, saved next to the .blend. The first run rendered the LEFT view as the front: two inputs prepared in the same second shared one temporary file (bug in the plugin's pass naming, fixed) |
| **Batch renders from a folder** (2026-10-06, Media Folder node; sandbox, real graph) | ✅ pictures: 4 clay views in 2 subfolders through Klein edit, 101 s (113 s via the *Batch* class preset), each result next to its own input in `photos_renders/<same subfolder>/<name>_render.png`; videos: 2 clips in 2 subfolders through FILM 2×, 58 s including the model load, 48 fps, sound kept. One job at a time; resume after a Stop skips finished files |
| Gaussian splat in Blender + Cycles (`tools/splat_render.py`) | ✅ Blender 5.2 has no splat support: the script loads the .ply as a coloured point cloud (colour, size, opacity from the splat) and Cycles renders TripoSplat's 1M splats in ~1.3 s per view with the right colours (spheres, not true Gaussian blending). The plugin's own PLY import is still bare vertices without colour |
| Overall, by workflow (ComfyQ `be109b3`, **61 workflows**) | **56 of 61 run** (43 library presets + LTX deblur-upscale + SAM3 + PiD + SeedVR2 7B image + SeedVR2 4K + Pixal3D/TRELLIS.2 + Stable Audio + Qwen 2.1 Fix + Qwen 2511 unblur + SeedVR2 HD video + FILM + Pixal3D Multi-View + TripoSplat; 2 Wan ID-V2V fail in ComfyUI); **3 never run**: SeedVR2 3B int8 video, Gemma ×2. Removed by ComfyQ on 2026-10-05: TRELLIS2, Pixal3D image→mesh, Flux2 Dev ×2, Qwen Viggle Turbo, Marigold ×2 |

## 7. What is missing

### 7.1 For ComfyQ (b2renger), most important first
1. **Security for class:**
   - ~~there is no admin password~~ an admin password is set since 2026-10-21 (admin page); the bridge's teacher
     profile holds the same one. Lanes, calibration and model downloads now need it;
   - `POST /admin/activate-workflow` has **no admin gate at all**;
   - ComfyUI listens on 0.0.0.0:8188, so students can skip the queue.

   This is P1.0 of the plan, postponed until now.
1b. **Lane-port race after a restart** (reproduced 2026-10-21, `tools/lane_race_check.py`,
   `live_check/lane_race/report.json`): `restoreRemembered()` reopens the remembered lanes in the background (not
   awaited) and `LaneManager._nextPort()` only counts lanes already in `this.lanes`, which a lane joins only after its
   ComfyUI has started. A lane opened during the restore (Load Model / Solo right after a main switch) gets the
   SAME port as the lane being restored: both report 8189, one ComfyUI process serves them, and the next job on the
   new lane fails `comfyui-process-exited` (7 s). Opened after the restore, the same lane gets 8190 and works (31 s).
   Fix: reserve the port before `await worker.start()` (e.g. a `pendingPorts` set), or await the restore before
   accepting `POST /admin/lanes`. The bridge works around it (Solo, Load Model..., `comfyq_serve` wait until the
   remembered lanes are back: `runtime/serving.wait_restored`), but other clients (ComfyQ's own admin page) still
   hit it.
1c. **LTX 2.5 slows down over consecutive jobs** (2026-10-22, multi-shot film, only LTX loaded, no other lane): jobs 1 and 2 took 138 s and 116 s, job 3 started with the GPU almost full ("Unloaded partially … lowvram" while loading the text encoder) and its upscale stage ran at 62.7 s/step instead of 9.7: 15 min 48 s. ComfyUI seems to keep more memory after each LTX job; freeing the models between jobs (or a `--reserve-vram` margin) on the LTX lane would keep every job at ~2 min.
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
   the job ("lora_name … this ComfyUI does not offer"). On the teacher PC we downloaded them (2026-10-05) from
   `e-n-v-y/Qwen-Image-2.1-Fix` and `prithivMLmods/Qwen-Image-Edit-2511-Unblur-Upscale` (all 4 checkpoints); other
   rigs still need them.
3. **Calibration inputs missing:** `comfyq__bench-vi__sports_car.png` and `comfyq__bench-im__sports_car.png`
   are not in ComfyUI/input, so calibrating LTX 2.5 union and Qwen 2.1 control depth fails.
4. **Names that don't match ids:**
   - "LTX 2.5 IC-LoRA — video to video (**depth control**)" is `…iclora_union_control`;
   - "MiniMax H3 ControlNet (**pose**)" is `…fun_controlnet_union`.
5. **`meta.json` metadata the clients need:**
   - `utility_seedvr2_hd_upscale` still labels its size "longest edge" while SeedVR2 uses it as the SHORTEST edge
     (1080 → 1908×1080 on a 16:9 clip): the fix made to the 4K bundle in `9f8bacb` should go to the HD video one too;
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
