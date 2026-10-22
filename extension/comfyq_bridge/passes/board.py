# SPDX-License-Identifier: GPL-3.0-or-later
"""Material board: an approved picture cut into tiles, the most detailed tiles kept and shuffled, so a reference made
from it carries materials and no layout.

Why (docs/multi-shot.md, 2026-10-22): given a whole hero picture as a reference, Qwen 2.1 / Flux2 Klein copy its
composition and ignore the shot's own camera; given swatches, Qwen 2.1 keeps the shot's composition and takes the
materials. `board_array` is plain numpy; `make_board` reads and writes the files through Blender.
"""
from __future__ import annotations

from pathlib import Path

import bpy
import numpy as np

from .render import _out_path


def board_array(px: np.ndarray, cols: int = 4, rows: int = 3, keep: int = 8, seed: int = 7) -> np.ndarray:
    """`px` (h, w, channels) → a board of the `keep` most detailed square tiles of a cols × rows grid, shuffled
    (fixed seed: the same picture always gives the same board)."""
    h, w = px.shape[:2]
    ch, cw = h // rows, w // cols
    s = min(ch, cw)
    tiles = []
    for r in range(rows):
        for c in range(cols):
            y0, x0 = r * ch + (ch - s) // 2, c * cw + (cw - s) // 2
            tiles.append(px[y0:y0 + s, x0:x0 + s])

    def detail(t):                                          # mean gradient of the luminance: flat sky scores low
        lum = t[..., :3].mean(axis=2)
        return float(np.abs(np.diff(lum, axis=0)).mean() + np.abs(np.diff(lum, axis=1)).mean())

    keep = max(1, min(keep, len(tiles)))
    best = sorted(range(len(tiles)), key=lambda i: -detail(tiles[i]))[:keep]
    order = np.random.default_rng(seed).permutation(best)
    out_cols = min(keep, 4)
    out_rows = -(-keep // out_cols)
    board = np.zeros((out_rows * s, out_cols * s, px.shape[2]), dtype=px.dtype)
    if px.shape[2] == 4:
        board[..., 3] = 1
    for n, i in enumerate(order):
        r, c = divmod(n, out_cols)
        board[r * s:(r + 1) * s, c * s:(c + 1) * s] = tiles[i]
    return board


def make_board(path: Path, cols: int = 4, rows: int = 3, keep: int = 8, seed: int = 7) -> Path:
    """The board of the picture at `path`, saved as a PNG pass; returns its path."""
    src = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = src.size
        px = np.empty(w * h * 4, dtype=np.float32)
        src.pixels.foreach_get(px)
    finally:
        bpy.data.images.remove(src)
    board = board_array(px.reshape(h, w, 4), cols, rows, keep, seed)
    bh, bw = board.shape[:2]
    out = _out_path("board")
    img = bpy.data.images.new("ComfyQ board pass", bw, bh, alpha=False)
    try:
        img.pixels.foreach_set(board.ravel())
        img.filepath_raw, img.file_format = str(out), "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return out
