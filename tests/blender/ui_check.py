"""GUI check of every ComfyQ button, run in a VISIBLE Mixar/Blender by tools/ui_test.ps1.

  mixar --online-mode --python tests/blender/ui_check.py -- <scene copy.mixar> <out dir> [--book]

Opens the scene after startup (Mixar crashes when launched directly with some files), then
presses every button of the bar, the ComfyQ window, the Player and the job cards the way a
click does: the operator runs with the window/area/region the button lives in, after its poll.
Drawing errors are caught. System viewers (Explorer, video player) are not opened: those calls
are recorded instead. With --book, Generate books one job on the served workflow and the test
cancels it at once. Writes <out dir>/report.json and screenshots, then quits.
"""
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy
from bpy.app.handlers import persistent

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SCENE_FILE, OUT = ARGS[0], Path(ARGS[1])
BOOK = "--book" in ARGS
OUT.mkdir(parents=True, exist_ok=True)
MOD = "bl_ext.user_default.comfyq_bridge"
results: list[dict] = []
draw_errors: list[str] = []
opened: list[str] = []            # what open_file() was asked to open (Explorer / system player)
report = {"scene": SCENE_FILE, "started": time.strftime("%H:%M:%S"), "results": results,
          "draw_errors": draw_errors, "opened": opened}


def save():
    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)})
    save()


def ext(name):
    return sys.modules[f"{MOD}.{name}"]


# -- where the buttons live ----------------------------------------------------------------------
def main_win():
    return bpy.context.window_manager.windows[0]


def v3d():
    win = main_win()
    return win, max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)


def img():
    win = ext("ops.workflow").comfyq_window()
    if win is None:
        raise RuntimeError("the ComfyQ window is not open")
    return win, next(a for a in win.screen.areas if a.type == "IMAGE_EDITOR")


def ui_region(area):
    return next(r for r in area.regions if r.type == "UI")


def ctx(where):
    win, area = where()
    return bpy.context.temp_override(window=win, screen=win.screen, area=area, region=ui_region(area))


def click(op, where, _mode="INVOKE_DEFAULT", **props):
    """Press button `op` in `where`: greyed out (poll fails) → "GREYED", else the operator's result.
    `_mode="EXEC_DEFAULT"` = the button's confirmation / dialog already answered (a popup would block the run)."""
    fn = getattr(bpy.ops.comfyq, op)
    with ctx(where):
        if not fn.poll():
            return "GREYED"
        return fn(_mode, **props)


def redraw():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            a.tag_redraw()
        with bpy.context.temp_override(window=w):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def shot(name):
    redraw()
    for n, w in enumerate(bpy.context.window_manager.windows):
        tag = "main" if n == 0 else ("comfyq" if w == ext("ops.workflow").comfyq_window() else f"win{n}")
        with bpy.context.temp_override(window=w):
            try:
                bpy.ops.screen.screenshot(filepath=str(OUT / f"{name}_{tag}.png"), check_existing=False)
            except RuntimeError as e:
                print("screenshot", name, e)


def install_hooks():
    """Record drawing errors and system-viewer calls instead of losing them in the console."""
    panel, studio, _player, wfops, gen = (ext("ui.panel"), ext("ui.studio"), ext("ui.player"),
                                         ext("ops.workflow"), ext("ops.generate"))

    def guard(fn, where):
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception:
                draw_errors.append(f"{where}: {traceback.format_exc()}")
                save()
                raise
        return wrapped

    panel.draw_studio = guard(studio.draw_studio, "draw_studio")
    panel.draw_jobs = guard(studio.draw_jobs, "draw_jobs")
    # Panel/menu methods are not wrapped (Blender calls them its own way): their errors are read
    # from the console log at the end instead.

    def fake_open(path):
        opened.append(str(path))
        save()
    wfops.open_file = fake_open
    gen.open_file = fake_open


# -- test steps (generators: yield a number to wait seconds, or a predicate to wait for) ------------
def S():
    return ext("runtime.session").SESSION


def specs_ready(wf_id):
    from_scene = {it.key for it in bpy.context.scene.comfyq.params if it.workflow_id == wf_id}
    return wf_id in S().specs and from_scene >= {s.key for s in S().specs[wf_id]}


def show_comfyq_tab(where):
    """Open the sidebar of `where` on the ComfyQ tab (as a user click on the tab does)."""
    _win, area = where()
    area.spaces.active.show_region_ui = True
    focus = ext("ops.workflow")._focus_comfyq_tab
    yield (lambda: focus(area)), 5


def step_start():
    install_hooks()
    yield from show_comfyq_tab(v3d)
    _win, area = v3d()
    check("3D View sidebar shows the ComfyQ tab", ui_region(area).active_panel_category == "ComfyQ")
    saved = ext("ops.workflow").comfyq_window()
    report["saved_comfyq_window"] = saved is not None
    if saved is not None:
        yield (lambda: ui_region(img()[1]).active_panel_category == "ComfyQ"), 30
        check("the ComfyQ window saved in the file is recognised and shows the ComfyQ tab",
              ui_region(img()[1]).active_panel_category == "ComfyQ",
              f"{ui_region(img()[1]).active_panel_category} focus log={ext('ops.workflow').FOCUS_LOG} "
              f"windows={[sorted(a.type for a in w.screen.areas) for w in bpy.context.window_manager.windows]}")
    sc = bpy.context.scene
    report["scene_info"] = {"name": sc.name, "frames": f"{sc.frame_start}-{sc.frame_end}",
                            "camera": sc.camera.name if sc.camera else None,
                            "saved_workflow": sc.comfyq.workflow_id, "jobs": len(sc.comfyq.jobs)}
    yield 1.0
    shot("00_start")
    check("bar shows Connect before connecting", S().client is None)


def step_connect():
    p = ext("prefs").prefs()
    check("no student name confirmed yet: Connect asks for it first (dialog)", not p.name_confirmed)
    res = click("connect", v3d, _mode="EXEC_DEFAULT", student_name="UI Check")   # = the name typed in the dialog
    check("Connect button (name typed in its dialog)", res == {"FINISHED"} and p.student_name == "UI Check"
          and p.name_confirmed, res)
    yield lambda: S().connected and S().workflows
    check("connected", S().connected, S().status)
    wf = bpy.context.scene.comfyq.workflow_id
    yield (lambda: specs_ready(wf)), 10
    check("saved workflow's settings load after Connect (chips + Generate enabled)", specs_ready(wf), wf)
    with ctx(v3d):
        served = wf in S().served_ids()
        why = ext("ops.generate").why_not_generate(bpy.context.scene, wf)
        check("Generate enabled after Connect when its model is served, else greyed with the reason",
              bpy.ops.comfyq.generate.poll() == served and (served or "not served" in why), why or "enabled")
    shot("01_connected")


