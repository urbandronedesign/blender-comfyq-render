---
name: blender-comfyq-roundtrip
description: Render the open Blender scene, run it through a ComfyQ workflow and bring the result back, with the blender-comfyq MCP tools (AI rendering of a layout or a clay animation; node-graph presets).
---

# Blender ↔ ComfyQ round trip

Needs Blender running with *AI agents (MCP server)* on (add-on preferences) and connected to ComfyQ.
If the only tool is `blender_status`, Blender is not ready: tell the user how to turn it on.

## Direct round trip (one job)
1. `blender_scene_info` — frame range, fps, camera, what is animated.
2. `comfyq_list_workflows` — only **served** workflows run. Pick by what it `makes` and its description
   (video-edit = restyle the clay animation; t2i/image-edit with an image input = restyle a still).
3. `comfyq_describe_workflow` — keys, kinds, defaults, `disabled_when` rules.
4. `blender_render` — `animation` for video inputs, `still` (or `mask` after `blender_select`) for images.
   Use `max_edge` ≤ the workflow's limits (512–1280).
5. `comfyq_submit` — `files` = {media key: rendered path}, `prompt`, `params` for settings.
   Match timing to the scene (clip length ≈ scene seconds) unless asked for a short test.
6. `comfyq_wait` (repeat while running) → `comfyq_outputs` → `blender_import`.

## In the student's node graph
`blender_graph_add_preset` (`clay_v2v`, `keyframe_v2v`, `render_image`, `image_3d`, `image_upscale`) →
`blender_graph_describe` → `blender_graph_set` (node, `prompt`, text) → `blender_graph_run` →
`blender_graph_status` until done. Results stay on the nodes (and in their Viewer/Save nodes).

## Etiquette
The GPU is shared by the class: short tests first (smallest resolution, short clips), cancel jobs that
are no longer needed (`comfyq_cancel`), never loop on submits.
