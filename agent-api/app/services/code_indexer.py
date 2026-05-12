"""Code Intelligence Indexer.

Walks a local repository, extracts function and class definitions using the
Python `ast` module (Python files) and regex patterns (TypeScript/TSX files),
embeds each chunk via AWS Bedrock Titan, and upserts into the code_chunks table.

Phase-split indexing
--------------------
``index_repo()`` now has two phases:

* **Phase 1 (fast, synchronous):** extract chunks + embeddings + raw call edges +
  import edges.  Returns once all DB writes are committed with
  ``enrichment_phase = 'raw'``.

* **Phase 2 (enrichment, async fire-and-forget):** edge-weight computation,
  pre-reindex call-graph snapshot, CODEOWNERS parsing, git-tag deployment
  inference.  Runs in the background via ``asyncio.create_task()``.  Tools
  still work during enrichment; they just use uniform edge weights (1.0) until
  Phase 2 completes and sets ``enrichment_phase = 'enriched'``.

Usage
-----
    from app.services.code_indexer import CodeIndexer

    indexer = CodeIndexer()
    chunks = await indexer.index_repo(
        repo_name="kyc-protect-api",
        local_path="/tmp/repos/kyc-protect-api",
        language="python",
        progress_callback=my_cb,   # optional: called (done, total) per file
    )
    print(f"Indexed {len(chunks)} chunks")
"""
from __future__ import annotations

import ast
import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.security import check_path, PathJailError

logger = logging.getLogger(__name__)

# Maximum file size to index (skip huge generated files)
_MAX_FILE_BYTES = 500_000
# Bedrock Titan embedding model
_EMBED_MODEL = "amazon.titan-embed-text-v2:0"
# For repos with more edges than this threshold, only the top-N most-accessed
# functions get semantic edge-weight computation (others stay at 1.0).
_EDGE_WEIGHT_THRESHOLD = int(os.getenv("EDGE_WEIGHT_THRESHOLD", "10000"))


# ─────────────────────────────────────────────────────────────────────────────
# Canonical entity ID
# ─────────────────────────────────────────────────────────────────────────────

def compute_entity_id(repo_name: str, normalized_signature: str) -> str:
    """Return a stable 16-char hex identity for a code entity.

    Derived from ``sha256(repo_name + '::' + normalized_signature)[:16]``.
    Survives: renames (if param names/types unchanged), file moves, module renames.
    Changes when: param names, types, or the function name changes.
    """
    key = f"{repo_name}::{normalized_signature.strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _build_normalized_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    """Build a deterministic signature string from a Python AST function node.

    Includes only: name, param names, param annotations, return annotation.
    Excludes: body, defaults, decorators, docstring.
    """
    args = []
    for arg in node.args.args:
        ann = ast.unparse(arg.annotation) if arg.annotation else ""
        args.append(f"{arg.arg}:{ann}" if ann else arg.arg)
    # Also handle *args and **kwargs for completeness
    if node.args.vararg:
        a = node.args.vararg
        ann = ast.unparse(a.annotation) if a.annotation else ""
        args.append(f"*{a.arg}:{ann}" if ann else f"*{a.arg}")
    if node.args.kwarg:
        a = node.args.kwarg
        ann = ast.unparse(a.annotation) if a.annotation else ""
        args.append(f"**{a.arg}:{ann}" if ann else f"**{a.arg}")
    ret = ast.unparse(node.returns) if node.returns else ""
    return f"{node.name}({','.join(args)})->{ret}"


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CodeChunk:
    """A single indexable unit of source code."""
    repo_name:  str
    file_path:  str
    name:       str
    chunk_type: str          # 'function' | 'class' | 'method'
    body:       str
    language:   str
    signature:  str  = ""
    docstring:  str  = ""
    line_start: int  = 0
    line_end:   int  = 0
    entity_id:  str  = ""   # stable identity across renames
    complexity: int  = 1   # approximate cyclomatic complexity
    embedding:  Optional[List[float]] = field(default=None, repr=False)


@dataclass
class LogPattern:
    """A log call site extracted from source code."""
    repo_name:        str
    file_path:        str
    function_name:    str
    log_level:        str
    message_template: str
    line_number:      int


@dataclass
class ImportEdge:
    """A single import relationship extracted from a source file."""
    repo_name:   str
    file_path:   str
    from_module: str
    import_name: str
    is_relative: bool = False


@dataclass
class CallEdge:
    """A caller → callee relationship extracted from a source file."""
    caller_repo: str
    caller_name: str
    caller_file: str
    callee_name: str
    callee_file: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Python AST extraction
# ─────────────────────────────────────────────────────────────────────────────

def _get_source_lines(source: str, node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 0), getattr(node, "end_lineno", 0)


def _extract_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str:
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        return node.body[0].value.value[:500]
    return ""


def _build_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = []
    for arg in node.args.args:
        ann = ast.unparse(arg.annotation) if arg.annotation else ""
        args.append(f"{arg.arg}: {ann}" if ann else arg.arg)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)}){ret}"


def extract_python_chunks(source: str, file_path: str, repo_name: str) -> List[CodeChunk]:
    """Parse *source* as Python and return one CodeChunk per function/class."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.debug("Skipping %s (syntax error): %s", file_path, exc)
        return []

    lines  = source.splitlines()
    chunks: List[CodeChunk] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _get_source_lines(source, node)
            body = "\n".join(lines[start - 1 : end]) if start and end else ""
            norm_sig = _build_normalized_signature(node)
            chunks.append(CodeChunk(
                repo_name=repo_name,
                file_path=file_path,
                name=node.name,
                chunk_type="function",
                body=body,
                language="python",
                signature=_build_signature(node),
                docstring=_extract_docstring(node),
                line_start=start,
                line_end=end,
                entity_id=compute_entity_id(repo_name, norm_sig),
                complexity=_python_node_complexity(node),
            ))
        elif isinstance(node, ast.ClassDef):
            start, end = _get_source_lines(source, node)
            body = "\n".join(lines[start - 1 : end]) if start and end else ""
            chunks.append(CodeChunk(
                repo_name=repo_name,
                file_path=file_path,
                name=node.name,
                chunk_type="class",
                body=body,
                language="python",
                docstring=_extract_docstring(node),
                line_start=start,
                line_end=end,
                entity_id=compute_entity_id(repo_name, f"class:{node.name}"),
                complexity=_python_node_complexity(node),
            ))

    return chunks


def extract_python_imports(
    source: str, file_path: str, repo_name: str,
) -> List[ImportEdge]:
    """Walk Python AST and return ImportEdge for each import statement."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    edges: List[ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            is_relative = (node.level or 0) > 0
            for alias in node.names:
                edges.append(ImportEdge(
                    repo_name=repo_name,
                    file_path=file_path,
                    from_module=module,
                    import_name=alias.name,
                    is_relative=is_relative,
                ))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(ImportEdge(
                    repo_name=repo_name,
                    file_path=file_path,
                    from_module=alias.name,
                    import_name=alias.name,
                    is_relative=False,
                ))
    return edges


def extract_python_calls(
    source: str, file_path: str, repo_name: str,
) -> List[CallEdge]:
    """Walk Python AST and return CallEdge for each function call site.

    Only records calls where the callee is a simple Name (direct function call),
    not attribute access chains like ``obj.method()`` — those are heuristically
    resolved later via the entity graph.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    edges: List[CallEdge] = []
    # Build a mapping line→function_name for caller attribution
    func_ranges: List[Tuple[int, int, str]] = []  # (start, end, name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_ranges.append((
                getattr(node, "lineno", 0),
                getattr(node, "end_lineno", 0),
                node.name,
            ))

    def _caller_at(lineno: int) -> str:
        # Return the innermost function that contains this line
        best = ""
        best_size = 10**9
        for start, end, name in func_ranges:
            if start <= lineno <= end:
                size = end - start
                if size < best_size:
                    best_size = size
                    best = name
        return best

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee_name = ""
            if isinstance(node.func, ast.Name):
                callee_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee_name = node.func.attr
            if not callee_name:
                continue
            caller = _caller_at(getattr(node, "lineno", 0))
            if not caller or caller == callee_name:
                continue
            edges.append(CallEdge(
                caller_repo=repo_name,
                caller_name=caller,
                caller_file=file_path,
                callee_name=callee_name,
            ))
    return edges