def step_refresh():
    wf = bpy.context.scene.comfyq.workflow_id
    res = click("refresh", v3d)
    check("Refresh button", res == {"FINISHED"}, res)
    yield lambda: not S().busy
    yield (lambda: specs_ready(wf)), 10
    check("settings still loaded after Refresh", specs_ready(wf))
    with ctx(v3d):
        check("Generate after Refresh: enabled when its model is served, else greyed",
              bpy.ops.comfyq.generate.poll() == (wf in S().served_ids()))


def step_tabs_and_menu():
    props = ext("props")
    cq = bpy.context.scene.comfyq
    saved = cq.tab
    counts = {}
    for key, *_ in props.TABS:
        cq.tab = key
        counts[key] = sum(1 for w in S().workflows if props.tab_of(w) == key)
        redraw()
    cq.tab = saved
    check("every tab lists workflows", all(counts.values()), counts)
    served = sorted(S().served_ids())
    wf = served[0]
    res = click("set_workflow", v3d, workflow_id=wf)
    yield (lambda: specs_ready(wf)), 15
    check(f"model menu → {wf}", res == {"FINISHED"} and cq.workflow_id == wf and specs_ready(wf), res)
    check("tab follows the workflow", cq.tab == props.tab_of(next(w for w in S().workflows if w.id == wf)), cq.tab)


def step_chips():
    props = ext("props")
    sc = bpy.context.scene
    wf = sc.comfyq.workflow_id
    for s in S().specs[wf]:
        it = props.param_item(sc, s.key, wf)
        if s.kind == "enum" and len(s.options) > 1:
            old = it.e
            it.e = s.options[1] if old == s.options[0] else s.options[0]
            check(f"enum chip “{s.label[:30]}” is sent", it.value() == it.e, it.value())
            it.e = old
        if s.kind == "seed":
            it.randomize = False
            it.i = 1234
            check("seed chip", it.value() == 1234)
            it.randomize = True
    yield 0.2


def step_prompt():
    props = ext("props")
    sc = bpy.context.scene
    wf = sc.comfyq.workflow_id
    spec = next((s for s in S().specs[wf] if s.kind == "prompt"), None)
    if spec is None:
        check("prompt field", False, "served workflow has no prompt")
        return
    it = props.param_item(sc, spec.key, wf)
    if it.text is not None:                 # start from the one-line field
        click("unlink_prompt", v3d, key=spec.key, workflow_id=wf)
    it = props.param_item(sc, spec.key, wf)
    it.s = "ui check prompt, a red car"
    check("one-line prompt is what is sent", it.value() == "ui check prompt, a red car")
    wfops = ext("ops.workflow")
    old = wfops.comfyq_window()
    res = click("open_window", v3d)
    yield (lambda: wfops.comfyq_window() is not None and wfops.comfyq_window() != old), 5
    yield (lambda: ui_region(img()[1]).active_panel_category == "ComfyQ"), 30
    n_cq = sum(1 for w in bpy.context.window_manager.windows
               if sorted(a.type for a in w.screen.areas) == ["IMAGE_EDITOR", "TEXT_EDITOR"])
    check("window icon opens the ComfyQ window (one only), sidebar on the ComfyQ tab",
          res == {"FINISHED"} and n_cq == 1 and ui_region(img()[1]).active_panel_category == "ComfyQ",
          f"res={res} comfyq windows={n_cq} tab={ui_region(img()[1]).active_panel_category}")
    shot("02_window")
    res = click("edit_prompt", v3d, key=spec.key, workflow_id=wf)
    yield 0.5
    it = props.param_item(sc, spec.key, wf)
    win = wfops.comfyq_window()
    te = next((a for a in win.screen.areas if a.type == "TEXT_EDITOR"), None) if win else None
    check("⤢ shows the prompt in the ComfyQ window's Text Editor",
          res == {"FINISHED"} and it.text is not None and te is not None and te.spaces.active.text == it.text,
          f"res={res} text={it.text and it.text.name} editor={te and te.spaces.active.text}")
    it.text.clear()
    it.text.write("line one\nline two")
    check("multi-line prompt is what is sent", it.value() == "line one\nline two")
    shot("02_prompt_text")
    n_win = len(bpy.context.window_manager.windows)
    res = click("edit_prompt", img, key=spec.key, workflow_id=wf)
    check("Edit (from the ComfyQ window) reuses its Text Editor", res == {"FINISHED"}
          and len(bpy.context.window_manager.windows) == n_win, res)
    res = click("unlink_prompt", img, key=spec.key, workflow_id=wf)
    it = props.param_item(sc, spec.key, wf)
    check("✕ goes back to the one-line field", res == {"FINISHED"} and it.text is None
          and it.s == "line one line two", it.s)
    it.s = "ui check prompt, a red car"


def step_match():
    sc = bpy.context.scene
    studio, wfops, match, props = ext("ui.studio"), ext("ops.workflow"), ext("match"), ext("props")
    wf = sc.comfyq.workflow_id
    m = match.match(S().specs[wf], wfops.scene_facts(sc))
    for k in m.values:                          # detune, then press the button
        it = props.param_item(sc, k, wf)
        if it.kind in ("int", "seed"):
            it.i += 7
        elif it.kind == "float":
            it.f += 0.7
    res = click("match_scene", v3d)
    cur = {it.key: it.value() for it in props.workflow_items(sc, wf)}
    bad = {k: (cur.get(k), v) for k, v in m.values.items() if not studio._same(cur.get(k), v)}
    check("Match to scene", res == {"FINISHED"} and not bad, bad or m.notes)
    yield 0.2


def step_render_scene():
    sc = bpy.context.scene
    for style, *_ in ext("passes.render").STYLES:
        sc.comfyq.style = style
        res = click("preview_pass", v3d, mask=False)
        _, ia = img()
        shown = ia.spaces.active.image
        check(f"Render scene ({style}) from the bar → shown in the ComfyQ viewer",
              res == {"FINISHED"} and shown is not None and shown.name == "ComfyQ render preview", res)
        yield 0.3
    sc.comfyq.style = "CLAY"
    res = click("preview_pass", img, mask=False)
    shown = img()[1].spaces.active.image
    # What ComfyQ receives is the file: PNG colour type 0 = grayscale. (Pixels read back through
    # Blender are converted to the scene's working colour space and need not be equal per channel.)
    head = Path(bpy.path.abspath(shown.filepath)).read_bytes()[:26]
    check("Render scene (Player panel) is a grayscale clay render", res == {"FINISHED"} and head[25] == 0,
          f"{shown.size[0]}×{shown.size[1]} PNG colour type {head[25]}")
    check("viewer says what it shows", ext("ui.player").describe(shown) == "Scene render (camera view)",
          ext("ui.player").describe(shown))
    meshes = [o for o in sc.objects if o.type == "MESH" and o.visible_get()]
    if meshes:
        bpy.ops.object.select_all(action="DESELECT") if bpy.context.mode == "OBJECT" else None
        meshes[0].select_set(True)
        res = click("preview_pass", v3d, mask=True)
        shown = img()[1].spaces.active.image
        check("Mask button → mask preview", res == {"FINISHED"} and shown.name == "ComfyQ mask preview", res)
    shot("03_render_scene")


