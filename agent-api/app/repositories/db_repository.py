"""Database-backed repository for workflows and executions."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.db_models import (
    WorkflowModel,
    ExecutionModel,
    LLMConfigModel,
    MCPServerModel,
    ModelKeyModel,
)
from app.core.redact import redact

logger = logging.getLogger(__name__)


# Matches the output shape of ``mask_value`` in
# ``app/api/v1/endpoints/model_keys.py``: ``{4 chars}...{4 chars}`` or the
# all-stars ``"********"`` fallback. We use this on the server side to refuse
# overwriting a stored credential with what is obviously just its display
# mask round-tripped by the UI.
import re as _re
_MASKED_RE = _re.compile(r"^[A-Za-z0-9_\-+/=]{1,8}\.\.\.[A-Za-z0-9_\-+/=]{1,8}$")


def _looks_masked(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if v in {"", "********"}:
        return True
    if "..." in v and _MASKED_RE.match(v):
        return True
    return False


class DatabaseRepository:
    """Database-backed repository for all data access."""

    # ============================================
    # WORKFLOW OPERATIONS
    # ============================================

    async def create_workflow(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new workflow.
        
        Args:
            workflow_data: Workflow data including name, nodes, edges, etc.
            
        Returns:
            Created workflow data
        """
        async with AsyncSessionLocal() as session:
            workflow = WorkflowModel(
                name=workflow_data["name"],
                description=workflow_data.get("description"),
                nodes=workflow_data.get("nodes", []),
                edges=workflow_data.get("edges", []),
                viewport=workflow_data.get("viewport"),
                enabled=workflow_data.get("enabled", True),
                schedule=workflow_data.get("schedule"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            return self._workflow_to_dict(workflow)

    async def get_workflow(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a workflow by name.
        
        Args:
            name: Workflow name
            
        Returns:
            Workflow data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.name == name)
            )
            workflow = result.scalar_one_or_none()
            return self._workflow_to_dict(workflow) if workflow else None

    async def get_workflow_by_id(self, workflow_id: int) -> Optional[Dict[str, Any]]:
        """Get a workflow by ID - direct query.
        
        Args:
            workflow_id: Workflow ID (integer)
            
        Returns:
            Workflow data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            workflow = result.scalar_one_or_none()
            return self._workflow_to_dict(workflow) if workflow else None

    async def list_workflows(self) -> List[Dict[str, Any]]:
        """List all workflows.
        
        Returns:
            List of workflow data
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(WorkflowModel))
            workflows = result.scalars().all()
            return [self._workflow_to_dict(w) for w in workflows]

    async def update_workflow(self, name: str, workflow_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a workflow.
        
        Args:
            name: Workflow name
            workflow_data: Updated workflow data
            
        Returns:
            Updated workflow data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.name == name)
            )
            workflow = result.scalar_one_or_none()
            if not workflow:
                return None

            # Update fields - skip timestamp and id fields that should not be manually set
            skip_fields = {'id', 'created_at', 'updated_at', 'createdAt', 'updatedAt'}
            for key, value in workflow_data.items():
                if key in skip_fields:
                    continue
                if hasattr(workflow, key):
                    setattr(workflow, key, value)
            workflow.updated_at = datetime.now(timezone.utc)
            
            await session.commit()
            await session.refresh(workflow)
            return self._workflow_to_dict(workflow)

    async def delete_workflow(self, name: str) -> bool:
        """Delete a workflow.
        
        Args:
            name: Workflow name
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(WorkflowModel).where(WorkflowModel.name == name).returning(WorkflowModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None

    def _workflow_to_dict(self, workflow: WorkflowModel) -> Dict[str, Any]:
        """Convert workflow model to dictionary."""
        return {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "nodes": workflow.nodes or [],
            "edges": workflow.edges or [],
            "viewport": workflow.viewport,
            "enabled": workflow.enabled,
            "schedule": workflow.schedule,
            "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
            "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None
        }

    # ============================================
    # EXECUTION OPERATIONS
    # ============================================

    async def create_execution(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an execution record.
        
        Args:
            execution_data: Execution data
            
        Returns:
            Created execution data
        """
        async with AsyncSessionLocal() as session:
            execution = ExecutionModel(
                workflow_id=execution_data.get("workflow_id"),
                workflow_name=execution_data["workflow_name"],
                status=execution_data.get("status", "pending"),
                started_at=execution_data.get("started_at", datetime.now(timezone.utc)),
                completed_at=execution_data.get("completed_at"),
                duration_ms=execution_data.get("duration_ms"),
                input=execution_data.get("input"),
                output=execution_data.get("output"),
                error=execution_data.get("error"),
                logs=execution_data.get("logs"),
                input_tokens=execution_data.get("input_tokens",   0) or 0,
                output_tokens=execution_data.get("output_tokens", 0) or 0,
                total_tokens=execution_data.get("total_tokens",   0) or 0,
            )
            session.add(execution)
            await session.commit()
            await session.refresh(execution)
            return self._execution_to_dict(execution)

    async def update_execution(self, execution_id: int, execution_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an execution record.
        
        Args:
            execution_id: Execution ID
            execution_data: Updated execution data
            
        Returns:
            Updated execution data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ExecutionModel).where(ExecutionModel.id == execution_id)
            )
            execution = result.scalar_one_or_none()
            if not execution:
                return None

            for key, value in execution_data.items():
                if hasattr(execution, key):
                    setattr(execution, key, value)
            
            await session.commit()
            await session.refresh(execution)
            return self._execution_to_dict(execution)

    async def get_execution_by_id(self, execution_id: int) -> Optional[Dict[str, Any]]:
        """Get an execution by ID - direct query.
        
        Args:
            execution_id: Execution ID (integer)
            
        Returns:
            Execution data or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ExecutionModel).where(ExecutionModel.id == execution_id)
            )
            execution = result.scalar_one_or_none()
            return self._execution_to_dict(execution) if execution else None

    async def list_executions(self, workflow_name: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """List executions.
        
        Args:
            workflow_name: Filter by workflow name (optional)
            limit: Maximum number of results
            
        Returns:
            List of execution data
        """
        async with AsyncSessionLocal() as session:
            query = select(ExecutionModel).order_by(ExecutionModel.started_at.desc()).limit(limit)
            if workflow_name:
                query = query.where(ExecutionModel.workflow_name == workflow_name)
            
            result = await session.execute(query)
            executions = result.scalars().all()
            return [self._execution_to_dict(e) for e in executions]

    async def delete_execution(self, execution_id: int) -> bool:
        """Delete an execution by ID.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ExecutionModel).where(ExecutionModel.id == execution_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def delete_all_executions(self) -> int:
        """Delete all executions.
        
        Returns:
            Number of executions deleted
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(delete(ExecutionModel))
            await session.commit()
            return result.rowcount

    def _execution_to_dict(self, execution: ExecutionModel) -> Dict[str, Any]:
        """Convert execution model to dictionary."""
        return {
            "id": execution.id,
            "workflow_id": execution.workflow_id,
            "workflow_name": execution.workflow_name,
            "status": execution.status,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            "duration_ms": execution.duration_ms,
            "input":         execution.input,
            "output":        execution.output,
            "error":         execution.error,
            "logs":          execution.logs,
            "input_tokens":  getattr(execution, "input_tokens",  0) or 0,
            "output_tokens": getattr(execution, "output_tokens", 0) or 0,
            "total_tokens":  getattr(execution, "total_tokens",  0) or 0,
        }

    # ============================================
    # LLM CONFIG OPERATIONS
    # ============================================

    async def list_llm_configs(self) -> Dict[str, Dict[str, Any]]:
        """List all LLM configurations.
        
        Args:
            include_api_key: If True, include the api_key field in the result.
        
        Returns:
            Dictionary of LLM configurations keyed by name
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(LLMConfigModel))
            configs = result.scalars().all()
            return {
                config.name: self._llm_config_to_dict(config)
                for config in configs
            }

    async def get_llm_config(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific LLM configuration by name.

        Args:
            name: Configuration name
            include_api_key: If True, include the api_key field.

        Returns:
            LLM configuration dict or None
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LLMConfigModel).where(LLMConfigModel.name == name)
            )
            config = result.scalar_one_or_none()
            return self._llm_config_to_dict(config) if config else None

    async def create_llm_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new LLM configuration.

        Args:
            config_data: Configuration data including name, provider, model, etc.

        Returns:
            Created configuration dict
        """
        use_for_embeddings = bool(
            config_data.get("use_for_embeddings")
            or config_data.get("useForEmbeddings")
        )
        async with AsyncSessionLocal() as session:
            # Single-row exclusivity: only one config can be the active
            # embedding model. Clear the flag on every other row before
            # inserting the new one with true.
            if use_for_embeddings:
                await session.execute(
                    update(LLMConfigModel).values(use_for_embeddings=False)
                )

            config = LLMConfigModel(
                name=config_data["name"],
                provider=config_data["provider"],
                model=config_data.get("model", ""),
                endpoint=config_data.get("endpoint"),
                base_url=config_data.get("baseUrl") or config_data.get("base_url"),
                temperature=config_data.get("temperature", 0.7),
                max_tokens=config_data.get("maxTokens") or config_data.get("max_tokens", 4096),
                region=config_data.get("region", "us-east-1"),
                icon=config_data.get("icon"),
                description=config_data.get("description"),
                aws_profile=config_data.get("aws_profile"),
                use_for_embeddings=use_for_embeddings,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(config)
            await session.commit()
            await session.refresh(config)
            return self._llm_config_to_dict(config)

    async def update_llm_config(self, name: str, config_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing LLM configuration.

        Args:
            name: Configuration name
            config_data: Fields to update

        Returns:
            Updated configuration dict or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LLMConfigModel).where(LLMConfigModel.name == name)
            )
            config = result.scalar_one_or_none()
            if not config:
                return None

            field_map = {
                "provider": "provider",
                "model": "model",
                "endpoint": "endpoint",
                "baseUrl": "base_url",
                "base_url": "base_url",
                "temperature": "temperature",
                "maxTokens": "max_tokens",
                "max_tokens": "max_tokens",
                "region": "region",
                "icon": "icon",
                "description": "description",
                "aws_profile": "aws_profile",
                "use_for_embeddings": "use_for_embeddings",
                "useForEmbeddings": "use_for_embeddings",
            }

            # Single-row exclusivity for the embedding flag. If the caller is
            # turning this row's flag on, clear it on every other row first.
            wants_embedding = bool(
                config_data.get("use_for_embeddings")
                or config_data.get("useForEmbeddings")
            )
            if wants_embedding:
                await session.execute(
                    update(LLMConfigModel)
                    .where(LLMConfigModel.name != name)
                    .values(use_for_embeddings=False)
                )

            for json_key, col_name in field_map.items():
                if json_key in config_data:
                    setattr(config, col_name, config_data[json_key])

            if "name" in config_data and config_data["name"] != name:
                config.name = config_data["name"]

            config.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(config)
            return self._llm_config_to_dict(config)

    async def upsert_llm_config(self, name: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update an LLM configuration by name.

        Args:
            name: Configuration name (used as the unique key)
            config_data: Fields to set

        Returns:
            The upserted config dict
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LLMConfigModel).where(LLMConfigModel.name == name)
            )
            config = result.scalar_one_or_none()
            if config:
                field_map = {
                    "provider": "provider",
                    "model": "model",
                    "endpoint": "endpoint",
                    "baseUrl": "base_url",
                    "base_url": "base_url",
                    "temperature": "temperature",
                    "maxTokens": "max_tokens",
                    "max_tokens": "max_tokens",
                    "region": "region",
                    "icon": "icon",
                    "description": "description",
                    "aws_profile": "aws_profile",
                }
                for json_key, col_name in field_map.items():
                    if json_key in config_data:
                        setattr(config, col_name, config_data[json_key])
                config.updated_at = datetime.now(timezone.utc)
            else:
                config = LLMConfigModel(
                    name=name,
                    provider=config_data.get("provider", ""),
                    model=config_data.get("model", ""),
                    endpoint=config_data.get("endpoint"),
                    base_url=config_data.get("baseUrl") or config_data.get("base_url"),
                    temperature=config_data.get("temperature", 0.7),
                    max_tokens=config_data.get("maxTokens") or config_data.get("max_tokens", 4096),
                    region=config_data.get("region", "us-east-1"),
                    icon=config_data.get("icon"),
                    description=config_data.get("description"),
                    aws_profile=config_data.get("aws_profile"),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(config)
            await session.commit()
            await session.refresh(config)
            return self._llm_config_to_dict(config)

    async def delete_llm_config(self, name: str) -> bool:
        """Delete an LLM configuration.

        Args:
            name: Configuration name

        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(LLMConfigModel).where(LLMConfigModel.name == name).returning(LLMConfigModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None

    async def llm_config_exists(self, name: str) -> bool:
        """Check if an LLM configuration exists.

        Args:
            name: Configuration name

        Returns:
            True if exists
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LLMConfigModel.id).where(LLMConfigModel.name == name)
            )
            return result.scalar_one_or_none() is not None

    def _llm_config_to_dict(self, config: LLMConfigModel) -> Dict[str, Any]:
        """Convert LLM config model to dictionary.

        Args:
            config: LLM config model
            include_api_key: If True, include the api_key field.

        Returns:
            Dictionary representation with both snake_case and camelCase keys
            for backward compatibility with the frontend.
        """
        # ORM may not have the column on older deployments — read defensively
        # so this dict serializer doesn't blow up the GET endpoint.
        use_for_embeddings = bool(getattr(config, "use_for_embeddings", False) or False)
        d = {
            "provider": config.provider,
            "model": config.model,
            "endpoint": config.endpoint,
            "base_url": config.base_url,
            "baseUrl": config.base_url,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "maxTokens": config.max_tokens,
            "region": config.region,
            "icon": config.icon,
            "description": config.description,
            "aws_profile": config.aws_profile,
            # Exposed under both casings so existing front-end paths work.
            "use_for_embeddings": use_for_embeddings,
            "useForEmbeddings": use_for_embeddings,
        }
        return d

    # ============================================
    # MODEL KEY OPERATIONS
    # ============================================

    async def list_mcp_servers(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        """List all MCP servers.
        
        Args:
            include_disabled: If True, include disabled servers
        
        Returns:
            List of MCP server configurations
        """
        async with AsyncSessionLocal() as session:
            query = select(MCPServerModel)
            if not include_disabled:
                query = query.where(MCPServerModel.enabled == True)
            
            result = await session.execute(query)
            servers = result.scalars().all()
            return [self._mcp_server_to_dict(server) for server in servers]

    async def get_mcp_server_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get MCP server by name.
        
        Args:
            name: Server name
            
        Returns:
            MCP server configuration or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MCPServerModel).where(MCPServerModel.name == name)
            )
            server = result.scalar_one_or_none()
            return self._mcp_server_to_dict(server) if server else None

    async def create_mcp_server(self, server_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new MCP server.
        
        Args:
            server_data: Server configuration
            
        Returns:
            Created server configuration
        """
        async with AsyncSessionLocal() as session:
            server = MCPServerModel(
                name=server_data["name"],
                command=server_data["command"],
                args=server_data.get("args", []),
                env=server_data.get("env", {}),
                enabled=server_data.get("enabled", True),
                description=server_data.get("description"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return self._mcp_server_to_dict(server)

    async def update_mcp_server(self, name: str, server_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an MCP server.
        
        Args:
            name: Server name
            server_data: Updated server data
            
        Returns:
            Updated server configuration or None if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MCPServerModel).where(MCPServerModel.name == name)
            )
            server = result.scalar_one_or_none()
            if not server:
                return None

            # Update fields
            if "command" in server_data:
                server.command = server_data["command"]
            if "args" in server_data:
                server.args = server_data["args"]
            if "env" in server_data:
                server.env = server_data["env"]
            if "enabled" in server_data:
                server.enabled = server_data["enabled"]
            if "description" in server_data:
                server.description = server_data["description"]
            
            # Handle rename
            if "name" in server_data and server_data["name"] != name:
                server.name = server_data["name"]
            
            server.updated_at = datetime.now(timezone.utc)
            
            await session.commit()
            await session.refresh(server)
            return self._mcp_server_to_dict(server)

    async def delete_mcp_server(self, name: str) -> bool:
        """Delete an MCP server.
        
        Args:
            name: Server name
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(MCPServerModel).where(MCPServerModel.name == name).returning(MCPServerModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None

    async def mcp_server_exists(self, name: str) -> bool:
        """Check if an MCP server exists.
        
        Args:
            name: Server name
            
        Returns:
            True if server exists, False otherwise
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MCPServerModel.id).where(MCPServerModel.name == name)
            )
            return result.scalar_one_or_none() is not None

    def _mcp_server_to_dict(self, server: MCPServerModel) -> Dict[str, Any]:
        """Convert MCP server model to dictionary.
        
        Args:
            server: MCP server model
            
        Returns:
            Dictionary representation
        """
        return {
            "id": server.id,
            "name": server.name,
            "command": server.command,
            "args": server.args or [],
            "env": server.env or {},
            "enabled": server.enabled,
            "description": server.description,
            "created_at": server.created_at.isoformat() if server.created_at else None,
            "updated_at": server.updated_at.isoformat() if server.updated_at else None
        }

    # ============================================
    # MODEL KEY OPERATIONS
    # ============================================

    async def list_model_keys(self, include_secrets: bool = False) -> List[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ModelKeyModel))
            keys = result.scalars().all()
            return [self._model_key_to_dict(k, include_secrets) for k in keys]

    async def get_model_key(self, provider: str, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
        # Alias-aware lookup — discover-models sends the UI label ("AWS Bedrock")
        # but the row may have been written with a legacy spelling ("bedrock").
        # See ``app.infrastructure.persistence.model_key_repository._provider_aliases``.
        from app.infrastructure.persistence.model_key_repository import _provider_aliases
        aliases = _provider_aliases(provider)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelKeyModel).where(ModelKeyModel.provider.in_(aliases))
            )
            k = result.scalars().first()
            return self._model_key_to_dict(k, include_secrets) if k else None

    async def create_model_key(self, data: Dict[str, Any]) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            key = ModelKeyModel(
                provider=data["provider"],
                api_key=data.get("api_key"),
                secret_key=data.get("secret_key"),
                endpoint=data.get("endpoint"),
                region=data.get("region"),
                access_key_id=data.get("access_key_id"),
                secret_access_key=data.get("secret_access_key"),
                session_token=data.get("session_token"),
                description=data.get("description"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(key)
            await session.commit()
            await session.refresh(key)
            return self._model_key_to_dict(key, include_secrets=True)

    async def update_model_key(self, provider: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Defensive: drop any secret-field value that looks like the masked
        # form returned by ``mask_value`` (e.g. ``sk-p...AAAA``). Without
        # this, a UI list/edit round-trip overwrites the real credential
        # with the displayed mask — the symptom we hit with the OpenAI key.
        _SECRET_FIELDS = {
            "api_key", "secret_key", "access_key_id", "secret_access_key",
            "session_token",
        }
        data = {k: v for k, v in data.items()
                if not (k in _SECRET_FIELDS and _looks_masked(v))}

        existing = await self.get_model_key(provider, include_secrets=False)
        if not existing:
            return None
        resolved_provider = existing["provider"]

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelKeyModel).where(ModelKeyModel.provider == resolved_provider)
            )
            key = result.scalar_one_or_none()
            if not key:
                return None
            for field in ("api_key", "secret_key", "endpoint", "region",
                         "access_key_id", "secret_access_key",
                         "session_token", "description"):
                if field in data:
                    setattr(key, field, data[field])
            key.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(key)
            return self._model_key_to_dict(key, include_secrets=True)

    async def upsert_model_key(self, provider: str, data: Dict[str, Any]) -> Dict[str, Any]:
        # Same masked-value guard as ``update_model_key`` — prevents the UI
        # round-trip mask from silently overwriting real credentials.
        _SECRET_FIELDS = {
            "api_key", "secret_key", "access_key_id", "secret_access_key",
            "session_token",
        }
        data = {k: v for k, v in data.items()
                if not (k in _SECRET_FIELDS and _looks_masked(v))}

        existing = await self.get_model_key(provider, include_secrets=False)
        resolved_provider = existing["provider"] if existing else provider

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelKeyModel).where(ModelKeyModel.provider == resolved_provider)
            )
            key = result.scalar_one_or_none()
            if key:
                for field in ("api_key", "secret_key", "endpoint", "region",
                             "access_key_id", "secret_access_key",
                             "session_token", "description"):
                    if field in data:
                        setattr(key, field, data[field])
                key.updated_at = datetime.now(timezone.utc)
            else:
                key = ModelKeyModel(
                    provider=provider,
                    api_key=data.get("api_key"),
                    secret_key=data.get("secret_key"),
                    endpoint=data.get("endpoint"),
                    region=data.get("region"),
                    access_key_id=data.get("access_key_id"),
                    secret_access_key=data.get("secret_access_key"),
                    session_token=data.get("session_token"),
                    description=data.get("description"),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(key)
            await session.commit()
            await session.refresh(key)
            return self._model_key_to_dict(key, include_secrets=True)

    async def delete_model_key(self, provider: str) -> bool:
        existing = await self.get_model_key(provider, include_secrets=False)
        if not existing:
            return False
        resolved_provider = existing["provider"]
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ModelKeyModel)
                .where(ModelKeyModel.provider == resolved_provider)
                .returning(ModelKeyModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None

    async def model_key_exists(self, provider: str) -> bool:
        row = await self.get_model_key(provider, include_secrets=False)
        return row is not None

    def _model_key_to_dict(self, key: ModelKeyModel, include_secrets: bool = False) -> Dict[str, Any]:
        d = {
            "provider": key.provider,
            "has_api_key": bool(key.api_key),
            "has_secret_key": bool(key.secret_key),
            "has_access_credentials": bool(key.access_key_id and key.secret_access_key),
            "endpoint": key.endpoint,
            "region": key.region,
            "description": key.description,
        }
        if include_secrets:
            d["api_key"] = key.api_key
            d["secret_key"] = key.secret_key
            d["access_key_id"] = key.access_key_id
            d["secret_access_key"] = key.secret_access_key
            d["session_token"] = key.session_token
        return d


# Singleton instance
db_repository = DatabaseRepository()
