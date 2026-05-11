"""Enhanced API error classification for smart failover and recovery.

Provides a structured taxonomy of API errors with priority-ordered classification
pipeline that determines the correct recovery action.
"""
from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class FailoverReason(enum.Enum):
    """Why an API call failed — determines recovery strategy."""

    # Authentication / authorization
    AUTH = "auth"  # Transient auth (401/403) — refresh/rotate
    AUTH_PERMANENT = "auth_permanent"  # Auth failed after refresh — abort

    # Billing / quota
    BILLING = "billing"  # 402 or confirmed credit exhaustion — rotate immediately
    RATE_LIMIT = "rate_limit"  # 429 or quota-based throttling — backoff then rotate

    # Server-side
    OVERLOADED = "overloaded"  # 503/529 — provider overloaded, backoff
    SERVER_ERROR = "server_error"  # 500/502 — internal server error, retry

    # Transport
    TIMEOUT = "timeout"  # Connection/read timeout — rebuild client + retry

    # Context / payload
    CONTEXT_OVERFLOW = "context_overflow"  # Context too large — compress, not failover
    PAYLOAD_TOO_LARGE = "payload_too_large"  # 413 — compress payload

    # Model
    MODEL_NOT_FOUND = "model_not_found"  # 404 or invalid model — fallback

    # Request format
    FORMAT_ERROR = "format_error"  # 400 bad request — abort or strip + retry

    # Provider-specific
    THINKING_SIGNATURE = "thinking_signature"  # Anthropic thinking block sig invalid
    LONG_CONTEXT_TIER = "long_context_tier"  # Anthropic "extra usage" tier gate

    # Catch-all
    UNKNOWN = "unknown"  # Unclassifiable — retry with backoff


@dataclass
class ClassifiedError:
    """Structured classification of an API error with recovery hints."""

    reason: FailoverReason
    status_code: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    message: str = ""
    error_context: Dict[str, Any] = field(default_factory=dict)

    # Recovery action hints
    retryable: bool = True
    should_compress: bool = False
    should_rotate_credential: bool = False
    should_fallback: bool = False
    original_error: Exception | None = field(default=None, repr=False)

    @property
    def description(self) -> str:
        return f"{self.reason.value} (retryable={self.retryable})"

    @property
    def is_auth(self) -> bool:
        return self.reason in (FailoverReason.AUTH, FailoverReason.AUTH_PERMANENT)


# ── Error taxonomy ──────────────────────────────────────────────────────────

# Billing exhaustion patterns (not transient rate limit)
_BILLING_PATTERNS = [
    "insufficient credits",
    "insufficient_quota",
    "credit balance",
    "credits have been exhausted",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "exceeded your current quota",
    "account is deactivated",
    "plan does not include",
    "can only afford",
]

# Rate limiting patterns (transient, will resolve)
_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttled",
    "requests per minute",
    "tokens per minute",
    "requests per day",
    "try again in",
    "please retry after",
    "resource_exhausted",
    "rate increased too quickly",
]

# Usage-limit patterns that need disambiguation
_USAGE_LIMIT_PATTERNS = [
    "usage limit",
    "quota",
    "limit exceeded",
    "key limit exceeded",
]

# Signals that usage limit is transient (not billing)
_USAGE_LIMIT_TRANSIENT_SIGNALS = [
    "try again",
    "retry",
    "resets at",
    "reset in",
    "wait",
    "requests remaining",
    "periodic",
    "window",
]

# Payload-too-large patterns
_PAYLOAD_TOO_LARGE_PATTERNS = [
    "request entity too large",
    "payload too large",
    "error code: 413",
]

# Context overflow patterns
_CONTEXT_OVERFLOW_PATTERNS = [
    "context length",
    "context size",
    "maximum context",
    "token limit",
    "too many tokens",
    "reduce the length",
    "exceeds the limit",
    "context window",
    "prompt is too long",
    "prompt exceeds max length",
    "max_tokens",
    "maximum number of tokens",
    "max_model_len",
    "prompt length",
    "input is too long",
    "maximum model length",
    "context length exceeded",
    # vLLM patterns
    "exceeds the max_model_len",
    # Ollama patterns
    "truncating input",
    # llama.cpp / llama-server patterns
    "slot context",
    "n_ctx_slot",
]