def step_animation():
    player = ext("ui.player")
    res = click("preview_animation", v3d, force=True, play=True)
    yield 0.5
    _, ia = img()
    shown = ia.spaces.active.image
    check("Render & play clay animation → plays in the ComfyQ viewer",
          res == {"FINISHED"} and shown is not None and shown.name == "ComfyQ animation pass"
          and player.is_playing(), f"res={res} shown={shown and shown.name} playing={player.is_playing()}")
    check("viewer label says it is the clay animation", player.describe(shown).startswith("Clay animation"),
          player.describe(shown))
    f0 = player._PLAY["frame"]
    yield 1.0
    check("playback advances", player._PLAY["frame"] > f0, f"{f0} → {player._PLAY['frame']}")
    shot("04_clay_playing")
    res = click("preview_animation", v3d, force=False, play=True)
    check("Replay", res == {"FINISHED"} and player.is_playing(), res)
    # without a ComfyQ window: falls back to an Image Editor or the system player, never errors
    yield 0.2


def step_player():
    player = ext("ui.player")
    wm = bpy.context.window_manager
    _, ia = img()
    frames, fps = player.movie_meta(ia.spaces.active.image)

    def act(a):
        return click("player", img, action=a)

    act("TOGGLE")
    check("⏯ pauses", not player.is_playing())
    act("FIRST")
    check("⏮ first frame", player._PLAY["frame"] == 1)
    act("NEXT")
    act("NEXT")
    check("▶| next frame", player._PLAY["frame"] == 3, player._PLAY["frame"])
    act("PREV")
    check("|◀ previous frame", player._PLAY["frame"] == 2, player._PLAY["frame"])
    act("LAST")
    check("⏭ last frame", player._PLAY["frame"] == frames, f"{player._PLAY['frame']} / {frames}")
    wm.comfyq_player_pos = 50.0
    mid = 1 + round(0.5 * (frames - 1))
    check("position slider seeks", player._PLAY["frame"] == mid and not player.is_playing(),
          f"{player._PLAY['frame']} vs {mid}")
    shot("05_player_mid")
    sc = bpy.context.scene
    keep = sc.frame_current
    wm.comfyq_player_sync = True
    act("FIRST")
    act("NEXT")
    check("Sync scene frame moves the scene", sc.frame_current == sc.frame_start + 1,
          f"scene frame {sc.frame_current}, start {sc.frame_start}")
    wm.comfyq_player_sync = False
    sc.frame_set(keep)
    wm.comfyq_player_speed = "2"
    act("PLAY")
    t0 = time.monotonic()
    samples = []
    for _ in range(10):
        yield 0.1
        samples.append((round(time.monotonic() - t0, 2), player._PLAY["frame"]))
    f2, dt = samples[-1][1], samples[-1][0]
    check("▶ plays from the start at 2×", player.is_playing() and f2 > fps * 1.6 * dt,
          f"frame {f2} after {dt} s, fps {fps}, speed {wm.comfyq_player_speed}, samples {samples}")
    wm.comfyq_player_speed = "1"
    wm.comfyq_player_loop = False
    act("LAST")
    act("TOGGLE")
    check("⏯ at the last frame restarts from the start", player.is_playing() and player._PLAY["frame"] <= 3,
          player._PLAY["frame"])
    wm.comfyq_player_loop = True
    n = len(opened)
    click("open_media", img, path=bpy.path.abspath(ia.spaces.active.image.filepath))
    check("⧉ opens the movie in the system player", len(opened) == n + 1, opened[-1:] )
    pv = bpy.data.images.get("ComfyQ render preview")
    res = click("show_media", img, image=pv.name) if pv else None
    check("Results menu → shows the chosen item", res == {"FINISHED"} and img()[1].spaces.active.image == pv, res)
    check("showing a still stops playback", not player.is_playing())


def step_library():
    n = len(opened)
    res = click("open_folder", v3d, job_id="")
    cache = str(ext("runtime.session").cache_dir())
    check("Library opens the results folder", res == {"FINISHED"} and opened[n:] == [cache], opened[n:])
    yield 0.1


def step_cleanup():
    sc = bpy.context.scene
    results_mod = ext("ops.results")
    mp4 = ext("passes.animation").last()
    seq = sc.sequence_editor_create()
    seq.strips.new_movie(name="ComfyQ fake result", filepath=str(mp4.path), channel=5, frame_start=sc.frame_start)
    redraw()
    check("bar warns about result strips in the scene", results_mod.scene_has_result_strips(sc))
    shot("06_strip_warning")
    res = click("cleanup", v3d, _mode="EXEC_DEFAULT")        # its "Clean Up?" confirmation answered
    check("Clean up removes them", res == {"FINISHED"} and not results_mod.scene_has_result_strips(sc), res)
    yield 0.1


