"""Error handling middleware for FastAPI."""
import logging
import uuid
from datetime import datetime
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AppException,
    NotFoundException,
    ValidationException,
    WorkflowException,
    ExecutionException,
    StorageException,
    ConfigurationException,
    ReleaseManagementException,
    AzureDevOpsException,
    AzureWikiException,
    GitOperationException,
    ConflictResolutionException,
    CredentialException
)
from app.services.azure_devops_client import (
    AzureDevOpsError,
    AzureDevOpsConnectionError,
    AzureDevOpsAuthenticationError,
    AzureDevOpsNotFoundError,
    AzureDevOpsRateLimitError
)
from app.services.azure_wiki_client import (
    AzureWikiError,
    AzureWikiConnectionError,
    AzureWikiAuthenticationError,
    AzureWikiNotFoundError,
    AzureWikiConflictError
)
from app.services.release_manager import (
    ReleaseManagerError,
    ReleaseValidationError,
    ReleaseCreationError,
    ReleaseCredentialsError
)

logger = logging.getLogger(__name__)


def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handle application exceptions.
    
    Args:
        request: FastAPI request
        exc: Application exception
        
    Returns:
        JSON error response
    """
    # Map exception types to HTTP status codes
    status_code_map = {
        NotFoundException: status.HTTP_404_NOT_FOUND,
        ValidationException: status.HTTP_400_BAD_REQUEST,
        WorkflowException: status.HTTP_422_UNPROCESSABLE_ENTITY,
        ExecutionException: status.HTTP_500_INTERNAL_SERVER_ERROR,
        StorageException: status.HTTP_500_INTERNAL_SERVER_ERROR,
        ConfigurationException: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    
    status_code = status_code_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    logger.error(
        f"{exc.__class__.__name__}: {exc.message}",
        extra={"details": exc.details, "path": request.url.path}
    )
    
    return JSONResponse(
        status_code=status_code,
        content={
            "error": exc.__class__.__name__,
            "message": exc.message,
            "details": exc.details
        }
    )


def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle validation errors.
    
    Args:
        request: FastAPI request
        exc: Validation error
        
    Returns:
        JSON error response
    """
    errors = exc.errors()
    logger.warning(
        f"Validation error on {request.url.path}: {errors}"
    )
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "ValidationError",
            "message": "Request validation failed",
            "details": exc.errors()
        }
    )


def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle HTTP exceptions.
    
    Args:
        request: FastAPI request
        exc: HTTP exception
        
    Returns:
        JSON error response
    """
    logger.warning(
        f"HTTP {exc.status_code} on {request.url.path}: {exc.detail}"
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTPException",
            "message": exc.detail
        }
    )


def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions.
    
    Args:
        request: FastAPI request
        exc: Exception
        
    Returns:
        JSON error response
    """
    logger.exception(
        f"Unexpected error on {request.url.path}",
        exc_info=exc
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred"
        }
    )


def azure_devops_exception_handler(request: Request, exc: AzureDevOpsError) -> JSONResponse:
    """Handle Azure DevOps API exceptions.
    
    Args:
        request: FastAPI request
        exc: Azure DevOps exception
        
    Returns:
        JSON error response
        
    Requirements: 12.3, 12.4
    """
    # Map exception types to HTTP status codes
    status_code_map = {
        AzureDevOpsAuthenticationError: status.HTTP_401_UNAUTHORIZED,
        AzureDevOpsNotFoundError: status.HTTP_404_NOT_FOUND,
        AzureDevOpsRateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
        AzureDevOpsConnectionError: status.HTTP_502_BAD_GATEWAY,
        AzureDevOpsError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    
    status_code = status_code_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    logger.error(
        f"Azure DevOps error on {request.url.path}: {exc.__class__.__name__}: {str(exc)}",
        extra={
            "error_type": exc.__class__.__name__,
            "path": request.url.path,
            "status_code": status_code
        }
    )
    
    return JSONResponse(
        status_code=status_code,
        content={
            "error": exc.__class__.__name__,
            "message": str(exc),
            "details": {"service": "Azure DevOps"},
            "timestamp": datetime.utcnow().isoformat(),
            "request_id": str(uuid.uuid4())
        }
    )


def azure_wiki_exception_handler(request: Request, exc: AzureWikiError) -> JSONResponse:
    """Handle Azure Wiki API exceptions.
    
    Args:
        request: FastAPI request
        exc: Azure Wiki exception
        
    Returns:
        JSON error response
        
    Requirements: 12.3, 12.4
    """
    # Map exception types to HTTP status codes
    status_code_map = {
        AzureWikiAuthenticationError: status.HTTP_401_UNAUTHORIZED,
        AzureWikiNotFoundError: status.HTTP_404_NOT_FOUND,
        AzureWikiConflictError: status.HTTP_409_CONFLICT,
        AzureWikiConnectionError: status.HTTP_502_BAD_GATEWAY,
        AzureWikiError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    
    status_code = status_code_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    logger.error(
        f"Azure Wiki error on {request.url.path}: {exc.__class__.__name__}: {str(exc)}",
        extra={
            "error_type": exc.__class__.__name__,
            "path": request.url.path,
            "status_code": status_code
        }
    )
    
    return JSONResponse(
        status_code=status_code,
        content={
            "error": exc.__class__.__name__,
            "message": str(exc),
            "details": {"service": "Azure Wiki"},
            "timestamp": datetime.utcnow().isoformat(),
            "request_id": str(uuid.uuid4())
        }
    )


def release_manager_exception_handler(request: Request, exc: ReleaseManagerError) -> JSONResponse:
    """Handle release manager exceptions.
    
    Args:
        request: FastAPI request
        exc: Release manager exception
        
    Returns:
        JSON error response
        
    Requirements: 12.3, 12.4
    """
    # Map exception types to HTTP status codes
    status_code_map = {
        ReleaseValidationError: status.HTTP_400_BAD_REQUEST,
        ReleaseCredentialsError: status.HTTP_401_UNAUTHORIZED,
        ReleaseCreationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        ReleaseManagerError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    
    status_code = status_code_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    logger.error(
        f"Release manager error on {request.url.path}: {exc.__class__.__name__}: {str(exc)}",
        extra={
            "error_type": exc.__class__.__name__,
            "path": request.url.path,
            "status_code": status_code
        }
    )
    
    return JSONResponse(
        status_code=status_code,
        content={
            "error": exc.__class__.__name__,
            "message": str(exc),
            "details": {"service": "Release Manager"},
            "timestamp": datetime.utcnow().isoformat(),
            "request_id": str(uuid.uuid4())
        }
    )


def register_exception_handlers(app):
    """Register all exception handlers with the FastAPI app.
    
    Args:
        app: FastAPI application instance
        
    Requirements: 12.3, 12.4
    """
    # Register Azure Release Management exception handlers
    app.add_exception_handler(AzureDevOpsError, azure_devops_exception_handler)
    app.add_exception_handler(AzureWikiError, azure_wiki_exception_handler)
    app.add_exception_handler(ReleaseManagerError, release_manager_exception_handler)
    
    # Register general application exception handlers
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
