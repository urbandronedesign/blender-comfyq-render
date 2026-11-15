# Upscaling with the latest models (2026-11-15)

Which ComfyQ upscaler the presets use, and why: compared on the GPU on the same inputs, judged by looking, by numbers
against a known original, and by time. Pictures and sheets: `live_check/upscale_compare/`.

## Video: RelayVSR ×2

A 5 s photoreal LTX clip (1792×1024) was shrunk to a 896×512 "draft", upscaled ×2 by each model, and compared with
the real original at the same frame. *Sharpness* is the Laplacian variance (the original: 572). *Flicker* is the mean
change between consecutive frames (the original: 7.0; higher = the upscaler adds flicker).

| Model | Time | Sharpness | Flicker | Look (crop of the house) |
|---|---|---|---|---|
| **RelayVSR ×2** (chosen) | **45 s** | 564 (= the original) | **7.9** | The original's own texture and grain. White halos on bright edges against the sky |
| SeedVR2 HD (1080p) | 152 s | 1428 (over-sharpened) | 9.2 | Crisp, but invents a texture: the cladding becomes vertical planks with dark pockmarks |
| SeedVR2 3B int8 ×2 (never run before) | 475 s | 1683 | 10.1 | Same invented texture, more flicker, 10× slower |
| bicubic ×2 (baseline) | — | 154 (soft) | — | Soft |

## Pictures: SeedVR2 4K

The FLUX.3 photoreal render of the living room (1360×768) was taken to about 4K.

| Model | Time | Result |
|---|---|---|
| **SeedVR2 4K** (chosen, as before) | 46 s | Real wood grain and floorboards, a crisp vase. No grid at 1:1 on flat walls |
| SeedVR2 7B int8 ×3 | 26 s | As good on this picture, no grid at 1:1 (the grid seen in October was at 4 MP). In the library as the faster choice |
| PiD ×4 / Qwen 2511 unblur | — | Rejected earlier: squiggle pattern / portraits only, about 1 MP |

## Presets

- **Library › Upscale** (8 presets, `tools/make_preset_library.py … i2i,preprocessor`): one per upscaler. The picture or
  video to enlarge is a file or a result the student picks (an Image / Video File node), not the clay render.
  Includes RelayVSR, SeedVR2 4K / 7B / HD / 3B, PiD, Qwen unblur and LTX 2.5 2×.
- **Class presets** (`tools/make_upscale_presets.py`): each adds an Upscale node after the model of an existing class
  preset. Viewer + Save show the upscaled result, and **Before upscale** shows the model's own result.
  - *Cycles render → photoreal 4K (FLUX.3 Klein + SeedVR2 4K)*.
  - *Blockout animation → photoreal video ×2 (FLUX.3 + LTX canny + RelayVSR)*.
- Upscale only one step again: press Generate on the Upscale node. The earlier results are reused.

## Limits
- RelayVSR: bright edges against a white sky get halos. For clean restoration of a soft or compressed clip without
  doubling it, SeedVR2 HD stays in the library.
- LTX 2.5 2× is generative (it invents detail) and needs a clip with sound; it is not part of the chains.
