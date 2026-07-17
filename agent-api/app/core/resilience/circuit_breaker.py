"""
Circuit Breaker Pattern Implementation

This module provides a circuit breaker pattern for handling repeated failures
in external service calls, preventing cascading failures.

Requirements: 3.6, 12.3
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Callable, Optional, Any
from functools import wraps


logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """
    Circuit breaker for external service calls.
    
    Implements the circuit breaker pattern to prevent cascading failures:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, reject requests immediately
    - HALF_OPEN: Testing if service recovered, allow limited requests
    
    Features:
    - Configurable failure threshold
    - Configurable timeout before retry
    - Automatic state transitions
    - Failure rate tracking
    
    Requirements: 3.6, 12.3
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Circuit breaker name for logging
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            expected_exception: Exception type to catch
            
        Requirements: 3.6, 12.3
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.success_count = 0
        
        self.logger = logging.getLogger(f"circuit_breaker.{name}")
        self.logger.info(
            f"Circuit breaker '{name}' initialized: "
            f"threshold={failure_threshold}, timeout={recovery_timeout}s"
        )
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Original exception if circuit is closed
            
        Requirements: 3.6, 12.3
        """
        # Check if circuit should transition from OPEN to HALF_OPEN
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.logger.info(f"Circuit '{self.name}' transitioning to HALF_OPEN")
                self.state = CircuitState.HALF_OPEN
            else:
                self.logger.warning(
                    f"Circuit '{self.name}' is OPEN, rejecting request"
                )
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is open. "
                    f"Service is unavailable."
                )
        
        try:
            # Execute function
            result = func(*args, **kwargs)
            
            # Record success
            self._on_success()
            
            return result
            
        except self.expected_exception as e:
            # Record failure
            self._on_failure()
            
            # Re-raise exception
            raise
    
    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute async function with circuit breaker protection.
        
        Args:
            func: Async function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Original exception if circuit is closed
            
        Requirements: 3.6, 12.3
        """
        # Check if circuit should transition from OPEN to HALF_OPEN
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.logger.info(f"Circuit '{self.name}' transitioning to HALF_OPEN")
                self.state = CircuitState.HALF_OPEN
            else:
                self.logger.warning(
                    f"Circuit '{self.name}' is OPEN, rejecting request"
                )
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is open. "
                    f"Service is unavailable."
                )
        
        try:
            # Execute async function
            result = await func(*args, **kwargs)
            
            # Record success
            self._on_success()
            
            return result
            
        except self.expected_exception as e:
            # Record failure
            self._on_failure()
            
            # Re-raise exception
            raise
    
    def _should_attempt_reset(self) -> bool:
        """
        Check if enough time has passed to attempt reset.
        
        Returns:
            True if should attempt reset, False otherwise
        """
        if self.last_failure_time is None:
            return True
        
        elapsed = time.time() - self.last_failure_time
        return elapsed >= self.recovery_timeout
    
    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            # Success in HALF_OPEN state, close circuit
            self.logger.info(
                f"Circuit '{self.name}' recovered, transitioning to CLOSED"
            )
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            if self.failure_count > 0:
                self.failure_count = 0
    
    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            # Failure in HALF_OPEN state, reopen circuit
            self.logger.warning(
                f"Circuit '{self.name}' failed in HALF_OPEN, reopening"
            )
            self.state = CircuitState.OPEN
        elif self.state == CircuitState.CLOSED:
            # Check if threshold reached
            if self.failure_count >= self.failure_threshold:
                self.logger.error(
                    f"Circuit '{self.name}' failure threshold reached "
                    f"({self.failure_count}/{self.failure_threshold}), opening circuit"
                )
                self.state = CircuitState.OPEN
            else:
                self.logger.warning(
                    f"Circuit '{self.name}' failure {self.failure_count}/"
                    f"{self.failure_threshold}"
                )
    
    def reset(self):
        """Manually reset circuit breaker to CLOSED state."""
        self.logger.info(f"Circuit '{self.name}' manually reset")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open."""
        return self.state == CircuitState.OPEN
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed."""
        return self.state == CircuitState.CLOSED


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    expected_exception: type = Exception
):
    """
    Decorator for applying circuit breaker to functions.
    
    Args:
        name: Circuit breaker name
        failure_threshold: Number of failures before opening
        recovery_timeout: Seconds before attempting recovery
        expected_exception: Exception type to catch
        
    Returns:
        Decorated function
        
    Requirements: 3.6, 12.3
    """
    breaker = CircuitBreaker(
        name=name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        expected_exception=expected_exception
    )
    
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await breaker.call_async(func, *args, **kwargs)
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                return breaker.call(func, *args, **kwargs)
            return sync_wrapper
    
    return decorator


# Global circuit breakers for Azure services
azure_devops_breaker = CircuitBreaker(
    name="azure_devops",
    failure_threshold=5,
    recovery_timeout=60.0
)

azure_wiki_breaker = CircuitBreaker(
    name="azure_wiki",
    failure_threshold=5,
    recovery_timeout=60.0
)
