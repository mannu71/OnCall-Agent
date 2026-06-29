"""Unit tests for node-level model gateway resolution (pure functions).

Covers the no-DB helpers in :mod:`app.workflow.llm_config` that resolve which
LLM node feeds a consumer port across edge dialects (LangflowEditor
``sourceSlot``/``targetSlot`` vs legacy ReactFlow ``sourceHandle``/``targetHandle``),
multi-model slot parsing, and per-model config readout.

The DB-touching async resolvers (``resolve_llm_config_for_consumer_port`` etc.)
are intentionally NOT exercised here — only the pure functions.
"""
from app.workflow.llm_config import (
    _edge_target_slot,
    _edge_source_slot,
    _model_key_from_slot,
    find_llm_node_for_consumer,
    read_llm_node,
)


# ─── _edge_target_slot / _edge_source_slot ────────────────────────────────────


def test_edge_target_slot_editor_key():
    assert _edge_target_slot({"targetSlot": "crawler"}) == "crawler"


def test_edge_target_slot_legacy_key():
    assert _edge_target_slot({"targetHandle": "model"}) == "model"


def test_edge_target_slot_empty():
    assert _edge_target_slot({}) == ""


def test_edge_target_slot_editor_wins_over_legacy():
    # When both dialects are present, the editor key (targetSlot) takes priority.
    assert _edge_target_slot({"targetSlot": "crawler", "targetHandle": "model"}) == "crawler"


def test_edge_source_slot_editor_key():
    assert _edge_source_slot({"sourceSlot": "lm::A"}) == "lm::A"


def test_edge_source_slot_legacy_key():
    assert _edge_source_slot({"sourceHandle": "lm::A"}) == "lm::A"


def test_edge_source_slot_empty():
    assert _edge_source_slot({}) == ""


def test_edge_source_slot_editor_wins_over_legacy():
    assert _edge_source_slot({"sourceSlot": "lm::A", "sourceHandle": "lm::B"}) == "lm::A"


# ─── _model_key_from_slot ─────────────────────────────────────────────────────


def test_model_key_from_slot_named():
    assert _model_key_from_slot("lm::Claude Sonnet 4.6") == "Claude Sonnet 4.6"


def test_model_key_from_slot_prefix_only():
    # "lm" is not the multi-model prefix ("lm::"), so no model name is extracted.
    assert _model_key_from_slot("lm") is None


def test_model_key_from_slot_empty():
    assert _model_key_from_slot("") is None


# ─── find_llm_node_for_consumer ───────────────────────────────────────────────


def _workflow(edges):
    """Build a workflow with an LM node, an agent node and a crawler node."""
    return {
        "nodes": [
            {"id": "lm1", "type": "language_model"},
            {"id": "ag1", "type": "agent"},
            {"id": "cc1", "type": "code_search_tool"},
        ],
        "edges": edges,
    }


def test_consumer_editor_dialect_resolves():
    wf = _workflow([
        {"source": "lm1", "sourceSlot": "lm::A", "target": "ag1", "targetSlot": "lm"},
    ])
    node, source_slot, connected = find_llm_node_for_consumer(
        wf, "ag1", ("model", "lm")
    )
    assert connected is True
    assert source_slot == "lm::A"
    assert node is not None and node["id"] == "lm1"


def test_consumer_legacy_dialect_resolves_same():
    wf = _workflow([
        {"source": "lm1", "sourceHandle": "lm::A", "target": "ag1", "targetHandle": "lm"},
    ])
    node, source_slot, connected = find_llm_node_for_consumer(
        wf, "ag1", ("model", "lm")
    )
    assert connected is True
    assert source_slot == "lm::A"
    assert node is not None and node["id"] == "lm1"


def test_crawler_port_tagged_edge_connects():
    wf = _workflow([
        {"source": "lm1", "sourceSlot": "lm::A", "target": "cc1", "targetSlot": "lm"},
    ])
    node, source_slot, connected = find_llm_node_for_consumer(
        wf, "cc1", ("lm",), accept_untagged=False
    )
    assert connected is True
    assert node is not None and node["id"] == "lm1"


def test_crawler_port_untagged_edge_does_not_bleed():
    # An untagged edge (no targetSlot) must NOT match the crawler port when
    # accept_untagged=False — the main-model edge can't bleed into the crawler.
    wf = _workflow([
        {"source": "lm1", "sourceSlot": "lm::A", "target": "cc1"},
    ])
    node, source_slot, connected = find_llm_node_for_consumer(
        wf, "cc1", ("lm",), accept_untagged=False, fallback=False
    )
    assert connected is False


def test_no_matching_edge_no_fallback():
    wf = _workflow([])  # no edges at all
    node, source_slot, connected = find_llm_node_for_consumer(
        wf, "ag1", ("model", "lm"), fallback=False
    )
    assert node is None
    assert source_slot == ""
    assert connected is False


# ─── read_llm_node ────────────────────────────────────────────────────────────


def test_read_llm_node_comma_multiselect_named():
    node = {"id": "lm1", "type": "language_model", "params": {"llm": "A,B"}}
    assert read_llm_node(node, "B").config_name == "B"


def test_read_llm_node_comma_multiselect_default_first():
    node = {"id": "lm1", "type": "language_model", "params": {"llm": "A,B"}}
    assert read_llm_node(node, None).config_name == "A"


def test_read_llm_node_models_list_selected():
    node = {
        "id": "lm1",
        "type": "language_model",
        "params": {"models": [{"name": "X", "temp": 0.2}, {"name": "Y"}]},
    }
    assert read_llm_node(node, "Y").config_name == "Y"


def test_read_llm_node_models_list_per_model_temp():
    node = {
        "id": "lm1",
        "type": "language_model",
        "params": {"models": [{"name": "X", "temp": 0.2}, {"name": "Y"}]},
    }
    assert read_llm_node(node, "X").temperature == 0.2
