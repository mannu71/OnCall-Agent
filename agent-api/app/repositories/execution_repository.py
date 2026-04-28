"""Execution repository for data access with database backend."""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from app.repositories.db_repository import db_repository

logger = logging.getLogger(__name__)


class ExecutionRepository:
    """Repository for execution history data access using PostgreSQL database."""
    
    def __init__(self):
        """Initialize execution repository."""
        self.db = db_repository
        logger.info("ExecutionRepository initialized with database backend")
    
    async def get_by_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get execution by ID - direct query.
        
        Args:
            execution_id: Execution ID (integer as string)
            
        Returns:
            Execution if found, None otherwise
        """
        try:
            execution_id_int = int(execution_id)
            execution = await self.db.get_execution_by_id(execution_id_int)
            return self._format_execution(execution) if execution else None
        except (ValueError, TypeError):
            return None
    
    async def get_by_workflow_and_id(self, workflow_name: str, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get execution by workflow name and execution ID.
        
        Args:
            workflow_name: Workflow name
            execution_id: Execution ID
            
        Returns:
            Execution if found, None otherwise
        """
        try:
            execution_id_int = int(execution_id)
            execution = await self.db.get_execution_by_id(execution_id_int)
            if execution and execution.get('workflow_name') == workflow_name:
                return self._format_execution(execution)
            return None
        except (ValueError, TypeError):
            return None
    
    async def list_all(self) -> List[Dict[str, Any]]:
        """List all executions across all workflows.
        
        Returns:
            List of all executions
        """
        executions = await self.db.list_executions(limit=1000)
        return [self._format_execution(e) for e in executions]
    
    async def list_by_workflow(self, workflow_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """List executions for a specific workflow.
        
        Args:
            workflow_name: Workflow name
            limit: Maximum number of executions to return
            
        Returns:
            List of executions for the workflow
        """
        executions = await self.db.list_executions(
            workflow_name=workflow_name,
            limit=limit or 100
        )
        return [self._format_execution(e) for e in executions]
    
    async def save(self, execution: Dict[str, Any]) -> Dict[str, Any]:
        """Save an execution.
        
        Args:
            execution: Execution data
            
        Returns:
            Saved execution
            
        Raises:
            ValueError: If execution is missing required fields
        """
        if 'workflow_name' not in execution:
            raise ValueError("Execution must have a 'workflow_name' field")
        
        workflow_name = execution['workflow_name']
        execution_id = execution.get('execution_id') or execution.get('id')
        
        # Map the execution data to database fields
        execution_data = {
            'workflow_name': workflow_name,
            'workflow_id': execution.get('workflow_id'),
            'status': execution.get('status', 'pending'),
            'started_at': self._parse_datetime(execution.get('start_time')),
            'input': execution.get('input'),
            'logs': execution.get('logs', [])
        }
        
        if execution.get('end_time'):
            execution_data['completed_at'] = self._parse_datetime(execution.get('end_time'))
        
        # Handle duration - convert from seconds to milliseconds if needed
        if execution.get('duration_ms'):
            execution_data['duration_ms'] = execution.get('duration_ms')
        elif execution.get('duration'):
            # duration is in seconds, convert to milliseconds
            execution_data['duration_ms'] = int(execution.get('duration') * 1000)
        
        # Handle both 'result' and 'results' keys for output
        if execution.get('result'):
            execution_data['output'] = execution.get('result')
        elif execution.get('results'):
            execution_data['output'] = execution.get('results')
        
        if execution.get('error'):
            execution_data['error'] = execution.get('error')
        
        # Check if this is an update
        if execution_id:
            try:
                execution_id_int = int(execution_id)
                existing = await self.db.get_execution_by_id(execution_id_int)
                if existing:
                    updated = await self.db.update_execution(execution_id_int, execution_data)
                    logger.debug(f"Updated execution: {execution_id} for workflow: {workflow_name}")
                    return self._format_execution(updated)
            except (ValueError, TypeError):
                pass
        
        # Create new execution
        created = await self.db.create_execution(execution_data)
        logger.debug(f"Created execution: {created.get('id')} for workflow: {workflow_name}")
        return self._format_execution(created)
    
    async def delete(self, execution_id: str) -> bool:
        """Delete an execution by ID.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            True if deleted, False if not found
        """
        try:
            execution_id_int = int(execution_id)
            return await self.db.delete_execution(execution_id_int)
        except (ValueError, TypeError):
            return False
    
    async def delete_all(self) -> int:
        """Delete all executions.
        
        Returns:
            Number of executions deleted
        """
        return await self.db.delete_all_executions()
    
    async def delete_by_workflow_and_id(self, workflow_name: str, execution_id: str) -> bool:
        """Delete an execution by workflow name and execution ID.
        
        Args:
            workflow_name: Workflow name
            execution_id: Execution ID
            
        Returns:
            True if deleted, False if not found
        """
        return await self.delete(execution_id)
    
    async def exists(self, execution_id: str) -> bool:
        """Check if an execution exists by ID.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            True if exists, False otherwise
        """
        execution = await self.get_by_id(execution_id)
        return execution is not None
    
    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string to datetime object.
        
        Args:
            dt_str: Datetime string in ISO format
            
        Returns:
            Datetime object or None
        """
        if not dt_str:
            return None
        
        try:
            # Handle ISO format with Z suffix
            if dt_str.endswith('Z'):
                dt_str = dt_str[:-1] + '+00:00'
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None
    
    def _format_execution(self, execution: Dict[str, Any]) -> Dict[str, Any]:
        """Format execution from database format to API format.
        
        Args:
            execution: Execution data from database
            
        Returns:
            Formatted execution data
        """
        return {
            'id': str(execution.get('id')),
            'execution_id': str(execution.get('id')),
            'workflow_id': execution.get('workflow_id'),
            'workflow_name': execution.get('workflow_name'),
            'status': execution.get('status'),
            'start_time': execution.get('started_at'),
            'end_time': execution.get('completed_at'),
            'duration_ms': execution.get('duration_ms'),
            'duration': execution.get('duration_ms') / 1000 if execution.get('duration_ms') else None,
            'input': execution.get('input'),
            'result': execution.get('output'),
            'results': execution.get('output'),
            'error': execution.get('error'),
            'logs': execution.get('logs', [])
        }
