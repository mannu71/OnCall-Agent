"""SQLAlchemy database models."""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON, Float
from sqlalchemy.orm import declarative_base
from pgvector.sqlalchemy import Vector

# Import Base from database module
from app.core.database import Base


class WorkflowModel(Base):
    """Workflow database model."""
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text)
    nodes = Column(JSON, nullable=False)
    edges = Column(JSON, nullable=False)
    viewport = Column(JSON)
    enabled = Column(Boolean, default=True)
    schedule = Column(String(100))  # Cron expression
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class ExecutionModel(Base):
    """Execution history database model."""
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer)
    workflow_name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    duration_ms = Column(Integer)
    input = Column(JSON)
    output = Column(JSON)
    error = Column(Text)
    logs = Column(JSON)
    trajectory = Column(JSON)  # Full message trace for analysis and training


class LLMConfigModel(Base):
    """LLM configuration database model."""
    __tablename__ = "llm_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    provider = Column(String(100), nullable=False)
    model = Column(String(255), nullable=False)
    endpoint = Column(String(500))
    base_url = Column(String(500))
    temperature = Column(Float, default=0.7)
    max_tokens = Column(Integer, default=4096)
    region = Column(String(50), default="us-east-1")
    icon = Column(String(10))
    description = Column(Text)
    aws_profile = Column(String(100))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class ModelKeyModel(Base):
    """Provider-level API key storage model."""
    __tablename__ = "model_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(100), unique=True, nullable=False)
    api_key = Column(String(500))
    secret_key = Column(String(500))
    endpoint = Column(String(500))
    region = Column(String(50))
    access_key_id = Column(String(500))
    secret_access_key = Column(String(500))
    session_token = Column(String(500))
    description = Column(Text)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class MCPServerModel(Base):
    """MCP server configuration database model."""
    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    command = Column(String(500), nullable=False)
    args = Column(JSON)
    env = Column(JSON)
    enabled = Column(Boolean, default=True)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class LogPatternModel(Base):
    """Log pattern database model for RAG."""
    __tablename__ = "log_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    pattern = Column(Text, nullable=False)
    pattern_type = Column(String(50), nullable=False)
    severity = Column(Integer, default=1)
    description = Column(Text)
    embedding = Column(Vector(1536))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class KnownIssueModel(Base):
    """Known issue database model for RAG."""
    __tablename__ = "known_issues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    symptoms = Column(JSON)  # Array of strings
    solution = Column(Text)
    category = Column(String(100))
    source = Column(String(50), default="manual")  # manual | agent | verified
    embedding = Column(Vector(1536))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class BaselineMetricModel(Base):
    """Baseline metric database model."""
    __tablename__ = "baseline_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_name = Column(String(255), nullable=False)
    log_group = Column(String(255), nullable=False)
    normal_range_min = Column(Float)
    normal_range_max = Column(Float)
    threshold_warning = Column(Float)
    threshold_critical = Column(Float)
    time_window = Column(String(50))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class AnalysisHistoryModel(Base):
    """Analysis history database model."""
    __tablename__ = "analysis_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    log_group = Column(String(255), nullable=False)
    analysis_type = Column(String(100), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    summary = Column(Text)
    anomalies_found = Column(Integer, default=0)
    patterns_matched = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True))


class AlertModel(Base):
    """Alert database model."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    log_group = Column(String(255), nullable=False)
    alert_type = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON)
    status = Column(String(20), default="new")
    created_at = Column(DateTime(timezone=True))
    resolved_at = Column(DateTime(timezone=True))
    resolved_by = Column(String(255))