def step_finished_job_card():
    """A finished job reattached from the server: every button of its card."""
    sc = bpy.context.scene
    client = S().client
    jobs = client.http.get("/jobs", status="completed", limit=200)["jobs"]
    pick = next((j for j in jobs if any((o.get("filename") or "").endswith(".mp4") for o in j.get("outputs") or [])),
                None) or next((j for j in jobs if j.get("outputs")), None)
    if pick is None:
        check("finished job card", False, "no completed job on the server")
        return
    sc.comfyq.auto_import = False
    rec = sc.comfyq.jobs.add()
    rec.job_id, rec.workflow_id, rec.label, rec.created = pick["id"], pick["workflow_id"], "ui check", time.time()
    S().reattach([(pick["id"], pick["workflow_id"])])
    yield (lambda: (v := S().jobs.get(pick["id"])) is not None and v.files), 60
    view = S().jobs.get(pick["id"])
    check("finished job: result downloaded", bool(view and view.files), view and view.files)
    if not view or not view.files:
        return
    f = view.files[0]
    shot("07_job_card")
    res = click("show_media", v3d, path=str(f))
    shown = img()[1].spaces.active.image
    check("card › Show → ComfyQ viewer", res == {"FINISHED"} and shown is not None
          and Path(bpy.path.abspath(shown.filepath)) == f, res)
    n = len(opened)
    res = click("open_media", v3d, path=str(f))
    check("card › Open → system player/viewer", res == {"FINISHED"} and opened[n:] == [str(f)], opened[n:])
    dst = OUT / "saved" / f.name
    with ctx(v3d):
        res = bpy.ops.comfyq.save_result("EXEC_DEFAULT", source=str(f), filepath=str(dst))
    check("card › Save as… copies the file", res == {"FINISHED"} and dst.is_file()
          and dst.stat().st_size == f.stat().st_size, res)
    with ctx(v3d):
        res = bpy.ops.comfyq.save_result("INVOKE_DEFAULT", source=str(f))
    check("card › Save as… opens the file browser", res == {"RUNNING_MODAL"}, res)
    yield 0.5
    _close_file_browser()
    res = click("import_file", v3d, job_id=pick["id"], path=str(f))
    rs = bpy.data.scenes.get("ComfyQ Results")
    rec = next(r for r in sc.comfyq.jobs if r.job_id == pick["id"])
    check("card › Import (video → ComfyQ Results scene, not the 3D scene)",
          res == {"FINISHED"} and rec.imported and (f.suffix != ".mp4" or (rs and len(rs.sequence_editor.strips)))
          and not ext("ops.results").scene_has_result_strips(sc), res)
    n = len(opened)
    res = click("open_folder", v3d, job_id=pick["id"])
    check("card › folder icon opens this job's folder", res == {"FINISHED"} and opened[n:]
          and Path(opened[-1]).name == pick["id"][:8], opened[n:])
    res = click("download", v3d, job_id=pick["id"])
    yield (lambda: S().jobs[pick["id"]].files and not S().jobs[pick["id"]].downloading), 30
    check("card › Download result", res == {"FINISHED"} and bool(S().jobs[pick["id"]].files), res)
    with ctx(v3d):
        bpy.ops.wm.call_panel(name="COMFYQ_PT_queue", keep_open=False)
    yield 0.3
    shot("08_queue_popover")
    check("Queue popover draws without error", not any("draw_jobs" in e for e in draw_errors))


def _close_file_browser():
    for w in list(bpy.context.window_manager.windows):
        if any(a.type == "FILE_BROWSER" for a in w.screen.areas) and w != main_win():
            with bpy.context.temp_override(window=w):
                bpy.ops.wm.window_close()


def step_keyframe():
    """AI keyframe controls (booking needs an image workflow being served: only checked if one is)."""
    sc = bpy.context.scene
    props = ext("props")
    wf = sc.comfyq.workflow_id
    spec = next((s for s in S().specs[wf] if s.kind == "image"), None)
    if spec is None:
        check("AI keyframe controls", True, f"skipped: {wf} has no image input")
        return
    it = props.param_item(sc, spec.key, wf)
    keep = it.source
    it.source = "KEYFRAME"
    kf = "image_qwen_image_2_1_control_depth"
    res = click("set_workflow", v3d, workflow_id=kf, role="keyframe")
    yield (lambda: specs_ready(kf)), 15
    check("keyframe model menu", res == {"FINISHED"} and sc.comfyq.keyframe_workflow_id == kf and specs_ready(kf)
          and sc.comfyq.workflow_id == wf, res)
    shot("09_keyframe")
    with ctx(v3d):
        greyed = not bpy.ops.comfyq.generate_keyframe.poll()
    kf = sc.comfyq.keyframe_workflow_id
    check("Generate keyframe: enabled when the keyframe model is served, else greyed",
          greyed == (kf not in S().served_ids()), f"{kf} served={kf in S().served_ids()}")
    it = props.param_item(sc, spec.key, wf)
    it.image = bpy.data.images.get("ComfyQ render preview")
    res = click("clear_keyframe", v3d, key=spec.key)
    it = props.param_item(sc, spec.key, wf)
    check("✕ clears the keyframe", res == {"FINISHED"} and it.image is None, res)
    it.source = keep


def graph_area():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == "NODE_EDITOR" and a.spaces.active.tree_type == "ComfyQGraphTree":
                return w, a
    raise RuntimeError("no ComfyQ graph editor open")


def _graph_open():
    try:
        graph_area()
        return True
    except RuntimeError:
        return False


