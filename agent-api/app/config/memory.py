"""Semantic memory, durable facts, compaction, chat replay, knowledge bundle.

One slice of :class:`app.config.Settings`. Mixins carry no behaviour of
their own — they exist so 200+ fields are readable in domain-sized files.
Settings inherits every one of them, so the flat ``settings.<field>``
surface every call site already uses is unchanged.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

from app.config._shared import DEFAULT_DELEGATION_BLOCKED_TOOLS  # noqa: F401


class MemorySettings(BaseSettings):
    """Semantic memory, durable facts, compaction, chat replay, knowledge bundle."""

    # Metamemory (app.harness.metamemory): seeds /plan.txt, /milestones.txt,
    # /context_summary.txt in the session VFS at run start and steers compaction
    # to inject them instead of re-summarizing raw history. Also read at the
    # start of a follow-up turn so a resumed investigation begins with prior
    # plan/progress in view (preflight injects a resumed-state block). Default
    # ON: still a full no-op unless AgentSpec.filesystem is set on the profile
    # (nowhere to seed files otherwise), so existing non-filesystem workflows
    # are byte-identical. Toggle in Settings → Feature flags.
    metamemory_enabled: bool = Field(default=True, validation_alias="METAMEMORY_ENABLED")

    # ── Bank-scoped semantic memory ──────────────────────────────────────────
    # Learned, operational memory: auto-captured investigation findings recalled
    # (Postgres FTS) before each run, scoped per-repo + a shared global bank.
    # Opt-in (default off) so it is a no-op until an operator enables it. Recall
    # is full-text over the OKF bundle + semantic_memory banks; document-side
    # tags act as paraphrase synonyms (the Bedrock-embedding legs were removed).
    semantic_memory_enabled: bool = Field(
        default=False, validation_alias="SEMANTIC_MEMORY_ENABLED"
    )

    # Token-budget guardrails (keep recall net-positive, not a per-turn leak):
    memory_recall_k: int = Field(default=4, validation_alias="MEMORY_RECALL_K")

    memory_max_chars: int = Field(default=600, validation_alias="MEMORY_MAX_CHARS")

    # Only auto-capture an investigation finding when confidence is at least this
    # (avoids storing low-value/uncertain results). 0 captures everything.
    memory_capture_min_confidence: float = Field(
        default=0.6, validation_alias="MEMORY_CAPTURE_MIN_CONFIDENCE"
    )

    # ── Per-turn durable-fact extraction + audit loop ────────────────────────
    # After each turn, extract a few DURABLE operational/session facts and store
    # them in semantic_memory (conservative caps: a couple short facts per turn).
    # Opt-in (default off) — a no-op until enabled. Reuses the existing store and
    # its sha256 dedup, so it never duplicates infra.
    memory_fact_extraction_enabled: bool = Field(
        default=False, validation_alias="MEMORY_FACT_EXTRACTION_ENABLED"
    )

    memory_fact_max_per_turn: int = Field(
        default=2, validation_alias="MEMORY_FACT_MAX_PER_TURN"
    )

    # The audit/consolidation pass (run by the curator) merges memories that
    # state the same fact in different words. A SHA256 fingerprint of the store
    # short-circuits the LLM call when nothing changed; a hard guard refuses any
    # pass that would delete more than this fraction of memories.
    memory_audit_enabled: bool = Field(
        default=False, validation_alias="MEMORY_AUDIT_ENABLED"
    )

    memory_audit_max_delete_fraction: float = Field(
        default=0.5, validation_alias="MEMORY_AUDIT_MAX_DELETE_FRACTION"
    )

    # ── Knowledge bundle (OKF) + skill storage (file-based; no DB) ────────────
    # Durable knowledge (known issues, log patterns, services, skills) lives as
    # an Open Knowledge Format bundle: a portable directory of markdown files
    # with YAML frontmatter. Auto-learn writes here (reviewable file diffs) and
    # concepts are indexed into the ``kb`` memory bank for recall.
    knowledge_dir: str = Field(default="data/knowledge", validation_alias="KNOWLEDGE_DIR")

    knowledge_bundle_enabled: bool = Field(
        default=True, validation_alias="KNOWLEDGE_BUNDLE_ENABLED"
    )

    # Retrievability lint: recall is FTS-only (no vector fallback), so a concept
    # with no tags and a thin title is unfindable by paraphrase. write_concept
    # logs a warning when a doc has fewer than this many tags or a too-short
    # description; the same check backs the bundle-scan lint. Advisory only —
    # never blocks a write (0 disables the tag check).
    knowledge_min_tags: int = Field(default=3, validation_alias="KNOWLEDGE_MIN_TAGS")

    # ── Pinned-facts memory tier ─────────────────────────────────────────────
    # An always-injected (not similarity-gated) memory tier on the existing
    # semantic store (bank='pinned'), with a per-turn token budget.
    pinned_facts_enabled: bool = Field(
        default=True, validation_alias="PINNED_FACTS_ENABLED"
    )

    memory_turn_token_budget: int = Field(
        default=800, validation_alias="MEMORY_TURN_TOKEN_BUDGET"
    )

    pinned_facts_max_tokens: int = Field(
        default=400, validation_alias="PINNED_FACTS_MAX_TOKENS"
    )

    # ── Context-injection sizes (operator-tunable; NOT hardcoded caps) ────────
    # How much recalled/pre-computed context may be injected into the model's
    # INPUT. These were module-level constants in app/harness/helpers.py and
    # app/harness/metamemory.py — moved here so limits are configurable per
    # deployment rather than baked into source. Set to 0 to disable truncation
    # entirely (inject the full payload — bounded only by context compaction).
    recall_block_max_chars: int = Field(
        default=1_000, validation_alias="RECALL_BLOCK_MAX_CHARS"
    )

    context_block_max_chars: int = Field(
        default=800, validation_alias="CONTEXT_BLOCK_MAX_CHARS"
    )

    metamemory_inject_max_chars: int = Field(
        default=2_400, validation_alias="METAMEMORY_INJECT_MAX_CHARS"
    )

    pinned_promote_recall_threshold: int = Field(
        default=3, validation_alias="PINNED_PROMOTE_RECALL_THRESHOLD"
    )

    # ── Per-chat-session compaction ──────────────────────────────────────────
    # Chat.jsx replays the last ~12 messages every turn; each can carry a full
    # multi-KB investigation report. This budget is about keeping THAT replay
    # light (distinct from the model's full context window, which the
    # per-execution ContextCompactionManager already governs) — when a
    # session's stored messages exceed it, older turns collapse into one
    # system summary + a recent tail (session_repository.replace_with_summary).
    chat_session_compaction_tokens: int = Field(
        default=6_000, validation_alias="CHAT_SESSION_COMPACTION_TOKENS"
    )

    chat_session_keep_recent_tokens: int = Field(
        default=1_500, validation_alias="CHAT_SESSION_KEEP_RECENT_TOKENS"
    )

    # ── Tool-inclusive chat history replay ───────────────────────────────────
    # A follow-up question re-runs every tool from scratch unless prior turns'
    # tool_calls/tool_results are replayed alongside the plain text history
    # (app.harness.chat_history). Fail-open by design: any error in the
    # rebuild path falls back to the UI's text-only history, so this defaults
    # on. The token/execution caps bound how much prior tool activity gets
    # replayed — per-turn context compaction handles overall window pressure
    # on top of this.
    chat_history_include_tools: bool = Field(
        default=True, validation_alias="CHAT_HISTORY_INCLUDE_TOOLS"
    )

    chat_tool_history_max_tokens: int = Field(
        default=12_000, validation_alias="CHAT_TOOL_HISTORY_MAX_TOKENS"
    )

    chat_tool_history_max_executions: int = Field(
        default=2, validation_alias="CHAT_TOOL_HISTORY_MAX_EXECUTIONS"
    )

    # How often the scheduler runs the self-improvement curator (hours). The
    # cheap promotions run every cycle; LLM consolidation stays gated by
    # ``memory_audit_enabled``.
    curator_interval_hours: int = Field(
        default=6, validation_alias="CURATOR_INTERVAL_HOURS"
    )

    # ── Context references (@file / @folder / @url / @git in the query) ──────
    # When enabled, an inbound query is scanned for @-references and their
    # content is expanded inline. File access is restricted to
    # ``context_reference_root`` (defaults to the process CWD). No-op when the
    # message has no references.
    context_references_enabled: bool = Field(
        default=True, validation_alias="CONTEXT_REFERENCES_ENABLED"
    )

    context_reference_root: str = Field(
        default="", validation_alias="CONTEXT_REFERENCE_ROOT"
    )
