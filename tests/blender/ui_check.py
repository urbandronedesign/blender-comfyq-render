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


def click(op, where, **props):
    """Press button `op` in `where`: greyed out (poll fails) → "GREYED", else the operator's result."""
    fn = getattr(bpy.ops.comfyq, op)
    with ctx(where):
        if not fn.poll():
            return "GREYED"
        return fn("INVOKE_DEFAULT", **props)


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
        yield (lambda: ui_region(img()[1]).active_panel_category == "ComfyQ"), 8
        check("the ComfyQ window saved in the file is recognised and shows the ComfyQ tab",
              ui_region(img()[1]).active_panel_category == "ComfyQ", ui_region(img()[1]).active_panel_category)
    sc = bpy.context.scene
    report["scene_info"] = {"name": sc.name, "frames": f"{sc.frame_start}-{sc.frame_end}",
                            "camera": sc.camera.name if sc.camera else None,
                            "saved_workflow": sc.comfyq.workflow_id, "jobs": len(sc.comfyq.jobs)}
    yield 1.0
    shot("00_start")
    check("bar shows Connect before connecting", S().client is None)


def step_connect():
    res = click("connect", v3d)
    check("Connect button", res == {"FINISHED"}, res)
    yield lambda: S().connected and S().workflows
    check("connected", S().connected, S().status)
    wf = bpy.context.scene.comfyq.workflow_id
    yield (lambda: specs_ready(wf)), 10
    check("saved workflow's settings load after Connect (chips + Generate enabled)", specs_ready(wf), wf)
    with ctx(v3d):
        check("Generate enabled after Connect", bpy.ops.comfyq.generate.poll())
    shot("01_connected")


def step_refresh():
    wf = bpy.context.scene.comfyq.workflow_id
    res = click("refresh", v3d)
    check("Refresh button", res == {"FINISHED"}, res)
    yield lambda: not S().busy
    yield (lambda: specs_ready(wf)), 10
    check("settings still loaded after Refresh", specs_ready(wf))
    with ctx(v3d):
        check("Generate enabled after Refresh", bpy.ops.comfyq.generate.poll())


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
    yield (lambda: ui_region(img()[1]).active_panel_category == "ComfyQ"), 8
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
    res = click("cleanup", v3d)
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
    check("Generate keyframe button enabled", not greyed)
    it = props.param_item(sc, spec.key, wf)
    it.image = bpy.data.images.get("ComfyQ render preview")
    res = click("clear_keyframe", v3d, key=spec.key)
    it = props.param_item(sc, spec.key, wf)
    check("✕ clears the keyframe", res == {"FINISHED"} and it.image is None, res)
    it.source = keep


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
    for name, space in (("3D View", "VIEW_3D"), ("Image", "IMAGE_EDITOR")):
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


STEPS = [step_start, step_connect, step_refresh, step_tabs_and_menu, step_chips, step_prompt, step_match,
         step_render_scene, step_animation, step_player, step_library, step_cleanup, step_finished_job_card,
         step_keyframe, step_generate, step_disconnect_reconnect, step_reload_file, step_extras]


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


@persistent
def _after_load(_dummy):
    if _after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_after_load)
    bpy.app.timers.register(RUNNER.tick, first_interval=2.0, persistent=True)


def _open():
    addon_utils.enable(MOD, default_set=True)        # enabled before the file opens, as in real use
    shutil.copy2(SCENE_FILE, OUT / "scene.mixar")
    bpy.ops.wm.open_mainfile(filepath=str(OUT / "scene.mixar"))


bpy.app.handlers.load_post.append(_after_load)
bpy.app.timers.register(lambda: _open() and None, first_interval=4.0)
