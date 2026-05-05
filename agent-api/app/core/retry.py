"""Retry utilities with decorrelated jittered backoff and error classification.

Provides intelligent retry with:
- Error classification-aware retry decisions
- Context compression trigger on overflow
- Context length probing with tiered fallback
- Provider fallback on auth/billing errors
- Configurable retry policies per error type
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional, TypeVar

from app.core.error_classifier import classify_error, ClassifiedError, FailoverReason

logger = logging.getLogger(__name__)

T = TypeVar("T")

_BASE = 1.0
_CAP = 30.0


@dataclass
class RetryPolicy:
    """Retry policy configuration for different error types."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    max_total_delay: float = 120.0

    # Per-reason overrides
    rate_limit_max_retries: int = 5
    rate_limit_base_delay: float = 2.0
    rate_limit_max_delay: float = 60.0
    
    overloaded_max_retries: int = 3
    timeout_max_retries: int = 3
    server_error_max_retries: int = 3
    context_overflow_max_retries: int = 5


@dataclass
class ContextProbingState:
    """State for context length probing during retries."""

    current_context_length: int
    model: str = ""
    base_url: str = ""
    probed_lengths: list[int] = None  # type: ignore

    def __post_init__(self):
        if self.probed_lengths is None:
            self.probed_lengths = []

    def record_probe(self, length: int) -> None:
        """Record a probed context length."""
        self.probed_lengths.append(length)


def jittered_backoff(base: float = _BASE, cap: float = _CAP, attempt: int = 0) -> float:
    """Decorrelated jitter backoff.

    Formula:  sleep = min(cap, random_between(base, sleep * 3))
    where sleep starts at base on the first attempt.

    Args:
        base: Minimum wait in seconds.
        cap: Maximum wait in seconds.
        attempt: Zero-indexed attempt number.

    Returns:
        Wait time in seconds (float).
    """
    if attempt <= 0:
        return base
    sleep = base
    for _ in range(attempt):
        sleep = min(cap, random.uniform(base, sleep * 3))
    return sleep


def _get_retry_count_for_reason(classified: ClassifiedError, policy: RetryPolicy) -> int:
    """Get max retry count based on error reason."""
    reason_counts = {
        FailoverReason.RATE_LIMIT: policy.rate_limit_max_retries,
        FailoverReason.OVERLOADED: policy.overloaded_max_retries,
        FailoverReason.TIMEOUT: policy.timeout_max_retries,
        FailoverReason.SERVER_ERROR: policy.server_error_max_retries,
        FailoverReason.CONTEXT_OVERFLOW: policy.context_overflow_max_retries,
    }
    return reason_counts.get(classified.reason, policy.max_retries)


