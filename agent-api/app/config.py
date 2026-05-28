"""Application configuration."""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# AWS Secrets Manager loader
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def get_secret(key: str, region: str = "us-east-1") -> Optional[str]:
    """Retrieve a secret value from AWS Secrets Manager, with env-var fallback.

    In development (or when ``AWS_SECRETS_MANAGER_ENABLED`` is not 'true'),
    the env var matching *key* is returned directly — no AWS call is made.

    The result is cached per *(key, region)* for the lifetime of the process.
    Call ``get_secret.cache_clear()`` in tests to reset.

    Args:
        key: Secret name / ARN in Secrets Manager.  Also used as the env-var
             name looked up in the fallback path.
        region: AWS region where the secret is stored.

    Returns:
        The secret string, or ``None`` if not found anywhere.
    """
    if os.getenv("AWS_SECRETS_MANAGER_ENABLED", "").lower() != "true":
        value = os.getenv(key)
        if value is None:
            logger.debug("get_secret: env var '%s' not set (dev mode)", key)
        return value

    try:
        import boto3  # type: ignore
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=key)
        secret = response.get("SecretString") or response.get("SecretBinary")
        if isinstance(secret, bytes):
            secret = secret.decode("utf-8")
        # If the secret is a JSON blob, callers get the raw JSON string —
        # use get_secret_json() below to parse it.
        return secret
    except Exception as exc:
        logger.warning("get_secret: failed to retrieve '%s': %s", key, exc)
        return os.getenv(key)  # final fallback to env var


def get_secret_json(key: str, region: str = "us-east-1") -> Optional[dict]:
    """Like :func:`get_secret` but JSON-parses the result.

    Returns ``None`` when the secret is absent or not valid JSON.
    """
    raw = get_secret(key, region)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("get_secret_json: secret '%s' is not valid JSON", key)
        return None


class Settings(BaseSettings):
    """Application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )
    
    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"
    
    # Database
    database_url: str = Field(
        default="postgresql://kycuser:kycpassword@localhost:5432/kycagent",
        description="PostgreSQL database URL"
    )
    db_pool_size: int = Field(
        default=20,
        description="SQLAlchemy async engine pool_size. Sized for parallel DAG node execution.",
    )
    db_max_overflow: int = Field(
        default=40,
        description="SQLAlchemy async engine max_overflow above pool_size.",
    )
    
    # CORS settings - configurable for production
    cors_origins: List[str] = Field(
        default=["*"],
        description="Allowed CORS origins. Set to specific domains in production."
    )
    
    # Storage paths (kept for backward compatibility, but database is preferred)
    storage_path: str = "data/storage"
    workflow_dir: str = "data/workflows"
    logs_dir: str = "data/logs"
    
    # Scheduler settings
    scheduler_timezone: str = "UTC"
    max_concurrent_workflows: int = 5
    
    @property
    def async_database_url(self) -> str:
        """Get async database URL for asyncpg."""
        return self.database_url.replace("postgresql://", "postgresql+asyncpg://")


settings = Settings()
