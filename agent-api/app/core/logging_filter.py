"""Logging filter that automatically redacts secrets from log output."""
from __future__ import annotations

import logging

from app.core.redact import redact


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from log messages.

    Install on the root logger (or any handler) so that every record
    passes through ``redact()`` before it is formatted and emitted.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: redact(str(v)) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact(str(a)) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            pass
        return True
