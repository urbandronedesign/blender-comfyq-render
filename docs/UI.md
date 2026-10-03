# ComfyQ bridge — user interface reference

Every place the bridge appears in Mixar (or Blender 5.x), every button, what it does, and how it is
checked. The checks run in a visible Mixar on a copy of a real scene:

```powershell
.\tools\smoke_test.ps1 -Ui          # presses every button (no GPU job)        → live_check\ui\report.json + screenshots
.\tools\smoke_test.ps1 -Ui -Book    # also Generate: books one job and cancels it at once
```

Last full run: **72 / 72 checks passed, no drawing errors** (Mixar 4.2.0 / Blender 5.2, 2026-10-03,
on `testV2V_clean.mixar`).

---

## 1. Where ComfyQ appears

| Place | How to open it | What it shows |
|---|---|---|
| **3D View sidebar** | `N` in the 3D View → **ComfyQ** tab | The generation bar + the **Jobs** list |
| **ComfyQ window** | Window icon (bar, or 3D View header) | Its own window: **viewer** (top), **Player** + bar + Jobs in the viewer's sidebar, **prompt editor** (bottom) |
| **Dialog** | **ComfyQ** button in the 3D View header, or **Ctrl+Shift+Q** (3D View, Image Editor) | The bar as a floating dialog; its **Generate** button sends the job |
| **Queue popover** | **Queue** button of the bar | The last 8 jobs of this scene |
| **Preferences** | Edit › Preferences › Add-ons › ComfyQ Bridge | Student name, profile, server URL, connect on startup |

The ComfyQ window is saved with the file. When the file is opened again it is recognised as the
ComfyQ window (players, previews and the prompt editor use it) and its sidebar is put back on the ComfyQ tab.
Pressing the window icon while it is open closes it and opens a fresh one in front (Blender cannot
raise a window). The icon is hidden inside the ComfyQ window itself.

---

## 2. The generation bar

Same code in the 3D View sidebar, the ComfyQ window and the dialog. In a narrow sidebar the tabs
become icons (hover for the name), long lines wrap and **Generate** gets its own row.

### 2.1 Top row
| Control | Does | Check |
|---|---|---|
| **Image / Video / 3D / Splats / Audio / Tools** tabs | Filters the model menu by category. Choosing a model also switches to its tab | `every tab lists workflows`, `tab follows the workflow` |
| Window icon | Opens the ComfyQ window (see §1) | `window icon opens the ComfyQ window (one only), sidebar on the ComfyQ tab` |
| Graph icon | Opens the **ComfyQ Graph** (node editor, Moodboard-style) in its own window — *build and wire nodes (see §8); running from the graph comes next* (docs/plans/node-graph.md) | `bar › graph icon opens the ComfyQ graph…` |
| **Library** | Opens the results folder `%LOCALAPPDATA%\comfyq-bridge\cache` in Explorer | `Library opens the results folder` |
| **Queue N** | Popover with the last jobs; N = jobs not finished yet | `Queue popover draws without error` |
| ⟳ (panel header) | **Refresh**: reloads the workflow list and what is served. The chosen workflow's settings reload by themselves | `Refresh button`, `settings still loaded after Refresh`, `Generate enabled after Refresh` |
| ⛓ (panel header) | **Disconnect** | `Disconnect` |
| **Connect** (when not connected) | Connects with the preferences' profile / URL / student name. The scene's chosen workflow (and AI-keyframe workflow) load their settings automatically — also after reconnecting or opening a file | `Connect button`, `connected`, `saved workflow's settings load after Connect`, `reconnect: settings load again`, `reopened file: settings load by themselves` |

**Warning box** (red) — *“AI results sit in this scene's sequencer…”*: shown when ComfyQ result strips
are in a 3D scene's Video Sequencer (they would replace the camera in every render, including the
clay pass). **Clean up** removes them, deletes temporary renders and old previews; job results are kept.
Check: `bar warns about result strips in the scene`, `Clean up removes them`.

### 2.2 Model and settings chips
| Control | Does | Check |
|---|---|---|
| **Model** chip (▶ = served now) | Menu of the tab's workflows: *Served now* first, then *Not served (ask the teacher)*. Each shows its VRAM badge (`VRAM ?` = not calibrated). Every workflow is listed | `model menu → <workflow>` |
| Red line *“Not served right now”* | The chosen workflow has no lane on the server: Generate would be refused | — |
| Number / toggle / list chips | The workflow's own settings (strength, resolution, clip length, toggles such as *Use a start image*). Kept **per workflow**: switching models never loses tweaks | `enum chip … is sent`, smoke test `switching workflows keeps each workflow's settings` |
| **Seed** + ⟳ | Seed value; ⟳ on = a new random seed for every job | `seed chip` |

### 2.3 Prompt
| Control | Does | Check |
|---|---|---|
| Prompt field | One-line prompt (large field) | `one-line prompt is what is sent` |
| ⤢ | Moves the prompt to a Text block for several lines and shows it in the ComfyQ window's Text Editor (opens the window if no Text Editor is open) | `⤢ shows the prompt in the ComfyQ window's Text Editor` |
| **Edit** (multi-line prompt) | Shows the Text block in the Text Editor again | `Edit (from the ComfyQ window) reuses its Text Editor` |
| ✕ (multi-line prompt) | Back to the one-line field (lines joined) | `✕ goes back to the one-line field` |
| Wrapped preview | Read-only view of the multi-line prompt (up to 10 lines) | `multi-line prompt is what is sent` |

