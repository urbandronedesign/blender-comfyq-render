# UX / UI audit 2 — findings (Blender 5.3, 2026-11-15)

Phase 1 of [ux-audit-2-plan.md](ux-audit-2-plan.md). Read-only: no code was changed.

**Method.**
- Three independent reviews:
  - Blender HIG / Best Practice checklist over the bar, operators and preferences (`ui/`, `ops/`, `prefs.py`, `props.py`);
  - the same checklist over the node graph (`graph/`);
  - Nielsen heuristics walked through nine journeys, using 56 screenshots of today's `smoke_test.ps1 -Ui -Version 5.3` run.
- The strongest claims were then re-checked in the code by hand (marked ✔).
- Severity is Nielsen 0–4 (4 = must fix before class).
- **[R]** marks a regression of something the first audit (2026-10-04) fixed.

**Totals.** No severity 4. **19 at severity 3**, about 45 at 2, about 110 at 1, about 30 at 0.

Most of it is in code added since October: the Server panel, 3D-model and splat inputs, shots, Film, Board, Media Folder and the camera block. There are 10 regressions.

## Test-evidence problems (fix first: the next audit runs on these)
| # | Problem | Fix |
|---|---|---|
| T1 | ~~Six ComfyQ-window screenshots are byte-identical~~ **false alarm** (checked 2026-11-15): the file names are not in run order. In run order these shots come from steps that work in the main window and then restore the ComfyQ window exactly as it was (reload → camera bar → mesh bar; new-inputs graph removed; Media Folder restored). Window capture works. | None. |
| T2 | **The Queue popover from step 08 stays open** in `09_keyframe_main` and `11_disconnected_main`. This is gotcha 42 regressed. | DONE: moved to a `step_queue_popover` near the end, with the other popups. |
| T3 | **Panels are drawn on top of each other:** Jobs over Prompt in `01_connected_main`, and the Server (Teacher) header over a job card in `07_job_card_main`. | Partly explained. `shot()` now redraws 3 times, and a 1 s settle goes before `09_keyframe`, which is now clean, so that overlap was panel animation. `01_connected` **still overlaps after 1 s**, with LTX's long multi-line prompt preview: suspected layout oscillation (a tall bar → scrollbar → narrower → re-wrap → new height). **To check interactively in phase 6** (choose LTX with a long prompt in a normal sidebar). |
| T4 | **`09j_cycles_rendering` shows no Cycles progress:** the view is zoomed on a Viewer node. | DONE: the node is made active and drawn before `view_selected`; the shot shows "Cycles: loading the scene" on the framed node. |

## Severity 3 — fix before the next class

### Connecting (journey 1)
| # | Where | What happens | Proposal |
|---|---|---|---|
| 1 | `client/profile.py:20`, `client/http.py:22` | A student PC without `profiles.toml` connects to *its own* 127.0.0.1. The message blames the "server PC". | On loopback and not the teacher PC, say "No class server set on this PC: ask the teacher for the profiles file, or type the server address". Add a **Server Address...** button. Test Connection says the same. |
| 2 | `client/api.py:41`, `prefs.py` | "This ComfyQ requires an access password" is a dead end: there is nowhere to type it. | An **Access Password...** dialog. Keep it in the session or the profile, never in the .blend. |
| 3 | `ui/studio.py:109`, `:178` | **The connection is lost mid-class** (ComfyQ restart, admin mode). The bar still looks connected: Generate is greyed with no text, there is no Connect button, and the reason is only in a tooltip. | A state line ("Reconnecting to the class server… 0:12" / "Back, but not serving"), a **Reconnect** button, and specs reloaded on reconnect. |

