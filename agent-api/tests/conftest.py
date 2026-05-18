"""Pytest configuration and fixtures for agent-api tests.

This module wires up the foundational fakes and an in-memory DB so future
refactors (god-class split, repo extraction, transport seam, etc.) can land
behind a real test suite. Keep these fakes thin — they are scaffolding for
unit tests, not full-fidelity simulators.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.transport.provider import (
    ProviderTransport,
    TransportMessage,
    TransportResponse,
)


# ─────────────────────────────────────────────────────────────────────────────
# FakeTransport — implements ProviderTransport, records calls for assertions.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _RecordedCall:
    """One captured call to FakeTransport.complete() or complete_stream()."""
    method: str
    messages: List[TransportMessage]
    model: str
    max_tokens: int
    temperature: float
    system: Optional[str]
    tools: Optional[List[Dict[str, Any]]]
    wall_clock: float = field(default_factory=time.monotonic)


class FakeTransport(ProviderTransport):
    """In-memory ProviderTransport for unit tests.

    Configurable response is returned by every ``complete()`` call.
    ``complete_stream()`` yields chunks one at a time.  All calls are
    recorded on ``self.calls`` so tests can assert on routing / ordering.
    """

    def __init__(
        self,
        *,
        response: Optional[TransportResponse] = None,
        stream_chunks: Optional[List[str]] = None,
        complete_delay: float = 0.0,
    ) -> None:
        self.response = response or TransportResponse(
            content="ok", model="fake-model",
            input_tokens=1, output_tokens=2,
        )
        self.stream_chunks = list(stream_chunks or ["fake", " ", "stream"])
        self.complete_delay = complete_delay
        self.calls: List[_RecordedCall] = []

    async def complete(
        self,
        messages,
        *,
        model,
        max_tokens=4096,
        temperature=0.7,
        system=None,
        tools=None,
    ) -> TransportResponse:
        self.calls.append(_RecordedCall(
            method="complete", messages=list(messages), model=model,
            max_tokens=max_tokens, temperature=temperature,
            system=system, tools=tools,
        ))
        if self.complete_delay:
            await asyncio.sleep(self.complete_delay)
        return self.response

    async def complete_stream(
        self,
        messages,
        *,
        model,
        max_tokens=4096,
        temperature=0.7,
        system=None,
        on_usage=None,
    ) -> AsyncGenerator[str, None]:
        self.calls.append(_RecordedCall(
            method="complete_stream", messages=list(messages), model=model,
            max_tokens=max_tokens, temperature=temperature,
            system=system, tools=None,
        ))
        for chunk in self.stream_chunks:
            yield chunk
        if on_usage is not None:
            await on_usage({
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "model": model,
            })

    def get_langchain_llm(self, model: str, **kwargs: Any) -> Any:
        # Tests that care about the LangChain seam should stub this on
        # their own FakeTransport instance.
        return None


@pytest.fixture
def fake_transport() -> FakeTransport:
    """Default FakeTransport — override per test by constructing your own."""
    return FakeTransport()


# ─────────────────────────────────────────────────────────────────────────────
# FakeMCPClientManager — records tool calls and returns canned results.
# ─────────────────────────────────────────────────────────────────────────────

class FakeMCPClientManager:
    """In-memory stand-in for ``MCPClientManager``.

    Tests pre-load ``canned_results`` keyed by tool name (or ``(server, tool)``)
    and inspect ``calls`` afterwards.
    """

    def __init__(
        self,
        canned_results: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.canned_results: Dict[str, Any] = canned_results or {}
        self.calls: List[Dict[str, Any]] = []
        # Match real manager surface where possible.
        self.connected_servers: Dict[str, Any] = {}
        self.tool_schemas: Dict[str, Dict[str, Any]] = {}

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append({
            "server_id": server_id, "tool_name": tool_name,
            "arguments": arguments or {},
        })
        # Look up by (server, tool) first, fall back to tool name.
        key = f"{server_id}:{tool_name}"
        if key in self.canned_results:
            return self.canned_results[key]
        return self.canned_results.get(tool_name, {"ok": True})

    async def connect(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def disconnect_all(self) -> None:
        self.connected_servers.clear()


@pytest.fixture
def fake_mcp_manager() -> FakeMCPClientManager:
    return FakeMCPClientManager()


# ─────────────────────────────────────────────────────────────────────────────
# In-memory SQLite session for repository tests.
#
# pgvector.Vector columns on KnowledgeEntryModel / LogPatternModel are not
# supported by SQLite, so we create only the tables we actually exercise.
# ─────────────────────────────────────────────────────────────────────────────

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession bound to a fresh in-memory SQLite database.

    Only creates tables that don't depend on pgvector.  Tests that need
    pgvector-backed tables should ``pytest.skip`` themselves.
    """
    try:
        # Import lazily so a missing aiosqlite/pgvector setup surfaces as a
        # skip in the test, not a collection error.
        from app.models.db_models import (
            CodeReference,
            CodeSymbol,
            ExecutionModel,
            WorkflowModel,
        )
    except Exception as exc:  # pragma: no cover - defensive
        pytest.skip(f"db_models import failed: {exc}")

    engine = create_async_engine(
        _SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create only the non-pgvector tables we need. Doing this per-table
    # avoids the pgvector Vector(1536) columns on knowledge_entries /
    # log_patterns, which SQLite cannot compile.
    safe_tables = [
        ExecutionModel.__table__,
        WorkflowModel.__table__,
        # Phase 2 SCIP tables — pure SQLAlchemy core types, SQLite-safe.
        CodeSymbol.__table__,
        CodeReference.__table__,
    ]
    try:
        async with engine.begin() as conn:
            for table in safe_tables:
                await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn, checkfirst=True))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"SQLite schema creation failed (likely pgvector incompat): {exc}")

    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def patched_async_session(monkeypatch, db_session):
    """Patch ``app.core.database.AsyncSessionLocal`` to use the in-memory DB.

    Returns a context-manager-compatible factory that yields the shared
    ``db_session`` so repository code (which opens its own ``async with
    AsyncSessionLocal() as session``) talks to the same SQLite engine.

    TODO: this shares one session across all ``async with`` blocks inside a
    single test.  That's fine for the simple save/get flows we cover today
    but real concurrent repo tests will need a per-call session factory.
    """
    from app.core import database as db_mod

    class _SharedSessionCtx:
        async def __aenter__(self_inner):
            return db_session

        async def __aexit__(self_inner, exc_type, exc, tb):
            # Don't close the shared session; the db_session fixture owns it.
            return False

    def _factory(*args, **kwargs):
        return _SharedSessionCtx()

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", _factory)
    # Some modules import AsyncSessionLocal directly at import time.
    import app.infrastructure.persistence.execution_repository as exec_repo_mod
    monkeypatch.setattr(exec_repo_mod, "AsyncSessionLocal", _factory)
    return _factory
