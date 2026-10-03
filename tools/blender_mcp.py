"""stdio MCP launcher for this repository's .mcp.json: runs the one shipped in the extension.

  python tools/blender_mcp.py
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[1] / "extension" / "comfyq_bridge" / "mcp" / "launcher.py"),
               run_name="__main__")
