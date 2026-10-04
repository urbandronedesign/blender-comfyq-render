# SPDX-License-Identifier: GPL-3.0-or-later
"""Contact sheets for reviewing preset results: images are shrunk, videos become start / middle / end
frames side by side (+ frame count, fps, audio yes/no). Needs OpenCV + PyAV (ComfyUI's embedded Python
has both):

    <ComfyUI>/python_embeded/python.exe tools/contact_sheet.py [live_check/presets/results.json]
Writes live_check/presets/sheets/<n>_<workflow>.jpg and sheets/index.json.
"""
import json
import sys
from pathlib import Path

import av
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RES = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "live_check" / "presets" / "results.json"
OUT = RES.parent / "sheets"
H = 300


def fit(img):
    h, w = img.shape[:2]
    return cv2.resize(img, (max(1, int(w * H / h)), H))


def video(path: Path):
    cap = cv2.VideoCapture(str(path))
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    frames = []
    for i in (0, n // 2, max(0, n - 1)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if ok:
            frames.append(fit(f))
    cap.release()
    try:
        with av.open(str(path)) as c:
            audio = any(s.type == "audio" for s in c.streams)
    except Exception:  # noqa: BLE001
        audio = None
    return frames, {"frames": n, "fps": round(fps, 2), "seconds": round(n / fps, 2) if fps else None,
                    "audio": audio}


OUT.mkdir(parents=True, exist_ok=True)
index = []
for i, r in enumerate(json.loads(RES.read_text(encoding="utf-8"))):
    p = Path(r.get("result") or "")
    if not p.is_file():
        continue
    info = {"key": r["key"], "file": str(p)}
    if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
        frames = [fit(img)] if img is not None else []
        if img is not None:
            info.update(size=[img.shape[1], img.shape[0]], mean=round(float(img.mean()) / 255, 3))
    elif p.suffix.lower() in (".mp4", ".webm", ".mov", ".gif"):
        frames, meta = video(p)
        info.update(meta)
    else:
        continue
    if frames:
        sheet = np.hstack(frames)
        name = f"{i:02d}_{(r.get('workflow_id') or 'x')[:40]}.jpg"
        cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tofile(str(OUT / name))
        info["sheet"] = name
    index.append(info)
(OUT / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"{len(index)} sheets in {OUT}")