### Results and 3D (journeys 2 and 9)
| # | Where | What happens | Proposal |
|---|---|---|---|
| 4 ✔ | `importers/media.py:170-198`, `passes/render.py:197` | **Imported results go INTO the next render.** "Beside the scene" ignores the camera, so the idol lands inside the camera frame. Passes and the fingerprint skip only `hide_render`, so the AI result is sent again and every scene-fed result goes out of date. | Place outside the camera frustum. Leave `comfyq_job` collections out of passes and the fingerprint by default, with an "Include in Renders" switch. |
| 5 | `ui/player.py`, `ops/workflow.py` | **The viewer never fits the picture:** a 1920×1080 render shows at 1:1, a corner only. | `image.view_all(fit_view=True)` from the timer whenever the viewer shows something new. |
| 6 | `importers/media.py:60-67` | **Results take over the first Image Editor of any window,** including one being painted or used for UVs. The new Paint and Mask features would lose work this way. | Show results only in the ComfyQ viewer. Never touch an editor in Paint or Mask mode, or a pinned one. |
| 7 | `ui/studio.py:528-532`, `ui/player.py:237-239` ✔ | **A sent `.glb` (Selected Objects) is labelled "(image)",** and its **Show** button loads it as an image, ending in an uncaught RuntimeError. | Pick the kind from the extension (3D model / sound). Show only viewable files; otherwise Open. Wrap the load. |
| 8 | `ops/results.py:202`, `ops/generate.py:367, 396` | A failed import is reported as INFO "Imported x.glb: import failed (…)", and the card marks it imported. | `import_files` returns (ok, text). Report failures as WARNING, and set `imported` only on success. |

### Waits and failures (journey 4)
| # | Where | What happens | Proposal |
|---|---|---|---|
| 9 ✔ | `runtime/session.py:219-228`, `ops/results.py:178` | **Ghost jobs:** jobs the server no longer knows stay "Waiting in queue" for ever. They cannot be cancelled or removed, and the Queue badge counts them. 7 October jobs show this way while the Server panel says the queue is empty. | A **Lost** state ("Not on the server any more"), removable. Add "Remove Finished and Lost" to the Queue popover. |

### Teacher and splats (journey 5)
| # | Where | What happens | Proposal |
|---|---|---|---|
| 10 | `importers/media.py` (`.spz` on 5.2), `docs/CLASS.md:12`, `docs/UI.md:3` | **Splat presets produce nothing on Blender 5.2 PCs.** IDOL returns only `.spz`, and the docs still tell teachers to install 5.2. | CLASS.md: 5.3 for splats. On 5.2 the splat presets, menu entries and Import are greyed with "Needs Blender 5.3". |

### Saving and outputs (journey 3)
| # | Where | What happens | Proposal |
|---|---|---|---|
| 11 ✔ | `graph/tree.py:1002`, `:1156` | **A failed automatic Save, Import or Film shows a green check:** the status "Failed: …" is drawn with `CHECKMARK`. | Use ERROR and alert when it failed, and wrap the text. |
| 12 ✔ | `graph/preset_ops.py:479-481` | **Save Graph as Preset silently overwrites** a preset of the same name, also in the class's shared folder. It does not catch OSError, so a read-only share gives a traceback. | Warn "Replaces …" in the dialog, or refuse with a message. Catch OSError and report it. |

### Starting frame (journey 6)
| # | Where | What happens | Proposal |
|---|---|---|---|
| 13 | `ui/studio.py:326-368` | The AI-keyframe model shows **Aspect 1:1** in a 16:9 scene, with its own example prompt: the video's start frame comes out square. | Match the keyframe model to the scene too. Start its prompt empty or linked. |
| 14 | `ops/generate.py:290`, gotcha 29 | **Keyframe → video needs two big models at once,** which do not fit together. Nothing warns before the wait, and the chain has never run end to end. | Say which model is not served before Generate. Offer an existing picture or earlier result as the start frame. Run the chain once. |
| 15 | `ui/studio.py:292`, `graph/tree.py:229` | **No way to make a frame in Blender:** the Blender Image source offers Open only (no New, no Paint). | The new feature. Meanwhile `new="image.new"` and a **Paint...** button. |
| 16 ✔ | `graph/run.py:73-76` | **Painting more does not refresh the result:** the Image node hash uses `is_dirty`, which stays True after the first stroke. | Hash the pixels (downsampled, from the timer). |

### Masking (journey 7)
| # | Where | What happens | Proposal |
|---|---|---|---|
| 17 ✔ | `presets/library/Image edit/Flux2 Klein 9B — inpaint*.json` | **The inpaint presets promise painting that does not exist** ("paint over the part you want to replace"). The graph actually uses the selected objects. | Describe what exists, until Mask Zone ships. |
| 18 | `props.py:53` | **Only scene renders can be masked.** An AI result, keyframe or photo cannot. *Scene Render* as a mask source is opaque, so nothing is repainted and nothing says so. SAM3 masks are agent-only. | The new feature. Remove or warn on the opaque source. |
| 19 | `graph/tree.py:322-325`, `graph/run.py:69` | **The mask follows whatever is selected NOW,** so selecting something else changes the zone and re-runs the job. | **Lock Selection** (store the object names), and show them. |

