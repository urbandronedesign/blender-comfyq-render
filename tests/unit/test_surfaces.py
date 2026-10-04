from surfaces import Region, label_name, labels_from, surface_prompt, where


def test_where_reads_the_box_in_thirds():
    assert where((0.0, 0.7, 1.0, 1.0)) == "bottom"              # spans the width: only the row
    assert where((0.0, 0.0, 0.3, 0.3)) == "top left"
    assert where((0.4, 0.4, 0.6, 0.6)) == "centre"
    assert where((0.0, 0.0, 1.0, 1.0)) == "across the picture"
    assert where((0.7, 0.1, 0.95, 0.9)) == "right"               # spans the height: only the column


def test_prompt_names_found_surfaces_biggest_first_with_their_material():
    regions = [Region("window", 0.08, (0.3, 0.2, 0.5, 0.5)), Region("floor", 0.25, (0.0, 0.6, 1.0, 1.0)),
               Region("stairs", 0.0, (0, 0, 0, 0)), Region("rug", 0.05, (0.3, 0.7, 0.8, 0.9))]
    p = surface_prompt(regions, {"floor": "light oak parquet.", "Window": "black steel glazing"},
                       "Photorealistic interior.", "Keep the layout.")
    assert p == ("Photorealistic interior. The floor is light oak parquet. The window is black steel glazing. "
                 "The rug (bottom). Keep the layout.")
    assert "stairs" not in p                                     # not found: not named


def test_plural_labels_take_are():
    assert surface_prompt([Region("stairs", 0.1, (0.6, 0.2, 0.9, 0.9))], {"stairs": "polished concrete"}) == \
        "The stairs are polished concrete."


def test_labels_from_text_or_list():
    assert labels_from("Floor, wall; floor ,window") == ["floor", "wall", "window"]
    assert labels_from(["sofa", " Rug "]) == ["sofa", "rug"]
    assert [label_name(x) for x in labels_from(None)[:3]] == ["floor", "wall", "ceiling"]
    assert labels_from(None)[1] == "wall|left wall|right wall"         # SAM3: one surface per phrase
