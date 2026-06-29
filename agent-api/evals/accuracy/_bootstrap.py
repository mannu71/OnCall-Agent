"""Harness bootstrap — must be imported before any AWS/Bedrock call.

Mirrors the scoped SSL override in ``app/main.py``: when ``AWS_SSL_VERIFY`` is
false (the container's setting), boto3 client factories default to
``verify=False`` so Bedrock calls succeed behind the corporate TLS intercept.
Importing this module applies the patch idempotently.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
_APPLIED = False


def apply() -> None:
    global _APPLIED
    if _APPLIED:
        return
    try:
        from app.config import settings
    except Exception as exc:  # pragma: no cover
        logger.warning("bootstrap: could not import settings (%s)", exc)
        return

    if not getattr(settings, "aws_ssl_verify", True):
        try:
            import boto3

            def _wrap(factory):
                def _patched(*args, **kwargs):
                    kwargs.setdefault("verify", False)
                    return factory(*args, **kwargs)
                return _patched

            boto3.client = _wrap(boto3.client)          # type: ignore[assignment]
            boto3.resource = _wrap(boto3.resource)      # type: ignore[assignment]
            _orig = boto3.Session.client

            def _patched_session_client(self, *args, **kwargs):  # type: ignore[misc]
                kwargs.setdefault("verify", False)
                return _orig(self, *args, **kwargs)
            boto3.Session.client = _patched_session_client  # type: ignore[assignment]
        except Exception as exc:  # pragma: no cover
            logger.warning("bootstrap: boto3 SSL patch failed (%s)", exc)
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    _APPLIED = True


apply()
