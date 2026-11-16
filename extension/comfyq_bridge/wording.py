# SPDX-License-Identifier: GPL-3.0-or-later
"""Small wording helpers for UI text (no bpy): Blender's HIG avoids "job(s)" and abbreviations."""
from __future__ import annotations


def middle(text: str, width: int) -> str:
    """`text` fitted to `width` characters by cutting its MIDDLE: the start and the end (often what differs) stay."""
    if len(text) <= width or width < 8:
        return text
    head = (width - 1) // 2
    return text[:head].rstrip() + "…" + text[-(width - 1 - head):].lstrip()


def count(n: int, one: str, many: str | None = None) -> str:
    """count(1, "job") -> '1 job'; count(3, "job") -> '3 jobs'; count(2, "strip", "strips")."""
    return f"{n} {one if n == 1 else (many or one + 's')}"
