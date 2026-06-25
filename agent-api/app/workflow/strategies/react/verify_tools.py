"""Verify tool — close the edit → verify → fix loop.

After the agent applies a fix with ``edit_file`` / ``create_file``, it needs a way
to PROVE the change is good (tests pass, types check, lint is clean) and to SEE the
failure when it isn't — so it can iterate. ``run_verify`` runs an operator-configured
command (e.g. ``pytest -q``, ``tsc --noEmit``, ``ruff check``) inside the existing
isolated sandbox, but bound to the REAL repository directory
(``REPOS_BASE_PATH/<repo>``) rather than the throwaway scratch dir that ``run_command``
uses — so the verify exercises the just-edited code.

Safety / design:
  * The command is OPERATOR-configured (per-workflow ``verify_command``), not chosen by
    the agent — the agent only triggers it with a ``repo``. This keeps arbitrary shell
    out of the model's hands while still closing the loop.
  * Runs through the same hardened sandbox backend as ``run_command``
    (``app.core.sandbox`` — ephemeral container, no network, dropped caps), so it's a
    no-op when sandboxing is disabled.
  * Path-jailed to ``REPOS_BASE_PATH`` via ``check_path``.
  * Gated ``ask`` by the permission layer (it executes code), like ``run_command``.
  * Output is tail-capped so a noisy test run doesn't blow the agent's context — test
    failures surface at the END of output, so we keep the tail.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Keep the last N chars of each stream. Failures (tracebacks, assertion diffs,
# compiler errors) appear at the tail, so tail-capping preserves the signal the
# agent needs to fix the code while bounding replay cost across ReAct iterations.
_STREAM_TAIL_CHARS = 4000


def _tail(text: str, limit: int = _STREAM_TAIL_CHARS) -> str:
    if not text or len(text) <= limit:
        return text or ""
    return "…[earlier output truncated]\n" + text[-limit:]


def _backend(image: Optional[str]) -> Any:
    """Resolve the sandbox backend, honouring an optional per-verify image override.

    When ``image`` is set we build a container backend with that image directly
    (the primary backend on this Windows + Docker host) so a JS repo can verify on
    ``node`` while a Python repo verifies on ``python``. Otherwise we use the
    configured backend (``app.core.sandbox.get_sandbox``).
    """
    from app.core import sandbox as _sandbox

    if image:
        try:
            from app.config import settings
            from app.core.sandbox.container import ContainerSandbox

            sb = ContainerSandbox(
                image=image,
                memory=settings.sandbox_memory,
                cpus=settings.sandbox_cpus,
            )
            return sb if sb.available() else None
        except Exception as exc:  # noqa: BLE001 — fall back to the default backend
            logger.warning("run_verify: image override %r failed (%s); using default", image, exc)
    return _sandbox.get_sandbox()


def build_verify_tool(
    command: str,
    *,
    image: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Optional[Any]:
    """Build the ``run_verify`` StructuredTool, or ``None`` when sandboxing is off.

    ``command`` is the fixed operator-configured verify command; the agent supplies
    only the ``repo`` to run it against.
    """
    from app.core import sandbox as _sandbox

    if not command or not command.strip():
        return None
    if not _sandbox.is_enabled():
        return None

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field as PydanticField
    from app.config import settings

    eff_timeout = int(timeout) if timeout else int(settings.sandbox_timeout_seconds)

    class _VerifyInput(BaseModel):
        repo: str = PydanticField(
            description="Repository name under REPOS_BASE_PATH to run the verify command in "
                        "(the repo you just edited).")

    async def _run_verify(repo: str) -> str:
        from app.core.security import check_path, PathJailError

        root = settings.repos_base_path
        repo_dir = os.path.join(root, repo)
        try:
            check_path(repo_dir, root)
        except PathJailError as exc:
            return json.dumps({"ok": False, "error": f"path outside the repository jail: {exc}",
                               "repo": repo})
        if not os.path.isdir(repo_dir):
            return json.dumps({"ok": False, "error": "repo not found under REPOS_BASE_PATH",
                               "repo": repo})

        sb = _backend(image)
        if sb is None:
            return json.dumps({"ok": False, "error": "sandbox disabled", "repo": repo})

        try:
            result = await sb.run(
                command,
                cwd=repo_dir,
                network=settings.sandbox_network,
                timeout=eff_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc), "repo": repo,
                               "command": command})

        payload = {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "repo": repo,
            "command": command,
            "stdout_tail": _tail(result.stdout),
            "stderr_tail": _tail(result.stderr),
        }
        logger.info("run_verify: %s in %s → exit=%s ok=%s", command, repo,
                    result.exit_code, result.ok)
        return json.dumps(payload)

    return StructuredTool.from_function(
        coroutine=_run_verify,
        name="run_verify",
        description=(
            "Run the project's configured verify command (tests/typecheck/lint) against a "
            f"repository inside an isolated sandbox. The command is fixed by the operator "
            f"('{command}'); you only choose the repo. Returns exit_code + stdout/stderr tails. "
            "WHEN TO USE: right AFTER you edit_file/create_file — run this to confirm the change "
            "is good. If exit_code != 0, READ the stderr/stdout tail, fix the code with another "
            "edit, and run_verify again. Repeat until it passes (exit_code 0) before you give "
            "your final answer. This executes code and REQUIRES operator approval."
        ),
        args_schema=_VerifyInput,
    )


__all__ = ["build_verify_tool"]
