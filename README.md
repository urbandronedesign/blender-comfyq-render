# ComfyQ Render

A Blender 5.x extension for **AI rendering in the classroom**. Lay out a scene in Blender (clay, camera,
animation), send it to [ComfyQ](https://github.com/b2renger/ComfyQ), a multi-user queue in front of ComfyUI,
and get the results back in the scene: pictures, videos, 3D models.

- **Passes from the scene:** clay, matcap or material stills, Cycles renders, masks of the selected objects,
  the clay animation as an MP4.
- **A bar and a node graph:** every workflow ComfyQ serves becomes a node with its own inputs and settings.
  Chain nodes (render → edit → upscale), reuse results, save chains as presets.
- **Batch renders:** a Media Folder node renders a whole folder of photos or videos, subfolders included, into
  `<folder>_renders`.
- **Results come back:** images in the Image Editor, videos and audio in a "ComfyQ Results" scene, GLB/PLY
  models placed next to the scene.
- **For the class:** one GPU server for many students; teacher tools (load a model, server panel) stay locked on
  student PCs.
- **AI agents (optional):** a local MCP server lets Claude Code or Codex drive the scene and ComfyQ.

## Install

1. Build the zip: `.\tools\smoke_test.ps1` (writes `dist\comfyq_bridge-0.1.0.zip` and runs the headless tests).
2. In Blender: Edit › Preferences › Get Extensions › Install from Disk, then pick the zip.
3. Preferences › Add-ons › **ComfyQ Render**: student name, server address (for example `http://192.168.1.10:3000`).
4. In the 3D View: **Ctrl+Shift+Q**, or the ComfyQ tab in the sidebar.

## Docs

| | |
|---|---|
| [docs/STUDENT.md](docs/STUDENT.md) | Quickstart for students (also shipped in the extension: the **?** button) |
| [docs/CLASS.md](docs/CLASS.md) | Teacher guide: class setup, presets, sharing presets |
| [docs/UI.md](docs/UI.md) | Every control and how it is checked |
| [docs/AGENTS.md](docs/AGENTS.md) | AI agents through the MCP server |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Hand-over: features, demo, tests, known gaps |

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q tests\unit tests\contract   # contract tests need a running ComfyQ
.\tools\smoke_test.ps1                                         # headless Blender, isolated profile
.\tools\smoke_test.ps1 -Ui                                     # visible Blender, presses every button
```

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)). The ComfyQ client in `extension/comfyq_bridge/client/` (no `bpy`)
is Apache-2.0.