def step_graph():
    """N0: the node graph opens in Mixar's Node Editor, nodes are built from the served workflow."""
    gtree = ext("graph.tree")
    sc = bpy.context.scene
    res = click("open_graph", v3d)
    yield _graph_open, 8
    check("bar › graph icon opens the ComfyQ graph in a Node Editor (Mixar accepts the tree type)",
          res == {"FINISHED"} and _graph_open(), res)
    if not _graph_open():
        return
    w, area = graph_area()
    tree = area.spaces.active.node_tree
    check("the graph editor shows the ComfyQ Graph", tree is not None and tree.bl_idname == gtree.TREE,
          tree and tree.name)
    wfops = ext("ops.workflow")
    check("N3: the graph opens in the ComfyQ window, next to the viewer (graph mode)",
          w == wfops.comfyq_window() and any(a.type == "IMAGE_EDITOR" for a in w.screen.areas),
          [a.type for a in w.screen.areas])
    tree.nodes.clear()
    wf = sc.comfyq.workflow_id
    r1 = click("graph_add", graph_area, node_type="ComfyQSceneAnimationNode")
    r2 = click("graph_add", graph_area, node_type="ComfyQWorkflowNode", workflow_id=wf)
    yield 0.5
    anim = next((n for n in tree.nodes if n.bl_idname == "ComfyQSceneAnimationNode"), None)
    node = next((n for n in tree.nodes if n.bl_idname == "ComfyQWorkflowNode"), None)
    check("toolbar › Scene › Scene Animation adds the node", r1 == {"FINISHED"} and anim is not None, r1)
    want = [s.key for s in S().specs[wf] if ext("graph.kinds").input_kind(s.kind)]
    check(f"toolbar › Video › {wf} adds a node with its inputs as sockets",
          r2 == {"FINISHED"} and node is not None and [x.identifier for x in node.inputs] == want,
          node and [x.identifier for x in node.inputs])
    vid = next((x for x in node.inputs if gtree.socket_kind(x) == "video"), None)
    feed = anim
    if vid is None:          # what ComfyQ serves changes (gotcha 32): an image model gets a Scene Render
        feed = tree.nodes.new("ComfyQSceneRenderNode")   # (the Scene Animation stays for the N4 output checks)
        feed.location = (node.location.x - 360, node.location.y - 320)
        vid = next(x for x in node.inputs if gtree.socket_kind(x) in ("image", "mask"))
    tree.links.new(feed.outputs[0], vid)
    anim.location = (node.location.x - 360, node.location.y)
    node.select = True
    tree.nodes.active = node
    with bpy.context.temp_override(window=w, area=area,
                                   region=next(r for r in area.regions if r.type == "WINDOW")):
        bpy.ops.node.view_all()
    yield 1.0
    check("Scene Animation wired into the video input", all(link.is_valid for link in tree.links)
          and len(tree.links) == 1)
    funcs = getattr(bpy.types.NODE_HT_header.draw, "_draw_funcs", [])
    check("graph toolbar (Scene · Image · Video · 3D · Splats · Audio · Tools · Clear) in the header",
          any(f.__name__ == "_header" for f in funcs))
    shot("09b_graph")
    # N1: "+" on the unlinked start image, the model list, the sidebar view of the node
    free = next((x for x in node.inputs if not x.is_linked and x.enabled), None)
    if free is not None:
        key = free.identifier
        res = click("graph_add_source", graph_area, tree=tree.name, node=node.name, key=key)
        sock = next(x for x in node.inputs if x.identifier == key)
        check(f"node › + on “{sock.name}” adds and links a source node", res == {"FINISHED"} and sock.is_linked, res)
    items = ext("graph.ui")._workflow_items(None, bpy.context)
    served = S().served_ids()
    check("node › Change model lists every workflow, served first",
          len(items) == len(S().workflows) and items[0][0] in served, f"{len(items)} items, first {items[0][0]}")
    area.spaces.active.show_region_ui = True
    with bpy.context.temp_override(window=w, area=area,
                                   region=next(r for r in area.regions if r.type == "WINDOW")):
        bpy.ops.node.view_all()
    yield 1.0
    shot("09c_graph_n1")
    # N2 without GPU: rendering a node's inputs as a run does, then showing results while Mixar idles,
    # must not make the node look out of date (it did in the live check: A re-ran every time).
    grun, gengine, anim_mod = ext("graph.run"), ext("graph.engine"), ext("passes.animation")
    before = grun.describe(tree, sc)
    h0 = gengine.node_hash(before, node.name)
    r = grun.Run(tree, sc, [node.name], before, [])
    r._collect(sc, node, S().specs[wf])
    pv = bpy.data.images.get("ComfyQ render preview")
    if pv is not None:
        click("show_media", graph_area, image=pv.name)
    node.job_id = "stability-check"
    yield 4.0
    after = grun.describe(tree, sc)
    changed = {n: (before[n].own, after[n].own) for n in after if n in before and after[n].own != before[n].own}
    check("rendering a node's inputs and showing results keeps it up to date (no false re-runs)",
          gengine.node_hash(after, node.name) == h0,
          f"changed={list(changed)} last edit={anim_mod.last_edit_by or 'none'}")
    node.job_id = ""
    with bpy.context.temp_override(window=w, area=area):
        res = bpy.ops.comfyq.graph_clear("EXEC_DEFAULT")
    check("Clear Graph", res == {"FINISHED"} and len(tree.nodes) == 0, res)
    click("graph_add", graph_area, node_type="ComfyQSceneAnimationNode")
    click("graph_add", graph_area, node_type="ComfyQWorkflowNode", workflow_id=wf)
    yield 0.3
    yield from _graph_n3(tree, wf)


def _graph_n3(tree, wf):
    """N3: header without truncated labels, Bar / Graph switch, Open as graph, viewer follows the node."""
    gui, gtree = ext("graph.ui"), ext("graph.tree")
    _w, area = graph_area()
    header = next(r for r in area.regions if r.type == "HEADER")
    room = gui.header_room(type("C", (), {"preferences": bpy.context.preferences, "region": header})())
    report["graph_header"] = {"width": header.width, "room": round(room), "labels": room >= gui._LABELS_NEED}
    check("N3: the toolbar picks labels or icons by the header width (no “Sce…” truncation)", room > 0,
          report["graph_header"])
    check("N3: no Group / Group Sockets / Node Width panels for the ComfyQ graph",
          tree.bl_use_group_interface is False)
    res = click("open_graph", img, toggle=True)
    yield (lambda: not _graph_open()), 5
    check("N3: window › Graph switch closes the graph (bar mode)", res == {"FINISHED"} and not _graph_open(), res)
    with ctx(img):
        bar_back = ext("ui.panel").COMFYQ_PT_image_main.poll(bpy.context)
        node_gone = not ext("graph.ui").COMFYQ_PT_viewer_graph_node.poll(bpy.context)
    check("D2: bar mode shows the bar again (no Selected Node panel)", bar_back and node_gone)
    res = click("open_graph", img, toggle=True)
    yield _graph_open, 5
    check("N3: window › Graph switch shows it again (graph mode)", res == {"FINISHED"} and _graph_open(), res)
    with ctx(img):
        bar_shown = ext("ui.panel").COMFYQ_PT_image_main.poll(bpy.context)
        node_shown = ext("graph.ui").COMFYQ_PT_viewer_graph_node.poll(bpy.context)
    check("D2: graph mode shows the selected node's panel instead of the bar", node_shown and not bar_shown,
          f"bar={bar_shown} node={node_shown}")
    yield 0.5
    anim = next((n for n in tree.nodes if n.bl_idname == "ComfyQSceneAnimationNode"), None)
    if anim is not None and gtree.animation_path():
        tree.nodes.active = anim
        yield 1.5
        thumb = gtree._PREVIEWS[0].get(gtree._MOVIES.get(gtree.animation_path(), "")) if gtree._PREVIEWS[0] else None
        check("N3: Scene Animation shows a thumbnail of the clay video", thumb is not None and thumb.icon_id > 0,
              thumb and tuple(thumb.image_size))
        _w, viewer = img()
        shown = viewer.spaces.active.image
        check("N3: selecting a node shows what it makes in the viewer",
              shown is not None and shown.name == "ComfyQ animation pass", shown and shown.name)
    n = len(tree.nodes)
    res = click("open_as_graph", v3d)
    yield 1.0
    added = list(tree.nodes)[n:]
    wnode = next((x for x in added if x.bl_idname == "ComfyQWorkflowNode"), None)
    check("N3: bar › Open as graph adds the bar's workflow, its settings and its sources",
          res == {"FINISHED"} and wnode is not None and wnode.workflow_id == bpy.context.scene.comfyq.workflow_id
          and any(x.is_linked for x in wnode.inputs), f"{res} {[x.bl_idname for x in added]}")
    shot("09f_open_as_graph")
    yield from _graph_n4(tree)
    yield from _graph_n5(tree)


