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

    for tool_config in tools_config:
        server_name = tool_config["name"]
        node_id = tool_config.get("node_id", server_name)

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
    logger.info(
        "ReactStrategy: built %d LangChain tools",
        len(langchain_tools),
        extra={"execution_id": execution_id},
    )
    return langchain_tools
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

    class ExecuteSkillInput(BaseModel):
        skill_name: str = PydanticField(
            description="Slug name of the skill to execute (as shown in the memory-context block)."
        )
        context: dict = PydanticField(
            default_factory=dict,
            description=(
                "Key-value pairs injected into the skill's args_template placeholders. "
                "For example: {\"log_group\": \"/aws/app\", \"threshold\": \"100\"}."
            ),
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

    async def _execute_skill(skill_name: str, context: dict) -> str:
        """Run a named skill's steps against the live MCP tool set."""
        try:
            from app.core.skills import skill_service
            exec_result = await skill_service.execute(skill_name, context=context)
            return exec_result.to_agent_text()
        except Exception as exc:
            return f"execute_skill failed: {exc}"

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
            coroutine=_execute_skill,
            name="execute_skill",
            description=(
                "Execute a named, pre-built remediation skill by running its ordered steps "
                "against the live MCP tool set. Use this when the memory-context block "
                "shows a matching 'Executable Skill' and you want to apply it directly. "
                "Pass any required placeholder values in the 'context' dict."
            ),
            args_schema=ExecuteSkillInput,
        ),
    ]
