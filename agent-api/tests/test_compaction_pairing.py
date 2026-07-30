"""Compaction must never orphan a tool_use / tool_result pair.

Bedrock Converse and Anthropic both reject a request where an assistant turn
carries a ``toolUse`` that the next user turn does not answer with a matching
``toolResult`` (and vice versa). ``compact_input_state`` is the "microcompact"
tier inside ``ContextCompactionManager.compact_if_needed``, so it runs from the
pre-model hook of any run long enough to need compacting — a violation there
fails the model call on exactly the long investigations compaction exists to
keep alive.

Regression: the middle-pruning step used to drop ToolMessages while leaving the
AIMessage.tool_calls that requested them, producing 3 unanswered toolUse blocks
on a 5-round history.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.harness.helpers import compact_input_state


def _pairing(messages):
    """(unanswered tool_use ids, unrequested tool_result ids)."""
    requested, answered = set(), set()
    for m in messages:
        for tc in (getattr(m, "tool_calls", None) or []):
            if isinstance(tc, dict) and tc.get("id"):
                requested.add(tc["id"])
        content = getattr(m, "content", None)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    requested.add(b["id"])
        tcid = getattr(m, "tool_call_id", None)
        if tcid:
            answered.add(tcid)
    return requested - answered, answered - requested


def _history(rounds=5, blob=3000, list_content=False, parallel=False):
    msgs = [HumanMessage(content="Investigate the production error spike.")]
    if parallel:
        msgs.append(AIMessage(content="fan out", tool_calls=[
            {"id": "p0", "name": "probe", "args": {}},
            {"id": "p1", "name": "probe", "args": {}},
        ]))
        msgs.append(ToolMessage(content="A" * blob, tool_call_id="p0"))
        msgs.append(ToolMessage(content="B" * blob, tool_call_id="p1"))
    for i in range(rounds):
        tid = f"t{i}"
        call = {"id": tid, "name": "probe", "args": {"q": str(i)}}
        content = (
            [{"type": "text", "text": f"step {i}"},
             {"type": "tool_use", "id": tid, "name": "probe", "input": {}}]
            if list_content else f"step {i}"
        )
        msgs.append(AIMessage(content=content, tool_calls=[call]))
        msgs.append(ToolMessage(content="D" * blob, tool_call_id=tid))
    msgs.append(AIMessage(content="Draft conclusion."))
    return msgs


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"list_content": True},
        {"parallel": True},
        {"rounds": 12},
        {"rounds": 3, "blob": 50},
    ],
    ids=["string-content", "list-content", "parallel-calls", "long", "short"],
)
def test_compact_input_state_preserves_pairing(kwargs):
    msgs = _history(**kwargs)
    assert _pairing(msgs) == (set(), set()), "fixture itself must be well-paired"

    out = compact_input_state({"messages": msgs})["messages"]
    unanswered, unrequested = _pairing(out)

    assert not unanswered, f"orphaned tool_use after compaction: {sorted(unanswered)}"
    assert not unrequested, f"orphaned tool_result after compaction: {sorted(unrequested)}"


def test_compact_input_state_keeps_calls_answered_by_the_preserved_tail():
    """A request whose ToolMessage survives in the tail must NOT be stripped —
    doing so orphans the tool_result, which the provider rejects just as hard."""
    msgs = _history(rounds=5)
    out = compact_input_state({"messages": msgs})["messages"]

    answered = {getattr(m, "tool_call_id", None) for m in out}
    kept_calls = {
        tc["id"] for m in out for tc in (getattr(m, "tool_calls", None) or [])
        if isinstance(tc, dict) and tc.get("id")
    }
    assert kept_calls, "compaction stripped every tool call, including answered ones"
    assert kept_calls <= answered


def test_compact_input_state_actually_prunes():
    """The repair must not defeat the point: bulk still has to come out."""
    msgs = _history(rounds=6, blob=4000)
    before = sum(len(m.content) for m in msgs if isinstance(m.content, str))
    out = compact_input_state({"messages": msgs})["messages"]
    after = sum(len(m.content) for m in out if isinstance(m.content, str))
    assert after < before / 2, f"expected real pruning, went {before} -> {after}"


def test_compact_input_state_is_noop_when_short():
    msgs = _history(rounds=2)[:5]
    state = {"messages": msgs}
    assert compact_input_state(state) is state


def test_compacted_history_is_valid_bedrock_wire_format():
    """End-to-end guard through langchain_aws's own converter — the format the
    provider actually validates."""
    conv = pytest.importorskip(
        "langchain_aws.chat_models.bedrock_converse",
        reason="langchain_aws not installed",
    )
    out = compact_input_state({"messages": _history(rounds=5)})["messages"]
    turns, _system = conv._messages_to_bedrock(out)

    unanswered = []
    for i, turn in enumerate(turns):
        uses = [b["toolUse"]["toolUseId"] for b in turn["content"] if "toolUse" in b]
        if not uses:
            continue
        nxt = turns[i + 1]["content"] if i + 1 < len(turns) else []
        results = {b["toolResult"]["toolUseId"] for b in nxt if "toolResult" in b}
        unanswered.extend(u for u in uses if u not in results)

    assert not unanswered, (
        f"Bedrock would reject this request: unanswered toolUse {unanswered}"
    )
