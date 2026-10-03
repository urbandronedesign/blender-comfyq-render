You are testing the Blender ↔ ComfyQ bridge through the `blender-comfyq` MCP tools. Use only those tools.
Do one full round trip, keeping the GPU job as short as possible (the GPU is shared by a class):

1. `blender_scene_info`, then `comfyq_list_workflows` (served only).
2. Take the served video-to-video workflow and read its parameters with `comfyq_describe_workflow`.
3. `blender_render` with kind `animation` and max_edge 512 (the clay animation).
4. `comfyq_submit` to that workflow: the rendered file in its video input (`files`), the prompt
   "agent round trip: a claymation city at dusk", and the cheapest settings it allows — smallest working
   resolution, shortest clip length (at most 2 s), start image off, prompt enhancement off. Leave other
   settings at their defaults.
5. `comfyq_wait` until the job is done (call it again while it is still running), then `comfyq_outputs`,
   then `blender_import` the video file.

Finish with a single JSON object and nothing else:
{"job_id": ..., "workflow_id": ..., "params_sent": {...}, "output_file": ..., "imported": ..., "problems": [...]}
List in "problems" anything in the tools that was confusing or did not work.
