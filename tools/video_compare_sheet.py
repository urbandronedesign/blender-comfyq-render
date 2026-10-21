# SPDX-License-Identifier: GPL-3.0-or-later
"""One comparison sheet: a row per video (label + frames at 0, 1/3, 2/3 and the end), same height. Plus each video's
frame count, fps, duration, size and whether it has sound. Needs OpenCV + PyAV (ComfyUI's embedded Python):

    <ComfyUI>/python_embeded/python.exe tools/video_compare_sheet.py <out.jpg> <label=video.mp4> [label=video.mp4 ...]
"""
import sys
from pathlib import Path

import av
import cv2
import numpy as np

H = 260


def frames(path: Path):
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        imgs = [f.to_ndarray(format="bgr24") for f in c.decode(v)]
        fps = float(v.average_rate or 0)
    with av.open(str(path)) as c:
        sound = bool(c.streams.audio)
    return imgs, fps, sound


def row(label: str, path: Path):
    imgs, fps, sound = frames(path)
    n = len(imgs)
    picks = [imgs[min(n - 1, round(i * (n - 1) / 3))] for i in range(4)]
    h, w = picks[0].shape[:2]
    tiles = [cv2.resize(p, (int(w * H / h), H)) for p in picks]
    strip = np.hstack(tiles)
    text = f"{label}  {n} fr  {fps:.0f} fps  {n / fps if fps else 0:.1f} s  {w}x{h}  {'sound' if sound else 'silent'}"
    head = np.full((34, strip.shape[1], 3), 30, np.uint8)
    cv2.putText(head, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 1, cv2.LINE_AA)
    print(text, flush=True)
    return np.vstack([head, strip])


rows = [row(*a.split("=", 1)) for a in sys.argv[2:]]
width = max(r.shape[1] for r in rows)
rows = [np.hstack([r, np.full((r.shape[0], width - r.shape[1], 3), 30, np.uint8)]) for r in rows]
cv2.imwrite(sys.argv[1], np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
print("SHEET", sys.argv[1], flush=True)