# ─────────────────────────────────────────────────────────────────────────────
# Complexity + log helpers (language-agnostic)
# ─────────────────────────────────────────────────────────────────────────────

_COMPLEXITY_RE = re.compile(
    r'\b(if|else\s+if|elif|else\s+when|for|foreach|while|do|case|catch|unless|guard)\b'
    r'|&&|\|\||\?(?![:\s>])',
    re.MULTILINE,
)

_LOG_CALL_RE = re.compile(
    r'(?:logger|log|logging|LOG|self\.log|console)\s*[\.\[]\s*'
    r'(?:info|debug|warning|warn|error|critical|fatal|exception|trace)\s*[\(\[]\s*'
    r'f?["\']([^"\'\\]{5,200})',
    re.MULTILINE | re.IGNORECASE,
)

_LOG_LEVEL_RE = re.compile(
    r'\.(info|debug|warning|warn|error|critical|fatal|exception|trace)\s*[\(\[]',
    re.IGNORECASE,
)

_TEST_FILE_PATTERNS = frozenset(["test_", "_test.", "spec_", "_spec.", ".test.", ".spec."])
_TEST_DIR_PATTERNS  = frozenset(["test/", "tests/", "__tests__/", "spec/", "specs/"])


def _compute_body_complexity(body: str) -> int:
    """Approximate cyclomatic complexity from source text (language-agnostic)."""
    return 1 + len(_COMPLEXITY_RE.findall(body))


def _python_node_complexity(node: ast.AST) -> int:
    """Count branch points in a Python AST node for cyclomatic complexity."""
    branches = sum(
        1 for n in ast.walk(node)
        if isinstance(n, (
            ast.If, ast.For, ast.While, ast.AsyncFor, ast.AsyncWith,
            ast.ExceptHandler, ast.comprehension, ast.BoolOp,
        ))
    )
    return 1 + branches


def _is_test_file(relative_path: str) -> bool:
    p = relative_path.lower().replace("\\", "/")
    return (
        any(pat in p for pat in _TEST_FILE_PATTERNS)
        or any(p.startswith(d) or f"/{d}" in p for d in _TEST_DIR_PATTERNS)
    )


def _source_name_from_test(test_name: str) -> str:
    """Heuristic: test_fetchUser → fetchUser, TestUserService → UserService."""
    name = re.sub(r'^(?:test_|Test|spec_|Spec|it_|It|should_)', '', test_name)
    name = re.sub(r'(?:_test|Test|_spec|Spec|_should\w*|_when\w*|_returns\w*|_raises\w*)$', '', name)
    return name if name and name != test_name else ""


def extract_log_patterns(
    source: str,
    file_path: str,
    repo_name: str,
    func_ranges: List[Tuple[int, int, str]],
) -> List[LogPattern]:
    """Extract log call sites from *source*, attributing each to its enclosing function."""
    patterns: List[LogPattern] = []
    for m in _LOG_CALL_RE.finditer(source):
        lineno = source[:m.start()].count("\n") + 1
        caller = ""
        best_sz = 10 ** 9
        for s, e, n in func_ranges:
            if s <= lineno <= e and (e - s) < best_sz:
                caller, best_sz = n, e - s
        lm = _LOG_LEVEL_RE.search(m.group(0))
        level = lm.group(1).lower() if lm else "unknown"
        patterns.append(LogPattern(
            repo_name=repo_name,
            file_path=file_path,
            function_name=caller,
            log_level=level,
            message_template=m.group(1)[:200],
            line_number=lineno,
        ))
    return patterns


# ─────────────────────────────────────────────────────────────────────────────
# TypeScript / TSX regex extraction (no full parser dependency)
# ─────────────────────────────────────────────────────────────────────────────

