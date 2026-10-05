import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "extension" / "comfyq_bridge" / "graph" / "batch.py"
_spec = importlib.util.spec_from_file_location("comfyq_graph_batch", _PATH)
batch = importlib.util.module_from_spec(_spec)
sys.modules["comfyq_graph_batch"] = batch
_spec.loader.exec_module(batch)


def _tree(tmp_path):
    root = tmp_path / "photos"
    for rel in ("b.jpg", "A.png", "north/facade [1].jpg", "north/clip.mp4", "south/deep/x.webp",
                "notes.txt", ".hidden/y.png", "north/.z.png"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return root


def test_scan_recursive_sorted_by_path_and_kind(tmp_path):
    root = _tree(tmp_path)
    found = batch.scan(root)
    rel = lambda ps: [p.relative_to(root).as_posix() for p in ps]
    assert rel(found["image"]) == ["A.png", "b.jpg", "north/facade [1].jpg", "south/deep/x.webp"]
    assert rel(found["video"]) == ["north/clip.mp4"]


def test_scan_top_level_only(tmp_path):
    root = _tree(tmp_path)
    assert [p.name for p in batch.scan(root, recursive=False)["image"]] == ["A.png", "b.jpg"]
    assert batch.scan(tmp_path / "missing") == {"image": [], "video": []}


def test_results_mirror_the_subfolders_next_to_the_input(tmp_path):
    root = _tree(tmp_path)
    f = root / "north" / "facade [1].jpg"
    assert batch.out_root(root) == tmp_path / "photos_renders"
    assert batch.result_stem(root, f, "_render") == tmp_path / "photos_renders" / "north" / "facade [1]_render"


def test_done_and_remaining_resume_a_batch(tmp_path):
    root = _tree(tmp_path)
    files = batch.scan(root)["image"]
    stem = batch.result_stem(root, files[2], "_render")          # brackets in the name: a literal glob
    stem.parent.mkdir(parents=True)
    (stem.parent / f"{stem.name}.png").write_bytes(b"r")
    assert batch.done(root, files[2], "_render") is not None
    assert batch.done(root, files[2], "_v2") is None
    assert files[2] not in batch.remaining(files, root, ["_render"])
    assert files[2] in batch.remaining(files, root, ["_render", "_v2"])     # a second Save still needs it


def test_batching_the_parent_skips_earlier_results(tmp_path):
    parent = tmp_path / "project"
    for rel in ("photos/a.png", "photos_renders/a_render.png", "other_renders/kept.png"):
        p = parent / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    names = sorted(p.relative_to(parent).as_posix() for p in batch.scan(parent)["image"])
    # photos_renders sits next to photos: earlier results, skipped; other_renders has no "other": a normal folder
    assert names == ["other_renders/kept.png", "photos/a.png"]


def test_eta():
    assert batch.eta([], 5) == ""
    assert batch.eta([30, 30], 4) == "about 2 min left"
    assert batch.eta([10], 3) == "about 30 s left"
