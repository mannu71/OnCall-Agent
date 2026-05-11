"""
Error Response Models

This module defines error response models for API endpoints.

Requirements: 12.3, 12.4
"""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
import uuid


class ErrorResponse(BaseModel):
    """
    Standard error response model for API endpoints.
    
    Provides consistent error structure across all endpoints with:
    - Error type classification
    - User-friendly message
    - Additional context details
    - Timestamp for tracking
    - Request ID for tracing
    
    Requirements: 12.3, 12.4
    """
    error: str = Field(..., description="Error type or category")
    message: str = Field(..., description="User-friendly error message")
    details: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional error context and details"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.utcnow(),
        description="Error occurrence timestamp"
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique request identifier for tracing"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "error": "AzureDevOpsConnectionError",
                "message": "Failed to connect to Azure DevOps API",
                "details": {
                    "organization": "my-org",
                    "project": "my-project",
                    "reason": "Network timeout"
                },
                "timestamp": "2024-01-15T10:30:00Z",
                "request_id": "550e8400-e29b-41d4-a716-446655440000"
            }
        }


class ValidationErrorDetail(BaseModel):
    """
    Detailed validation error information.
    
    Provides specific information about validation failures including:
    - Field location in the request
    - Error message
    - Error type
    
    Requirements: 12.3
    """
    loc: list = Field(..., description="Location of the error in the request")
    msg: str = Field(..., description="Error message")
    type: str = Field(..., description="Error type")


class ValidationErrorResponse(BaseModel):
    """
    Validation error response model.
    
    Used for request validation failures with detailed field-level errors.
    
    Requirements: 12.3
    """
    error: str = Field(default="ValidationError", description="Error type")
    message: str = Field(
        default="Request validation failed",
        description="User-friendly error message"
    )
    details: list[ValidationErrorDetail] = Field(
        ...,
        description="List of validation errors"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.utcnow(),
        description="Error occurrence timestamp"
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique request identifier for tracing"
    )
