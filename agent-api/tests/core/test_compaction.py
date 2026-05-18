"""
Tests for the memory compaction module.

All tests use FakeTransport so no real LLM call is made.

Covered scenarios
-----------------
1.  No-op when under threshold.
2.  Walks backwards to keep_recent_tokens correctly (tail boundary check).
3.  Generates a summary with all 6 required sections.
4.  symbols_resolved + files_touched accumulate across two consecutive compactions.
5.  Uses FakeTransport — no real API call.
6.  Idempotency: compact(compact(x)) == compact(x) when under threshold.
7.  to_system_message_text() renders all required headers.
8.  _extract_symbols_and_files() picks up paths and symbols from tool calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.core.memory.compaction import (
    StructuredSummary,
    _estimate_tokens,
    _extract_symbols_and_files,
    compact,
)
# Phase 4 ships compaction under ``ContextCompactionManager`` to avoid
# collision with the unrelated provider-orchestrator ``MemoryManager`` in
# manager.py. Alias here so the existing assertions keep working.
from app.core.memory.compaction_manager import ContextCompactionManager as MemoryManager
from tests.conftest import FakeTransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_messages(n: int, chars_each: int = 200) -> List[BaseMessage]:
    """Return n HumanMessage + AIMessage pairs, each with chars_each characters."""
    msgs: List[BaseMessage] = []
    body = "x" * chars_each
    for i in range(n):
        msgs.append(HumanMessage(content=f"Q{i}: {body}"))
        msgs.append(AIMessage(content=f"A{i}: {body}"))
    return msgs


def _total_chars(messages: List[BaseMessage]) -> int:
    total = 0
    for m in messages:
        c = m.content
        if isinstance(c, str):
            total += len(c)
        else:
            total += sum(len(str(p)) for p in c)
    return total


# ---------------------------------------------------------------------------
# Test 1: No-op when under threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compact_noop_under_threshold(fake_transport: FakeTransport) -> None:
    """compact() must return messages unchanged when total tokens <= budget."""
    # 4 messages × 200 chars = ~200 tokens, well below window_size=50000
    messages = _make_messages(2, chars_each=200)

    compacted, summary = await compact(
        messages,
        transport=fake_transport,
        window_size=50_000,
        reserve_tokens=1_000,
        keep_recent_tokens=10_000,
    )

    assert compacted is messages, "Should return the exact same list object"
    assert len(fake_transport.calls) == 0, "Should NOT call LLM when under threshold"


# ---------------------------------------------------------------------------
# Test 2: Walks backwards to keep_recent_tokens correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compact_tail_boundary(fake_transport: FakeTransport) -> None:
    """The recent tail must contain at least keep_recent_tokens worth of messages."""
    # Build 20 messages with ~100 chars each → ~25 tokens each
    messages = _make_messages(10, chars_each=100)  # 20 msgs, ~500 tokens total

    # Use a tiny window to force compaction
    compacted, summary = await compact(
        messages,
        transport=fake_transport,
        window_size=200,        # very small → total > budget
        reserve_tokens=10,
        keep_recent_tokens=50,  # keep ~50 tokens worth (a couple messages)
    )

    # Compacted list must be shorter than original
    assert len(compacted) < len(messages), "Should have compacted"

    # First message must be a SystemMessage (the summary)
    assert isinstance(compacted[0], SystemMessage), "First message should be summary"

    # There must be at least 1 recent message after the summary
    assert len(compacted) >= 2, "Must keep some recent messages"

    # LLM must have been called exactly once
    assert len(fake_transport.calls) == 1


# ---------------------------------------------------------------------------
# Test 3: Summary has all 6 required sections
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compact_summary_has_all_sections(fake_transport: FakeTransport) -> None:
    """StructuredSummary must have all 6 fields populated from the LLM response."""
    # Pre-configure the fake transport to return a structured JSON payload
    # matching the schema compact() expects from the summarizer LLM. The
    # default FakeTransport response is ``content="ok"`` which is not
    # parseable JSON, so each section would come back empty.
    from app.core.transport.provider import TransportResponse
    fake_transport.response = TransportResponse(
        content=json.dumps({
            "goals": "Build the user authentication service",
            "progress": "Drafted login endpoint and password hashing",
            "decisions": ["use bcrypt", "JWT for sessions"],
            "symbols_resolved": ["SymbolA", "login_handler"],
            "files_touched": ["/app/service.py", "/app/auth.py"],
            "open_questions": ["MFA: TOTP or WebAuthn?"],
        }),
        model="fake-model",
        input_tokens=1,
        output_tokens=2,
    )

    messages = _make_messages(20, chars_each=400)

    _, summary = await compact(
        messages,
        transport=fake_transport,
        window_size=500,
        reserve_tokens=50,
        keep_recent_tokens=100,
    )

    assert summary.goals, "goals must be non-empty"
    assert summary.progress, "progress must be non-empty"
    assert isinstance(summary.decisions, list)
    assert isinstance(summary.symbols_resolved, list)
    assert isinstance(summary.files_touched, list)
    assert isinstance(summary.open_questions, list)

    # Verify the fake transport's values were parsed
    assert "SymbolA" in summary.symbols_resolved
    assert "/app/service.py" in summary.files_touched
    assert len(summary.decisions) >= 1


# ---------------------------------------------------------------------------
# Test 4: symbols_resolved + files_touched accumulate across two compactions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cumulative_tracking_across_two_compactions() -> None:
    """Cumulative lists must grow (never shrink) across back-to-back compactions."""
    first_response = json.dumps({
        "goals": "First round goal",
        "progress": "Did step 1",
        "decisions": ["decision-alpha"],
        "symbols_resolved": ["FuncA"],
        "files_touched": ["/src/a.py"],
        "open_questions": ["question-1"],
    })
    second_response = json.dumps({
        "goals": "Second round goal",
        "progress": "Did step 2",
        "decisions": ["decision-beta"],
        "symbols_resolved": ["FuncB"],
        "files_touched": ["/src/b.py"],
        "open_questions": [],
    })

    transport1 = FakeTransport(response=first_response)
    transport2 = FakeTransport(response=second_response)

    messages = _make_messages(20, chars_each=400)

    # First compaction
    compacted1, summary1 = await compact(
        messages,
        transport=transport1,
        window_size=500,
        reserve_tokens=50,
        keep_recent_tokens=100,
    )

    # Second compaction uses prior_summary from the first
    _, summary2 = await compact(
        compacted1,
        transport=transport2,
        window_size=200,
        reserve_tokens=20,
        keep_recent_tokens=50,
        prior_summary=summary1,
    )

    # Both symbols must survive
    assert "FuncA" in summary2.symbols_resolved, "FuncA from first compaction must persist"
    assert "FuncB" in summary2.symbols_resolved, "FuncB from second compaction must be added"

    # Both files must survive
    assert "/src/a.py" in summary2.files_touched
    assert "/src/b.py" in summary2.files_touched

    # Decisions accumulate (deduplication preserves both)
    decision_text = " ".join(summary2.decisions)
    assert "decision-alpha" in decision_text
    assert "decision-beta" in decision_text


# ---------------------------------------------------------------------------
# Test 5: FakeTransport is used — no real LLM calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uses_fake_transport_not_real_llm(fake_transport: FakeTransport) -> None:
    """FakeTransport must record calls and no real HTTP request should be made."""
    messages = _make_messages(20, chars_each=400)

    assert len(fake_transport.calls) == 0

    await compact(
        messages,
        transport=fake_transport,
        window_size=500,
        reserve_tokens=50,
        keep_recent_tokens=100,
    )

    # If the real LLM was called, this would fail with a network error.
    # If FakeTransport was used, calls are recorded as _RecordedCall
    # dataclasses (see tests/conftest.py FakeTransport).
    assert len(fake_transport.calls) == 1
    call = fake_transport.calls[0]
    # _RecordedCall is a dataclass — use attribute access, not subscripting.
    assert hasattr(call, "messages")
    assert len(call.messages) >= 1
    # The first message routed to the LLM is the summarization prompt.
    first = call.messages[0]
    # The transport accepts either LangChain BaseMessage objects or
    # ``TransportMessage`` dicts. Accept either shape here.
    assert hasattr(first, "type") or (isinstance(first, dict) and "role" in first)


# ---------------------------------------------------------------------------
# Test 6: Idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotency_compact_twice_under_threshold(fake_transport: FakeTransport) -> None:
    """compact(compact(x)) == compact(x) when already under threshold.

    Strategy:
    - First compaction: use a very small window to force compaction.
    - Second compaction: use a much larger window so the compacted result
      is definitively under threshold. compact() must return the same object.
    """
    messages = _make_messages(20, chars_each=400)

    # First compaction (over tiny threshold)
    compacted1, summary1 = await compact(
        messages,
        transport=fake_transport,
        window_size=500,
        reserve_tokens=50,
        keep_recent_tokens=100,
    )

    # Compacted list must start with a SystemMessage
    assert isinstance(compacted1[0], SystemMessage)

    # Second compaction: use a large window so compacted1 is definitively under budget
    compacted2, summary2 = await compact(
        compacted1,
        transport=fake_transport,
        window_size=200_000,   # generous — compacted1 is well under this
        reserve_tokens=16_384,
        keep_recent_tokens=20_000,
        prior_summary=summary1,
    )

    # compacted2 should be the same list object as compacted1 (no-op)
    assert compacted2 is compacted1, (
        "When already under threshold, compact() must return the same list unchanged"
    )

    # No additional LLM call should have happened
    assert len(fake_transport.calls) == 1, (
        "Second compact() (no-op) must not make an additional LLM call"
    )


# ---------------------------------------------------------------------------
# Test 7: to_system_message_text renders all required headers
# ---------------------------------------------------------------------------

def test_to_system_message_text_contains_all_headers() -> None:
    """The rendered summary block must include all 6 section headers."""
    summary = StructuredSummary(
        goals="Fix the login bug",
        progress="Checked auth.py",
        decisions=["use JWT over sessions"],
        symbols_resolved=["login_view", "AuthToken"],
        files_touched=["/app/auth.py"],
        open_questions=["Does this affect mobile?"],
        generated_at=datetime.now(timezone.utc),
        token_count_at_summarization=1234,
    )

    text = summary.to_system_message_text()

    assert "## Goals" in text
    assert "## Progress" in text
    assert "## Decisions" in text
    assert "## Symbols Resolved" in text
    assert "## Files Touched" in text
    assert "## Open Questions" in text
    assert "Fix the login bug" in text
    assert "/app/auth.py" in text
    assert "login_view" in text


# ---------------------------------------------------------------------------
# Test 8: _extract_symbols_and_files picks up paths and symbols
# ---------------------------------------------------------------------------

def test_extract_symbols_and_files() -> None:
    """Helper must extract file paths from tool results and symbols from tool calls."""
    ai_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc1",
                "name": "get_function",
                "args": {"name": "process_payment", "file_path": "/app/billing.py"},
            },
            {
                "id": "tc2",
                "name": "find_symbol",
                "args": {"symbol": "PaymentGateway"},
            },
        ],
    )

    tool_result = json.dumps({
        "file_path": "/app/models/payment.py",
        "content": "class PaymentGateway: ...",
    })
    tool_msg = ToolMessage(content=tool_result, tool_call_id="tc1")

    messages: List[BaseMessage] = [ai_msg, tool_msg]
    symbols, files = _extract_symbols_and_files(messages)

    assert "process_payment" in symbols
    assert "PaymentGateway" in symbols
    assert "/app/billing.py" in files
    assert "/app/models/payment.py" in files


# ---------------------------------------------------------------------------
# Test 9: MemoryManager.compact_if_needed returns unchanged when under threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_manager_noop(fake_transport: FakeTransport) -> None:
    """MemoryManager.compact_if_needed should be a no-op when under threshold."""
    manager = MemoryManager(
        transport=fake_transport,
        session_id="test-session-noop",
        window_size=100_000,
        reserve_tokens=5_000,
        keep_recent_tokens=10_000,
    )

    messages = _make_messages(2, chars_each=100)
    result = await manager.compact_if_needed(messages)

    assert result is messages
    assert len(fake_transport.calls) == 0


# ---------------------------------------------------------------------------
# Test 10: MemoryManager.compact_if_needed triggers and saves summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_manager_compacts_and_persists(fake_transport: FakeTransport) -> None:
    """MemoryManager must compact and persist the summary when over threshold."""
    session_id = "test-session-compact"
    manager = MemoryManager(
        transport=fake_transport,
        session_id=session_id,
        window_size=300,
        reserve_tokens=20,
        keep_recent_tokens=50,
    )

    messages = _make_messages(15, chars_each=300)
    compacted = await manager.compact_if_needed(messages)

    assert len(compacted) < len(messages)
    assert isinstance(compacted[0], SystemMessage)

    saved = manager.get_current_summary()
    assert saved is not None
    assert isinstance(saved, StructuredSummary)
