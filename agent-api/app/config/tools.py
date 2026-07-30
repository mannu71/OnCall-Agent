"""Skills, code search/indexing, DB introspection, sandbox, output compression.

One slice of :class:`app.config.Settings`. Mixins carry no behaviour of
their own — they exist so 200+ fields are readable in domain-sized files.
Settings inherits every one of them, so the flat ``settings.<field>``
surface every call site already uses is unchanged.
"""
from __future__ import annotations

import os
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

from app.config._shared import DEFAULT_DELEGATION_BLOCKED_TOOLS  # noqa: F401


class ToolSettings(BaseSettings):
    """Skills, code search/indexing, DB introspection, sandbox, output compression."""

    # Crawler / code analyzer
    repos_base_path: str = Field(
        default="/tmp/indexed_repos",
        validation_alias="REPOS_BASE_PATH",
    )

    # codegraph — the native C code-intelligence engine baked into the image. Used
    # as an alternative Code-Crawler-node backend, driven in-process over stdio
    # (codegraph serve) via an inline MCP config — NOT registered as a platform
    # MCP server. Path is configurable for non-container / test hosts.
    codegraph_bin: str = Field(
        default="/usr/local/bin/codegraph",
        validation_alias="CODEGRAPH_BIN",
    )

    # v0.10.0+ of the codegraph engine stores one SQLite DB per project under a
    # cache directory (default ~/.cache/codegraph). Admin reads scan this dir.
    # Override via CODEGRAPH_CACHE_DIR.
    codegraph_cache_dir: str = Field(
        default=os.path.expanduser("~/.cache/codegraph"),
        validation_alias="CODEGRAPH_CACHE_DIR",
    )

    # Per-call timeout (seconds) for codegraph's `search_code` tool only. It shells
    # out to grep over source files (the one non-graph, potentially slow tool); the
    # fast graph tools inherit the default MCP tool timeout. Capping it stops a
    # single broad grep from exhausting an agent run's time/iteration budget — the
    # agent gets a recoverable tool error and falls back to the graph tools. A
    # timeout no longer forces an MCP reconnect (the session survives it safely —
    # see mcp_client_manager.execute_tool).
    #
    # 45s (was 120s): this cap MUST stay well below the delegated-subagent budget
    # (delegation_child_timeout_seconds, 240s) — at 120s a single slow grep ate
    # two-thirds of a code-investigation subagent's wall clock and timed it out
    # before it could fall back to the fast indexed tools. search_code is already
    # steered to be a last resort (see build_codegraph_tools), so failing fast and
    # falling back costs little; a genuinely huge grep just returns a recoverable
    # timeout sooner.
    codegraph_search_timeout_seconds: float = Field(
        default=45.0,
        validation_alias="CODEGRAPH_SEARCH_TIMEOUT",
    )

    code_analyzer_list_cache_ttl_seconds: float = 60.0

    index_parse_concurrency: int = 4

    index_file_read_concurrency: int = 8

    # Bounded DB schema lookup tools (db_list_tables / db_describe_table / db_search_columns)
    db_schema_cache_ttl_seconds: float = 300.0

    db_schema_max_tables: int = 2000      # complete-catalog fetch cap (names are cheap)

    db_schema_output_max_rows: int = 200  # max names/columns returned per tool call

    db_schema_max_columns: int = 300

    # ── ONNX code-embedding semantic search ──────────────────────────────────
    # A Bedrock-independent embedding path for concept-level code search (fills
    # codegraph's empty search_semantic). Runs a small quantized sentence-
    # transformer on CPU via onnxruntime with a SHA256(model+content) cache.
    # Always on: it degrades gracefully (the tool returns an error/indexing note)
    # until a model is provisioned, so there's no reason to gate it. The env var
    # remains as an escape hatch for a locked-down build.
    code_semantic_enabled: bool = Field(
        default=True, validation_alias="CODE_SEMANTIC_ENABLED"
    )

    # The single embedding model used: snowflake-arctic-embed-s — best accuracy in
    # the fast 384-dim/34MB tier (see app/core/code_semantic/models.py). Fixed
    # (not user-selectable); the env var stays for an ops override if ever needed.
    code_semantic_model: str = Field(
        default="snowflake-arctic-embed-s", validation_alias="CODE_SEMANTIC_MODEL"
    )

    # Base dir holding one subdir per model. Defaults to /opt/models, where the
    # arctic-embed-s model is baked into the image (NOT /app/data — a mounted
    # volume that would shadow baked files). The Docker image also sets
    # CODE_SEMANTIC_MODELS_ROOT=/opt/models explicitly.
    code_semantic_models_root: str = Field(
        default="/opt/models", validation_alias="CODE_SEMANTIC_MODELS_ROOT"
    )

    # SQLite embedding cache path (SHA256(model+content) → float32 vector).
    code_semantic_cache_db: str = Field(
        default="/app/data/code_semantic/embeddings.db",
        validation_alias="CODE_SEMANTIC_CACHE_DB",
    )

    # When the model files are missing, allow fetching them from HuggingFace over
    # an unverified TLS context (the only path past the corp self-signed-cert
    # wall). Off by default — a locked-down deploy should vendor the files.
    code_semantic_allow_download: bool = Field(
        default=False, validation_alias="CODE_SEMANTIC_ALLOW_DOWNLOAD"
    )

    code_semantic_batch_size: int = Field(
        default=32, validation_alias="CODE_SEMANTIC_BATCH_SIZE"
    )

    # Max chars of a symbol's real source body folded into its embedding text
    # (0 = header-only). Embedding the body is the main accuracy lever, but longer
    # text = proportionally slower indexing, so this is the primary index-cost
    # knob. 2000 ≈ ~50 lines — enough of the body for real retrieval signal past
    # the signature; the embedder's 512-token cap bounds the upper end anyway, and
    # background indexing absorbs the extra build cost. Was 600 (signature-only),
    # which made long functions retrievable only by their opening lines.
    code_semantic_body_max_chars: int = Field(
        default=2000, validation_alias="CODE_SEMANTIC_BODY_MAX_CHARS"
    )

    # Score multiplier applied to test/build-output entities when the query does
    # NOT express test intent — de-prioritizes tests without hiding them (mirrors
    # code-context-engine's 0.8 path penalty). 1.0 disables the penalty.
    code_semantic_test_penalty: float = Field(
        default=0.85, validation_alias="CODE_SEMANTIC_TEST_PENALTY"
    )

    # Default model id for the shared DB-resolved LLM utility (app.core.llm.
    # call_llm) — used as the env fallback when no DB LLM config exists, and as a
    # last-resort model for compaction summaries. Backs graders, supervision, and
    # memory extraction. Override via CRAWLER_MODEL (name kept for compatibility
    # with the "crawler" gateway role).
    crawler_model: str = "anthropic.claude-3-5-haiku-20241022-v1:0"

    # Opt-in override for the shared call_llm utility. When set, it uses this
    # model instead of the DB-configured model — callers can be moved to a
    # cheaper tier once accuracy holds. None = keep DB model.
    crawler_model_override: Optional[str] = Field(
        default=None, validation_alias="CRAWLER_MODEL_OVERRIDE"
    )

    # ── Tool-output compression ──────────────────────────────────────────────
    # Replaces lossy char-truncation with type-aware reversible compression for
    # large tool outputs. Compression runs in a local sidecar (no LLM call).
    # Opt-in (default off); on timeout/error falls back to existing truncation.
    # Compression is applied at the tool-output source and ALWAYS re-capped, so
    # conversation-history compaction is intentionally left to the existing
    # structure-aware ladder (it preserves Bedrock tool_use/tool_result pairing,
    # which a message-array compressor cannot guarantee). Reversible retrieval
    # (recover originals) is covered by the VFS offload + fs_read path.
    compression_enabled: bool = Field(
        default=False, validation_alias="COMPRESSION_ENABLED"
    )

    compression_endpoint: str = Field(
        default="http://headroom:8787", validation_alias="COMPRESSION_ENDPOINT"
    )

    # Only compress outputs larger than this; smaller ones go straight to truncation.
    compression_min_chars: int = Field(
        default=2000, validation_alias="COMPRESSION_MIN_CHARS"
    )

    # Hard timeout for a single compress call (ms). On expiry, fall back to truncation.
    compression_timeout_ms: int = Field(
        default=2000, validation_alias="COMPRESSION_TIMEOUT_MS"
    )

    # Retries on transient sidecar errors. Compression is opportunistic (the
    # truncation fallback is always correct), so keep this low to bound the
    # latency added to the hot tool-call path.
    compression_max_retries: int = Field(
        default=1, validation_alias="COMPRESSION_MAX_RETRIES"
    )

    # Model id sent to the sidecar so it selects the matching tokenizer. Empty
    # falls back to a neutral placeholder. Never hardcoded — operators set the
    # deployed Bedrock model id here (models are DB-resolved elsewhere).
    compression_model_hint: str = Field(
        default="", validation_alias="COMPRESSION_MODEL_HINT"
    )

    # Extend compression from MCP tool outputs to every other tool family
    # (db, cloudwatch, crawler, code analyzer) via the universal output cap.
    # Default off → non-MCP tool behaviour is byte-identical until enabled.
    compression_all_tools: bool = Field(
        default=False, validation_alias="COMPRESSION_ALL_TOOLS"
    )

    # ── Skill + tool disclosure (two-stage: map + search/load tools) ─────────
    # Stage one is a MAP — names only, near-zero per-turn cost: every
    # non-conversational turn gets a ``# Skill map`` block naming the available
    # skills, and a deferred MCP tool set is named in the ``search_tools``
    # description. Stage two is the model's own lookup: ``search_skills`` /
    # ``search_tools`` rank the map by intent, then ``skill`` loads the full
    # runbook (or ``call_tool`` invokes the tool). A user message starting with
    # ``/<skill-name>`` bypasses both stages and expands that skill directly.
    skill_tool_enabled: bool = Field(
        default=True, validation_alias="SKILL_TOOL_ENABLED"
    )

    # Deterministic ``/<skill-name> [args]`` slash-command expansion in chat.
    skill_slash_commands_enabled: bool = Field(
        default=True, validation_alias="SKILL_SLASH_COMMANDS_ENABLED"
    )

    # Char budget for the per-turn skill map (names only). Over budget the map
    # degrades to a count-only hint — search_skills still reaches every skill.
    skill_map_char_budget: int = Field(
        default=1500, validation_alias="SKILL_MAP_CHAR_BUDGET"
    )

    # Default number of skills ``search_skills`` returns per query.
    skill_search_k: int = Field(default=5, validation_alias="SKILL_SEARCH_K")

    # Char budget for the tool map embedded in the ``search_tools`` description
    # (deferred tool names). Over budget it truncates to "+K more".
    tool_map_char_budget: int = Field(
        default=2000, validation_alias="TOOL_MAP_CHAR_BUDGET"
    )

    # Cap on the full runbook body injected when a skill is loaded by the
    # ``skill`` tool. Longer bodies are truncated.
    skill_body_inject_chars: int = Field(
        default=8000, validation_alias="SKILL_BODY_INJECT_CHARS"
    )

    # Skills are markdown SKILL.md files (SkillManager) under skills_dir — now a
    # sub-tree of the knowledge bundle so skills + KB share one portable bundle
    # and can cross-link (a known-issue doc → its runbook skill).
    skills_dir: str = Field(
        default="data/knowledge/skills", validation_alias="SKILLS_DIR"
    )

    # ── Tool execution sandbox ───────────────────────────────────────────────
    # Isolate shell/code-execution tools. Backends:
    #   disabled  — no sandboxing (default; tools run in-process as before)
    #   auto      — pick the best available for the OS (container > bwrap > seatbelt)
    #   container — ephemeral Docker container (best for this Docker stack)
    #   bwrap     — Linux bubblewrap
    #   seatbelt  — macOS sandbox-exec
    sandbox_backend: str = Field(default="disabled", validation_alias="SANDBOX_BACKEND")

    sandbox_image: str = Field(default="python:3.12-slim", validation_alias="SANDBOX_IMAGE")

    sandbox_timeout_seconds: int = Field(default=60, validation_alias="SANDBOX_TIMEOUT_SECONDS")

    sandbox_memory: str = Field(default="512m", validation_alias="SANDBOX_MEMORY")

    sandbox_cpus: str = Field(default="1", validation_alias="SANDBOX_CPUS")

    sandbox_network: bool = Field(default=False, validation_alias="SANDBOX_NETWORK")
