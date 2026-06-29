"""Policy registry — declarative config → composed :class:`ResolvedPolicy`.

The registry is an **allowlist**: a policy ``type`` in config must name a handler
registered here, otherwise it is rejected — config can select and parameterize
governance, but can never inject an arbitrary callable.

Config shape (a list of entries)::

    [
        {"type": "ask_on_os_tools"},
        {"type": "cost_budget", "params": {"max_cost_usd": 5.0,
                                            "ask_thresholds_usd": [3.0]}},
        {"type": "max_tool_calls_per_session", "params": {"limit": 50}},
    ]
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, List, Optional

from app.core.policy import builtins
from app.core.policy.types import Policy, ResolvedPolicy, _ResolveBuilder

logger = logging.getLogger(__name__)

# name → factory. Factories take keyword ``params`` only.
_REGISTRY: Dict[str, Callable[..., Policy]] = {
    "ask_on_os_tools": builtins.ask_on_os_tools,
    "ask_tools": builtins.ask_tools,
    "deny_tools": builtins.deny_tools,
    "allow_tools": builtins.allow_tools,
    "cost_budget": builtins.cost_budget,
    "max_tool_calls_per_session": builtins.max_tool_calls_per_session,
    "output_cap": builtins.output_cap,
    "loop_guardrails": builtins.loop_guardrails,
}


class PolicyConfigError(ValueError):
    """Raised when a policy config entry is malformed or names an unknown type."""


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def registered_names() -> List[str]:
    return sorted(_REGISTRY)


def _build_one(entry: Dict[str, Any]) -> Policy:
    if not isinstance(entry, dict):
        raise PolicyConfigError(f"policy entry must be an object, got {type(entry).__name__}")
    name = entry.get("type")
    if not name:
        raise PolicyConfigError("policy entry missing required 'type'")
    factory = _REGISTRY.get(name)
    if factory is None:
        raise PolicyConfigError(
            f"unknown policy type {name!r}; registered: {registered_names()}"
        )
    params = entry.get("params") or {}
    if not isinstance(params, dict):
        raise PolicyConfigError(f"policy {name!r} params must be an object")
    # Validate params against the factory signature (reject unknown keys early).
    sig = inspect.signature(factory)
    accepts_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if not accepts_kwargs:
        unknown = set(params) - set(sig.parameters)
        if unknown:
            raise PolicyConfigError(
                f"policy {name!r} got unexpected params {sorted(unknown)}; "
                f"accepts {sorted(sig.parameters)}"
            )
    try:
        return factory(**params)
    except TypeError as exc:  # missing required param, wrong type
        raise PolicyConfigError(f"policy {name!r}: {exc}") from exc


def build_policy_set(config: Optional[List[Dict[str, Any]]]) -> List[Policy]:
    """Turn declarative config into a list of :class:`Policy` instances."""
    if not config:
        return []
    if not isinstance(config, list):
        raise PolicyConfigError("policies config must be a list of entries")
    return [_build_one(entry) for entry in config]


def resolve(config: Optional[List[Dict[str, Any]]]) -> ResolvedPolicy:
    """Compose a config list into a single :class:`ResolvedPolicy`.

    An empty/None config yields an empty ResolvedPolicy; callers apply
    :meth:`ResolvedPolicy.with_defaults` to reproduce platform defaults.
    """
    builder = _ResolveBuilder()
    for policy in build_policy_set(config):
        policy.contribute(builder)
    return builder.finish()
