"""Unified persistence layer.

Canonical import path for all repository implementations.  Module-level
singletons are provided for callers that previously used ``db_repository``.
"""
from app.infrastructure.persistence.workflow_repository import WorkflowRepository
from app.infrastructure.persistence.execution_repository import ExecutionRepository
from app.infrastructure.persistence.llm_config_repository import LLMConfigRepository
from app.infrastructure.persistence.mcp_config_repository import MCPConfigRepository
from app.infrastructure.persistence.model_key_repository import ModelKeyRepository
from app.infrastructure.persistence.app_settings_repository import AppSettingsRepository

# Shared singletons — prefer injecting ``Repository()`` in tests.
workflow_repository = WorkflowRepository()
execution_repository = ExecutionRepository()
llm_config_repository = LLMConfigRepository()
mcp_config_repository = MCPConfigRepository()
model_key_repository = ModelKeyRepository()
app_settings_repository = AppSettingsRepository()

__all__ = [
    "WorkflowRepository",
    "ExecutionRepository",
    "LLMConfigRepository",
    "MCPConfigRepository",
    "ModelKeyRepository",
    "AppSettingsRepository",
    "workflow_repository",
    "execution_repository",
    "llm_config_repository",
    "mcp_config_repository",
    "model_key_repository",
    "app_settings_repository",
]
