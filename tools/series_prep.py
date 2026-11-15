# SPDX-License-Identifier: GPL-3.0-or-later
"""File inputs for the preset test series (tools/preset_series.py), from material on this machine. Run with ComfyUI's
python (av, cv2):

    <ComfyUI python_embeded>/python.exe -I tools/series_prep.py <assets folder>

- dance.mp4      the free stock clip of a woman dancing (ComfyUI input 3044160, 3840×2160 24 fps): 5 s, 1280×720
- dance_man.mp4  the rooftop dancer (ComfyUI input 2795750, the example clip of MiniMax's video reference
                 workflow): as is, 720×720 with its sound
- dance_frame0.png  the first frame of dance.mp4 (pose / structure pictures)
- tunnel_loop.mp4   ComfyQ's example looping depth tunnel (loop restyle)
- ai_clip.mp4       an earlier AI clip of the cliff house (LTX 2.5 canny, 2026-10-22) for the video upscalers
- truck_photo.jpg   capture photo 000041 of the truck splat (a real, slightly soft photo: unblur)
- truck_flux3.png   the FLUX.3 picture of the truck splat (2026-11-15): picture upscalers, background repaint
- museum_views/     4 clay views of the museum blockout (Batch, Four views)
- speech.wav        made separately with Windows text-to-speech (see preset_series.py)
"""
import shutil
import sys
from pathlib import Path

import av
import cv2

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
IN = Path(r"F:\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\ComfyUI\input")
REPO = Path(__file__).resolve().parents[1]


def trim(src: Path, dst: Path, seconds: float, width: int, height: int, fps: int) -> None:
    """Re-encode the first `seconds` of `src`, centre-cropped to width:height and scaled (no sound)."""
    inp = av.open(str(src))
    vs = inp.streams.video[0]
    out = av.open(str(dst), "w")
    st = out.add_stream("libx264", rate=fps)
    st.width, st.height, st.pix_fmt = width, height, "yuv420p"
    st.options = {"crf": "16", "preset": "slow"}
    n_max = int(seconds * fps)
    src_fps = float(vs.average_rate)
    n = 0
    for i, fr in enumerate(inp.decode(video=0)):
        if i * fps / src_fps < n:                      # drop frames to reach the target rate
            continue
        img = fr.to_ndarray(format="bgr24")
        h, w = img.shape[:2]
        tw = int(h * width / height)
        if tw <= w:
            x = (w - tw) // 2
            img = img[:, x:x + tw]
        img = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
        if n == 0:
            cv2.imwrite(str(OUT / "dance_frame0.png"), img)
        for p in st.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
            out.mux(p)
        n += 1
        if n >= n_max:
            break
    for p in st.encode():
        out.mux(p)
    out.close()
    inp.close()
    print("made", dst.name, n, "frames")


trim(IN / "3044160-uhd_3840_2160_24fps.mp4", OUT / "dance.mp4", 5.0, 1280, 720, 24)
for src, name in ((IN / "2795750-uhd_3840_2160_25fps_1_1.mp4", "dance_man.mp4"),
                  (IN / "comfyq_session__0_0__tunnel_depth_loop.mp4", "tunnel_loop.mp4"),
                  (REPO / "live_check/cliff_house/e2e/video_e2e.mp4", "ai_clip.mp4"),
                  (Path(r"E:\zaki\truck\images\000041.jpg"), "truck_photo.jpg"),
                  (REPO / "live_check/truck_flux3_result.png", "truck_flux3.png")):
    if src.is_file():
        shutil.copyfile(src, OUT / name)
        print("copied", name)
    else:
        print("MISSING", src)
views = OUT / "museum_views"
views.mkdir(exist_ok=True)
for v in ("front", "left", "back", "right"):
    shutil.copyfile(REPO / f"live_check/never_run/museum/clay/{v}.png", views / f"{v}.png")
print("copied museum views")
