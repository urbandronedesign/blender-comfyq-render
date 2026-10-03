# Plan — ComfyQ node graph (Moodboard-style UI)

Status: **N0 + N1 + N2 done and verified in Blender 5.2.2 LTS** (2026-10-03); **N3 (look) is next** — task list in CLAUDE.md › Next steps. Decisions: Node Editor (option A); the bar stays next to
the graph; settings are chips inside each node only (no linkable setting sockets). Goal: the same way of working as Mixar's Moodboard
(cards wired together: image → *Generate Video* → …) inside the bridge, with nodes built from what
ComfyQ serves, exposing every input and parameter of each workflow.

## 1. Where the graph lives — decision

| Option | Look | Works in plain Blender | Risk | Verdict |
|---|---|---|---|---|
| **A. Blender Node Editor + our own node tree** (`bpy.types.NodeTree`) | Close: dot grid, wires, coloured sockets, big cards with previews; Blender's node frame and theme | Yes | Low: public API, stable since 2.8 | **Recommended** |
| B. Custom canvas drawn with `gpu`/`blf` in an editor | Can copy the Moodboard exactly | Yes | High: we re-implement selection, drag, zoom, wires, text input, undo | Only if A is not close enough |
| C. Plug into Mixar's Moodboard | Identical | **No** | Moodboard is Mixar's native `MIXIE` space + internal `mixar.modules.moodboard`; breaks on Mixar updates; against our "public bpy only" rule | Only with an extension API from Mixar's team |

The Moodboard stays usable next to us. A later "Send to Moodboard" button would need a public
operator from Mixar (to ask them; `mixar.render_camera_to_moodboard` exists but is internal).

## 2. What it looks like (screenshot → Node Editor)

| Moodboard | ComfyQ graph |
|---|---|
| Top bar *Image to 3D · Video Generation · Generate Image · Upscale Video · Generate Splat · +* | Node Editor header (only in the ComfyQ tree): one button per category (**Image · Video · 3D · Splats · Audio · Tools**) + **Scene** (sources) + **+** (everything). Each opens the workflow menu: *Served now* first, VRAM badge, never hidden. Adds the node at the view centre |
| *Clear Moodboard* | **Clear graph** (header right, asks first) |
| Left tools (open, text, draw…) | **Open image/video**, **Prompt**, **Note**; Blender's own annotate tool and frames for drawing/grouping |
| Image card | **Image** node with a large thumbnail (Blender preview, ~256 px), Show / Open |
| *Generate Video* card, "Describe what you want to create" | **Workflow** node: wide card, big prompt field (multi-line via Text block), coloured sockets left (image = cyan, video = teal, mask = grey, audio = purple, text = yellow), result socket right, Generate button, result thumbnail, progress |
| Wire image → card | Native node links, type-checked |

Limits (honest): Blender draws the node frame (rectangular header, user's theme); previews are
thumbnail-sized; a video cannot play *inside* a node (thumbnail + ▶ plays it in the ComfyQ viewer).

## 3. Node types

**Sources** (rendered in Mixar, on the main thread)
- **Scene Render** — style (clay/matcap/material), frame (start / end / current / number). Out: Image
- **Scene Animation** — clay MP4 of the frame range through the camera. Out: Video
- **Selection Mask** — render with the selected objects transparent. Out: Mask
- **Image** / **Video** / **Audio** — Blender image or file. Out: matching kind
- **Prompt** — text (multi-line). Out: Text
- later (P2.3): **Exact Depth / Normal / Lines** passes

**Workflow node** — one node class, content generated from the chosen workflow's parameters (`ParamSpec`):
- *Inputs*: a socket per media parameter (image, mask, video, audio) and per prompt/text parameter
  (unlinked text socket = inline field). Hidden live when the workflow's own rules disable them
  (`is_disabled`), links kept.
- *Settings*: numbers, lists, toggles, seed (+ random) drawn as chips in the node; all of them in the
  sidebar for the selected node. Kept per workflow (switching model keeps tweaks).
- *Outputs*: by result kind — Image / Video / 3D model / Splat / Audio / Text (see §5).
- *Header*: name, ▶ served / red "not served", VRAM badge. *Body*: status (queued, progress bar, done,
  failed), result thumbnail, **Generate**, **Cancel**, **Show**, **Import**, **Match to scene** when it has
  timing/size settings.
- *Model menu inside the node*: swap to another workflow; links stay where the kind still fits.

**Outputs**
- **Viewer** — shows/plays what is linked in the ComfyQ window player
- **Import to Scene** — image → Image Editor, video/audio → *ComfyQ Results* scene, GLB/PLY → collection
- **Save** — copy to a folder

Native **Reroute** and **Frame** work as usual.

## 4. Nodes follow what the server serves

- Palette and node headers update on Connect, Refresh and every `state_patch` lane change
  (served badge turns ▶ / red live).
- Each workflow node stores a hash of its parameter list. When ComfyQ's workflow changes (parameter
  added, removed, kind changed) the node rebuilds its sockets: links on parameters that still exist are
  kept, dropped ones are reported on the node ("input removed by the workflow update").
