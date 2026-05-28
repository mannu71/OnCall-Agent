"""Scope — single source of truth for variable lookup during pipeline execution.

Replaces the legacy ``SQLOrchestrator.variables`` dict + ``SQLOrchestrator.results``
dict + ``_get_var_lower_map()`` + ``_resolve_path()`` constellation with one
focused class that:

* Owns built-in vars (``current_date``, ``next_date``, ``current_date_time``)
  seeded at construction.
* Maintains a single lowercase index so case-insensitive lookups are O(1) and
  the index is built **once at insertion**, not rebuilt on every read.
* Knows how to absorb a statement's ``StatementResult`` into itself based on
  the statement's ``promote_as`` directive — no implicit column-name magic.
* Supports dotted-path lookup ``{VarName.column}`` for accessing a single
  column of a row-list value.

Pure, synchronous, no I/O. Trivial to unit-test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

from .statement import Statement, StatementResult


# Names the parser MUST NOT register as references (otherwise every statement
# that mentions {current_date} becomes a dependency on a phantom producer).
_BUILT_IN_NAMES = frozenset({"current_date", "next_date", "current_date_time"})


def built_in_names() -> frozenset:
    """Return the set of built-in variable names. Used by the parser/graph."""
    return _BUILT_IN_NAMES


class Scope:
    """Case-insensitive variable store with ``absorb()`` for result-promotion.

    The class is intentionally tiny — one dict, one lowercase index. Every
    method has a single, obvious responsibility.
    """

    def __init__(self, workflow_inputs: Optional[Dict[str, Any]] = None) -> None:
        # Storage: original-case name → value.
        self._values: Dict[str, Any] = {}
        # Index: lowercase name → original-case key. Built once at insertion;
        # never recomputed on read.
        self._lower_index: Dict[str, str] = {}

        # Seed built-ins.
        now_utc = datetime.now(timezone.utc)
        self._set("current_date", now_utc.strftime("%Y-%m-%d"))
        self._set("next_date", (now_utc + timedelta(days=1)).strftime("%Y-%m-%d"))
        self._set("current_date_time", now_utc.isoformat().replace("+00:00", "Z"))

        # Seed workflow inputs (e.g. parameters passed from the trigger).
        for key, value in (workflow_inputs or {}).items():
            self._set(key, value)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._lower_index

    def names(self) -> Iterable[str]:
        """Iterate over registered variable names (original case)."""
        return iter(self._values)

    def get(self, name: str, default: Any = None) -> Any:
        """Case-insensitive lookup of a top-level variable."""
        canonical = self._lower_index.get(name.lower())
        if canonical is None:
            return default
        return self._values[canonical]

    def resolve(self, path: str) -> Tuple[bool, Any]:
        """Resolve a dotted-path like ``WorklistRunId`` or ``WorklistRunId.id``.

        Returns ``(found, value)``. ``found=False`` means the path could not be
        resolved (missing variable, missing column, or non-dict navigation).
        Callers turn ``found=False`` into ``NULL`` in SQL.

        Single-element list values are auto-unwrapped (legacy ``_unwrap_array``
        parity — many MCP postgres responses come back as ``[{"col": 1}]``
        even for single-row SELECTs).
        """
        parts = [p.strip() for p in path.split(".") if p.strip()]
        if not parts:
            return False, None

        # Resolve base name.
        canonical = self._lower_index.get(parts[0].lower())
        if canonical is None:
            return False, None

        value: Any = _unwrap_single(self._values[canonical])

        # Walk subsequent dotted components — each must navigate into a dict.
        for part in parts[1:]:
            value = _unwrap_single(value)
            if not isinstance(value, dict):
                return False, None
            # Case-insensitive column lookup.
            col_key = next((k for k in value if k.lower() == part.lower()), None)
            if col_key is None:
                return False, None
            value = value[col_key]

        return True, _unwrap_single(value)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def absorb(self, statement: Statement, result: StatementResult) -> None:
        """Bind the result of ``statement`` into the scope if it has ``promote_as``.

        Promotion rules:

        * No ``promote_as`` ⇒ no binding (caller still gets ``result`` in the
          report).
        * Single-cell result (1 row × 1 col) ⇒ scope[promote_as] = scalar.
        * Multi-row or multi-col result ⇒ scope[promote_as] = first row (dict).
          Callers reference it via ``{name.column}``.
        * Empty result ⇒ scope[promote_as] = None.

        We bind the **first row** (not the full row list) for the multi-row
        case because every existing dotted-path consumer in the codebase
        reads only the first row anyway, and unwrapping here keeps the
        renderer simpler.
        """
        if not statement.promote_as or not result.success:
            return

        rows = result.rows or []
        if not rows:
            value: Any = None
        elif len(rows) == 1 and isinstance(rows[0], dict) and len(rows[0]) == 1:
            # Single cell — promote the scalar directly.
            (value,) = rows[0].values()
        else:
            value = rows[0]  # dict (first row) — supports {name.column}

        self._set(statement.promote_as, value)

    def bind(self, name: str, value: Any) -> None:
        """Public helper for tests and the compat shim — bind a name directly."""
        self._set(name, value)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set(self, name: str, value: Any) -> None:
        """Insert/update a variable, maintaining the lowercase index."""
        lower = name.lower()
        existing = self._lower_index.get(lower)
        if existing is not None and existing != name:
            # Same name in a different case — drop the old entry so we don't
            # leak two storage keys for one logical variable.
            self._values.pop(existing, None)
        self._values[name] = value
        self._lower_index[lower] = name


def _unwrap_single(value: Any) -> Any:
    """Unwrap a single-element list ⇒ its sole element. Empty list ⇒ None.

    Pure helper, exported only so tests can hit edge cases directly.
    """
    if isinstance(value, list):
        if len(value) == 0:
            return None
        if len(value) == 1:
            return value[0]
    return value
