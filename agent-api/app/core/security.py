"""Security guards for all outbound HTTP calls and filesystem access.

- OS-level isolation (Docker/subprocess) is the real security boundary.
- In-process guards here are accident-prevention layers, not containment.
- SSRF guard: block private/reserved IP ranges, only allow HTTP/HTTPS.
- Path jail: keep filesystem access inside an allowed root directory.
- Write denylist: block writes to SSH keys, AWS creds, sudoers, etc.
- Injection scan: cooperative detection of prompt-injection patterns.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


# ─────────────────────────────────────────────────────────────────────────────
# Credential / sensitive-file write protection
# ─────────────────────────────────────────────────────────────────────────────

def _build_write_denied_paths() -> frozenset[str]:
    """Exact file paths that must never be written to."""
    home = str(Path.home())
    return frozenset([
        f"{home}/.ssh/authorized_keys",
        f"{home}/.ssh/known_hosts",
        f"{home}/.ssh/config",
        f"{home}/.bashrc",
        f"{home}/.bash_profile",
        f"{home}/.zshrc",
        f"{home}/.profile",
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
        "/etc/hosts",
        "/etc/ssh/sshd_config",
    ])


def _build_write_denied_prefixes() -> tuple[str, ...]:
    """Directory prefixes where writes are never permitted."""
    home = str(Path.home())
    return (
        f"{home}/.ssh/",
        f"{home}/.aws/",
        f"{home}/.gnupg/",
        f"{home}/.kube/",
        f"{home}/.docker/",
        f"{home}/.config/gcloud/",
        "/etc/sudoers.d/",
        "/etc/cron.d/",
    )


_WRITE_DENIED_PATHS: frozenset[str] = _build_write_denied_paths()
_WRITE_DENIED_PREFIXES: tuple[str, ...] = _build_write_denied_prefixes()


def get_safe_write_root() -> Optional[str]:
    """Return ``AGENT_WRITE_SAFE_ROOT`` env var if set — restricts all writes
    to that directory tree."""
    return os.getenv("AGENT_WRITE_SAFE_ROOT")


# ─────────────────────────────────────────────────────────────────────────────
# Private / reserved IP ranges — never route outbound to these
# ─────────────────────────────────────────────────────────────────────────────
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),   # Shared address space (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / AWS metadata service
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),    # Benchmark testing
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),   # TEST-NET-3
    ipaddress.ip_network("240.0.0.0/4"),      # Reserved
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


# ─────────────────────────────────────────────────────────────────────────────
# Prompt-injection heuristics (cooperative / accident-prevention only)
# ─────────────────────────────────────────────────────────────────────────────
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+(instructions?|prompts?)", re.I),
    re.compile(r"you\s+are\s+now\b", re.I),
    re.compile(r"disregard\s+(your|all)\s+(previous|prior|instructions?)", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"\bDAN\s*mode\b", re.I),
    re.compile(r"act\s+as\s+(an?\s+)?(unrestricted|unfiltered|evil|malicious)", re.I),
    re.compile(r"forget\s+(your\s+)?(training|guidelines|restrictions?)", re.I),
    re.compile(r"new\s+persona", re.I),
    re.compile(r"system\s+prompt\s*:", re.I),
    re.compile(r"<\s*/?\s*(system|instructions?)\s*>", re.I),
]


# ─────────────────────────────────────────────────────────────────────────────
# Exception types
# ─────────────────────────────────────────────────────────────────────────────

class SSRFError(ValueError):
    """Raised when a URL is blocked by the SSRF guard."""


class PathJailError(ValueError):
    """Raised when a file path escapes the allowed jail directory."""


class WriteBlockedError(ValueError):
    """Raised when a write to a sensitive credential/system file is attempted."""


class InjectionError(ValueError):
    """Raised when prompt injection is detected in user-supplied text."""


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_host(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve a hostname to IP addresses (blocking I/O)."""
    try:
        results = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return [ipaddress.ip_address(r[4][0]) for r in results]
    except (socket.gaierror, ValueError):
        return []


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(addr in net for net in _BLOCKED_NETWORKS)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_write_denied(path: str | Path) -> bool:
    """Return True when writing to *path* should be blocked.

    Checks (in order):
    1. Exact denylist (SSH keys, /etc/passwd, sudoers, shell rc files)
    2. Directory prefix denylist (.ssh/, .aws/, .gnupg/, .kube/, .docker/)
    3. AGENT_WRITE_SAFE_ROOT containment

    Returns True (blocked) / False (allowed).
    """
    resolved = str(Path(path).resolve())

    if resolved in _WRITE_DENIED_PATHS:
        return True

    if resolved.startswith(_WRITE_DENIED_PREFIXES):
        return True

    safe_root = get_safe_write_root()
    if safe_root:
        jail = str(Path(safe_root).resolve())
        if not resolved.startswith(jail + "/") and resolved != jail:
            return True

    return False


