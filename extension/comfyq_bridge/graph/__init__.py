# SPDX-License-Identifier: GPL-3.0-or-later
"""ComfyQ node graph (Moodboard-style): see docs/plans/node-graph.md."""
from __future__ import annotations

from . import tree, ui

classes = (*tree.SOCKET_CLASSES.values(), tree.ComfyQGraphTree, tree.ComfyQResultRecord, *tree.NODE_CLASSES,
           *ui.classes)
register_extras = ui.register_extras
unregister_extras = ui.unregister_extras
sync_all = ui.sync_all