- Settings load through the same `ensure_loaded` mechanism as the bar (after connect / file load).
- Before running a chain: list the nodes whose workflow is not served (run is refused by ComfyQ otherwise).

## 5. Output kinds (workflows don't declare them yet)

Phase 1: inferred from `category` + `tags` + id — `t2i / image-edit / i2i` → Image; `i2v / video-edit` →
Video; `3d` → Model or Splat (tag/id `splat`, `gaussian`); `audio` → Audio; tools by id/tags (upscale
video vs image, depth/normal → Image, describe → Text). Corrected and remembered from the first real result
(file extension). Phase 2 (ComfyQ PR): add `outputs: [{kind, name}]` to `meta.json` and `GET /workflows`.

## 6. Running a graph

- **Generate on a node** runs it and whatever upstream is not up to date. **Run graph** runs every
  output/leaf node.
- Graph → dependency order. Sources render on the main thread; each workflow node = one ComfyQ job;
  a downstream job is booked when its upstream results are downloaded (generalises today's AI-keyframe
  chain). Independent branches book in parallel (ComfyQ queues them).
- **Caching**: each node keeps job id, result files and a hash of its inputs + settings; unchanged nodes
  are not re-run (same idea as the animation pass staleness), **Force** re-runs. Scene sources always
  re-render at Generate (as today).
- **Fan-out**: one result into several workflows (one keyframe → three video models) in one run.
- **Type checks** at link time (image ↛ video input); later an *Extract frame* node for video → image.
- **Cancel** on a node cancels its job and stops what waits on it.
- Phase 2: ComfyQ `job_deps` so a chain continues server-side when Mixar is closed.

## 7. Code reuse (no change to the ComfyQ client)

- `client/` unchanged (Socket.IO phase 1). Execution engine in a bpy-free `graph.py` (order, dirty
  hashes, type rules) → unit-tested like `match.py`.
- Refactor `ops/generate.py`: `_collect` reads from a *parameter store* (the bar's scene store or a node's
  store) and gets linked upstream files for media inputs. The bar and the graph share renders, upload,
  booking, tracking, download, import.
- Nodes store values with the existing `ComfyQParamValue` group; job records stay in `scene.comfyq.jobs`
  (the job cards keep working) with a link to the node.
- The **bar stays** (quick one-node use). **Open as graph** turns the current bar setup into nodes
  (e.g. Scene Animation → V2V node with the prompt).

## 8. ComfyQ window, graph mode

```
┌────────────────────────────────┬───────────────┐
│ Node Editor (ComfyQ graph)     │ Viewer+Player │
│                                ├───────────────┤
│                                │ selected node │
├────────────────────────────────┴───────────────┤
│ Text Editor: prompt of the selected node        │
└─────────────────────────────────────────────────┘
```
A *Bar / Graph* switch in the window; the graph is saved in the .blend (several graphs per file).

## 9. Milestones

