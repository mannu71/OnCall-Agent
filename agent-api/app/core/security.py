"""Security guards for all outbound HTTP calls and filesystem access.

Design follows the Hermes security model:
- OS-level isolation (Docker/subprocess) is the real security boundary.
- In-process guards here are accident-prevention layers, not containment.
- SSRF guard: block private/reserved IP ranges, only allow HTTP/HTTPS.
- Path jail: keep filesystem access inside an allowed root directory.
- Injection scan: cooperative detection of prompt-injection patterns.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


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
    subprocess jailing) is the real containment mechanism per the Hermes
    security model.

    Raises:
        InjectionError: When a suspicious pattern is detected.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise InjectionError(
                f"Potential prompt injection detected (pattern: {pattern.pattern!r})"
            )
