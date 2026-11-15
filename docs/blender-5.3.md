# Blender 5.3 and native Gaussian splats (2026-11-15)

Blender 5.3.0 (released 10 November 2026) imports and renders **3D Gaussian splats** natively. ComfyQ Render now
targets Blender 5.3 and still runs on 5.2 LTS (`blender_version_min` stays 5.0.0; splats are detected at run time).

## What changed in the plugin

| | Blender 5.3 | Blender 5.2 |
|---|---|---|
| Splat `.ply` from a ComfyQ job | native Gaussian splat (`PointCloud`, type `GAUSSIAN_SPLAT`), upright | bare vertices (as before) |
| Splat `.spz` | native Gaussian splat (`wm.spz_import`), upright | kept in the job folder; the message says 5.3 imports it |
| A job returning the same splat as `.ply` **and** `.spz` (TripoSplat) | only the `.ply` is imported (`importers.media.pick_files`); a graph node's result is the `.ply` (`run.pick_result`) | same |
| Render | Workbench, EEVEE and Cycles draw them (default emissive shader; ~1 s per view for 262,144 splats on the 5090) | — |

Placement (*Beside the scene*, *On the ground*, *Size*) works on splats like on models; only the object transform is
set, because Blender's **Apply Transform does not handle the splat attributes** (scale, rotation, SH) in 5.3.

### The axis fixes (measured, not guessed)
Blender's splat importers keep the file's axes. ComfyQ's splats (TripoSplat, IDOL) are not Z-up:

| file | raw "up" after import | fix (object rotation) |
|---|---|---|
| `.ply` | −Y (OpenCV axes: Y down, Z away from the camera) | −90° about X (`PLY_SPLAT_FIX`) |
| `.spz` | −Z (upside down) | 180° about X (`SPZ_FIX`) |

Measured on an olive tree in a pot (pot → canopy direction from the splats' colours); the `.ply` and `.spz` of one
TripoSplat job differ by exactly `ply = (x, z, −y)` of `spz` (residual 1e-4 on 1,048,576 splats). After the fixes the
splat faces the scene camera like the picture and matches its `.glb` mesh twin (`tools/splat_compare.py` sheet).
A first attempt used a room-shaped splat to decide "up" and got it wrong: check orientation on an object with an
obvious top. The smoke test checks it on the tree when `live_check/splat53` is present.

### Picking the splat workflow (GPU, 2026-11-15)
Same picture (FLUX.3 Klein distilled, 38 s), same seed:

| | time | look |
|---|---|---|
| **IDOL** (chosen) | 98 s (+ an orbit video) | keeps the picture's wide umbrella canopy and twisted trunks |
| TripoSplat (262,144 splats) | 52 s | canopy squeezed into a ball; its `.glb` mesh has holes in the leaves |

Both `.glb` meshes are worse than the splats for foliage. Class presets: **Picture → Gaussian splat (IDOL)** and
**Text → Gaussian splat (FLUX.3 Klein + IDOL)** (`tools/make_splat_presets.py`), run end to end headless on the cliff
house scene in 65 s (models loaded): splat imported beside the scene, upright, rendered in Cycles
(`live_check/splat53/e2e_text/scene_cycles.png`).

## ComfyQ changes the plugin followed (ComfyQ of 2026-11-15, 87 workflows)
- **Admin mode after a restart.** ComfyQ remembers whether it was serving; started otherwise it stays in admin mode:
  HTTP only, no Socket.IO, nothing served. The bridge's connect now reads `GET /admin/mode` and says *"ComfyQ is
  running but serves no model (admin mode). The teacher starts serving in ComfyQ's admin page…"* instead of
  "Connection error". The teacher starts serving from the admin page (or `activate-workflow`).
- **Main model switch without a restart.** While serving, `activate-workflow` swaps the main lane in place (other
  lanes stay; 409 when it does not fit beside them). The bridge's wording no longer promises a 1–2 minute restart.
- **`activate-workflow` is gated** by the admin password (the RUNBOOK §7.1 item is done).
- No new parameter types (all 87 workflows use the 12 the bridge knows). Library regenerated: 67 presets (25 new:
  FLUX.3 Klein ×4, MiniMax H3 image ×3, MOSS ×3, P-H3 ×2, Pika H3 Flex ×3, Qwen 2512 360° panorama, Qwen relight,
  FastH3 OmniRef / loops, MiniMax 2K…; 3 renamed upstream).

## Tests (2026-11-15)
- unit + contract: 95 passed (1 skipped); `smoke_test.ps1 -Version 5.3` and `-Version 5.2` PASSED (splat checks per
  version); GUI `smoke_test.ps1 -Ui -Version 5.3`: 112 checks ok, no draw errors (screenshots looked at).
- The tools take the Blender version: `smoke_test.ps1 -Version 5.3` (default) / `5.2`; the PowerShell helpers use
  Blender 5.3's Python when it is installed, else 5.2's.

## Upgrading a PC
Install Blender 5.3 next to 5.2 (official MSI from download.blender.org, SHA-256 in `blender-5.3.0.sha256`, signed by
the Blender Foundation). Open 5.3 once and choose **Load Blender 5.2 Settings** in the splash screen (it copies the
preferences: ComfyQ Render's settings, the MCP server switch, the theme), close it, then install the new build into
5.3 (`--command extension install-file -r user_default -e dist\comfyq_bridge-0.1.0.zip`, never `--factory-startup`).

## Limits / next
- Splats are not sent TO workflows (no ComfyQ workflow takes one) and the mesh input skips point clouds.
- Blender 5.3 cannot export splats yet; its render assumes sRGB-trained splats (low-opacity bright areas differ).
- Not checked yet: how a splat looks in the plugin's **clay** pass (grey override) and in the Material pass; the
  Cycles source renders it (it is a real render).