## Severity 2 — next

### Bar, viewer and player
- **20.** Clay wording when the style is Matcap or Material:
  - `ui/studio.py:399, 401`, `ui/player.py:166, 168` — "Clay animation…", "Render & Play Clay Animation";
  - `graph/tree.py:358` — the same on the node.
  - Fix: name the chosen style, or say "Scene Animation".
- **21.** `ops/workflow.py:417` — the tooltip "Generate reuses it while the scene is unchanged" contradicts the code, which always re-renders.
- **22.** `ui/studio.py:378` and `graph/tree.py:355` — "NO CAMERA" in capitals inside a facts line. Fix: an alert row, "No scene camera: add one and make it active".
- **23.** `ops/generate.py:315` — **✕ Cancel** a queued or running job with no confirm; the student loses their queue place. Fix: `invoke_confirm` "Cancel Job?".
- **24.** Cut labels in the default sidebar (Phase C/D [R]):
  - chips: "Improve the instruction w…", "Grayscale (C…";
  - keyframe box: "Generate Key…";
  - the Server panel shows two identical "FLUX.3 Klein distil…" rows;
  - nodes: "Picture to ed…ed (needed)", "Show when rea…".
  - Fix: `short_label` everywhere, wrap at the real width, and cut model names in the middle.
- **25.** `ui/studio.py:432`, `ops/workflow.py:373` — old graph events stay in the bar for ever ("Cycles render done: 20261115_…png"), even when disconnected [R D2]. Fix: notes expire, and graph notes stay out of the bar.
- **26.** The ComfyQ window's Text Editor keeps a stale prompt ("line one / line two") after unlinking and across node selections.
- **27.** `ui/studio.py:164` — the render-style menu sits far from the inputs it controls; "Workbench render" (`props.py:39`) is jargon.
- **28.** The mask preview shows the zone as a dark checkerboard on grey, hard to see. Fix: a red overlay (`passes/segment.py` already makes one).
- **29.** A 3D or splat result in the viewer shows "Still image · 0×0" over a checkerboard. Fix: a thumbnail or "3D model: Import to see it".
- **30.** Job-card Import has no Place / Size controls. Fix: an Import dialog with the Import node's options.

### Graph
- **31.** `graph/tree.py:761` — workflow-node notes are not wrapped; "Loading its model in ComfyQ… 1:23 (usually 1 to 2 min)" and raw job errors are cut.
- **32.** Raw ids and internal node names shown to students:
  - `tree.py:627` — workflow id shown as the header when disconnected;
  - `run.py:293, 654, 664, 679, 684, 721, 726` — "Workflow.002";
  - `preset_ops.py:50, 382, 417` — raw ids in tooltips and status lines; "(not served)" is cut off.
- **33.** `ui.py:51` — every Add-menu item has the same tooltip ("Add this node in the middle of the view"). New nodes (Material Board, Join Text, Film, Media Folder) explain nothing.
- **34.** Work done in draw:
  - `tree.py:432-442` — Media Folder scans its folder;
  - `tree.py:564` / `run.py:586` — Join Text reads files;
  - `preset_ops.py:296-304` — the Presets menu globs (shared or network folder);
  - `prefs.py:166, 225` — `profiles.toml` parsed [R];
  - `studio.py:487, 531` — `is_file` and JSON parsing per job card;
  - `ui.py:627-636` — node history.
- **35.** `tree.py:1045` — **Replace Previous Import** is on by default and deletes objects the student may have edited since. Fix: off by default, or confirm when objects would be removed.
- **36.** `tree.py:753, 746, 831` — "Not Served" / "Unknown workflow" / a raw error, without the next step. Fix: "Not Served: Ask the Teacher", "Model Gone from Server: Change Model", a "Press ⟳ to try again" line.
- **37.** `run.py:195-198` — Solo waiting for other people's jobs leaves the node on "Waiting for its inputs".
- **38.** `outputs.py:279, 294` — "Saved C:\…\very\long\path" on a node. Fix: the file name on the node, the full path in the report.
- **39.** `tree.py:305, 333` — **every Scene Render / Mask node shows the same global thumbnail.** With several shots, they all show the last render.
- **40.** The graph header is 12 unlabelled icons at the default window width.
- **41.** New nodes are placed on top of existing ones.

