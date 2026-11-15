# SPDX-License-Identifier: GPL-3.0-or-later
"""ComfyQ node graph (Moodboard-style): see docs/plans/node-graph.md."""
from __future__ import annotations

from . import picture_mask_ui, preset_ops, tree, ui

classes = (*tree.SOCKET_CLASSES.values(), tree.ComfyQGraphTree, tree.ComfyQResultRecord, *tree.NODE_CLASSES,
           *ui.classes, *preset_ops.classes)


def register_extras():
    ui.register_extras()
    picture_mask_ui.register()


def unregister_extras():
    picture_mask_ui.unregister()
    ui.unregister_extras()


sync_all = ui.sync_all
