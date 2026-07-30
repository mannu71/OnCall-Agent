"""MCP tool wiring and playbook tools for ReAct agents."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.infrastructure.persistence import mcp_config_repository
from app.workflow.mcp.mcp_langchain_adapter import build_langchain_tools

logger = logging.getLogger(__name__)

async def setup_tools(
    tools_config: List[Dict[str, Any]],
    mcp_manager: Any,
    execution_id: Optional[str] = None,
) -> List[Any]:
    """
    Connect to MCP servers and convert their tools to LangChain BaseTool instances.

    Args:
        tools_config: List of tool node configurations from the workflow.
        mcp_manager: Live MCPClientManager instance.
        execution_id: Execution ID for logging.

    Returns:
        List of LangChain-compatible tool objects.
    """
    from app.workflow.mcp.mcp_langchain_adapter import build_langchain_tools

    # Track server names already wired so a workflow with two tool nodes pointing
    # at the SAME MCP server doesn't silently connect/overwrite it twice (which
    # yields duplicate tool names the agent can't disambiguate). First wins; the
    # duplicate is logged and skipped.
    _seen_servers: set = set()

    for tool_config in tools_config:
        server_name = tool_config["name"]
        node_id = tool_config.get("node_id", server_name)

        if server_name in _seen_servers:
            logger.warning(
                "ReactStrategy: duplicate MCP server '%s' in workflow — already "
                "wired, skipping the duplicate tool node (node_id=%s)",
                server_name, node_id,
            )
            continue
        _seen_servers.add(server_name)

        # Resolve command from DB if not inline
        if not tool_config.get("command"):
            db_config = await mcp_config_repository.get_by_name(server_name)
            if db_config:
                tool_config["command"] = db_config.get("command", "")
                tool_config["args"] = db_config.get("args", [])
                tool_config["env"] = db_config.get("env", {})
                logger.info(
                    "ReactStrategy: loaded MCP server '%s' from DB",
                    server_name,
                    extra={"execution_id": execution_id},
                )
            else:
                logger.warning(
                    "ReactStrategy: MCP server '%s' not found in DB, skipping",
                    server_name,
                )
                continue

        # Connect (skip if already connected from a previous tool node)
        if not mcp_manager.is_connected(node_id):
            mcp_config = {
                "command": tool_config["command"],
                "args": tool_config["args"],
                "env": tool_config["env"],
            }
            connected = await mcp_manager.connect_server(node_id, mcp_config)
            if not connected:
                logger.warning(
                    "ReactStrategy: failed to connect MCP server '%s', skipping",
                    server_name,
                )
                continue

    # Convert all live MCP connections to LangChain tools
    langchain_tools = build_langchain_tools(mcp_manager)

    # Final safety net: collapse any tools that ended up sharing a name (e.g.
    # two different servers exposing the same tool). A duplicate name makes the
    # model's tool choice ambiguous, so keep the first and drop the rest.
    _by_name: Dict[str, Any] = {}
    for t in langchain_tools:
        tname = getattr(t, "name", None) or ""
        if tname in _by_name:
            logger.warning(
                "ReactStrategy: duplicate tool name '%s' from MCP — keeping first, "
                "dropping duplicate", tname,
            )
            continue
        _by_name[tname] = t
    deduped = list(_by_name.values())

    logger.info(
        "ReactStrategy: built %d LangChain tools (%d after de-dup)",
        len(langchain_tools), len(deduped),
        extra={"execution_id": execution_id},
    )
    return deduped
def build_playbook_tools(*, auto_learn: bool = True) -> List[Any]:
    """Build LangChain StructuredTool instances that let the agent write
    and update investigation playbooks in the knowledge base.

    These are appended to the MCP tools list before the ReAct agent is
    constructed so the agent can call them like any other tool.

    ``auto_learn`` mirrors the Agent node's Auto-learn toggle and gates the two
    KB *writers* (``save_playbook`` / ``patch_playbook``). They used to bind
    unconditionally, so an agent on a workflow with auto-learn OFF still wrote
    playbooks — and, since ``save_playbook`` is the first entry in
    ``DEFAULT_ASK_PATTERNS``, stalled every unattended scheduled run for the
    full 300s approval timeout. ``pin_fact`` is deliberately NOT gated here: it
    belongs to the pinned-facts memory tier and has its own
    ``pinned_facts_enabled`` gate.

    The default stays ``True`` so the registry's catalog scan
    (``registry_loader._register_introspected_builtins``) still enumerates all
    three tools.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    class SavePlaybookInput(BaseModel):
        title: str = PydanticField(description="Short title identifying the issue type (max 120 chars).")
        symptoms: List[str] = PydanticField(description="List of symptoms or error patterns observed.")
        solution: str = PydanticField(description="Step-by-step resolution or investigation procedure.")
        category: str = PydanticField(description="Category, e.g. 'database', 'auth', 'network', 'agent_discovered'.")

    class PatchPlaybookInput(BaseModel):
        slug: str = PydanticField(
            description="The slug of the known issue to update (returned by save_playbook)."
        )
        new_solution: str = PydanticField(description="Replacement resolution text.")

    class PinFactInput(BaseModel):
        fact: str = PydanticField(
            description=(
                "A durable, high-value fact worth recalling in EVERY future "
                "investigation (e.g. an environment quirk, an escalation owner, a "
                "confirmed invariant). Keep it one concise sentence."
            )
        )
        repo: Optional[str] = PydanticField(
            default=None,
            description="Repo name to scope the fact to; omit to pin it globally.",
        )

    async def _save_playbook(title: str, symptoms: List[str], solution: str, category: str) -> str:
        try:
            from pathlib import Path
            from app.core.knowledge import get_default_bundle, index_concept_into_kb
            from app.core.improvement.auto_learn import _derive_tags

            symptoms = [s for s in (symptoms or []) if str(s).strip()]
            parts: List[str] = []
            if symptoms:
                parts.append("# Symptoms\n" + "\n".join(f"- {s}" for s in symptoms))
            parts.append("# Resolution\n" + (solution or "").strip())
            body = "\n\n".join(parts)
            tags = _derive_tags(f"{title}\n{solution}", symptoms,
                                extra=[category] if category else [])
            bundle = get_default_bundle()
            res = bundle.write_concept(
                section="known-issues",
                title=title[:120],
                body=body,
                description=(solution or title)[:500],
                tags=tags,
                source="agent",
            )
            await index_concept_into_kb(bundle, Path(res["path"]), bank="kb")
            action = "created" if res["created"] else "updated"
            return f"Playbook {action}: slug='{res['slug']}' title='{title[:120]}'"
        except Exception as exc:
            return f"save_playbook failed: {exc}"

    async def _patch_playbook(slug: str, new_solution: str) -> str:
        try:
            import re
            from pathlib import Path
            from app.core.knowledge import get_default_bundle, index_concept_into_kb

            bundle = get_default_bundle()
            path = bundle.concept_path("known-issues", slug)
            doc = bundle.read_concept(path)
            if not doc:
                return f"patch_playbook error: no known issue with slug '{slug}'"
            fm = doc.get("frontmatter") or {}
            body = doc.get("body") or ""
            new_res = "# Resolution\n" + (new_solution or "").strip()
            # Replace an existing Resolution section, else append one.
            if re.search(r"(?m)^#\s+Resolution\b", body):
                body = re.sub(r"(?ms)^#\s+Resolution\b.*?(?=^#\s|\Z)", new_res + "\n\n", body).rstrip()
            else:
                body = (body.rstrip() + "\n\n" + new_res).strip()
            res = bundle.write_concept(
                section="known-issues",
                title=str(fm.get("title") or slug),
                body=body,
                description=str(fm.get("description") or ""),
                tags=fm.get("tags") or [],
                source="agent",
                confidence=fm.get("confidence"),
            )
            await index_concept_into_kb(bundle, Path(res["path"]), bank="kb")
            return f"Playbook patched: slug='{res['slug']}'"
        except Exception as exc:
            return f"patch_playbook failed: {exc}"

    async def _pin_fact(fact: str, repo: Optional[str] = None) -> str:
        try:
            from app.config import settings
            if not getattr(settings, "pinned_facts_enabled", True):
                return "pin_fact is disabled in this environment."
            # A pinned fact is injected into EVERY future turn, so reject trivial
            # or empty content outright — it would be permanent context noise.
            _clean = (fact or "").strip()
            if len(_clean) < 10:
                return (
                    "pin_fact rejected: a pinned fact must be a substantive, "
                    "durable statement (too short/empty)."
                )
            from app.services.semantic_memory import semantic_memory
            row_id = await semantic_memory.pin_fact(_clean, repo=repo)
            if row_id is None:
                return "pin_fact: nothing stored (empty fact)."
            scope = f"repo '{repo}'" if repo else "global"
            return f"Pinned fact (id={row_id}, {scope}); it will be injected every turn."
        except Exception as exc:
            return f"pin_fact failed: {exc}"

    pin_fact_tool = StructuredTool.from_function(
        coroutine=_pin_fact,
        name="pin_fact",
        description=(
            "Pin a durable, high-value fact so it is injected into EVERY future "
            "investigation's context (the always-on memory tier). Use sparingly "
            "for facts that stay true across runs — not run-specific findings "
            "(use save_playbook for those)."
        ),
        args_schema=PinFactInput,
    )
    if not auto_learn:
        return [pin_fact_tool]

    return [
        StructuredTool.from_function(
            coroutine=_save_playbook,
            name="save_playbook",
            description=(
                "Save or update an investigation playbook for a recurring task or issue type. "
                "Call this when you have reached findings and recommendations worth reusing "
                "(for example the root cause and resolution of an issue)."
            ),
            args_schema=SavePlaybookInput,
        ),
        StructuredTool.from_function(
            coroutine=_patch_playbook,
            name="patch_playbook",
            description=(
                "Update the resolution of an existing playbook by its slug (from save_playbook). "
                "Use this when you have found a better resolution than what is already recorded."
            ),
            args_schema=PatchPlaybookInput,
        ),
        pin_fact_tool,
    ]
