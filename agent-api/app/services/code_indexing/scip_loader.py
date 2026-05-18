"""SCIP (Source Code Intelligence Protocol) index loader.

SCIP is Sourcegraph's open-source replacement for LSIF.  The ``scip-python``
CLI emits a portable protobuf index for any Python repo.  This module:

1. Invokes the ``scip-python index`` CLI as a subprocess.
2. Parses the resulting ``index.scip`` protobuf file.
3. Persists symbol definitions and cross-file references to the
   ``code_symbols`` / ``code_references`` tables.

Install requirements (not yet in requirements.txt — see TODO below):
    pip install scip-python          # the CLI
    pip install protobuf>=4.0        # Python protobuf runtime

SCIP proto schema reference:
    https://github.com/sourcegraph/scip/blob/main/scip.proto

Protobuf message types needed (in priority order):
    Index          — top-level wrapper  (field 1: metadata, field 3: documents)
    Document       — per-file data       (field 1: relative_path, field 3: occurrences, field 4: symbols)
    SymbolInformation — definition info  (field 1: symbol, field 3: kind, field 5: signature_documentation)
    Occurrence     — a single site       (field 1: range, field 2: symbol, field 3: symbol_roles)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class ScipBinaryMissing(RuntimeError):
    """Raised when the ``scip-python`` CLI is not found on PATH.

    Install with::

        pip install scip-python
    """


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ScipSymbol:
    """Represents a single symbol extracted from a SCIP index."""

    symbol_id: str
    """SCIP moniker — globally unique identifier for the symbol."""

    kind: str
    """Symbol kind: 'function', 'class', 'method', 'variable', etc."""

    file_path: str
    """Repo-relative file path where the symbol is *defined*."""

    line_start: int
    """1-based start line of the definition."""

    line_end: Optional[int] = None
    """1-based end line of the definition (may be None)."""

    signature: Optional[str] = None
    """Hover-documentation / signature extracted from SCIP."""

    language: str = "python"

    references: List[Tuple[str, int, str]] = field(default_factory=list)
    """List of (file_path, line, role) tuples where role is one of
    'definition', 'reference', 'import'."""

    relationships: List[Tuple[str, str]] = field(default_factory=list)
    """List of (other_symbol_id, relationship_kind) pairs."""


# ---------------------------------------------------------------------------
# Protobuf parser
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SCIP protobuf field numbers (from scip.proto)
# ---------------------------------------------------------------------------
# Index:
#   3 → repeated Document documents
# Document:
#   1 → string relative_path
#   3 → repeated Occurrence occurrences
#   4 → repeated SymbolInformation symbols
# SymbolInformation:
#   1 → string symbol
#   3 → SymbolKind kind (enum)
#   5 → Documentation signature_documentation
# Documentation:
#   2 → string text
# Occurrence:
#   1 → repeated int32 range (packed)
#   2 → string symbol
#   3 → int32 symbol_roles (bitmask: 1=definition, 8=import)

# SymbolKind enum values (incomplete — covers common cases)
_SYMBOL_KIND_MAP = {
    0: "unknown",
    1: "abstract_method",
    2: "accessor",
    3: "array",
    4: "boolean",
    5: "class",
    6: "constant",
    7: "constructor",
    8: "enum",
    9: "enum_member",
    10: "event",
    11: "field",
    12: "file",
    13: "function",
    14: "getter",
    15: "interface",
    16: "key",
    17: "method",
    18: "module",
    19: "namespace",
    20: "null",
    21: "number",
    22: "object",
    23: "operator",
    24: "package",
    25: "property",
    26: "setter",
    27: "string",
    28: "struct",
    29: "type_alias",
    30: "type_parameter",
    31: "unit",
    32: "value",
    33: "variable",
}

# symbol_roles bitmask
_ROLE_DEFINITION = 1
_ROLE_IMPORT = 8


def _iter_fields_v2(data: bytes) -> Iterator[Tuple[int, int, object]]:  # noqa: C901
    """Iterate over all (field_number, wire_type, value) in a protobuf blob."""
    pos = 0
    length = len(data)
    while pos < length:
        # Read tag varint
        tag = 0
        shift = 0
        while True:
            b = data[pos]
            pos += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7

        field_number = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:
            value_int = 0
            shift = 0
            while True:
                b = data[pos]
                pos += 1
                value_int |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field_number, wire_type, value_int

        elif wire_type == 1:
            value = int.from_bytes(data[pos : pos + 8], "little")
            pos += 8
            yield field_number, wire_type, value

        elif wire_type == 2:
            msg_len = 0
            shift = 0
            while True:
                b = data[pos]
                pos += 1
                msg_len |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            blob = data[pos : pos + msg_len]
            pos += msg_len
            yield field_number, wire_type, blob

        elif wire_type == 5:
            value = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
            yield field_number, wire_type, value

        else:
            raise ValueError(
                f"Unsupported protobuf wire_type={wire_type} at byte offset {pos}"
            )


def _parse_packed_int32(blob: bytes) -> List[int]:
    """Decode a packed repeated int32 / int64 varint field."""
    values: List[int] = []
    pos = 0
    while pos < len(blob):
        v = 0
        shift = 0
        while True:
            b = blob[pos]
            pos += 1
            v |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        values.append(v)
    return values


def _parse_occurrence(blob: bytes) -> Tuple[Optional[List[int]], Optional[str], int]:
    """Parse an Occurrence message.

    Returns (range_values, symbol, symbol_roles).
    """
    range_values: Optional[List[int]] = None
    symbol: Optional[str] = None
    symbol_roles: int = 0

    for fn, wt, val in _iter_fields_v2(blob):
        if fn == 1 and wt == 2:
            # packed int32 range
            range_values = _parse_packed_int32(val)  # type: ignore[arg-type]
        elif fn == 2 and wt == 2:
            symbol = val.decode("utf-8", errors="replace")  # type: ignore[union-attr]
        elif fn == 3 and wt == 0:
            symbol_roles = val  # type: ignore[assignment]

    return range_values, symbol, symbol_roles


def _parse_symbol_information(blob: bytes) -> Tuple[Optional[str], str, Optional[str]]:
    """Parse a SymbolInformation message.

    Returns (symbol_id, kind_str, signature).
    """
    symbol_id: Optional[str] = None
    kind_int: int = 0
    signature: Optional[str] = None

    for fn, wt, val in _iter_fields_v2(blob):
        if fn == 1 and wt == 2:
            symbol_id = val.decode("utf-8", errors="replace")  # type: ignore[union-attr]
        elif fn == 3 and wt == 0:
            kind_int = val  # type: ignore[assignment]
        elif fn == 5 and wt == 2:
            # Documentation message; extract field 2 (text)
            for dfn, _dwt, dval in _iter_fields_v2(val):  # type: ignore[arg-type]
                if dfn == 2:
                    signature = dval.decode("utf-8", errors="replace")  # type: ignore[union-attr]

    kind_str = _SYMBOL_KIND_MAP.get(kind_int, "unknown")
    return symbol_id, kind_str, signature


def _parse_document(blob: bytes, repo_path: str) -> List[ScipSymbol]:
    """Parse a Document message and return a list of ScipSymbol objects."""

    relative_path: Optional[str] = None
    occurrences: List[bytes] = []
    symbol_infos: List[bytes] = []

    for fn, wt, val in _iter_fields_v2(blob):
        if fn == 1 and wt == 2:
            relative_path = val.decode("utf-8", errors="replace")  # type: ignore[union-attr]
        elif fn == 3 and wt == 2:
            occurrences.append(val)  # type: ignore[arg-type]
        elif fn == 4 and wt == 2:
            symbol_infos.append(val)  # type: ignore[arg-type]

    if not relative_path:
        return []

    # Build a map: symbol_id -> ScipSymbol from SymbolInformation entries
    sym_map: dict[str, ScipSymbol] = {}
    for si_blob in symbol_infos:
        sym_id, kind_str, sig = _parse_symbol_information(si_blob)
        if sym_id:
            sym_map[sym_id] = ScipSymbol(
                symbol_id=sym_id,
                kind=kind_str,
                file_path=relative_path,
                line_start=0,  # filled in from occurrences below
                signature=sig,
            )

    # Process occurrences to fill in line numbers and references
    for occ_blob in occurrences:
        range_vals, sym_id, roles = _parse_occurrence(occ_blob)
        if not sym_id or not range_vals:
            continue

        line_start = range_vals[0] + 1  # SCIP ranges are 0-based
        line_end = (range_vals[2] + 1) if len(range_vals) >= 4 else line_start

        is_definition = bool(roles & _ROLE_DEFINITION)
        is_import = bool(roles & _ROLE_IMPORT)
        role = "definition" if is_definition else ("import" if is_import else "reference")

        if is_definition:
            if sym_id not in sym_map:
                sym_map[sym_id] = ScipSymbol(
                    symbol_id=sym_id,
                    kind="unknown",
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                )
            else:
                sym_map[sym_id].file_path = relative_path
                sym_map[sym_id].line_start = line_start
                sym_map[sym_id].line_end = line_end
        else:
            # Add as reference on the owning symbol (best-effort)
            if sym_id in sym_map:
                sym_map[sym_id].references.append((relative_path, line_start, role))

    return list(sym_map.values())


def parse_scip_protobuf(path: Path) -> Iterator[ScipSymbol]:
    """Parse a SCIP ``index.scip`` protobuf file and yield :class:`ScipSymbol`.

    This is a hand-written minimal decoder that only reads the fields needed
    for cross-file symbol resolution:

    * ``Index.documents`` (field 3, repeated embedded message)
    * ``Document.relative_path`` (field 1, string)
    * ``Document.occurrences`` (field 3, repeated embedded message)
    * ``Document.symbols`` (field 4, repeated embedded message)
    * ``SymbolInformation.symbol`` (field 1, string)
    * ``SymbolInformation.kind`` (field 3, enum)
    * ``SymbolInformation.signature_documentation`` (field 5, embedded message)
    * ``Occurrence.range`` (field 1, packed int32)
    * ``Occurrence.symbol`` (field 2, string)
    * ``Occurrence.symbol_roles`` (field 3, int32 bitmask)

    TODO(phase2-finish): Replace with generated code from:
        python -m grpc_tools.protoc -I. --python_out=. scip.proto
        # or install the ``scip`` PyPI package if/when it becomes available
    """
    data = path.read_bytes()

    repo_path = str(path.parent)

    for fn, wt, val in _iter_fields_v2(data):
        if fn == 3 and wt == 2:
            # Document message
            try:
                symbols = _parse_document(val, repo_path)  # type: ignore[arg-type]
                yield from symbols
            except Exception as exc:
                logger.warning("Failed to parse SCIP document blob: %s", exc)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def _persist_symbols(
    session: AsyncSession,
    repo_name: str,
    symbols: List[ScipSymbol],
) -> int:
    """Upsert *symbols* into ``code_symbols`` / ``code_references``.

    Returns the number of new ``CodeSymbol`` rows inserted/updated.
    """
    from app.models.db_models import CodeReference, CodeSymbol  # late import

    now = datetime.now(timezone.utc)
    count = 0

    for sym in symbols:
        if not sym.symbol_id or not sym.file_path:
            continue

        # Upsert CodeSymbol
        stmt = select(CodeSymbol).where(
            CodeSymbol.repo_name == repo_name,
            CodeSymbol.symbol_id == sym.symbol_id,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is None:
            row = CodeSymbol(
                repo_name=repo_name,
                symbol_id=sym.symbol_id,
                kind=sym.kind,
                file_path=sym.file_path,
                line_start=sym.line_start,
                line_end=sym.line_end,
                signature=sym.signature,
                language=sym.language,
                indexed_at=now,
            )
            session.add(row)
            count += 1
        else:
            existing.kind = sym.kind
            existing.file_path = sym.file_path
            existing.line_start = sym.line_start
            existing.line_end = sym.line_end
            existing.signature = sym.signature
            existing.indexed_at = now

        # Delete old references for this symbol+repo before reinserting
        await session.execute(
            delete(CodeReference).where(
                CodeReference.repo_name == repo_name,
                CodeReference.symbol_id == sym.symbol_id,
            )
        )

        # Insert references
        for ref_file, ref_line, ref_role in sym.references:
            ref = CodeReference(
                repo_name=repo_name,
                symbol_id=sym.symbol_id,
                file_path=ref_file,
                line=ref_line,
                role=ref_role,
                caller_symbol=None,  # TODO(phase2-finish): resolve enclosing symbol
            )
            session.add(ref)

    await session.flush()
    return count


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def load_scip_index(
    repo_name: str,
    repo_path: str,
    session: AsyncSession,
    output_path: Optional[str] = None,
) -> int:
    """Index a Python repository using ``scip-python`` and persist results.

    Args:
        repo_name:   Logical name for the repo (used as a partition key).
        repo_path:   Absolute path to the repo root on disk.
        session:     SQLAlchemy async session for persistence.
        output_path: Where to write ``index.scip``.  Defaults to
                     ``<repo_path>/index.scip``.

    Returns:
        Number of ``CodeSymbol`` rows inserted/updated.

    Raises:
        ScipBinaryMissing: if ``scip-python`` is not on PATH.
        subprocess.CalledProcessError: if the CLI exits with a non-zero code.
    """
    if shutil.which("scip-python") is None:
        raise ScipBinaryMissing(
            "scip-python binary not found on PATH. "
            "Install with: pip install scip-python"
        )

    output_file = Path(output_path) if output_path else Path(repo_path) / "index.scip"

    cmd = [
        "scip-python",
        "index",
        "--output",
        str(output_file),
        "--cwd",
        repo_path,
        repo_path,
    ]

    logger.info(
        "Running scip-python indexer for repo=%s path=%s", repo_name, repo_path
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes for large repos
            cwd=repo_path,
        )
    except FileNotFoundError as exc:
        raise ScipBinaryMissing(
            "scip-python binary not found on PATH. "
            "Install with: pip install scip-python"
        ) from exc

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            cmd,
            output=proc.stdout,
            stderr=proc.stderr,
        )

    logger.info("scip-python finished. Parsing %s", output_file)

    if not output_file.exists():
        raise FileNotFoundError(
            f"scip-python completed but output file not found: {output_file}"
        )

    symbols: List[ScipSymbol] = list(parse_scip_protobuf(output_file))
    logger.info("Parsed %d symbols from SCIP index", len(symbols))

    count = await _persist_symbols(session, repo_name, symbols)
    logger.info("Persisted %d new/updated symbols for repo=%s", count, repo_name)

    return count
