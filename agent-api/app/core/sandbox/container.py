"""Ephemeral Docker container sandbox backend.

The primary backend for this platform (Windows host + Docker). Each command runs
in a throwaway ``docker run --rm`` container with: no network by default
(``--network none``), a read-only root filesystem, a writable bind of the working
directory at ``/work``, dropped Linux capabilities, a PID cap, and memory/CPU
limits, implemented with the Docker that already runs this stack.
"""
from __future__ import annotations

import shutil
from typing import Dict, List, Optional

from app.core.sandbox._subprocess import run_argv
from app.core.sandbox.base import Command, Sandbox, SandboxResult, normalize_command

_WORKDIR = "/work"


class ContainerSandbox(Sandbox):
    name = "container"

    def __init__(
        self,
        *,
        image: str = "python:3.12-slim",
        memory: str = "512m",
        cpus: str = "1",
    ) -> None:
        self._image = image
        self._memory = memory
        self._cpus = cpus

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def _argv(self, command: List[str], *, cwd: str, env: Optional[Dict[str, str]], network: bool) -> List[str]:
        argv: List[str] = [
            "docker", "run", "--rm",
            "--network", "bridge" if network else "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,exec,nosuid,size=64m",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
            "--memory", self._memory,
            "--cpus", self._cpus,
            "-v", f"{cwd}:{_WORKDIR}:rw",
            "-w", _WORKDIR,
        ]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]
        argv += [self._image, *command]
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
        argv = self._argv(normalize_command(command), cwd=cwd, env=env, network=network)
        # env is injected via -e flags, not the docker client's own environment.
        return await run_argv(argv, backend=self.name, cwd=None, env=None, timeout=timeout)
