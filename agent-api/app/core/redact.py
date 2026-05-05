"""Secret redaction utilities for log sanitization.

Provides ``redact()`` to scrub API keys, tokens,
credentials, and other sensitive values from strings before
they reach logs or external systems.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_REDACTION_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "sk-****"),
    (re.compile(r"sk-ant-[a-zA-Z0-9]{20,}"), "sk-ant-****"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "AKIA****"),
    (re.compile(r"aws_secret_access_key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})"), "aws_secret_access_key=****"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer ****"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "ghp_****"),
    (re.compile(r"gho_[a-zA-Z0-9]{36}"), "gho_****"),
    (re.compile(r"ghu_[a-zA-Z0-9]{36}"), "ghu_****"),
    (re.compile(r"ghs_[a-zA-Z0-9]{36}"), "ghs_****"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{22,}"), "github_pat_****"),
    (re.compile(r"postgres(ql)?://[^:]+:[^@]+@"), "postgres://****@"),
    (re.compile(r"mysql://[^:]+:[^@]+@"), "mysql://****@"),
    (re.compile(r"mongodb(\+srv)?://[^:]+:[^@]+@"), "mongodb://****@"),
    (re.compile(r"redis://[^:]*:[^@]+@"), "redis://****@"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "[REDACTED KEY]"),
    (re.compile(r"xox[bpras]-[a-zA-Z0-9\-]+"), "xox****"),
    (re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[a-zA-Z0-9]{24}"), "hooks.slack.com/****"),
    (re.compile(r"(?:api_?key|secret_?key|access_?key|auth_?token|api_?secret|private_?key|password|passwd)\b['\"]?\s*[=:]\s*['\"]?([^\s'\"]{8,})"), '"[key]": "****"'),
    (re.compile(r'"(?:api_?key|secret_?key|token|password|apiKey|apiSecret|accessToken|secretAccessKey)"\s*:\s*"[^"]+"'), '"[key]": "****"'),
]


def redact(text: str) -> str:
    """Apply all redaction patterns to *text*, returning the sanitized string."""
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
