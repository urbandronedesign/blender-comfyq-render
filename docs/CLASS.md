# Running a class with ComfyQ in Blender

A guide for the teacher: what to set up before the class, how students start, and how to share
your own ready-made graphs. Every button is described in [UI.md](UI.md).

## 1. Before the class (teacher)

1. **ComfyQ server** (the GPU PC): start ComfyQ and choose the workflows to **serve** for this class
   (ComfyQ's own interface). Only served workflows can run; the others are listed but marked
   *Not served (ask the teacher)*. For animation work, serve a video-to-video workflow (e.g.
   *LTX 2.5 IC-LoRA*) and, for AI keyframes, an image workflow too (e.g. *Qwen Image 2.1 control depth*).
2. **Every student PC**: Blender 5.2 LTS with the *ComfyQ bridge* extension installed
   (Edit › Preferences › Get Extensions › Install from Disk › `comfyq_bridge-…zip`).
3. **Connection**: copy one `profiles.toml` to `%APPDATA%\comfyq-bridge\` on each PC with the server
   address, for example:
   ```toml
   default = "class"
   [profiles.class]
   comfyq = { url = "http://192.168.1.10:3000" }
   ```
   In the extension's preferences each student types their **name** (their jobs show under it in
   ComfyQ's queue).
4. **Class presets** (optional, §3): put your preset files in each PC's presets folder, or in one
   shared folder.
5. Check one PC: `tools\comfyq.ps1 doctor` (or just press **Connect** in the ComfyQ bar).

## 2. Starting from a preset (students)

1. Open the scene, press **Connect** in the ComfyQ bar (3D View sidebar › ComfyQ, or Ctrl+Shift+Q).
2. Press the **graph** icon: the ComfyQ window opens with the node graph on the left, the viewer
   and the bar on the right, and the prompt editor below.
3. Graph header › **Presets** › pick one:

| Preset | What it makes | What the student does |
|---|---|---|
| **Scene render → image** | An AI image of the camera view (the clay render drives the composition) | Write the prompt, **Generate** on the workflow node (or **Run Graph**) |
| **Clay animation → video** | The scene's animation restyled into a video (first frame as start image when the workflow uses one) | Prompt, **Generate**. The Viewer plays the result; **Save** keeps a copy |
| **AI keyframe → video** | The first frame restyled first (check it in the *Keyframe viewer*), then the video starts from it. One prompt drives both, so the look matches | Prompt, **Generate** on the video node: the keyframe runs first by itself |
| **Image → 3D model** | A textured 3D model from an image, imported into the scene when ready | Choose the image in the *Image* node, **Generate** |
| **Image → upscale** | The image upscaled for a final frame or print | Choose the image, **Generate** |

Built-in presets take the **workflows your ComfyQ serves right now** (served first, then the best
match), so the same preset works whatever you serve that day. A workflow node marked *Not served*
means you need to serve one of that kind, or the student picks another with **Change model**.

Good to know:
- A node's result is reused while nothing it depends on changes: pressing **Generate** on the video
  after only changing its prompt does not re-run the keyframe.
- Results stay on their node (sidebar › **ComfyQ** › *Results of this node* › **Use** brings back an
  earlier take).
- **Save** nodes write to a `comfyq` folder next to the .blend (Downloads for an unsaved file).

## 3. Your own presets

1. Build and tune a graph (models, settings, prompts, Frames with notes for the steps).
2. Graph header › **Presets** › **Save Graph as Preset…**: a name and a short description (shown as
   the menu tooltip).
3. Share it, either:
   - copy the `.json` file into `%APPDATA%\comfyq-bridge\presets\` on every student PC
     (**Presets › Open Presets Folder** opens yours in Explorer and shows its path in the status bar; it is
     empty until you use **Save Graph as Preset…**), or
   - put it in **one shared folder** and set the environment variable `COMFYQ_BRIDGE_PRESETS` to that
     folder on every PC (e.g. a network share of the teacher's PC). *Save in the shared folder* then
     saves there directly.
4. Students find it under **Presets › Saved**.

A saved preset is an exact copy: the same workflows (if one is not served that day it shows
*Not served*), settings, prompts and links. Image files are referenced by their path, so use images
every PC can reach (or let students pick theirs).

**Subfolders are submenus.** A preset file in a subfolder of the presets folder (one level, e.g.
`presets\Video → video\…json`) appears under **Presets › Saved › <subfolder>**: use them to sort a
long list by lesson or task.

### The workflow library (one preset per image / video workflow)
`presets\library\` in this repository holds 48 ready-made graphs, one per workflow of ComfyQ's image and
video categories, sorted into four submenus: **Image** (t2i, 9), **Image edit** (13), **Image → video**
(i2v, 11), **Video → video** (15). Each one is wired the same way:

- **Scene render** (clay, current frame) for the image to edit or the structure to follow; **First /
  Last frame** of the scene for videos that start or end on an image; **Clay animation** for a source video;
  **Mask (selected objects)** for inpainting;
- an **image file node** wherever the workflow wants a picture the scene cannot give: a character, a
  style or a reference, a portrait, a reference sheet, a stylized keyframe (the student picks the file);
  an audio file node for speech; optional extra images are left free;
- a **Prompt** node starting from the workflow's own example prompt; **Viewer + Save** on the result;
- the workflow's **default settings**, with its duration / size / fps **matched to the scene** the
  preset is added to (not frozen at the time it was made).

Install: copy the four folders of `presets\library\` into each PC's presets folder (or into the shared
`COMFYQ_BRIDGE_PRESETS` folder). Only workflows ComfyQ serves that day can run; the others show
*Not served*. Regenerate after ComfyQ's workflows change (headless, no GPU, extension installed in the
profile it runs with):
`blender -b --online-mode --python tools\make_preset_library.py -- presets\library [t2i,image-edit,i2v,video-edit]`

## 4. Fair use of the GPU

Every job goes through ComfyQ's queue (one GPU, first come first served): long videos block others.
Keep class presets short (a few seconds of animation, working resolution 512–768) and ask students
to **cancel** (✕ on the node) jobs they no longer need.
