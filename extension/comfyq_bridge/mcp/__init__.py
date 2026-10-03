# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP server for AI agents (Claude Code, Codex…): off by default, turned on in the add-on's preferences.

While it runs, `%APPDATA%\\comfyq-bridge\\mcp.json` holds its address and token (readable by this
Windows user only, like the rest of the profile); `tools/blender_mcp.py` reads it to connect agents.
A new token is made every time the server starts.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from ..client.profile import config_path

_SERVER = [None]
STATE = {"error": "", "url": "", "teacher": False}   # teacher = preference "Teacher tools" (read by tools)


def info_path() -> Path:
    return config_path().parent / "mcp.json"


def running() -> bool:
    return _SERVER[0] is not None


def start(port: int) -> bool:
    from .server import Dispatcher, McpHttpServer
    from .tools import INSTRUCTIONS, TOOLS
    stop()
    token = secrets.token_urlsafe(32)
    try:
        srv = McpHttpServer(Dispatcher(TOOLS, instructions=INSTRUCTIONS), token, port)
    except OSError as e:
        STATE.update(error=f"Port {port} is taken ({e.strerror or e}); choose another", url="")
        return False
    srv.start()
    _SERVER[0] = srv
    path = info_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": srv.url, "token": token, "pid": os.getpid()}), encoding="utf-8")
    STATE.update(error="", url=srv.url)
    print(f"ComfyQ MCP server on {srv.url}")
    return True


def stop() -> None:
    srv = _SERVER[0]
    if srv is None:
        return
    _SERVER[0] = None
    srv.stop()
    try:                                       # only our own file (another Blender may run one)
        if json.loads(info_path().read_text(encoding="utf-8")).get("pid") == os.getpid():
            info_path().unlink()
    except (OSError, ValueError):
        pass
    STATE.update(url="")