def _graph_n5(tree):
    """N5: class presets from the header's Presets menu; Save Graph as Preset (into a test folder)."""
    import os
    gpo = ext("graph.preset_ops")
    before = len(tree.nodes)
    res = click("graph_preset", graph_area, preset="keyframe_v2v")
    yield (lambda: len(tree.nodes) > before), 30
    added = list(tree.nodes)[before:]
    wnodes = [n for n in added if n.bl_idname == "ComfyQWorkflowNode"]
    check("N5: Presets › AI keyframe → video builds the graph from what ComfyQ serves",
          res == {"FINISHED"} and len(wnodes) == 2 and all(lk.is_valid for lk in tree.links),
          f"{res} {[n.workflow_id for n in wnodes]} · {S().status_note}")
    yield 1.5
    shot("09h_preset")
    os.environ["COMFYQ_BRIDGE_PRESETS"] = str(OUT / "presets")
    shutil.rmtree(OUT / "presets", ignore_errors=True)
    with ctx(graph_area):
        res = bpy.ops.comfyq.graph_save_preset("EXEC_DEFAULT", name="UI check class", shared=True)
    listed = [h["label"] for _f, h in gpo.saved_presets()]
    check("N5: Save Graph as Preset writes a preset the Presets menu lists", res == {"FINISHED"}
          and "UI check class" in listed, listed)
    del os.environ["COMFYQ_BRIDGE_PRESETS"]


def _graph_n4(tree):
    """N4 without GPU: output nodes from the toolbar, Viewer / Save on the clay animation, sidebar tab."""
    gout = ext("graph.outputs")
    anim = next((n for n in tree.nodes if n.bl_idname == "ComfyQSceneAnimationNode"), None)
    added = {}
    for idn in ("ComfyQViewerNode", "ComfyQSaveNode", "ComfyQImportNode", "NodeReroute", "NodeFrame"):
        res = click("graph_add", graph_area, node_type=idn)
        added[idn] = tree.nodes.active if res == {"FINISHED"} and tree.nodes.active.bl_idname == idn else None
    check("N4: toolbar › Output adds Viewer, Save, Import to Scene, Reroute and Frame", all(added.values()),
          {k: bool(v) for k, v in added.items()})
    viewer, save, rr = added["ComfyQViewerNode"], added["ComfyQSaveNode"], added["NodeReroute"]
    if anim is None or viewer is None or save is None or rr is None:
        return
    if not ext("graph.tree").animation_path():   # Clean Up removed it and no video model ran (served set changes): render it
        sc_ = bpy.context.scene
        keep = sc_.frame_end
        sc_.frame_end = min(keep, sc_.frame_start + 23)
        try:
            ext("passes.animation").animation_pass(sc_, "CLAY", 512, force=True)
        finally:
            sc_.frame_end = keep
    x, y = anim.location
    viewer.location, rr.location, save.location = (x + 360, y + 380), (x + 330, y - 380), (x + 420, y - 420)
    tree.links.new(anim.outputs[0], viewer.inputs[0])
    tree.links.new(anim.outputs[0], rr.inputs[0])
    tree.links.new(rr.outputs[0], save.inputs[0])
    shutil.rmtree(OUT / "saved", ignore_errors=True)
    save.folder = str(OUT / "saved")
    yield 1.5                                            # the timer: Viewer shows it (Show when ready)
    _w, view = img()
    shown = view.spaces.active.image
    check("N4: Viewer fed by Scene Animation shows the clay video in the ComfyQ viewer (Show when ready)",
          shown is not None and bool(viewer.handled), f"{shown and shown.name} handled={bool(viewer.handled)}")
    res = click("graph_output", graph_area, tree=tree.name, node=save.name)
    files = list((OUT / "saved").glob("*.mp4"))
    check("N4: Save (through a Reroute) copies the clay video to its folder", res == {"FINISHED"} and files,
          f"{res} {[f.name for f in files]}")
    tree.nodes.active = viewer
    _w, area = graph_area()
    area.spaces.active.show_region_ui = True
    yield 0.5
    for region in area.regions:
        if region.type == "UI":
            try:
                region.active_panel_category = "ComfyQ"
            except (AttributeError, TypeError):
                pass
    yield 1.0
    check("N4: sidebar › ComfyQ tab shows the selected node", gout.linked(viewer) is not None)
    shot("09g_outputs")
    area.spaces.active.show_region_ui = False


def _small(node, specs):
    """Cheapest settings for a GPU test: no start image / prompt rewrite, smallest size, shortest clip."""
    for sp in specs:
        it = node.item(sp.key)
        low = sp.label.lower()
        if it is None:
            continue
        if sp.kind == "bool" and ("start image" in low or "enhance" in low):
            it.b = False
        elif sp.kind == "int" and "resolution" in low and sp.min is not None:
            it.i = int(sp.min)
        elif sp.kind == "float" and ("clip length" in low or "duration" in low):
            it.f = float(sp.min) if sp.min else 1.0


