"""Custom exceptions for the application."""
from typing import Optional, Any, Dict


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