| # | Scope | Done when |
|---|---|---|
| N0 spike (½–1 day) | Node tree + Image, Scene Animation, Workflow node built from the served V2V workflow; thumbnail in a node; header buttons | Visible and editable in **Mixar** (its Editor Type menu shows the ComfyQ graph) and Blender 5.1 |
| N1 | All source nodes, workflow node generation, served-state updates, socket rebuild on workflow change, output kind inference | Every one of the 66 workflows becomes a node with the right sockets |
| N2 | Engine: single node, chain, fan-out, caching, cancel, served check | Scene Animation → V2V and Scene Render → image → V2V (start image) run from the graph |
| N3 | Look: category toolbar, socket colours, big cards, previews, Clear graph, graph mode of the ComfyQ window, *Open as graph* | Side-by-side with the Moodboard screenshot, accepted by the user |
| N4 | Viewer / Import / Save nodes, sidebar for the selected node, tests (`graph.py` unit tests; `ui_check` adds nodes, links, runs and cancels) | `smoke_test.ps1 -Ui -Book` green |
| N5 | docs/UI.md graph section; class presets: *Clay animation → V2V*, *Keyframe → V2V*, *Image → 3D*, *Image → upscale* | Teacher can start a class from a preset |

Phase-2 ComfyQ PRs that help: `outputs` in meta/`/workflows`, `job_deps`.

## 10. Decisions (user, 2026-10-03)
1. Host: **Node Editor** (option A).
2. The generation **bar stays** next to the graph.
3. Settings: **chips inside each node only** (and in the sidebar for the selected node); no setting sockets.

## 11. N0 results (2026-10-03)
- Code: `extension/comfyq_bridge/graph/` — `kinds.py` (bpy-free: socket kinds, colours, link rules,
  output-kind inference, parameter signature), `tree.py` (tree, 7 socket types, Image / Scene Render /
  Scene Animation / Prompt / Workflow nodes), `ui.py` (header toolbar, Shift+A entries, add/clear/open
  operators, timer `sync_all`). Bar: new graph icon (`comfyq.open_graph`).
- Verified: unit tests (all 66 local workflows → 428 params map to 161 sockets + 267 chips, every
  output kind known); headless smoke in **Mixar and Blender 5.1** (sockets in order, chips stored, link
  rule, rebuild after a workflow change keeps links, saved/reloaded with the file, reinstall with a graph
  open); GUI check in Mixar 75/75 (Mixar's Node Editor accepts the tree type, toolbar adds nodes, wiring,
  Clear). Blender's *GUI* was not run (Node Editor is stock Blender there).
- Found and fixed: reinstalling the add-on with a graph in the file **crashed Blender** (tree update
  during class registration) → `tree.REGISTERING` guard; socket kinds read from `bl_idname`.
- For N3 (look): node headers are drawn red by Mixar's theme for add-on nodes; the Node Editor sidebar
  shows Blender's *Group / Group Sockets* panels for our tree; toolbar labels truncate when the header is
  crowded; Blender draws input sockets below the node's settings (fixed layout).
- Not yet: running from the graph (N2), multi-line prompts in nodes, model swap inside a node, the other
  source kinds (mask, video/audio files).

## 12. N1 results (2026-10-03)
- New source nodes: **Selection Mask**, **Video File**, **Audio File**; Scene Render / Mask / Scene Animation
  previews now use the node's own style and frame (`preview_pass` / `preview_animation` got `style`, `frame`).
- Workflow node: **Change model** (searchable list of every workflow, served first, VRAM badge; settings kept
  per model; links restored on inputs of the same kind), **“+” on each unlinked input** (adds the matching
  source node — scene render at the first/last frame as the input asks, clay animation, mask, prompt, audio —
  and links it), **multi-line prompts** (Text block in the ComfyQ window's Text Editor, wrapped preview,
  Edit / ✕) in workflow and Prompt nodes, every setting one per row in the sidebar (Node tab) with the
  workflow's description. Node values are now filtered per workflow (a key shared by two models no longer mixes).
