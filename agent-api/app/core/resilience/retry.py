"""Retry utilities with decorrelated jittered backoff."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Coroutine, TypeVar

from app.core.llm.error_classifier import classify_error, ClassifiedError

logger = logging.getLogger(__name__)

T = TypeVar("T")

_BASE = 1.0
_CAP = 30.0


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


async def with_retry(
    fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    max_retries: int = 3,
    on_retry: Callable[[int, ClassifiedError], Coroutine[Any, Any, None]] | None = None,
    retry_on: Callable[[ClassifiedError], bool] | None = None,
    **kwargs: Any,
) -> T:
    """Call an async function with automatic retry on transient errors.

    Args:
        fn: Async callable to invoke.
        *args: Positional args forwarded to *fn*.
        max_retries: Maximum number of retry attempts.
        on_retry: Optional async callback ``(attempt, classified_error)`` invoked
                  before each retry (for logging / metrics).
        retry_on: Optional predicate ``(classified_error) -> bool`` deciding
                  whether to retry in place. Defaults to ``classified.retryable``.
                  Pass a stricter predicate to let an *outer* failover loop handle
                  certain errors immediately instead of sleeping through retries
                  here (e.g. exclude ``should_fallback`` throttles).
        **kwargs: Keyword args forwarded to *fn*.

    Returns:
        The return value of *fn*.

    Raises:
        The last exception if all retries are exhausted or the error is not retryable.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            classified = classify_error(exc)

            # Control-flow signals (e.g. a run budget running out) are never
            # transient — retrying one does the exact thing it was raised to
            # stop. Honoured ahead of `retry_on` so a caller's predicate cannot
            # opt into retrying them by accident.
            if getattr(exc, "never_retry", False):
                raise

            _should_retry = retry_on(classified) if retry_on else classified.retryable
            if not _should_retry or attempt >= max_retries:
                raise

            wait = jittered_backoff(attempt=attempt)
            logger.warning(
                "with_retry: attempt %d/%d failed (%s), retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                classified.description,
                wait,
            )

            if on_retry:
                try:
                    await on_retry(attempt + 1, classified)
                except Exception as hook_exc:  # noqa: BLE001
                    # A failing hook must never break the retry it observes.
                    logger.debug(
                        "with_retry: on_retry hook raised (%s), continuing",
                        hook_exc,
                        exc_info=True,
                    )

            await asyncio.sleep(wait)

    assert last_error is not None
    raise last_error
