# UX / UI audit 2 + Draw & Mask in a ComfyQ workspace — plan for discussion (2026-11-15)

Nothing here is built yet. Each phase is agreed first, then built, tested (`smoke_test.ps1 -Ui` + docs/UI.md, screenshots
looked at), and committed. Follows the `blender-ui-audit` skill and the `comfyq-workflow-build` protocol.

## 1. What the request asks (analysis)

| In the request | What it means here | Open question |
|---|---|---|
| "a complete audit UI/UX of our plugin in Blender" | Second full audit. The first (2026-10-04, `docs/ux-audit-plan.md`, phases A–F) predates: camera / 3D-model inputs, shots, Material Board, Join Text, Film, Media Folder, Surfaces, splats, the Server panel, Blender 5.3. Same method: Blender HIG + Best Practice checklist on the code, Nielsen heuristics on the class journeys, GUI screenshots. | — |
| "draw a starting frame" | Make the first picture of an image or video by drawing: a sketch that a model turns into an image (scribble / canny / image edit), or paint-over hints on the scene render, then use it as the image input or the video's start frame. Today the start frame can only be the scene render, an AI keyframe, a Blender image or a file. | 3D drawing in the camera view, 2D painting, or both? (§4, D2) |
| "or select a zone for masking" | Choose the area a model repaints (inpaint, transparent = repaint). Today: *Render + Selection Mask* (selected objects) and, for agents only, SAM3 surfaces (`blender_mask_image`). Nothing lets a student draw a zone. | Vector shapes, painted mask, or both? (D3) |
| "using all the Blender native tools" | Use Blender's own editors, modes, brushes and data (Grease Pencil, Image Editor Paint / Mask modes, Mask data-blocks) instead of custom drawing code: students learn Blender, and the tools stay in the .blend. | — |
| "in a new context UI" | A dedicated place to work: proposed = a **ComfyQ workspace** (a tab at the top of Blender, next to Layout / Shading), laid out for this work. | Workspace tab, the existing detached ComfyQ window, or both? (D1) |
| "first produce a plan" | This document. No code changes until the decisions in §6 are made. | — |

## 2. What Blender 5.3 offers (probed headless on 5.3.0, 2026-11-15)

| Need | Native tool | Python access (checked) | Notes |
|---|---|---|---|
| Draw in the scene, aligned with the camera | **Grease Pencil** (v3) Draw mode in the camera view: pressure, layers, fill, primitives, frame-by-frame | `bpy.types.GreasePencil`, `layers / frames / drawing.add_strokes`, `grease_pencil.*` ops | **A camera render shows it**: Workbench and EEVEE renders of the camera view include the strokes (probe image). Animatable: a drawn key per frame. Cycles does not draw GP. |
| Paint a picture / paint over the render | **Image Editor › Paint** mode (texture paint in 2D): brushes Draw / Soften / Smear / Clone / Fill, all blend modes | `SpaceImageEditor.ui_mode = 'PAINT'`, `bpy.ops.image.new`, brush `image_brush_type`, `blend` | One layer per image. Painting on an image the plugin already renders (scene pass) is possible. |
| Paint the repaint zone | Paint brushes with **Erase Alpha / Add Alpha** blend | `Brush.blend` has `ERASE_ALPHA`, `ADD_ALPHA` | Alpha IS ComfyQ's mask convention (transparent = repaint): what is painted is what is sent, no conversion. |
| Shape a zone (box, circle, free shape), feathered, editable | **Image Editor › Mask** mode: Mask data-block, splines with feather, `primitive_square_add` / `primitive_circle_add`, add-vertex clicks, select box / lasso / circle, shape keys over time | `bpy.types.Mask`, `MaskLayer` (invert, blend, falloff), `MaskSplinePoint` (co, handles, feather_points) | Must be rasterized for upload: Compositor *Mask* node (exists in 5.3) or our own fill of the splines (numpy, bpy-free, testable). Shape keys → a zone that moves over time (video masks later). |
| Quick notes on top | Annotate tool | `gpencil.annotate` | Not rendered: no use as input. |
| A dedicated layout | **Workspace** tab | `bpy.ops.workspace.append_activate(idname=, filepath=)` from a .blend shipped in the extension; `bpy.utils.register_tool` for toolbar tools | The same way app templates add workspaces; the layout is drawn by Blender, not rebuilt by script (gotchas 10 / 26). |

ComfyQ side: only 2 workflows take a `mask` input (Flux2 Klein inpaint with a prompt / with a reference); sketches go
to image-input workflows (Flux2 / FLUX.3 Klein edit "turn this sketch into…", Qwen 2.1 canny control, MOSS pose +
depth) and to video workflows as the start image (LTX 2.5 i2v / FLF, MiniMax H3 i2v, FastH3).

## 3. Proposed design (to confirm)

### 3.1 The ComfyQ workspace (new context)
A **ComfyQ** tab, added by a button in the bar / preferences (*Add ComfyQ Workspace*), appended from
`extension/comfyq_bridge/workspace.blend`:

```
┌──────────────────────────────┬──────────────────────────┐
│ 3D Viewport (camera view)    │ Image Editor: the FRAME  │
│  draw with Grease Pencil     │  View / Paint / Mask     │
│  N sidebar = the ComfyQ bar  │  result + player         │
├──────────────────────────────┴──────────────────────────┤
│ Node Editor: the ComfyQ graph                │ Timeline │
└─────────────────────────────────────────────────────────┘
```
- A **mode strip** at the top of the bar: **Frame · Draw · Mask · Graph**. Each switches the editors and tools in one
  click (camera view + GP Draw with a "Sketch" layer; Image Editor Paint; Image Editor Mask with a square ready; graph)
  and says in one line what to do next.
