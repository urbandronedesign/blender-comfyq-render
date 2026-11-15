# SPDX-License-Identifier: GPL-3.0-or-later
"""The preset test series of 2026-11-15: EVERY preset (library + class) run on inputs chosen for it, with prompts
written for it, to compare with the earlier runs (baselines/2026-10-04, baselines/2026-10-22).

Unlike tools/preset_run.py (one scene, each workflow's own example prompt), every preset has a CARD here: the scene
it runs on, the files it gets, the nodes it swaps (a Clay animation replaced by real dance footage for the pose /
character workflows, a Scene Render by a photoreal keyframe for image-to-video), its prompts and settings. Inputs that
other cards need are made first by the presets themselves (ASSET cards: the character, the portrait, the keyframes…).
Seeds are fixed (7) so a later run can be compared.

    python tools/preset_series.py [--only=a,b] [--scenes=cliff,room] [--fresh]     # orchestrates, one Blender per scene
    (inside Blender: tools/preset_run_headless.py <out> --module=preset_series --scene=<alias>)

Needs: ComfyQ serving, the sandbox profile with the extension (tools/smoke_test.ps1), file inputs made by
tools/series_prep.py + speech.wav, the scenes of SCENES. Results: live_check/series_2026-11-15/results.json
(resumable), report: tools/preset_report.py baselines/2026-11-15 --results … --baseline baselines/2026-10-22.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "live_check" / "series_2026-11-15"
ASSETS = OUT / "assets"
RESULTS = OUT / "results.json"
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"
SEED = 7
CAP_SECONDS, CAP_FRAMES = 5, 121
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_EXT = {".wav", ".mp3", ".flac"}

SCENES = {   # alias: (file, what it is)
    "cliff": (r"E:\zaki\cliff_house.blend", "blockout of a cliff-edge house, 5 s orbiting camera, 1280×720"),
    "room": (r"E:\zaki\mask_demo.blend", "blockout living room, the vase selected, 1280×720"),
    "truck": (r"E:\zaki\truck_splat.blend", "captured Gaussian splat of a truck, camera at a capture photo"),
    "mesh": (r"E:\zaki\mesh_test.blend", "raw AI-generated character mesh (19,827 faces), selected"),
    "arch": (r"E:\zaki\arch_room.blend", "blocked-out room for the architecture chain"),
    "shots": (r"E:\zaki\cliff_house_shots.blend", "cliff house with 3 camera-bound shots"),
}

# -- prompts ----------------------------------------------------------------------------------------------------
HOUSE = ("a modern house hanging over the edge of a rocky granite cliff: board-formed concrete core and piers "
         "running down the rock, a warm larch-clad volume cantilevered over the void on thin steel struts with a deep "
         "glazed loggia, a white fibre-cement panelled volume with recessed windows, a dark standing-seam zinc tower, "
         "a timber deck terrace with a glass-sided infinity pool full of turquoise water, pine trees on the plateau, "
         "snow patches and dry winter grass on the rocks")
KEY_FLUX3 = ("Turn <ref_image_0> into a photorealistic architectural photograph, keeping exactly the same composition, "
             f"camera angle, shapes and proportions. {HOUSE[0].upper() + HOUSE[1:]}. Overcast winter sky with soft "
             "diffuse light, warm light glowing in the windows, crisp detail, high-end architectural photography.")
HERO = ("Golden-hour architectural photograph of a modern house cantilevered over a granite sea cliff: a warm "
        "larch-clad volume on slender steel struts, board-formed concrete piers running down the rock, a glass-sided "
        "infinity pool glowing turquoise on a timber deck, pine trees on the plateau, the ocean far below catching the "
        "low sun, long soft shadows, shot on a full-frame camera with a 24 mm tilt-shift lens, crisp detail, natural "
        "colours")
CONTROL = (f"Photorealistic architectural photograph of {HOUSE}. Overcast winter light, warm light in the windows, "
           "crisp detail, natural colours")
VIDEO = ("Photorealistic cinematic shot slowly orbiting to the right around a modern house on the edge of a rocky "
         "cliff: board-formed concrete piers down the rock, a warm larch-clad volume cantilevered on thin steel struts, "
         "a white panelled volume, a dark zinc tower, a glass-sided pool with turquoise water on a timber deck. Snow "
         "patches and dry winter grass on the rocks, pine forest, overcast winter sky, soft diffuse light, warm light in "
         "the windows. Smooth steady camera, no people. Sound: soft wind over the cliff and distant birds.")
T2V = ("Cinematic aerial shot slowly circling a modern house cantilevered over a granite sea cliff at golden hour: "
       "warm larch cladding, board-formed concrete piers, a glass-sided pool glowing turquoise on a timber deck, pine "
       "trees swaying in the wind, waves breaking on the rocks far below. Smooth drone move, warm low sun, long "
       "shadows. We hear wind, waves and distant gulls.")
ROOM = ("a calm Scandinavian living room: light oak floorboards, warm white plaster walls, a large window with soft "
        "daylight and a view of trees, a round walnut table, a linen armchair, a brass floor lamp, a framed abstract "
        "painting")
TRUCK = ("Turn the 3D render <ref_image_0> into a sharp photorealistic photograph taken with a full-frame camera, "
         "keeping everything exactly where it is: the weathered turquoise 1950s pickup truck with its wooden stake bed, "
         "the San Pedro Square Market logo on the door, the concrete sidewalk with the manhole and orange marks, the "
         "trees, lamp post and buildings behind. Remove the blur and smears, crisp detail on the paint, rust, wood and "
         "tyres, natural late-afternoon daylight.")
TRUCK_BG = ("behind the truck: a modern glass office tower with clean straight mullions and crisp reflections of the "
            "sky and trees, green leafy street trees with defined leaves, a red steel column, clear late-afternoon "
            "sky; sharp photorealistic daylight photograph, same perspective and light as the truck, nothing in front "
            "of the truck")
DANCER = ("a young woman contemporary dancer with short curly copper hair, a fitted mustard-yellow jumpsuit with "
          "rolled sleeves and white canvas sneakers")
LOOP = ("Locked-off static camera, no pan, no zoom. The house on the cliff from <Picture 1>: the turquoise water of "
        "the glass pool ripples gently, pine branches sway in a light wind, thin clouds drift slowly across the "
        "overcast sky, warm light glows in the windows. A seamless loop: the last frame matches the first. Sound: soft "
        "wind and gently lapping water.")
CLIFF_KEY = "@cliff_k1.png"
MAT = {"style": "MATERIAL"}

# -- cards ------------------------------------------------------------------------------------------------------
# key = "<group>/<preset label>" (the report compares by key) or "asset:<file>" (made for other cards: save_as).
# files: {node label: file}   swap: {node label: file} (a source node replaced by a file node, links kept)
# relink: {node label: workflow input key}   prompts: {node label: text}   set: {node label: {key: value}}
# select: [objects]   frame: scene frame before the run   chain: run the whole graph (class presets)
C = []


def card(key, scene, **kw):
    C.append({"key": key, "scene": scene, **kw})


L, E, I2V, UP, V2V, CH = "Image", "Image edit", "Image → video", "Upscale", "Video → video", "Chains"
T2I_FLUX3 = "FLUX.3 Klein — text to image"
T2I_FAST = "FLUX.3 Klein distilled — fast text to image"
EDIT_FAST = "FLUX.3 Klein distilled — fast picture edit"

# assets (made first, scene by scene) ---------------------------------------------------------------------------
card("asset:character.png", "cliff", preset=T2I_FLUX3, prompts={"Prompt": (
    f"Full-length studio photograph of {DANCER}, standing straight and facing the camera, arms relaxed at her sides, "
    "the whole body visible from head to feet with space above and below, plain light-grey seamless studio backdrop, "
    "soft even front light, sharp focus, 85 mm lens, no text")})
card("asset:portrait.png", "cliff", preset=T2I_FLUX3, prompts={"Prompt": (
    "Head-and-shoulders studio portrait of a friendly middle-aged man with a short grey beard and round tortoiseshell "
    "glasses, looking straight into the camera, relaxed neutral expression with the mouth closed, face fully visible "
    "and evenly lit, navy crew-neck sweater, plain warm-grey background, soft diffused light, sharp focus on the eyes, "
    "85 mm portrait lens")})
card("asset:sheet.png", "cliff", preset=T2I_FLUX3, prompts={"Prompt": (
    "A clean reference sheet on a pure white background, three items side by side with generous space between them "
    "and no overlap, each fully visible: on the left a red-haired girl about eight years old in a bright yellow "
    "raincoat and green rubber boots, standing, front view; in the middle a vintage red bicycle with a wicker basket, "
    "side view; on the right a small tabletop model of a rainy Amsterdam canal street with narrow brick houses and an "
    "arched bridge. Soft even studio light, product-catalogue style, no text, no labels, no frames")})
card("asset:style.png", "cliff", preset=T2I_FLUX3, prompts={"Prompt": (
    "A mid-century travel poster painted in flat gouache: a lighthouse on a rocky headland at sunset, bold simplified "
    "shapes, a limited palette of teal, coral, cream and deep navy, visible dry-brush texture and paper grain, strong "
    "graphic silhouettes, soft halftone shading, no text, no lettering")})
card("asset:materials.png", "cliff", preset=T2I_FLUX3, prompts={"Prompt": (
    "Architectural detail photograph of a contemporary house at golden hour: warm vertical larch battens beside "
    "board-formed concrete with a visible wood-grain imprint, a floor-to-ceiling window in a thin black steel frame "
    "reflecting pine trees, a timber deck in the foreground, crisp detail, natural light")})
card("asset:vase.png", "cliff", preset=T2I_FLUX3, prompts={"Prompt": (
    "Studio product photo of a tall sculptural ceramic vase with a deep cobalt-blue reactive glaze running over a "
    "sandy speckled clay body, an asymmetric hand-thrown shape, standing upright, the whole vase visible, plain pure "
    "white background, soft even light, sharp focus, no text")})
card("asset:chair.png", "cliff", preset=T2I_FLUX3, prompts={"Prompt": (
    "Studio product photo of a mid-century lounge armchair in cognac leather with a walnut frame, three-quarter front "
    "view, the whole chair visible, plain pure white background, soft even studio light, sharp focus, no text")})
card("asset:olive.png", "cliff", preset=T2I_FAST, prompts={"Prompt": (
    "Studio product photo of a mature olive tree in a terracotta pot, the whole tree and pot visible with space around "
    "it, silver-green leaves, twisted trunk, plain pure white background, soft even studio light, sharp focus, no "
    "text")})
card("asset:face.mp4", "cliff", preset="LTX 2.5 — text to video + audio", prompts={"Prompt": (
    "Static close-up of a woman facing the camera straight on, head and shoulders centred, plain grey wall behind. "
    "She talks calmly to the camera, nodding slightly, her eyebrows rising as she makes a point, blinking naturally, "
    "a small smile at the end. Soft even daylight, locked-off camera, no cuts. She says: “Hello everyone, today we "
    "turn a simple 3D scene into a film.”")})
for name, frame, node_frame in (("cliff_k1.png", 1, "START"), ("cliff_k2.png", 40, "CURRENT"),
                                ("cliff_k3.png", 80, "CURRENT"), ("cliff_k4.png", 120, "END")):
    card(f"asset:{name}", "cliff", preset=EDIT_FAST, frame=frame, prompts={"Prompt": KEY_FLUX3},
         set={"Scene render": {"style": "MATERIAL", "frame": node_frame}})
card("asset:dance_key.png", "cliff", preset=EDIT_FAST, swap={"Scene render": "@dance_frame0.png"}, prompts={"Prompt": (
    "Turn the dancer in <ref_image_0> into a sleek chrome humanoid robot with glowing cyan seams, in exactly the same "
    "pose, with the same body proportions and the same place in the frame; replace the background with a neon-lit "
    "city rooftop at night in light rain with wet reflections; cinematic lighting, photorealistic")})
card("asset:room_photo.png", "room", preset=EDIT_FAST, set={"Scene render": MAT}, prompts={"Prompt": (
    f"Turn <ref_image_0> into a photorealistic interior photograph of {ROOM}, keeping exactly the same composition, "
    "camera angle, furniture shapes and positions. Soft natural daylight, gentle shadows, crisp detail, high-end "
    "interior photography.")})

# Image (text to image): one hero prompt across the general models, so they can be compared with each other ------
for label in ("FLUX.3 Klein distilled — fast text to image", T2I_FLUX3, "Flux2 Klein 9B — text to image",
              "Krea-2 Turbo — text to image (+ style LoRA)", "MOSS-Image Base — text to image, with an optional box layout",
              "MOSS-Image SFT — text to image, with an optional box layout", "MiniMax H3 — text to image",
              "MiniMax-H3-Image — text to image", "P-H3-Image — text to image", "Qwen Image 2.1 — text to image",
              "Qwen Image 2.1 - with the community \"Fix\" LoRA"):
    card(f"{L}/{label}", "cliff", prompts={"Prompt": HERO})
# Ideogram 4.0 takes ONLY its structured JSON caption with a box per element (bbox = [top, left, bottom, right] in
# thousandths); plain text came back twice as a grey "Image blocked by safety filter" picture (2026-11-15).
IDEOGRAM = json.dumps({
    "high_level_description": "An architecture exhibition poster: a photograph of a modern house cantilevered over a "
                              "granite sea cliff at dusk fills the frame, with the title 'CLIFF HOUSE' in large white "
                              "capitals across the sky and 'ARCHITECTURE 2026' in small type below it.",
    "style_description": {
        "aesthetics": "Clean Swiss-style exhibition poster, generous margins, a single strong photograph.",
        "lighting": "Blue-hour dusk, warm light glowing in the windows, soft glow on the pool water.",
        "photo": "Sharp architectural photograph, full-frame camera, 24 mm lens.",
        "medium": "Photograph with typography",
        "color_palette": ["#0F2A44", "#F2F2F0", "#C98A4B", "#3FB8C4", "#6B6E70"]},
    "compositional_deconstruction": {
        "background": "A deep blue dusk sky over a calm sea horizon, granite cliffs falling to the water.",
        "elements": [
            {"type": "text", "bbox": [70, 120, 230, 880], "desc": "The title 'CLIFF HOUSE' in large bold white "
             "condensed sans-serif capitals, one line, centred across the sky.", "color_palette": ["#F2F2F0"]},
            {"type": "text", "bbox": [240, 380, 290, 620], "desc": "The line 'ARCHITECTURE 2026' in small white "
             "spaced capitals, centred under the title.", "color_palette": ["#F2F2F0"]},
            {"type": "obj", "bbox": [330, 200, 700, 800], "desc": "A modern house cantilevered over the cliff edge: a "
             "warm larch-clad volume on slender steel struts, board-formed concrete piers, large windows glowing warm, "
             "a glass-sided infinity pool lit turquoise on a timber deck.",
             "color_palette": ["#C98A4B", "#8C8C88", "#3FB8C4", "#FFD9A0"]},
            {"type": "obj", "bbox": [620, 0, 1000, 1000], "desc": "Rugged grey granite cliff with pine trees on top, "
             "falling steeply to the sea, covering the lower part of the frame.",
             "color_palette": ["#6B6E70", "#2E3B2F", "#0F2A44"]}]}}, ensure_ascii=False, indent=1)
card(f"{L}/Ideogram 4.0 — text to image", "cliff", prompts={"Prompt": IDEOGRAM},
     set={"Workflow": {"aspect_ratio_37": "16:9 (Widescreen)"}})
card(f"{L}/Krea 2 Turbo — generate in the style of 1–2 pictures", "cliff", files={"Style reference 1": "@style.png"},
     prompts={"Prompt": ("a modern house cantilevered over a sea cliff with a glass pool on its terrace, pine trees, "
                         "a small sailing boat on the water below")})
card(f"{L}/MOSS-Image — new picture from a photo's pose and depth", "cliff", swap={"Scene render": "@dance_frame0.png"},
     prompts={"Prompt": ("Full-body fashion photograph of a dancer in a flowing crimson silk dress caught mid-move on a "
                         "white marble stage, dramatic theatre spotlight from above, dark velvet curtain behind, sharp "
                         "focus, 50 mm lens")})
for label in ("Qwen Image 2.1 - draw from depth (ControlNet depth)", "Qwen Image 2.1 - draw from edges (ControlNet canny)"):
    card(f"{L}/{label}", "cliff", prompts={"Prompt": CONTROL})
card(f"{L}/Qwen-Image 2512 — 360° panorama / HDRI from a description", "cliff", prompts={"Prompt": (
    "the top of a rocky granite sea cliff at golden hour: pine trees, dry winter grass with patches of snow, the ocean "
    "horizon all around, a few soft clouds, warm low sun, shot on a full-frame mirrorless camera, sharp realistic "
    "photograph")})

# Image edit -------------------------------------------------------------------------------------------------------
for label in (EDIT_FAST, "FLUX.3 Klein — edit a picture"):
    card(f"{E}/{label}", "cliff", set={"Scene render": MAT}, prompts={"Prompt": KEY_FLUX3})
card(f"{E}/Flux2 Klein 9B — image edit", "cliff", set={"Scene render": MAT}, prompts={"Prompt": (
    f"Turn this 3D blockout into a photorealistic architectural photograph of {HOUSE}. Overcast winter light, warm "
    "light in the windows, crisp detail. Keep every shape exactly where it is, at the same size and from the same "
    "camera position.")})
card(f"{E}/Flux2 Klein 9B — image edit (with reference)", "cliff", set={"Scene render": MAT},
     files={"Reference image": "@materials.png"}, prompts={"Prompt": (
         "Re-render the 3D blockout as a photorealistic architectural photograph using the materials of the reference "
         "picture: warm vertical larch battens on the cantilevered volume, board-formed concrete with a wood-grain "
         "imprint on the core and piers, thin black steel window frames. Keep the composition, camera angle, shapes and "
         "proportions of the blockout exactly; granite cliff, pine trees, soft golden-hour light.")})
card(f"{E}/MiniMax-H3-Image — edit a picture", "cliff", set={"Scene render": MAT}, prompts={"Prompt": (
    f"Turn <Picture 1> into a photorealistic architectural photograph of {HOUSE}. Keep every object, its shape, its "
    "proportions and its place exactly the same, and the same camera angle. Overcast winter light, warm light in the "
    "windows, crisp detail.")})
card(f"{E}/P-H3-Image — edit a picture", "cliff", set={"Scene render": MAT}, prompts={"Prompt": (
    "make it a photorealistic architectural photograph: larch-clad cantilevered volume on steel struts, board-formed "
    "concrete piers, white panelled volume, dark zinc tower, glass pool with turquoise water on a timber deck, granite "
    "cliff, pine trees, overcast winter light; keep every shape and the camera exactly")})
card(f"{E}/Qwen Image 2.1 — image edit with references", "cliff", set={"Scene render": MAT},
     files={"Image 2": "@materials.png"}, prompts={"Prompt": (
         "Turn <image1> into a photorealistic architectural photograph, keeping exactly its composition, camera angle, "
         "shapes and proportions. Clad the cantilevered volume in the warm vertical larch battens and make the core and "
         "piers in the board-formed concrete shown in <image2>, with its thin black steel window frames. Granite cliff, "
         "pine trees, glass pool with turquoise water, soft golden-hour light, crisp detail.")})
card(f"{E}/Wan 2.2 Bernini-R — AI image editing", "cliff", set={"Scene render": MAT}, prompts={"Prompt": (
    "Turn this 3D blockout into a photorealistic architectural photograph: larch-clad volume on steel struts, "
    "board-formed concrete piers, glass pool with turquoise water, granite cliff, pine trees, overcast winter light. "
    "Keep every shape, its place and the camera exactly as they are.")})
card(f"{E}/Wan 2.2 Bernini-R — image editing with a reference image", "room", swap={"Scene render": "@room_photo.png"},
     files={"Reference image": "@chair.png"}, prompts={"Prompt": (
         "Replace the armchair in the picture with the cognac leather armchair from the reference image, in the same "
         "spot, at the same size and in the same light, keeping everything else exactly as it is.")})
card(f"{E}/Qwen Image 2.1 — relight a composite (blend an object into a background)", "room",
     swap={"Scene render": "@room_photo.png"}, files={"Image 3": "@chair.png"},
     relink={"Image 3": "loadimage_image_475"}, prompts={"Prompt": (
         "Place the cognac leather armchair from <image2> into the living room of <image1>, standing on the floor next "
         "to the floor lamp at a natural size. Apply consistent lighting to it, matching the soft daylight from the "
         "window, enhance specular highlights on the leather, keep the front-back spatial relationship, add a natural "
         "contact shadow, and blend it seamlessly into the room.")})
card(f"{E}/Flux2 Klein 9B — inpaint (paint & prompt)", "room", select=["Vase"], set={"Mask (selected objects)": MAT},
     prompts={"Prompt": ("a tall sculptural vase in matte terracotta holding a few dried pampas grass stems, standing on "
                         "the table, soft daylight from the window, photorealistic")})
card(f"{E}/Flux2 Klein 9B — inpaint with reference (paint & insert)", "room", select=["Vase"],
     set={"Mask (selected objects)": MAT}, files={"Reference image": "@vase.png"}, prompts={"Prompt": (
         "Put the cobalt-blue glazed vase from the reference image on the table in the painted area, matching the "
         "room's soft daylight and perspective, with a natural contact shadow.")})
card(f"{E}/Flux2 Klein 9B — reference + depth control (pose transfer)", "cliff",
     files={"Reference image": "@character.png"}, swap={"Scene render": "@dance_frame0.png"}, prompts={"Prompt": (
         f"refcontrol, {DANCER} from the reference image, in exactly the pose of the structure picture, dancing on a "
         "paved square in front of a concrete monument, trees behind, overcast daylight, photorealistic")})
card(f"{E}/Krea 2 Turbo — QuadView character turnaround (4 panels)", "cliff", files={"Character image": "@character.png"})
card(f"{E}/Krea 2 Turbo — dynamic character sheet (auto-prompt)", "cliff", files={"Subject image": "@character.png"})
card(f"{E}/Qwen Image-Edit 2511 - reshoot from another angle (place the camera)", "cliff",
     files={"The subject to reshoot": "@character.png"})

# Image → video ----------------------------------------------------------------------------------------------------
KEYS = {"First frame": CLIFF_KEY, "Last frame": "@cliff_k4.png"}
card(f"{I2V}/LTX 2.5 — image to video + audio", "cliff", swap={"First frame": CLIFF_KEY}, prompts={"Prompt": (
    "Use the provided start image as the first frame. " + VIDEO)})
card(f"{I2V}/LTX 2.5 — first & last frame to video + audio", "cliff", swap=KEYS, prompts={"Prompt": (
    "Use the provided start image as the first frame and the provided end image as the final frame anchor. " + VIDEO)})
card(f"{I2V}/LTX 2.5 — text to video + audio", "cliff", prompts={"Prompt": T2V})
card(f"{I2V}/MiniMax H3 — text to video + audio (4-step turbo)", "cliff", prompts={"Prompt": T2V})
card(f"{I2V}/MiniMax H3 — text to video + audio in 2K (turbo draft + Open-H3-Regenerate-2K)", "cliff",
     prompts={"Prompt": T2V})
card(f"{I2V}/FastVideo FastH3 — text to video with sound (8 steps)", "cliff", prompts={"Prompt": "[Shot 1] " + T2V})
card(f"{I2V}/Pika H3 Flex — text to video with sound (6 steps)", "cliff", prompts={"Prompt": (
    "integrated_multimodal_description: [Shot 1] " + T2V)})
card(f"{I2V}/MiniMax H3 — image to video + audio (4-step turbo)", "cliff", swap={"First frame": CLIFF_KEY},
     prompts={"Prompt": ("The scene from <Picture 1> comes to life: the camera slowly orbits to the right around the "
                         "house on the cliff, the turquoise pool water ripples, pine branches sway in the wind, soft "
                         "overcast light, warm light in the windows. Audio: soft wind over the cliff and distant birds.")})
card(f"{I2V}/FastVideo FastH3 — video with sound, from a first and/or last frame (8 steps)", "cliff", swap=KEYS,
     prompts={"Prompt": ("For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is "
                         "fully referenced; at 5.00 seconds, <Picture 2> (from [Shot 1]) is fully referenced. "
                         "integrated_multimodal_description: [Shot 1] " + VIDEO)})
card(f"{I2V}/Pika H3 Flex — first / last frame to video with sound (6 steps)", "cliff", swap=KEYS, prompts={"Prompt": (
    "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark "
    "of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video. "
    "integrated_multimodal_description: [Shot 1] " + VIDEO)})
REF_CAST = ("subject_definitions: <Subject 1> is the dancer in <Picture 1>: " + DANCER + ". <Subject 2> is the setting "
            "in <Picture 2>: a modern house on a granite cliff with a timber terrace and a glass-sided pool of "
            "turquoise water. summary: The dancer from <Picture 1> dances barefoot on the timber terrace beside the "
            "pool of the house in <Picture 2>, spinning and stretching her arms towards the sea; the camera slowly "
            "circles her. Overcast soft light. Sound: soft wind, her footsteps on the wood and a gentle piano melody.")
card(f"{I2V}/FastVideo FastH3 OmniRef — video with sound from reference pictures (8 steps)", "cliff",
     files={"Picture 1": "@character.png"}, swap={"First frame": CLIFF_KEY}, prompts={"Prompt": REF_CAST})
card(f"{I2V}/Pika H3 Flex — reference pictures to video with sound (6 steps)", "cliff",
     files={"Picture 1": "@character.png"}, swap={"First frame": CLIFF_KEY}, prompts={"Prompt": REF_CAST})
card(f"{I2V}/LTX 2.3 IC-LoRA — reference sheet to video (ingredients + audio)", "cliff",
     files={"Reference sheet": "@sheet.png"}, prompts={"Prompt": (
         "### Reference Sheet Description **Left (Character):** a red-haired girl about eight years old in a bright "
         "yellow raincoat and green rubber boots. **Middle (Prop):** a vintage red bicycle with a wicker basket. "
         "**Right (Setting):** a rainy Amsterdam canal street with narrow brick houses and an arched bridge. "
         "### Shot The girl rides the red bicycle along the canal in light rain, the camera tracking beside her at her "
         "height; she rings the bell and smiles as she crosses the arched bridge, wet cobbles shining, reflections of "
         "the brick houses in the water. Sound: rain on the canal, the bicycle bell, her laughter.")})
card(f"{I2V}/LTX 2.3 — image + audio to video (auto-prompt)", "cliff",
     files={"Character image": "@portrait.png", "Reference audio": "@speech.wav"}, prompts={"Prompt": (
         "The bearded man with round glasses looks into the camera and talks warmly, explaining something to a class, "
         "with small natural head movements and expressive eyebrows; static camera, soft studio light, plain warm-grey "
         "background.")})
card(f"{I2V}/LivePortrait — animate a portrait from a driving video", "cliff",
     files={"Portrait image": "@portrait.png", "Driving video": "@face.mp4"})
card(f"{I2V}/MiniMax H3 — seamless looping video with sound", "cliff", files={"Reference picture": CLIFF_KEY},
     prompts={"Prompt": LOOP})
card(f"{I2V}/FastH3-Omni — seamless looping video with sound (8 steps)", "cliff", files={"Reference picture": CLIFF_KEY},
     prompts={"Prompt": LOOP})
WAN_ORBIT = ("A smooth continuous camera orbit to the right around a modern house on a granite cliff, photorealistic: "
             "the larch-clad volume on its steel struts, the concrete piers, the glass pool with turquoise water on the "
             "timber deck, pine trees, overcast winter light. The camera glides steadily from one key frame to the next "
             "with no cuts; the building stays perfectly still and rigid, only the water and the trees move slightly "
             "in the wind.")
card(f"{I2V}/Wan 2.2 — key-frame animation (4 frames to video)", "cliff",
     swap=KEYS, files={"Key frame 2": "@cliff_k2.png", "Key frame 3": "@cliff_k3.png"}, prompts={"Prompt": WAN_ORBIT},
     # the workflow has one motion prompt per segment; the preset links only the first, the other two keep ComfyQ's
     # example (a red line-art capoeira dancer, who then appeared in the clip): every segment gets the orbit
     set={"Workflow": {"cliptextencode_text_228_225": WAN_ORBIT, "cliptextencode_text_245_242": WAN_ORBIT,
                       "primitiveint_value_283": 1024, "primitiveint_value_284": 576}})

# Upscale -----------------------------------------------------------------------------------------------------------
for label in ("RelayVSR — video upscale 2x", "SeedVR2 3B (int8) — video upscale & restore",
              "SeedVR2 — video upscale to HD (1080p)"):
    card(f"{UP}/{label}", "cliff", files={"Video to upscale": "@ai_clip.mp4"})
card(f"{UP}/LTX 2.5 — 2× generative video upscale (keeps original audio)", "cliff",
     files={"Video to upscale": "@ai_clip.mp4"}, prompts={"Prompt": (
         "Sharp, highly detailed footage of a modern house on a granite cliff: crisp larch battens, board-formed "
         "concrete texture, clean glass reflections, defined pine needles, natural colours and crisp edges.")})
for label in ("PiD — image upscale ×4 (up to 33 MP)", "SeedVR2 7B (int8) — image upscale & restore (up to 4K)",
              "SeedVR2 — image upscale to 4K"):
    card(f"{UP}/{label}", "cliff", files={"Picture to upscale": "@truck_flux3.png"})
card(f"{UP}/Unblur & upscale a photo (Qwen Image Edit 2511)", "cliff", files={"Picture to upscale": "@truck_photo.jpg"},
     prompts={"Prompt": ("Remove the blur and restore a sharp, detailed photograph: crisp edges on the truck's wooden "
                         "stake bed, the rust and turquoise paint, the logo on the door and the tyres; keep everything "
                         "exactly as it is.")})

# Video → video ------------------------------------------------------------------------------------------------------
DANCE = "@dance.mp4"
card(f"{V2V}/LTX 2.3 3DReal — rough 3D render to photoreal (render-guided + audio)", "cliff",
     set={"Clay animation": MAT}, files={"Reference image": CLIFF_KEY}, prompts={"Prompt": (
         "Make it photorealistic. " + VIDEO)})
for label in ("LTX 2.5 IC-LoRA — video to video (canny edges + audio)",
              "LTX 2.5 IC-LoRA — video to video (depth control + audio)"):
    card(f"{V2V}/{label}", "cliff", swap={"First frame": CLIFF_KEY}, prompts={"Prompt": VIDEO})
card(f"{V2V}/LTX 2.5 IC-LoRA — video to video (pose control + audio)", "cliff",
     swap={"Clay animation": DANCE, "First frame": "@dance_key.png"}, prompts={"Prompt": (
         "A sleek chrome humanoid robot with glowing cyan seams dances on a neon-lit city rooftop at night in light "
         "rain, wet reflections sliding over its metal body, cinematic lighting. Upbeat electronic music.")})
for label in ("MiniMax H3 ControlNet (canny) — restyle a video from its outlines + audio",
              "MiniMax H3 ControlNet (depth) — restyle a video from its 3D layout + audio"):
    card(f"{V2V}/{label}", "cliff", prompts={"Prompt": VIDEO})
card(f"{V2V}/MiniMax H3 ControlNet (pose) — transfer a person's movement + audio", "cliff",
     swap={"Clay animation": DANCE}, prompts={"Prompt": (
         "Cinematic shot: a dancer made entirely of flowing, glass-clear water performs the same dance on a wet black "
         "stage, droplets flying off every spin and arm swing, rim light catching the water, dark background. Sound: "
         "splashing water and a deep electronic beat.")})
card(f"{V2V}/MiniMax H3 — restyle a seamless loop (ControlNet) with sound", "cliff",
     swap={"Clay animation": "@tunnel_loop.mp4"}, prompts={"Prompt": (
         "Camera looking straight down an endless tunnel of carved sandstone arches lit by warm flickering torches, "
         "the arches flying steadily towards the camera, fine dust drifting in the light, deep shadows between the "
         "arches; a seamless loop. Sound: a low echoing wind and crackling torches.")})
card(f"{V2V}/MiniMax H3 — transform a video by reference + audio", "cliff", swap={"Clay animation": "@dance_man.mp4"},
     prompts={"Prompt": (
         "subject_definitions: <Video 1> is the source video for the edit: one continuous handheld shot of a man "
         "dancing on a flat grey rooftop under an overcast sky, with brick factory buildings, a tall brick chimney and "
         "a city skyline behind him. <Subject 1> is the dancer in <Video 1>. summary: Keep the choreography, the timing "
         "and the camera move of <Video 1> exactly; replace the dancer with a knight in polished silver plate armour "
         "with a red cloak, and turn the rooftop into the battlements of a medieval stone castle at sunset. Sound: "
         "clanking armour in time with the steps and a driving drum beat.")})
card(f"{V2V}/SCAIL-2 (Wan 2.1) — character replacement in video", "cliff",
     files={"Character image": "@character.png"}, swap={"Clay animation": DANCE}, prompts={"Prompt": (
         f"{DANCER[0].upper() + DANCER[1:]} dances on a paved square in front of a concrete monument with square "
         "skylights, trees behind, overcast daylight; same movements as the original dancer.")})
card(f"{V2V}/Wan Animate 2 — make a character perform a video's motion", "cliff",
     files={"Character image": "@character.png"}, swap={"Clay animation": DANCE}, prompts={"Prompt": (
         f"Character appearance description: {DANCER}, a natural athletic build. Motion: the energetic dance of the "
         "driving video, with the same rhythm and arm swings. Setting: a plain light-grey studio, soft even light.")})
for label in ("Wan 2.1 ID-V2V — restyle a video from one keyframe",
              "Wan 2.1 ID-V2V — restyle a video from one keyframe (comparison view)"):
    card(f"{V2V}/{label}", "cliff", files={"Stylized keyframe": CLIFF_KEY}, prompts={"Prompt": VIDEO})
card(f"{V2V}/Wan 2.2 Bernini-R — AI video editing", "cliff", swap={"Clay animation": DANCE}, prompts={"Prompt": (
    "Turn the dancer's black leather jacket bright red, keeping its texture, folds and fit. Leave the face, the "
    "headscarf, the background and every movement exactly as they are.")})
card(f"{V2V}/Wan 2.2 Bernini-R — video editing with a reference image", "cliff",
     files={"Reference image": "@character.png"}, swap={"Clay animation": DANCE}, prompts={"Prompt": (
         "Replace the dancer's clothing with the mustard-yellow jumpsuit and white sneakers from the reference image. "
         "Keep the face, the headscarf, the background, the lighting, the camera and all motion unchanged.")})
card(f"{V2V}/Wan 2.2 Bernini-R — video editing with reference (auto-prompt)", "cliff",
     files={"Reference image": "@character.png"}, swap={"Clay animation": DANCE})

# Class presets (whole chains) ----------------------------------------------------------------------------------------
CL = "Chains"
card(f"{CL}/Cycles render → photoreal (FLUX.3 Klein)", "truck", chain=True, prompts={"Prompt": TRUCK})
card(f"{CL}/Cycles render → photoreal 4K (FLUX.3 Klein + SeedVR2 4K)", "truck", chain=True, prompts={"Prompt": TRUCK})
card(f"{CL}/Repaint the background of a picture (SAM3 + Flux2 Klein inpaint + SeedVR2 4K)", "truck", chain=True,
     files={"Picture to fix": "@truck_flux3.png"}, set={"Subject to keep": {"words": "truck, sidewalk, road, manhole"}},
     prompts={"Background": TRUCK_BG})
card(f"{CL}/Picture → Gaussian splat (IDOL)", "truck", chain=True, files={"Picture": "@olive.png"})
card(f"{CL}/Text → Gaussian splat (FLUX.3 Klein + IDOL)", "truck", chain=True)
card(f"{CL}/Text → 3D model (FLUX.3 Klein + Pixal3D)", "truck", chain=True)
card(f"{CL}/Remesh: scene objects → clean low-poly", "mesh", chain=True, select=["AI character mesh"])
card(f"{CL}/UV unwrap: scene objects → UVs + AO", "mesh", chain=True, select=["AI character mesh"])
card(f"{CL}/Clean up a model: remesh + UV unwrap", "mesh", chain=True, select=["AI character mesh"])
card(f"{CL}/Reshoot from another angle (Qwen 2511)", "mesh", chain=True, select=["AI character mesh"])
card(f"{CL}/Four views: clay or photo → rendering (FLUX.3 Klein edit)", "cliff", chain=True,
     files={f"{v.title()} view": f"@museum_views/{v}.png" for v in ("front", "left", "back", "right")})
card(f"{CL}/Batch: folder → rendering (FLUX.3 Klein edit)", "cliff", chain=True,
     set={"Media Folder": {"folder": "@museum_views"}}, prompts={"Prompt": (
         "Re-render the picture <ref_image_0> of a building as a finished photorealistic architectural photograph, "
         "keeping exactly the same shape, proportions, openings and the same camera view: the main volume is clad in a "
         "perforated copper skin with a pixelated pattern of punched holes of varying sizes, the openings are deep "
         "windows with dark glass, the ground floor is glazed, the base is a light concrete plinth. Soft even daylight, "
         "sharp architectural photograph.")})
card(f"{CL}/Blockout animation → photoreal video (FLUX.3 keyframe + LTX canny)", "cliff", chain=True)
card(f"{CL}/Blockout animation → photoreal video ×2 (FLUX.3 + LTX canny + RelayVSR)", "cliff", chain=True)
card(f"{CL}/Architecture: Cycles render → photoreal → 4K upscale (Qwen edit + SeedVR2)", "arch", chain=True)
card(f"{CL}/Multi-shot film: one look in every shot (FLUX.3 + Qwen board + LTX canny)", "shots", chain=True,
     timeout=150 * 60)
CARDS = C


# -- running (inside Blender, through tools/preset_run_headless.py) ------------------------------------------------
def tool(name: str, **args):     # replaced in-process by preset_run_headless
    raise RuntimeError("tool() is set by tools/preset_run_headless.py")


def log(*a) -> None:
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def resolve(v):
    if isinstance(v, str) and v.startswith("@"):
        return str(ASSETS / v[1:])
    return v


def _on_main(fn):
    from bl_ext.user_default.comfyq_bridge.mcp.tools import on_main
    return on_main(fn, timeout=600)


def _graph():
    from bl_ext.user_default.comfyq_bridge.mcp.tools import _tree
    return _tree()


def node_by(label: str, names: list[str]):
    t = _graph()
    pool = [t.nodes[n] for n in names if n in t.nodes]
    return next((n for n in pool if n.label == label), None) or next((n for n in pool if n.name == label), None)


def clear_graph() -> None:
    def go():
        t = _graph()
        for n in list(t.nodes):
            t.nodes.remove(n)
    _on_main(go)


def set_frame(frame: int) -> None:
    def go():
        import bpy
        bpy.context.scene.frame_set(frame)
    _on_main(go)


def swap(label: str, path: str, names: list[str]) -> str:
    """Replace a source node (Scene Render / Scene Animation…) by a file node holding `path`, links kept."""
    def go():
        import bpy
        t = _graph()
        n = node_by(label, names)
        if n is None:
            raise RuntimeError(f"no node {label!r}")
        video = Path(path).suffix.lower() in VIDEO_EXT
        m = t.nodes.new("ComfyQVideoFileNode" if video else "ComfyQImageNode")
        m.location, m.label = n.location, f"{n.label or n.name} (file)"
        if video:
            m.filepath = path
        else:
            m.image = bpy.data.images.load(path, check_existing=True)
        for lk in list(n.outputs[0].links):
            t.links.new(m.outputs[0], lk.to_socket)
        t.nodes.remove(n)
        return m.name
    return _on_main(go)


def relink(label: str, key: str, names: list[str]) -> None:
    def go():
        from bl_ext.user_default.comfyq_bridge.graph import preset_ops
        t = _graph()
        n = node_by(label, names)
        lk = n.outputs[0].links[0]
        wf = lk.to_node
        preset_ops._enable(wf, key)
        sock = next(s for s in wf.inputs if s.identifier == key or s.name == key)
        t.links.remove(lk)
        t.links.new(n.outputs[0], sock)
    _on_main(go)


def fix_settings(wf_nodes: list[dict]) -> dict:
    """Seeds fixed, video length capped (only lowered)."""
    done = {}
    for w in wf_nodes:
        ok, desc = tool("comfyq_describe_workflow", workflow_id=w["workflow_id"])
        if not ok:
            continue
        for p in desc.get("parameters", []):
            lab, kind, dflt = (p.get("label") or "").lower(), p.get("kind"), p.get("default")
            v = None
            if kind == "seed":
                v = SEED
            elif kind in ("int", "float") and isinstance(dflt, (int, float)) and not lab.startswith(("frame rate", "fps")):
                if ("second" in lab or "duration" in lab) and dflt > CAP_SECONDS:
                    v = CAP_SECONDS
                elif "frame" in lab and ("length" in lab or "number" in lab or "count" in lab) and dflt > CAP_FRAMES:
                    v = CAP_FRAMES
            if v is not None and tool("blender_graph_set", node=w["name"], key=p["key"], value=v)[0]:
                done[f"{w['name']}: {(p.get('label') or p['key'])[:40]}"] = v
    return done


def run_card(c: dict) -> dict:
    rec = {"key": c["key"], "group": c["key"].split("/", 1)[0] if "/" in c["key"] else "assets",
           "label": c.get("preset") or c["key"].split("/", 1)[1], "scene": c["scene"],
           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "inputs": {}, "settings": {}}
    t0 = time.monotonic()
    clear_graph()
    if c.get("frame"):
        set_frame(c["frame"])
    for attempt in range(3):                        # the settings load from ComfyQ; retried after a slow start
        ok, added = tool("blender_graph_add_preset", preset=rec["label"])
        if ok:
            break
        log(f"   add preset: {added} (retry)")
        clear_graph()
        time.sleep(20)
    if not ok:
        return {**rec, "ok": False, "error": f"add preset: {added}"}
    names = list(added["nodes"])
    try:
        for label, path in (c.get("swap") or {}).items():
            names.append(swap(label, resolve(path), names))
            rec["inputs"][label] = Path(resolve(path)).name + " (instead of the scene)"
        for label, key in (c.get("relink") or {}).items():
            relink(label, key, names)
    except Exception as e:  # noqa: BLE001
        return {**rec, "ok": False, "error": f"swap/relink: {e}"}
    if c.get("select"):
        tool("blender_select", names=c["select"])
        rec["inputs"]["selected"] = c["select"]
    for label, path in (c.get("files") or {}).items():
        n = node_by(label, names)
        if n is None:
            return {**rec, "ok": False, "error": f"no node {label!r} for a file"}
        path = resolve(path)
        key = ("image" if n.bl_idname == "ComfyQImageNode" else "folder" if n.bl_idname == "ComfyQFolderNode"
               else "filepath")
        ok, msg = tool("blender_graph_set", node=n.name, key=key, value=path)
        if not ok:
            return {**rec, "ok": False, "error": f"{label}: {msg}"}
        rec["inputs"][label] = Path(path).name
    for label, kv in (c.get("set") or {}).items():
        n = node_by(label, names)
        if n is None:
            return {**rec, "ok": False, "error": f"no node {label!r} to set"}
        for k, v in kv.items():
            ok, msg = tool("blender_graph_set", node=n.name, key=k, value=resolve(v))
            if not ok:
                return {**rec, "ok": False, "error": f"{label}.{k}: {msg}"}
            rec["settings"][f"{label}: {k}"] = v
    for label, text in (c.get("prompts") or {}).items():
        n = node_by(label, names)
        if n is None:
            return {**rec, "ok": False, "error": f"no prompt node {label!r}"}
        tool("blender_graph_set", node=n.name, key="text", value=text)
    rec["prompt"] = "written for this series" if c.get("prompts") else "the preset's own"
    ok, g = tool("blender_graph_status")
    wf_nodes = [n for n in g["nodes"] if n["node"] in names and n.get("workflow_id")]
    rec["workflow_id"] = wf_nodes[-1]["workflow_id"] if wf_nodes else None
    rec["settings"].update(fix_settings([{"name": n["node"], "workflow_id": n["workflow_id"]} for n in wf_nodes]))
    ok, msg = tool("blender_graph_run", force=True)
    if not ok:
        return {**rec, "ok": False, "error": f"run: {msg}"}
    log(f"   run: {msg.get('message', msg) if isinstance(msg, dict) else msg}")
    timeout, last = c.get("timeout", 60 * 60), ""
    while time.monotonic() - t0 < timeout:
        time.sleep(10)
        ok, st = tool("blender_graph_status")
        if not ok:
            continue
        note = st.get("status") or ""
        if note != last:
            log(f"   {note}")
            last = note
        if st.get("running"):
            continue
        mine = [n for n in st["nodes"] if n["node"] in names and n.get("workflow_id")]
        jobs = [n for n in mine if n.get("job")]

        def made(n):
            p = Path(n["result"]) if n.get("result") else None
            return p.stat().st_mtime if p and p.exists() else float("inf")
        jobs.sort(key=made)                                  # chain order = order the results came back
        done = [n for n in jobs if n.get("result")]
        final = done[-1] if done else (jobs[-1] if jobs else {})
        failed = [n for n in jobs if n["job"] and n["job"].get("ok") is False]
        rec.update(seconds=round(time.monotonic() - t0), job_id=(final.get("job") or {}).get("job_id"),
                   chain_jobs=[n["job"]["job_id"] for n in jobs],
                   result=final.get("result"), workflow_id=final.get("workflow_id") or rec["workflow_id"],
                   error=st.get("error") or "; ".join(f"{n['node']}: {n['job'].get('error') or n.get('note')}"
                                                      for n in failed) or None)
        rec["ok"] = bool(rec["result"]) and not failed and not st.get("error")
        return rec
    def stop():
        from bl_ext.user_default.comfyq_bridge.graph.run import RUNS
        t = _graph()
        if t.name in RUNS:
            RUNS[t.name].stop(t)
    _on_main(stop)
    return {**rec, "ok": False, "error": f"timeout after {timeout // 60} min"}


def main() -> None:
    scene = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--scene=")), None)
    only = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("--only=")), None)
    ASSETS.mkdir(parents=True, exist_ok=True)
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.is_file() else []
    done = {r["key"] for r in results if r.get("ok")} if "--redo" not in sys.argv else set()

    def save(rec):
        results[:] = [r for r in results if r["key"] != rec["key"]] + [rec]
        RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        log(f"{'OK  ' if rec.get('ok') else 'FAIL'} {rec['key']} ({rec.get('seconds', '?')} s) {rec.get('error') or ''}")

    t0 = time.monotonic()
    while time.monotonic() - t0 < 600:              # connected to ComfyQ, workflows listed
        ok, info = tool("blender_scene_info")
        if ok and info.get("comfyq", {}).get("connected"):
            break
        time.sleep(5)
    time.sleep(5)
    for c in CARDS:
        if c["scene"] != scene or c["key"] in done or (only and not any(o.lower() in c["key"].lower() for o in only)):
            continue
        missing = [v for d in (c.get("files"), c.get("swap")) for v in (d or {}).values()
                   if str(v).startswith("@") and not Path(resolve(v)).exists()]
        if missing:
            save({"key": c["key"], "scene": scene, "ok": False, "error": f"missing inputs {missing}"})
            continue
        log(f"CARD {c['key']}")
        try:
            rec = run_card(c)
        except Exception as e:  # noqa: BLE001 - one card never stops the series
            rec = {"key": c["key"], "scene": scene, "ok": False, "error": f"{type(e).__name__}: {e}"}
        if rec.get("ok") and c["key"].startswith("asset:"):
            shutil.copyfile(rec["result"], ASSETS / c["key"].split(":", 1)[1])
        save(rec)
    log(f"SCENE DONE {scene}")


# -- orchestrating (plain python: one background Blender per scene) -------------------------------------------------
def orchestrate() -> None:
    scenes = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("--scenes=")), list(SCENES))
    extra = [a for a in sys.argv[1:] if a.startswith("--only=") or a == "--redo"]
    if "--fresh" in sys.argv and RESULTS.is_file():
        RESULTS.rename(RESULTS.with_suffix(f".{time.strftime('%H%M%S')}.json"))
    env = dict(__import__("os").environ, BLENDER_USER_RESOURCES=str(Path(__import__("os").environ["TEMP"])
                                                                     / "comfyq_bridge_smoke_blender"), PYTHONUTF8="1")
    for s in scenes:
        if not any(c["scene"] == s for c in CARDS):
            continue
        log(f"=== scene {s}: {SCENES[s][1]}")
        cmd = [BLENDER, "-b", SCENES[s][0], "--online-mode", "--python", str(ROOT / "tools/preset_run_headless.py"),
               "--", str(OUT), "--module=preset_series", f"--scene={s}", *extra]
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace")
        for line in p.stdout:
            if line.startswith("[") or "Traceback" in line or "Error" in line or "HEADLESS" in line:
                print(line.rstrip(), flush=True)
        p.wait()
    log("SERIES DONE")


if __name__ == "__main__":
    orchestrate()
