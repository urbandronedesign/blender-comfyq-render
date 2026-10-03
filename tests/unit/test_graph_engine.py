import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "extension" / "comfyq_bridge" / "graph" / "engine.py"
_spec = importlib.util.spec_from_file_location("comfyq_graph_engine", _PATH)
engine = importlib.util.module_from_spec(_spec)
sys.modules["comfyq_graph_engine"] = engine      # dataclasses look the module up while building classes
_spec.loader.exec_module(engine)
GNode, plan, order, node_hash, leaves, ready = (engine.GNode, engine.plan, engine.order, engine.node_hash,
                                                engine.leaves, engine.ready)


def graph(**results):
    """render → keyframe (image wf) → v2v (video wf) ← anim ; prompt → v2v ; v2v → upscale."""
    g = {
        "render": GNode("render", False, own="clay@1"),
        "anim": GNode("anim", False, own="clay anim"),
        "prompt": GNode("prompt", False, own="a red car"),
        "keyframe": GNode("keyframe", True, own="qwen", inputs={"image": "render"}),
        "v2v": GNode("v2v", True, own="ltx", inputs={"video": "anim", "start": "keyframe", "text": "prompt"}),
        "upscale": GNode("upscale", True, own="seedvr", inputs={"video": "v2v"}),
    }
    for name, done in results.items():
        g[name].has_result = done
        g[name].result_hash = node_hash(g, name) if done else ""
    return g


def test_order_puts_dependencies_first():
    assert order(graph(), ["upscale"]) == ["keyframe", "v2v", "upscale"]


def test_first_run_books_the_whole_chain():
    assert plan(graph(), ["v2v"]) == ["keyframe", "v2v"]


def test_up_to_date_upstream_is_reused():
    g = graph(keyframe=True)
    assert plan(g, ["v2v"]) == ["v2v"]           # the target always runs; the keyframe is reused


def test_changed_upstream_settings_rerun_it():
    g = graph(keyframe=True)
    g["render"].own = "clay@60"                    # e.g. the scene was edited / another frame
    assert plan(g, ["v2v"]) == ["keyframe", "v2v"]


def test_downstream_of_a_rerun_node_reruns():
    g = graph(keyframe=True, v2v=True)
    g["keyframe"].has_result = False               # result file gone
    assert plan(g, ["upscale"]) == ["keyframe", "v2v", "upscale"]


def test_force_reruns_everything():
    assert plan(graph(keyframe=True, v2v=True), ["upscale"], force=True) == ["keyframe", "v2v", "upscale"]


def test_prompt_change_dirties_only_its_consumers():
    g = graph(keyframe=True, v2v=True)
    g["prompt"].own = "a blue car"
    assert plan(g, ["upscale"]) == ["v2v", "upscale"]


def test_ready_waits_for_dependencies():
    g = graph()
    assert ready(g, "keyframe", {"keyframe", "v2v"})
    assert not ready(g, "v2v", {"keyframe", "v2v"})
    assert ready(g, "v2v", {"v2v"})


def test_fan_out_books_branches_together():
    g = graph()
    g["v2v_b"] = GNode("v2v_b", True, own="minimax", inputs={"video": "anim", "start": "keyframe"})
    runs = plan(g, leaves(g))
    assert runs[0] == "keyframe" and set(runs[1:]) == {"v2v_b", "upscale", "v2v"}
    pending = set(runs) - {"keyframe"}
    assert ready(g, "v2v", pending) and ready(g, "v2v_b", pending) and not ready(g, "upscale", pending)


def test_leaves_are_what_run_graph_runs():
    assert leaves(graph()) == ["upscale"]


def test_cycles_are_refused():
    g = graph()
    g["keyframe"].inputs["loop"] = "upscale"
    with pytest.raises(engine.CycleError):
        plan(g, ["upscale"])


# -- N4: output nodes (Viewer / Import / Save) and reroutes -------------------------------------------
def with_outputs(**results):
    g = graph(**results)
    g["viewer"] = GNode("viewer", False, own="viewer", inputs={"result": "upscale"})
    g["save"] = GNode("save", False, own="save", inputs={"result": "render"})       # a source's render
    return g


def test_output_nodes_never_book_and_are_not_leaves():
    g = with_outputs()
    assert leaves(g) == ["upscale"]
    assert "viewer" not in plan(g, ["viewer"])  # an output node is not a job (what feeds it is)


def test_output_generate_runs_the_workflow_feeding_it():
    g = with_outputs()
    assert engine.feeding_workflows(g, "viewer") == ["upscale"]
    assert engine.feeding_workflows(g, "save") == []              # only a source: act right away
    assert plan(g, ["upscale"], reuse_targets=True) == ["keyframe", "v2v", "upscale"]


def test_output_generate_reuses_an_up_to_date_result():
    g = with_outputs(keyframe=True, v2v=True, upscale=True)
    assert plan(g, ["upscale"], reuse_targets=True) == []         # just show / import / save
    assert plan(g, ["upscale"]) == ["upscale"]                    # the node's own Generate re-runs it
    g["anim"].own = "edited"
    assert plan(g, ["upscale"], reuse_targets=True) == ["v2v", "upscale"]