# Model not found patterns
_MODEL_NOT_FOUND_PATTERNS = [
    "is not a valid model",
    "invalid model",
    "model not found",
    "model_not_found",
    "does not exist",
    "no such model",
    "unknown model",
    "unsupported model",
]

# Auth patterns
_AUTH_PATTERNS = [
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid token",
    "token expired",
    "token revoked",
    "access denied",
]

# Server disconnect patterns
_SERVER_DISCONNECT_PATTERNS = [
    "server disconnected",
    "peer closed connection",
    "connection reset by peer",
    "connection was closed",
    "network connection lost",
    "unexpected eof",
    "incomplete chunked read",
]

# Transport error type names
_TRANSPORT_ERROR_TYPES = frozenset({
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError",
    "TimeoutError", "ReadError",
    "ServerDisconnectedError",
    "APIConnectionError", "APITimeoutError",
})


# ── Provider detection ─────────────────────────────────────────────────────

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


# ── HTTP status code and error body extraction ─────────────────────────────

def _extract_status_code(error: Exception) -> Optional[int]:
    """Extract HTTP status code from error or its cause chain.
    
    Searches through the error's cause chain to find a status code.
    Handles various error types from different providers.
    """
    # Try direct attribute access first
    if hasattr(error, "status_code") and error.status_code is not None:
        return error.status_code
    
    # Try response object (common in HTTP libraries)
    if hasattr(error, "response") and error.response is not None:
        response = error.response
        if hasattr(response, "status_code"):
            return response.status_code
        # Try dict-like response (boto3 style)
        if isinstance(response, dict):
            http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if http_status:
                return http_status
    
    # Walk the cause chain
    current = error
    max_depth = 10  # Prevent infinite loops
    depth = 0
    
    while current is not None and depth < max_depth:
        # Check __cause__ (explicit exception chaining)
        if hasattr(current, "__cause__") and current.__cause__ is not None:
            current = current.__cause__
            if hasattr(current, "status_code") and current.status_code is not None:
                return current.status_code
            if hasattr(current, "response"):
                response = current.response
                if hasattr(response, "status_code"):
                    return response.status_code
        
        # Check __context__ (implicit exception chaining)
        elif hasattr(current, "__context__") and current.__context__ is not None:
            current = current.__context__
            if hasattr(current, "status_code") and current.status_code is not None:
                return current.status_code
            if hasattr(current, "response"):
                response = current.response
                if hasattr(response, "status_code"):
                    return response.status_code
        else:
            break
        
        depth += 1
    
    return None


def _extract_error_body(error: Exception) -> Dict[str, Any]:
    """Extract structured error body from error response.
    
    Parses error response bodies to extract error codes, messages,
    and other structured information.
    """
    error_body = {}
    
    # Try to get response body
    response = getattr(error, "response", None)
    if response is None:
        return error_body
    
    # Handle dict-like response (boto3 style)
    if isinstance(response, dict):
        error_info = response.get("Error", {})
        if error_info:
            error_body["error_code"] = error_info.get("Code")
            error_body["error_message"] = error_info.get("Message")
            error_body["error_type"] = error_info.get("Type")
        return error_body
    
    # Try to get JSON body from response
    try:
        if hasattr(response, "json"):
            # Handle callable json() method
            if callable(response.json):
                body = response.json()
            else:
                body = response.json
            
            if isinstance(body, dict):
                # OpenAI/Anthropic style error body
                error_info = body.get("error", {})
                if isinstance(error_info, dict):
                    error_body["error_code"] = error_info.get("code")
                    error_body["error_message"] = error_info.get("message")
                    error_body["error_type"] = error_info.get("type")
                    error_body["error_param"] = error_info.get("param")
                
                # Alternative flat structure
                if "code" in body:
                    error_body["error_code"] = body.get("code")
                if "message" in body:
                    error_body["error_message"] = body.get("message")
                if "type" in body:
                    error_body["error_type"] = body.get("type")
    except Exception:
        # Ignore JSON parsing errors
        pass
    
    # Try to get text body
    try:
        if hasattr(response, "text"):
            text = response.text if not callable(response.text) else response.text()
            if text and not error_body:
                # Store raw text if we couldn't parse JSON
                error_body["raw_text"] = text[:500]  # Limit size
    except Exception:
        pass
    
    return error_body





