"""
Base Strategy for Workflow Execution

This module defines the abstract base class for workflow execution strategies.
All workflow strategies (Orchestrator, ReAct, etc.) must inherit from this class.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """
    Abstract base class for workflow execution strategies.
    
    Each strategy implements agent-style execution invoked from node handlers:
    - ReactStrategy: AI agent workflows using LangChain / LangGraph
    - BatchReactStrategy: Parallel agent batches
    - RouterStrategy: Semantic routing between agents

    SQL orchestration runs via ``workflow/executor/handlers/orchestrator.py``
    and ``services/sql_pipeline/``, not through WorkflowEngine strategies.
    """
    
    def __init__(self):
        self.logger = logger
    
    @abstractmethod
    def can_handle(self, workflow: Dict[str, Any]) -> bool:
        """
        Determine if this strategy can handle the given workflow.
        
        Args:
            workflow: The workflow definition dictionary
            
        Returns:
            True if this strategy can execute the workflow, False otherwise
        """
        pass
    
    @abstractmethod
    async def execute(self, workflow: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the workflow using this strategy.
        
        Args:
            workflow: The workflow definition dictionary
            context: Execution context (variables, user query, etc.)
            
        Returns:
            Execution result dictionary with success status and output
        """
        pass
    
    def validate_workflow(self, workflow: Dict[str, Any]) -> bool:
        """
        Validate the workflow structure for this strategy.
        
        Args:
            workflow: The workflow definition dictionary
            
        Returns:
            True if valid
            
        Raises:
            ValueError: If workflow is invalid
        """
        # Default implementation - can be overridden
        if not workflow:
            raise ValueError("Workflow cannot be empty")
        
        if "nodes" not in workflow:
            raise ValueError("Workflow must have 'nodes' field")
        
        if not isinstance(workflow["nodes"], list):
            raise ValueError("Workflow 'nodes' must be a list")
        
        return True
