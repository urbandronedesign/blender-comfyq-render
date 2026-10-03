import importlib.util
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("comfyq_mcp_server", ROOT / "extension/comfyq_bridge/mcp/server.py")
srv = importlib.util.module_from_spec(_spec)
sys.modules["comfyq_mcp_server"] = srv
_spec.loader.exec_module(srv)


def _boom(args):
    raise srv.ToolError("no scene")


TOOLS = [srv.Tool("echo", "Echo", srv.obj({"x": {"type": "string"}}, ("x",)), lambda a: {"got": a["x"]}),
         srv.Tool("fail", "Fails", srv.obj(), _boom)]


def rpc(d, method, params=None, mid=1):
    return d.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_protocol_methods():
    d = srv.Dispatcher(TOOLS, instructions="hi")
    init = rpc(d, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26" and init["capabilities"]["tools"] is not None
    assert init["instructions"] == "hi"
    assert [t["name"] for t in rpc(d, "tools/list")["result"]["tools"]] == ["echo", "fail"]
    assert d.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc(d, "nope")["error"]["code"] == -32601


def test_tool_calls_and_errors_reach_the_agent():
    d = srv.Dispatcher(TOOLS)
    ok = rpc(d, "tools/call", {"name": "echo", "arguments": {"x": "a"}})["result"]
    assert not ok["isError"] and json.loads(ok["content"][0]["text"]) == {"got": "a"}
    assert rpc(d, "tools/call", {"name": "fail", "arguments": {}})["result"]["isError"]
    bad = rpc(d, "tools/call", {"name": "echo", "arguments": {"y": 1}})["result"]
    assert bad["isError"] and "unknown ['y']" in bad["content"][0]["text"]
    assert rpc(d, "tools/call", {"name": "missing"})["error"]["code"] == -32602


def test_gated_tools_are_hidden_and_refused_while_off():
    on = [False]
    d = srv.Dispatcher(TOOLS + [srv.Tool("serve", "Teacher", srv.obj(), lambda a: "served",
                                         lambda: on[0], "Teacher tools are off")])
    assert "serve" not in [t["name"] for t in rpc(d, "tools/list")["result"]["tools"]]
    off = rpc(d, "tools/call", {"name": "serve", "arguments": {}})["result"]
    assert off["isError"] and "Teacher tools are off" in off["content"][0]["text"]
    on[0] = True
    assert "serve" in [t["name"] for t in rpc(d, "tools/list")["result"]["tools"]]
    assert rpc(d, "tools/call", {"name": "serve", "arguments": {}})["result"]["content"][0]["text"] == "served"


@pytest.fixture
def live():
    s = srv.McpHttpServer(srv.Dispatcher(TOOLS), "secret-token", 0)
    s.start()
    yield s
    s.stop()


def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, (json.loads(r.read() or b"null"))
    except urllib.error.HTTPError as e:
        return e.code, None


def test_http_needs_the_token_and_refuses_web_pages(live):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert post(live.url, msg, {})[0] == 401
    assert post(live.url, msg, {"Authorization": "Bearer wrong"})[0] == 401
    auth = {"Authorization": "Bearer secret-token"}
    assert post(live.url, msg, {**auth, "Origin": "http://evil.example"})[0] == 403
    assert post(live.url, msg, auth) == (200, {"jsonrpc": "2.0", "id": 1, "result": {}})
    assert post(live.url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, auth)[0] == 202


def _launcher(tmp_path, lines):
    env = {"COMFYQ_BRIDGE_CONFIG": str(tmp_path / "profiles.toml"), "SYSTEMROOT": "C:\\Windows", "PATH": ""}
    p = subprocess.run([sys.executable, str(ROOT / "tools/blender_mcp.py")], input="\n".join(lines) + "\n",
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=30, check=True)
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]


def test_stdio_launcher_forwards_to_blender(live, tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({"url": live.url, "token": "secret-token"}), encoding="utf-8")
    out = _launcher(tmp_path, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"x": "é ✓"}}})])
    assert [o["id"] for o in out] == [1, 2]
    assert json.loads(out[1]["result"]["content"][0]["text"]) == {"got": "é ✓"}


def test_stdio_launcher_explains_when_blender_is_off(tmp_path):
    out = _launcher(tmp_path, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "x"}})])
    assert out[0]["result"]["serverInfo"]["version"] == "offline"
    assert out[1]["result"]["isError"] and "Preferences" in out[1]["result"]["content"][0]["text"]
