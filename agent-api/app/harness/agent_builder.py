"""Shared system-prompt composition for the in-house ReAct harness.

The agent construction (``build_agent`` / ``_finish_build_agent``) lives in
``app/harness/react_agent.py``. This module assembles the deterministic system
prompt — the Bedrock cachePoint prefix — so accuracy stays identical across
all agent builds.
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
    verify: bool = False,
    subagents: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Assemble the deterministic system prompt (the Bedrock cachePoint prefix).

    Assembles the accuracy-critical, cache-stable text used by the in-house ReAct
    harness. Never inject run-specific data here (see the CACHE CONTRACT note below).
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
    # Deterministic given the bound tools (same cache-safety basis as
    # has_db_tools) — drives the static "# Skills" section below. No skill
    # NAMES appear in the prompt; the per-turn listing (in the user query)
    # carries those, so the cachePoint prefix stays stable.
    has_skill_tool = any(getattr(t, "name", "") == "skill" for t in tools)

    # ── Resolve active capabilities (composable; see app.harness.capabilities) ──
    # The investigation trio is derived from the runtime flags so existing
    # workflows are unchanged; a profile may declare extra capability ids via
    # ``capabilities`` (Phase 1), which append after the builtins in registry order.
    from app.harness import capabilities as _caps
    _active_ids: List[str] = []
    if has_db_tools:
        _active_ids.append("database")
        _active_ids.append("rds_performance")
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
    _tool_bullets = [
        "# Using your tools",
        "- Prefer the most specific tool over a generic one, and fetch only what the question "
        "needs — never read whole files or dump an entire schema when a targeted lookup will do.",
        # The routing procedure must START with the skill check when the skill
        # tool is bound: a caveat appended after the domain rules was measured
        # (trajectory eval, traj-skill-locate) to lose — the model runs the
        # routing procedure to its concrete tool choice and never comes back.
        # Conditional on has_skill_tool: deterministic given the bound tools,
        # so the cachePoint prefix stays stable (same basis as "# Skills").
        "- Match each question to the tool whose domain fits what is being asked, and pick by "
        "what the question is ABOUT — not by habit. "
        + ("STEP ZERO of this routing, before choosing any domain tool: check the 'Skill map' "
           "in the user turn — when a listed skill matches the request, load it with the "
           "`skill` tool FIRST and let its runbook direct the tool choice. Route yourself only "
           "when no skill matches. "
           if has_skill_tool else "")
        + "A question about live data, records, counts, "
        "or current state is answered by querying the DATA SOURCE that holds it (e.g. a connected "
        "database) — not by reading the code that writes it. A question about how the system is "
        "BUILT or where logic lives is answered by the code tools. Logs, metrics, and alarms are "
        "answered by the observability tools. If the tool you need is not directly visible in your "
        "tool list, use search_tools to find it before falling back to a different domain's tools.",
        "- When you reach a useful conclusion or resolution worth reusing, call save_playbook to "
        "record it so future runs can benefit from it.",
    ]
    system_parts.append("\n".join(_tool_bullets))

    # ── Capability sections (registry-driven; stable order) ──────────────
    # Each active capability contributes its system-prompt section in registry
    # order (database → cloudwatch → code_analyzer → any profile extras). The
    # text lives in app.harness.capabilities.
    for _cap in active_caps:
        if _cap.section:
            system_parts.append(_cap.section)

    # ── Dispatch-time governance (3.3) — AGENT_POLICY.md rules sliced to
    # this run's bound tools; empty (no-op) when no rule matches or the
    # policy doc is absent. Deterministic given `tools` — same cache-prefix
    # dependency the role sentence/capability sections above already have.
    try:
        from app.core.governance import slice_rules
        _gov_text = slice_rules([getattr(t, "name", "") for t in tools])
        if _gov_text:
            system_parts.append(f"# Governance\n{_gov_text}")
    except Exception:  # noqa: BLE001 — governance must never break prompt build
        pass

    # ── Deep-agent capability instructions (profile-gated, config-stable) ──────
    if planning:
        system_parts.append(
            "# Planning\n"
            "You have write_todos and update_todo. For a genuinely multi-step task, call "
            "write_todos FIRST with the concrete steps, then work the list top to bottom, "
            "calling update_todo to mark each item in_progress then completed as the tool "
            "evidence supports it. Skip the plan for a single-lookup question."
        )
    system_parts.append(
        "# Scratch filesystem\n"
        "You have a session-scoped virtual filesystem (fs_write, fs_read, fs_ls, fs_grep). "
        "When a tool returns a large result you only partly need, fs_write it to a file and "
        "keep working from a short note, then fs_read/fs_grep just the part you need later. "
        "This keeps your context lean. The files vanish when the run ends."
    )
    if filesystem:
        from app.harness.metamemory import is_active as _metamemory_active, METAMEMORY_SECTION
        if _metamemory_active(filesystem):
            system_parts.append(METAMEMORY_SECTION)
    if sandbox:
        system_parts.append(
            "# Sandboxed shell\n"
            "You have run_command, which executes a shell command inside an isolated "
            "sandbox (no network by default; writes confined to a scratch working "
            "directory). Use it for safe, self-contained commands; it returns "
            "stdout/stderr/exit_code. It requires operator approval before each run."
        )
    if verify:
        system_parts.append(
            "# Verify your changes\n"
            "After you edit_file or create_file, call run_verify(repo) to run the project's "
            "configured checks (tests/typecheck/lint) against your edit. If it returns a non-zero "
            "exit_code, READ the stderr/stdout tail, fix the code with another edit, and run_verify "
            "again — repeat until it passes (exit_code 0) before you give your final answer. Don't "
            "claim a fix works until run_verify is green; if it stays red after a reasonable number "
            "of attempts, report the remaining failure and what you tried."
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

    # § Skills — two-stage disclosure. Static (no skill names), so cache-safe.
    # MUST be the LAST platform section: the capability sections name a specific
    # tool for a specific job ("prefer codegraph__find_symbol for a known
    # symbol"), which directly competes with "load the matching skill first",
    # and recency is what was measured to win — when Skills came before the
    # capability text the agent skipped the runbook for the obvious one-shot
    # tool, and when later default-on sections (scratch-fs/planning) slid in
    # after it the same regression came back. Keep every other section above
    # this one. (Static order: the cache prefix stays deterministic.)
    if has_skill_tool:
        system_parts.append(
            "# Skills\n"
            "- Some tasks have a matching skill — a proven, reusable runbook. The user turn "
            "carries a 'Skill map': the names of the skills you can load, and nothing more.\n"
            "- Call `search_skills` with what you are trying to do to see what those names "
            "mean and which fits; when a map name is obviously the match, load it directly.\n"
            "- When a skill matches the request, calling the `skill` tool to load it BEFORE any "
            "other work is a blocking requirement — then follow its steps, using your other tools "
            "as it directs. This OVERRIDES any tool preference above: if a skill matches, load it "
            "first even when a capability section names a tool that would answer directly.\n"
            "- Never mention a skill without invoking it. A user message starting with "
            "'/<skill-name>' is a request to invoke that skill."
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
