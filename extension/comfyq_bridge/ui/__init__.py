# SPDX-License-Identifier: GPL-3.0-or-later


def append_once(target, fn) -> None:
    """Append a header/menu draw function, first removing any copy left by an earlier load of the add-on
    (a reload that failed half-way would otherwise draw our buttons twice)."""
    funcs = getattr(target.draw, "_draw_funcs", None) or []
    for f in list(funcs):
        if getattr(f, "__name__", "") == fn.__name__ and getattr(f, "__module__", "") == fn.__module__:
            target.remove(f)
    target.append(fn)
