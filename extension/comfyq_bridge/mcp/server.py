# SPDX-License-Identifier: GPL-3.0-or-later
"""A minimal MCP server (Model Context Protocol, "streamable HTTP" without streaming): JSON-RPC 2.0
over POST /mcp on 127.0.0.1. No bpy here (unit-tested): the tools are plain callables given to it.

Safety: loopback only; every request needs `Authorization: Bearer <token>`; requests from web pages
are refused (an `Origin` header, or a Host other than localhost: DNS-rebinding protection).
"""
from __future__ import annotations

import hmac
import json
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROTOCOL = "2025-06-18"
SERVER_NAME = "blender-comfyq"


class ToolError(Exception):
    """A tool failed in a way the agent should read (returned as isError, not a protocol error)."""


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], Any]


def obj(props: dict | None = None, required: tuple[str, ...] = ()) -> dict:
    """JSON Schema of a tool's arguments."""
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


class Dispatcher:
    """JSON-RPC methods of MCP; transport independent (also used by tests)."""

    def __init__(self, tools: list[Tool], version: str = "0.1.0", instructions: str = ""):
        self.tools = {t.name: t for t in tools}
        self.version = version
        self.instructions = instructions

    def handle(self, msg: Any) -> Any:
        if isinstance(msg, list):                      # a batch
            out = [r for r in (self.handle(m) for m in msg) if r is not None]
            return out or None
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if mid is None:                                # a notification: nothing to answer
            return None
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": self._call(method, params)}
        except KeyError as e:
            return _error(mid, -32601, f"Method not found: {e}")
        except ValueError as e:
            return _error(mid, -32602, str(e))

    def _call(self, method: str, params: dict) -> dict:
        if method == "initialize":
            info = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": self.version}}
            if self.instructions:
                info["instructions"] = self.instructions
            return info
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.schema}
                              for t in self.tools.values()]}
        if method == "tools/call":
            tool = self.tools.get(params.get("name", ""))
            if tool is None:
                raise ValueError(f"Unknown tool: {params.get('name')}")
            args = params.get("arguments") or {}
            unknown = set(args) - set(tool.schema.get("properties", {}))
            missing = [k for k in tool.schema.get("required", []) if k not in args]
            if unknown or missing:
                return _text({"error": f"bad arguments: unknown {sorted(unknown)}, missing {missing}"}, True)
            try:
                return _text(tool.fn(args), False)
            except ToolError as e:
                return _text({"error": str(e)}, True)
            except Exception as e:  # noqa: BLE001 - reported to the agent, traced in the console
                traceback.print_exc()
                return _text({"error": f"{e.__class__.__name__}: {e}"}, True)
        raise KeyError(method)


def _text(result: Any, is_error: bool) -> dict:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


class McpHttpServer:
    """POST /mcp on 127.0.0.1:port, Bearer token. Runs in its own threads."""

    def __init__(self, dispatcher: Dispatcher, token: str, port: int = 0):
        self.dispatcher, self.token = dispatcher, token
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="comfyq-mcp", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):       # quiet: no request log in Blender's console
                pass

            def _reply(self, code: int, body: Any = None) -> None:
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                if body is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _allowed(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                if self.headers.get("Origin") or host not in ("127.0.0.1", "localhost"):
                    self._reply(403, {"error": "local agents only"})
                    return False
                auth = self.headers.get("Authorization") or ""
                if not hmac.compare_digest(auth.encode(), f"Bearer {server.token}".encode()):
                    self._reply(401, {"error": "missing or wrong token"})
                    return False
                return True

            def do_GET(self):                         # no server-sent events stream
                if self._allowed():
                    self._reply(405, {"error": "POST JSON-RPC to /mcp"})

            def do_DELETE(self):
                if self._allowed():
                    self._reply(405, {"error": "stateless server"})

            def do_POST(self):
                if self.path.rstrip("/") != "/mcp":
                    self._reply(404, {"error": "use /mcp"})
                    return
                if not self._allowed():
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    msg = json.loads(self.rfile.read(n) or b"null")
                except (ValueError, OSError):
                    self._reply(400, _error(None, -32700, "Parse error"))
                    return
                out = server.dispatcher.handle(msg)
                if out is None:
                    self._reply(202)                   # notifications only
                else:
                    self._reply(200, out)

        return Handler
