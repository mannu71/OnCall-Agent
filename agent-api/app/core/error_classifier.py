"""Error classification for intelligent retry and failover decisions."""
from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class FailoverReason(enum.Enum):
    AUTH = "auth"
    AUTH_PERMANENT = "auth_permanent"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    CONTEXT_OVERFLOW = "context_overflow"
    TIMEOUT = "timeout"
    MODEL_NOT_FOUND = "model_not_found"
    UNKNOWN = "unknown"


@dataclass
class ClassifiedError:
    reason: FailoverReason
    retryable: bool = False
    should_compress: bool = False
    should_fallback: bool = False
    original_error: Exception | None = field(default=None, repr=False)
    # Target metadata — populated by the caller (the agent runner knows which
    # model/region/credential it was using when the error fired). Lets the
    # fallback-chain router record per-target backoff. Left None when unknown.
    model_id: str | None = None
    region: str | None = None
    provider_key: str | None = None

    @property
    def description(self) -> str:
        return f"{self.reason.value} (retryable={self.retryable})"


def _is_openai_error(error: Exception) -> bool:
    mod = type(error).__module__ or ""
    return mod.startswith("openai")


def _is_anthropic_error(error: Exception) -> bool:
    mod = type(error).__module__ or ""
    return mod.startswith("anthropic")


def _is_boto_error(error: Exception) -> bool:
    mod = type(error).__module__ or ""
    return mod.startswith("botocore")


def _is_httpx_error(error: Exception) -> bool:
    mod = type(error).__module__ or ""
    return mod.startswith("httpx")


def _classify_openai_error(error: Exception) -> ClassifiedError:
    error_type_name = type(error).__name__

    if error_type_name == "RateLimitError":
        return ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            retryable=True,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "AuthenticationError":
        return ClassifiedError(
            reason=FailoverReason.AUTH_PERMANENT,
            retryable=False,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "BadRequestError":
        msg = str(error).lower()
        if "context" in msg or "token" in msg or "maximum" in msg:
            return ClassifiedError(
                reason=FailoverReason.CONTEXT_OVERFLOW,
                retryable=False,
                should_compress=True,
                original_error=error,
            )
        return ClassifiedError(
            reason=FailoverReason.UNKNOWN,
            retryable=False,
            original_error=error,
        )

    if error_type_name == "APIStatusError":
        status_code = getattr(error, "status_code", None) or 0
        if status_code in (502, 503):
            return ClassifiedError(
                reason=FailoverReason.OVERLOADED,
                retryable=True,
                original_error=error,
            )
        if status_code == 500:
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
                original_error=error,
            )
        if status_code == 404:
            return ClassifiedError(
                reason=FailoverReason.MODEL_NOT_FOUND,
                retryable=False,
                should_fallback=True,
                original_error=error,
            )
        if status_code == 429:
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                retryable=True,
                should_fallback=True,
                original_error=error,
            )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        retryable=False,
        original_error=error,
    )


def _classify_anthropic_error(error: Exception) -> ClassifiedError:
    error_type_name = type(error).__name__

    if error_type_name == "RateLimitError":
        return ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            retryable=True,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "AuthenticationError":
        return ClassifiedError(
            reason=FailoverReason.AUTH_PERMANENT,
            retryable=False,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "BadRequestError":
        msg = str(error).lower()
        if "context" in msg or "token" in msg or "maximum" in msg or "too many" in msg:
            return ClassifiedError(
                reason=FailoverReason.CONTEXT_OVERFLOW,
                retryable=False,
                should_compress=True,
                original_error=error,
            )

    if error_type_name == "APIStatusError":
        status_code = getattr(error, "status_code", None) or 0
        if status_code in (502, 503, 529):
            return ClassifiedError(
                reason=FailoverReason.OVERLOADED,
                retryable=True,
                original_error=error,
            )
        if status_code == 500:
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
                original_error=error,
            )
        if status_code == 429:
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                retryable=True,
                should_fallback=True,
                original_error=error,
            )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        retryable=False,
        original_error=error,
    )


def _classify_boto_error(error: Exception) -> ClassifiedError:
    response = getattr(error, "response", None)
    if response:
        error_code = response.get("Error", {}).get("Code", "")
        if error_code in ("ThrottlingException", "Throttling", "RequestLimitExceeded"):
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                retryable=True,
                should_fallback=True,
                original_error=error,
            )
        if error_code in ("ServiceUnavailable", "InternalFailure", "InternalError"):
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                retryable=True,
                should_fallback=True,
                original_error=error,
            )
        if error_code in ("InvalidClientTokenId", "UnrecognizedClientException"):
            return ClassifiedError(
                reason=FailoverReason.AUTH_PERMANENT,
                retryable=False,
                should_fallback=True,
                original_error=error,
            )
        if error_code in ("AccessDenied", "UnauthorizedAccess"):
            return ClassifiedError(
                reason=FailoverReason.AUTH,
                retryable=False,
                original_error=error,
            )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        retryable=False,
        original_error=error,
    )


def _classify_httpx_error(error: Exception) -> ClassifiedError:
    error_type_name = type(error).__name__

    if error_type_name == "TimeoutException":
        return ClassifiedError(
            reason=FailoverReason.TIMEOUT,
            retryable=True,
            original_error=error,
        )

    if error_type_name == "ConnectError":
        return ClassifiedError(
            reason=FailoverReason.SERVER_ERROR,
            retryable=True,
            original_error=error,
        )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        retryable=False,
        original_error=error,
    )


def classify_error(error: Exception) -> ClassifiedError:
    if _is_openai_error(error):
        return _classify_openai_error(error)

    if _is_anthropic_error(error):
        return _classify_anthropic_error(error)

    if _is_boto_error(error):
        return _classify_boto_error(error)

    if _is_httpx_error(error):
        return _classify_httpx_error(error)

    if isinstance(error, asyncio.TimeoutError):
        return ClassifiedError(
            reason=FailoverReason.TIMEOUT,
            retryable=True,
            original_error=error,
        )

    if isinstance(error, ConnectionError):
        return ClassifiedError(
            reason=FailoverReason.SERVER_ERROR,
            retryable=True,
            original_error=error,
        )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        retryable=False,
        original_error=error,
    )