The main prompt is also sent as the job's prompt, so ComfyQ's job cards show it (`ComfyQ stored the prompt`).

### 2.4 Scene strip (workflows with timing/size settings or a video input)
| Control | Does | Check |
|---|---|---|
| *Scene 0–120 · 24 fps · 5.0 s · 1280×720 · Camera* | The scene facts the workflow is matched against | — |
| **Match to scene** / **✓ Matched** | Sets duration / frame count / fps / size / aspect from the scene. Turns into *Matched* when nothing differs | `Match to scene` |
| **Render & play clay animation** | Renders the frame range through the scene camera (exactly what Generate sends) and plays it in the ComfyQ viewer (else any Image Editor, else the system player) | `Render & play clay animation → plays in the ComfyQ viewer`, `playback advances` |
| **Replay** | Plays the last clay render again without re-rendering | `Replay` |

Generate always renders the clay animation fresh, whatever this preview shows.

### 2.5 Inputs (references) — one block per image / video / mask / audio input
| Control | Does |
|---|---|
| Source list | **Scene render** (camera view, at the first/last frame for start/end inputs) · **AI keyframe** · **Render + selection mask** · **Blender image** · **File** · **Leave empty** · (video) **Scene animation** |
| Image picker / file path | Shown for *Blender image* / *File* |

### 2.6 AI keyframe (an image input set to *AI keyframe*)
A second, image workflow restyles the scene frame first; its result becomes the input.
| Control | Does | Check |
|---|---|---|
| **Choose the style model…** | Image workflows menu (role: keyframe; the main workflow is kept) | `keyframe model menu` |
| Its chips + prompt + inputs | That workflow's own settings | — |
| **Generate keyframe** / **Regenerate** | Books the keyframe job now (needs the keyframe workflow **served**) | `Generate keyframe button enabled` (booking not run: no image workflow served during the check) |
| ✕ | Forgets the keyframe; the next Generate makes a new one first | `✕ clears the keyframe` |

If the keyframe is missing, **Generate** makes it first and then sends the main job automatically.
*Not yet verified end to end on the server* (needs an image and a video workflow served together).

### 2.7 Actions
| Control | Does | Check |
|---|---|---|
| Style list | **Grayscale (clay)** (true grayscale PNG / video) · **Matcap** · **Material colors** | `Render scene (CLAY / MATCAP / MATERIAL)…` |
| **Render scene** | A still of the camera view in the chosen style, shown in the viewer | `Render scene (…) from the bar → shown in the ComfyQ viewer`, `… is a grayscale clay render` |
| **Mask** (when an input uses the mask) | Render with the **selected objects** transparent (= area to repaint) | `Mask button → mask preview` |
| **Generate** | Renders the inputs, uploads them, books the job, follows it, imports the result | `Generate`, `job booked; card lists what was sent` |

---

## 3. Jobs (sidebar list, Queue popover)

One card per job of this scene, newest first.

