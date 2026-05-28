"""SQL pipeline parser — turns raw SQL text into a list of :class:`Statement`.

Grammar (informal):

* A *directive* is a SQL comment line whose body starts with a keyword:

  - ``-- label: <name>``   human-readable name for the next statement
  - ``-- db: <name>``      MCP server label for the next statement
  - ``-- as: <name>``      variable name to bind the result under (new)

  Whitespace between ``--`` and the keyword is optional. The keyword match is
  case-insensitive. Each directive applies to the **next** statement only —
  directives do not leak across statement boundaries. (The legacy parser
  leaked ``-- db:`` between statements; this is intentionally fixed here.)

* A *statement* is everything between directives, terminated by a semicolon at
  end-of-line. Blank lines and non-directive comment lines (``-- some prose``)
  are stripped from the SQL body to keep the rendered query readable.

* A trailing statement that lacks a terminating semicolon is still emitted —
  matches the legacy behaviour and lets authors omit the last ``;``.

The parser also extracts the set of variable references (``{var}`` and
``{var.column}``) from each statement's body so the graph module doesn't need
to re-scan the SQL.
"""
from __future__ import annotations

import re
from typing import FrozenSet, List, Optional, Tuple

from .scope import built_in_names
from .statement import Statement


# Matches `{var}` or `{var.column.something}` — we capture the entire path
# but extract only the top-level name into `references`.
_TEMPLATE_VAR_RE = re.compile(r"\{([^}]+)\}")

# Directive recognisers — case-insensitive on the keyword, allows `--key:` or
# `-- key:` and arbitrary internal whitespace.
_DIRECTIVE_RE = re.compile(
    r"""
    ^\s*--\s*                   # comment opener (already-stripped line)
    (?P<key>label|db|as)        # one of the three directive keywords
    \s*:\s*                     # colon separator with optional spaces
    (?P<value>.+?)              # the value (non-greedy)
    \s*$                        # trailing whitespace
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse(sql_text: str) -> List[Statement]:
    """Parse ``sql_text`` into an ordered list of :class:`Statement`.

    Empty input or input containing only comments returns an empty list.
    """
    statements: List[Statement] = []

    pending_label: Optional[str] = None
    pending_db: Optional[str] = None
    pending_as: Optional[str] = None
    body_lines: List[str] = []
    counter = 1

    for raw_line in sql_text.splitlines():
        stripped = raw_line.strip()

        # Directive line — capture and continue (do NOT add to SQL body).
        directive = _match_directive(stripped)
        if directive is not None:
            key, value = directive
            if key == "label":
                pending_label = value
            elif key == "db":
                pending_db = value
            elif key == "as":
                pending_as = value
            continue

        # Non-directive comment or blank line — skip entirely.
        if not stripped or stripped.startswith("--"):
            continue

        body_lines.append(raw_line)

        # Statement terminator — emit and reset.
        if stripped.endswith(";"):
            stmt = _build(
                body_lines, pending_label, pending_db, pending_as, counter
            )
            if stmt is not None:
                statements.append(stmt)
                counter += 1
            body_lines = []
            pending_label = None
            pending_db = None
            pending_as = None

    # Trailing statement without semicolon.
    if body_lines:
        stmt = _build(body_lines, pending_label, pending_db, pending_as, counter)
        if stmt is not None:
            statements.append(stmt)

    return statements


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match_directive(stripped_line: str) -> Optional[Tuple[str, str]]:
    """Return ``(keyword, value)`` for a directive line, else ``None``.

    Keyword is normalised to lowercase. ``value`` keeps its original case so
    label text and server names round-trip unchanged.
    """
    if not stripped_line.startswith("--"):
        return None
    match = _DIRECTIVE_RE.match(stripped_line)
    if not match:
        return None
    return match.group("key").lower(), match.group("value").strip()


def _build(
    body_lines: List[str],
    label: Optional[str],
    database: Optional[str],
    promote_as: Optional[str],
    counter: int,
) -> Optional[Statement]:
    """Assemble a :class:`Statement` from accumulated lines.

    Returns ``None`` if the body is whitespace-only (the parser sometimes sees
    a stray semicolon after a comment block).
    """
    sql_text = "\n".join(body_lines).strip()
    if not sql_text:
        return None

    stmt_id = f"stmt_{counter}"
    return Statement(
        id=stmt_id,
        label=(label or stmt_id),
        database=database,
        promote_as=promote_as,
        sql=sql_text,
        references=_extract_references(sql_text),
    )


def _extract_references(sql: str) -> FrozenSet[str]:
    """Return the set of top-level variable names referenced in ``sql``.

    Built-ins (``current_date`` etc.) are excluded so they don't create
    phantom dependency edges in the graph.
    """
    builtins = {b.lower() for b in built_in_names()}
    seen: set = set()
    for match in _TEMPLATE_VAR_RE.finditer(sql):
        top = match.group(1).strip().split(".")[0].strip()
        if top and top.lower() not in builtins:
            seen.add(top)
    return frozenset(seen)
