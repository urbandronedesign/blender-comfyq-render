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

## Phase B — feedback & errors (severity 2–3, effort S–M)
| # | Problem | Proposal |
|---|---|---|
| B1 | Buttons go grey with no reason: `poll_message_set` is used nowhere. This affects Generate, Run Graph, Refresh, Generate Keyframe, Match to Scene, Open as Graph and Load Model. | Give every poll a reason, shown in the tooltip ("Connect to ComfyQ first", "This model is not served: ask the teacher", "Settings are still loading"). |
| B2 | **Generate** stays active for an unserved model. It renders (even a whole clay animation) and only then fails, with raw ids (`generate.py:201`, `api.py:114`). | Check what is served in poll, which also covers B1. Use workflow display names in every message. |
| B3 | Long waits give no sense of progress. The queue just says "Waiting in queue". SAM3 runs 12 jobs in about 4 minutes. Model switches take 1–2 minutes. | Queue position and the user on the GPU ("In queue: 4th · running for Tom"). Counters: "Finding surfaces 5/12", "Loading model 0:42 / ~1:30". Use the same line on the node and in the graph header. |
| B4 | The Surfaces node and model switches have no ✕ to cancel. Failed jobs stay on cards that can't be removed. | Add ✕ for the Surfaces task and the model wait. Add **Retry** (same inputs) and **Remove** on job cards. |
| B5 | Connection errors read like "ConnectionError" (`http.py:55`). | "The class server is not answering — check that the teacher's PC is on, then Connect again", with a "Server Address..." button. |
| B6 | Some actions fail or finish silently: cancel, disconnect, open a missing file, download, Use this result. | `self.report` for every outcome. |

## Phase C — wording, tooltips, icons (Blender HIG; severity 1–2, effort S, mostly mechanical)
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

## Phase D — layout & structure (severity 2–3, effort M)
| # | Problem | Proposal |
|---|---|---|
| D1 | Workflow nodes list every optional input. Qwen edit shows "Image 2…16: not linked", 15 "+ Scene render" buttons and 16 sockets; beginners think all of them are required. | Show the required inputs and the linked ones only. Replace the rest with "+ Add Reference Image (15 free)". Unlinked optional sockets stay hidden. |
| D2 | In graph mode the bar stays visible with its own model, prompt and Generate, which ignore the graph. Graph messages also leak into the bar's status line. | In graph mode, replace the bar with the selected node's panel (it already exists in the sidebar) and keep the viewer. Give the graph and the bar separate status lines. |
| D3 | Presets pile up next to the graph (the user's graph has about 60 groups). Run Graph runs *every* last node, so old chains get booked on the shared GPU. | Presets menu: **Replace Graph / Add Beside**, with each preset wrapped in a labelled Frame. Run Graph runs the selected Frame, or asks "Run 6 chains?". |
| D4 | Preferences are a flat list. Profile is a free-text field next to a list of names. | Use property split with headings (Student, Server, Agents, Teacher). Make Profile a dropdown. Move the URL override and paths into a collapsed **Advanced** section. Add a **Test Connection** button. |
| D5 | The bar is one big box with nested boxes. In the narrow sidebar, labels get cut off ("LTX 2.5 IC-LoR…", "Qu…"). | Collapsible sub-panels (`layout.panel`): Model & Settings, Prompt, Scene, Inputs, Jobs. A short model-name form for narrow widths. |
| D6 | Ctrl+Shift+Q doesn't work in the Node Editor, and the header buttons can't be hidden. | Add the keymap to the Node Editor. Add a preference for the header buttons. |

## Phase E — code health that users feel (effort S)
| # | Problem | Proposal |
|---|---|---|
| E1 | The Player loads and removes a MovieClip **inside draw()** (`player.py:28`). This breaks our own gotcha 17. | Read the movie data once when the movie is shown, or from the timer. |
| E2 | Draw functions parse files on every redraw: every preset JSON (Presets menu), `profiles.toml` (preferences), each job's inputs JSON and file checks (bar), the Surfaces prompt and overlay (node). | Cache them, and refresh when the file's modification time changes or from the timer. |
| E3 | The dialog redraws on every event (`check()` always returns True). Header draw functions have no guard against being appended twice. | Fix both. |
| E4 | Some actions have no Undo support: Generate's seed change, and preview data-blocks. | Decide case by case, given the async callbacks (gotcha 7). |

## Phase F — larger items (effort L)
- **F1. Non-blocking Cycles render.** Today Blender freezes and shows "Not responding" for up to 30 minutes.
  - Run the render process while Blender stays responsive, and show "Cycles 40/96" with ✕.
  - Quick stopgap first: a wait cursor plus a status-bar warning.
- **F2. Teacher "Server" panel:** served models, VRAM, queue, connected students, and a switch button. Plus a **Class mode** profile flag that forces Solo off and hides the teacher controls on student PCs.
- **F3. Student help:**
  - a one-page quickstart, opened by a **?** button in the bar and in the graph header;
  - built-in presets carry a Frame with "Step 1 / 2 / 3" notes.
- **F4. Shared presets folder** as a preference, which `profiles.toml` can also set, instead of an environment variable or copying files by hand.

## Decisions (answered 2026-10-04)
1. admin password in the profile · 2. yes, hide the bar in graph mode (test it) · 3. add beside · 4. yes · 5. yes (`.claude/skills/blender-ui-audit`)

### Original questions
1. **A1, how to gate teacher controls:** admin password in the profile, `teacher = true` in `profiles.toml`, or both?
2. **D2:** may the bar disappear in graph mode (graph users then see only the viewer and the node panel)?
3. **D3:** what should a preset do by default: replace the graph, or add beside it?
4. **Order:** proposed A → B → C → E → D → F. Phase C is mechanical but touches about 100 strings, so it goes in one commit with a `-Ui` re-run.
5. **A reusable "Blender UI audit" skill** (HIG checklist plus Nielsen journeys) in `.claude/skills/`, so future UI changes are checked the same way?