| Element | Does | Check |
|---|---|---|
| Title `Workflow (job id)` | — | — |
| **Sent · Source video (animation) ▶ Show** | The exact file that was sent (clay video, start image…). *(cleared)* once Clean up removed it | `card › Sent · Show` |
| Progress bar / *Waiting in queue* | Live status while running | — |
| ✕ (running job) | Cancels it (own jobs only) | `card › ✕ cancels the job` |
| 📁 (finished job) | Opens this job's folder | `card › folder icon opens this job's folder` |
| **Download result** | Shown when finished but not downloaded (e.g. reopened elsewhere) | `card › Download result` |
| **Show** | Result in the ComfyQ viewer (videos play there) | `card › Show → ComfyQ viewer` |
| **Open** | In the system viewer / player | `card › Open → system player/viewer` |
| **Save as…** | Copy anywhere (default: `comfyq` folder next to the .blend) | `card › Save as… opens the file browser`, `… copies the file` |
| **Import** / **✓ In scene** | Image → Image Editor; video / audio → separate **ComfyQ Results** scene (never the 3D scene's sequencer); GLB / PLY → a `ComfyQ <job>` collection. *In scene* asks before importing a second copy | `card › Import (video → ComfyQ Results scene, not the 3D scene)` |
| **Jobs shown** | How many cards to list | — |
| **Import results** | Import finished results automatically (off: use Import) | — |

---

## 4. Player (ComfyQ window → viewer sidebar, above the bar)

Plays videos in the viewer **without moving the scene's timeline**.

| Control | Does | Check |
|---|---|---|
| Style + **Render scene** | Same as the bar's (handy while reviewing) | `Render scene (Player panel) is a grayscale clay render` |
| Results menu (shows what is on screen: *Clay animation (sent to ComfyQ)*, *Scene render*, *AI result · job …*) | Pick the clay animation, a render, a keyframe or any result to show | `Results menu → shows the chosen item`, `viewer says what it shows` |
| ⏮ ◀| ⏯ |▶ ⏭ | First / previous / play-pause / next / last frame. ⏯ at the end restarts | `⏯ pauses`, `⏮ first frame`, `▶| next frame`, `|◀ previous frame`, `⏭ last frame`, `⏯ at the last frame restarts…` |
| ⟳ | Loop | — |
| **Frame n / N** slider | Seek (percent of the clip) | `position slider seeks` |
| ¼× ½× 1× 2× | Speed | `▶ plays from the start at 2×` |
| **Sync scene frame** | While on, the scene follows the player (compare a V2V result with the animation) | `Sync scene frame moves the scene` |
| ⧉ | Open the video in the system player | `⧉ opens the movie in the system player` |

---

## 5. Header, shortcut, preferences

| Control | Does | Check |
|---|---|---|
| **ComfyQ** (3D View header) / **Ctrl+Shift+Q** | Opens the dialog (connects first if needed) | `header ComfyQ button / Ctrl+Shift+Q opens the dialog`, `Ctrl+Shift+Q shortcut registered`, `Ctrl+Shift+Q not taken by Mixar` |
| Window icon (3D View header) | Opens the ComfyQ window | (same operator as §2.1) |
| Preferences: **Student name** | Name on your jobs; you can only cancel jobs booked under it | — |
| **Profile** / **Server URL override** | Which ComfyQ to use (`%APPDATA%\comfyq-bridge\profiles.toml`; tokens never go in the .blend) | — |
| **Connect on startup** | Connects when Mixar starts | — |

The shortcut was **Ctrl+Shift+G** until 2026-10-03: in Object Mode Mixar/Blender run *Add to active
collection* first, so it never reached ComfyQ.

---

## 6. Fixed on 2026-10-03 (found by the GUI check on the real scene)

1. **Generate greyed out / “Loading…” forever** after Connect, Refresh, reconnecting or opening a file:
   nothing reloaded the chosen workflow's settings. Now the timer loads any missing settings
   (`ops/workflow.py: ensure_loaded`).
2. **ComfyQ window not recognised after reopening the file** (it is saved in the file): Render & play
   opened the system player, ⤢ used the wrong editor, the icon stacked windows. The window is now
   marked on its screen and found again; older files are recognised by their layout.
3. **ComfyQ window sidebar stuck on “Tool”**: switching the tab fails with *read-only* until the
   sidebar has been drawn once, and that error stopped the retry. Now retried until it works.
4. **Ctrl+Shift+G** taken by Mixar's Object Mode → **Ctrl+Shift+Q**.
5. **Narrow sidebar**: tabs, the scene strip and the action row were cut off → icon tabs, wrapped
   lines, Generate on its own row.

## 7. Known limits
- The **dialog** does not redraw live progress (Blender redraws dialogs only on input); use the
  sidebar or the ComfyQ window to follow a job.
- The AI-keyframe chain has not run end to end on the server yet.
- Job cards cannot be removed from the list yet (lower **Jobs shown** instead).

## 8. ComfyQ Graph (node editor) — build and wire; running comes in N2

Open it with the graph icon of the bar. Saved with the file.

| Control | Does | Check |
|---|---|---|
| Header **Scene** | Adds a source node: Scene Animation (clay video), Scene Render (still, first/last/current frame), Selection Mask, Image, Video File, Audio File, Prompt | `toolbar › Scene › Scene Animation adds the node` |
| Header **Image · Video · 3D · Splats · Audio · Tools** | Adds a workflow node (served first, VRAM badge). Same entries in Shift+A | `toolbar › Video › … adds a node with its inputs as sockets` |
| Header **Clear Graph** | Removes every node (asks first) | `Clear Graph` |
| Workflow node **Change model** | Searchable list of every workflow (▶ = served). Settings kept per model; links come back on inputs of the same kind | `node › Change model lists every workflow, served first`; smoke `Change model … stays linked` |
| Workflow node chips | The workflow's settings (sidebar › Node: all of them, with the description) | smoke `settings stored on the node as chips` |
| Prompt field + ⤢ / Edit / ✕ | Same as the bar's prompt, per node | smoke `Prompt node: multi-line…` |
| **+ Scene render / Scene animation / …** next to an unlinked input | Adds the matching source node and links it | `node › + on “Start image” adds and links a source node` |
| Sockets | Inputs = the workflow's images / masks / videos / audio / prompts; output = what it makes. A wrong kind is drawn red and never sent | smoke `Image → video input is refused` |
| Served / Not served, VRAM | Live from the server | — |
