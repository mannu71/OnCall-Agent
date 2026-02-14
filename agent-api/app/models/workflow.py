"""Workflow and task models."""
from datetime import datetime
from typing import Any, Dict, List, Optional, Union, Literal
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator


class TaskType(str, Enum):
    """Supported task types."""
    SHELL = "shell"
    PYTHON = "python"


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class WorkflowStatus(str, Enum):
    """Workflow execution status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class Task(BaseModel):
    """Individual task within a workflow."""
    name: str = Field(..., description="Unique task name")
    type: TaskType = Field(..., description="Task type (shell or python)")
    command: Optional[str] = Field(None, description="Shell command to execute")
    script: Optional[str] = Field(None, description="Python script to execute")
    timeout: int = Field(300, description="Task timeout in seconds")
    retry_count: int = Field(0, description="Number of retries on failure")
    retry_delay: int = Field(5, description="Delay between retries in seconds")

    @model_validator(mode='after')
    def validate_task_content(self) -> 'Task':
        """Ensure either command or script is provided based on task type."""
        if self.type == TaskType.SHELL and not self.command:
            raise ValueError("Shell tasks require a 'command' field")
        if self.type == TaskType.PYTHON and not self.script:
            raise ValueError("Python tasks require a 'script' field")
        return self

    class Config:
        use_enum_values = True


class Workflow(BaseModel):
    """Workflow definition - all workflows configured via UI with nodes and edges."""
    id: Optional[str] = Field(None, description="Unique workflow ID")
    name: str = Field(..., description="Unique workflow name")
    description: str = Field("", description="Workflow description")
    type: Literal["workflow"] = Field("workflow", description="Workflow type")
    schedule: Optional[str] = Field(None, description="Cron expression for scheduling")
    enabled: bool = Field(True, description="Whether the workflow is enabled")
    
    # Workflow structure (nodes and edges)
    nodes: Optional[List[Dict[str, Any]]] = Field(None, description="Workflow nodes")
    edges: Optional[List[Dict[str, Any]]] = Field(None, description="Workflow edges")
    
    # Legacy fields (for compatibility) - accept any task structure
    tasks: Optional[List[Dict[str, Any]]] = Field(None, description="Legacy tasks field")
    max_retries: int = Field(0, description="Max workflow retries on failure")
    timeout: int = Field(3600, description="Workflow timeout in seconds")
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    # UI Compatibility fields
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Ensure workflow name is valid."""
        if not v or not v.strip():
            raise ValueError("Workflow name cannot be empty")
        return v.strip()

    @field_validator('schedule')
    @classmethod
    def validate_schedule(cls, v: Optional[str]) -> Optional[str]:
        """Basic cron expression validation."""
        if v is None:
            return v
        parts = v.split()
        if len(parts) != 5:
            raise ValueError("Cron expression must have exactly 5 fields")
        return v

    @model_validator(mode='after')
    def validate_structure(self) -> 'Workflow':
        """Ensure the workflow has at least one node or legacy task."""
        if not (self.nodes or self.tasks):
            raise ValueError("Workflows must have at least one node or task")
        return self

    class Config:
        use_enum_values = True
        extra = "allow"  # Allow additional fields


class TaskResult(BaseModel):
    """Result of a task execution."""
    task_name: str
    status: TaskStatus
    output: Optional[str] = None
    error: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    class Config:
        use_enum_values = True


class WorkflowExecution(BaseModel):
    """Workflow execution record."""
    workflow_name: str
    execution_id: str
    status: WorkflowStatus
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    task_results: List[TaskResult] = []
    error: Optional[str] = None

    class Config:
        use_enum_values = True


class WorkflowExecutionEvent(BaseModel):
    """Real-time execution event for SSE streaming."""
    event_type: str = Field(..., description="Event type: workflow_start, task_start, task_complete, workflow_complete")
    workflow_name: str
    execution_id: str
    timestamp: datetime
    data: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        use_enum_values = True


class WorkflowCreate(BaseModel):
    """Request model for creating a workflow."""
    id: Optional[str] = None
    name: str
    description: str = ""
    type: Literal["workflow"] = "workflow"
    schedule: Optional[str] = None
    enabled: bool = True
    
    # Workflow structure
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    
    # Legacy fields
    tasks: Optional[List[Any]] = None
    max_retries: int = 0
    timeout: int = 3600
    
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    
    class Config:
        extra = "allow"


class WorkflowUpdate(BaseModel):
    """Request model for updating a workflow."""
    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[Literal["workflow"]] = None
    schedule: Optional[str] = None
    enabled: Optional[bool] = None
    tasks: Optional[List[Dict[str, Any]]] = None
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    max_retries: Optional[int] = None
    timeout: Optional[int] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    
    class Config:
        extra = "allow"


class WorkflowResponse(BaseModel):
    """Response model for workflow details."""
    id: Optional[str] = None
    name: str
    description: str
    type: Literal["workflow"]
    schedule: Optional[str] = None
    enabled: bool
    
    # Workflow structure
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    
    # Legacy fields
    tasks: Optional[List[Dict[str, Any]]] = None
    max_retries: Optional[int] = None
    timeout: Optional[int] = None
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    
    class Config:
        extra = "allow"


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    timestamp: datetime
    scheduler_running: bool
    active_workflows: int
