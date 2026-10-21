# Multi-shot films: keeping one look across shots

The blockout → photoreal video pipeline (docs/blockout-video.md) is meant for long videos made of several shots. Each
shot = a FLUX/Qwen keyframe of its first frame + LTX 2.5 canny on its clay animation. This page is the assessment of
how to keep the same look from shot to shot (tested 2026-10-22 on the cliff house, `live_check/cliff_house/consistency/`).

## What carries the look

- **LTX copies its start image.** Whatever the start image shows keeps its look for the whole clip; whatever is not in
  it is invented from the prompt. So every shot's keyframe must look like the others.
- **The same seed is necessary, not enough.** Seed 7 everywhere, yet the reveal shot's timber came out white: a seed
  reproduces a result only for identical inputs, and every shot has a different frame and clay.
- **The keyframe prompt describes only that shot's first frame**; a shared look sentence keeps the vocabulary identical.

## Test: shot 2 (three-quarter orbit from the right) against shot 1's keyframe (the hero)

Same first frame (material colours), same look words, seed 7. `keyframes_compare.jpg`, `keyframes_compare2.jpg`.

| Keyframe | Result | Verdict |
|---|---|---|
| A FLUX.3 Klein edit, no reference | keeps shot 2's composition; larch, white panels, concrete, glass pool, snow: close to the hero already | ✅ good baseline |
| B Qwen 2.1 edit + **hero image** | copied the hero's composition (shot 1's angle), ignored shot 2's | ❌ |
| C Flux2 Klein 9B ref + **hero image** | same: the hero re-rendered | ❌ |
| D Flux2 Klein ref + depth control (hero = look, shot 2 = structure) | mixed the two layouts: the timber volume gone, the zinc tower in front | ❌ |
| E Qwen 2.1 edit + **material board** (8 swatches cut from the hero: larch, white panels, zinc, concrete, rock + snow, pool glass, pine, sky) | keeps shot 2's composition AND takes the hero's materials (dark zinc tower, board-formed concrete, larch, turquoise pool) | ✅ best |

A whole picture as a reference gets copied, layout included; swatches carry only materials.

Videos (LTX canny, same clay, prompt, seed; `sheet_shots.jpg`): both shot 2 videos keep their own move and read as the
same project as shot 1. E is closer on the zinc and concrete, A on the warm windows. Neither kept shot 1's warm tint in
the sky (flat grey): put the light in the shared look sentence ("soft warm overcast at dusk").

## Recommended multi-shot recipe

1. Approve one hero keyframe (shot 1), then cut a **material board** from it (swatches, no layout).
2. Every other shot's keyframe: **Qwen 2.1 edit** with its own first frame as `<image1>` and the board as `<image2>`;
   prompt = what this frame shows + the shared look sentence + "use the materials of `<image2>`, not its layout".
3. Video: LTX 2.5 canny, same seed, shared look sentence + this shot's camera move.
4. Continuous camera moves: the last frame of shot N can start shot N+1.
5. Assemble the clips in the ComfyQ Results scene's sequencer; one soundtrack for the whole film (each clip's own
   generated sound jumps at the cuts).

## Done (2026-10-22)

- **Shots**: a *Shot* field on Scene Render / Scene Animation / Selection Mask = a timeline marker bound to a camera (`passes/shots.py`); `blender_camera_shot shot=` creates it, `blender_render shot=` previews it.
- **Serving**: `serving.make_room_for_main` closes extra lanes that would not fit beside a new main model (card − 2 GB), largest first, unknown size = all; Solo, Load Model and `comfyq_serve` report what they closed.

## Still to do

- **Material board** node (crops of an approved image) or an agent tool.
- A **multi-shot preset** (hero, board, one keyframe + video per shot, run in order).
- An **assembly** step (clips → sequencer → one film, one soundtrack).
