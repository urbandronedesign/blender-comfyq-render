"""Start Mixar, THEN open a file (avoids a Mixar 4.2 crash when launched directly with some files),
then run tools/open_and_send.py on it.

  mixar --online-mode --python tools/late_open.py -- <file.mixar> [--no-send] [--cleanup]
"""
import sys
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
FILE = ARGS[0]
SCRIPT = Path(__file__).with_name("open_and_send.py")


def _unhook():
    if _after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_after_load)


@persistent
def _after_load(_dummy):
    # Not removed here: Blender is walking the load_post list right now, and removing an entry
    # makes it skip the next handler (the add-on's own, in Blender 5.2's order). Remove it just after.
    bpy.app.timers.register(_unhook, first_interval=0.0)
    code = compile(SCRIPT.read_text(encoding="utf-8-sig"), str(SCRIPT), "exec")
    exec(code, {"__name__": "__open_and_send__", "__file__": str(SCRIPT)})  # noqa: S102 - our own tool script


bpy.app.handlers.load_post.append(_after_load)
bpy.app.timers.register(lambda: bpy.ops.wm.open_mainfile(filepath=FILE) and None, first_interval=4.0)
