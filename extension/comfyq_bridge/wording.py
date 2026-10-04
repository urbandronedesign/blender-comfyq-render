# SPDX-License-Identifier: GPL-3.0-or-later
"""Small wording helpers for UI text (no bpy): Blender's HIG avoids "job(s)" and abbreviations."""
from __future__ import annotations


def count(n: int, one: str, many: str | None = None) -> str:
    """count(1, "job") -> '1 job'; count(3, "job") -> '3 jobs'; count(2, "strip", "strips")."""
    return f"{n} {one if n == 1 else (many or one + 's')}"
