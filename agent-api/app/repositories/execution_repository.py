"""Execution repository for data access."""
import aiofiles
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from app.repositories.base import BaseRepository
from app.config import settings

logger = logging.getLogger(__name__)

# Constants
JSON_GLOB_PATTERN = "*.json"


class ExecutionRepository(BaseRepository[Dict[str, Any]]):
    """Repository for execution history data access."""
    
    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize execution repository.
        
        Args:
            storage_path: Path to executions directory
        """
        path = Path(storage_path) if storage_path else Path(settings.storage_path) / "executions"
        super().__init__(path)
    
    def _get_workflow_dir(self, workflow_name: str) -> Path:
        """Get directory for a workflow's executions.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Path to workflow executions directory
        """
        safe_name = workflow_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        workflow_dir = self.storage_path / safe_name
        workflow_dir.mkdir(parents=True, exist_ok=True)
        return workflow_dir
    
    def _get_file_path(self, workflow_name: str, execution_id: str) -> Path:
        """Get file path for an execution.
        
        Args:
            workflow_name: Workflow name
            execution_id: Execution ID
            
        Returns:
            Path to execution file
        """
        workflow_dir = self._get_workflow_dir(workflow_name)
        return workflow_dir / f"{execution_id}.json"
    
    async def get_by_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get execution by ID.
        
        Note: This requires scanning all workflow directories.
        For better performance, use get_by_workflow_and_id.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            Execution if found, None otherwise
        """
        # Scan all workflow directories
        for workflow_dir in self.storage_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            
            file_path = workflow_dir / f"{execution_id}.json"
            if file_path.exists():
                try:
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        return json.loads(content)
                except Exception as e:
                    logger.error(f"Error loading execution from {file_path}: {e}")
        
        return None
    
    async def get_by_workflow_and_id(self, workflow_name: str, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get execution by workflow name and execution ID.
        
        Args:
            workflow_name: Workflow name
            execution_id: Execution ID
            
        Returns:
            Execution if found, None otherwise
        """
        file_path = self._get_file_path(workflow_name, execution_id)
        
        if not file_path.exists():
            return None
        
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.error(f"Error loading execution from {file_path}: {e}")
            return None
    
    async def list_all(self) -> List[Dict[str, Any]]:
        """List all executions across all workflows.
        
        Returns:
            List of all executions
        """
        executions = []
        
        for workflow_dir in self.storage_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            
            for file_path in workflow_dir.glob(JSON_GLOB_PATTERN):
                try:
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        execution = json.loads(content)
                        executions.append(execution)
                except Exception as e:
                    logger.error(f"Error loading execution from {file_path}: {e}")
        
        # Sort by start time (most recent first)
        executions.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        return executions
    
    async def list_by_workflow(self, workflow_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """List executions for a specific workflow.
        
        Args:
            workflow_name: Workflow name
            limit: Maximum number of executions to return
            
        Returns:
            List of executions for the workflow
        """
        workflow_dir = self._get_workflow_dir(workflow_name)
        executions = []
        
        for file_path in workflow_dir.glob("*.json"):
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    execution = json.loads(content)
                    executions.append(execution)
            except Exception as e:
                logger.error(f"Error loading execution from {file_path}: {e}")
        
        # Sort by start time (most recent first)
        executions.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        
        # Apply limit if specified
        if limit:
            executions = executions[:limit]
        
        return executions
    
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
        if 'execution_id' not in execution:
            raise ValueError("Execution must have an 'execution_id' field")
        
        workflow_name = execution['workflow_name']
        execution_id = execution['execution_id']
        
        file_path = self._get_file_path(workflow_name, execution_id)
        
        try:
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                content = json.dumps(execution, indent=2, default=str)
                await f.write(content)
            
            logger.debug(f"Saved execution: {execution_id} for workflow: {workflow_name}")
            return execution
        
        except Exception as e:
            logger.error(f"Error saving execution: {e}")
            raise
    
    async def delete(self, execution_id: str) -> bool:
        """Delete an execution by ID.
        
        Note: This requires scanning all workflow directories.
        For better performance, use delete_by_workflow_and_id.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            True if deleted, False if not found
        """
        # Scan all workflow directories
        for workflow_dir in self.storage_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            
            file_path = workflow_dir / f"{execution_id}.json"
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Deleted execution: {execution_id}")
                return True
        
        return False
    
    async def delete_by_workflow_and_id(self, workflow_name: str, execution_id: str) -> bool:
        """Delete an execution by workflow name and execution ID.
        
        Args:
            workflow_name: Workflow name
            execution_id: Execution ID
            
        Returns:
            True if deleted, False if not found
        """
        file_path = self._get_file_path(workflow_name, execution_id)
        
        if file_path.exists():
            file_path.unlink()
            logger.info(f"Deleted execution: {execution_id} for workflow: {workflow_name}")
            return True
        
        return False
    
    async def exists(self, execution_id: str) -> bool:
        """Check if an execution exists by ID.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            True if exists, False otherwise
        """
        execution = await self.get_by_id(execution_id)
        return execution is not None
    
    async def delete_old_executions(self, workflow_name: str, keep_count: int = 100) -> int:
        """Delete old executions, keeping only the most recent ones.
        
        Args:
            workflow_name: Workflow name
            keep_count: Number of recent executions to keep
            
        Returns:
            Number of executions deleted
        """
        executions = await self.list_by_workflow(workflow_name)
        
        if len(executions) <= keep_count:
            return 0
        
        # Delete old executions
        to_delete = executions[keep_count:]
        deleted_count = 0
        
        for execution in to_delete:
            execution_id = execution.get('execution_id')
            if execution_id:
                success = await self.delete_by_workflow_and_id(workflow_name, execution_id)
                if success:
                    deleted_count += 1
        
        logger.info(f"Deleted {deleted_count} old executions for workflow: {workflow_name}")
        return deleted_count
    
    async def delete_all(self) -> int:
        """Delete all executions across all workflows.
        
        Returns:
            Number of executions deleted
        """
        deleted_count = 0
        
        # Scan all workflow directories
        for workflow_dir in self.storage_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            
            # Delete all .json files in the directory
            for file_path in workflow_dir.glob(JSON_GLOB_PATTERN):
                try:
                    file_path.unlink()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"Error deleting execution file {file_path}: {e}")
        
        logger.info(f"Deleted {deleted_count} total executions")
        return deleted_count