async def with_retry(
    fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    max_retries: int = 3,
    on_retry: Callable[[int, ClassifiedError], Coroutine[Any, Any, None]] | None = None,
    on_context_overflow: Callable[[], Coroutine[Any, Any, None]] | None = None,
    on_context_probing: Callable[[int], Coroutine[Any, Any, None]] | None = None,
    on_fallback: Callable[[ClassifiedError], Coroutine[Any, Any, None]] | None = None,
    context_probing_state: ContextProbingState | None = None,
    policy: RetryPolicy | None = None,
    **kwargs: Any,
) -> T:
    """Call an async function with automatic retry on transient errors.

    Enhanced with error classification-aware retry decisions:
    - Context overflow: triggers compression callback
    - Context length probing: step down to lower context length on overflow
    - Auth/billing errors: triggers fallback callback
    - Rate limits: extended retry with longer backoff
    - Total delay tracking: enforces max_total_delay limit

    Args:
        fn: Async callable to invoke.
        *args: Positional args forwarded to *fn*.
        max_retries: Maximum number of retry attempts (deprecated, use policy).
        on_retry: Optional async callback ``(attempt, classified_error)`` invoked
            before each retry (for logging / metrics).
        on_context_overflow: Optional callback for context overflow errors.
            Called before retry if the error indicates context overflow.
        on_context_probing: Optional callback when context length is probed.
            Called with the new context length to try.
        on_fallback: Optional callback for fallback situations.
            Called for auth/billing errors before giving up.
        context_probing_state: Optional state for context length probing.
            If provided, enables automatic context length step-down.
        policy: Optional RetryPolicy for configuring retry behavior.
            If not provided, uses default policy with max_retries.
        **kwargs: Keyword args forwarded to *fn*.

    Returns:
        The return value of *fn*.

    Raises:
        The last exception if all retries are exhausted or the error is not retryable.
    """
    last_error: Exception | None = None
    
    # Use provided policy or create default
    if policy is None:
        policy = RetryPolicy(max_retries=max_retries)
    
    total_delay = 0.0

    for attempt in range(policy.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            classified = classify_error(exc)

            # Get retry count based on error reason
            max_attempts = _get_retry_count_for_reason(classified, policy)

            # Handle context overflow - trigger compression and/or probing
            if classified.should_compress:
                logger.info(
                    f"Context overflow detected (reason={classified.reason.value}, "
                    f"attempt={attempt + 1}/{max_attempts + 1})"
                )
                
                # First, try context length probing if state is provided
                if context_probing_state is not None and classified.reason == FailoverReason.CONTEXT_OVERFLOW:
                    from app.core.model_metadata import resolve_context_length_with_probing

                    new_length = resolve_context_length_with_probing(
                        exc,
                        context_probing_state.current_context_length,
                        context_probing_state.model,
                        context_probing_state.base_url,
                    )

                    if new_length is not None:
                        context_probing_state.record_probe(new_length)
                        context_probing_state.current_context_length = new_length
                        logger.info(
                            f"Context probing: stepping down to {new_length} tokens "
                            f"(attempt {attempt + 1}, probed lengths: {context_probing_state.probed_lengths})"
                        )
                        if on_context_probing:
                            try:
                                await on_context_probing(new_length)
                            except Exception as e:
                                logger.warning(f"Context probing callback failed: {e}")

                # Then trigger compression callback
                compression_applied = False
                if on_context_overflow:
                    logger.info("Invoking on_context_overflow callback for compression")
                    try:
                        await on_context_overflow()
                        logger.info("Context compression callback completed successfully")
                        compression_applied = True
                    except Exception as e:
                        logger.error(f"Context overflow callback failed: {e}", exc_info=True)
                else:
                    logger.warning(
                        "Context overflow detected but no on_context_overflow callback provided. "
                        "Retrying without compression may fail again."
                    )
            else:
                compression_applied = False

            # Handle fallback situations
            if classified.should_fallback:
                if on_fallback:
                    logger.info(
                        f"Triggering fallback for {classified.reason.value} "
                        f"(attempt={attempt + 1}/{max_attempts + 1}, "
                        f"description='{classified.description}')"
                    )
                    try:
                        await on_fallback(classified)
                        logger.info("Fallback callback completed successfully")
                    except Exception as e:
                        logger.error(f"Fallback callback failed: {e}", exc_info=True)
                else:
                    logger.warning(
                        f"Fallback recommended for {classified.reason.value} but no "
                        f"on_fallback callback provided. Error may persist."
                    )

            # Check if we should retry.
            # If compression was applied successfully we always allow one more attempt
            # (even if retryable=False), because the root cause has been addressed.
            if not classified.retryable and not compression_applied:
                logger.debug(f"Error not retryable: {classified.reason.value}")
                raise

            if attempt >= max_attempts:
                logger.debug(f"Max retries ({max_attempts}) exceeded")
                raise

            # Calculate wait time based on error reason with error-type-specific settings
            if classified.reason == FailoverReason.RATE_LIMIT:
                # Use rate limit specific settings
                wait = jittered_backoff(
                    base=policy.rate_limit_base_delay,
                    cap=policy.rate_limit_max_delay,
                    attempt=attempt
                )
            elif classified.reason == FailoverReason.OVERLOADED:
                wait = jittered_backoff(base=1.0, cap=30.0, attempt=attempt)
            elif classified.reason == FailoverReason.CONTEXT_OVERFLOW:
                # Short backoff for context overflow - we're actively fixing it
                wait = jittered_backoff(base=0.5, cap=5.0, attempt=attempt)
            else:
                wait = jittered_backoff(
                    base=policy.base_delay,
                    cap=policy.max_delay,
                    attempt=attempt
                )

            # Track total delay and enforce max_total_delay
            total_delay += wait
            if total_delay > policy.max_total_delay:
                logger.warning(
                    f"Total retry delay ({total_delay:.1f}s) exceeds max_total_delay "
                    f"({policy.max_total_delay:.1f}s), giving up"
                )
                raise

            # Log retry attempt with detailed classified error information
            logger.warning(
                "with_retry: attempt %d/%d failed (%s), retrying in %.1fs (total delay: %.1fs) | "
                "reason=%s, retryable=%s, should_compress=%s, should_fallback=%s",
                attempt + 1,
                max_attempts + 1,
                classified.description,
                wait,
                total_delay,
                classified.reason.value,
                classified.retryable,
                classified.should_compress,
                classified.should_fallback,
            )

            if on_retry:
                try:
                    await on_retry(attempt + 1, classified)
                except Exception as e:
                    logger.warning(f"on_retry callback failed: {e}")

            await asyncio.sleep(wait)

    assert last_error is not None
    raise last_error
