# SPDX-License-Identifier: GPL-3.0-or-later
"""Batch renders from a folder (no bpy): which files a Media Folder node feeds, where each result goes, and
whether a file is already done (so a stopped batch resumes where it left off).

The results go to a new folder next to the input one, with the same subfolders:
    photos/north/facade_01.jpg  →  photos_renders/north/facade_01_render.png
"""
from __future__ import annotations

from pathlib import Path

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".exr", ".tga"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif"})
EXTS = {"image": IMAGE_EXTS, "video": VIDEO_EXTS}
OUT_SUFFIX = "_renders"


def out_root(root: Path) -> Path:
    """The folder the results go to: next to the input folder, named after it."""
    root = Path(root)
    return root.parent / f"{root.name}{OUT_SUFFIX}"


def scan(root: Path, recursive: bool = True) -> dict[str, list[Path]]:
    """{"image": [...], "video": [...]} under `root`, sorted by relative path. Hidden files and folders are
    skipped, and so are earlier results: a folder `X_renders` next to a folder `X` (batching the parent of a
    folder already rendered must not render the renders)."""
    root = Path(root)
    found: dict[str, list[Path]] = {"image": [], "video": []}
    if not root.is_dir():
        return found
    pattern = "**/*" if recursive else "*"
    for p in root.glob(pattern):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts) or not p.is_file():
            continue
        if _in_results(root, rel):
            continue
        ext = p.suffix.lower()
        for kind, exts in EXTS.items():
            if ext in exts:
                found[kind].append(p)
    for files in found.values():
        files.sort(key=lambda p: [s.lower() for s in p.relative_to(root).parts])
    return found


def _in_results(root: Path, rel: Path) -> bool:
    here = root
    for part in rel.parts[:-1]:
        if part.endswith(OUT_SUFFIX) and (here / part[: -len(OUT_SUFFIX)]).is_dir():
            return True
        here = here / part
    return False


def result_stem(root: Path, file: Path, suffix: str) -> Path:
    """Where the result of `file` goes, without its extension (the result decides it: .png, .mp4…)."""
    rel = Path(file).relative_to(root)
    return out_root(root) / rel.parent / f"{rel.stem}{suffix}"


def done(root: Path, file: Path, suffix: str) -> Path | None:
    """The result already saved for `file` (any extension), or None."""
    stem = result_stem(root, file, suffix)
    if not stem.parent.is_dir():
        return None
    return next((p for p in stem.parent.glob(f"{glob_escape(stem.name)}.*") if p.is_file()), None)


def glob_escape(name: str) -> str:
    """A file name as a literal glob pattern ([ ] * ? are glob syntax)."""
    return "".join(f"[{c}]" if c in "[]*?" else c for c in name)


def remaining(files: list[Path], root: Path, suffixes: list[str]) -> list[Path]:
    """The files that still need a run: those missing a result for at least one Save suffix."""
    return [f for f in files if any(done(root, f, s) is None for s in suffixes)]


def eta(durations: list[float], left: int) -> str:
    """'about 9 min left' from the files done so far ('' before the first one)."""
    if not durations or left <= 0:
        return ""
    secs = sum(durations[-5:]) / len(durations[-5:]) * left
    return f"about {secs / 60:.0f} min left" if secs >= 90 else f"about {secs:.0f} s left"
