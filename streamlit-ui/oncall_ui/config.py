"""Runtime configuration, read from the environment.

The Streamlit server calls agent-api server-side, so the base URL is whatever
the *container* can reach: ``http://agent-api:8000`` inside the compose
network, ``http://localhost:48000`` for local development.
"""
from __future__ import annotations

import os

API_PREFIX = "/api/v1"
DEFAULT_API_HOST = "http://localhost:48000"


def api_base_url() -> str:
    """Backend base URL, always ending in ``/api/v1``.

    Honours ``AGENT_API_URL`` (preferred) then ``API_URL``. A bare host is
    accepted with or without the ``/api/v1`` suffix, mirroring the React
    client's ``getApiBaseUrl`` normalisation.
    """
    raw = (os.environ.get("AGENT_API_URL") or os.environ.get("API_URL") or DEFAULT_API_HOST).strip()
    raw = raw.rstrip("/")
    return raw if raw.endswith(API_PREFIX) else f"{raw}{API_PREFIX}"


def api_key() -> str:
    """Optional key for agent-api's opt-in API-key middleware (empty = none)."""
    return os.environ.get("AGENT_API_KEY", "").strip()


# Default request timeout (seconds). Long-running calls (agent execution,
# reindex, git checkout) pass ``timeout=None`` explicitly.
DEFAULT_TIMEOUT = float(os.environ.get("AGENT_API_TIMEOUT", "30"))

APP_VERSION = "3.0.0"
