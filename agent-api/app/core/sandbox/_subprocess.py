"""Shared async subprocess runner used by the native sandbox backends."""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from app.core.sandbox.base import SandboxResult


async def run_argv(
    argv: List[str],
    *,
    backend: str,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 60,
) -> SandboxResult:
    """Run *argv* as a subprocess with a hard timeout, capturing output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return SandboxResult(
            stdout="", stderr=f"sandbox backend unavailable: {exc}",
            exit_code=127, backend=backend,
        )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return SandboxResult(
            stdout="", stderr=f"command timed out after {timeout}s",
            exit_code=124, backend=backend, timed_out=True,
        )
    return SandboxResult(
        stdout=(out or b"").decode("utf-8", "replace"),
        stderr=(err or b"").decode("utf-8", "replace"),
        exit_code=proc.returncode if proc.returncode is not None else -1,
        backend=backend,
    )