def _classify_openai_error(error: Exception) -> ClassifiedError:
    error_type_name = type(error).__name__
    msg = str(error).lower()
    
    # Extract status code and error body
    status_code = _extract_status_code(error)
    error_body = _extract_error_body(error)

    if error_type_name == "RateLimitError":
        # Disambiguate billing vs rate limit
        if any(pattern in msg for pattern in _BILLING_PATTERNS):
            return ClassifiedError(
                reason=FailoverReason.BILLING,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_rotate_credential=True,
                should_fallback=True,
                original_error=error,
            )
        
        # Check usage limit patterns with transient signals
        if any(pattern in msg for pattern in _USAGE_LIMIT_PATTERNS):
            has_transient_signal = any(signal in msg for signal in _USAGE_LIMIT_TRANSIENT_SIGNALS)
            if not has_transient_signal:
                # Usage limit without transient signals = billing
                return ClassifiedError(
                    reason=FailoverReason.BILLING,
                    status_code=status_code,
                    message=error_body.get("error_message", str(error)),
                    error_context=error_body,
                    retryable=False,
                    should_rotate_credential=True,
                    should_fallback=True,
                    original_error=error,
                )
        
        # Default to rate limit (transient)
        return ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "AuthenticationError":
        return ClassifiedError(
            reason=FailoverReason.AUTH_PERMANENT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=False,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "BadRequestError":
        if any(pattern in msg for pattern in _CONTEXT_OVERFLOW_PATTERNS):
            return ClassifiedError(
                reason=FailoverReason.CONTEXT_OVERFLOW,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_compress=True,
                original_error=error,
            )
        return ClassifiedError(
            reason=FailoverReason.UNKNOWN,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=False,
            original_error=error,
        )

    if error_type_name == "APIStatusError":
        # Check for billing patterns in status error messages
        if status_code == 402 or any(pattern in msg for pattern in _BILLING_PATTERNS):
            return ClassifiedError(
                reason=FailoverReason.BILLING,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_rotate_credential=True,
                should_fallback=True,
                original_error=error,
            )
        
        if status_code in (502, 503):
            return ClassifiedError(
                reason=FailoverReason.OVERLOADED,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        if status_code == 500:
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        if status_code == 404:
            return ClassifiedError(
                reason=FailoverReason.MODEL_NOT_FOUND,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_fallback=True,
                original_error=error,
            )
        if status_code == 429:
            # Disambiguate billing vs rate limit for 429 errors
            if any(pattern in msg for pattern in _BILLING_PATTERNS):
                return ClassifiedError(
                    reason=FailoverReason.BILLING,
                    status_code=status_code,
                    message=error_body.get("error_message", str(error)),
                    error_context=error_body,
                    retryable=False,
                    should_rotate_credential=True,
                    should_fallback=True,
                    original_error=error,
                )
            
            # Check usage limit patterns with transient signals
            if any(pattern in msg for pattern in _USAGE_LIMIT_PATTERNS):
                has_transient_signal = any(signal in msg for signal in _USAGE_LIMIT_TRANSIENT_SIGNALS)
                if not has_transient_signal:
                    return ClassifiedError(
                        reason=FailoverReason.BILLING,
                        status_code=status_code,
                        message=error_body.get("error_message", str(error)),
                        error_context=error_body,
                        retryable=False,
                        should_rotate_credential=True,
                        should_fallback=True,
                        original_error=error,
                    )
            
            # Default to rate limit
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                should_fallback=True,
                original_error=error,
            )
    
    # Fallback: Check for provider-specific patterns (vLLM, Ollama, llama.cpp)
    # Check for context overflow patterns from local inference servers
    if status_code == 400 and any(pattern in msg for pattern in _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=False,
            should_compress=True,
            original_error=error,
        )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        status_code=status_code,
        message=error_body.get("error_message", str(error)),
        error_context=error_body,
        retryable=False,
        original_error=error,
    )


