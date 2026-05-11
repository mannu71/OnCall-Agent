"""Application layer: Aggregated exceptions from various core modules.

This module serves as the unified application-level exception module,
centralizing all custom exceptions for the agent-api application.
"""

from typing import Optional, Any, Dict


# --------------------------------------------------------------------------- #
# Base exceptions
# --------------------------------------------------------------------------- #

class AppException(Exception):
    """Base exception for all application exceptions."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        """Initialize exception.

        Args:
            message: Error message
            details: Additional error details
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundException(AppException):
    """Exception raised when a resource is not found."""
    pass


class ValidationException(AppException):
    """Exception raised when validation fails."""
    pass


class WorkflowException(AppException):
    """Exception raised for workflow-related errors."""
    pass


class ExecutionException(AppException):
    """Exception raised for execution-related errors."""
    pass


class StorageException(AppException):
    """Exception raised for storage-related errors."""
    pass


class ConfigurationException(AppException):
    """Exception raised for configuration errors."""
    pass


# --------------------------------------------------------------------------- #
# Azure Release Management Exceptions
# --------------------------------------------------------------------------- #

class ReleaseManagementException(AppException):
    """Base exception for Azure Release Management errors."""
    pass


class AzureDevOpsException(ReleaseManagementException):
    """Exception raised for Azure DevOps API errors."""
    pass


class AzureWikiException(ReleaseManagementException):
    """Exception raised for Azure Wiki API errors."""
    pass


class GitOperationException(ReleaseManagementException):
    """Exception raised for Git operation errors."""
    pass


class ConflictResolutionException(ReleaseManagementException):
    """Exception raised for conflict resolution errors."""
    pass


class CredentialException(ReleaseManagementException):
    """Exception raised for credential-related errors."""
    pass