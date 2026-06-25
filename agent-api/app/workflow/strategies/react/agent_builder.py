"""Shared system-prompt composition (used by both the legacy and deepagents harnesses).

The legacy in-house ReAct construction (``build_agent`` / ``_finish_build_agent``)
moved to ``app/legacy/react_agent.py`` as part of the deepagents migration. This
module now only assembles the deterministic system prompt — the Bedrock cachePoint
prefix — which BOTH harnesses use, so the prompt (and accuracy) stays identical.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def compose_system_prompt(
    *,
    tools: List[Any],
    agent_config: Dict[str, Any],
    has_cloudwatch: bool = False,
    has_code_analyzer: bool = False,
    capabilities: Optional[List[str]] = None,
    role_prompt: Optional[str] = None,
    planning: bool = False,
    filesystem: bool = False,
    sandbox: bool = False,
    subagents: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Assemble the deterministic system prompt (the Bedrock cachePoint prefix).

    Extracted so both the legacy ReAct builder and the deepagents harness compose
    the *same* prompt — the accuracy-critical, cache-stable text. Never inject
    run-specific data here (see the CACHE CONTRACT note below).
    """
    # Build system prompt from agent instructions if provided
    instructions = agent_config.get("instructions", "")
    agent_mode = agent_config.get("agentMode", "single")

    # Determine which capability groups are actually present so the role
    # sentence and instructions accurately reflect what the agent can do.
    #
    # The database capability section describes the SQL schema-navigation tools
    # (db_list_tables / db_describe_table / db_search_columns), which are built
    # only when a Database node populates db_server_map. Activate it ONLY when
    # those tools are present — never for arbitrary MCP tools. A generic MCP
    # server (Azure DevOps, a custom API, …) is NOT a SQL database and must not
    # be steered with SQL guidance; its behavior is driven entirely by the
    # agent's own instructions and the tools the user wired. This keeps the
    # platform capability-agnostic rather than assuming "any MCP tool = a DB".
    has_db_tools = any(
        getattr(t, "name", "").startswith("db_") for t in tools
    )

    # ── Resolve active capabilities (composable; see app.harness.capabilities) ──
    # The investigation trio is derived from the runtime flags so existing
    # workflows are unchanged; a profile may declare extra capability ids via
    # ``capabilities`` (Phase 1), which append after the builtins in registry order.
    from app.harness import capabilities as _caps
    _active_ids: List[str] = []
    if has_db_tools:
        _active_ids.append("database")
    if has_cloudwatch:
        _active_ids.append("cloudwatch")
    if has_code_analyzer:
        _active_ids.append("code_analyzer")
    for _cid in (capabilities or []):
        if _cid not in _active_ids:
            _active_ids.append(_cid)
    active_caps = _caps.resolve(_active_ids)

    # ── Role sentence ──────────────────────────────────────────────────
    if role_prompt:
        # Profile-supplied full role override (Phase 1).
        role_sentence = role_prompt
    else:
        role_fragments = [c.role_fragment for c in active_caps if c.role_fragment]
        if role_fragments:
            capability_str = ", ".join(role_fragments)
            role_sentence = (
                f"You are an expert assistant "
                f"with access to {capability_str}. Adapt to whatever the user is "
                f"trying to do, using the tools available to you."
            )
        else:
            role_sentence = "You are an expert assistant."

    # ── System prompt assembly ─────────────────────────────────────────
    # CACHE CONTRACT: this prompt MUST stay deterministic given agent_config.
    # Never inject recalled memory, retry guidance, timestamps, or any other
    # run-specific data here — those belong in the query (see strategy.py
    # `augmented_query`). The composed string is the Bedrock cachePoint prefix
    # (system + tools), so any per-run variation busts the prompt cache on
    # every model call. Keep the section order and tool ordering stable too.
    system_parts = [role_sentence]

    # § Doing tasks — shared discipline that governs every capability below.
    system_parts.append(
        "# Doing tasks\n"
        "- Reason step by step and call the available tools to get facts — never guess "
        "or invent data.\n"
        "- The ONLY exception: if the user's message is purely a greeting, a thank-you, or "
        "other small talk with nothing to investigate, just reply conversationally without "
        "calling tools. Any actual question or task still uses the tools.\n"
        "- Answer exactly what was asked and back every claim with specific evidence from the "
        "tool results (repo/file/line, log_group/timestamp, table/column). Don't gold-plate or "
        "pad with unrequested analysis.\n"
        "- Stop as soon as the evidence supports a conclusion — every tool call has cost and "
        "latency, so don't keep digging once you can answer.\n"
        "- NEVER end a turn by announcing a next step (e.g. 'Let me search…', 'Now I'll check…'). "
        "If you say you will do something, call the tool in the SAME turn. If a focused search "
        "finds nothing, say so plainly rather than inventing an answer or trailing off."
    )

    # § Plan → execute → verify. Multi-step only, so trivial
    # single-tool runs are not bloated. Gated on a stable config flag + agent_mode
    # so the cache prefix stays deterministic per run (re-baseline evals on change).
    try:
        from app.config import settings as _settings
        _planning_on = bool(getattr(_settings, "agent_planning_enabled", True))
    except Exception:  # noqa: BLE001
        _planning_on = True
    if _planning_on and agent_mode == "multi":
        system_parts.append(
            "# Plan, then execute, then verify\n"
            "- For a multi-step task, FIRST write a short markdown task list "
            "(GitHub checkboxes: '- [ ] step') of the concrete steps you intend to take.\n"
            "- Then work the list top to bottom, calling tools as you go. Keep it honest: "
            "only check an item ('- [x]') once the tool evidence actually supports it.\n"
            "- Close with a brief '## Verification' section confirming each item was done and "
            "citing the evidence. Skip the plan for a trivial single-lookup question — it is a "
            "tool for genuinely multi-step work, not ceremony."
        )

    # § Using your tools — shared steering: prefer the specific tool, reuse work.
    system_parts.append(
        "# Using your tools\n"
        "- Prefer the most specific tool over a generic one, and fetch only what the question "
        "needs — never read whole files or dump an entire schema when a targeted lookup will do.\n"
        "- When you reach a useful conclusion or resolution worth reusing, call save_playbook to "
        "record it so future runs can benefit from it.\n"
        "- If the memory-context block at the start of the query lists 'Executable Skill' entries "
        "that match the current issue, prefer calling execute_skill with the skill's name before "
        "running manual tool calls — this reuses proven remediation steps and is faster."
    )

    # ── Capability sections (registry-driven; stable order) ──────────────
    # Each active capability contributes its system-prompt section in registry
    # order (database → cloudwatch → code_analyzer → any profile extras). The
    # text lives in app.harness.capabilities; this loop reproduces the previous
    # inline ordering exactly so the Bedrock cache prefix is unchanged.
    for _cap in active_caps:
        if _cap.section:
            system_parts.append(_cap.section)

    # On the deepagents harness, planning (write_todos via TodoListMiddleware) and
    # the filesystem (ls/read_file/write_file/edit_file/glob/grep) are provided by
    # deepagents' own middleware + system prompt, so we DON'T emit our overlapping
    # planning/scratch-filesystem sections (and don't inject our fs_*/write_todos
    # tools — see tool_assembler). Avoids duplicate tools + a write_todos name clash.
    try:
        from app.harness.spec_factory import resolve_harness
        _deepagents_builtins = resolve_harness(agent_config) == "deepagents"
    except Exception:  # noqa: BLE001
        _deepagents_builtins = False

    # ── Deep-agent capability instructions (profile-gated, config-stable) ──────
    if planning and not _deepagents_builtins:
        system_parts.append(
            "# Planning\n"
            "You have write_todos and update_todo. For a genuinely multi-step task, call "
            "write_todos FIRST with the concrete steps, then work the list top to bottom, "
            "calling update_todo to mark each item in_progress then completed as the tool "
            "evidence supports it. Skip the plan for a single-lookup question."
        )
    # Scratch filesystem (legacy harness only — deepagents has its own filesystem).
    if not _deepagents_builtins:
        system_parts.append(
        "# Scratch filesystem\n"
        "You have a session-scoped virtual filesystem (fs_write, fs_read, fs_ls, fs_grep). "
        "When a tool returns a large result you only partly need, fs_write it to a file and "
        "keep working from a short note, then fs_read/fs_grep just the part you need later. "
        "This keeps your context lean. The files vanish when the run ends."
    )
    if sandbox:
        system_parts.append(
            "# Sandboxed shell\n"
            "You have run_command, which executes a shell command inside an isolated "
            "sandbox (no network by default; writes confined to a scratch working "
            "directory). Use it for safe, self-contained commands; it returns "
            "stdout/stderr/exit_code. It requires operator approval before each run."
        )
    if subagents:
        _names = ", ".join(
            f"delegate_to_{re.sub(r'[^a-z0-9]+', '_', str(s.get('name', '')).lower()).strip('_')}"
            for s in subagents if isinstance(s, dict) and s.get("name")
        )
        if _names:
            system_parts.append(
                "# Delegation\n"
                f"You can delegate scoped subtasks to specialized subagents ({_names}). Each runs "
                "its own loop with a fresh context and returns only a concise summary. Use them "
                "for separable parts of a larger task so your own context stays focused on "
                "synthesis; they cannot delegate further."
            )

    if instructions:
        system_parts.append(f"\nAdditional instructions:\n{instructions}")

    if agent_mode == "multi":
        system_parts.append(
            "\nYou are coordinating multiple sub-tasks. "
            "Break down the task into logical steps and address each systematically."
        )

    system_prompt = "\n".join(system_parts)

    # ── Configurable persona + context document (operator-set, cache-stable) ──
    # Per-agent-node config wins; else the global setting. Empty by default, so
    # the prompt — and the accuracy eval — are byte-identical unless configured.
    try:
        from app.config import settings as _settings
        persona = str(
            agent_config.get("persona") or getattr(_settings, "agent_persona", "") or ""
        ).strip()
        context_doc = str(
            agent_config.get("contextDoc")
            or agent_config.get("context_doc")
            or getattr(_settings, "agent_context_doc", "")
            or ""
        ).strip()
        preamble: List[str] = []
        if persona:
            preamble.append(persona)
        if context_doc:
            preamble.append(f"## Context\n{context_doc}")
        if preamble:
            system_prompt = "\n\n".join(preamble) + "\n\n" + system_prompt
    except Exception:  # noqa: BLE001 — persona is additive; never break prompt build
        pass

    return system_prompt