_TS_FUNCTION_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)(?:\s*:\s*[^\{]+)?\s*\{",
    re.MULTILINE,
)
_TS_ARROW_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(?[^)]*\)?\s*(?::\s*[^\=]+)?\s*=>\s*\{",
    re.MULTILINE,
)
_TS_IMPORT_RE = re.compile(
    r"""import\s+(?:\*\s+as\s+\w+|\{([^}]+)\}|(\w+))\s+from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_TS_CALL_RE = re.compile(r"\b(\w{2,})\s*\(", re.MULTILINE)


# ─────────────────────────────────────────────────────────────────────────────
# tree-sitter AST parsing (TypeScript / TSX / C#)
# Lazy init — falls back to regex if tree-sitter is not installed.
# ─────────────────────────────────────────────────────────────────────────────

_TS_PARSER:   Any = None
_TSX_PARSER:  Any = None
_CS_PARSER:   Any = None
_JAVA_PARSER: Any = None
_GO_PARSER:   Any = None
_RUST_PARSER: Any = None
_RUBY_PARSER: Any = None
_KT_PARSER:   Any = None

_TS_DEF_QUERY = """
  (function_declaration        name: (identifier)           @name) @def
  (function_expression         name: (identifier)           @name) @def
  (method_definition           name: (property_identifier)  @name) @def
  (class_declaration           name: (type_identifier)      @name) @def
  (interface_declaration       name: (type_identifier)      @name) @def
  (type_alias_declaration      name: (type_identifier)      @name) @def
  (lexical_declaration
    (variable_declarator
      name:  (identifier) @name
      value: [(arrow_function)(function_expression)])) @def
"""

_TS_CALL_QUERY = """
  (call_expression
    function: (identifier) @callee) @call
  (call_expression
    function: (member_expression
      property: (property_identifier) @callee)) @call
"""

_CS_DEF_QUERY = """
  (method_declaration       name: (identifier) @name) @def
  (constructor_declaration  name: (identifier) @name) @def
  (class_declaration        name: (identifier) @name) @def
  (interface_declaration    name: (identifier) @name) @def
  (struct_declaration       name: (identifier) @name) @def
  (record_declaration       name: (identifier) @name) @def
  (enum_declaration         name: (identifier) @name) @def
"""

_CS_CALL_QUERY = """
  (invocation_expression
    function: (identifier) @callee) @call
  (invocation_expression
    function: (member_access_expression
      name: (identifier) @callee)) @call
"""

_CS_USING_QUERY = """
  (using_directive
    [(qualified_name)(identifier)] @namespace)
"""

# ── Java ──────────────────────────────────────────────────────────────────────
_JAVA_DEF_QUERY = """
  (method_declaration       name: (identifier) @name) @def
  (constructor_declaration  name: (identifier) @name) @def
  (class_declaration        name: (identifier) @name) @def
  (interface_declaration    name: (identifier) @name) @def
  (enum_declaration         name: (identifier) @name) @def
  (record_declaration       name: (identifier) @name) @def
"""
_JAVA_CALL_QUERY = """
  (method_invocation name: (identifier) @callee) @call
"""
_JAVA_IMPORT_QUERY = """
  (import_declaration (scoped_identifier) @path)
  (import_declaration (identifier) @path)
"""

# ── Go ────────────────────────────────────────────────────────────────────────
_GO_DEF_QUERY = """
  (function_declaration name: (identifier) @name) @def
  (method_declaration   name: (field_identifier) @name) @def
  (type_declaration (type_spec name: (type_identifier) @name)) @def
"""
_GO_CALL_QUERY = """
  (call_expression function: (identifier) @callee) @call
  (call_expression function: (selector_expression field: (field_identifier) @callee)) @call
"""
_GO_IMPORT_QUERY = """
  (import_spec path: (interpreted_string_literal) @path)
"""

# ── Rust ──────────────────────────────────────────────────────────────────────
_RUST_DEF_QUERY = """
  (function_item name: (identifier) @name) @def
  (struct_item   name: (type_identifier) @name) @def
  (enum_item     name: (type_identifier) @name) @def
  (trait_item    name: (type_identifier) @name) @def
  (impl_item     type: (type_identifier) @name) @def
"""
_RUST_CALL_QUERY = """
  (call_expression function: (identifier) @callee) @call
  (call_expression function: (field_expression field: (field_identifier) @callee)) @call
"""
_RUST_IMPORT_QUERY = """
  (use_declaration [(scoped_identifier)(identifier)] @path)
"""

# ── Ruby ──────────────────────────────────────────────────────────────────────
_RUBY_DEF_QUERY = """
  (method           name: (identifier) @name) @def
  (singleton_method name: (identifier) @name) @def
  (class            name: (constant) @name) @def
  (module           name: (constant) @name) @def
"""
_RUBY_CALL_QUERY = """
  (call method: (identifier) @callee) @call
"""
_RUBY_IMPORT_QUERY = """
  (call method: (identifier) @_m
        arguments: (argument_list (string (string_content) @path)))
"""

# ── Kotlin ────────────────────────────────────────────────────────────────────
_KT_DEF_QUERY = """
  (function_declaration (simple_identifier) @name) @def
  (class_declaration    (type_identifier) @name) @def
  (object_declaration   (type_identifier) @name) @def
"""
_KT_CALL_QUERY = """
  (call_expression (simple_identifier) @callee) @call
  (call_expression (navigation_expression (simple_identifier) @callee)) @call
"""
_KT_IMPORT_QUERY = """
  (import_header [(identifier)(scoped_identifier)] @path)
"""

# ── Query router (parser_lang → (def_query, call_query)) ─────────────────────
_LANG_QUERIES: Dict[str, Tuple[str, str]] = {
    "typescript": (_TS_DEF_QUERY,   _TS_CALL_QUERY),
    "tsx":        (_TS_DEF_QUERY,   _TS_CALL_QUERY),
    "csharp":     (_CS_DEF_QUERY,   _CS_CALL_QUERY),
    "java":       (_JAVA_DEF_QUERY, _JAVA_CALL_QUERY),
    "go":         (_GO_DEF_QUERY,   _GO_CALL_QUERY),
    "rust":       (_RUST_DEF_QUERY, _RUST_CALL_QUERY),
    "ruby":       (_RUBY_DEF_QUERY, _RUBY_CALL_QUERY),
    "kotlin":     (_KT_DEF_QUERY,   _KT_CALL_QUERY),
}

_CLASS_TYPE_KEYWORDS = frozenset(
    ["class", "interface", "struct", "record", "enum", "trait", "module", "object"]
)


def _get_ts_parser(lang: str) -> Any:
    """Return a cached tree-sitter Parser for *lang*.

    Returns None (never raises) if the grammar package is not installed.
    """
    global _TS_PARSER, _TSX_PARSER, _CS_PARSER
    global _JAVA_PARSER, _GO_PARSER, _RUST_PARSER, _RUBY_PARSER, _KT_PARSER
    try:
        from tree_sitter import Parser, Language  # type: ignore[import]
        if lang == "typescript" and _TS_PARSER is None:
            import tree_sitter_typescript as _tst  # type: ignore[import]
            _TS_PARSER = Parser(Language(_tst.language_typescript()))
        if lang == "tsx" and _TSX_PARSER is None:
            import tree_sitter_typescript as _tst  # type: ignore[import]
            _TSX_PARSER = Parser(Language(_tst.language_tsx()))
        if lang == "csharp" and _CS_PARSER is None:
            import tree_sitter_c_sharp as _tsc  # type: ignore[import]
            _CS_PARSER = Parser(Language(_tsc.language_c_sharp()))
        if lang == "java" and _JAVA_PARSER is None:
            import tree_sitter_java as _tsj  # type: ignore[import]
            _JAVA_PARSER = Parser(Language(_tsj.language_java()))
        if lang == "go" and _GO_PARSER is None:
            import tree_sitter_go as _tsg  # type: ignore[import]
            _GO_PARSER = Parser(Language(_tsg.language_go()))
        if lang == "rust" and _RUST_PARSER is None:
            import tree_sitter_rust as _tsr  # type: ignore[import]
            _RUST_PARSER = Parser(Language(_tsr.language_rust()))
        if lang == "ruby" and _RUBY_PARSER is None:
            import tree_sitter_ruby as _tsrb  # type: ignore[import]
            _RUBY_PARSER = Parser(Language(_tsrb.language_ruby()))
        if lang == "kotlin" and _KT_PARSER is None:
            import tree_sitter_kotlin as _tsk  # type: ignore[import]
            _KT_PARSER = Parser(Language(_tsk.language_kotlin()))
        return {
            "typescript": _TS_PARSER,  "tsx":    _TSX_PARSER,
            "csharp":     _CS_PARSER,  "java":   _JAVA_PARSER,
            "go":         _GO_PARSER,  "rust":   _RUST_PARSER,
            "ruby":       _RUBY_PARSER,"kotlin": _KT_PARSER,
        }.get(lang)
    except Exception:
        return None


def _extract_chunks_via_ts(
    source: str,
    file_path: str,
    repo_name: str,
    parser_lang: str,
    chunk_lang: str,
) -> Tuple[List[CodeChunk], List[CallEdge]]:
    """Parse *source* with tree-sitter; return (chunks, call_edges).

    Uses exact AST line ranges — no 80-line cap, no keyword false-positives,
    call edges produced for all languages.
    Returns ([], []) on any failure so callers fall back to regex silently.
    """
    parser = _get_ts_parser(parser_lang)
    if parser is None:
        return [], []
    try:
        tree  = parser.parse(source.encode())
        lines = source.splitlines()
        lang  = parser.language

        dq_str, cq_str = _LANG_QUERIES.get(parser_lang, (_TS_DEF_QUERY, _TS_CALL_QUERY))
        def_q  = lang.query(dq_str)
        call_q = lang.query(cq_str)

        chunks: List[CodeChunk] = []
        seen:   set             = set()

        captures = def_q.captures(tree.root_node)
        # tree-sitter >=0.22 returns dict[str, list[Node]]
        name_nodes = captures.get("name", [])
        if not isinstance(name_nodes, list):
            name_nodes = [name_nodes]

        for name_node in name_nodes:
            name     = name_node.text.decode(errors="replace")
            def_node = name_node.parent
            ls       = def_node.start_point[0] + 1
            le       = def_node.end_point[0]   + 1
            key      = (name, ls)
            if key in seen:
                continue
            seen.add(key)
            body  = "\n".join(lines[ls - 1 : le])
            ctype = "class" if any(
                t in def_node.type
                for t in ("class", "interface", "struct", "record", "enum")
            ) else "function"
            chunks.append(CodeChunk(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                chunk_type=ctype,
                body=body,
                language=chunk_lang,
                line_start=ls,
                line_end=le,
                entity_id=compute_entity_id(repo_name, f"{ctype}:{name}:{ls}"),
            ))

        func_ranges = [(c.line_start, c.line_end, c.name)
                       for c in chunks if c.chunk_type == "function"]

        def _caller_at(lineno: int) -> str:
            best, best_sz = "", 10 ** 9
            for s, e, n in func_ranges:
                if s <= lineno <= e and (e - s) < best_sz:
                    best, best_sz = n, e - s
            return best

        calls:      List[CallEdge] = []
        seen_calls: set            = set()
        callee_nodes = call_q.captures(tree.root_node).get("callee", [])
        if not isinstance(callee_nodes, list):
            callee_nodes = [callee_nodes]

        for callee_node in callee_nodes:
            callee = callee_node.text.decode(errors="replace")
            lineno = callee_node.start_point[0] + 1
            caller = _caller_at(lineno)
            if not caller or caller == callee:
                continue
            ck = (caller, callee)
            if ck in seen_calls:
                continue
            seen_calls.add(ck)
            calls.append(CallEdge(
                caller_repo=repo_name,
                caller_name=caller,
                caller_file=file_path,
                callee_name=callee,
            ))

        return chunks, calls

    except Exception as exc:
        logger.debug(
            "tree-sitter extraction failed for %s (%s): %s", file_path, parser_lang, exc,
        )
        return [], []


def _extract_imports_via_ts(
    source: str,
    file_path: str,
    repo_name: str,
    parser_lang: str,
    import_query_str: str,
) -> List[ImportEdge]:
    """Generic tree-sitter import extractor; returns [] on any failure."""
    parser = _get_ts_parser(parser_lang)
    if parser is None:
        return []
    try:
        tree = parser.parse(source.encode())
        lang = parser.language
        q    = lang.query(import_query_str)
        path_nodes = q.captures(tree.root_node).get("path", [])
        if not isinstance(path_nodes, list):
            path_nodes = [path_nodes]
        edges: List[ImportEdge] = []
        for pn in path_nodes:
            raw = pn.text.decode(errors="replace").strip().strip('"\'')
            if not raw:
                continue
            edges.append(ImportEdge(
                repo_name=repo_name,
                file_path=file_path,
                from_module=raw,
                import_name=raw.split(".")[-1].split("/")[-1],
                is_relative=raw.startswith("."),
            ))
        return edges
    except Exception as exc:
        logger.debug("tree-sitter import extraction failed for %s (%s): %s", file_path, parser_lang, exc)
        return []


def _extract_cs_imports_via_ts(
    source: str, file_path: str, repo_name: str,
) -> List[ImportEdge]:
    """Extract C# `using` directives via tree-sitter; falls back to regex."""
    parser = _get_ts_parser("csharp")
    if parser is None:
        return extract_csharp_imports(source, file_path, repo_name)
    try:
        tree = parser.parse(source.encode())
        lang = parser.language
        q    = lang.query(_CS_USING_QUERY)
        ns_nodes = q.captures(tree.root_node).get("namespace", [])
        if not isinstance(ns_nodes, list):
            ns_nodes = [ns_nodes]
        edges: List[ImportEdge] = []
        for ns_node in ns_nodes:
            namespace = ns_node.text.decode(errors="replace").strip()
            if namespace:
                edges.append(ImportEdge(
                    repo_name=repo_name,
                    file_path=file_path,
                    from_module=namespace,
                    import_name=namespace.split(".")[-1],
                    is_relative=False,
                ))
        return edges
    except Exception as exc:
        logger.debug("tree-sitter import extraction failed for %s: %s", file_path, exc)
        return extract_csharp_imports(source, file_path, repo_name)


# ─────────────────────────────────────────────────────────────────────────────
# C# / .NET regex extraction
# ─────────────────────────────────────────────────────────────────────────────

_CS_METHOD_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static|async|virtual|override|"
    r"abstract|sealed|partial|extern|new)\s+)*"
    r"(?:[\w<>\[\]?,\s]+?)\s+(\w+)\s*\([^)]*\)\s*(?:where\s+[^{;]+)?\s*\{",
    re.MULTILINE,
)
_CS_CLASS_RE = re.compile(
    r"(?:(?:public|private|protected|internal|static|abstract|sealed|partial)\s+)*"
    r"(?:class|interface|struct|record|enum)\s+(\w+)(?:<[^>]+>)?(?:\s*:\s*[^{\n]+)?\s*\{",
    re.MULTILINE,
)
_CS_USING_RE = re.compile(
    r"^using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;",
    re.MULTILINE,
)


