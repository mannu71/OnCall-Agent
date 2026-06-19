"""Tool-execution sandbox.

Public surface:

* :func:`get_sandbox` — resolve the configured backend (or ``None`` when disabled
  / unavailable).
* :func:`run_sandboxed` — convenience: select a backend and run one command.
* :func:`is_enabled` — whether sandboxing is configured on.
* :func:`build_sandboxed_command_tool` — a ready-to-wire ``run_command`` agent
  tool that executes through the sandbox. Opt-in: it is *not* added to agents by
  default (default backend is ``disabled``), so the existing action space and the
  prompt-cache prefix are unchanged until an operator turns sandboxing on.

Backend selection (``settings.sandbox_backend``): ``disabled`` (default) →
``None``; ``container``/``bwrap``/``seatbelt`` → that backend; ``auto`` → the
first available of container → bwrap → seatbelt.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.sandbox.base import Command, Sandbox, SandboxResult, normalize_command

logger = logging.getLogger(__name__)


def _make_backend(name: str) -> Optional[Sandbox]:
    from app.config import settings

    if name == "container":
        from app.core.sandbox.container import ContainerSandbox
        return ContainerSandbox(
            image=settings.sandbox_image,
            memory=settings.sandbox_memory,
            cpus=settings.sandbox_cpus,
        )
    if name == "bwrap":
        from app.core.sandbox.bwrap import BwrapSandbox
        return BwrapSandbox()
    if name == "seatbelt":
        from app.core.sandbox.seatbelt import SeatbeltSandbox
        return SeatbeltSandbox()
    return None


def get_sandbox(backend: Optional[str] = None) -> Optional[Sandbox]:
    """Return the configured/available sandbox, or ``None`` when disabled."""
    from app.config import settings

    name = (backend or settings.sandbox_backend or "disabled").lower()
    if name in ("disabled", "none", "off", ""):
        return None

    if name == "auto":
        for candidate in ("container", "bwrap", "seatbelt"):
            sb = _make_backend(candidate)
            if sb is not None and sb.available():
                return sb
        logger.warning("sandbox: backend=auto but no isolation backend is available")
        return None

    sb = _make_backend(name)
    if sb is None:
        logger.warning("sandbox: unknown backend %r; treating as disabled", name)
        return None
    if not sb.available():
        logger.warning("sandbox: backend %r is not available on this host", name)
        return None
    return sb


def is_enabled() -> bool:
    return get_sandbox() is not None


async def run_sandboxed(
    command: Command,
    *,
    cwd: str,
    env: Optional[dict] = None,
    timeout: Optional[int] = None,
    network: Optional[bool] = None,
    backend: Optional[str] = None,
) -> Optional[SandboxResult]:
    """Run *command* in the configured sandbox; ``None`` when sandboxing is off."""
    from app.config import settings

    sb = get_sandbox(backend)
    if sb is None:
        return None
    return await sb.run(
        command,
        cwd=cwd,
        env=env,
        timeout=timeout if timeout is not None else settings.sandbox_timeout_seconds,
        network=settings.sandbox_network if network is None else network,
    )


def build_sandboxed_command_tool(cwd: str) -> Any:
    """Build a ``run_command`` StructuredTool that executes inside the sandbox.

    Opt-in helper for callers that want to give an agent shell access *safely*.
    Returns ``None`` when sandboxing is disabled so callers can simply skip it.
    The tool is gated 'ask' by name via the policy engine's ``ask_on_os_tools`` /
    ``run_command`` pattern, giving HITL + isolation defense-in-depth.
    """
    if not is_enabled():
        return None
    from langchain_core.tools import StructuredTool

    async def _run_command(command: str) -> str:
        result = await run_sandboxed(command, cwd=cwd)
        if result is None:
            return json.dumps({"error": "sandbox disabled"})
        return json.dumps(result.to_dict())

    return StructuredTool.from_function(
        coroutine=_run_command,
        name="run_command",
        description=(
            "Run a shell command inside an isolated sandbox (no network by "
            "default, writes confined to the working directory). Returns "
            "stdout/stderr/exit_code."
        ),
    )


__all__ = [
    "Command",
    "Sandbox",
    "SandboxResult",
    "build_sandboxed_command_tool",
    "get_sandbox",
    "is_enabled",
    "normalize_command",
    "run_sandboxed",
]
