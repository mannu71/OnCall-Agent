"""Sandbox abstraction — isolate shell / code-execution tools.

The design is a small provider interface with one backend per isolation
mechanism, adapted to this platform's Windows + Docker stack: the primary
backend is an ephemeral
Docker container (:mod:`app.core.sandbox.container`); the native ``bwrap``
(Linux) and ``seatbelt`` (macOS) wrappers are ported for completeness.

The interface is intentionally tiny: ``run`` a command with a working directory,
environment, timeout, and a network toggle, returning a structured result. The
caller (e.g. a future ``terminal`` / ``run_command`` tool, wired via
:func:`app.core.sandbox.wrap_exec_tools_with_sandbox`) decides how to surface it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Union

# A command may be a pre-split argv or a shell string (run via ``sh -c``).
Command = Union[str, Sequence[str]]


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of a sandboxed command."""

    stdout: str
    stderr: str
    exit_code: int
    backend: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> Dict[str, object]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "backend": self.backend,
            "timed_out": self.timed_out,
            "ok": self.ok,
        }


class Sandbox(abc.ABC):
    """A mechanism for running a command in isolation."""

    #: Stable identifier surfaced in :class:`SandboxResult.backend`.
    name: str = "base"

    @abc.abstractmethod
    def available(self) -> bool:
        """True iff this backend can run on the current host (binary present)."""

    @abc.abstractmethod
    async def run(
        self,
        command: Command,
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 60,
        network: bool = False,
    ) -> SandboxResult:
        """Run *command* in the sandbox and return its result.

        Args:
            command: argv sequence or a shell string.
            cwd: host directory bound as the working directory.
            env: environment variables exposed inside the sandbox.
            timeout: hard wall-clock limit in seconds.
            network: when False, network access is denied (the safe default).
        """


def normalize_command(command: Command) -> List[str]:
    """Coerce a command into an argv list, wrapping shell strings in ``sh -c``."""
    if isinstance(command, str):
        return ["sh", "-c", command]
    return list(command)
