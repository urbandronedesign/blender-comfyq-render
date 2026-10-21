# SPDX-License-Identifier: GPL-3.0-or-later
"""Call one tool of the running Blender's ComfyQ MCP server directly over HTTP (address + token from
%APPDATA%/comfyq-bridge/mcp.json). For driving the user's open Blender when the agent session's own MCP tool list is
stale (it is fixed when the session starts; Blender started later shows only blender_status).

    python tools/mcp_call.py <tool> ['{"json": "args"}']      e.g. blender_graph_add_preset '{"preset": "<label>"}'

Windows paths in the JSON: use forward slashes (backslashes are JSON escapes).
"""
import json
import os
import sys
from pathlib import Path

import requests

info = json.loads((Path(os.environ["APPDATA"]) / "comfyq-bridge" / "mcp.json").read_text(encoding="utf-8"))
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
r = requests.post(info["url"], headers={"Authorization": f"Bearer {info['token']}"}, timeout=1900,
                  json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": sys.argv[1], "arguments": args}})
msg = r.json()
if "error" in msg:
    print("ERROR", json.dumps(msg["error"], ensure_ascii=False))
    sys.exit(1)
res = msg["result"]
for c in res.get("content", []):
    print(c.get("text", c))
if res.get("isError"):
    sys.exit(1)