def _classify_anthropic_error(error: Exception) -> ClassifiedError:
    error_type_name = type(error).__name__
    msg = str(error).lower()
    
    # Extract status code and error body
    status_code = _extract_status_code(error)
    error_body = _extract_error_body(error)

    if error_type_name == "RateLimitError":
        # Check for Anthropic long-context tier gate (429 "extra usage" + "long context")
        if "extra usage" in msg and "long context" in msg:
            return ClassifiedError(
                reason=FailoverReason.LONG_CONTEXT_TIER,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                should_compress=True,
                original_error=error,
            )
        
        # Disambiguate billing vs rate limit
        if any(pattern in msg for pattern in _BILLING_PATTERNS):
            return ClassifiedError(
                reason=FailoverReason.BILLING,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_rotate_credential=True,
                should_fallback=True,
                original_error=error,
            )
        
        # Check usage limit patterns with transient signals
        if any(pattern in msg for pattern in _USAGE_LIMIT_PATTERNS):
            has_transient_signal = any(signal in msg for signal in _USAGE_LIMIT_TRANSIENT_SIGNALS)
            if not has_transient_signal:
                # Usage limit without transient signals = billing
                return ClassifiedError(
                    reason=FailoverReason.BILLING,
                    status_code=status_code,
                    message=error_body.get("error_message", str(error)),
                    error_context=error_body,
                    retryable=False,
                    should_rotate_credential=True,
                    should_fallback=True,
                    original_error=error,
                )
        
        # Default to rate limit (transient)
        return ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "AuthenticationError":
        return ClassifiedError(
            reason=FailoverReason.AUTH_PERMANENT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=False,
            should_fallback=True,
            original_error=error,
        )

    if error_type_name == "BadRequestError":
        # Check for Anthropic thinking signature error (400 "signature" + "thinking")
        if "signature" in msg and "thinking" in msg:
            return ClassifiedError(
                reason=FailoverReason.THINKING_SIGNATURE,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                should_compress=False,
                original_error=error,
            )
        
        if any(pattern in msg for pattern in _CONTEXT_OVERFLOW_PATTERNS):
            return ClassifiedError(
                reason=FailoverReason.CONTEXT_OVERFLOW,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_compress=True,
                original_error=error,
            )

    if error_type_name == "APIStatusError":
        # Check for Anthropic thinking signature error (400 "signature" + "thinking")
        if status_code == 400 and "signature" in msg and "thinking" in msg:
            return ClassifiedError(
                reason=FailoverReason.THINKING_SIGNATURE,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                should_compress=False,
                original_error=error,
            )
        
        # Check for Anthropic long-context tier gate (429 "extra usage" + "long context")
        if status_code == 429 and "extra usage" in msg and "long context" in msg:
            return ClassifiedError(
                reason=FailoverReason.LONG_CONTEXT_TIER,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                should_compress=True,
                original_error=error,
            )
        
        # Check for billing patterns in status error messages
        if status_code == 402 or any(pattern in msg for pattern in _BILLING_PATTERNS):
            return ClassifiedError(
                reason=FailoverReason.BILLING,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_rotate_credential=True,
                should_fallback=True,
                original_error=error,
            )
        
        if status_code in (502, 503, 529):
            return ClassifiedError(
                reason=FailoverReason.OVERLOADED,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        if status_code == 500:
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        if status_code == 429:
            # Disambiguate billing vs rate limit for 429 errors
            if any(pattern in msg for pattern in _BILLING_PATTERNS):
                return ClassifiedError(
                    reason=FailoverReason.BILLING,
                    status_code=status_code,
                    message=error_body.get("error_message", str(error)),
                    error_context=error_body,
                    retryable=False,
                    should_rotate_credential=True,
                    should_fallback=True,
                    original_error=error,
                )
            
            # Check usage limit patterns with transient signals
            if any(pattern in msg for pattern in _USAGE_LIMIT_PATTERNS):
                has_transient_signal = any(signal in msg for signal in _USAGE_LIMIT_TRANSIENT_SIGNALS)
                if not has_transient_signal:
                    return ClassifiedError(
                        reason=FailoverReason.BILLING,
                        status_code=status_code,
                        message=error_body.get("error_message", str(error)),
                        error_context=error_body,
                        retryable=False,
                        should_rotate_credential=True,
                        should_fallback=True,
                        original_error=error,
                    )
            
            # Default to rate limit
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                should_fallback=True,
                original_error=error,
            )
    
    # Fallback: Check for provider-specific patterns regardless of error type
    # Check for Anthropic thinking signature error (400 "signature" + "thinking")
    if status_code == 400 and "signature" in msg and "thinking" in msg:
        return ClassifiedError(
            reason=FailoverReason.THINKING_SIGNATURE,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            should_compress=False,
            original_error=error,
        )
    
    # Check for Anthropic long-context tier gate (429 "extra usage" + "long context")
    if status_code == 429 and "extra usage" in msg and "long context" in msg:
        return ClassifiedError(
            reason=FailoverReason.LONG_CONTEXT_TIER,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            should_compress=True,
            original_error=error,
        )
    
    # Check for context overflow patterns (vLLM, Ollama, llama.cpp)
    if status_code == 400 and any(pattern in msg for pattern in _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            should_compress=True,
            original_error=error,
        )
    
    # Check for rate limit patterns
    if status_code == 429:
        return ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            should_fallback=True,
            original_error=error,
        )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        status_code=status_code,
        message=error_body.get("error_message", str(error)),
        error_context=error_body,
        retryable=False,
        original_error=error,
    )


