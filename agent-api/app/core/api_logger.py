"""
API Request/Response Logging

This module provides utilities for logging API requests and responses
with full context for debugging and monitoring.

Requirements: 12.4
"""

import logging
import time
from typing import Optional, Dict, Any
from datetime import datetime


class APILogger:
    """
    Logger for API requests and responses.
    
    Provides structured logging for:
    - API requests (method, URL, headers, body)
    - API responses (status, headers, body)
    - Request duration
    - Error details
    
    Requirements: 12.4
    """
    
    def __init__(self, service_name: str):
        """
        Initialize API logger.
        
        Args:
            service_name: Name of the service (e.g., "AzureDevOps", "AzureWiki")
        """
        self.service_name = service_name
        self.logger = logging.getLogger(f"api.{service_name}")
    
    def log_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Any] = None,
        request_id: Optional[str] = None
    ) -> float:
        """
        Log an API request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            headers: Request headers (PAT will be redacted)
            body: Request body
            request_id: Optional request ID for tracing
            
        Returns:
            Start time for duration calculation
            
        Requirements: 12.4
        """
        start_time = time.time()
        
        # Redact sensitive headers
        safe_headers = self._redact_headers(headers) if headers else {}
        
        self.logger.info(
            f"API Request: {method} {url}",
            extra={
                "service": self.service_name,
                "method": method,
                "url": url,
                "headers": safe_headers,
                "body": body if body else None,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        
        return start_time
    
    def log_response(
        self,
        method: str,
        url: str,
        status_code: int,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Any] = None,
        start_time: Optional[float] = None,
        request_id: Optional[str] = None
    ):
        """
        Log an API response.
        
        Args:
            method: HTTP method
            url: Request URL
            status_code: Response status code
            headers: Response headers
            body: Response body (will be truncated if large)
            start_time: Request start time for duration calculation
            request_id: Optional request ID for tracing
            
        Requirements: 12.4
        """
        duration = time.time() - start_time if start_time else None
        
        # Truncate large response bodies
        safe_body = self._truncate_body(body) if body else None
        
        log_level = logging.INFO if status_code < 400 else logging.WARNING
        
        self.logger.log(
            log_level,
            f"API Response: {method} {url} - {status_code}",
            extra={
                "service": self.service_name,
                "method": method,
                "url": url,
                "status_code": status_code,
                "headers": headers if headers else {},
                "body": safe_body,
                "duration_ms": round(duration * 1000, 2) if duration else None,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def log_error(
        self,
        method: str,
        url: str,
        error: Exception,
        start_time: Optional[float] = None,
        request_id: Optional[str] = None
    ):
        """
        Log an API error.
        
        Args:
            method: HTTP method
            url: Request URL
            error: Exception that occurred
            start_time: Request start time for duration calculation
            request_id: Optional request ID for tracing
            
        Requirements: 12.4
        """
        duration = time.time() - start_time if start_time else None
        
        self.logger.error(
            f"API Error: {method} {url} - {error.__class__.__name__}: {str(error)}",
            extra={
                "service": self.service_name,
                "method": method,
                "url": url,
                "error_type": error.__class__.__name__,
                "error_message": str(error),
                "duration_ms": round(duration * 1000, 2) if duration else None,
                "request_id": request_id,
                "timestamp": datetime.utcnow().isoformat()
            },
            exc_info=True
        )
    
    def _redact_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """
        Redact sensitive information from headers.
        
        Args:
            headers: Original headers
            
        Returns:
            Headers with sensitive values redacted
        """
        sensitive_keys = ["authorization", "x-api-key", "api-key", "token"]
        
        redacted = {}
        for key, value in headers.items():
            if key.lower() in sensitive_keys:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = value
        
        return redacted
    
    def _truncate_body(self, body: Any, max_length: int = 1000) -> Any:
        """
        Truncate large response bodies for logging.
        
        Args:
            body: Response body
            max_length: Maximum length for string bodies
            
        Returns:
            Truncated body
        """
        if isinstance(body, str) and len(body) > max_length:
            return body[:max_length] + f"... (truncated, total length: {len(body)})"
        elif isinstance(body, dict):
            # For dict bodies, just log the keys
            return {"keys": list(body.keys()), "truncated": True}
        elif isinstance(body, list) and len(body) > 10:
            return {"count": len(body), "truncated": True}
        else:
            return body


class GitOperationLogger:
    """
    Logger for Git operations.
    
    Provides structured logging for:
    - Branch creation
    - Commit application
    - Conflict detection
    - Merge operations
    
    Requirements: 12.4
    """
    
    def __init__(self):
        """Initialize Git operation logger."""
        self.logger = logging.getLogger("git.operations")
    
    def log_branch_creation(
        self,
        branch_name: str,
        base_branch: str,
        success: bool,
        error: Optional[str] = None
    ):
        """
        Log branch creation operation.
        
        Args:
            branch_name: Name of the branch being created
            base_branch: Base branch
            success: Whether operation succeeded
            error: Error message if failed
            
        Requirements: 12.4
        """
        log_level = logging.INFO if success else logging.ERROR
        
        self.logger.log(
            log_level,
            f"Branch creation: {branch_name} from {base_branch} - "
            f"{'Success' if success else 'Failed'}",
            extra={
                "operation": "branch_creation",
                "branch_name": branch_name,
                "base_branch": base_branch,
                "success": success,
                "error": error,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def log_commit_application(
        self,
        branch_name: str,
        commit_id: str,
        success: bool,
        conflict: bool = False,
        error: Optional[str] = None
    ):
        """
        Log commit application (cherry-pick) operation.
        
        Args:
            branch_name: Target branch name
            commit_id: Commit being applied
            success: Whether operation succeeded
            conflict: Whether conflict occurred
            error: Error message if failed
            
        Requirements: 12.4
        """
        if conflict:
            log_level = logging.WARNING
            status = "Conflict"
        elif success:
            log_level = logging.INFO
            status = "Success"
        else:
            log_level = logging.ERROR
            status = "Failed"
        
        self.logger.log(
            log_level,
            f"Commit application: {commit_id[:8]} to {branch_name} - {status}",
            extra={
                "operation": "commit_application",
                "branch_name": branch_name,
                "commit_id": commit_id,
                "success": success,
                "conflict": conflict,
                "error": error,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def log_conflict_resolution(
        self,
        file_path: str,
        resolution_type: str,
        success: bool,
        error: Optional[str] = None
    ):
        """
        Log conflict resolution operation.
        
        Args:
            file_path: Path to conflicting file
            resolution_type: Type of resolution (ours, theirs, manual)
            success: Whether operation succeeded
            error: Error message if failed
            
        Requirements: 12.4
        """
        log_level = logging.INFO if success else logging.ERROR
        
        self.logger.log(
            log_level,
            f"Conflict resolution: {file_path} using '{resolution_type}' - "
            f"{'Success' if success else 'Failed'}",
            extra={
                "operation": "conflict_resolution",
                "file_path": file_path,
                "resolution_type": resolution_type,
                "success": success,
                "error": error,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
