"""Throttle classification + retry/failover mechanism tests.

Exercises the seam that drives model failover without standing up a full agent:
a real botocore ThrottlingException must classify as a fallback-worthy rate
limit, and with_retry's ``retry_on`` predicate must let it bubble immediately
(so the outer failover loop can switch targets) while still retrying genuine
transient errors in place.
"""
import pytest
from botocore.exceptions import ClientError

from app.core.error_classifier import classify_error, FailoverReason
from app.core.retry import with_retry


def _throttle_error():
    return ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}},
        "Converse",
    )


def _server_error():
    return ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "try later"}},
        "Converse",
    )


def test_throttling_classifies_as_fallback_rate_limit():
    ce = classify_error(_throttle_error())
    assert ce.reason is FailoverReason.RATE_LIMIT
    assert ce.retryable is True
    assert ce.should_fallback is True


# Predicate used by the strategy when a fallback chain exists.
_FAILOVER_PREDICATE = lambda ce: ce.retryable and not ce.should_fallback  # noqa: E731


@pytest.mark.asyncio
async def test_throttle_bubbles_immediately_under_failover_predicate():
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise _throttle_error()

    with pytest.raises(ClientError):
        await with_retry(boom, max_retries=3, retry_on=_FAILOVER_PREDICATE)

    # Throttle is excluded by the predicate → no in-place retries, fail fast so
    # the failover loop can switch targets.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_boto_server_error_is_fallback_worthy():
    # Bedrock ServiceUnavailable/InternalFailure are also worth failing over.
    ce = classify_error(_server_error())
    assert ce.reason is FailoverReason.SERVER_ERROR
    assert ce.should_fallback is True


@pytest.mark.asyncio
async def test_timeout_retries_in_place_under_failover_predicate():
    # A pure-transient timeout (retryable, NOT should_fallback) should keep
    # retrying in place rather than triggering a target switch.
    import asyncio
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise asyncio.TimeoutError()
        return "ok"

    out = await with_retry(flaky, max_retries=5, retry_on=_FAILOVER_PREDICATE)
    assert out == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_default_predicate_retries_throttle_in_place():
    """Without a failover predicate (single-candidate chain), the old behaviour
    holds: a retryable throttle is retried in place up to the budget."""
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _throttle_error()
        return "ok"

    out = await with_retry(flaky, max_retries=3)  # no retry_on
    assert out == "ok"
    assert calls["n"] == 2
