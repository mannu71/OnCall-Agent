"""Workflow repository for data access."""
import aiofiles
import yaml
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from app.repositories.base import BaseRepository
from app.config import settings

logger = logging.getLogger(__name__)


class WorkflowRepository(BaseRepository[Dict[str, Any]]):
    """Repository for workflow data access with caching."""
    
    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize workflow repository.
        
        Args:
            storage_path: Path to workflows directory
        """
        path = Path(storage_path) if storage_path else Path(settings.storage_path) / "workflows"
        super().__init__(path)
        
        # Cache configuration
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
        self._cache_ttl = timedelta(minutes=5)  # 5 minute TTL
    
    def _get_file_path(self, workflow_name: str) -> Path:
        """Get file path for a workflow.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Path to workflow file
        """
        # Sanitize workflow name for filename
        safe_name = workflow_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        return self.storage_path / f"{safe_name}.yaml"
    
    def _is_cache_valid(self, workflow_name: str) -> bool:
        """Check if cached workflow is still valid.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            True if cache is valid, False otherwise
        """
        if workflow_name not in self._cache:
            return False
        
        timestamp = self._cache_timestamps.get(workflow_name)
        if not timestamp:
            return False
        
        return datetime.now(timezone.utc) - timestamp < self._cache_ttl
    
    def _update_cache(self, workflow_name: str, workflow: Dict[str, Any]) -> None:
        """Update cache for a workflow.
        
        Args:
            workflow_name: Workflow name
            workflow: Workflow data
        """
        self._cache[workflow_name] = workflow
        self._cache_timestamps[workflow_name] = datetime.now(timezone.utc)
    
    def _invalidate_cache(self, workflow_name: str) -> None:
        """Invalidate cache for a workflow.
        
        Args:
            workflow_name: Workflow name
        """
        self._cache.pop(workflow_name, None)
        self._cache_timestamps.pop(workflow_name, None)
    
    async def get_by_id(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow by ID.
        
        Args:
            workflow_id: Workflow ID
            
        Returns:
            Workflow if found, None otherwise
        """
        # Need to scan all workflows to find by ID
        workflows = await self.list_all()
        for workflow in workflows:
            if workflow.get('id') == workflow_id:
                return workflow
        return None
    
    async def get_by_name(self, workflow_name: str) -> Optional[Dict[str, Any]]:
        """Get workflow by name.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Workflow if found, None otherwise
        """
        # Check cache first
        if self._is_cache_valid(workflow_name):
            logger.debug(f"Cache hit for workflow: {workflow_name}")
            return self._cache[workflow_name]
        
        file_path = self._get_file_path(workflow_name)
        
        if not file_path.exists():
            # Try exact match search in case name was sanitized differently
            async for workflow in self._scan_workflows():
                if workflow.get('name') == workflow_name:
                    self._update_cache(workflow_name, workflow)
                    return workflow
            return None
        
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                workflow = yaml.safe_load(content)
                
                if isinstance(workflow, dict):
                    self._update_cache(workflow_name, workflow)
                    return workflow
                
                logger.warning(f"Invalid workflow format in {file_path}")
                return None
        
        except yaml.YAMLError as e:
            logger.error(f"YAML parse error in {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error loading workflow from {file_path}: {e}")
            return None
    
    async def _scan_workflows(self):
        """Async generator to scan all workflow files.
        
        Yields:
            Workflow dictionaries
        """
        for file_path in self.storage_path.glob("*.yaml"):
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    workflow = yaml.safe_load(content)
                    
                    if isinstance(workflow, dict):
                        yield workflow
            
            except yaml.YAMLError as e:
                logger.error(f"YAML parse error in {file_path}: {e}")
            except Exception as e:
                logger.error(f"Error loading workflow from {file_path}: {e}")
    
    async def list_all(self) -> List[Dict[str, Any]]:
        """List all workflows.
        
        Returns:
            List of all workflows
        """
        workflows = []
        async for workflow in self._scan_workflows():
            workflows.append(workflow)
        return workflows
    
    async def save(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Save or update a workflow.
        
        Args:
            workflow: Workflow data
            
        Returns:
            Saved workflow
            
        Raises:
            ValueError: If workflow is missing required fields
        """
        if 'name' not in workflow:
            raise ValueError("Workflow must have a 'name' field")
        
        workflow_name = workflow['name']
        file_path = self._get_file_path(workflow_name)
        
        # Check if this is an update
        existing = await self.get_by_name(workflow_name)
        
        if existing:
            # If name changed, delete old file
            if existing.get('name') != workflow_name:
                old_file = self._get_file_path(existing['name'])
                if old_file.exists():
                    old_file.unlink()
            
            logger.info(f"Updated workflow: {workflow_name} (ID: {workflow.get('id', 'N/A')})")
        else:
            logger.info(f"Created new workflow: {workflow_name} (ID: {workflow.get('id', 'N/A')})")
        
        # Save to file
        try:
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                content = yaml.dump(workflow, default_flow_style=False, sort_keys=False, 
                                   allow_unicode=True, indent=2)
                await f.write(content)
            
            # Update cache
            self._update_cache(workflow_name, workflow)
            
            return workflow
        
        except Exception as e:
            logger.error(f"Error saving workflow: {e}")
            raise
    
    async def delete(self, workflow_id: str) -> bool:
        """Delete a workflow by ID or name.
        
        Args:
            workflow_id: Workflow ID or name
            
        Returns:
            True if deleted, False if not found
        """
        # Try to find by name first
        workflow = await self.get_by_name(workflow_id)
        
        # If not found, try by ID
        if not workflow:
            workflow = await self.get_by_id(workflow_id)
        
        if not workflow:
            return False
        
        workflow_name = workflow['name']
        file_path = self._get_file_path(workflow_name)
        
        if file_path.exists():
            file_path.unlink()
            self._invalidate_cache(workflow_name)
            logger.info(f"Deleted workflow: {workflow_name}")
            return True
        
        return False
    
    async def exists(self, workflow_id: str) -> bool:
        """Check if a workflow exists by ID or name.
        
        Args:
            workflow_id: Workflow ID or name
            
        Returns:
            True if exists, False otherwise
        """
        workflow = await self.get_by_name(workflow_id)
        if not workflow:
            workflow = await self.get_by_id(workflow_id)
        return workflow is not None
    
    def clear_cache(self) -> None:
        """Clear all cached workflows."""
        self._cache.clear()
        self._cache_timestamps.clear()
        logger.info("Workflow cache cleared")
