"""SQL renderer — substitute ``{var}`` / ``{var.column}`` against a :class:`Scope`.

Two render modes:

1. **Parameterised** (default, preferred): every variable becomes a positional
   placeholder (``$1``, ``$2``, …) and the bind value goes into a parallel
   list. Safe against SQL injection because the MCP postgres driver passes
   the values to a parameterised query underneath.

2. **Safe inline** (fallback): used only when the deployed MCP postgres server
   refuses a ``params`` array. Numeric/bool/null values are inlined directly;
   strings are single-quote-escaped. Still safer than the legacy unescaped
   ``replace()`` because it consistently quotes every string.

Callers ask for a specific mode via the ``parameterized`` flag — the pipeline
detects MCP capability once at startup and threads the result through.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Tuple

from .scope import Scope

logger = logging.getLogger(__name__)

# Same placeholder grammar the parser uses — kept here as a private constant
# rather than importing from parser to preserve the dependency direction
# (renderer must not depend on parser).
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


def render(
    sql: str, scope: Scope, *, parameterized: bool = True
) -> Tuple[str, List[Any]]:
    """Render ``sql`` against ``scope``.

    Returns ``(rendered_sql, bind_values)``. ``bind_values`` is empty when
    ``parameterized=False`` because every value was inlined.

    Unresolved variables become SQL ``NULL`` (parameterised mode binds the
    Python ``None``; inline mode emits the literal keyword). A warning is
    logged so authors can find typos quickly without the workflow silently
    doing the wrong thing — execution does not abort, matching the legacy
    behaviour.
    """
    if parameterized:
        return _render_parameterized(sql, scope)
    return _render_inline(sql, scope)


# ---------------------------------------------------------------------------
# Parameterized
# ---------------------------------------------------------------------------


def _render_parameterized(sql: str, scope: Scope) -> Tuple[str, List[Any]]:
    """Replace placeholders with ``$N`` and accumulate bind values."""
    binds: List[Any] = []

    def _replace(match: "re.Match[str]") -> str:
        path = match.group(1).strip()
        found, value = scope.resolve(path)
        if not found:
            logger.warning("SQL renderer: variable %r unresolved, binding NULL", path)
            value = None
        binds.append(value)
        return f"${len(binds)}"

    rendered = _PLACEHOLDER_RE.sub(_replace, sql)
    return rendered, binds


# ---------------------------------------------------------------------------
# Safe inline (fallback)
# ---------------------------------------------------------------------------


def _render_inline(sql: str, scope: Scope) -> Tuple[str, List[Any]]:
    """Replace placeholders with safely-formatted literals."""

    def _replace(match: "re.Match[str]") -> str:
        path = match.group(1).strip()
        found, value = scope.resolve(path)
        if not found:
            logger.warning("SQL renderer: variable %r unresolved, inlining NULL", path)
            return "NULL"
        return _format_sql_literal(value)

    rendered = _PLACEHOLDER_RE.sub(_replace, sql)
    return rendered, []


def _format_sql_literal(value: Any) -> str:
    """Format ``value`` as a SQL literal for safe inlining.

    Handles None, bool, int/float, and string. Dicts/lists become NULL with a
    warning — they're never a sensible inline target. A future commit could
    JSON-encode them if Postgres JSON columns become a use case.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    logger.warning(
        "SQL renderer: cannot inline value of type %s, substituting NULL",
        type(value).__name__,
    )
    return "NULL"