def extract_csharp_chunks(source: str, file_path: str, repo_name: str) -> List[CodeChunk]:
    """Extract methods and classes from C# source via regex."""
    lines  = source.splitlines()
    chunks: List[CodeChunk] = []
    seen:   set = set()

    for pattern, ctype in [(_CS_METHOD_RE, "function"), (_CS_CLASS_RE, "class")]:
        for m in pattern.finditer(source):
            name = m.group(1)
            if name in {"if", "for", "foreach", "while", "switch", "catch", "using", "lock"}:
                continue
            key = (name, ctype)
            if key in seen:
                continue
            seen.add(key)
            line_start = source[:m.start()].count("\n") + 1
            line_end   = min(line_start + 79, len(lines))
            body       = "\n".join(lines[line_start - 1 : line_end])
            chunks.append(CodeChunk(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                chunk_type=ctype,
                body=body,
                language="csharp",
                line_start=line_start,
                line_end=line_end,
                entity_id=compute_entity_id(repo_name, f"{ctype}:{name}"),
            ))

    return chunks


def extract_csharp_imports(
    source: str, file_path: str, repo_name: str,
) -> List[ImportEdge]:
    """Extract `using` directives from C# source."""
    edges: List[ImportEdge] = []
    for m in _CS_USING_RE.finditer(source):
        namespace = m.group(1)
        if namespace:
            edges.append(ImportEdge(
                repo_name=repo_name,
                file_path=file_path,
                from_module=namespace,
                import_name=namespace.split(".")[-1],
                is_relative=False,
            ))
    return edges


# ─────────────────────────────────────────────────────────────────────────────
# React / JSX regex extraction (extends TypeScript patterns with components)
# ─────────────────────────────────────────────────────────────────────────────

_JSX_COMPONENT_RE = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:function|const)\s+([A-Z]\w*)\s*[=(]",
    re.MULTILINE,
)


def extract_typescript_chunks(source: str, file_path: str, repo_name: str) -> List[CodeChunk]:
    """Extract named functions and arrow functions from TypeScript/TSX source."""
    lines  = source.splitlines()
    chunks: List[CodeChunk] = []

    for pattern, ctype in [(_TS_FUNCTION_RE, "function"), (_TS_ARROW_RE, "function")]:
        for m in pattern.finditer(source):
            name       = m.group(1)
            line_start = source[:m.start()].count("\n") + 1
            # Grab a reasonable body window (up to 80 lines)
            line_end = min(line_start + 79, len(lines))
            body = "\n".join(lines[line_start - 1 : line_end])
            norm_sig = f"{name}()"  # TS regex can't reconstruct full sig
            chunks.append(CodeChunk(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                chunk_type=ctype,
                body=body,
                language="typescript",
                line_start=line_start,
                line_end=line_end,
                entity_id=compute_entity_id(repo_name, norm_sig),
            ))

    return chunks


def extract_react_chunks(source: str, file_path: str, repo_name: str) -> List[CodeChunk]:
    """Extract functions and React components from JSX/TSX source.

    Runs the TypeScript extractor first, then adds any PascalCase components
    matched by _JSX_COMPONENT_RE that weren't already captured (deduped by name).
    """
    chunks = extract_typescript_chunks(source, file_path, repo_name)
    seen   = {c.name for c in chunks}
    lines  = source.splitlines()

    for m in _JSX_COMPONENT_RE.finditer(source):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        line_start = source[:m.start()].count("\n") + 1
        line_end   = min(line_start + 79, len(lines))
        body       = "\n".join(lines[line_start - 1 : line_end])
        chunks.append(CodeChunk(
            repo_name=repo_name,
            file_path=file_path,
            name=name,
            chunk_type="function",
            body=body,
            language="react",
            line_start=line_start,
            line_end=line_end,
            entity_id=compute_entity_id(repo_name, f"{name}()"),
        ))
        # Tag previously-captured TS chunks from this file as react
        for c in chunks:
            if c.language == "typescript" and c.file_path == file_path:
                c.language = "react"

    return chunks


def extract_typescript_imports(
    source: str, file_path: str, repo_name: str,
) -> List[ImportEdge]:
    """Extract ES import statements from TypeScript source via regex."""
    edges: List[ImportEdge] = []
    for m in _TS_IMPORT_RE.finditer(source):
        named_group = m.group(1)  # "{A, B, C}"
        default_name = m.group(2)  # "React"
        from_module = m.group(3)

        is_relative = from_module.startswith(".")
        if named_group:
            for name in re.split(r"\s*,\s*", named_group):
                name = name.strip().split(" as ")[0].strip()
                if name:
                    edges.append(ImportEdge(
                        repo_name=repo_name,
                        file_path=file_path,
                        from_module=from_module,
                        import_name=name,
                        is_relative=is_relative,
                    ))
        elif default_name:
            edges.append(ImportEdge(
                repo_name=repo_name,
                file_path=file_path,
                from_module=from_module,
                import_name=default_name,
                is_relative=is_relative,
            ))
    return edges


# ─────────────────────────────────────────────────────────────────────────────
# Embedder — AWS Bedrock Titan
# ─────────────────────────────────────────────────────────────────────────────

