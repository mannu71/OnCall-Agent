"""Declarative agent-spec import.

Turn a portable, version-controllable agent spec (``config.yaml`` + ``AGENTS.md``
+ ``tools/mcp/*.yaml``) into a runnable workflow. See :mod:`app.spec.types` for
the schema and the mapping to platform concepts.

Typical use::

    from app.spec import spec_to_workflow_dict, SpecValidationError
    workflow = spec_to_workflow_dict(my_spec_dict)   # validates, then imports
"""
from __future__ import annotations

from typing import Any, Dict

from app.spec.importer import import_spec_to_workflow
from app.spec.loader import load_spec_dict, load_spec_dir
from app.spec.types import AgentSpecConfig
from app.spec.validator import validate_spec


class SpecValidationError(ValueError):
    """Raised when a spec fails validation; ``errors`` holds all messages."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def spec_to_workflow_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate an in-memory spec dict and import it to a workflow dict."""
    spec = load_spec_dict(data)
    errors = validate_spec(spec)
    if errors:
        raise SpecValidationError(errors)
    return import_spec_to_workflow(spec)


def spec_dir_to_workflow_dict(path: str) -> Dict[str, Any]:
    """Load a spec directory, validate it, and import it to a workflow dict."""
    spec = load_spec_dir(path)
    errors = validate_spec(spec)
    if errors:
        raise SpecValidationError(errors)
    return import_spec_to_workflow(spec)


__all__ = [
    "AgentSpecConfig",
    "SpecValidationError",
    "import_spec_to_workflow",
    "load_spec_dict",
    "load_spec_dir",
    "spec_dir_to_workflow_dict",
    "spec_to_workflow_dict",
    "validate_spec",
]