def check_write_path(path: str | Path) -> Path:
    """Assert that *path* may be written to.

    Raises:
        WriteBlockedError: When the path is in the credential denylist or
            outside the safe-write-root jail.
    """
    if is_write_denied(path):
        raise WriteBlockedError(
            f"Write to '{path}' is blocked — path is a sensitive credential "
            "or system file."
        )
    return Path(path).resolve()


# Internal agent directories that should not be read by tools
_INTERNAL_READ_BLOCK_DIRS: tuple[str, ...] = (
    str(Path.home() / ".kyc_protect" / "cache"),
    str(Path.home() / ".kyc_protect" / "skills"),
)


def check_read_path(path: str | Path, jail: Optional[str | Path] = None) -> Path:
    """Validate that *path* is safe to read.

    Blocks:
    - Reads from internal agent cache directories (prompt-injection vector).
    - Path traversal if *jail* is provided.

    Returns the resolved Path when safe.
    """
    resolved = Path(path).resolve()

    for blocked in _INTERNAL_READ_BLOCK_DIRS:
        if str(resolved).startswith(blocked):
            raise PathJailError(
                f"Read from '{resolved}' is blocked — use the designated tools "
                "to access agent cache data (prompt-injection prevention)."
            )

    if jail is not None:
        return check_path(resolved, jail)

    return resolved


def check_ssrf(url: str, allowlist: Optional[list[str]] = None) -> None:
    """Validate *url* is safe to fetch.

    Blocks:
    - Non-HTTP/HTTPS schemes
    - Private / reserved IP ranges (incl. AWS metadata 169.254.169.254)
    - Hosts that resolve to a blocked IP

    Args:
        url: The URL to validate.
        allowlist: Optional list of exact hostnames. When provided only these
            hosts are permitted regardless of IP check result.

    Raises:
        SSRFError: When the URL is not safe to request.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(
            f"Scheme '{parsed.scheme}' is not allowed (only http/https permitted)"
        )

    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no resolvable hostname")

    if allowlist is not None:
        if host not in allowlist:
            raise SSRFError(f"Host '{host}' is not in the configured SSRF allowlist")
        return

    # Try parsing as a raw IP address first (no DNS lookup needed)
    try:
        addr = ipaddress.ip_address(host)
        if _is_blocked_ip(addr):
            raise SSRFError(f"IP address '{host}' is in a blocked network range")
        return
    except ValueError:
        pass  # Not a raw IP — resolve via DNS

    resolved = _resolve_host(host)
    if not resolved:
        raise SSRFError(f"Cannot resolve hostname '{host}'")

    for addr in resolved:
        if _is_blocked_ip(addr):
            raise SSRFError(
                f"Host '{host}' resolves to '{addr}' which is in a blocked network range"
            )


def check_path(path: str | Path, jail: str | Path) -> Path:
    """Ensure *path* is within the *jail* directory.

    Args:
        path: The path to validate (relative or absolute).
        jail: The allowed root directory.

    Returns:
        Resolved absolute Path confirmed to be inside *jail*.

    Raises:
        PathJailError: When *path* would escape *jail*.
    """
    resolved = Path(path).resolve()
    jail_resolved = Path(jail).resolve()

    try:
        resolved.relative_to(jail_resolved)
    except ValueError:
        raise PathJailError(
            f"Path '{resolved}' escapes the allowed directory '{jail_resolved}'"
        )

    return resolved


def scan_injection(text: str) -> None:
    """Scan *text* for common prompt-injection patterns.

    This is a cooperative / accident-prevention layer only — not a security
    boundary against an adversarial LLM. OS-level isolation (Docker,
    subprocess jailing) is the real containment mechanism.

    Raises:
        InjectionError: When a suspicious pattern is detected.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise InjectionError(
                f"Potential prompt injection detected (pattern: {pattern.pattern!r})"
            )
