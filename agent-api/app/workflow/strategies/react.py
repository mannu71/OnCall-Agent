"""
ReAct Strategy for AI Agent Workflows

Executes AI-driven agentic workflows using LangChain and the ReAct pattern.
This strategy is used for investigative queries where the AI decides which
tools to call and in what order.
"""

from typing import Dict, Any, List, Optional
import logging

from app.workflow.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class ReactStrategy(BaseStrategy):
    """
    Strategy for executing AI agent workflows using LangChain ReAct pattern.
    
    Use cases:
    - "Why did profiles fail today?"
    - "What's causing high CPU usage?"
    - "Investigate database connection issues"
    """
    
    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        """
        Check if workflow is a ReAct agent workflow.
        
        A workflow is considered a ReAct workflow if it has:
        - An 'agent' node (defines agent behavior)
        - An 'llm' node (provides AI capabilities)
        
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
    
    async def execute(
        self,
        workflow: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute ReAct agent workflow.
        
        Args:
            workflow: The workflow definition
            context: Execution context with user_query, mcp_manager, etc.
            
        Returns:
            Execution result
        """
        execution_id = context.get("execution_id")
        logger_instance = context.get("logger", logger)
        user_query = context.get("user_query")
        mcp_manager = context.get("mcp_manager")
        
        logger_instance.info(
            "ReactStrategy: Starting execution",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow.get("id"),
                "user_query": user_query[:100] if user_query else None
            }
        )
        
        # Validate that we have a user query
        if not user_query:
            raise ValueError("ReactStrategy requires a user_query in the context")
        
        try:
            # Extract agent and LLM configuration
            agent_config = self._extract_agent_config(workflow)
            llm_config = self._extract_llm_config(workflow)
            tools_config = self._extract_tools_config(workflow)
            
            # Connect to MCP servers and get tools
            tools = await self._setup_tools(tools_config, mcp_manager)
            
            # Build LangChain agent
            agent = await self._build_agent(agent_config, llm_config, tools)
            
            # Execute the agent with user query
            result = await self._execute_agent(agent, user_query, logger_instance)
            
            # Cleanup MCP connections
            await mcp_manager.disconnect_all()
            
            logger_instance.info(
                "ReactStrategy: Execution completed",
                extra={
                    "execution_id": execution_id,
                    "message_count": len(result.get("messages", []))
                }
            )
            
            return {
                "type": "react",
                "user_query": user_query,
                "final_answer": result.get("final_answer"),
                "messages": result.get("messages", []),
                "message_count": len(result.get("messages", [])),
                "correlation_id": result.get("correlation_id")
            }
            
        except Exception as error:
            logger_instance.error(
                "ReactStrategy: Execution failed",
                extra={
                    "execution_id": execution_id,
                    "error": str(error)
                },
                exc_info=True
            )
            raise
    
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
        
        # Validate agent node has instructions
        if not agent_node.get("data", {}).get("instructions"):
            raise ValueError("Agent node must have instructions")
        
        # Validate LLM node has required config
        llm_data = llm_node.get("data", {})
        if not llm_data.get("model"):
            raise ValueError("LLM node must specify a model")
        
        if not llm_data.get("provider"):
            raise ValueError("LLM node must specify a provider")
        
        return True
    
    def _extract_agent_config(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Extract agent configuration from workflow."""
        nodes = workflow.get("nodes", [])
        agent_node = next((n for n in nodes if n.get("type") == "agent"), None)
        
        return agent_node.get("data", {}) if agent_node else {}
    
    def _extract_llm_config(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Extract LLM configuration from workflow."""
        nodes = workflow.get("nodes", [])
        llm_node = next((n for n in nodes if n.get("type") == "llm"), None)
        
        return llm_node.get("data", {}) if llm_node else {}
    
    def _extract_tools_config(self, workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract tools configuration from workflow."""
        nodes = workflow.get("nodes", [])
        tool_nodes = [n for n in nodes if n.get("type") == "tool"]
        
        tools = []
        for tool_node in tool_nodes:
            tool_data = tool_node.get("data", {})
            tools.append({
                "name": tool_data.get("serverName", ""),
                "command": tool_data.get("command", ""),
                "args": tool_data.get("args", []),
                "env": tool_data.get("env", {})
            })
        
        return tools
    
    async def _setup_tools(
        self,
        tools_config: List[Dict[str, Any]],
        mcp_manager: Any
    ) -> List[Any]:
        """
        Setup MCP tools for LangChain agent.
        
        Args:
            tools_config: List of tool configurations
            mcp_manager: MCP client manager
            
        Returns:
            List of LangChain tools
        """
        # Connect to MCP servers
        for tool_config in tools_config:
            if not mcp_manager.is_connected(tool_config["name"]):
                await mcp_manager.connect(tool_config)
        
        # TODO: Convert MCP tools to LangChain tools
        # For now, return empty list
        return []
    
    async def _build_agent(
        self,
        agent_config: Dict[str, Any],
        llm_config: Dict[str, Any],
        tools: List[Any]
    ) -> Any:
        """
        Build LangChain ReAct agent.
        
        Args:
            agent_config: Agent configuration
            llm_config: LLM configuration
            tools: List of LangChain tools
            
        Returns:
            LangChain agent
        """
        # TODO: Implement LangChain agent building
        # This will use langchain, langchain-openai, etc.
        # For now, return a placeholder
        logger.info("Building LangChain agent (placeholder)")
        return {
            "agent_config": agent_config,
            "llm_config": llm_config,
            "tools": tools
        }
    
    async def _execute_agent(
        self,
        agent: Any,
        user_query: str,
        logger_instance: Any
    ) -> Dict[str, Any]:
        """
        Execute LangChain agent with user query.
        
        Args:
            agent: LangChain agent
            user_query: User's question
            logger_instance: Logger instance
            
        Returns:
            Agent execution result
        """
        # TODO: Implement actual LangChain agent execution
        # For now, return a placeholder
        logger_instance.info(f"Executing agent with query: {user_query}")
        
        return {
            "final_answer": f"Placeholder answer for: {user_query}",
            "messages": [
                {"role": "user", "content": user_query},
                {"role": "assistant", "content": f"Placeholder answer for: {user_query}"}
            ],
            "correlation_id": "placeholder-cid"
        }
