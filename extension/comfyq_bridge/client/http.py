# SPDX-License-Identifier: Apache-2.0
"""Thin HTTP layer over `requests` (bundled with Mixar and Blender).

Adds the access token, timeouts and readable errors. Never logs the token.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


def _unreachable(base: str, e: Exception) -> str:
    """Plain words for a network failure, with what to do next."""
    if isinstance(e, requests.ReadTimeout):        # connected, but no answer in time
        what = "is too slow to answer (it may be loading a model)"
    else:
        what = "is not answering"
    return (f"The ComfyQ server at {base} {what}. Check that the server PC is on and ComfyQ is running, "
            f"then press Connect again (address: Preferences › Add-ons › ComfyQ Bridge).")


class ComfyQError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, access_required: bool = False):
        super().__init__(message)
        self.status = status
        self.access_required = access_required


class Http:
    def __init__(self, base_url: str, token: str = "", timeout_s: float = 30.0, admin_password: str = ""):
        self.base = base_url.rstrip("/")
        self.token = token
        self.admin_password = admin_password
        self.timeout = timeout_s
        self.session = requests.Session()

    def _headers(self, admin: bool = False) -> dict[str, str]:
        h = {"x-access-token": self.token} if self.token else {}
        if admin and self.admin_password:
            h["x-admin-password"] = self.admin_password
        return h

    def _check(self, r: requests.Response) -> Any:
        if r.ok:
            return r.json() if r.content else None
        try:
            body = r.json()
        except ValueError:
            body = {"error": r.text[:300]}
        raise ComfyQError(
            body.get("error") or f"HTTP {r.status_code}",
            status=r.status_code,
            access_required=bool(body.get("accessRequired")),
        )

    def get(self, path: str, **params: Any) -> Any:
        try:
            r = self.session.get(self.base + path, params=params or None,
                                 headers=self._headers(), timeout=self.timeout)
        except requests.RequestException as e:
            raise ComfyQError(_unreachable(self.base, e)) from e
        return self._check(r)

    def post_json(self, path: str, body: dict[str, Any], admin: bool = False, timeout: float | None = None) -> Any:
        try:
            r = self.session.post(self.base + path, json=body, headers=self._headers(admin),
                                  timeout=timeout or self.timeout)
        except requests.RequestException as e:
            raise ComfyQError(_unreachable(self.base, e)) from e
        return self._check(r)

    def delete(self, path: str, admin: bool = False, timeout: float | None = None) -> Any:
        try:
            r = self.session.delete(self.base + path, headers=self._headers(admin), timeout=timeout or self.timeout)
        except requests.RequestException as e:
            raise ComfyQError(_unreachable(self.base, e)) from e
        return self._check(r)

    def upload(self, path: str, file_path: Path, field: str = "file") -> Any:
        with open(file_path, "rb") as f:
            try:
                r = self.session.post(self.base + path, files={field: (file_path.name, f)},
                                      headers=self._headers(), timeout=max(self.timeout, 300))
            except requests.RequestException as e:
                raise ComfyQError(f"Upload to {self.base} failed: {e.__class__.__name__}") from e
        return self._check(r)

    def download(self, media_path: str, dest: Path, chunk: int = 1 << 20) -> Path:
        """Stream `/media/_any/<media_path>` to `dest`, atomically."""
        url = f"{self.base}/media/_any/{quote(media_path)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            with self.session.get(url, headers=self._headers(), stream=True, timeout=self.timeout) as r:
                if not r.ok:
                    raise ComfyQError(f"Download failed ({r.status_code}): {media_path}", status=r.status_code)
                with open(tmp, "wb") as f:
                    f.writelines(r.iter_content(chunk))
        except requests.RequestException as e:
            raise ComfyQError(f"Download failed: {e.__class__.__name__}") from e
        os.replace(tmp, dest)
        return dest
