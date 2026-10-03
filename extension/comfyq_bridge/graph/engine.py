# SPDX-License-Identifier: GPL-3.0-or-later
"""Which workflow nodes a graph run must book, in which order. No bpy here (unit-tested).

The graph is given as plain data (`GNode`). A workflow node runs when it is a target, when it has
no usable result, when its inputs or settings changed since that result was made (its *hash*), or
when something it depends on runs again in the same run.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


class CycleError(ValueError):
    pass


@dataclass
class GNode:
    name: str
    workflow: bool                          # True = a ComfyQ job; False = a source (render, image, prompt…)
    own: str = ""                           # hash of the node's own settings (and, for scene sources, the scene)
    inputs: dict[str, str] = field(default_factory=dict)    # input key -> name of the node linked to it
    has_result: bool = False                # a downloaded result exists on disk
    result_hash: str = ""                   # the hash the result was made with


def node_hash(nodes: dict[str, GNode], name: str, _memo: dict[str, str] | None = None,
              _path: tuple[str, ...] = ()) -> str:
    """Hash of a node's own settings and of everything upstream of it."""
    memo = {} if _memo is None else _memo
    if name in memo:
        return memo[name]
    if name in _path:
        raise CycleError(" → ".join([*_path, name]))
    n = nodes[name]
    ups = {key: node_hash(nodes, src, memo, (*_path, name)) for key, src in sorted(n.inputs.items()) if src in nodes}
    h = hashlib.sha1(json.dumps([n.own, ups], sort_keys=True).encode()).hexdigest()[:16]
    memo[name] = h
    return h


def order(nodes: dict[str, GNode], targets: list[str]) -> list[str]:
    """Workflow nodes needed by `targets` (included), dependencies first."""
    seen: list[str] = []

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            raise CycleError(" → ".join([*path, name]))
        if name in seen or name not in nodes:
            return
        for src in nodes[name].inputs.values():
            visit(src, (*path, name))
        if nodes[name].workflow:
            seen.append(name)

    for t in targets:
        visit(t, ())
    return seen


def plan(nodes: dict[str, GNode], targets: list[str], force: bool = False,
         reuse_targets: bool = False) -> list[str]:
    """The workflow nodes to book, dependencies first. Up-to-date upstream results are reused; the
    targets always run unless `reuse_targets` (an output node's Generate: up to date = just act)."""
    memo: dict[str, str] = {}
    runs: list[str] = []
    for name in order(nodes, targets):
        n = nodes[name]
        upstream_runs = any(_depends_on(nodes, name, r) for r in runs)
        stale = not n.has_result or n.result_hash != node_hash(nodes, name, memo)
        if force or (name in targets and not reuse_targets) or stale or upstream_runs:
            runs.append(name)
    return runs


def _depends_on(nodes: dict[str, GNode], name: str, other: str, _seen: set[str] | None = None) -> bool:
    seen = set() if _seen is None else _seen
    for src in nodes[name].inputs.values():
        if src == other:
            return True
        if src in nodes and src not in seen:
            seen.add(src)
            if _depends_on(nodes, src, other, seen):
                return True
    return False


def ready(nodes: dict[str, GNode], name: str, pending: set[str]) -> bool:
    """A node can be booked once no workflow node it depends on is still pending in this run."""
    return not any(_depends_on(nodes, name, p) for p in pending if p != name)


def leaves(nodes: dict[str, GNode]) -> list[str]:
    """Workflow nodes whose result feeds no other workflow node (what "Run graph" runs)."""
    fed = {src for n in nodes.values() if n.workflow for src in n.inputs.values()}
    return [name for name, n in nodes.items() if n.workflow and name not in fed]


def feeding_workflows(nodes: dict[str, GNode], name: str) -> list[str]:
    """The nearest workflow nodes upstream of `name` (what an output node's Generate runs)."""
    found: list[str] = []
    seen: set[str] = set()

    def visit(n: str) -> None:
        for src in nodes[n].inputs.values():
            if src in seen or src not in nodes:
                continue
            seen.add(src)
            if nodes[src].workflow:
                found.append(src)
            else:
                visit(src)
    if name in nodes:
        visit(name)
    return found


def settings_hash(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]
