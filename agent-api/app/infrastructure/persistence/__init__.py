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
from app.infrastructure.persistence.tool_approval_repository import ToolApprovalRepository
from app.infrastructure.persistence.model_role_repository import ModelRoleRepository
from app.infrastructure.persistence.mcp_role_repository import MCPRoleRepository
from app.infrastructure.persistence.session_repository import SessionRepository
from app.infrastructure.persistence.agent_profile_repository import AgentProfileRepository
from app.infrastructure.persistence.execution_scratch_repository import ExecutionScratchRepository
from app.infrastructure.persistence.failure_ledger_repository import FailureLedgerRepository
from app.infrastructure.persistence.trajectory_event_repository import TrajectoryEventRepository

# Shared singletons — prefer injecting ``Repository()`` in tests.
workflow_repository = WorkflowRepository()
execution_repository = ExecutionRepository()
llm_config_repository = LLMConfigRepository()
mcp_config_repository = MCPConfigRepository()
model_key_repository = ModelKeyRepository()
app_settings_repository = AppSettingsRepository()
tool_approval_repository = ToolApprovalRepository()
model_role_repository = ModelRoleRepository()
mcp_role_repository = MCPRoleRepository()
session_repository = SessionRepository()
agent_profile_repository = AgentProfileRepository()
execution_scratch_repository = ExecutionScratchRepository()
failure_ledger_repository = FailureLedgerRepository()
trajectory_event_repository = TrajectoryEventRepository()

__all__ = [
    "WorkflowRepository",
    "ExecutionRepository",
    "LLMConfigRepository",
    "MCPConfigRepository",
    "ModelKeyRepository",
    "AppSettingsRepository",
    "ToolApprovalRepository",
    "ModelRoleRepository",
    "MCPRoleRepository",
    "SessionRepository",
    "AgentProfileRepository",
    "ExecutionScratchRepository",
    "FailureLedgerRepository",
    "TrajectoryEventRepository",
    "workflow_repository",
    "execution_repository",
    "llm_config_repository",
    "mcp_config_repository",
    "model_key_repository",
    "app_settings_repository",
    "tool_approval_repository",
    "model_role_repository",
    "mcp_role_repository",
    "session_repository",
    "execution_scratch_repository",
    "failure_ledger_repository",
    "trajectory_event_repository",
]
