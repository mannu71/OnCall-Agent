"""Dependency injection container and factory functions."""
from typing import Optional

from app.repositories import WorkflowRepository, ExecutionRepository


class DependencyContainer:
    """Container for managing application dependencies."""
    
    def __init__(self):
        """Initialize the dependency container."""
        self._workflow_repo: Optional[WorkflowRepository] = None
        self._execution_repo: Optional[ExecutionRepository] = None
    
    def get_workflow_repository(self) -> WorkflowRepository:
        """Get or create workflow repository instance.
        
        Returns:
            WorkflowRepository instance
        """
        if self._workflow_repo is None:
            self._workflow_repo = WorkflowRepository()
        return self._workflow_repo
    
    def get_execution_repository(self) -> ExecutionRepository:
        """Get or create execution repository instance.
        
        Returns:
            ExecutionRepository instance
        """
        if self._execution_repo is None:
            self._execution_repo = ExecutionRepository()
        return self._execution_repo
    
    def reset(self) -> None:
        """Reset all dependencies (useful for testing)."""
        self._workflow_repo = None
        self._execution_repo = None


# Global container instance
_container = DependencyContainer()


def get_container() -> DependencyContainer:
    """Get the global dependency container.
    
    Returns:
        DependencyContainer instance
    """
    return _container


# FastAPI dependency functions
def get_workflow_repository() -> WorkflowRepository:
    """FastAPI dependency for workflow repository.
    
    Returns:
        WorkflowRepository instance
    """
    return get_container().get_workflow_repository()


def get_execution_repository() -> ExecutionRepository:
    """FastAPI dependency for execution repository.
    
    Returns:
        ExecutionRepository instance
    """
    return get_container().get_execution_repository()
