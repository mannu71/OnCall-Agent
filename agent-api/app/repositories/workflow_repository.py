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

# Constants
WORKFLOW_FILENAME = "workflow.yaml"


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
    
    def _get_workflow_dir(self, workflow_name: str) -> Path:
        """Get the directory path for a workflow's files.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Path to workflow's directory
        """
        safe_name = workflow_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        return self.storage_path / safe_name
    
    def _get_file_path(self, workflow_name: str) -> Path:
        """Get file path for a workflow definition.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            Path to workflow.yaml file inside workflow directory
        """
        return self._get_workflow_dir(workflow_name) / WORKFLOW_FILENAME
    
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
        """Async generator to scan all workflow directories.
        
        Yields:
            Workflow dictionaries
        """
        # Scan for directories (each workflow has its own directory)
        for workflow_dir in self.storage_path.iterdir():
            if not workflow_dir.is_dir():
                continue
                
            workflow_file = workflow_dir / WORKFLOW_FILENAME
            if not workflow_file.exists():
                continue
                
            try:
                async with aiofiles.open(workflow_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    workflow = yaml.safe_load(content)
                    
                    if isinstance(workflow, dict):
                        yield workflow
            
            except yaml.YAMLError as e:
                logger.error(f"YAML parse error in {workflow_file}: {e}")
            except Exception as e:
                logger.error(f"Error loading workflow from {workflow_file}: {e}")
    
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
        workflow_dir = self._get_workflow_dir(workflow_name)
        file_path = workflow_dir / WORKFLOW_FILENAME
        
        # Check if this is an update
        existing = await self.get_by_name(workflow_name)
        
        if existing:
            # If name changed, delete old directory
            if existing.get('name') != workflow_name:
                old_dir = self._get_workflow_dir(existing['name'])
                if old_dir.exists():
                    import shutil
                    shutil.rmtree(old_dir)
            
            logger.info(f"Updated workflow: {workflow_name} (ID: {workflow.get('id', 'N/A')})")
        else:
            logger.info(f"Created new workflow: {workflow_name} (ID: {workflow.get('id', 'N/A')})")
        
        # Create workflow directory if it doesn't exist
        workflow_dir.mkdir(parents=True, exist_ok=True)
        
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
    
    async def delete(self, workflow_id: str, delete_sql_files: bool = False) -> bool:
        """Delete a workflow by ID or name.
        
        Args:
            workflow_id: Workflow ID or name
            delete_sql_files: Whether to also delete associated SQL files
            
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
        workflow_dir = self._get_workflow_dir(workflow_name)
        
        if workflow_dir.exists():
            import shutil
            shutil.rmtree(workflow_dir)
            self._invalidate_cache(workflow_name)
            logger.info(f"Deleted workflow directory: {workflow_dir}")
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
    
    async def save_sql_file(self, workflow_name: str, filename: str, content: str) -> Path:
        """Save an SQL file to the workflow's directory.
        
        Args:
            workflow_name: Workflow name
            filename: SQL filename (without path)
            content: SQL file content
            
        Returns:
            Path to saved file
        """
        workflow_dir = self._get_workflow_dir(workflow_name)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        
        # Sanitize filename
        safe_filename = Path(filename).name
        file_path = workflow_dir / safe_filename
        
        try:
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(content)
            
            logger.info(f"Saved SQL file: {file_path}")
            return file_path
        
        except Exception as e:
            logger.error(f"Error saving SQL file: {e}")
            raise
    
    async def get_sql_file(self, workflow_name: str, filename: str) -> Optional[str]:
        """Get SQL file content from the workflow's directory.
        
        Args:
            workflow_name: Workflow name
            filename: SQL filename (without path)
            
        Returns:
            SQL file content or None if not found
        """
        safe_filename = Path(filename).name
        workflow_dir = self._get_workflow_dir(workflow_name)
        file_path = workflow_dir / safe_filename
        
        if not file_path.exists():
            return None
        
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                return await f.read()
        
        except Exception as e:
            logger.error(f"Error reading SQL file: {e}")
            return None
    
    async def delete_sql_file(self, workflow_name: str, filename: str) -> bool:
        """Delete an SQL file from the workflow's directory.
        
        Args:
            workflow_name: Workflow name
            filename: SQL filename (without path)
            
        Returns:
            True if deleted, False if not found
        """
        safe_filename = Path(filename).name
        workflow_dir = self._get_workflow_dir(workflow_name)
        file_path = workflow_dir / safe_filename
        
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Deleted SQL file: {file_path}")
                return True
            except Exception as e:
                logger.error(f"Error deleting SQL file: {e}")
                return False
        
        return False
    
    async def list_sql_files(self, workflow_name: str) -> List[str]:
        """List all SQL files in the workflow's directory.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            List of SQL filenames
        """
        workflow_dir = self._get_workflow_dir(workflow_name)
        
        if not workflow_dir.exists():
            return []
        
        return [f.name for f in workflow_dir.glob("*.sql")]
    
    async def get_sql_files_for_workflow(self, workflow_name: str) -> List[str]:
        """Get list of SQL files referenced in a workflow.
        
        Args:
            workflow_name: Workflow name
            
        Returns:
            List of SQL filenames referenced in the workflow
        """
        workflow = await self.get_by_name(workflow_name)
        if not workflow:
            return []
        
        sql_files = []
        for node in workflow.get('nodes', []):
            if node.get('type') == 'orchestrator':
                node_data = node.get('data', {})
                sql_file = node_data.get('sqlFile') or node_data.get('fileName')
                if sql_file:
                    # Strip any path prefix
                    sql_filename = sql_file.replace('sql/', '').replace('sql\\', '')
                    if sql_filename not in sql_files:
                        sql_files.append(sql_filename)
        
        return sql_files
    
    def _normalize_sql_filename(self, sql_file: str) -> str:
        """Normalize SQL filename by removing path prefixes.
        
        Args:
            sql_file: SQL file path or name
            
        Returns:
            Normalized filename
        """
        return sql_file.replace('sql/', '').replace('sql\\', '')
    
    def _is_sql_file_match(self, node_data: Dict[str, Any], target_filename: str) -> bool:
        """Check if orchestrator node uses the target SQL file.
        
        Args:
            node_data: Node data dictionary
            target_filename: Target filename to match
            
        Returns:
            True if node uses this SQL file
        """
        sql_file = node_data.get('sqlFile') or node_data.get('fileName')
        if not sql_file:
            return False
        
        sql_filename = self._normalize_sql_filename(sql_file)
        return sql_filename == target_filename
    
    async def is_sql_file_used_by_other_workflows(self, filename: str, exclude_workflow: str) -> bool:
        """Check if an SQL file is used by other workflows.
        
        Args:
            filename: SQL filename to check
            exclude_workflow: Workflow name to exclude from check
            
        Returns:
            True if used by other workflows, False otherwise
        """
        safe_filename = Path(filename).name
        
        async for workflow in self._scan_workflows():
            if workflow.get('name') == exclude_workflow:
                continue
            
            for node in workflow.get('nodes', []):
                if node.get('type') == 'orchestrator':
                    node_data = node.get('data', {})
                    if self._is_sql_file_match(node_data, safe_filename):
                        return True
        
        return False