def step_graph_run():
    """N2 on the GPU (--book): Scene Animation → V2V A → V2V B, then reuse and cancel."""
    if not BOOK:
        return
    grun, gtree = ext("graph.run"), ext("graph.tree")
    sc = bpy.context.scene
    _w, area = graph_area()
    tree = area.spaces.active.node_tree
    tree.nodes.clear()
    wf = min(S().served_ids())
    specs = S().specs[wf]
    anim = tree.nodes.new("ComfyQSceneAnimationNode")
    a = tree.nodes.new("ComfyQWorkflowNode")
    b = tree.nodes.new("ComfyQWorkflowNode")
    for n, x in ((anim, -700), (a, -300), (b, 200)):
        n.location = (x, 0)
    for n, text in ((a, "ui check chain A: clay figures, warm light"), (b, "ui check chain B: same shot, night")):
        n.workflow_id = wf
        n.build(specs)
        _small(n, specs)
        n.item(next(sp.key for sp in specs if sp.kind == "prompt")).s = text
    names = (a.name, b.name)
    tree.links.new(anim.outputs[0], next(x for x in a.inputs if gtree.socket_kind(x) == "video"))
    tree.links.new(a.outputs[0], next(x for x in b.inputs if gtree.socket_kind(x) == "video"))
    # N4 on the GPU: B's result goes to a Viewer, an Import (when ready) and a Save (when ready)
    viewer, imp, save = (tree.nodes.new(i) for i in ("ComfyQViewerNode", "ComfyQImportNode", "ComfyQSaveNode"))
    for n, dy in ((viewer, 300), (imp, 0), (save, -300)):
        n.location = (700, dy)
        tree.links.new(b.outputs[0], n.inputs[0])
    shutil.rmtree(OUT / "saved_live", ignore_errors=True)
    imp.auto, save.auto, save.folder = True, True, str(OUT / "saved_live")
    out_names = (viewer.name, imp.name, save.name)
    n_jobs = len(sc.comfyq.jobs)
    run_started = round(time.time(), 2)
    res = click("graph_run", graph_area, tree=tree.name, node=names[1])
    started = dict(grun.RUNS[tree.name].nodes) if tree.name in grun.RUNS else {}
    check("node › Generate on B starts the chain A → B", res == {"FINISHED"} and tree.name in grun.RUNS,
          S().status_note)
    shot("09d_graph_running")
    yield (lambda: tree.name not in grun.RUNS), 1500
    a, b = tree.nodes[names[0]], tree.nodes[names[1]]
    check("chain finished: A and B have results", Path(a.result).is_file() and Path(b.result).is_file(),
          f"A={a.result} B={b.result} note={a.note or b.note} {S().status_note}")
    recs = {r.job_id: r for r in sc.comfyq.jobs[n_jobs:]}
    sent_b = json.loads(recs[b.job_id].inputs or "{}") if b.job_id in recs else {}
    check("B was given A's result (not a fresh clay render)", list(sent_b.values()) == [a.result], sent_b)
    stored = S().client.http.get(f"/jobs/{b.job_id}")["job"]
    check("ComfyQ stored B's own prompt", stored.get("prompt") == "ui check chain B: same shot, night",
          stored.get("prompt"))
    if not _graph_open():                         # seen once: the graph window was gone after the run
        report["graph_window_reopened"] = True
        click("open_graph", v3d)
        yield _graph_open, 8
    check("the graph editor is still open after the run", not report.get("graph_window_reopened"))
    yield 2.0                                         # the timer acts on the new result
    viewer, imp, save = (tree.nodes[n] for n in out_names)
    shown = img()[1].spaces.active.image
    check("N4 live: the Viewer shows B's result", shown is not None
          and bpy.path.abspath(shown.filepath) == b.result, shown and shown.filepath)
    check("N4 live: Import when ready brought B's result into the scene", "Imported" in imp.status
          and imp.imported, imp.status)
    check("N4 live: Save when ready copied B's result", list((OUT / "saved_live").glob("*.mp4")), save.status)
    check("N4 live: B's result is in its history", any(r.path == b.result for r in b.history), len(b.history))
    res = click("graph_run", graph_area, tree=tree.name, node=viewer.name)
    check("N4 live: Generate on the Viewer with everything up to date books nothing, just shows",
          res == {"FINISHED"} and tree.name not in grun.RUNS and S().status_note.startswith("Up to date"),
          S().status_note)
    for name in out_names:
        tree.nodes.remove(tree.nodes[name])
    res = click("show_media", graph_area, path=b.result)
    yield 0.5
    shot("09e_graph_done")
    report["graph_jobs"] = [a.job_id, b.job_id]
    # Generate on B again, nothing changed upstream: A is reused, only B is booked — then cancel it
    anim_mod, gengine = ext("passes.animation"), ext("graph.engine")
    now = grun.describe(tree, sc)
    report["reuse_diag"] = {
        "changed_own": [n for n in now if n in started and now[n].own != started[n].own],
        "A_hash_now": gengine.node_hash(now, names[0]), "A_result_hash": tree.nodes[names[0]].result_hash,
        "last_edit_by": anim_mod.last_edit_by, "A_inputs": now[names[0]].inputs,
        "edit_log": list(anim_mod.edit_log), "run_started": run_started, "now": round(time.time(), 2),
        "anim_own_then_now": [started.get(anim.name).own if anim.name in started else None, now[anim.name].own]}
    res = click("graph_run", graph_area, tree=tree.name, node=names[1])
    run = grun.RUNS.get(tree.name)
    check("Generate again reuses A and books only B", res == {"FINISHED"} and run is not None
          and run.pending == {names[1]} and "reusing" in S().status_note, S().status_note)
    yield (lambda: bool(tree.nodes[names[1]].job_id) and tree.nodes[names[1]].job_id != report["graph_jobs"][1]), 60
    jid = tree.nodes[names[1]].job_id
    click("cancel", graph_area, job_id=jid)
    yield (lambda: tree.name not in grun.RUNS), 90
    v = S().jobs.get(jid)
    check("node › ✕ cancels its job and ends the run", v is not None and v.job.phase == "cancelled"
          and tree.name not in grun.RUNS, v and v.job.phase)
    report["graph_jobs"].append(jid)


def step_generate():
    sc = bpy.context.scene
    if not BOOK:
        with ctx(v3d):
            check("Generate button enabled", bpy.ops.comfyq.generate.poll())
        return
    n = len(sc.comfyq.jobs)
    res = click("generate", v3d)
    check("Generate", res == {"FINISHED"}, res)
    yield (lambda: len(sc.comfyq.jobs) > n), 120
    if len(sc.comfyq.jobs) <= n:
        check("job booked", False, S().error)
        return
    rec = sc.comfyq.jobs[-1]
    jid = rec.job_id
    sent = json.loads(rec.inputs or "{}")
    check("job booked; card lists what was sent", bool(sent) and all(Path(p).is_file() for p in sent.values()),
          sent)
    res = click("cancel", v3d, job_id=jid)
    yield (lambda: (v := S().jobs.get(jid)) is not None and v.job.done), 60
    v = S().jobs.get(jid)
    check("card › ✕ cancels the job", res == {"FINISHED"} and v is not None and v.job.phase == "cancelled",
          v and v.job.phase)
    stored = S().client.http.get(f"/jobs/{jid}")["job"]
    check("ComfyQ stored the prompt", stored.get("prompt") == "ui check prompt, a red car", stored.get("prompt"))
    p = next(iter(sent.values()))
    res = click("show_media", v3d, path=p)
    check("card › Sent · Show", res == {"FINISHED"}, res)
    shot("10_generated")


def step_disconnect_reconnect():
    wf = bpy.context.scene.comfyq.workflow_id
    res = click("disconnect", v3d)
    check("Disconnect", res == {"FINISHED"} and S().client is None, res)
    yield 0.5
    shot("11_disconnected")
    res = click("connect", v3d)
    yield lambda: S().connected
    yield (lambda: specs_ready(wf)), 10
    check("reconnect: settings load again", specs_ready(wf))


def step_reload_file():
    """Saving and reopening the file while connected: the bar must come back by itself."""
    path = str(OUT / "reload.mixar")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    S().specs.clear()               # as after a fresh start (specs are per session)
    bpy.ops.wm.open_mainfile(filepath=path)
    yield 1.0
    install_hooks()
    wf = bpy.context.scene.comfyq.workflow_id
    yield (lambda: specs_ready(wf)), 10
    check("reopened file: settings load by themselves", specs_ready(wf), wf)
    yield from show_comfyq_tab(v3d)
    shot("12_reopened")


