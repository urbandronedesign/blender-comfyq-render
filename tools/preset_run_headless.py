# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/preset_run.py inside a BACKGROUND Blender: no window, no MCP port, the user's Blender stays free.

The runner works in a worker thread and calls the MCP tool functions in-process (mcp.tools.TOOLS); the main thread
pumps the session and the graph runs, as a window's timers do (gotcha 13: timers do not run in background mode).
Solo is turned on in the sandbox profile. Results go to <out>/results.json (resumable), assets to <out>/assets.

    blender -b <scene.blend> --online-mode --python tools/preset_run_headless.py -- <out folder> [--only=a,b] [--fresh]
"""
import importlib
import json
import sys
import threading
import time
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(ARGS[0])
MOD = "bl_ext.user_default.comfyq_bridge"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import preset_run  # tools/ (on sys.path above)


def ext(name):
    return importlib.import_module(f"{MOD}.{name}")


# Background Blender runs no timers (gotcha 13), and some steps poll with one (adding a preset waits for the
# workflows' parameters): a stand-in scheduler for every timer registered from here on, run by the main loop.
_TIMERS: list[list] = []                                    # [due time, function]


def _register(fn, first_interval=0.0, persistent=False):
    _TIMERS.append([time.monotonic() + (first_interval or 0.0), fn])


def _is_registered(fn):
    return any(t[1] is fn for t in _TIMERS)


def _unregister(fn):
    _TIMERS[:] = [t for t in _TIMERS if t[1] is not fn]


def run_timers():
    now = time.monotonic()
    for t in [t for t in _TIMERS if t[0] <= now]:
        if t not in _TIMERS:
            continue
        try:
            again = t[1]()
        except Exception as e:  # noqa: BLE001 - a failing timer is dropped, like Blender does
            print("timer error:", repr(e), flush=True)
            again = None
        if again is None:
            _unregister(t[1])
        else:
            t[0] = time.monotonic() + float(again)


bpy.app.timers.register, bpy.app.timers.is_registered, bpy.app.timers.unregister = (
    _register, _is_registered, _unregister)
addon_utils.enable(MOD, default_set=True)
S = ext("runtime.session").SESSION
grun = ext("graph.run")
tools = ext("mcp.tools")
ToolError = ext("mcp.server").ToolError
BY_NAME = {t.name: t for t in tools.TOOLS}
prefs = bpy.context.preferences.addons[MOD].preferences
prefs.solo_mode = True                                     # sandbox profile only


def tool(name: str, **args):
    """preset_run's MCP call, in-process: (ok, result)."""
    t = BY_NAME.get(name)
    if t is None:
        return False, {"error": f"no tool {name}"}
    try:
        return True, json.loads(json.dumps(t.fn(args), default=str))
    except ToolError as e:
        return False, {"error": str(e)}
    except Exception as e:  # noqa: BLE001 - recorded like the MCP server would
        return False, {"error": f"{type(e).__name__}: {e}"}


preset_run.tool = tool
preset_run.OUT = OUT
preset_run.ASSETS = OUT / "assets"
preset_run.RESULTS = OUT / "results.json"
sys.argv = [sys.argv[0], *ARGS[1:]]                        # --only / --fresh for preset_run.main

S.connect(prefs.profile, "preset-run", prefs.url.strip())
box = {}


def work():
    try:
        preset_run.main()
    except BaseException as e:  # noqa: BLE001
        box["error"] = repr(e)
        import traceback
        traceback.print_exc()


worker = threading.Thread(target=work, daemon=True)
worker.start()
while worker.is_alive():                                   # the main thread: what a window's timers would do
    S.pump()
    grun.tick_all()
    run_timers()
    time.sleep(0.1)
print("HEADLESS RUN END", box.get("error") or "ok", flush=True)
S.close()
