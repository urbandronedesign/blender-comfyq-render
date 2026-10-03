"""Open a file in Blender, connect to ComfyQ and turn the MCP server on (for agent round-trip tests).

  blender --online-mode --python tools/mcp_session.py -- <file.blend|.mixar> <ready-marker-file>

Writes <ready-marker-file> once connected with the served workflows' settings loaded and the MCP
server running. Blender stays open for the agent; the caller closes it.
"""
import sys
from pathlib import Path

import addon_utils
import bpy
from bpy.app.handlers import persistent

ARGS = sys.argv[sys.argv.index("--") + 1:]
FILE, READY = ARGS[0], Path(ARGS[1])
MOD = "bl_ext.user_default.comfyq_bridge"


def ext(name):
    return sys.modules[f"{MOD}.{name}"]


def connect():
    S = ext("runtime.session").SESSION
    prefs = bpy.context.preferences.addons[MOD].preferences
    S.connect(prefs.profile, prefs.student_name.strip(), prefs.url.strip())

    def wait():
        if not (S.connected and S.served_ids()):
            return 0.5
        for wf in S.served_ids():
            if wf not in S.specs:
                ext("ops.workflow").request_specs(wf)
                return 0.5
        prefs.mcp_enabled = True                      # starts the server (preference update)
        if not ext("mcp").running():
            print("[mcp_session] MCP server did not start:", ext("mcp").STATE)
            return None
        READY.write_text(ext("mcp").STATE["url"], encoding="utf-8")
        print("[mcp_session] ready:", ext("mcp").STATE["url"], flush=True)
        return None
    bpy.app.timers.register(wait, first_interval=0.5)


def _unhook():
    if _after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_after_load)


@persistent
def _after_load(_dummy):
    bpy.app.timers.register(_unhook, first_interval=0.0)       # never remove a load_post handler inside itself
    bpy.app.timers.register(lambda: connect() and None, first_interval=1.5)


addon_utils.enable(MOD, default_set=True)
bpy.app.handlers.load_post.append(_after_load)
bpy.app.timers.register(lambda: bpy.ops.wm.open_mainfile(filepath=FILE) and None, first_interval=3.0)
