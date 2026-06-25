"""deepagents harness path (official LangChain stack).

Builds an agent with the official ``deepagents`` library instead of the in-house
ReAct wrapper, while staying faithful to the legacy path on the things that drive
accuracy + cost + governance:
  * the SAME composed system prompt (``compose_system_prompt``) — the cachePoint prefix,
  * the SAME governed tool set (playbook tools + policy gating),
  * the SAME Bedrock ``cache_control`` binding (survives deepagents' internal bind_tools),
  * the shared durable checkpointer/store.

Gated by ``settings.harness == "deepagents"``. Later phases move planning/filesystem/
subagents/permissions/skills/memory onto deepagents' own middleware and drop the
overlapping legacy sections.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


_CACHING_SUBCLASSES: dict = {}


def _with_bedrock_caching(llm: Any, ttl: str = "1h") -> Any:
    """Return *llm* as a model whose ``bind_tools`` re-applies Bedrock cache_control.

    Reassigns the instance's class to a (cached) subclass that overrides
    ``bind_tools`` to chain ``.bind(cache_control=...)``. Keeps it a real
    BaseChatModel instance so deepagents' ``resolve_model`` accepts it, while
    ensuring native Bedrock cachePoint markers are emitted on every model call.
    """
    base = type(llm)
    sub = _CACHING_SUBCLASSES.get(base)
    if sub is None:
        def _bind_tools(self, *args, _ttl=ttl, **kwargs):
            return base.bind_tools(self, *args, **kwargs).bind(cache_control={"ttl": _ttl})
        sub = type(f"Caching{base.__name__}", (base,), {"bind_tools": _bind_tools})
        _CACHING_SUBCLASSES[base] = sub
    llm.__class__ = sub
    return llm


def build_deep_agent(
    spec: Any,
    llm: Any,
    tools: List[Any],
    checkpointer: Any = None,
    store: Any = None,
    execution_port: Any = None,
) -> Any:
    """Build a deepagents agent from an :class:`AgentSpec` + runtime objects.

    Mirrors ``build_agent_from_spec`` so the executor drives either harness through
    the same ``execute_agent`` loop (both are LangGraph compiled graphs).
    """
    from deepagents import create_deep_agent
    from app.harness.runtime import get_saver, get_store
    from app.workflow.strategies.react.agent_builder import compose_system_prompt
    from app.workflow.strategies.react.tool_setup import build_playbook_tools

    cfg = getattr(spec, "agent_config", None) or {}

    # Same accuracy-critical, cache-stable system prompt as the legacy path.
    system_prompt = compose_system_prompt(
        tools=tools,
        agent_config=cfg,
        has_cloudwatch=getattr(spec, "has_cloudwatch", False),
        has_code_analyzer=getattr(spec, "has_code_analyzer", False),
        capabilities=getattr(spec, "capabilities", None),
        role_prompt=getattr(spec, "role_prompt", None),
        planning=getattr(spec, "planning", False),
        filesystem=getattr(spec, "filesystem", False),
        sandbox=getattr(spec, "sandbox", False),
        subagents=getattr(spec, "subagents", None),
    )

    # Same governed tool set: agent-writable playbook tools + declarative policy
    # gating (ask/deny + output cap + cost/loop budgets). Governance is NOT part
    # of deepagents, so we keep applying our policy engine here.
    all_tools = list(tools or []) + build_playbook_tools()
    try:
        from app.core import policy as _policy
        resolved = _policy.resolve_with_platform_defaults(getattr(spec, "policies", None))
        _policy.set_current(resolved)
        all_tools = _policy.apply_to_tools(
            all_tools, resolved,
            mode=getattr(spec, "permission_mode", "default"),
            execution_id=getattr(spec, "session_id", None),
            execution_port=execution_port,
        )
    except Exception as _pol_err:  # noqa: BLE001 — never break a run on governance
        logger.warning("deepagents: policy engine skipped (%s)", _pol_err)

    # Bedrock native prompt caching (cachePoint), deepagents-compatible.
    # deepagents.resolve_model returns a BaseChatModel INSTANCE unchanged but
    # rejects a .bind(...) RunnableBinding (its apply_provider_profile does
    # spec.count() → AttributeError → silent legacy fallback). So instead of
    # wrapping, we keep a real model instance whose bind_tools() re-applies
    # cache_control — deepagents binds tools AFTER resolve_model, so the kwarg
    # lands on every model call and cachePoint markers are inserted as before.
    model: Any = llm
    if "Bedrock" in type(llm).__name__:
        try:
            model = _with_bedrock_caching(llm)
        except Exception as _ce:  # noqa: BLE001 — caching is best-effort
            logger.warning("deepagents: Bedrock caching not applied (%s)", _ce)
            model = llm
    # NOTE on compaction: create_deep_agent manages its own summarization middleware
    # (passing our own SummarizationMiddleware triggers a "duplicate middleware"
    # error), so mid-loop compaction is covered by deepagents. The legacy
    # pre-invocation compaction in execute_agent still runs too (harness-agnostic).
    agent = create_deep_agent(
        model=model,
        tools=all_tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer if checkpointer is not None else get_saver(),
        store=store if store is not None else get_store(),
    )
    logger.info(
        "deepagents: built agent (tools=%d, system_prompt=%d chars)",
        len(all_tools), len(system_prompt),
    )
    return agent