- Verified: headless smoke in Mixar and Blender 5.1 (**all 66 workflows build as nodes with the right
  sockets and output**, change model keeps links and settings, “+” adds and links, prompt node multi-line);
  GUI check in Mixar 77/77, no drawing errors.
- Known: no thumbnail for videos (Blender makes no previews for movies; N3 could show a frame), red headers
  and *Group* sidebar panels (N3).

## 13. N2 results (2026-10-03)
- `graph/engine.py` (bpy-free, 11 unit tests): dependency order, node hash (own settings + everything
  upstream), plan (target always; upstream only without an up-to-date result; anything downstream of a
  re-run), readiness, leaves, cycle refusal.
- `graph/run.py`: a run renders the scene sources a job needs (fresh, once per run even when several
  nodes share them), books workflow nodes in order through the bar's own `ops.generate.book()`, gives
  each downstream node its upstream result (images resized to ComfyQ's limits; text results read as
  prompts), records jobs in the scene (purpose `graph`, no auto-import), shows the target's result in the
  ComfyQ viewer at the end. Refuses with a message: not connected, unserved workflows, unlinked needed
  inputs, settings still loading, loops, a run already going.
- Node: **Generate** (and ⟳ = re-run upstream too), status / progress / ✕ cancel (stops what depends on
  it), result thumbnail (images) and Show / Open / Import; header **Run Graph** (the graph's last nodes).
  A job finished while the file was closed is picked up again (`_adopt_result`).
- Found and fixed (GUI check: re-running B re-ran A every time): "has the scene changed?" was first
  answered from depsgraph updates, but our own renders (frame steps of the animated camera, mask colours)
  and **Mixar's UI** (it re-tags objects with shading updates while it draws) look like edits. Scene
  sources now hash what the render depends on instead — `render.scene_fingerprint()`: camera, frame
  range, resolution, each renderable object's placement (keyframes when animated), mesh shape, modifiers,
  constraints, materials in Material style. Scrubbing the timeline, selection, custom properties and UI
  activity do not change it. (`render.own_changes()` also brackets every pass, which keeps the bar's
  animation cache honest.)
- Cancelling a running job: ComfyQ stores it as failed / "cancelled" → now read as **Cancelled**
  everywhere (job cards, nodes).
- Verified: headless smoke in Mixar and Blender 5.1 (plan, reuse, B gets A's result, edits re-run,
  results arriving do not, refusals); live in Mixar on the GPU: Scene Animation → V2V A → V2V B
  (B received A's MP4; ComfyQ stored each node's prompt), cancel from the node.
- **Confirmed live in Blender 5.2.2 LTS** (2026-10-03): chain A → B, *Generate again reuses A* ("Running
  1 job(s): Workflow.001 (reusing Workflow)"), ✕ cancels; and a 5 s run of the user's project from a node.
- Not verified live: Scene Render → *image workflow* → V2V start image, because no image workflow is
  served on this ComfyQ (the mechanics are covered headless: image results are resized and uploaded).

## 14. N3 results (2026-10-03, Blender 5.2.2 LTS)
- **Header colour:** Blender's default theme draws our headers maroon too: every Python node is class
  *Input* (`Node.color_tag` read-only; `color_tag`, `bl_color_tag`, `NodeCustomGroup` all read back
  INPUT — probed). Only the user's theme could change it (and would recolour shader inputs): accepted.
- **Sidebar:** `ComfyQGraphTree.bl_use_group_interface = False` → Blender's Group tab keeps only
  Name/Description (no Group Sockets, Color Tag, Node Width) and the Make Group / Ungroup entries go.
- **Header:** stepped by width (labels → icons → one + menu), 🔍 search of every workflow, 🗑 clear.
  Room measured: about `(width − 200) / 2 − 200` px (Blender centres the tree selector).
- **Video thumbnails:** `bpy.utils.previews` `load(…, 'MOVIE')` (Blender's file-browser thumbnailer)
  for Scene Animation (last clay pass), Video File and video results; loaded by the timer, keyed by
  path + mtime + size; the draw only looks them up.
- **Graph mode of the ComfyQ window** (§8): the graph icon splits the window's viewer, the graph takes
  the larger part (left 62%), viewer + bar the rest, prompt editor below. The window's bar shows a
  **Graph** switch. Selecting a node shows its output in the viewer and its multi-line prompt below.
  (Split areas only get their positions on the next draw: the graph side is chosen from a timer.)
- **Open as Graph** (bar › Actions): the bar's model, settings, prompt (Text copied) and one source
  node per input, as the bar would feed it (AI keyframe → the keyframe workflow's node in front).
- **Sockets below the buttons:** Blender's fixed layout for Python nodes; accepted (the sidebar › Node
  tab shows the full settings of the selected node).
- Verified: headless smoke (thumbnail 256×144, Open as graph feeds each input like the bar) and
  `smoke_test.ps1 -Ui` **86 / 86** in Blender 5.2 (graph mode, switch both ways, thumbnail with an icon,
  viewer follows the node, Open as graph). Waiting for the user's side-by-side with Moodboard.

## 15. N4 results (2026-10-03, Blender 5.2.2 LTS)
- **Output nodes** (`graph/outputs.py`, socket kind `any`): Viewer (*Show when ready* on), Import to
  Scene (*Replace previous import* on: removes the strips / objects / collections its last import made,
  found by diffing before/after; images stay), Save (folder, name pattern, no overwrite). They act on a
  workflow node's result or a source's last render (Scene Render / Mask preview, clay animation, image,
  file, prompt text). *… when ready*: the timer acts once per new result (path + mtime kept on the node).
- **Generate on an output node**: `engine.feeding_workflows` + `plan(..., reuse_targets=True)` → only what
  is out of date upstream runs, then the node acts (`Run.after`); all up to date → it just acts.
- **Reroute / Frame**: added from the Output menu; `tree.upstream()` / `link_source()` look through
  reroutes (runs, type checks, outputs); muted links feed nothing.
- **Result history** per workflow node (last 20) + sidebar **ComfyQ** tab (description, settings, job
  details, files sent, earlier results with *Use*).
- Tests: unit (output nodes never book, feeding workflows, reuse_targets, `any` accepts every kind);
  smoke (import/replace, save naming, when-ready once per result, source-only Generate, text viewer,
  reroutes incl. refusal, history/Use); ui_check N4 steps; `-Book`: B's result → Viewer / Import / Save,
  then Generate on the Viewer books nothing.

## 16. N5 results (2026-10-03, Blender 5.2.2 LTS)
- **Built-in class presets** (`graph/presets.py`, bpy-free): roles instead of workflow ids
  (`Role(categories, needs, prefer, avoid)`), `pick()` = takes the needed inputs, served first, then
  preference. Unit-tested on the 66 workflows (best picks: LTX 2.5 IC-LoRA union, Qwen 2.1 control
  depth, Trellis2 textured mesh, SeedVR2 7B image upscale; a served workflow of the role wins).
- `graph/preset_ops.py`: loads the candidates' parameters, then builds (scene matching on workflow
  nodes, the workflow's example prompt in the shared Prompt node, *enable* switches a start image on,
  unused sources dropped). **Saved presets**: the graph as JSON (nodes, models, settings, prompts,
  frames, links), read from `%APPDATA%\comfyq-bridge\presets` and `COMFYQ_BRIDGE_PRESETS` (shared).
- Header **Presets** menu; teacher guide **docs/CLASS.md**.
- Fixed on the way: Blender 5.2 warns on `//` paths unless the property has
  `PATH_SUPPORTS_BLEND_RELATIVE` (`props.REL_PATH`, used by the Save folder and file paths).
- Verified: unit (pick + catalogue), headless smoke (all 5 presets built on the live ComfyQ, keyframe
  feeds the switched-on start image, one prompt → both, saved preset round trip from a shared folder),
  `smoke_test.ps1 -Ui` **92 / 92**.