- Replaces or complements the detached ComfyQ window (D1). The bar, graph and player code stay the same.

### 3.2 Draw a starting frame (two native ways, one result)
1. **Sketch in the camera view** (Grease Pencil): *Start Sketch* adds a `ComfyQ Sketch` GP object in front of the
   camera (layers *Lines* / *Colour*), enters Draw mode, camera view. New input source **Sketch**: the camera render
   with the sketch over the clay scene, or the sketch alone on white (setting *Sketch Only*). Aligned with the 3D
   layout, so the result can drive a video of the same camera.
2. **Paint the frame** (Image Editor Paint): *Paint Frame* makes a canvas image the size the workflow wants (from
   the scene aspect, `match`), optionally starting from the scene render, and switches to Paint. Source = the
   existing **Blender Image** (already supported).
- Both appear as sources of image inputs and of the video start / last frame, and as graph nodes (**Sketch** node;
  the Image node gains *Paint…*).

### 3.3 Select a zone to repaint (native, one mask)
1. **Mask Zone** (Image Editor Mask mode, vector): *Add Zone › Box / Circle / Free Shape*, feather by dragging,
   several shapes, invert, kept in the .blend. The plugin rasterizes it at the upload size (feather → soft alpha).
2. **Paint Zone** (Paint with Erase Alpha / Add Alpha on the frame): for organic areas.
3. Existing: **Selected Objects** (Render + Selection Mask) and **Surfaces (SAM3)** (exposed to students: pick
   surfaces by name, today agent-only).
- One preview: the frame with the zone in red over it (the overlay `passes/segment.py` already makes), on the node
  and in the Image Editor.
- New mask source **Zone** for mask inputs; graph node **Mask Zone** (Mask data-block, feather, invert, frame).

## 4. The audit itself (phase 1, read-only)
- **Code checklist** (skill): every label, tooltip, poll message, icon, layout, operator, draw() in `ui/`, `graph/`,
  `ops/`, `prefs.py`, `props.py`, `mcp/` preferences — with file:line, rule, replacement.
- **Journeys, Nielsen 0–4**, walked on Blender 5.3 with `smoke_test.ps1 -Ui` screenshots: 1 first run & connect
  (incl. ComfyQ in admin mode), 2 the bar, 3 the graph & presets, 4 waits / failures / cancel, 5 the teacher
  (serve, Solo, Server panel, agents), and new: **6 draw → image / video**, **7 mask → inpaint**, **8 multi-shot film**,
  **9 3D / splats** (import, place, size).
- **Blender 5.3 specifics**: Vulkan drawing of custom node headers / previews, theme, 5.2 compatibility.
- Output: `docs/ux-audit-2.md` (findings ranked) — then the fixes are scheduled in phase 6.

## 5. Phases
Phase 1 DONE 2026-11-15: findings in [ux-audit-2.md](ux-audit-2.md) (19 at severity 3, ~45 at 2), with an adjusted
order at its end (test harness first, then spikes, then the severity-3 fixes that hurt in class today).

| Phase | Content | Test | Effort |
|---|---|---|---|
| 0 | This plan; decisions §6 | — | done |
| 1 | Audit (read-only): findings report | screenshots on 5.3 | M |
| 2 | Spikes, headless 5.3: GP sketch pass (clay + sketch, sketch only on white, grey conversion of coloured ink); Mask rasterizer (own numpy fill vs Compositor Mask node; feather; at upload size) with unit tests; Erase-Alpha painting → alpha mask; workspace appended from a bundled .blend inside the extension zip | numbers + images looked at | S–M |
| 3 | ComfyQ workspace + mode strip | GUI -Ui steps + screenshots | M |
| 4 | Draw: Sketch source / node, Paint Frame | smoke + GUI; GPU: sketch → Klein edit, Qwen canny, LTX i2v start frame (judged by looking) | M |
| 5 | Mask: Mask Zone source / node, Paint Zone, overlay, SAM3 pick for students | smoke + GUI; GPU: zone → Klein inpaint | M |
| 6 | Audit fixes: safety → feedback → wording → code health → layout | GUI -Ui, docs/UI.md | M |
| 7 | Class presets (*Sketch → image*, *Sketch → video*, *Mask a zone → inpaint*), docs (UI, CLASS, STUDENT help), MCP tools for agents (draw/mask by script) | e2e presets, report | S–M |

## 6. Decisions — ANSWERED 2026-11-15
D1 = (c) ComfyQ workspace tab, detached window kept for second screens · D2 = (a) Grease Pencil + Paint ·
D3 = (a) Mask shapes + painted zone (+ SAM3 picking for students) · D4 = audit first · D5 = later (not asked yet).

The options as they were put:
- **D1 — the new context**: (a) a **ComfyQ workspace tab** (recommended: native, saved in the file, one click);
  (b) keep only the detached ComfyQ window; (c) the workspace, and the detached window stays for second screens.
- **D2 — drawing**: (a) **both** Grease Pencil in the camera view and 2D Paint (recommended: GP for sketches aligned
  with the 3D scene and videos, Paint for touch-ups on a render); (b) only GP; (c) only Paint.
- **D3 — masking**: (a) **Mask Zone shapes + Paint Zone** (recommended); (b) only shapes; (c) only painting.
  SAM3 surface picking for students: yes / later.
- **D4 — scope order**: audit first then features (recommended: the audit finds what the new mode strip must fix),
  or features first.
- **D5 — video**: masks that move over time (Mask shape keys, a mask video) — later (no ComfyQ video-mask workflow
  yet besides SAM3), or now.
