# SPDX-License-Identifier: GPL-3.0-or-later
"""Move saved class presets from Flux2 Klein 9B to FLUX.3 Klein distilled (2026-11-15: same render, prompt and seed:
FLUX.3 distilled kept the layout best and ran in 18 s vs Flux2 42 s and FLUX.3 full 83 s; live_check/flux3_compare).

Plain Python (no Blender): rewrites the preset JSON files in place (renamed when the name says Flux2) —
    python tools/presets_to_flux3.py presets/class/*.json

- edit:  image_edit_flux2_klein_9b_image_edit → image_edit_flux3_klein_distilled_image_edit
- text:  image_flux2_klein_9b_t2i            → image_flux3_klein_distilled_t2i
Links are moved to FLUX.3's inputs (picture → image, prompt → prompt; the negative prompt has no FLUX.3 input and
is dropped), the seed setting is kept, other Flux2 settings (steps, size…) fall back to FLUX.3's defaults. Edit
prompts get FLUX.3's way of naming the picture (<ref_image_0>). Labels, notes and descriptions say FLUX.3 Klein.
"""
import json
import re
import sys
from pathlib import Path

MAP = {
    "image_edit_flux2_klein_9b_image_edit": ("image_edit_flux3_klein_distilled_image_edit",
                                             {"loadimage_image_76": "image", "cliptextencode_text_75_74": "prompt",
                                              "randomnoise_noise_seed_75_73": "seed"}),
    "image_flux2_klein_9b_t2i": ("image_flux3_klein_distilled_t2i",
                                 {"primitivestringmultiline_value_76": "prompt",
                                  "randomnoise_noise_seed_75_73": "seed"}),
}
EDIT_IDS = {"image_edit_flux3_klein_distilled_image_edit"}


def flux3_name(text: str) -> str:
    return re.sub(r"Flux2 Klein(?: 9B)?", "FLUX.3 Klein", text)


def edit_prompt(text: str) -> str:
    """FLUX.3 edit prompts name the picture <ref_image_0> (its own guidance)."""
    if "<ref_image_0>" in text or not text:
        return text
    named, n = re.subn(r"\bthis (picture|image|photo|3D render|render)\b", r"the \1 <ref_image_0>", text, count=1)
    return named if n else f"In <ref_image_0>, {text[0].lower()}{text[1:]}"


def convert(path: Path) -> Path | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["name"]: n for n in data["nodes"]}
    changed = False
    edit_nodes = set()
    for n in data["nodes"]:
        if n.get("workflow_id") in MAP:
            new_id, keys = MAP[n["workflow_id"]]
            n["workflow_id"] = new_id
            n["params"] = [{**p, "key": keys[p["key"]]} for p in n.get("params", []) if p["key"] in keys]
            if new_id in EDIT_IDS:
                edit_nodes.add(n["name"])
                for p in n["params"]:
                    if p["key"] == "prompt" and p.get("s"):
                        p["s"] = edit_prompt(p["s"])
            changed = True
    if not changed:
        return None
    links = []
    for lk in data["links"]:
        to = nodes.get(lk["to"])
        if to is not None and to.get("workflow_id") in {v[0] for v in MAP.values()}:
            keys = next(k for old, (new, k) in MAP.items() if new == to["workflow_id"])
            if lk["to_socket"] not in keys:
                continue                                        # the negative prompt: no FLUX.3 input
            lk = {**lk, "to_socket": keys[lk["to_socket"]]}
            if lk["to"] in edit_nodes and lk["to_socket"] == "prompt":
                src = nodes.get(lk["from"])
                if src is not None and src.get("type") == "ComfyQPromptNode" and src.get("text"):
                    src["text"] = edit_prompt(src["text"])
        links.append(lk)
    data["links"] = links
    data["label"] = flux3_name(data["label"])
    data["description"] = flux3_name(data.get("description", ""))
    for n in data["nodes"]:
        for k in ("label", "text"):
            if isinstance(n.get(k), str) and n.get("type") != "ComfyQPromptNode":
                n[k] = flux3_name(n[k])
    out = path.with_name(flux3_name(path.stem) + path.suffix)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    if out != path:
        path.unlink()
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        for p in sorted(Path().glob(arg)) if any(c in arg for c in "*?") else [Path(arg)]:
            got = convert(p)
            print(f"{p.name} → {got.name}" if got else f"{p.name}: no Flux2 Klein node")
