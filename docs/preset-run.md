# Preset library run (2026-10-04)

All 48 presets of the workflow library (`presets/library/`), run one after the other on a real Blender
scene through the **MCP server**, the way an AI agent drives it (`tools/preset_run.py`). Each preset was
added to the graph, its file nodes were filled with test assets, videos were shortened, and then it was run.
**Solo mode** loaded each workflow's model in ComfyQ.

- **Scene:** `test1_AI.blend`. Blender's default cube, camera and light; 1920×1080, 24 fps, 10.4 s;
  nothing animated. The clay animation is therefore a still cube.
- **Machine:** RTX 5090 32 GB, ComfyUI 0.37, ComfyQ in dev mode (nodemon), Blender 5.2.2 LTS, bridge
  build `da3e068` plus the two fixes below.
- **Test assets, made by the presets themselves:**
  - with Flux2 Klein t2i: a clay explorer character, a portrait, a reference sheet;
  - with LTX 2.5 t2v: a 2 s talking face;
  - with Windows text-to-speech: a spoken sentence;
  - from a Klein image edit: a watercolour repaint of the scene, used as the stylized keyframe.
  No photos or voices of real people were used.
- **Settings:** each workflow's defaults, matched to the scene (size, fps, duration). Video durations were
  set to 2 s (or the workflow's minimum). Prompts were each workflow's own example prompt.
- **Cost:** about 92 min of GPU time for the 48 presets, with one model switch per preset (about 30 s).

**Result: 46 / 48 run end to end** (booked → done → downloaded → shown in the Viewer). The 2 failures
are in ComfyUI, not in the bridge. "Looks right" below was judged by looking at every image and at the
start, middle and end frames of every video (`tools/contact_sheet.py`).

Legend: ✅ ran and the result fits the inputs · ⚠️ ran, but the result ignores part of the inputs (see
note) · ❌ failed.

## Image (t2i) — 9 / 9
| Preset | Time | Output | Verdict |
|---|---|---|---|
| Flux2 Dev — text to image | 112 s | 1024×576 | ✅ |
| Flux2 Klein 9B — text to image | 62 s | 1024×576 | ✅ (made the test character) |
| Ideogram 4.0 — text to image | 102 s | **768×1376** | ✅ text rendered well. ⚠️ size not matched to the scene (portrait) |
| Krea 2 Turbo — style of 1–3 pictures | 103 s | 1024×1024 | ✅ (after fix 1) |
| Krea-2 Turbo — text to image (+ LoRA) | 72 s | 1024×1024 | ✅ |
| Qwen 2.1 — draw from depth | 82 s | 1024×1024 | ✅ follows the cube's shape and camera angle |
| Qwen 2.1 — draw from edges (canny) | 62 s | 1024×1024 | ✅ follows the cube |
| Qwen 2.1 Viggle Turbo | 72 s | 1024×1024 | ✅ |
| Qwen 2.1 — text to image | 42 s | 1024×1024 | ✅ |

Qwen and Krea give square images: their size settings are not recognised by the scene matching (see the
runbook, "what is missing").

## Image edit — 13 / 13
| Preset | Time | Output | Verdict |
|---|---|---|---|
| Flux2 Dev — image edit | 143 s | 1360×768 | ✅ cube on a beach |
| Flux2 Klein 9B — image edit (with reference) | 113 s | 1360×768 | ⚠️ returned the clay render almost unchanged (example prompt vs a cube) |
| Flux2 Klein 9B — image edit | 62 s | 1360×768 | ✅ watercolour repaint, composition kept |
| Flux2 Klein 9B — inpaint (paint & prompt) | 52 s | 1920×1080 | ✅ flowers only on the selected cube (mask works) |
| Flux2 Klein 9B — inpaint with reference | 42 s | 1920×1080 | ⚠️ the character is inserted but ghosted inside the cube |
| Flux2 Klein 9B — reference + depth (pose transfer) | 42 s | 1360×768 | ⚠️ a cube gives no pose; small character, odd background |
| Krea 2 Turbo — dynamic character sheet | 113 s | 1536×1024 | ✅ full sheet of the character (earlier sweep: echo without a character) |
| Krea 2 Turbo — QuadView turnaround | 72 s | 1536×1024 | ✅ 4 views |
| Qwen 2.1 — image edit with references | 92 s | 1920×1088 | ✅ characters added around the cube |
| Qwen Image Edit — character camera angle | 92 s | 1392×752 | ✅ |
| Qwen Image Edit — scene camera angle | 72 s | 1360×768 | ✅ new viewpoint (made the cube a cardboard box) |
| Wan 2.2 Bernini-R — image editing | 82 s | 1280×720 | ✅ |
| Wan 2.2 Bernini-R — with a reference image | 42 s | 1280×720 | ✅ |

## Image → video — 11 / 11
| Preset | Time | Output | Verdict |
|---|---|---|---|
| FastH3 — text to video | 113 s | 2.3 s, sound | ✅ |
| FastH3 — from first and/or last frame | 82 s | 2.3 s, sound | ⚠️ starts and ends on the cube, cuts to an unrelated street in between |
| LivePortrait — portrait + driving video | 52 s | 3.0 s, 16 fps, no sound | ✅ the portrait follows the face video |
| LTX 2.3 — image + audio to video | 204 s | 2.0 s, sound | ⚠️ speech-driven, but cuts from the character to another one |
| LTX 2.3 — reference sheet (ingredients) | 153 s | 4.8 s, sound | ⚠️ the example prompt (a news anchor) wins; the prompt must name the sheet's items |
| LTX 2.5 — first & last frame | 123 s | 2.0 s, sound | ✅ |
| LTX 2.5 — image to video | 72 s | 2.0 s, sound | ⚠️ starts on the cube, then the example's cyborg scene |
| LTX 2.5 — text to video | 103 s | 2.0 s, sound | ✅ (made the talking-face asset) |
| MiniMax H3 — image to video | 113 s | 2.3 s, sound | ✅ push-in on the cube |
| MiniMax H3 — text to video | 113 s | **10.1 s**, sound | ✅ (the test tool's 2 s cap missed this setting) |
| Wan 2.2 — 4 key frames | 123 s | 3.2 s, no sound | ✅ clay → watercolour → clay, through the key frames |

## Video → video — 13 / 15
| Preset | Time | Output | Verdict |
|---|---|---|---|
| LTX 2.3 3DReal — rough 3D to photoreal | 139 s | 1.6 s, sound | ⚠️ mostly the reference character |
| LTX 2.5 IC-LoRA — canny | 143 s | 1.7 s, sound | ✅ the cube, held in a hand |
| LTX 2.5 IC-LoRA — "depth control" (`union_control`) | 112 s | 1.7 s, sound | ✅ the cube relit realistically |
| LTX 2.5 IC-LoRA — pose | 102 s | 1.7 s, sound | ⚠️ no person in the shot → a chrome robot |
| MiniMax H3 ControlNet — canny | 173 s | 2.3 s, sound | ✅ the cube in a sandstorm |
| MiniMax H3 ControlNet — depth | 133 s | 2.3 s, sound | ⚠️ a figure instead of the cube |
| MiniMax H3 ControlNet — "pose" (`fun_controlnet_union`) | 167 s | 2.3 s, sound | ⚠️ a dancer (no person in the shot) |
| MiniMax H3 — transform by reference | 274 s | 2.3 s, sound | ⚠️ the example's cyber city |
| SCAIL-2 — character replacement | 295 s | 6.5 s, sound | ⚠️ the character, but no person to replace |
| Wan 2.1 ID-V2V — restyle (comparison) | — | — | ❌ ComfyUI: `WanImageToVideo.execute() got an unexpected keyword argument 'ref_pad_image'` |
| Wan 2.1 ID-V2V — restyle | — | — | ❌ same error |
| Wan 2.2 Bernini-R — video editing | 123 s | 1.9 s, sound | ✅ the cube becomes a toy box |
| Wan 2.2 Bernini-R — with a reference image | 93 s | 1.9 s, sound | ✅ |
| Wan 2.2 Bernini-R — with reference (auto-prompt) | 194 s | 1.9 s, sound | ✅ |
| Wan Animate 2 — character performs the video | 317 s | 5.0 s, sound | ✅ (no motion to copy from a still cube) |

Most ⚠️ results come from the test, not from the bridge. Pose and character-replacement workflows need a
**moving person** in the shot, and example prompts describe other scenes. To judge those workflows, the
next run should use an animated character scene and prompts written for it.

## Found and fixed during the run
1. **Uploading an image file failed** ("Image '…' does not have any image data"). `image.copy()` of a
   file image, loaded by a script, by the MCP or from a saved .blend, can come without pixels. The upload
   image is now built from the pixels (`passes/render.py image_pass`); the smoke test covers it. Students
   would have hit this with a picture saved in their .blend.
2. **Agents could not put a picture in an image node.** `blender_graph_set` now loads a file into an
   Image node (`key: "image"`).
3. **Two presets had an empty Prompt node.** The Wan ID-V2V workflows have no example prompt, so the
   preset's prompt starts empty. The bridge refuses with a clear "Prompt … is required", which is
   correct behaviour. The test tool now fills in a prompt.

**Visual baseline:** `baselines/2026-10-04/index.html` (local, not in git, ~100 MB): for every workflow its
description, a screenshot of its graph in Blender, the inputs actually uploaded, the prompt and settings
sent, the output (images, playable videos), time and verdict. A new run is compared with it:
`tools/preset_run.py` → `tools/preset_shots.py` → `tools/preset_report.py baselines/<date> --baseline baselines/2026-10-04`
(each card then shows the earlier output next to the new one).
A **Chains** section holds multi-step class presets (records with group `Chains` in results.json, notes and
comparisons keyed by preset): first one, *Cycles render → photoreal (Flux2 Klein)* on the user's supercar scene.

Raw data: `live_check/presets/results.json`, contact sheets in `live_check/presets/sheets/` (not in git).
