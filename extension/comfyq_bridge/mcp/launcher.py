# SPDX-License-Identifier: GPL-3.0-or-later
"""stdio MCP launcher for AI agents: forwards JSON-RPC lines to the MCP server running inside Blender.

  python <extension folder>/mcp/launcher.py     (what .mcp.json / Codex's config.toml run; the path is
                                                 shown in the add-on preferences)

Ships with the extension, so student PCs need nothing else. Never imported by Blender.

Blender writes its server's address and token to %APPDATA%\\comfyq-bridge\\mcp.json while
"AI agents (MCP server)" is on in the ComfyQ Render preferences; this reads it on every message, so
Blender can be (re)started while the agent runs. Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HELP = ("Blender is not running with the ComfyQ MCP server: open Blender, then Edit › Preferences › "
        "Add-ons › ComfyQ Render › tick “AI agents (MCP server)”.")


def info_file() -> Path:
    env = os.environ.get("COMFYQ_BRIDGE_CONFIG")
    if env:
        return Path(env).expanduser().parent / "mcp.json"
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else (Path.home() / ("AppData/Roaming" if sys.platform == "win32" else ".config"))
    return root / "comfyq-bridge" / "mcp.json"


def forward(msg: dict | list) -> dict | list | None:
    info = json.loads(info_file().read_text(encoding="utf-8"))
    req = urllib.request.Request(info["url"], data=json.dumps(msg).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": f"Bearer {info['token']}"})
    with urllib.request.urlopen(req, timeout=660) as r:      # blender_render of an animation can take minutes
        body = r.read()
    return json.loads(body) if body else None


def offline(msg: dict) -> dict | None:
    """Answer without Blender: the agent starts, and every tool call says how to turn the server on."""
    mid = msg.get("id")
    if mid is None:
        return None
    if msg.get("method") == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {}}, "serverInfo": {"name": "blender-comfyq", "version": "offline"},
            "instructions": HELP}}
    if msg.get("method") == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
            "name": "blender_status", "description": "Why the Blender tools are missing, and how to fix it.",
            "inputSchema": {"type": "object", "properties": {}}}]}}
    if msg.get("method") == "tools/call":
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": HELP}], "isError": True}}
    if msg.get("method") == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": HELP}}


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            out = forward(msg)
        except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 401:
                sys.stderr.write("blender_mcp: token refused (Blender restarted its server?)\n")
            out = [r for r in (offline(m) for m in msg) if r] if isinstance(msg, list) else offline(msg)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