### Connecting and the teacher
- **42.** Admin mode is a dead end with no auto-retry. Fix: poll `/admin/mode`, show "Waiting for the teacher to start serving…", and connect by itself.
- **43.** `ops/generate.py:215`, `client/api.py:140` — an empty served set (during a model switch) lets any model book.
- **44.** The Server panel can load a model but **not close a lane**, has no "not serving" state with a Start action, and no Solo indicator. Its errors are raw exception text (`server.py:70`), and its fallbacks are raw ids (`:82, :100`).
- **45.** Making a shot needs the hidden Ctrl+B. Fix: an **Add Shot Here** button next to the Shot field.
- **46.** `help/student.html` is silent on masking, the start frame, multi-shot and 3D placement.

## Severity 1 and 0 — mechanical batch (phase 6, one sweep)

| Theme | Where (file:line) |
|---|---|
| Hex job ids [R] | `ui/player.py:164, 173` "AI result · job 345f…"; `ops/generate.py:143` "Booked 1a2b…"; "ComfyQ 24391878" collections |
| Raw parameter keys [R] | `ui/studio.py:527` "Sent · loadimage_image_76" when specs are not loaded; `studio.py:259` / `ops/workflow.py:477` text name "ComfyQ prompt · clip_text_encode_6_te" |
| "workflow" instead of "model" | `studio.py:277`; `ops/workflow.py:203, 223, 256`; `ops/generate.py:105, 170`; `ops/connection.py:88`; `props.py:45`; `graph/ui.py:121` |
| Abbreviations | "(s)" `graph/tree.py:681` [R]; "e.g." `tree.py:1103`; "min" `run.py:261, 273`; "VRAM ?" `ops/workflow.py:36`; "GLB" `studio.py:317`, `props.py:48`; "fps" `ops/workflow.py:256` |
| Hard-coded shortcuts in tooltips [R] | `prefs.py:111` Ctrl+Shift+Q; `graph/tree.py:242` Ctrl+B |
| Tooltips without a verb, outdated or shared | the player controls `player.py:199`; the position slider has none `:352`; `panel.py:79` (a docstring becomes the Queue tooltip); `ops/workflow.py:221, 231` (menu docstrings); Render Scene/Mask share one `ops/workflow.py:294`; Results Folder / job folder share one `ops/generate.py:374`; `ops/results.py:59` (says Sequencer); `graph/ui.py:355` Run Graph (out of date since D3) [R]; `ui.py:131, 147` (omit the new nodes); `ui.py:470` (4 meanings); `tree.py:454, 1088, 1090, 1093, 1128` |
| Title case | `tree.py:1013, 1040, 1082, 1118` "Show when ready"…; `tree.py:1185` SOURCE_LABEL; `tree.py:507, 881`; `props.py:115`; `results.py:68`; `ops/workflow.py:207, 210`; `preset_ops.py:451, 525` |
| Greyed with no reason | `prefs.py:183` (Port), `:199` (Agents May Load Models); `graph/ui.py:169` Clear Graph; `ui.py:57, 243`; `ops/workflow.py:823` |
| Missing `self.report` | `prefs.py:233` set profile; `player.py:240`; `ops/workflow.py` camera_from_view when the setting is gone; `graph/ui.py:178` Clear Graph; `ui.py:418` (can be empty) |
| Nested boxes rather than sub-panels | `ui/studio.py:120` with `242, 264, 330, 377`; `ui/server.py:78, 89, 104` |
| Icons (one icon, one meaning) | `studio.py:339` keyframe chip shows no check mark when served; `player.py:303` RENDER_ANIMATION on stills; `tree.py:931` IMPORT for "loads on run"; `preset_ops.py:527, 543` icons on every menu entry and FILE_FOLDER used twice; 8 icons missing from the docs/UI.md table |
| Odd wording | `props.py:102` enum tooltips show raw option values (LoRA file names); `tree.py:526-546` Board property names differ from the labels; `tree.py:1140`; `ui.py:676` "ComfyQ Graph" (no verb); `ui.py:895` "Solo"; `ops/connection.py:28` "Your"; Test Connection blocks the UI for up to 4 s and shows a raw WinError (`prefs.py:248-259`) |
| Unused operators | `ops/workflow.py:240` random_seed; `ops/generate.py:355` import_result |

