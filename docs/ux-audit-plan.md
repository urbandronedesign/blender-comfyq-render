# UX / UI audit of the Blender plugin — plan for discussion (2026-10-04)

Nothing here is implemented yet. Each phase is a batch we agree on first, then build, test
(`smoke_test.ps1 -Ui` + docs/UI.md), and commit.

**Sources**
- Blender Python [Best Practice](https://docs.blender.org/api/current/info_best_practice.html): layout code.
- Blender [Human Interface Guidelines](https://developer.blender.org/docs/features/interface/human_interface_guidelines/): writing style, tooltips, layouts, icons.
- Nielsen's 10 usability heuristics, applied to the classroom journeys.
- Two read-only audits of the code (52 guideline findings, 25 UX findings), plus the GUI screenshots in `live_check/ui`.

Severity uses Nielsen's 0–4 scale (4 = must fix before class).

## What works and stays
- Served workflows are listed first, and the others say "ask the teacher".
- Settings are kept per workflow.
- Scene facts are checked against the workflow ("Matched").
- Job cards show "Sent ·" rows.
- Run Graph checks what is served and what is linked before it books anything.
- Clear Graph asks for confirmation.
- Keymaps are registered and unregistered cleanly.

## Phase A — classroom safety (severity 3–4, effort S) — DONE 2026-10-04
| # | Problem (where) | Proposal |
|---|---|---|
| A1 | Any student can tick **Solo** or **Teacher tools** in their preferences and restart ComfyQ for the whole class. **Load Model** does it without asking (`prefs.py:57-66`, `graph/ui.py:858`). ComfyQ has no admin password today. | Show Solo, Teacher tools and Load Model only when the profile has an admin password, or a `teacher = true` flag in `profiles.toml`. Show a persistent red "SOLO — you control the server" line while Solo is on. Ask for confirmation before every model switch ("Restart ComfyQ for everyone?"). |
| A2 | The default student name is the Windows login (`prefs.py:18`). Lab PCs share logins, so everyone's jobs look alike and "you can only cancel your own jobs" stops protecting anyone. | Ask for a name at the first Connect and refuse the OS login. Show "Connected as Léa · server" in the panel header, with an edit button. |
| A3 | **Clean Up** deletes files and images with no confirmation (`results.py:170`). | Confirm first, with the count: "Remove 3 strips and 12 temporary files?". |
| A4 | The Surfaces node tells every student "solo mode loads it on Run", even with Solo off (`tree.py:689`). | Use the workflow node's wording, "Not served: ask the teacher". Mention solo only when it is on. |

## Phase B — feedback & errors (severity 2–3, effort S–M) — DONE 2026-10-04 (Retry on job cards left out: a re-run with the same inputs needs the inputs kept per job; Remove done)
| # | Problem | Proposal |
|---|---|---|
| B1 | Buttons go grey with no reason: `poll_message_set` is used nowhere. This affects Generate, Run Graph, Refresh, Generate Keyframe, Match to Scene, Open as Graph and Load Model. | Give every poll a reason, shown in the tooltip ("Connect to ComfyQ first", "This model is not served: ask the teacher", "Settings are still loading"). |
| B2 | **Generate** stays active for an unserved model. It renders (even a whole clay animation) and only then fails, with raw ids (`generate.py:201`, `api.py:114`). | Check what is served in poll, which also covers B1. Use workflow display names in every message. |
| B3 | Long waits give no sense of progress. The queue just says "Waiting in queue". SAM3 runs 12 jobs in about 4 minutes. Model switches take 1–2 minutes. | Queue position and the user on the GPU ("In queue: 4th · running for Tom"). Counters: "Finding surfaces 5/12", "Loading model 0:42 / ~1:30". Use the same line on the node and in the graph header. |
| B4 | The Surfaces node and model switches have no ✕ to cancel. Failed jobs stay on cards that can't be removed. | Add ✕ for the Surfaces task and the model wait. Add **Retry** (same inputs) and **Remove** on job cards. |
| B5 | Connection errors read like "ConnectionError" (`http.py:55`). | "The class server is not answering — check that the teacher's PC is on, then Connect again", with a "Server Address..." button. |
| B6 | Some actions fail or finish silently: cancel, disconnect, open a missing file, download, Use this result. | `self.report` for every outcome. |

## Phase C — wording, tooltips, icons (Blender HIG; severity 1–2, effort S, mostly mechanical) — DONE 2026-10-04
- **Labels in title case:**
  - "Render Scene", "Match to Scene", "Generate Keyframe", "Change Model...", "Find Surfaces", "Clean Up"…
  - Preferences: "Student Name", "Connect on Startup", "Teacher Tools".
  - About 40 strings in total.
- **"..." on operators that open a dialog, never on menus:**
  - Add it: "Add Any Workflow...", "Save Graph as Preset...", "Change Model...".
  - Remove it from menus: "Choose a model…" becomes "Choose Model".
- **No "you"/"your", no abbreviations, no implementation words:**
  - pronouns: "Describe your scene here…", "your own renders show them"…;
  - abbreviations: "e.g.", "~1–2 min", "job(s)";
  - implementation words: hex job ids, raw parameter keys ("Sent loadimage_image_76"), and the `python …launcher.py` line in the preferences (becomes a "Copy Agent Command" button).
- **Labels that don't say what the button does:**
  - "Library" opens the results folder; rename it "Results Folder".
  - "Capture Viewport" renders the camera.
  - "Act" is unclear.
  - "Matched" and "In scene" turn a button into a status.
- **Tooltips:**
  - Add them to the icon-only buttons (✕ cancel, folder, disconnect, open).
  - Start each with a verb, keep it to 1–3 lines, don't document defaults, and don't hard-code "(Ctrl+Shift+Q)".
  - Give each button its own text where one tooltip now covers several (Generate vs ⟳ re-run).
- **One icon, one meaning:**
  - `PLAY` currently has 6 meanings: "served" gets a check or green dot, and Run Graph keeps PLAY.
  - `FILE_REFRESH` has 5: the seed gets dice, the player loop gets LOOP_FORWARDS, and re-run keeps FILE_REFRESH.
  - `X` cancel becomes `CANCEL`.
  - Pick one icon for rendering a pass.
  - In menus, icons go only on the first entry of each section.
- **Number display:** round values like "Clip length used 5.042" to "5.0 s".

## Phase D — layout & structure (severity 2–3, effort M) — DONE 2026-10-04 (D1 collapsed optional inputs; D2 graph mode = Selected Node panel; D3 presets in Frames + Run Graph scope/confirm; D4 preferences sections + Profile menu + Test Connection; D5 bar sections + narrow labels; D6 Ctrl+Shift+Q in the Node Editor + header-buttons preference)
| # | Problem | Proposal |
|---|---|---|
| D1 | Workflow nodes list every optional input. Qwen edit shows "Image 2…16: not linked", 15 "+ Scene render" buttons and 16 sockets; beginners think all of them are required. | Show the required inputs and the linked ones only. Replace the rest with "+ Add Reference Image (15 free)". Unlinked optional sockets stay hidden. |
| D2 | In graph mode the bar stays visible with its own model, prompt and Generate, which ignore the graph. Graph messages also leak into the bar's status line. | In graph mode, replace the bar with the selected node's panel (it already exists in the sidebar) and keep the viewer. Give the graph and the bar separate status lines. |
| D3 | Presets pile up next to the graph (the user's graph has about 60 groups). Run Graph runs *every* last node, so old chains get booked on the shared GPU. | Presets menu: **Replace Graph / Add Beside**, with each preset wrapped in a labelled Frame. Run Graph runs the selected Frame, or asks "Run 6 chains?". |
| D4 | Preferences are a flat list. Profile is a free-text field next to a list of names. | Use property split with headings (Student, Server, Agents, Teacher). Make Profile a dropdown. Move the URL override and paths into a collapsed **Advanced** section. Add a **Test Connection** button. |
| D5 | The bar is one big box with nested boxes. In the narrow sidebar, labels get cut off ("LTX 2.5 IC-LoR…", "Qu…"). | Collapsible sub-panels (`layout.panel`): Model & Settings, Prompt, Scene, Inputs, Jobs. A short model-name form for narrow widths. |
| D6 | Ctrl+Shift+Q doesn't work in the Node Editor, and the header buttons can't be hidden. | Add the keymap to the Node Editor. Add a preference for the header buttons. |

## Phase E — code health that users feel (effort S) — DONE 2026-10-04 (E1 `player.peek_meta`; E2 cached job inputs, Surfaces prompt, profile list — preset headers were already cached by mtime; E3 `ui.append_once` — the dialog's `check()` kept: returning False could stop it updating after edits; E4 no change: UNDO on async jobs is unsafe, gotcha 7)
| # | Problem | Proposal |
|---|---|---|
| E1 | The Player loads and removes a MovieClip **inside draw()** (`player.py:28`). This breaks our own gotcha 17. | Read the movie data once when the movie is shown, or from the timer. |
| E2 | Draw functions parse files on every redraw: every preset JSON (Presets menu), `profiles.toml` (preferences), each job's inputs JSON and file checks (bar), the Surfaces prompt and overlay (node). | Cache them, and refresh when the file's modification time changes or from the timer. |
| E3 | The dialog redraws on every event (`check()` always returns True). Header draw functions have no guard against being appended twice. | Fix both. |
| E4 | Some actions have no Undo support: Generate's seed change, and preview data-blocks. | Decide case by case, given the async callbacks (gotcha 7). |

## Phase F — larger items (effort L) — DONE 2026-10-04 (built in the order F5 → F4 → F1 → F3 → F2)
Smoke passes (F1–F5 checks), GUI `-Ui` 103/103. What differs from the designs below:
- F1: Cycles started from Python logs no samples, so the child script prints them from a `render_stats` handler.
  The MCP tools wait for the render on the agent's thread, so Blender is not frozen either.
- F2: the busy state is a word ("busy"), not ▶ (▶ means "run"). **Load Model** now needs only the teacher's PC, not
  Solo mode: Solo is about *automatic* loads during Run Graph.
- F3: the quickstart is an HTML page shipped in the extension (`help/student.html`), not Markdown, so it opens in the
  browser on every PC. `docs/STUDENT.md` points to it. The steps are one *How to use* Frame (a Text block) per preset.
- F5: Retry is greyed out in the card itself (a poll cannot see the job id); the reason is in its tooltip.

- **F1. Non-blocking Cycles render.** Today `passes/render.py cycles_pass` calls `subprocess.run`, so Blender
  freezes ("Not responding") for up to 30 minutes.
  - Use `subprocess.Popen` and poll it from a timer. A pass request returns a "pending" handle.
  - Read the progress from stdout: Cycles prints "Sample N/M". Show "Cycles 40/96" on the node / bar / MCP, with
    a ✕ that kills the process.
  - Callers to adapt:
    - `graph/run.py _file` (Scene Render style CYCLES): the run must wait for the pass, so add a "rendering"
      state and book the job when it is done;
    - `ops/workflow.py preview_pass` (Render Scene button);
    - MCP `blender_render` (it may stay blocking but return progress), and `blender_segment`'s render.
  - Stopgap (S), if F1 gets long: a wait cursor (`context.window.cursor_set("WAIT")`) plus a status message before
    the render.
  - Tests: smoke Cycles still (4 samples) through the new path; the GUI check (Render Scene with Cycles) stays
    responsive.
- **F2. Teacher "Server" panel + Class mode.**
  - A panel shown only on the teacher PC (`prefs.teacher_unlocked`):
    - served workflows (main + lanes, busy, VRAM) and card / used GB, from `client.server_lanes()`;
    - the queue: every unfinished job with user + workflow + phase, from `client.active_jobs()`;
    - the connected students, from ComfyQ's state if it lists users, otherwise from the jobs' users;
    - a model switch button: served list + Load (`runtime/serving.choose/serve`, `invoke_confirm`).
  - Where: the 3D View sidebar ComfyQ tab (sub-panel "Server") and the ComfyQ window.
  - **Class mode:** a `class_mode = true` flag in a profile (`client/profile.py`). It forces Solo off and hides
    every teacher control, even with an admin password. The teacher deploys that profile to the student PCs.
  - Tests: smoke (panel data from the live server, read only); `-Ui` screenshot.
- **F3. Student help.**
  - `docs/STUDENT.md`: a one-page quickstart (connect + name, choose a model, prompt, Generate, wait / queue,
    results; graph: add a preset, edit the prompt or materials, Run Graph, Stop).
  - A **?** button in the bar's top row and in the graph header, opening that page with `ops.workflow.open_file`.
    The extension ships the page: copy it into the extension folder at build time, or keep it in the extension
    folder.
  - The built-in presets (`graph/presets.py BUILTIN`) and the class presets get a Frame with "1. … 2. … 3. …" notes.
- **F4. Shared presets folder.**
  - A preference "Shared Presets Folder" (DIR_PATH).
  - A `presets_dir = "…"` key in a profile, so the teacher's profiles file sets it for the whole class.
  - `graph/presets.py folders()` reads both, plus the existing `COMFYQ_BRIDGE_PRESETS` environment variable.
  - The Presets menu shows where presets come from. Open Presets Folder offers the shared one too.
  - Tests: smoke (a temporary folder set in the preference is listed).
- **F5. Retry on failed jobs.**
  - The job record already stores the uploaded inputs (`rec.inputs` JSON: param key → local file).
  - Also store the values sent: a new `rec.values` JSON written by `ops/generate.book`.
  - **Retry** on a failed / cancelled card re-books the same workflow with the same values and the same files
    (re-uploaded if needed), with no new render. It is greyed out with the reason if a file is gone or the model
    is not served.
  - Tests: smoke (a fake failed record: the operator books with the same values, faked transport, or check poll /
    reason when the file is gone).

## Decisions (answered 2026-10-04)
1. admin password in the profile · 2. yes, hide the bar in graph mode (test it) · 3. add beside · 4. yes · 5. yes (`.claude/skills/blender-ui-audit`)

### Original questions
1. **A1, how to gate teacher controls:** admin password in the profile, `teacher = true` in `profiles.toml`, or both?
2. **D2:** may the bar disappear in graph mode (graph users then see only the viewer and the node panel)?
3. **D3:** what should a preset do by default: replace the graph, or add beside it?
4. **Order:** proposed A → B → C → E → D → F. Phase C is mechanical but touches about 100 strings, so it goes in one commit with a `-Ui` re-run.
5. **A reusable "Blender UI audit" skill** (HIG checklist plus Nielsen journeys) in `.claude/skills/`, so future UI changes are checked the same way?
