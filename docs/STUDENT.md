# Student quickstart

The quickstart students read is a web page shipped with the extension:
[`extension/comfyq_bridge/help/student.html`](../extension/comfyq_bridge/help/student.html). The **?** button
(the bar's top row, and the graph header) opens it in the web browser, on every PC, with no network access.

It covers, on one page:
1. **Start:** open the bar (sidebar tab or Ctrl+Shift+Q), Connect, type your name.
2. **The bar:** tab, model (✓ = served now), prompt, Render Scene, Generate, the queue place.
3. **The graph:** Presets and their *How to use* notes, edit the Prompt / Surfaces list, Run Graph (selection or
   all, unchanged steps reused), Stop, the Cycles progress and its ✕.
4. **Results:** automatic import, Show / Open / Save As… / Import, **Retry** on failed jobs, Results Folder.
5. **When something is greyed out:** hover for the reason; the usual reasons and what to do.

Edit the HTML file (keep it short and in the words of the buttons, see [UI.md](UI.md)); the next build ships it.
Each built-in preset also carries its own numbered steps (`graph/presets.py`, `steps=`); the class presets in
`presets/class/` carry theirs as a *How to use* Frame (its text is saved in the preset).
