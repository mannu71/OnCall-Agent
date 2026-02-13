"""
Workflow Engine for Python

This is the main facade for workflow execution, ported from the Node.js WorkflowEngine.
It provides a single entry point for executing any type of workflow by delegating
to the appropriate strategy.
"""

from typing import Dict, Any, List, Optional
import logging
import time
import uuid
import asyncio

from app.workflow.strategies.base import BaseStrategy
from app.workflow.mcp.manager import MCPClientManager

logger = logging.getLogger(__name__)


class WorkflowExecutionError(Exception):
    """Custom exception for workflow execution errors."""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


class WorkflowEngine:
    """
    Unified Workflow Engine for executing different types of workflows.
    
    This engine uses the strategy pattern to delegate execution to the
    appropriate strategy based on the workflow type.
    """
    
    def __init__(
        self,
        strategies: Optional[List[BaseStrategy]] = None,
        mcp_manager: Optional[MCPClientManager] = None,
        max_execution_time: int = 300_000,  # 5 minutes in milliseconds
        enable_metrics: bool = True
    ):
        """
        Initialize the WorkflowEngine.
        
        Args:
            strategies: List of workflow strategies (will be auto-loaded if None)
            mcp_manager: MCP client manager instance
            max_execution_time: Maximum execution time in milliseconds
            enable_metrics: Whether to enable metrics collection
        """
        # Import strategies here to avoid circular imports
        from app.workflow.strategies.orchestrator import OrchestratorStrategy
        from app.workflow.strategies.react import ReactStrategy
        
        self.strategies = strategies or [
            OrchestratorStrategy(),
            ReactStrategy()
        ]
        
        self.mcp_manager = mcp_manager or MCPClientManager()
        self.max_execution_time = max_execution_time
        self.enable_metrics = enable_metrics
        
        logger.info(
            "WorkflowEngine initialized",
            extra={
                "strategies": [s.__class__.__name__ for s in self.strategies],
                "max_execution_time": max_execution_time
            }
        )
    
    async def execute(
        self,
        workflow: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a workflow using the appropriate strategy.
        
        Args:
            workflow: The workflow definition dictionary
            context: Execution context (userQuery, variables, etc.)
            
        Returns:
            Execution result dictionary
            
        Raises:
            WorkflowExecutionError: If execution fails
        """
        context = context or {}
        execution_id = context.get("execution_id") or self._generate_execution_id()
        start_time = time.time()
        
        logger.info(
            "Workflow execution started",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow.get("id"),
                "workflow_name": workflow.get("name"),
                "context": self._sanitize_context(context)
            }
        )
        
        try:
            # 1. Select appropriate strategy
            strategy = self.select_strategy(workflow)
            logger.info(
                "Strategy selected",
                extra={
                    "execution_id": execution_id,
                    "strategy": strategy.__class__.__name__
                }
            )
            
            # 2. Validate workflow
            strategy.validate_workflow(workflow)
            
            # 3. Prepare execution context
            execution_context = {
                **context,
                "execution_id": execution_id,
                "mcp_manager": self.mcp_manager,
                "logger": logger
            }
            
            # 4. Execute with timeout
            result = await self._execute_with_timeout(
                strategy,
                workflow,
                execution_context
            )
            
            # 5. Format and return result
            duration = (time.time() - start_time) * 1000  # Convert to ms
            formatted_result = {
                "success": True,
                "execution_id": execution_id,
                "workflow_id": workflow.get("id"),
                "workflow_name": workflow.get("name"),
                "strategy": strategy.__class__.__name__,
                "duration": duration,
                "timestamp": time.time(),
                **result
            }
            
            logger.info(
                "Workflow execution completed",
                extra={
                    "execution_id": execution_id,
                    "duration": duration,
                    "success": True
                }
            )
            
            if self.enable_metrics:
                self._record_metrics(workflow, strategy, duration, True)
            
            return formatted_result
            
        except Exception as error:
            duration = (time.time() - start_time) * 1000
            
            logger.error(
                "Workflow execution failed",
                extra={
                    "execution_id": execution_id,
                    "workflow_id": workflow.get("id"),
                    "workflow_name": workflow.get("name"),
                    "duration": duration,
                    "error": str(error)
                },
                exc_info=True
            )
            
            if self.enable_metrics:
                self._record_metrics(workflow, None, duration, False)
            
            raise WorkflowExecutionError(
                f"Workflow execution failed: {str(error)}",
                {
                    "execution_id": execution_id,
                    "workflow_id": workflow.get("id"),
                    "workflow_name": workflow.get("name"),
                    "duration": duration,
                    "original_error": str(error)
                }
            )
    
    def select_strategy(self, workflow: Dict[str, Any]) -> BaseStrategy:
        """
        Select the appropriate strategy for a workflow.
        
        Args:
            workflow: The workflow definition
            
        Returns:
            Strategy instance
            
        Raises:
            ValueError: If no strategy can handle the workflow
        """
        for strategy in self.strategies:
            if strategy.can_handle(workflow):
                logger.debug(
                    "Strategy matched",
                    extra={
                        "strategy": strategy.__class__.__name__,
                        "workflow_id": workflow.get("id")
                    }
                )
                return strategy
        
        available = ", ".join(s.__class__.__name__ for s in self.strategies)
        raise ValueError(
            f"No strategy found for workflow: {workflow.get('name')} "
            f"({workflow.get('id')}). Available strategies: {available}"
        )
    
    async def _execute_with_timeout(
        self,
        strategy: BaseStrategy,
        workflow: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute strategy with timeout protection.
        
        Args:
            strategy: The strategy to execute
            workflow: The workflow definition
            context: Execution context
            
        Returns:
            Execution result
        """
        timeout_seconds = self.max_execution_time / 1000
        
        try:
            result = await asyncio.wait_for(
                strategy.execute(workflow, context),
                timeout=timeout_seconds
            )
            return result
        except asyncio.TimeoutError:
            raise WorkflowExecutionError(
                f"Workflow execution timeout after {self.max_execution_time}ms"
            )
    
    def validate(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate a workflow without executing it.
        
        Args:
            workflow: The workflow definition
            
        Returns:
            Validation result dictionary
        """
        try:
            strategy = self.select_strategy(workflow)
            strategy.validate_workflow(workflow)
            
            return {
                "valid": True,
                "strategy": strategy.__class__.__name__,
                "workflow": {
                    "id": workflow.get("id"),
                    "name": workflow.get("name"),
                    "node_count": len(workflow.get("nodes", []))
                }
            }
        except Exception as error:
            return {
                "valid": False,
                "error": str(error),
                "workflow": {
                    "id": workflow.get("id"),
                    "name": workflow.get("name")
                }
            }
    
    async def cleanup(self) -> None:
        """Clean up resources."""
        logger.info("WorkflowEngine cleanup started")
        
        try:
            await self.mcp_manager.disconnect_all()
            logger.info("WorkflowEngine cleanup completed")
        except Exception as error:
            logger.error(
                "WorkflowEngine cleanup failed",
                extra={"error": str(error)}
            )
    
    def _generate_execution_id(self) -> str:
        """Generate a unique execution ID."""
        timestamp = int(time.time() * 1000)
        random_part = str(uuid.uuid4())[:8]
        return f"exec-{timestamp}-{random_part}"
    
    def _sanitize_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize context for logging (remove sensitive data).
        
        Args:
            context: The context dictionary
            
        Returns:
            Sanitized context
        """
        sanitized = context.copy()
        
        # Remove sensitive fields
        for key in ["api_key", "password", "token", "secret"]:
            sanitized.pop(key, None)
        
        # Truncate long values
        if "user_query" in sanitized and len(sanitized["user_query"]) > 100:
            sanitized["user_query"] = sanitized["user_query"][:100] + "..."
        
        return sanitized
    
    def _record_metrics(
        self,
        workflow: Dict[str, Any],
        strategy: Optional[BaseStrategy],
        duration: float,
        success: bool
    ) -> None:
        """
        Record metrics for monitoring.
        
        Args:
            workflow: The workflow
            strategy: The strategy used
            duration: Execution duration in ms
            success: Whether execution succeeded
        """
        # TODO: Integrate with metrics system (Prometheus, etc.)
        logger.debug(
            "Workflow metrics",
            extra={
                "workflow_id": workflow.get("id"),
                "workflow_name": workflow.get("name"),
                "strategy": strategy.__class__.__name__ if strategy else None,
                "duration": duration,
                "success": success
            }
        )