## What works and stays
- Served check marks, with the red "Not served right now: ask the teacher" and Generate greyed with the reason.
- Settings kept per model; Match to Scene shown pressed.
- The name · server row; the name asked at the first Connect.
- Job cards: Sent rows, Done time, Show / Open / Save As / Import ✓, Retry, Remove.
- The plain "not answering" message and the admin-mode explanation.
- Preferences in sections; the Server panel's GPU bar, queue and people.
- Presets menu sections and the How to use frames; Media Folder counts.
- The camera block (shot words, From 3D View, UNDO, reports).
- Teacher controls locked behind the admin password; confirmations on Clear Graph, Load Model, Solo and Render Film.
- No draw() writes ID data; icon-only buttons have tooltips; keymaps are clean.

## Requirements for Draw & Mask (journeys 6–7), carried into phases 3–5
1. **One click from the input.**
   - Every image input and video start / last frame offers **Sketch** (Grease Pencil in the camera view) and **Paint Frame** (Image Editor Paint).
   - The graph gets a Sketch node and Image › New / Paint....
   - The click switches the workspace mode and says the next step on one line.
2. **Canvas the size of the job:** the scene aspect fitted to the upload box (`render.fit_size`). The keyframe model is matched too (#13).
3. **Start from the render, the AI keyframe or any result,** as a copy. Never paint on "ComfyQ render preview".
4. **Sketch over the clay scene, or sketch only on white.**
   - Workbench and EEVEE render Grease Pencil (checked on 5.3); Cycles does not, so warn.
   - "Keep Ink Colour" in the clay style.
5. **Edits make results out of date:**
   - Grease Pencil strokes in the scene fingerprint;
   - pixel hashes for images (#16);
   - Grease Pencil edits count as user edits in the depsgraph tracker.
6. **Show exactly what is sent,** at upload size, fitted to the view (#5).
7. **A start frame needs no second model:** a sketch, painted frame or earlier result (#14).
8. **Never lose a drawing:**
   - results never replace an editor in Paint or Mask mode (#6);
   - canvases and sketches are saved in the .blend;
   - Undo works as in Blender.
9. **A mask on any picture,** sent as that picture with alpha 0 in the zone at upload size, so picture and zone line up (#18).
10. **Four ways, one mask:**
    - Mask Zone (box, circle, free shape, feather, invert, rasterized at upload size);
    - Paint Zone (Erase / Add Alpha);
    - Selected Objects with Lock Selection (#19);
    - Surfaces (SAM3) by name, for students.
11. **The zone is visible:** a red overlay in the Image Editor, the viewer and the node thumbnail, plus the "% of the picture" figure (#28).
12. **Nothing to repaint = refuse,** with the reason: an empty mask, a full mask, or an opaque Scene Render.
13. **One thumbnail per node** (#39).
14. **The zone belongs to its picture's frame and shot.** Masks that move over time come later (D5).
15. **Honest presets and help** (#17, #46). New class presets: *Sketch → image*, *Sketch → video*, *Mask a zone → inpaint*.
16. **The ComfyQ workspace:**
    - appended from a bundled .blend;
    - a Frame · Draw · Mask · Graph mode strip;
    - never reuses an editor the student is working in;
    - survives reopening the file (gotchas 10, 17, 26);
    - labels that fit the sidebar; status lines that expire.
17. **Graph and agent parity:** Sketch, Paint Frame and Mask Zone as nodes and as MCP tools.
18. **Version checks at run time:** greyed with the reason when this Blender lacks the tool (Grease Pencil v3, Mask, splats). CLASS.md says which Blender the class PCs need (#10).

## Severity-3 fixes done (2026-11-15, step 3)
Verified: smoke PASSED on 5.3 and 5.2; GUI `-Ui -Version 5.3` passed with no draw errors; screenshots looked at
(`11b_access`, `11b_server`, lost job card, fitted render).

| # | Fix |
|---|---|
| 1 | No profiles file and the default address: the error says "No class server is set on this PC…", and the bar offers **Server Address...** (writes the Preferences URL, then connects). |
| 2 | A locked server: **Access Password...** (kept in the session only, never in a file). |
| 3 | Socket down while connected: a red line "Connection to the class server lost: reconnecting by itself… m:ss", plus **Reconnect**. |
| 4 | **Changed from the proposal.** Leaving imports out of every render would break classes that import a result on purpose to render it (the supercar scene). Instead, *Beside the scene* also stays **out of the camera's view** (`_out_of_view`). An import still where the import put it is left out of the scene fingerprint (`untouched_import`); once moved, it counts. |
| 5 | Every new picture is fitted to the viewer (`player.fit_view_soon`). |
| 6 | Results go only to the ComfyQ viewer, or to an Image Editor that shows nothing or a ComfyQ picture. Never one in Paint or Mask mode, pinned, or showing the student's own image (`player.free_for_results`). |
| 7 | Sent files are labelled by kind (image, animation, 3D model, sound). A 3D model or sound gets **Open**, not Show. Show never crashes on a non-picture. |
| 8 | `import_report`: imported / kept / failed. Failures are warnings, and the card is marked imported only when something came in. |
| 9 | Unknown jobs (404) become **lost** ("Not on the server any more", finished, removable). New Queue popover button **Remove Finished Jobs**; the popover got a real tooltip. |
| 10 | **Changed from the proposal.** On Blender 5.2 a splat node's result is the job's `.glb` mesh twin, so the splat presets still bring something in. The docs say the class PCs need 5.3 for splats. |
| 11 | Output nodes show a failure in red with ERROR, wrapped. *Saved* shows the file name. |
| 12 | Save Graph as Preset refuses to overwrite unless **Replace the Existing Preset** is ticked. An OSError is reported, not raised. |
| 17 | Inpaint preset descriptions say "select the objects", in the generator and in the 2 committed presets. |

Left for phases 3–5 (the workspace, Draw and Mask): 13, 14, 15, 16, 18, 19.

**Mask tool (2026-11-15, asked by the user before the workspace: "selection in 3D and also a mask tool in the toolbar").** The **ComfyQ Mask** tool (`ui/zone_tool.py`):
- click picks objects;
- drag draws a box, Shift+drag a free shape;
- Ctrl cuts.

It draws through the scene camera, with a red overlay and header settings Grow / Feather / Invert / Show / Clear. There is one zone per scene (`zone.py` bpy-free + unit tests, `passes/zone_scene.py`, the raster in `render.zone_coverage`). This solves **#19**: the zone no longer follows the current selection. It also covers requirements 10–11 for objects and shapes, and 'grow'.

The Mask node became **Mask Zone**. The source became *Render + Mask Zone*. GPU-verified on `mask_demo_zone.blend` (sunflowers spilling above the old vase).

**Picture Mask (2026-11-15):** the zone on ANY picture: the graph node *Picture Mask* (`graph/picture_mask_ui.py`,
`passes/picture_mask.py`). It finds objects by name with SAM3 and keeps them as a ticked list; clicks, boxes and
free shapes are drawn with the **ComfyQ Picture Mask** tool in the Image Editor; Grow / Feather / Invert as in 3D.
SAM3 runs only when the picture or the words change. This solves **#18** in the graph; the bar has no picture
source for masks yet. GPU-verified (a Chesterfield replacing only the armchair). ComfyQ's SAM3 takes words, not
clicks (gotcha 36), so a click picks among what was found; a click-point SAM workflow is on b2renger's list.

Still open for masking:
- a picture source for masks in the bar (the graph has Picture Mask);
- Paint Zone in the Image Editor;
- SAM3 picking;
- one thumbnail per node (#39).

## Phase 2b results — spikes on Blender 5.3 (2026-11-15, `tools/draw_mask_spikes.py`, `live_check/draw_mask_spikes/`)
| Spike | Result | Consequence for the build |
|---|---|---|
| S1 Grease Pencil sketch → the plugin's clay pass | ✅ A GP object parented to the camera, 2 m ahead, is in the clay pass (0.4 s): grey ink lines over the clay scene, aligned with the camera. "Sketch only": hiding the other objects works, but the background stays the pass's dark grey (the world colour is not used). | Sketch source = GP collection in the pass. "Sketch Only" needs a white-background option in `render.structure_pass`. Coloured ink becomes grey in Clay (expected; keep colours with the Material style). |
| S2 Mask data-block → alpha | ✅ Blender's **Compositor** (Mask node → output, 0.1 s at 1280×720) and a numpy fill of the splines agree on **99.99 %** of pixels. The axis rule was found by the spike: mask y is width-normalised around the centre, `frame_y = 0.5 + (y − 0.5)·W/H` (first try without it: 87 %). | Use the Compositor (exact Blender result, feather and layer blending for free), with the numpy fill as a bpy-free, unit-tested fallback and checker. `Scene.compositing_node_group` is deprecated for Blender 6.0: keep the call in one place. |
| S3 painted alpha (Erase Alpha) → upload | ✅ `render.image_pass` keeps the alpha exactly (8.68 % transparent in, 8.68 % out). | Paint Zone needs no conversion: the painted image IS the ComfyQ mask. |
| S4 a ComfyQ workspace in a .blend | ⚠ `libraries.write` stores a workspace with its layout, and loading it back works. But **area-type changes made in background mode are not applied** (the copy kept Properties / Outliner). | Build the workspace .blend once in a VISIBLE Blender (`tools/make_workspace_blend.py`, timers: gotchas 10 / 26) and ship it in the extension; append it with `workspace.append_activate`. |
| S5 pixel hash of a 1920×1080 image | ✅ 4.7 ms (`foreach_get` + sha1 of every 97th value) | Fine from the timer for the Image / Paint Frame nodes (fixes #16). |

## Proposed order (phases 2–7 of the plan, adjusted by these findings)
1. **Phase 2a — fix the test harness first:** T1–T4, so later screenshots can be trusted.
2. **Phase 2b — spikes for Draw & Mask:**
   - Grease Pencil sketch pass;
   - Mask rasterizer;
   - Erase-Alpha paint → alpha;
   - workspace from a bundled .blend;
   - pixel hash.
3. **Phase 6-early — severity 3 not tied to the new feature,** because they hurt in class today:
   - 1–3 (connect);
   - 4–8 (results go into the next render, viewer fit, editor takeover, the .glb Show crash, honest import reports);
   - 9 (ghost jobs);
   - 10 (5.2 vs splats);
   - 11–12 (green check on failure, preset overwrite);
   - 17 (honest inpaint presets).
4. **Phases 3–5 — workspace, Draw, Mask:** covers 13–16, 18, 19, 28, 39 and requirements 1–18.
5. **Phase 6 — the severity 2 list, then the mechanical sweep** (severity 1/0) in one pass. Then `-Ui` and docs/UI.md.
6. **Phase 7 — class presets, docs, MCP tools, GPU checks.**

## Phase 6, first pass (2026-11-16)
Done (smoke + GUI `-Ui` 136 checks, no drawing errors):
- **Safety:** #23 *Cancel Job?* asks (queue place / work lost); #35 Import › Replace keeps objects moved since the last
  import; #43 an empty served list received from ComfyQ blocks booking ("No model is served right now…").
- **Feedback:** #25 bar notes expire after 2 minutes and on disconnect; #26 unlinking a prompt clears it from the
  Text Editors (ComfyQ-made texts deleted); #29 a 3D / splat result in the viewer says "Import it to see it", not
  "0×0"; #31 node notes and job errors wrapped, with "Press ⟳ to try again"; #36 "Not Served: Ask the Teacher",
  "Model Gone from Server: Change Model"; #37 Solo waiting for other people's jobs says so on the node; #38 (already
  the file name); #42 admin mode: waits for the teacher to start serving and connects by itself (30 min).
- **Wording:** #20 "Scene animation" / "Render & Play Animation" (not clay whatever the style); #21 the animation
  tooltip told the truth; #22 "No scene camera: add one and make it active" as an alert line; #27 no "Workbench" jargon;
  #32 node titles, not "Workflow.002", in run messages; "Model (Not Connected)" instead of a raw id; #33 each Add-menu
  item's tooltip is its node's own description; mechanical: job ids → model names ("Booked: …", "AI result · …"),
  "workflow" → "model" in three messages, no hard-coded Ctrl+B / Ctrl+Shift+Q.
- **Code health:** #34 the Presets menu lists folders again only when their time changes; Media Folder scans at most
  every 15 s while drawing.
- **Layout:** #39 each Scene Render / Mask / Sketch node shows its own render; #41 new nodes never land on another;
  #45 **Add Shot Here** (+ next to Shot).
- #28 (red mask preview) was done in phase 5.

Still open: #24 cut labels (job-card errors are cut mid-word too), #30 an Import dialog on job cards, #40 the graph
header's unlabelled icons, #44 the Server panel (close a lane, not-serving state, Solo indicator, raw errors), #46
(phase 7), and the rest of the severity 1/0 sweep (title case, tooltips, nested boxes, icon table).