def _classify_boto_error(error: Exception) -> ClassifiedError:
    # Extract status code and error body
    status_code = _extract_status_code(error)
    error_body = _extract_error_body(error)
    
    response = getattr(error, "response", None)
    if response:
        error_code = response.get("Error", {}).get("Code", "")
        if error_code in ("ThrottlingException", "Throttling", "RequestLimitExceeded"):
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        if error_code in ("ServiceUnavailable", "InternalFailure", "InternalError"):
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        # Handle ExpiredTokenException - credentials expired, cannot proceed
        if error_code == "ExpiredTokenException" or "ExpiredToken" in str(error):
            return ClassifiedError(
                reason=FailoverReason.AUTH_PERMANENT,
                status_code=status_code,
                message=error_body.get("error_message", "AWS credentials expired. Please refresh your AWS credentials and try again."),
                error_context=error_body,
                retryable=False,
                should_fallback=False,  # Don't fallback, credentials need to be refreshed
                original_error=error,
            )
        if error_code in ("InvalidClientTokenId", "UnrecognizedClientException"):
            return ClassifiedError(
                reason=FailoverReason.AUTH_PERMANENT,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_fallback=True,
                original_error=error,
            )
        if error_code in ("AccessDenied", "UnauthorizedAccess"):
            return ClassifiedError(
                reason=FailoverReason.AUTH,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                original_error=error,
            )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        status_code=status_code,
        message=error_body.get("error_message", str(error)),
        error_context=error_body,
        retryable=False,
        original_error=error,
    )


def _classify_httpx_error(error: Exception) -> ClassifiedError:
    error_type_name = type(error).__name__
    
    # Extract status code and error body
    status_code = _extract_status_code(error)
    error_body = _extract_error_body(error)

    if error_type_name == "TimeoutException":
        return ClassifiedError(
            reason=FailoverReason.TIMEOUT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            original_error=error,
        )

    if error_type_name == "ConnectError":
        return ClassifiedError(
            reason=FailoverReason.SERVER_ERROR,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            original_error=error,
        )

    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        status_code=status_code,
        message=error_body.get("error_message", str(error)),
        error_context=error_body,
        retryable=False,
        original_error=error,
    )


