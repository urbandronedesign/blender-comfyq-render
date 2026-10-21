---
name: blender-ui-audit
description: Audit or review the UI/UX of the ComfyQ Render extension (or any change to its panels, bar, node graph, preferences, operators) against Blender's Best Practice page, Blender's Human Interface Guidelines and Nielsen's heuristics for the classroom journeys. Use before and after any UI change, or when the user asks for a UX/UI review.
---

# Blender UI audit

First full audit: `docs/ux-audit-plan.md` (2026-10-04). It has the findings, the phases and the decisions
the user made: teacher controls are gated by an admin password in the profile, the bar is hidden in
graph mode, and presets are added beside the graph.

## Sources
- `reference/blender_ui_guidelines.txt` holds the text of two Blender pages:
  - [Best Practice](https://docs.blender.org/api/current/info_best_practice.html) (layout code);
  - [Human Interface Guidelines](https://developer.blender.org/docs/features/interface/human_interface_guidelines/) (best practices, writing style, tooltips, layouts, icons).
- Nielsen's 10 usability heuristics, with severity 0–4.

## Code checklist (every label, tooltip, layout, operator)
- **Labels:**
  - American English and MLA title case ("Render Scene", "Change Model...").
  - "..." when the operator opens a dialog or popup. **Never** on menus.
  - No "you"/"your", no abbreviations ("e.g.", "~", "(s)"), no contractions.
  - No implementation words: IDs, raw parameter keys, hex job ids, "string", "token", code snippets.
  - Booleans without "Use/Enable".
  - A button keeps its verb. Show state with `depress=`, an icon, or a separate label.
- **Tooltips (`bl_description`, property `description`):**
  - Start with a verb.
  - 1–3 lines, no trailing period for a single sentence.
  - No defaults, no hard-coded shortcuts.
  - Describe the positive state of toggles.
  - Every icon-only button has one.
  - One tooltip per meaning: use a `description` classmethod when the same operator has two buttons.
- **Disabled state:** every `poll` that can be False calls `cls.poll_message_set("why")`.
  `layout.enabled = False` needs a nearby reason.
- **Icons:**
  - A label is the default; icons work as anchors (one per menu section).
  - One icon, one meaning across the add-on: keep a table in docs/UI.md.
  - Icons without a label only when space is limited, and then with a tooltip.
- **Layout code:**
  - row/col/split/flow/sub variable names.
  - `use_property_split` and headings for settings.
  - Sub-panels (`layout.panel`) rather than nested boxes.
  - Dropdowns for enums with more than 3 items.
  - If the layout code outweighs the properties, rethink it.
- **Operators:**
  - `invoke_confirm` for destructive or shared-server actions (deleting files, restarting ComfyQ).
  - `self.report` for every outcome, including failures.
  - UNDO where scene data changes, unless async callbacks make it unsafe (gotcha 7).
- **draw():**
  - No ID data writes (gotcha 17).
  - No file parsing, TOML/JSON loading or directory scans: cache them by mtime or in the timer.

## UX method (Nielsen, classroom)
Walk these journeys and rate each problem 0–4:
1. First run and connecting (name, server).
2. The bar: render → model → prompt → Generate → wait → result.
3. The node graph: presets, editing, running, outputs.
4. Waiting, failures, cancelling.
5. The teacher: serving models, solo mode, presets, agents.

Always check:
- that every wait has a counter or estimate and a ✕;
- that every error says what to do next, in plain words;
- that students cannot change shared state (served models, the server restarting) without the admin password;
- that nothing is only discoverable from docs.

Sources of evidence:
- screenshots: `tools\smoke_test.ps1 -Ui` writes them to `live_check/ui/`. They may show an account email, so never publish them;
- `docs/UI.md`, `docs/CLASS.md`;
- the code.

## Output
- Findings grouped by category or journey, each with file:line, the current text, the rule it breaks, and a concrete replacement, ranked by severity.
- A phased plan (safety → feedback → wording → code health → layout → large items).
- The decisions the user has to make.
- No code changes until the user agrees. After changes: `smoke_test.ps1 -Ui`, and update docs/UI.md.