def step_extras():
    km = bpy.context.window_manager.keyconfigs
    for name, space in (("3D View", "VIEW_3D"), ("Image", "IMAGE_EDITOR"), ("Node Editor", "NODE_EDITOR")):
        ours = [k for k in km.addon.keymaps[name].keymap_items if k.idname == "comfyq.studio"]
        check(f"Ctrl+Shift+Q shortcut registered ({name})",
              bool(ours) and ours[0].ctrl and ours[0].shift and not ours[0].alt and ours[0].type == "Q")
        clash = [f"{m.name}: {k.idname}" for m in km.user.keymaps if m.space_type in (space, "EMPTY")
                 for k in m.keymap_items if k.active and k.type == "Q" and k.ctrl and k.shift and not k.alt
                 and k.idname != "comfyq.studio"]
        check(f"Ctrl+Shift+Q not taken by Mixar ({name})", not clash, clash)
    taken = {}
    for m in km.user.keymaps:
        for k in m.keymap_items:
            if k.active:
                taken.setdefault((k.type, bool(k.ctrl), bool(k.shift), bool(k.alt)), []).append(f"{m.name}:{k.idname}")
    report["shortcuts"] = {f"{'Ctrl+' if c else ''}{'Shift+' if sh else ''}{'Alt+' if a else ''}{key}":
                           taken.get((key, bool(c), bool(sh), bool(a)), "FREE")
                           for key in ("G", "Q", "J", "K", "Y", "U", "SPACE", "F", "E", "C")
                           for c, sh, a in ((1, 1, 0), (1, 0, 1), (0, 1, 1), (1, 1, 1))}
    hdr = bpy.types.VIEW3D_HT_header.draw._draw_funcs if hasattr(bpy.types.VIEW3D_HT_header.draw, "_draw_funcs") else []
    check("3D View header has the ComfyQ button", any(f.__name__ == "_header_button" for f in hdr))
    win, area = v3d()
    with bpy.context.temp_override(window=win, area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
        res = bpy.ops.comfyq.studio("INVOKE_DEFAULT")
    check("header ComfyQ button / Ctrl+Shift+Q opens the dialog", res == {"RUNNING_MODAL"}, res)
    yield 0.8
    shot("13_dialog")


def step_preferences():
    """D4: Blender's Preferences window on the ComfyQ add-on (Student · Server · Advanced · AI Agents ·
    Teacher). Drawing errors land in the console log (checked at the end)."""
    before = set(bpy.context.window_manager.windows)
    bpy.ops.screen.userpref_show()
    yield 1.0
    prefs = bpy.context.preferences
    prefs.active_section = "ADDONS"
    bpy.context.window_manager.addon_search = "ComfyQ"
    try:
        bpy.ops.preferences.addon_expand(module=MOD)
    except (RuntimeError, AttributeError) as e:
        print("addon_expand:", e)
    yield 1.5
    shot("14_preferences")
    new = [w for w in bpy.context.window_manager.windows if w not in before]
    check("D4: the Preferences window opens on the ComfyQ add-on", bool(new))
    with ctx(v3d):
        res = bpy.ops.comfyq.test_connection()
    check("D4: Test Connection answers", res == {"FINISHED"} and ext("prefs")._TEST["ok"], ext("prefs")._TEST["msg"])
    for w in new:
        with bpy.context.temp_override(window=w):
            bpy.ops.wm.window_close()
    yield 0.5


STEPS = [step_start, step_connect, step_refresh, step_tabs_and_menu, step_chips, step_prompt, step_match,
         step_render_scene, step_animation, step_player, step_library, step_cleanup, step_finished_job_card,
         step_keyframe, step_graph, step_graph_run, step_generate, step_disconnect_reconnect, step_reload_file, step_extras, step_preferences]


class Runner:
    def __init__(self):
        self.steps = list(STEPS)
        self.gen = None
        self.wait = None            # (predicate, deadline)

    def tick(self):
        try:
            S().pump()
        except KeyError:
            pass
        if self.wait is not None:
            pred, deadline = self.wait
            try:
                ready = pred()
            except Exception:  # noqa: BLE001
                ready = False
            if not ready and time.monotonic() < deadline:
                return 0.2
            self.wait = None
        while True:
            if self.gen is None:
                if not self.steps:
                    self.finish()
                    return None
                step = self.steps.pop(0)
                print("==", step.__name__, flush=True)
                self.name = step.__name__
                out = step()
                if out is None or not hasattr(out, "__next__"):
                    continue
                self.gen = out
            try:
                y = next(self.gen)
            except StopIteration:
                self.gen = None
                continue
            except Exception:  # noqa: BLE001
                check(f"{self.name} crashed", False, traceback.format_exc())
                self.gen = None
                continue
            if isinstance(y, tuple):
                self.wait = (y[0], time.monotonic() + y[1])
                return 0.2
            if callable(y):
                self.wait = (y, time.monotonic() + 30)
                return 0.2
            return float(y)

    def finish(self):
        for log in ("stderr.log", "console.log"):           # drawing errors of panels and menus
            try:
                text = (OUT / log).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for block in text.split("Traceback (most recent call last):")[1:]:
                if "comfyq_bridge" in block and "ui_check.py" not in block:
                    draw_errors.append(block[:1500])
        report["finished"] = time.strftime("%H:%M:%S")
        report["passed"] = sum(r["ok"] for r in results)
        report["failed"] = [r["check"] for r in results if not r["ok"]]
        save()
        print("== UI CHECK", "FAILED: " + "; ".join(report["failed"]) if report["failed"] else "PASSED", flush=True)
        if draw_errors:
            print("== DRAW ERRORS:", len(draw_errors))
        bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=1.0)


RUNNER = Runner()


def _unhook():
    if _after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_after_load)


@persistent
def _after_load(_dummy):
    # Not removed here: Blender is walking the load_post list right now, and removing an entry
    # makes it skip the next handler (the add-on's own, in Blender 5.2's order). Remove it just after.
    bpy.app.timers.register(_unhook, first_interval=0.0)
    bpy.app.timers.register(RUNNER.tick, first_interval=2.0, persistent=True)


def _open():
    addon_utils.enable(MOD, default_set=True)        # enabled before the file opens, as in real use
    shutil.copy2(SCENE_FILE, OUT / "scene.mixar")
    bpy.ops.wm.open_mainfile(filepath=str(OUT / "scene.mixar"))


bpy.app.handlers.load_post.append(_after_load)
bpy.app.timers.register(lambda: _open() and None, first_interval=4.0)
