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
def build_playbook_tools() -> List[Any]:
    """Build LangChain StructuredTool instances that let the agent write
    and update investigation playbooks in the knowledge base.

    These are appended to the MCP tools list before the ReAct agent is
    constructed so the agent can call them like any other tool.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField

    class SavePlaybookInput(BaseModel):
        title: str = PydanticField(description="Short title identifying the issue type (max 120 chars).")
        symptoms: List[str] = PydanticField(description="List of symptoms or error patterns observed.")
        solution: str = PydanticField(description="Step-by-step resolution or investigation procedure.")
        category: str = PydanticField(description="Category, e.g. 'database', 'auth', 'network', 'agent_discovered'.")

    class PatchPlaybookInput(BaseModel):
        issue_id: int = PydanticField(description="The integer ID of the known issue to update.")
        new_solution: str = PydanticField(description="Replacement solution text.")

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
            from app.services.knowledge_base import knowledge_base as _kb
            result = await _kb.upsert_playbook(
                title=title[:120],
                symptoms=symptoms,
                solution=solution,
                category=category,
                source="agent",
            )
            action = result.get("action", "saved")
            return f"Playbook {action}: id={result.get('id')} title='{result.get('title')}'"
        except Exception as exc:
            return f"save_playbook failed: {exc}"

    async def _patch_playbook(issue_id: int, new_solution: str) -> str:
        try:
            from app.services.knowledge_base import knowledge_base as _kb
            result = await _kb.patch_playbook_solution(
                issue_id=issue_id,
                new_solution=new_solution,
                source="agent",
            )
            if "error" in result:
                return f"patch_playbook error: {result['error']}"
            return f"Playbook patched: id={result.get('id')} title='{result.get('title')}'"
        except Exception as exc:
            return f"patch_playbook failed: {exc}"

    async def _pin_fact(fact: str, repo: Optional[str] = None) -> str:
        try:
            from app.config import settings
            if not getattr(settings, "pinned_facts_enabled", True):
                return "pin_fact is disabled in this environment."
            from app.services.semantic_memory import semantic_memory
            row_id = await semantic_memory.pin_fact(fact, repo=repo)
            if row_id is None:
                return "pin_fact: nothing stored (empty fact)."
            scope = f"repo '{repo}'" if repo else "global"
            return f"Pinned fact (id={row_id}, {scope}); it will be injected every turn."
        except Exception as exc:
            return f"pin_fact failed: {exc}"

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
                "Update the solution of an existing playbook by its integer ID. "
                "Use this when you have found a better resolution than what is already recorded."
            ),
            args_schema=PatchPlaybookInput,
        ),
        StructuredTool.from_function(
            coroutine=_pin_fact,
            name="pin_fact",
            description=(
                "Pin a durable, high-value fact so it is injected into EVERY future "
                "investigation's context (the always-on memory tier). Use sparingly "
                "for facts that stay true across runs — not run-specific findings "
                "(use save_playbook for those)."
            ),
            args_schema=PinFactInput,
        ),
    ]
