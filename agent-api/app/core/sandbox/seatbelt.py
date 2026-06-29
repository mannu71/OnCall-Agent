"""macOS Seatbelt (``sandbox-exec``) backend.

Applies a minimal SBPL profile: deny by default, allow process exec and reads,
allow writes only under the working directory, and deny network unless requested.
"""
from __future__ import annotations

import shutil
import sys
from typing import Dict, List, Optional

from app.core.sandbox._subprocess import run_argv
from app.core.sandbox.base import Command, Sandbox, SandboxResult, normalize_command


def _profile(cwd: str, network: bool) -> str:
    net = "(allow network*)" if network else "(deny network*)"
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow file-read*)\n"
        "(allow sysctl-read)\n"
        f'(allow file-write* (subpath "{cwd}"))\n'
        '(allow file-write* (subpath "/private/tmp") (subpath "/private/var/tmp"))\n'
        f"{net}\n"
    )


class SeatbeltSandbox(Sandbox):
    name = "seatbelt"

    def available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

    def _argv(self, command: List[str], *, cwd: str, network: bool) -> List[str]:
        return ["sandbox-exec", "-p", _profile(cwd, network), *command]

    async def run(
        self,
        command: Command,
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 60,
        network: bool = False,
    ) -> SandboxResult:
        argv = self._argv(normalize_command(command), cwd=cwd, network=network)
        return await run_argv(argv, backend=self.name, cwd=cwd, env=env, timeout=timeout)