def _classify_generic_error(error: Exception) -> ClassifiedError:
    """Classify errors from unknown providers (vLLM, Ollama, llama.cpp, etc.)."""
    msg = str(error).lower()
    
    # Extract status code and error body
    status_code = _extract_status_code(error)
    error_body = _extract_error_body(error)
    
    # Check for context overflow patterns (vLLM, Ollama, llama.cpp)
    if any(pattern in msg for pattern in _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.CONTEXT_OVERFLOW,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=False,
            should_compress=True,
            original_error=error,
        )
    
    # Check for rate limit patterns
    if any(pattern in msg for pattern in _RATE_LIMIT_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.RATE_LIMIT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            should_fallback=True,
            original_error=error,
        )
    
    # Check for auth patterns
    if any(pattern in msg for pattern in _AUTH_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.AUTH_PERMANENT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=False,
            should_fallback=True,
            original_error=error,
        )
    
    # Check for model not found patterns
    if any(pattern in msg for pattern in _MODEL_NOT_FOUND_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.MODEL_NOT_FOUND,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=False,
            should_fallback=True,
            original_error=error,
        )
    
    # Check for server disconnect patterns
    if any(pattern in msg for pattern in _SERVER_DISCONNECT_PATTERNS):
        return ClassifiedError(
            reason=FailoverReason.TIMEOUT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            original_error=error,
        )
    
    # Check status code if available
    if status_code:
        if status_code in (502, 503, 529):
            return ClassifiedError(
                reason=FailoverReason.OVERLOADED,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        if status_code == 500:
            return ClassifiedError(
                reason=FailoverReason.SERVER_ERROR,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                original_error=error,
            )
        if status_code == 429:
            return ClassifiedError(
                reason=FailoverReason.RATE_LIMIT,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=True,
                should_fallback=True,
                original_error=error,
            )
        if status_code in (401, 403):
            return ClassifiedError(
                reason=FailoverReason.AUTH_PERMANENT,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_fallback=True,
                original_error=error,
            )
        if status_code == 404:
            return ClassifiedError(
                reason=FailoverReason.MODEL_NOT_FOUND,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                should_fallback=True,
                original_error=error,
            )
        if status_code == 400:
            return ClassifiedError(
                reason=FailoverReason.FORMAT_ERROR,
                status_code=status_code,
                message=error_body.get("error_message", str(error)),
                error_context=error_body,
                retryable=False,
                original_error=error,
            )
    
    return ClassifiedError(
        reason=FailoverReason.UNKNOWN,
        status_code=status_code,
        message=error_body.get("error_message", str(error)),
        error_context=error_body,
        retryable=False,
        original_error=error,
    )


def classify_error(error: Exception) -> ClassifiedError:
    """Classify an API error with enhanced status code and error body extraction.
    
    This function extracts HTTP status codes from the error cause chain and
    parses structured error bodies to provide comprehensive error classification.
    """
    if _is_openai_error(error):
        return _classify_openai_error(error)

    if _is_anthropic_error(error):
        return _classify_anthropic_error(error)

    if _is_boto_error(error):
        return _classify_boto_error(error)

    if _is_httpx_error(error):
        return _classify_httpx_error(error)

    if isinstance(error, asyncio.TimeoutError):
        status_code = _extract_status_code(error)
        error_body = _extract_error_body(error)
        return ClassifiedError(
            reason=FailoverReason.TIMEOUT,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            original_error=error,
        )

    if isinstance(error, ConnectionError):
        status_code = _extract_status_code(error)
        error_body = _extract_error_body(error)
        return ClassifiedError(
            reason=FailoverReason.SERVER_ERROR,
            status_code=status_code,
            message=error_body.get("error_message", str(error)),
            error_context=error_body,
            retryable=True,
            original_error=error,
        )

    # Try generic classification for unknown providers (vLLM, Ollama, llama.cpp, etc.)
    return _classify_generic_error(error)
