from wording import middle


def test_long_names_are_cut_in_the_middle_so_their_ends_still_differ():
    # audit 2, #24: two FLUX.3 Klein variants were cut at the end to the same "FLUX.3 Klein distil…"
    a = middle("FLUX.3 Klein distilled — fast text to image", 24)
    b = middle("FLUX.3 Klein distilled — fast picture edit", 24)
    assert len(a) == 24 and len(b) == 24 and "…" in a and a != b
    assert a.startswith("FLUX.3 Kle") and b.endswith("edit")
    assert middle("Short name", 24) == "Short name"
