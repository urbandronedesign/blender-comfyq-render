# Blockout animation → photoreal video

A blocked-out Blender scene with an animated camera, filmed as a photoreal video: an AI first frame, then a
video-to-video model that follows the clay animation in that look. Tested on 2026-10-21 on a modern house plugged on
top of a cliff (terrace, glass pool), built by `tools/make_cliff_house_scene.py` → `E:\zaki\cliff_house.blend`.

Class preset: **Blockout animation → photoreal video (FLUX.3 keyframe + LTX canny)**
(`presets/class/`, built by `tools/make_blockout_video_preset.py`). The built-in *AI keyframe → video* preset is
unchanged.

## The chain

| Step | Workflow | Input | Time | Why this one |
|---|---|---|---|---|
| First frame | Scene Render, **Material colours**, frame 1 | the scene camera | < 1 s | clay made FLUX read the cone trees as zinc sculptures; green cones became pines |
| Keyframe | **FLUX.3 Klein — edit a picture** (50 steps, seed 7) | first frame + keyframe prompt (`<ref_image_0>`) | 76 s (model loaded) | kept the composition, every volume, the struts and the pool exactly |
| Video | **LTX 2.5 IC-LoRA — canny** (seed 7, clip = scene, 5 s) | clay animation (Workbench, 1920×1080) + the keyframe as start image + video prompt | 255 s incl. loading the model | holds every volume for the whole camera move; crisper glass and edges than depth, a little less flicker |

Output: 113 frames, 24 fps, 4.7 s, 1792×1024, with a generated sound track (wind, birds).

**The whole preset in one Run Graph** (`tools/preset_e2e.py`, Solo loading both models): passed, same quality
(`live_check/cliff_house/e2e/`). Keyframe 95 s; the video job took 765 s that time, its upscale stage at 88.7 s per
step right after ComfyQ restarted onto LTX; run again with the model loaded: **157 s** (9.8 s per step). Count on
2.5 to 4 min for the video, occasionally much more on the first job after a model switch.

## Candidates compared (same keyframe, prompt and seed)

| Video workflow | Result | Verdict |
|---|---|---|
| LTX 2.5 IC-LoRA **canny** | geometry, materials, pool stable start to end; mean frame-to-frame change 8.39 | ✅ chosen |
| LTX 2.5 IC-LoRA **depth** ("depth control", id `union_control`) | the same at a glance; at 1:1 the pool glass edge smears; change 8.53; 205 s | ✅ close second |
| LTX 2.3 **3DReal** | after frame 1 the sky goes **black** and the pines turn back into **white cones** (it copies the clay render's empty background); 1280×704 | ❌ |

Sheets: `live_check/cliff_house/sheet_all.jpg` (start, ⅓, ⅔, end of each), `crop_canny_vs_depth.jpg` (1:1, last
frame), keyframes `keyframe_flux3_clay.png` / `keyframe_flux3_material.png`, videos `video_*.mp4`.

## What to know

- **Give the blockout viewport colours.** The keyframe is made from the Material style: trees, water and glass need
  their own colours or FLUX invents what grey cones are. The video itself follows the clay animation's edges.
- **Two prompts, and the keyframe one describes ONLY the first frame.** FLUX.3 Klein edits refer to the picture as
  `<ref_image_0>`. On a reveal shot (2026-10-22: start facing the rock under the house, rise to reveal it) a keyframe
  prompt describing the whole house made FLUX **paint the house into the first frame** (rock only in the clay), and
  LTX then ignored the clay rise. Describe what the camera sees at frame 1; the video prompt carries the whole shot
  (the reveal, the house, the sound).
- **Camera shots by agent:** `blender_camera_shot` (MCP) keys a camera from {frame, location, look_at (point or
  object names)}; preview with `blender_render kind=animation` before the GPU run.
- **Two models, both large.** In class serve FLUX.3 Klein edit (10 GB) as a lane and LTX 2.5 canny (~30 GB) as the
  main model; with Solo the graph loads them in turn (each main switch restarts ComfyQ, 1–2 min).
- LTX uses the first 5 s of the clay animation (up to 10 s: *Clip length*); the preset matches it to the scene.

## How to use it

- **Students:** Presets › Class › *Blockout animation → photoreal video*; edit the two prompts; Run Graph. Check the
  keyframe in its viewer first: a wrong keyframe makes a wrong video (change its prompt or seed and run again).
- **Agent:** `blender_graph_add_preset` with the label, `blender_graph_set` the two Prompt nodes' `text`,
  `blender_graph_run`, `blender_graph_status`.
- **Headless:** `tools/cliff_video_check.py` (keyframe / video steps, candidates) and `tools/preset_e2e.py` (any saved
  preset end to end).

## Limits / next

- The house is seen from below: the pool reads only because its sides are glass.
- 3DReal might do better with a real (Cycles / EEVEE) render as its source instead of clay: not tested.