async def _embed_text(text_to_embed: str, region: str = "us-east-1") -> Optional[List[float]]:
    """Call Bedrock Titan and return a 1536-dim embedding vector."""
    import json as _json
    import boto3

    def _call() -> Optional[List[float]]:
        client  = boto3.client("bedrock-runtime", region_name=region)
        payload = _json.dumps({"inputText": text_to_embed[:8191]})
        try:
            resp = client.invoke_model(
                modelId=_EMBED_MODEL,
                body=payload,
                contentType="application/json",
                accept="application/json",
            )
            result = _json.loads(resp["body"].read())
            return result.get("embedding")
        except Exception as exc:
            logger.warning("Bedrock embedding failed: %s", exc)
            return None

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call)


# ─────────────────────────────────────────────────────────────────────────────
# Indexer
# ─────────────────────────────────────────────────────────────────────────────

class CodeIndexer:
    """Walk a local repository, extract code chunks, embed, and upsert to DB.

    Phase-split design
    ------------------
    ``index_repo()`` performs a fast synchronous phase that returns promptly,
    then fires-and-forgets an async enrichment task for heavier graph analysis.
    Callers receive a complete chunk list as soon as Phase 1 is done.
    """

    def __init__(
        self,
        embed: bool = True,
        bedrock_region: str = "us-east-1",
        jail_path: Optional[str] = None,
    ):
        self.embed = embed
        self.bedrock_region = bedrock_region
        self.jail_path = jail_path  # If set, all file access is checked against this root

    # ── Public API ────────────────────────────────────────────────────────────

    async def index_repo(
        self,
        repo_name: str,
        local_path: str,
        language: str = "python",
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
        incremental: bool = False,
    ) -> List[CodeChunk]:
        """Index all source files in *local_path* and upsert to DB.

        Phase 1 (fast, parallel):
            - Walk files (optionally only git-changed files for incremental),
              extract chunks, embed, upsert to code_chunks
            - Upsert raw call edges to code_calls
            - Upsert import edges to code_imports
            - Index log patterns to code_log_patterns
            - Set enrichment_phase = 'raw' on all touched chunks

        Phase 2 (async, fire-and-forget via create_task):
            - Compute semantic edge weights
            - Write pre-reindex call-graph snapshot
            - Resolve cross-file call edges
            - Link test files to source files
            - Parse .github/CODEOWNERS into code_owners
            - Infer deployments from git tags as fallback

        Args:
            repo_name: Unique name for this repository.
            local_path: Absolute path to the local checkout.
            language: 'python' | 'typescript' | 'mixed' | 'csharp' | 'dotnet' |
                      'react' | 'java' | 'kotlin' | 'go' | 'rust' | 'ruby'
            progress_callback: Optional async callable(files_done, total_files).
            incremental: If True, only re-index files changed since the last
                indexed commit (uses git diff HEAD~1).

        Returns:
            List of CodeChunk objects indexed during Phase 1.
        """
        root = Path(local_path).resolve()
        if not root.exists():
            raise ValueError(f"Repository path does not exist: {root}")

        extensions: list[str] = []
        if language in {"python", "mixed"}:
            extensions += [".py"]
        if language in {"typescript", "mixed"}:
            extensions += [".ts", ".tsx"]
        if language in {"csharp", "dotnet"}:
            extensions += [".cs"]
        if language == "react":
            extensions += [".jsx", ".tsx"]
        if language == "java":
            extensions += [".java"]
        if language == "kotlin":
            extensions += [".kt", ".kts"]
        if language == "go":
            extensions += [".go"]
        if language == "rust":
            extensions += [".rs"]
        if language == "ruby":
            extensions += [".rb"]

        # ── Collect all files ────────────────────────────────────────────────
        changed_set: Optional[set[str]] = None
        if incremental:
            changed_set = await self._get_changed_files(local_path)

        all_files: List[Tuple[Path, str]] = []
        for ext in extensions:
            for fp in sorted(root.rglob(f"*{ext}")):
                if self.jail_path:
                    try:
                        check_path(fp, self.jail_path)
                    except PathJailError:
                        logger.warning("Skipping path outside jail: %s", fp)
                        continue
                if fp.stat().st_size > _MAX_FILE_BYTES:
                    logger.debug("Skipping oversized file: %s", fp)
                    continue
                if changed_set is not None:
                    rel = str(fp.relative_to(root)).replace("\\", "/")
                    if rel not in changed_set:
                        continue
                all_files.append((fp, fp.suffix))

        total_files = len(all_files)
        all_chunks:   List[CodeChunk]   = []
        all_calls:    List[CallEdge]    = []
        all_imports:  List[ImportEdge]  = []
        all_logs:     List[LogPattern]  = []
        files_done_counter              = 0
        sem = asyncio.Semaphore(8)

        async def _process_file(file_path: Path, ext: str) -> None:
            nonlocal files_done_counter
            async with sem:
                relative = str(file_path.relative_to(root)).replace("\\", "/")
                try:
                    source = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: file_path.read_text(encoding="utf-8", errors="ignore")
                    )
                except OSError:
                    return

                # Extract chunks + calls + imports per language
                if ext == ".py":
                    chunks  = extract_python_chunks(source, relative, repo_name)
                    calls   = extract_python_calls(source, relative, repo_name)
                    imports = extract_python_imports(source, relative, repo_name)
                elif ext == ".cs":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "csharp", "csharp")
                    if not chunks:
                        chunks = extract_csharp_chunks(source, relative, repo_name)
                        calls  = []
                    imports = _extract_cs_imports_via_ts(source, relative, repo_name)
                elif ext == ".jsx":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "tsx", "react")
                    if not chunks:
                        chunks = extract_react_chunks(source, relative, repo_name)
                        calls  = []
                    imports = extract_typescript_imports(source, relative, repo_name)
                elif ext == ".tsx":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "tsx", "typescript")
                    if not chunks:
                        chunks = extract_typescript_chunks(source, relative, repo_name)
                        calls  = []
                    imports = extract_typescript_imports(source, relative, repo_name)
                elif ext in {".ts"}:
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "typescript", "typescript")
                    if not chunks:
                        chunks = extract_typescript_chunks(source, relative, repo_name)
                        calls  = []
                    imports = extract_typescript_imports(source, relative, repo_name)
                elif ext == ".java":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "java", "java")
                    imports = _extract_imports_via_ts(source, relative, repo_name, "java", _JAVA_IMPORT_QUERY)
                elif ext in {".kt", ".kts"}:
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "kotlin", "kotlin")
                    imports = _extract_imports_via_ts(source, relative, repo_name, "kotlin", _KT_IMPORT_QUERY)
                elif ext == ".go":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "go", "go")
                    imports = _extract_imports_via_ts(source, relative, repo_name, "go", _GO_IMPORT_QUERY)
                elif ext == ".rs":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "rust", "rust")
                    imports = _extract_imports_via_ts(source, relative, repo_name, "rust", _RUST_IMPORT_QUERY)
                elif ext == ".rb":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "ruby", "ruby")
                    imports = _extract_imports_via_ts(source, relative, repo_name, "ruby", _RUBY_IMPORT_QUERY)
                else:
                    chunks, calls, imports = [], [], []

                # Log patterns — build func_ranges from chunks
                func_ranges = [(c.line_start, c.line_end, c.name) for c in chunks]
                logs = extract_log_patterns(source, relative, repo_name, func_ranges)

                all_chunks.extend(chunks)
                all_calls.extend(calls)
                all_imports.extend(imports)
                all_logs.extend(logs)

                files_done_counter += 1
                if progress_callback:
                    try:
                        await progress_callback(files_done_counter, total_files)
                    except Exception:
                        pass

        await asyncio.gather(*[_process_file(fp, ext) for fp, ext in all_files])

        logger.info(
            "CodeIndexer: extracted %d chunks, %d calls, %d imports, %d log patterns from %s (%s)",
            len(all_chunks), len(all_calls), len(all_imports), len(all_logs), repo_name, language,
        )

        # ── Phase 1: embed + upsert synchronously ────────────────────────────
        if self.embed:
            await self._embed_chunks(all_chunks)

        await self._upsert_chunks(all_chunks)
        await self._upsert_raw_calls(repo_name, all_calls)
        await self._upsert_imports(all_imports)
        await self._index_log_patterns(all_logs)

        # ── Fire Phase 2 in background ───────────────────────────────────────
        asyncio.create_task(self._enrich_graph(repo_name, local_path))

        return all_chunks

    # ── Phase 1 helpers ───────────────────────────────────────────────────────

    async def _embed_chunks(self, chunks: List[CodeChunk]) -> None:
        for chunk in chunks:
            text_to_embed = f"{chunk.signature}\n{chunk.docstring}\n{chunk.body}"[:4096]
            chunk.embedding = await _embed_text(text_to_embed, self.bedrock_region)

    async def _upsert_chunks(self, chunks: List[CodeChunk]) -> None:
        if not chunks:
            return

        async with AsyncSessionLocal() as session:
            # Ensure all optional columns exist (idempotent)
            for ddl in [
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS enrichment_phase VARCHAR(10) DEFAULT 'raw'",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS entity_id VARCHAR(16)",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS predecessor_entity_id VARCHAR(16)",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS complexity INTEGER DEFAULT 1",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS search_vector tsvector",
            ]:
                await session.execute(text(ddl))

            for chunk in chunks:
                emb = (
                    f"[{','.join(str(v) for v in chunk.embedding)}]"
                    if chunk.embedding else None
                )
                await session.execute(
                    text("""
                        INSERT INTO code_chunks
                            (repo_name, file_path, name, chunk_type, signature, body,
                             docstring, language, line_start, line_end, embedding,
                             entity_id, enrichment_phase, complexity, search_vector,
                             indexed_at)
                        VALUES
                            (:repo_name, :file_path, :name, :chunk_type, :signature, :body,
                             :docstring, :language, :line_start, :line_end,
                             :embedding::vector, :entity_id, 'raw', :complexity,
                             to_tsvector('english', coalesce(:sig,'') || ' ' || coalesce(:name2,'') || ' ' || coalesce(:body2,'')),
                             NOW())
                        ON CONFLICT (repo_name, file_path, name, chunk_type)
                        DO UPDATE SET
                            signature        = EXCLUDED.signature,
                            body             = EXCLUDED.body,
                            docstring        = EXCLUDED.docstring,
                            line_start       = EXCLUDED.line_start,
                            line_end         = EXCLUDED.line_end,
                            embedding        = EXCLUDED.embedding,
                            entity_id        = EXCLUDED.entity_id,
                            enrichment_phase = 'raw',
                            complexity       = EXCLUDED.complexity,
                            search_vector    = EXCLUDED.search_vector,
                            indexed_at       = NOW()
                    """),
                    {
                        "repo_name":  chunk.repo_name,
                        "file_path":  chunk.file_path,
                        "name":       chunk.name,
                        "chunk_type": chunk.chunk_type,
                        "signature":  chunk.signature,
                        "body":       chunk.body[:50_000],
                        "docstring":  chunk.docstring,
                        "language":   chunk.language,
                        "line_start": chunk.line_start,
                        "line_end":   chunk.line_end,
                        "embedding":  emb,
                        "entity_id":  chunk.entity_id or None,
                        "complexity": chunk.complexity,
                        # separate short params for tsvector to avoid 50k body
                        "sig":        chunk.signature[:500],
                        "name2":      chunk.name,
                        "body2":      chunk.body[:2000],
                    },
                )
            await session.commit()

        logger.info("CodeIndexer: upserted %d chunks", len(chunks))

    async def _upsert_raw_calls(
        self,
        repo_name: str,
        calls: List[CallEdge],
    ) -> None:
        """Insert caller→callee edges into code_calls (raw, no edge weights yet)."""
        if not calls:
            return

        # Deduplicate: same (caller_name, callee_name, caller_file)
        seen: set[Tuple[str, str, str]] = set()
        deduped: List[CallEdge] = []
        for c in calls:
            key = (c.caller_name, c.callee_name, c.caller_file)
            if key not in seen:
                seen.add(key)
                deduped.append(c)

        async with AsyncSessionLocal() as session:
            # Ensure schema is ready
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_calls (
                    id             SERIAL PRIMARY KEY,
                    caller_repo    VARCHAR(255),
                    caller_name    VARCHAR(500),
                    caller_file    VARCHAR(1000),
                    callee_name    VARCHAR(500),
                    callee_file    VARCHAR(1000),
                    call_frequency INTEGER DEFAULT 1,
                    edge_weight    FLOAT   DEFAULT 1.0,
                    indexed_at     TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await session.execute(text(
                "ALTER TABLE code_calls ADD COLUMN IF NOT EXISTS "
                "caller_entity_id VARCHAR(16)"
            ))
            await session.execute(text(
                "ALTER TABLE code_calls ADD COLUMN IF NOT EXISTS "
                "callee_entity_id VARCHAR(16)"
            ))
            await session.execute(text(
                "ALTER TABLE code_calls ADD COLUMN IF NOT EXISTS "
                "indexed_at TIMESTAMPTZ DEFAULT NOW()"
            ))

            for call in deduped:
                await session.execute(text("""
                    INSERT INTO code_calls
                        (caller_repo, caller_name, caller_file,
                         callee_name, callee_file,
                         call_frequency, edge_weight, indexed_at)
                    VALUES
                        (:repo, :caller, :caller_file,
                         :callee, :callee_file,
                         1, 1.0, NOW())
                    ON CONFLICT DO NOTHING
                """), {
                    "repo":        call.caller_repo,
                    "caller":      call.caller_name,
                    "caller_file": call.caller_file,
                    "callee":      call.callee_name,
                    "callee_file": call.callee_file,
                })
            await session.commit()

        logger.info(
            "CodeIndexer: upserted %d raw call edges for %s", len(deduped), repo_name,
        )

    async def _upsert_imports(self, imports: List[ImportEdge]) -> None:
        """Insert import edges into code_imports table."""
        if not imports:
            return

        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_imports (
                    id           SERIAL PRIMARY KEY,
                    repo_name    VARCHAR(255) NOT NULL,
                    file_path    VARCHAR(1000) NOT NULL,
                    from_module  VARCHAR(500) NOT NULL,
                    import_name  VARCHAR(255) NOT NULL,
                    is_relative  BOOLEAN DEFAULT FALSE,
                    indexed_at   TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (repo_name, file_path, from_module, import_name)
                )
            """))

            for imp in imports:
                await session.execute(text("""
                    INSERT INTO code_imports
                        (repo_name, file_path, from_module, import_name,
                         is_relative, indexed_at)
                    VALUES
                        (:rn, :fp, :from, :name, :rel, NOW())
                    ON CONFLICT (repo_name, file_path, from_module, import_name)
                    DO UPDATE SET indexed_at = NOW()
                """), {
                    "rn":   imp.repo_name,
                    "fp":   imp.file_path,
                    "from": imp.from_module,
                    "name": imp.import_name,
                    "rel":  imp.is_relative,
                })
            await session.commit()

        logger.info(
            "CodeIndexer: upserted %d import edges", len(imports),
        )

    # ── Phase 2: async enrichment ─────────────────────────────────────────────

    async def _enrich_graph(self, repo_name: str, local_path: str) -> None:
        """Background enrichment: edge weights, snapshot, cross-file calls, test links, CODEOWNERS, deployments."""
        logger.info("CodeIndexer: starting graph enrichment for %s", repo_name)
        try:
            async with AsyncSessionLocal() as session:
                await self._snapshot_graph(repo_name, session, trigger="pre_reindex")
                await session.commit()

            await self._compute_edge_weights(repo_name)
            await self._resolve_cross_file_calls(repo_name)
            await self._link_test_files(repo_name)

            codeowners_path = Path(local_path) / ".github" / "CODEOWNERS"
            if not codeowners_path.exists():
                codeowners_path = Path(local_path) / "CODEOWNERS"
            if codeowners_path.exists():
                await self._index_codeowners(repo_name, codeowners_path)

            await self._infer_deployments_from_tags(repo_name, local_path)
            await self._set_enrichment_phase(repo_name, "enriched")

            logger.info("CodeIndexer: enrichment complete for %s", repo_name)
        except Exception as exc:
            logger.error(
                "CodeIndexer: enrichment failed for %s — %s",
                repo_name, exc, exc_info=True,
            )

    async def _compute_edge_weights(self, repo_name: str) -> None:
        """Compute cosine-similarity edge weights for code_calls rows.

        For repos with more edges than _EDGE_WEIGHT_THRESHOLD we only compute
        weights for the top-N most-accessed function edges to bound cost.
        """
        async with AsyncSessionLocal() as session:
            edge_count_result = await session.execute(
                text("SELECT COUNT(*) FROM code_calls WHERE caller_repo = :rn"),
                {"rn": repo_name},
            )
            edge_count = edge_count_result.scalar() or 0

        if edge_count == 0:
            return

        if edge_count <= _EDGE_WEIGHT_THRESHOLD:
            await self._compute_edge_weights_full(repo_name)
        else:
            await self._compute_edge_weights_topn(repo_name, n=_EDGE_WEIGHT_THRESHOLD)

    async def _compute_edge_weights_full(self, repo_name: str) -> None:
        """Compute semantic edge weights for ALL call edges in a repo."""
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                UPDATE code_calls cc
                SET edge_weight = GREATEST(0.0,
                    1 - (c1.embedding <=> c2.embedding)
                )
                FROM code_chunks c1, code_chunks c2
                WHERE c1.name        = cc.caller_name
                  AND c1.repo_name   = cc.caller_repo
                  AND c2.name        = cc.callee_name
                  AND c2.repo_name   = cc.caller_repo
                  AND cc.caller_repo = :rn
                  AND c1.embedding   IS NOT NULL
                  AND c2.embedding   IS NOT NULL
            """), {"rn": repo_name})
            await session.commit()

        logger.info(
            "CodeIndexer: computed full edge weights for %s", repo_name,
        )

    async def _compute_edge_weights_topn(self, repo_name: str, n: int) -> None:
        """Compute semantic edge weights only for top-N most-accessed function edges."""
        async with AsyncSessionLocal() as session:
            # Restrict to edges involving the most-accessed functions
            await session.execute(text("""
                UPDATE code_calls cc
                SET edge_weight = GREATEST(0.0,
                    1 - (c1.embedding <=> c2.embedding)
                )
                FROM code_chunks c1, code_chunks c2,
                     (
                         SELECT DISTINCT function_name
                           FROM investigation_memory
                          WHERE repo_name = :rn
                            AND status    = 'active'
                          ORDER BY access_count DESC
                          LIMIT :n
                     ) hot
                WHERE (c1.name = hot.function_name OR c2.name = hot.function_name)
                  AND c1.name        = cc.caller_name
                  AND c1.repo_name   = cc.caller_repo
                  AND c2.name        = cc.callee_name
                  AND c2.repo_name   = cc.caller_repo
                  AND cc.caller_repo = :rn
                  AND c1.embedding   IS NOT NULL
                  AND c2.embedding   IS NOT NULL
            """), {"rn": repo_name, "n": n})
            await session.commit()

        logger.info(
            "CodeIndexer: computed top-%d edge weights for %s", n, repo_name,
        )

    async def _snapshot_graph(
        self,
        repo_name: str,
        session: Any,
        trigger: str = "pre_reindex",
        commit_sha: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> None:
        """Serialize current call graph to code_graph_snapshots (delta or full).

        Uses delta snapshots to avoid storage explosion:
        - First snapshot per repo: full (is_full=TRUE)
        - Subsequent snapshots: delta vs. previous (added/removed edges)
        - Force full every 10 deltas to keep chain reconstruction bounded
        """
        # Ensure table exists
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS code_graph_snapshots (
                id             SERIAL PRIMARY KEY,
                repo_name      VARCHAR(255) NOT NULL,
                snapshot_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                trigger        VARCHAR(50),
                commit_sha     VARCHAR(40),
                tag            VARCHAR(255),
                is_full        BOOLEAN      DEFAULT FALSE,
                parent_id      INTEGER,
                call_graph     JSONB,
                chunk_summary  JSONB,
                added_edges    JSONB DEFAULT '[]',
                removed_edges  JSONB DEFAULT '[]',
                modified_edges JSONB DEFAULT '[]'
            )
        """))

        # Fetch current call graph
        rows = await session.execute(text("""
            SELECT caller_name, callee_name, caller_file, callee_file, call_frequency
              FROM code_calls
             WHERE caller_repo = :rn
        """), {"rn": repo_name})
        current_edges = [dict(r._mapping) for r in rows]

        # Find parent snapshot
        parent_result = await session.execute(text("""
            SELECT id, call_graph, is_full
              FROM code_graph_snapshots
             WHERE repo_name = :rn
             ORDER BY snapshot_at DESC
             LIMIT 1
        """), {"rn": repo_name})
        parent_row = parent_result.one_or_none()

        # Count deltas since last full to decide if this one should be full
        delta_count = 0
        if parent_row and not parent_row.is_full:
            dc_result = await session.execute(text("""
                SELECT COUNT(*) FROM code_graph_snapshots
                 WHERE repo_name = :rn
                   AND is_full   = FALSE
                   AND id >= (
                       SELECT MAX(id) FROM code_graph_snapshots
                        WHERE repo_name = :rn AND is_full = TRUE
                   )
            """), {"rn": repo_name})
            delta_count = dc_result.scalar() or 0

        is_full = (parent_row is None) or (delta_count % 10 == 9)

        if is_full:
            # Full snapshot
            chunk_rows = await session.execute(text("""
                SELECT name, file_path, chunk_type
                  FROM code_chunks
                 WHERE repo_name = :rn
            """), {"rn": repo_name})
            chunk_summary = [dict(r._mapping) for r in chunk_rows]
            await session.execute(text("""
                INSERT INTO code_graph_snapshots
                    (repo_name, trigger, commit_sha, tag, is_full,
                     call_graph, chunk_summary)
                VALUES (:rn, :trigger, :sha, :tag, TRUE,
                        :cg::jsonb, :cs::jsonb)
            """), {
                "rn":      repo_name,
                "trigger": trigger,
                "sha":     commit_sha,
                "tag":     tag,
                "cg":      json.dumps(current_edges),
                "cs":      json.dumps(chunk_summary),
            })
            logger.info(
                "CodeIndexer: full call-graph snapshot saved for %s (%d edges)",
                repo_name, len(current_edges),
            )
        else:
            # Delta snapshot
            parent_graph = parent_row.call_graph or []
            prev_set = {
                (e["caller_name"], e["callee_name"], e.get("caller_file", ""))
                for e in parent_graph
            }
            curr_set = {
                (e["caller_name"], e["callee_name"], e.get("caller_file", ""))
                for e in current_edges
            }
            added   = [{"caller_name": a, "callee_name": b, "caller_file": c}
                       for a, b, c in (curr_set - prev_set)]
            removed = [{"caller_name": a, "callee_name": b, "caller_file": c}
                       for a, b, c in (prev_set - curr_set)]
            await session.execute(text("""
                INSERT INTO code_graph_snapshots
                    (repo_name, trigger, commit_sha, tag, is_full,
                     parent_id, added_edges, removed_edges)
                VALUES (:rn, :trigger, :sha, :tag, FALSE,
                        :parent_id, :added::jsonb, :removed::jsonb)
            """), {
                "rn":        repo_name,
                "trigger":   trigger,
                "sha":       commit_sha,
                "tag":       tag,
                "parent_id": parent_row.id,
                "added":     json.dumps(added),
                "removed":   json.dumps(removed),
            })
            logger.info(
                "CodeIndexer: delta snapshot saved for %s (+%d/-%d edges)",
                repo_name, len(added), len(removed),
            )

    async def _index_codeowners(
        self,
        repo_name: str,
        codeowners_path: Path,
    ) -> None:
        """Parse a CODEOWNERS file and upsert pattern→owners into code_owners."""
        try:
            content = codeowners_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            logger.warning("CodeIndexer: could not read CODEOWNERS: %s", exc)
            return

        entries: List[Dict[str, Any]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            pattern = parts[0]
            owners  = parts[1:]
            entries.append({"pattern": pattern, "owners": owners})

        if not entries:
            return

        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_owners (
                    id           SERIAL PRIMARY KEY,
                    repo_name    VARCHAR(255) NOT NULL,
                    path_pattern VARCHAR(500) NOT NULL,
                    owners       JSONB        NOT NULL,
                    indexed_at   TIMESTAMPTZ  DEFAULT NOW(),
                    UNIQUE (repo_name, path_pattern)
                )
            """))

            for entry in entries:
                await session.execute(text("""
                    INSERT INTO code_owners
                        (repo_name, path_pattern, owners, indexed_at)
                    VALUES (:rn, :pat, :owners::jsonb, NOW())
                    ON CONFLICT (repo_name, path_pattern)
                    DO UPDATE SET owners = EXCLUDED.owners, indexed_at = NOW()
                """), {
                    "rn":     repo_name,
                    "pat":    entry["pattern"],
                    "owners": json.dumps(entry["owners"]),
                })
            await session.commit()

        logger.info(
            "CodeIndexer: indexed %d CODEOWNERS entries for %s",
            len(entries), repo_name,
        )

    async def _infer_deployments_from_tags(
        self,
        repo_name: str,
        local_path: str,
    ) -> None:
        """Infer deployment records from annotated git tags (fallback only).

        CI/CD pipelines should write to the deployments table directly.  This
        method only adds rows that are not already present (ON CONFLICT DO NOTHING).
        """
        root = Path(local_path).resolve()
        if not root.exists():
            return

        def _run_git_tags() -> List[Dict[str, str]]:
            result = subprocess.run(
                [
                    "git", "-C", str(root),
                    "tag", "-l",
                    "--sort=-version:refname",
                    "--format=%(refname:short) %(creatordate:iso-strict) %(objectname:short)",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            tags: List[Dict[str, str]] = []
            for line in result.stdout.strip().splitlines():
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    tags.append({
                        "tag":        parts[0],
                        "deployed_at": parts[1],
                        "commit_sha": parts[2] if len(parts) > 2 else "",
                    })
            return tags

        loop = asyncio.get_event_loop()
        try:
            tags = await loop.run_in_executor(None, _run_git_tags)
        except (subprocess.TimeoutExpired, Exception) as exc:
            logger.debug("CodeIndexer: git tag lookup failed for %s: %s", repo_name, exc)
            return

        if not tags:
            return

        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS deployments (
                    id          SERIAL PRIMARY KEY,
                    repo_name   VARCHAR(255) NOT NULL,
                    commit_sha  VARCHAR(40),
                    deployed_at TIMESTAMPTZ  NOT NULL,
                    environment VARCHAR(50)  DEFAULT 'production',
                    deployed_by VARCHAR(255),
                    tag         VARCHAR(255),
                    notes       TEXT,
                    created_at  TIMESTAMPTZ  DEFAULT NOW()
                )
            """))

            for t in tags:
                try:
                    await session.execute(text("""
                        INSERT INTO deployments
                            (repo_name, commit_sha, deployed_at, tag, notes)
                        VALUES
                            (:rn, :sha, :deployed_at, :tag,
                             'inferred from git tag')
                        ON CONFLICT DO NOTHING
                    """), {
                        "rn":          repo_name,
                        "sha":         t["commit_sha"] or None,
                        "deployed_at": t["deployed_at"],
                        "tag":         t["tag"],
                    })
                except Exception:
                    pass  # malformed date or constraint — skip this tag

            await session.commit()

        logger.info(
            "CodeIndexer: inferred %d deployments from git tags for %s",
            len(tags), repo_name,
        )

    async def _set_enrichment_phase(
        self, repo_name: str, phase: str,
    ) -> None:
        """Update enrichment_phase on all code_chunks rows for a repo."""
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                UPDATE code_chunks
                   SET enrichment_phase = :phase
                 WHERE repo_name = :rn
            """), {"phase": phase, "rn": repo_name})
            await session.commit()

    # ── New enrichment helpers ────────────────────────────────────────────────

    async def _get_changed_files(self, local_path: str) -> set[str]:
        """Return relative POSIX paths of files changed since the previous commit."""
        def _run() -> set[str]:
            try:
                result = subprocess.run(
                    ["git", "-C", local_path, "diff", "--name-only", "HEAD~1", "HEAD"],
                    capture_output=True, text=True, timeout=15,
                )
                return {
                    line.strip()
                    for line in result.stdout.splitlines()
                    if line.strip()
                }
            except Exception:
                return set()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _run)

    async def _resolve_cross_file_calls(self, repo_name: str) -> None:
        """Populate callee_file in code_calls by joining imports + chunks."""
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                UPDATE code_calls cc
                   SET callee_file = ch.file_path
                  FROM code_chunks ch
                 WHERE ch.repo_name = cc.caller_repo
                   AND ch.name      = cc.callee_name
                   AND cc.caller_repo = :rn
                   AND (cc.callee_file IS NULL OR cc.callee_file = '')
            """), {"rn": repo_name})
            await session.commit()
        logger.info("CodeIndexer: resolved cross-file calls for %s", repo_name)

    async def _link_test_files(self, repo_name: str) -> None:
        """Insert test→source edges into code_test_links."""
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_test_links (
                    id           SERIAL PRIMARY KEY,
                    repo_name    VARCHAR(255),
                    test_file    VARCHAR(1000),
                    source_file  VARCHAR(1000),
                    test_name    VARCHAR(500),
                    source_name  VARCHAR(500),
                    indexed_at   TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (repo_name, test_file, test_name, source_name)
                )
            """))
            # Match test chunks to source chunks by heuristic name proximity
            rows = await session.execute(text("""
                SELECT tc.file_path  AS test_file,
                       tc.name       AS test_name,
                       sc.file_path  AS source_file,
                       sc.name       AS source_name
                  FROM code_chunks tc
                  JOIN code_chunks sc
                    ON sc.repo_name = tc.repo_name
                   AND sc.name = tc.name  -- exact name match after test prefix stripped
                 WHERE tc.repo_name = :rn
                   AND (
                         tc.file_path LIKE '%test%'
                      OR tc.file_path LIKE '%spec%'
                      OR tc.file_path LIKE '%__tests__%'
                   )
                   AND NOT (
                         sc.file_path LIKE '%test%'
                      OR sc.file_path LIKE '%spec%'
                      OR sc.file_path LIKE '%__tests__%'
                   )
            """), {"rn": repo_name})
            links = rows.fetchall()
            for lnk in links:
                await session.execute(text("""
                    INSERT INTO code_test_links
                        (repo_name, test_file, source_file, test_name, source_name, indexed_at)
                    VALUES (:rn, :tf, :sf, :tn, :sn, NOW())
                    ON CONFLICT (repo_name, test_file, test_name, source_name) DO NOTHING
                """), {
                    "rn": repo_name,
                    "tf": lnk.test_file,
                    "sf": lnk.source_file,
                    "tn": lnk.test_name,
                    "sn": lnk.source_name,
                })
            await session.commit()
        logger.info("CodeIndexer: linked %d test→source pairs for %s", len(links), repo_name)

    async def _index_log_patterns(self, patterns: List[LogPattern]) -> None:
        """Upsert log call-site patterns into code_log_patterns."""
        if not patterns:
            return
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_log_patterns (
                    id               SERIAL PRIMARY KEY,
                    repo_name        VARCHAR(255),
                    file_path        VARCHAR(1000),
                    function_name    VARCHAR(500),
                    log_level        VARCHAR(20),
                    message_template TEXT,
                    line_number      INTEGER,
                    indexed_at       TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (repo_name, file_path, line_number)
                )
            """))
            for p in patterns:
                await session.execute(text("""
                    INSERT INTO code_log_patterns
                        (repo_name, file_path, function_name, log_level,
                         message_template, line_number, indexed_at)
                    VALUES (:rn, :fp, :fn, :ll, :mt, :ln, NOW())
                    ON CONFLICT (repo_name, file_path, line_number)
                    DO UPDATE SET
                        function_name    = EXCLUDED.function_name,
                        log_level        = EXCLUDED.log_level,
                        message_template = EXCLUDED.message_template,
                        indexed_at       = NOW()
                """), {
                    "rn": p.repo_name, "fp": p.file_path,
                    "fn": p.function_name, "ll": p.log_level,
                    "mt": p.message_template, "ln": p.line_number,
                })
            await session.commit()
        logger.info("CodeIndexer: indexed %d log patterns", len(patterns))
