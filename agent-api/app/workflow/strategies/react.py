"""
ReAct Strategy for AI Agent Workflows

Executes AI-driven agentic workflows using LangGraph and the ReAct pattern.
This strategy is used for investigative queries where the AI decides which
tools to call and in what order.

Architecture:
    User Query
        ↓
    ReactStrategy.execute()
        ↓
    _setup_tools()       → MCPClientManager → MCPLangChainAdapter → List[BaseTool]
    _build_llm()         → ChatOpenAI | ChatBedrockConverse
    _build_agent()       → LangGraph create_react_agent StateGraph
    _execute_agent()     → agent.ainvoke() → ReAct loop (Thought→Action→Observation)
        ↓
    Final Answer
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.workflow.strategies.base import BaseStrategy
from app.repositories.db_repository import db_repository

logger = logging.getLogger(__name__)


class ReactStrategy(BaseStrategy):
    """
    Strategy for executing AI agent workflows using LangGraph ReAct pattern.

    Use cases:
    - "Why did profiles fail today?"
    - "What's causing high CPU usage?"
    - "Investigate database connection issues"
    """

    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        """
        Check if workflow is a ReAct agent workflow.

        A workflow is considered a ReAct workflow if it has:
        - An 'agent' node (defines agent behaviour and instructions)
        - An 'llm' node  (provides AI model configuration)

        Args:
            workflow: The workflow definition

        Returns:
            True if this is a ReAct workflow
        """
        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return False

        has_agent_node = any(node.get("type") == "agent" for node in nodes)
        has_llm_node = any(node.get("type") == "llm" for node in nodes)

        return has_agent_node and has_llm_node

    # ------------------------------------------------------------------
    # Main execute entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        workflow: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute ReAct agent workflow.

        Args:
            workflow: The workflow definition
            context: Execution context with user_query, mcp_manager, etc.

        Returns:
            Execution result including final_answer and message trace.
        """
        execution_id = context.get("execution_id")
        logger_instance = context.get("logger", logger)
        user_query = context.get("user_query") or context.get("inputs", {}).get("user_query", "")
        mcp_manager = context.get("mcp_manager")

        logger_instance.info(
            "ReactStrategy: Starting execution",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow.get("id"),
                "user_query_preview": (user_query or "")[:100],
            },
        )

        if not user_query:
            # Fall back to agent node instructions as the query
            agent_config = self._extract_agent_config(workflow)
            user_query = agent_config.get("instructions") or agent_config.get("description") or ""

        if not user_query:
            raise ValueError(
                "ReactStrategy requires a user_query in the execution context "
                "or 'instructions' set on the agent node."
            )

        try:
            # 1. Extract configuration from workflow nodes
            agent_config = self._extract_agent_config(workflow)
            llm_config = await self._resolve_llm_config(workflow)
            tools_config = self._extract_tools_config(workflow)

            # 2. Connect to MCP servers and convert to LangChain tools
            tools = await self._setup_tools(tools_config, mcp_manager, execution_id)

            # 3. Build the LLM instance from config
            llm = self._build_llm(llm_config)

            # 4. Build the LangGraph ReAct agent
            agent = self._build_agent(llm, tools, agent_config)

            # 5. Execute the agent with user query
            result = await self._execute_agent(agent, user_query, logger_instance)

            # 6. Cleanup MCP connections gracefully
            if mcp_manager:
                await mcp_manager.disconnect_all()

            logger_instance.info(
                "ReactStrategy: Execution completed",
                extra={
                    "execution_id": execution_id,
                    "message_count": len(result.get("messages", [])),
                },
            )

            return {
                "type": "react",
                "user_query": user_query,
                "final_answer": result.get("final_answer"),
                "messages": result.get("messages", []),
                "message_count": len(result.get("messages", [])),
                "tool_calls": result.get("tool_calls", []),
                "model": llm_config.get("model", "unknown"),
                "provider": llm_config.get("provider", "unknown"),
            }

        except Exception as error:
            logger_instance.error(
                "ReactStrategy: Execution failed",
                extra={"execution_id": execution_id, "error": str(error)},
                exc_info=True,
            )
            # Best-effort cleanup
            if mcp_manager:
                try:
                    await mcp_manager.disconnect_all()
                except Exception:
                    pass
            raise

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workflow(self, workflow: Dict[str, Any]) -> bool:
        """
        Validate ReAct workflow structure.

        Args:
            workflow: The workflow definition

        Returns:
            True if valid

        Raises:
            ValueError: If workflow is invalid
        """
        super().validate_workflow(workflow)

        nodes = workflow.get("nodes", [])
        agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
        llm_node = next((n for n in nodes if n.get("type") == "llm"), None)

        if not agent_node:
            raise ValueError("ReAct workflow must have an agent node")

        if not llm_node:
            raise ValueError("ReAct workflow must have an LLM node")

        # LLM node must reference a valid config or have inline config
        llm_data = llm_node.get("data", {})
        if not llm_data.get("model") and not llm_data.get("configName") and not llm_data.get("llmConfigId"):
            raise ValueError("LLM node must specify a model or reference an LLM config")

        return True

    # ------------------------------------------------------------------
    # Config extraction helpers
    # ------------------------------------------------------------------

    def _extract_agent_config(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Extract agent node configuration from workflow."""
        nodes = workflow.get("nodes", [])
        agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
        return agent_node.get("data", {}) if agent_node else {}

    async def _resolve_llm_config(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolve LLM configuration, preferring DB-stored configs over inline node data.

        Priority order:
        1. Inline config in LLM node data (model + provider set directly)
        2. Named config reference (configName / llmConfigId) → lookup in DB
        3. First available config in DB
        4. Default fallback (Bedrock Claude)
        """
        nodes = workflow.get("nodes", [])
        llm_node = next((n for n in nodes if n.get("type") == "llm"), None)
        llm_data = llm_node.get("data", {}) if llm_node else {}

        # Check for inline config
        if llm_data.get("model") and llm_data.get("provider"):
            return {
                "provider": llm_data["provider"],
                "model": llm_data["model"],
                "temperature": llm_data.get("temperature", 0.1),
                "max_tokens": llm_data.get("maxTokens") or llm_data.get("max_tokens") or 4096,
                "region": llm_data.get("region", "us-east-1"),
                "base_url": llm_data.get("baseUrl") or llm_data.get("base_url"),
                "api_key": llm_data.get("apiKey") or llm_data.get("api_key"),
            }

        # Try named config from DB
        config_name = llm_data.get("configName") or llm_data.get("llmConfigId")
        if config_name:
            try:
                db_configs = await db_repository.list_llm_configs()
                if config_name in db_configs:
                    cfg = db_configs[config_name]
                    return {
                        "provider": cfg.get("provider", "bedrock"),
                        "model": cfg.get("model", "anthropic.claude-3-sonnet-20240229-v1:0"),
                        "temperature": cfg.get("temperature", 0.1),
                        "max_tokens": cfg.get("max_tokens", 4096),
                        "region": cfg.get("region", "us-east-1"),
                        "base_url": cfg.get("base_url"),
                        "api_key": None,
                    }
            except Exception as e:
                logger.warning("Could not load LLM config '%s' from DB: %s", config_name, e)

        # Fall back to first available DB config
        try:
            db_configs = await db_repository.list_llm_configs()
            if db_configs:
                first_name, cfg = next(iter(db_configs.items()))
                logger.info("ReactStrategy: using first available LLM config '%s'", first_name)
                return {
                    "provider": cfg.get("provider", "bedrock"),
                    "model": cfg.get("model", "anthropic.claude-3-sonnet-20240229-v1:0"),
                    "temperature": cfg.get("temperature", 0.1),
                    "max_tokens": cfg.get("max_tokens", 4096),
                    "region": cfg.get("region", "us-east-1"),
                    "base_url": cfg.get("base_url"),
                    "api_key": None,
                }
        except Exception as e:
            logger.warning("Could not load LLM configs from DB: %s", e)

        # Hard fallback — AWS Bedrock Claude 3 Sonnet
        logger.warning("ReactStrategy: using hard-coded Bedrock fallback LLM config")
        return {
            "provider": "bedrock",
            "model": "anthropic.claude-3-sonnet-20240229-v1:0",
            "temperature": 0.1,
            "max_tokens": 4096,
            "region": "us-east-1",
            "base_url": None,
            "api_key": None,
        }

    def _extract_tools_config(self, workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract tool node configurations from workflow."""
        nodes = workflow.get("nodes", [])
        tool_nodes = [n for n in nodes if n.get("type") == "tool"]

        tools = []
        for tool_node in tool_nodes:
            tool_data = tool_node.get("data", {})
            server_name = tool_data.get("serverName", "")
            node_id = tool_node.get("id", server_name)

            if tool_data.get("command"):
                tools.append({
                    "node_id": node_id,
                    "name": server_name,
                    "command": tool_data.get("command", ""),
                    "args": tool_data.get("args", []),
                    "env": tool_data.get("env", {}),
                })
            else:
                # Will be resolved from DB below in _setup_tools
                tools.append({
                    "node_id": node_id,
                    "name": server_name,
                    "command": None,
                    "args": [],
                    "env": {},
                })

        return tools

    # ------------------------------------------------------------------
    # Tool setup (MCP → LangChain)
    # ------------------------------------------------------------------

    async def _setup_tools(
        self,
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
                db_config = await db_repository.get_mcp_server_by_name(server_name)
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

    # ------------------------------------------------------------------
    # LLM factory
    # ------------------------------------------------------------------

    def _build_llm(self, llm_config: Dict[str, Any]) -> Any:
        """
        Instantiate a LangChain LLM from the resolved configuration.

        Supports:
        - provider="openai"   → ChatOpenAI
        - provider="bedrock"  → ChatBedrockConverse
        - provider="azure"    → AzureChatOpenAI

        Args:
            llm_config: Resolved LLM configuration dict.

        Returns:
            LangChain chat model instance (BaseChatModel).

        Raises:
            ValueError: If the provider is not supported.
        """
        provider = (llm_config.get("provider") or "bedrock").lower()
        model = llm_config.get("model", "")
        temperature = float(llm_config.get("temperature") or 0.1)
        max_tokens = int(llm_config.get("max_tokens") or 4096)
        region = llm_config.get("region") or "us-east-1"

        if provider == "openai":
            from langchain_openai import ChatOpenAI
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            api_key = llm_config.get("api_key")
            if api_key:
                kwargs["api_key"] = api_key
            base_url = llm_config.get("base_url")
            if base_url:
                kwargs["base_url"] = base_url
            logger.info("ReactStrategy: using ChatOpenAI model=%s", model)
            return ChatOpenAI(**kwargs)

        if provider in ("bedrock", "aws", "aws_bedrock"):
            from langchain_aws import ChatBedrockConverse
            logger.info("ReactStrategy: using ChatBedrockConverse model=%s region=%s", model, region)
            return ChatBedrockConverse(
                model=model,
                region_name=region,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if provider in ("azure", "azure_openai"):
            from langchain_openai import AzureChatOpenAI
            logger.info("ReactStrategy: using AzureChatOpenAI model=%s", model)
            return AzureChatOpenAI(
                azure_deployment=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            f"Supported providers: openai, bedrock, azure."
        )

    # ------------------------------------------------------------------
    # Agent construction (LangGraph)
    # ------------------------------------------------------------------

    def _build_agent(
        self,
        llm: Any,
        tools: List[Any],
        agent_config: Dict[str, Any],
    ) -> Any:
        """
        Build a LangGraph ReAct agent graph.

        Uses langgraph.prebuilt.create_react_agent which implements the full
        Thought → Action → Observation loop as a compiled StateGraph.

        Args:
            llm: LangChain chat model instance.
            tools: List of LangChain BaseTool instances.
            agent_config: Agent node data with optional system prompt / instructions.

        Returns:
            Compiled LangGraph agent (CompiledGraph).
        """
        from langgraph.prebuilt import create_react_agent

        # Build system prompt from agent instructions if provided
        instructions = agent_config.get("instructions", "")
        agent_mode = agent_config.get("agentMode", "single")

        system_parts = [
            "You are an expert on-call engineer assistant for KYC Protect.",
            "You have access to database tools and can run SQL queries to investigate issues.",
            "Always reason step by step and use the available tools to find accurate answers.",
            "When querying databases, prefer targeted queries over full table scans.",
            "Present your findings clearly with specific data from the query results.",
        ]

        if instructions:
            system_parts.append(f"\nAdditional instructions:\n{instructions}")

        if agent_mode == "multi":
            system_parts.append(
                "\nYou are coordinating multiple sub-tasks. "
                "Break down the investigation into logical steps and address each systematically."
            )

        system_prompt = "\n".join(system_parts)

        logger.info(
            "ReactStrategy: building LangGraph ReAct agent with %d tool(s), mode=%s",
            len(tools),
            agent_mode,
        )

        agent = create_react_agent(
            model=llm,
            tools=tools,
            state_modifier=system_prompt,
        )
        return agent

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    async def _execute_agent(
        self,
        agent: Any,
        user_query: str,
        logger_instance: Any,
    ) -> Dict[str, Any]:
        """
        Execute the LangGraph ReAct agent with the user's query.

        Invokes the compiled LangGraph StateGraph asynchronously and extracts
        the final answer plus a structured trace of all messages.

        Args:
            agent: Compiled LangGraph agent graph.
            user_query: The user's question / investigation request.
            logger_instance: Logger for execution-scoped logging.

        Returns:
            Dict with:
              - final_answer: str — the last AI response
              - messages: list — all messages in the conversation
              - tool_calls: list — summary of tools invoked
        """
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

        logger_instance.info("ReactStrategy: invoking agent with query: %.100s", user_query)

        input_state = {"messages": [HumanMessage(content=user_query)]}

        try:
            # Run with a 5-minute timeout to match the WorkflowEngine default
            result_state = await asyncio.wait_for(
                agent.ainvoke(input_state),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("ReAct agent timed out after 300 seconds.")

        # Extract and serialize all messages
        messages = result_state.get("messages", [])
        serialized_messages = []
        tool_calls_summary = []
        final_answer = ""

        for msg in messages:
            if isinstance(msg, HumanMessage):
                serialized_messages.append({"role": "user", "content": str(msg.content)})

            elif isinstance(msg, AIMessage):
                content = str(msg.content) if msg.content else ""
                entry: Dict[str, Any] = {"role": "assistant", "content": content}

                # Capture tool call intentions
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.get("id"),
                            "name": tc.get("name"),
                            "args": tc.get("args", {}),
                        }
                        for tc in msg.tool_calls
                    ]
                    for tc in msg.tool_calls:
                        tool_calls_summary.append(
                            {"tool": tc.get("name"), "args_keys": list((tc.get("args") or {}).keys())}
                        )

                serialized_messages.append(entry)
                # Track last AI message as potential final answer
                if content:
                    final_answer = content

            elif isinstance(msg, ToolMessage):
                serialized_messages.append({
                    "role": "tool",
                    "tool_call_id": getattr(msg, "tool_call_id", ""),
                    "content": str(msg.content)[:2000],  # Cap tool output in trace
                })

            elif isinstance(msg, SystemMessage):
                pass  # Don't include system prompt in output trace

        logger_instance.info(
            "ReactStrategy: agent completed with %d messages, %d tool calls",
            len(serialized_messages),
            len(tool_calls_summary),
        )

        return {
            "final_answer": final_answer,
            "messages": serialized_messages,
            "tool_calls": tool_calls_summary,
        }
