"""Application configuration.

``Settings`` is assembled from one mixin per domain (``server``, ``aws``,
``cloudwatch``, ``agent``, ``memory``, ``tools``, ``governance``). The split is
purely organisational: 200+ fields in a single 1,100-line class had stopped
being readable, and its section headers had drifted far enough from their
contents that one "Tool execution sandbox" banner sat above every CloudWatch
knob in the file.

Nothing about the public surface changed. ``Settings`` still inherits every
field, so ``settings.<field>``, ``Settings.model_fields`` and every env var /
``validation_alias`` behave exactly as before — no call site needs to know a
field lives in a mixin. Add a new field to the mixin that owns its domain.

Validators stay HERE rather than in the mixins: ``_parse_bool_fields`` and
``_apply_lite_profile_defaults`` both span domains, and a validator only sees
fields of the class it is defined on.
"""
from __future__ import annotations

from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config._secrets import get_secret, get_secret_json
from app.config._shared import DEFAULT_DELEGATION_BLOCKED_TOOLS, parse_env_bool
from app.config.agent import AgentSettings
from app.config.aws import AwsSettings
from app.config.cloudwatch import CloudWatchSettings
from app.config.governance import GovernanceSettings
from app.config.memory import MemorySettings
from app.config.server import ServerSettings
from app.config.tools import ToolSettings

__all__ = [
    "Settings",
    "settings",
    "get_secret",
    "get_secret_json",
    "parse_env_bool",
    "DEFAULT_DELEGATION_BLOCKED_TOOLS",
]


class Settings(
    ServerSettings,
    AwsSettings,
    CloudWatchSettings,
    AgentSettings,
    MemorySettings,
    ToolSettings,
    GovernanceSettings,
):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )

    @field_validator(
        "aws_ssl_verify",
        "heartbeat_db_fallback",
        "parallel_flow_fail_fast",
        "guardrail_hard_stop",
        "supervisor_hitl_enabled",
        "supervisor_llm_scoring",
        "cloudwatch_auto_drilldown",
        "cloudwatch_metrics_fusion",
        "aws_secrets_manager_enabled",
        "sandbox_network",
        "routing_fallback_enabled",
        "semantic_memory_enabled",
        "memory_fact_extraction_enabled",
        "memory_audit_enabled",
        "self_improvement_enabled",
        "hillclimb_apply_enabled",
        "action_supervisor_enabled",
        "action_supervisor_shadow_mode",
        "wiki_publish_approval_enabled",
        mode="before",
    )
    @classmethod
    def _parse_bool_fields(cls, value: Any) -> bool:
        return parse_env_bool(value)

    @field_validator(
        "bedrock_fallback_regions",
        "bedrock_model_fallback",
        mode="before",
    )
    @classmethod
    def _parse_csv_list(cls, value: Any) -> Any:
        """Accept a comma-separated string (env) or a real list (default)."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _apply_lite_profile_defaults(self) -> "Settings":
        """When APP_PROFILE=lite, flip heavy features off — unless explicitly set.

        Only fields the operator did NOT provide (via env/.env, tracked by
        ``model_fields_set``) are changed, so an explicit override always wins.
        'full' (default) preserves today's behaviour exactly. No code is removed —
        this only changes defaults for a minimal-footprint runtime.
        """
        if (self.app_profile or "full").lower() != "lite":
            return self
        lite_defaults = {
            "semantic_memory_enabled": False,
            "sandbox_backend": "disabled",
            "cloudwatch_auto_escalate": False,
            "cloudwatch_auto_drilldown": False,
            "startup_indexing_recovery_enabled": False,
            "compression_enabled": False,
        }
        for name, value in lite_defaults.items():
            if name not in self.model_fields_set:
                setattr(self, name, value)
        return self

    @property
    def async_database_url(self) -> str:
        """Get async database URL for asyncpg."""
        return self.database_url.replace("postgresql://", "postgresql+asyncpg://")

    @property
    def effective_bedrock_region(self) -> str:
        return self.bedrock_region or self.aws_region


settings = Settings()
