# SPDX-License-Identifier: Apache-2.0
"""Connection profiles: which ComfyQ to use, as whom, and with which token.

File: %APPDATA%\\comfyq-bridge\\profiles.toml (~/.config/comfyq-bridge on
Linux/macOS), or the path in COMFYQ_BRIDGE_CONFIG. The teacher can deploy one
file to every classroom PC. Environment overrides: COMFYQ_BRIDGE_PROFILE,
COMFYQ_URL, COMFYQ_TOKEN, COMFYQ_PASSWORD, COMFYQ_USER.

Secrets are read from the environment or a file only; a .blend never stores
more than the profile name.
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:3000"


@dataclass(frozen=True)
class Profile:
    name: str
    url: str = DEFAULT_URL
    user: str = ""
    token: str = ""           # x-access-token (sha256 handed out by POST /access/login)
    password: str = ""        # access password; exchanged for a token at connect
    gpu_policy: str = "none"  # none | shared (ComfyQ on the same GPU as Mixar)
    mixar_reserve_gb: float = 6.0
    timeout_s: float = 30.0


def config_path() -> Path:
    env = os.environ.get("COMFYQ_BRIDGE_CONFIG")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "comfyq-bridge" / "profiles.toml"


def _secret(section: dict, key: str) -> str:
    """`key`, `key_env` (variable name) or `key_file` (path)."""
    if section.get(f"{key}_env"):
        return os.environ.get(section[f"{key}_env"], "")
    if section.get(f"{key}_file"):
        p = Path(section[f"{key}_file"]).expanduser()
        return p.read_text(encoding="utf-8").strip() if p.is_file() else ""
    return str(section.get(key) or "")


def list_profiles(path: Path | None = None) -> list[str]:
    data = _load(path)
    return sorted((data.get("profiles") or {}).keys()) or ["local"]


def _load(path: Path | None) -> dict:
    p = path or config_path()
    if not p.is_file():
        return {}
    with p.open("rb") as f:
        return tomllib.load(f)


def load_profile(name: str | None = None, path: Path | None = None) -> Profile:
    data = _load(path)
    name = name or os.environ.get("COMFYQ_BRIDGE_PROFILE") or data.get("default") or "local"
    section = (data.get("profiles") or {}).get(name) or {}
    comfyq = section.get("comfyq") or {}
    gpu = section.get("gpu") or {}
    return Profile(
        name=name,
        url=(os.environ.get("COMFYQ_URL") or comfyq.get("url") or DEFAULT_URL).rstrip("/"),
        user=os.environ.get("COMFYQ_USER") or comfyq.get("user") or "",
        token=os.environ.get("COMFYQ_TOKEN") or _secret(comfyq, "token"),
        password=os.environ.get("COMFYQ_PASSWORD") or _secret(comfyq, "password"),
        gpu_policy=gpu.get("policy") or "none",
        mixar_reserve_gb=float(gpu.get("mixar_reserve_gb") or 6.0),
        timeout_s=float(comfyq.get("timeout_s") or 30.0),
    )
