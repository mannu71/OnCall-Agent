"""Application configuration."""
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    
    # Context compression settings
    context_compression_enabled: bool = Field(
        default=True,
        description="Enable intelligent context compression for long conversations"
    )
    context_threshold_percent: float = Field(
        default=0.50,
        description="Token usage threshold (as fraction of context length) to trigger compression"
    )
    context_protect_first_n: int = Field(
        default=3,
        description="Number of initial messages (system prompt + first exchange) to protect from compression"
    )
    
    # Rate limit tracking settings
    rate_limit_tracking_enabled: bool = Field(
        default=True,
        description="Enable tracking of API rate limits from response headers"
    )
    rate_limit_warning_threshold: float = Field(
        default=0.80,
        description="Usage percentage threshold to trigger rate limit warnings"
    )
    
    # Auxiliary client settings (for side tasks like summarization)
    auxiliary_provider: str = Field(
        default="auto",
        description="Provider for auxiliary LLM tasks (auto, openrouter, anthropic, openai)"
    )
    auxiliary_model: str = Field(
        default="",
        description="Model to use for auxiliary tasks (empty = auto-select)"
    )
    auxiliary_base_url: str = Field(
        default="",
        description="Custom base URL for auxiliary provider"
    )
    
    # Skills and trajectories directories
    skills_dir: str = Field(
        default="data/skills",
        description="Directory containing skill SKILL.md files"
    )
    trajectories_dir: str = Field(
        default="data/trajectories",
        description="Directory for storing conversation trajectories"
    )
    
    # Agent execution settings
    agent_recursion_limit: int = Field(
        default=50,
        description="Maximum recursion depth for LangGraph agent execution (default: 50, LangGraph default: 25)"
    )
    agent_timeout_seconds: int = Field(
        default=300,
        description="Timeout in seconds for agent execution (default: 300 = 5 minutes)"
    )
    
    # Embedding settings
    embedding_provider: str = Field(
        default="bedrock",
        description="Provider for embedding generation (bedrock, openai, azure, cohere)"
    )
    embedding_model: str = Field(
        default="amazon.titan-embed-text-v1",
        description="Model ID for embedding generation"
    )
    embedding_region: str = Field(
        default="us-east-1",
        description="AWS region for Bedrock embeddings (only used for bedrock provider)"
    )
    embedding_dimensions: int = Field(
        default=1536,
        description="Embedding vector dimensions (1536 for Titan v1, 1024 for Titan v2, varies by model)"
    )
    
    @property
    def async_database_url(self) -> str:
        """Get async database URL for asyncpg."""
        return self.database_url.replace("postgresql://", "postgresql+asyncpg://")


settings = Settings()
