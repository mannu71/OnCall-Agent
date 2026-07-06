"""Opt-in API-key authentication middleware.

Off by default (``settings.api_auth_enabled=False``) so local/dev and any
deployment behind its own trusted-network boundary keep working with zero
friction. When enabled, every request must present a valid key via either the
``Authorization: Bearer <key>`` header or the ``X-API-Key: <key>`` header,
checked against ``settings.api_auth_keys`` (comma-separated).

A short allowlist of paths always stays open regardless of the flag: health
checks (load balancers / uptime monitors can't be expected to carry a key),
the root info endpoint, and the interactive API docs.
"""
import logging
from typing import Iterable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

# Always-open paths — exact match or prefix, checked case-sensitively against
# request.url.path. Health must stay open for infra probes; docs/root are
# informational, not data-bearing.
_EXEMPT_PATHS = ("/", "/docs", "/openapi.json", "/redoc", "/api/v1/health")


def _configured_keys() -> Iterable[str]:
    raw = getattr(settings, "api_auth_keys", "") or ""
    return {k.strip() for k in raw.split(",") if k.strip()}


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_PATHS


def _extract_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Rejects unauthenticated requests when ``API_AUTH_ENABLED=true``.

    No-op (passes every request through) when the flag is off, or when the
    flag is on but no keys are configured — a misconfiguration should not
    silently lock every route out with no way in.
    """

    async def dispatch(self, request: Request, call_next):
        if not getattr(settings, "api_auth_enabled", False):
            return await call_next(request)

        if _is_exempt(request.url.path) or request.method == "OPTIONS":
            return await call_next(request)

        keys = _configured_keys()
        if not keys:
            logger.warning(
                "api_auth: API_AUTH_ENABLED=true but no API_AUTH_KEYS configured — "
                "failing open, all requests allowed. Set API_AUTH_KEYS to actually enforce."
            )
            return await call_next(request)

        presented = _extract_key(request)
        if presented and presented in keys:
            return await call_next(request)

        logger.warning("api_auth: rejected unauthenticated request to %s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": "Unauthorized",
                "message": "Missing or invalid API key. Provide it via "
                            "'Authorization: Bearer <key>' or 'X-API-Key: <key>'.",
            },
        )
