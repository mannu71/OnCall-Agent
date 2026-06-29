"""Linux bubblewrap (``bwrap``) sandbox backend.

Read-only-binds the host root, bind-mounts only the working directory writable,
gives a private /tmp and isolated PID/IPC namespaces, and drops network unless
explicitly requested.
"""
from __future__ import annotations

import shutil
from typing import Dict, List, Optional

from app.core.sandbox._subprocess import run_argv
from app.core.sandbox.base import Command, Sandbox, SandboxResult, normalize_command


class BwrapSandbox(Sandbox):
    name = "bwrap"

    def available(self) -> bool:
        return shutil.which("bwrap") is not None

    def _argv(self, command: List[str], *, cwd: str, network: bool) -> List[str]:
        argv: List[str] = [
            "bwrap",
            "--ro-bind", "/", "/",          # read-only view of the host
            "--bind", cwd, cwd,             # working dir is the only writable path
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--chdir", cwd,
        ]
        if not network:
            argv += ["--unshare-net"]
        argv += ["--"]
        argv += command
        return argv

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
