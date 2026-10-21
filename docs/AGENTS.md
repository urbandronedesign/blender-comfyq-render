# AI agents (MCP): Claude Code, Codex

The ComfyQ Render can let an AI agent on the same PC work with the open Blender scene and the
classroom's ComfyQ: render passes, book jobs, import results, build and run the ComfyQ graph.
It is an **MCP server inside Blender**, **off by default**.

## Turning it on
Blender › Edit › Preferences › Add-ons › **ComfyQ Render** › tick **AI agents (MCP server)** (port 8790 by
default). The line below it shows the address and the connection file. Blender must also be connected
to ComfyQ (the bar's **Connect**) for the `comfyq_*` tools.

Safety:
- it listens on **127.0.0.1 only** (nothing on the network can reach it);
- every request needs a **token**, new at every start, written to `%APPDATA%\comfyq-bridge\mcp.json`
  (readable by this Windows user only) and deleted when the server stops;
- requests from web pages are refused (browser `Origin` header, or a host other than localhost);
- the tools are **scoped** (no "run any Python"); jobs go through ComfyQ's queue like the student's own,
  under the student's name.

## Connecting an agent
Agents start a small launcher, `mcp/launcher.py` in the installed extension (standard-library Python;
any Python 3.9+), which reads the connection file and forwards to Blender. The preferences show its
full path. If Blender is not running, the agent still starts and its tool explains how to turn it on.

**Claude Code** — `.mcp.json` in your project (this repository has one, using `tools/blender_mcp.py`):
```json
{ "mcpServers": { "blender-comfyq": {
    "command": "python",
    "args": ["C:/Users/<you>/AppData/Roaming/Blender Foundation/Blender/5.2/extensions/user_default/comfyq_bridge/mcp/launcher.py"]
} } }
```
or `claude mcp add blender-comfyq -- python "<that path>"`.

**Codex** (`~/.codex/config.toml`, also for `codex --oss` with a local model on the DGX Spark / A6000):
```toml
[mcp_servers.blender-comfyq]
command = "python"
args = ["C:/Users/<you>/AppData/Roaming/Blender Foundation/Blender/5.2/extensions/user_default/comfyq_bridge/mcp/launcher.py"]
```

## Tools
| Tool | Does |
|---|---|
| `blender_scene_info` | File, frame range, fps, duration, resolution, camera, objects (animated?), selection |
| `blender_select` | Select objects (what a mask repaints) |
| `blender_render` | `still` / `mask` / `animation` pass through the camera (clay, matcap, material) → file. `style: "CYCLES"` (stills only, `samples` 64): a real Cycles render in a separate Blender — the GPU is free again when it returns, ready for the ComfyQ job. `objects`: the selected objects as one GLB (modifiers applied) for 3D-model inputs (remesh, UV unwrap), with `size_m` and a `warning` when the model is too large for ComfyQ's remesh |
| `blender_import` | A result into Blender (image → Image Editor, video/audio → *ComfyQ Results*, GLB/PLY → collection placed `place` BESIDE the scene (default) / CURSOR / ORIGIN / ASIS, `on_ground`, `size` = longest side in m) |
| `blender_world` | Light the scene with an HDRI: a local .hdr/.exr or `builtin:forest` (city, courtyard, forest, interior, night, studio, sunrise, sunset), `strength`, `rotation_deg`, `background` (seen behind the scene in renders), `clear`. Not used by the clay/matcap passes ComfyQ gets |
| `blender_shadow_catcher` | Objects (`names`), a `collection`'s meshes, or a new ground `plane` catch shadows only. **Cycles**: Blender's Shadow Catcher flag (exact). **EEVEE**: an approximating material (transparent where lit, dark in shadow); `eevee_lit` = brightness of the lit ground (1; ~1.5 under a bright HDRI + sun), `strength` = darkness; `remove` undoes it. Verified by renders (sandbox, forest HDRI + sun) |
| `blender_transform` | Move / rotate / scale / hide objects by name (`location`, `rotation_deg`, `scale`, `hide`) |
| `blender_graph_describe` | The ComfyQ node graph: nodes, models, settings, prompts, links, results |
| `blender_graph_add_preset` | Add a class preset (`render_image`, `clay_v2v`, `keyframe_v2v`, `image_3d`, `image_upscale`, or a saved one) |
| `blender_graph_set` | Change a node: a setting or `prompt`, a source's style/frame/file, an output's options |
| `blender_graph_run` / `blender_graph_status` | Run the graph (or one node) and follow it |
| `comfyq_list_workflows` | Served workflows (or all): what they make, VRAM badge, description |
| `comfyq_describe_workflow` | A workflow's parameters. A `camera` parameter (e.g. Qwen 2511 reshoot from another angle) takes JSON `{"horizontal": 0-360 (0 front, 90 right side, 180 back), "vertical": degrees, "zoom": 0 wide-10 close-up}` and lists its `camera` block (ranges + the shot words the model gets); a `model` parameter takes a mesh file (`files`, e.g. from `blender_render kind=objects`) |
| `comfyq_submit` | Book a job: settings, local files for media inputs, prompt → job id |
| `comfyq_job_status` / `comfyq_wait` / `comfyq_cancel` / `comfyq_outputs` | Follow, wait (≤ 120 s per call), cancel, download results |
| `blender_graph_set` on an Image node | `key: "image"` + a local file path loads the picture into it (references, characters, styles) |
| `blender_graph_set` on a Media Folder / Save node | `folder` (a local folder) and `recursive` on the Media Folder, `batch_suffix` on the Save node: `blender_graph_run` then renders each file one job after the other into `<folder>_renders` (same subfolders, name + suffix); `blender_graph_status` gives `batch` = progress, time left, the file now |
| `comfyq_server_status` | What the GPU serves (main workflow + lanes, busy, VRAM), card/used GB, what fits alongside now, the running serve/calibrate task |
| `blender_segment` | **SAM3 by name** — ComfyQ's SAM3 returns ONE surface per phrase, so `"wall|left wall|right wall"` names one label from several phrases (masks merged; the default wall does this) — (architecture: floor, wall, ceiling, window, door, stairs, column, sofa, table, rug, shelf, lamp by default; any `labels`). Renders the camera (`style`, default CYCLES) or takes `image`, turns it into a 3-frame clip (ComfyQ's SAM3 workflow is video-only), books one job per label, reads each mask back → per label: mask PNG, % of the picture, where (bottom / top left…), a colour **overlay** (shown in the Image Editor), and `prompt` = the found surfaces named with `materials` ({label: "light oak parquet"}) between `intro` and `outro`. Needs SAM3 served. Background task: follow with `blender_task_status` |
| `blender_task_status` | The background task (segmentation, serve, calibrate): state, message, result |
| `blender_mask_image` | An inpaint input from a picture + surface masks: those surfaces transparent (= repainted by Flux2 Klein inpaint), resized to the picture, `grow_px` (6) over the seams, `invert` |

