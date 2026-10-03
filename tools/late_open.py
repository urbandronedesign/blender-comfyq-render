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


@persistent
def _after_load(_dummy):
    bpy.app.handlers.load_post.remove(_after_load)
    code = compile(SCRIPT.read_text(encoding="utf-8-sig"), str(SCRIPT), "exec")
    exec(code, {"__name__": "__open_and_send__", "__file__": str(SCRIPT)})  # noqa: S102 - our own tool script


bpy.app.handlers.load_post.append(_after_load)
bpy.app.timers.register(lambda: bpy.ops.wm.open_mainfile(filepath=FILE) and None, first_interval=4.0)