### Teacher tools: loading models from the agent
Off by default, and **hidden** from the agent while off. Turn them on only on the teacher's PC:
**Preferences › Add-ons › ComfyQ Render › Teacher › Teacher tools: agents may load models** (the Teacher box is locked unless the profile holds ComfyQ's admin password), then reconnect the
agent (`/mcp` in Claude Code). They use ComfyQ's own admin routes, so ComfyQ's code is unchanged.

| Tool | Does |
|---|---|
| `comfyq_serve` | Load a workflow's models. `how="alongside"`: a lane next to the main workflow (no restart; must fit in VRAM). `how="main"`: replaces the main workflow — **ComfyQ restarts (~1–2 min) for the whole class**; refused while a job runs unless `force`. `"auto"` (default): alongside if it fits, else main. Returns after `wait_s` (≤ 120 s) and keeps going in the background |
| `comfyq_unserve` | Stop a lane (frees its VRAM). Not the main workflow |
| `comfyq_calibrate` | Measure VRAM peak and duration on ComfyQ's benchmark inputs (GPU busy for minutes) |

With **Solo: load models on demand** on (Preferences), `blender_graph_run` also loads the models a graph needs by itself (same rules: one at a time, never while anyone has a job; the last model stays loaded).

ComfyQ's admin password, when one is set, comes from `COMFYQ_ADMIN_PASSWORD` or the profile
(`admin_password`, `admin_password_env` or `admin_password_file` in `profiles.toml`), never from the .blend.

**What actually keeps students out is ComfyQ, not this preference.** Checked 2026-10-04: this ComfyQ has
no admin password (`config.json` `auth.adminPasswordHash` is empty), so the admin gate is off and any PC
on the LAN can open lanes or calibrate by calling ComfyQ directly. `POST /admin/activate-workflow` has no
admin gate at all, even when a password is set. For a class: set an admin password in ComfyQ (part of
P1.0), and ask b2renger to gate `activate-workflow`.

The server's instructions tell the agent the usual round trip: scene info → served workflows →
render → submit → wait → outputs → import (or: preset → prompt → run → status in the graph).

## Checking it
- `pytest tests/unit/test_mcp_server.py` — protocol, token, web-page refusal, launcher (online and offline).
- `tools\smoke_test.ps1` — the tools called over HTTP inside a headless Blender.
- `tools\agent_roundtrip.ps1` — a visible sandbox Blender driven by **Claude Code** (`claude -p`) through
  the launcher: render the clay animation → book the served video workflow (cheapest settings) → wait →
  download → import. One short GPU job; transcript in `live_check\agent\claude.json`.

Verified 2026-10-03 (Blender 5.2.2, Claude Code 2.1): 10 agent turns, about 70 s. The clay animation was
rendered at 512 px, LTX 2.5 IC-LoRA canny was booked with the cheapest settings (job `86924b46`), and
the result was imported into *ComfyQ Results*. The agent's feedback led to three changes:
`comfyq_submit` echoes what it sent (and which parameter the prompt fills), `comfyq_describe_workflow`
gives each parameter's `step`, and `blender_render … max_seconds` renders short tests.
Not verified here: Codex / `codex --oss` (same stdio launcher; config above).
