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

# In-process telemetry counters. They're plain dicts (not Prometheus clients) so
# this module stays dependency-free; the indexer logs a summary at end-of-run
# and any host metrics layer can scrape ``code_indexer_counters``.
#
# Legacy regex extractors were removed entirely; there is no
# ``regex_fallback_used`` counter because there is no regex fallback.
code_indexer_counters: Dict[str, int] = {
    "files_parsed":          0,
    "files_skipped":         0,
    "files_failed":          0,
    "chunks_emitted":        0,
    "chunks_truncated":      0,
    "parse_failures":        0,
    "grammar_missing":       0,
    "calls_qualified":       0,
    "calls_resolved_xfile":  0,
}


def _bump(counter: str, n: int = 1) -> None:
    """Increment a telemetry counter; never raises."""
    try:
        code_indexer_counters[counter] = code_indexer_counters.get(counter, 0) + n
    except Exception:
        pass


def reset_counters() -> None:
    """Reset all telemetry counters — used by tests."""
    for k in list(code_indexer_counters):
        code_indexer_counters[k] = 0


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


def _stable_entity_key(
    chunk_type: str,
    file_path: str,
    name: str,
    parent_class: str = "",
    param_arity: int = 0,
) -> str:
    """Build a deterministic identity key that does **not** depend on line numbers.

    Two calls with identical (chunk_type, file_path, name, parent_class,
    param_arity) produce the same key — so unrelated edits above the function
    no longer flip the entity_id (was line 687 bug).
    """
    return (
        f"{chunk_type}:"
        f"{file_path}:"
        f"{parent_class}.{name}:"
        f"arity={param_arity}"
    )


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
    # Accuracy-enhancement fields.
    parent_class:        str       = ""   # enclosing class for methods
    parent_entity_id:    str       = ""   # parent class entity_id
    decorators:          List[str] = field(default_factory=list)
    param_arity:         int       = 0
    truncated_for_embed: bool      = False
    body_sha256:         str       = ""   # sha256(body) for incremental embedding skip


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
    # Full dotted/member chain for member calls (e.g. ``self.repo.save``).
    # Empty for plain identifier calls.
    qualified_callee: str = ""
    line_number:      int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Python AST extraction (moved to code_indexing.extractors.python)
# ─────────────────────────────────────────────────────────────────────────────
# Note: ``extract_python_chunks`` / ``extract_python_imports`` /
# ``extract_python_calls`` are re-exported near the bottom of this module
# (after their dependency ``_python_node_complexity`` is defined) to avoid a
# circular import at module-load time.


def _py_decorator_names(node: ast.AST) -> List[str]:
    """Return decorator names for a Python function/class (best effort)."""
    out: List[str] = []
    for d in getattr(node, "decorator_list", []) or []:
        try:
            out.append(ast.unparse(d))
        except Exception:
            pass
    return out


def _py_param_arity(node: ast.AST) -> int:
    """Count declared parameters for a Python function/method."""
    args = getattr(node, "args", None)
    if not args:
        return 0
    n = len(args.args) + len(getattr(args, "kwonlyargs", []))
    if getattr(args, "vararg", None):
        n += 1
    if getattr(args, "kwarg", None):
        n += 1
    return n




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
# Tree-sitter extractors moved to code_indexing.extractors.typescript
# (re-exported near the bottom of this module to keep load order safe)
# ─────────────────────────────────────────────────────────────────────────────



# ─────────────────────────────────────────────────────────────────────────────
# Embedder — AWS Bedrock Titan (extracted to code_indexing.embedding)
# ─────────────────────────────────────────────────────────────────────────────

from app.services.code_indexing.embedding import _embed_text, _embed_texts  # noqa: E402,F401
from app.services.code_indexing.extractors.python import (  # noqa: E402,F401
    extract_python_calls,
    extract_python_chunks,
    extract_python_imports,
)
from app.services.code_indexing.extractors.typescript import (  # noqa: E402,F401
    _extract_chunks_via_ts,
    _extract_cs_imports_via_ts,
    _extract_imports_via_ts,
    extract_typescript_imports,
)


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
        # ``CODE_INDEXER_EMBED=false`` is supported as an emergency escape
        # hatch (chunks persist with NULL vectors when Bedrock is unreachable).
        # Default is True; the Settings-page model + region drives the actual
        # call so most operators never need to flip this.
        env_embed = os.getenv("CODE_INDEXER_EMBED")
        if env_embed is not None and embed is True:
            embed = env_embed.lower() not in ("false", "0", "no", "off")
        self.embed = embed
        self.bedrock_region = bedrock_region
        self.jail_path = jail_path  # If set, all file access is checked against this root
        if not self.embed:
            logger.info(
                "CodeIndexer: embedding disabled (CODE_INDEXER_EMBED=%s); "
                "chunks will be persisted without vectors.",
                env_embed,
            )

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
        if language in {"javascript", "mixed"}:
            extensions += [".js", ".mjs", ".cjs", ".jsx"]
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
        # Deduplicate while preserving order.
        seen_exts: set[str] = set()
        extensions = [e for e in extensions if not (e in seen_exts or seen_exts.add(e))]

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
                    _bump("files_failed")
                    return

                # Extract chunks + calls + imports per language.
                # Non-Python languages go through tree-sitter exclusively;
                # if the grammar is missing, chunks=[] and a warning is logged
                # (no regex fallback — legacy extractors were removed).
                if ext == ".py":
                    chunks  = extract_python_chunks(source, relative, repo_name)
                    calls   = extract_python_calls(source, relative, repo_name)
                    imports = extract_python_imports(source, relative, repo_name)
                elif ext == ".cs":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "csharp", "csharp")
                    imports = _extract_cs_imports_via_ts(source, relative, repo_name)
                elif ext == ".jsx":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "tsx", "react")
                    imports = extract_typescript_imports(source, relative, repo_name, "tsx")
                elif ext == ".tsx":
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "tsx", "typescript")
                    imports = extract_typescript_imports(source, relative, repo_name, "tsx")
                elif ext in {".ts"}:
                    chunks, calls = _extract_chunks_via_ts(source, relative, repo_name, "typescript", "typescript")
                    imports = extract_typescript_imports(source, relative, repo_name, "typescript")
                elif ext in {".js", ".mjs", ".cjs"}:
                    chunks, calls = _extract_chunks_via_ts(
                        source, relative, repo_name, "javascript", "javascript",
                    )
                    imports = extract_typescript_imports(source, relative, repo_name, "javascript")
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

                if chunks:
                    _bump("files_parsed")
                else:
                    _bump("files_skipped")

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
        logger.info(
            "CodeIndexer: accuracy counters %s",
            {k: v for k, v in code_indexer_counters.items() if v},
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
        """Embed chunks, skipping Bedrock calls when body hash is unchanged.

        For each chunk we compute sha256(body) and look up any existing row
        with the same (repo_name, file_path, name, chunk_type).  If the stored
        hash matches we reuse the existing embedding — zero Bedrock calls for
        unchanged chunks (Fix 1, Phase 5).
        """
        if not chunks:
            return

        async with AsyncSessionLocal() as session:
            # Ensure body_sha256 column exists before querying it
            await session.execute(text(
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS body_sha256 VARCHAR(64)"
            ))
            await session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_code_chunks_body_sha256 "
                "ON code_chunks (body_sha256)"
            ))
            await session.commit()
            # Build a VALUES list for the lookup of existing hashes+embeddings
            placeholders = ",".join(
                f"(:rn{i}, :fp{i}, :nm{i}, :ct{i})" for i in range(len(chunks))
            )
            params: dict = {}
            for i, c in enumerate(chunks):
                params[f"rn{i}"] = c.repo_name
                params[f"fp{i}"] = c.file_path
                params[f"nm{i}"] = c.name
                params[f"ct{i}"] = c.chunk_type
            rows = await session.execute(
                text(f"""
                    SELECT repo_name, file_path, name, chunk_type,
                           body_sha256, embedding::text
                      FROM code_chunks
                     WHERE (repo_name, file_path, name, chunk_type)
                           IN ({placeholders})
                """),
                params,
            )
            existing: dict = {
                (r.repo_name, r.file_path, r.name, r.chunk_type):
                    (r.body_sha256, r.embedding)
                for r in rows.fetchall()
            }

        for chunk in chunks:
            new_hash = hashlib.sha256(chunk.body.encode()).hexdigest()
            chunk.body_sha256 = new_hash
            lookup_key = (chunk.repo_name, chunk.file_path, chunk.name, chunk.chunk_type)
            cached = existing.get(lookup_key)
            if cached and cached[0] == new_hash and cached[1] is not None:
                # Body unchanged — parse stored embedding string back to floats
                emb_str: str = cached[1]
                # Postgres returns vector as '[0.1,0.2,...]'
                try:
                    chunk.embedding = [
                        float(v) for v in emb_str.strip("[]").split(",") if v
                    ]
                except Exception:
                    chunk.embedding = None  # fall through to re-embed below
                if chunk.embedding:
                    continue  # skip Bedrock call
            # Hash changed or no existing row — call Bedrock
            text_to_embed = f"{chunk.signature}\n{chunk.docstring}\n{chunk.body}"[:4096]
            chunk.embedding = await _embed_text(text_to_embed, self.bedrock_region)

    async def _upsert_chunks(self, chunks: List[CodeChunk]) -> None:
        if not chunks:
            return

        async with AsyncSessionLocal() as session:
            # Create table on first run (idempotent)
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_chunks (
                    id                    SERIAL PRIMARY KEY,
                    repo_name             VARCHAR(255)  NOT NULL,
                    file_path             TEXT          NOT NULL,
                    name                  TEXT          NOT NULL,
                    chunk_type            VARCHAR(50)   NOT NULL,
                    signature             TEXT,
                    body                  TEXT,
                    docstring             TEXT,
                    language              VARCHAR(50),
                    line_start            INTEGER,
                    line_end              INTEGER,
                    embedding             vector(1536),
                    entity_id             VARCHAR(16),
                    predecessor_entity_id VARCHAR(16),
                    enrichment_phase      VARCHAR(10)   DEFAULT 'raw',
                    complexity            INTEGER       DEFAULT 1,
                    search_vector         tsvector,
                    indexed_at            TIMESTAMPTZ   DEFAULT NOW(),
                    UNIQUE (repo_name, file_path, name, chunk_type)
                )
            """))
            await session.commit()

            # Ensure optional columns exist on older installs (idempotent)
            for ddl in [
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS enrichment_phase VARCHAR(10) DEFAULT 'raw'",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS entity_id VARCHAR(16)",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS predecessor_entity_id VARCHAR(16)",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS complexity INTEGER DEFAULT 1",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS search_vector tsvector",
                # Accuracy-enhancement columns (see code_indexer accuracy plan).
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS parent_class VARCHAR(255)",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS parent_entity_id VARCHAR(16)",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS decorators JSONB DEFAULT '[]'::jsonb",
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS param_arity INTEGER DEFAULT 0",
                # Phase 5 Fix 1: body hash for incremental embedding skip
                "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS body_sha256 VARCHAR(64)",
                "CREATE INDEX IF NOT EXISTS ix_code_chunks_body_sha256 ON code_chunks (body_sha256)",
            ]:
                await session.execute(text(ddl))

            for chunk in chunks:
                emb = (
                    f"[{','.join(str(v) for v in chunk.embedding)}]"
                    if chunk.embedding else None
                )
                # Populate body_sha256 if not already set by _embed_chunks
                if not chunk.body_sha256:
                    chunk.body_sha256 = hashlib.sha256(chunk.body.encode()).hexdigest()
                # Fix 2 (Phase 5): The DO UPDATE clause rebuilds search_vector so
                # BM25 search reflects the latest signature/name/docstring rather
                # than the stale values captured on the original INSERT.
                await session.execute(
                    text("""
                        INSERT INTO code_chunks
                            (repo_name, file_path, name, chunk_type, signature, body,
                             docstring, language, line_start, line_end, embedding,
                             entity_id, enrichment_phase, complexity, search_vector,
                             body_sha256,
                             parent_class, parent_entity_id, decorators, param_arity,
                             indexed_at)
                        VALUES
                            (:repo_name, :file_path, :name, :chunk_type, :signature, :body,
                             :docstring, :language, :line_start, :line_end,
                             :embedding::vector, :entity_id, 'raw', :complexity,
                             to_tsvector('english', coalesce(:sig,'') || ' ' || coalesce(:name2,'') || ' ' || coalesce(:body2,'')),
                             :body_sha256,
                             :parent_class, :parent_entity_id,
                             CAST(:decorators AS JSONB), :param_arity,
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
                            body_sha256      = EXCLUDED.body_sha256,
                            search_vector    = to_tsvector('english',
                                coalesce(EXCLUDED.signature,'') || ' ' ||
                                coalesce(EXCLUDED.name,'') || ' ' ||
                                coalesce(EXCLUDED.docstring,'')),
                            parent_class     = EXCLUDED.parent_class,
                            parent_entity_id = EXCLUDED.parent_entity_id,
                            decorators       = EXCLUDED.decorators,
                            param_arity      = EXCLUDED.param_arity,
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
                        "body_sha256": chunk.body_sha256,
                        # separate short params for tsvector to avoid 50k body
                        "sig":        chunk.signature[:500],
                        "name2":      chunk.name,
                        "body2":      chunk.body[:2000],
                        "parent_class":     chunk.parent_class or "",
                        "parent_entity_id": chunk.parent_entity_id or None,
                        "decorators":       json.dumps(chunk.decorators or []),
                        "param_arity":      chunk.param_arity or 0,
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
            await session.execute(text(
                "ALTER TABLE code_calls ADD COLUMN IF NOT EXISTS "
                "qualified_callee VARCHAR(1000)"
            ))
            await session.execute(text(
                "ALTER TABLE code_calls ADD COLUMN IF NOT EXISTS "
                "line_number INTEGER DEFAULT 0"
            ))

            for call in deduped:
                await session.execute(text("""
                    INSERT INTO code_calls
                        (caller_repo, caller_name, caller_file,
                         callee_name, callee_file,
                         qualified_callee, line_number,
                         call_frequency, edge_weight, indexed_at)
                    VALUES
                        (:repo, :caller, :caller_file,
                         :callee, :callee_file,
                         :qualified, :line_no,
                         1, 1.0, NOW())
                    ON CONFLICT DO NOTHING
                """), {
                    "repo":        call.caller_repo,
                    "caller":      call.caller_name,
                    "caller_file": call.caller_file,
                    "callee":      call.callee_name,
                    "callee_file": call.callee_file,
                    "qualified":   call.qualified_callee or None,
                    "line_no":     call.line_number or 0,
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

    # TODO(post-phase2): delete entire edge-weight cosine block — SCIP gives real call edges
    # in code_references. This O(N²) cosine on caller/callee names is a heuristic
    # superseded by SCIP's deterministic call graph (see app.services.code_indexing.scip_loader).
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
        """Populate callee_file and callee_entity_id for unresolved code_calls rows.

        Resolution preference:
          1. Same-file match — if a chunk with the same name lives in
             ``caller_file``, prefer it (handles helper functions in same module).
          2. Unique repo-wide match — exactly one chunk in the repo with that
             name. Set ``callee_file`` and ``callee_entity_id``.
          3. Ambiguous match — leave unresolved (avoid wrong edges).
        """
        async with AsyncSessionLocal() as session:
            # Step 1: same-file preference.
            await session.execute(text("""
                UPDATE code_calls cc
                   SET callee_file      = ch.file_path,
                       callee_entity_id = ch.entity_id
                  FROM code_chunks ch
                 WHERE ch.repo_name   = cc.caller_repo
                   AND ch.file_path   = cc.caller_file
                   AND ch.name        = cc.callee_name
                   AND cc.caller_repo = :rn
                   AND (cc.callee_file IS NULL OR cc.callee_file = '')
            """), {"rn": repo_name})

            # Step 2: unique repo-wide match.
            await session.execute(text("""
                UPDATE code_calls cc
                   SET callee_file      = sub.file_path,
                       callee_entity_id = sub.entity_id
                  FROM (
                      SELECT name, MIN(file_path) AS file_path,
                             MIN(entity_id)       AS entity_id,
                             COUNT(*)             AS match_count
                        FROM code_chunks
                       WHERE repo_name = :rn
                       GROUP BY name
                      HAVING COUNT(*) = 1
                  ) sub
                 WHERE sub.name        = cc.callee_name
                   AND cc.caller_repo  = :rn
                   AND (cc.callee_file IS NULL OR cc.callee_file = '')
            """), {"rn": repo_name})

            # Count how many we resolved this pass for telemetry.
            res = await session.execute(text("""
                SELECT COUNT(*) FROM code_calls
                 WHERE caller_repo = :rn
                   AND callee_file IS NOT NULL
                   AND callee_file <> ''
            """), {"rn": repo_name})
            resolved = res.scalar() or 0
            await session.commit()

        _bump("calls_resolved_xfile", int(resolved))
        logger.info(
            "CodeIndexer: resolved %d cross-file calls for %s",
            resolved, repo_name,
        )

    async def _link_test_files(self, repo_name: str) -> None:
        """Insert test→source edges into code_test_links using structural rules.

        Phase 5 replacement: three complementary linkage rules produce a
        confidence score rather than a binary name-equality match.

        Rule A (name heuristic, confidence 0.4):
            Strip test/setup/teardown prefixes from the test chunk name; if the
            result matches a source chunk name, create a link.

        Rule B (import-based, confidence 0.4):
            If the test file imports the source chunk's module (via code_imports),
            link all test chunks in that file to source chunks in that module.

        Rule C (body-mention, confidence 0.4):
            If the test chunk's body contains a token matching the source chunk's
            name AND the test file is under a tests/ directory, create a link.

        Confidence is additive: a link that matches all three rules scores 1.0.
        The highest confidence seen for a (test_chunk, source_chunk) pair wins.

        # TODO(phase2-scip): replace with SCIP reference edges when
        # scip_loader populates code_references.
        """
        _TEST_DIR_PATTERNS_LINK = ("%/tests/%", "%/test/%", "%/spec/%", "%/__tests__/%",
                                   "tests/%", "test/%", "spec/%", "__tests__/%")
        _TEST_NAME_PREFIXES = ("test_", "test", "setup_", "teardown_", "spec_")

        def _strip_test_prefix(name: str) -> str:
            lower = name.lower()
            for pfx in _TEST_NAME_PREFIXES:
                if lower.startswith(pfx) and len(name) > len(pfx):
                    return name[len(pfx):]
            return name

        def _is_under_tests(fp: str) -> bool:
            fp_norm = fp.replace("\\", "/")
            return any(
                fp_norm.startswith(p.lstrip("%/")) or ("/" + p.lstrip("%/")) in fp_norm
                for p in _TEST_DIR_PATTERNS_LINK
            ) or "test" in fp_norm or "spec" in fp_norm

        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_test_links (
                    id           SERIAL PRIMARY KEY,
                    repo_name    VARCHAR(255),
                    test_file    VARCHAR(1000),
                    source_file  VARCHAR(1000),
                    test_name    VARCHAR(500),
                    source_name  VARCHAR(500),
                    confidence   FLOAT        DEFAULT 0.4,
                    indexed_at   TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (repo_name, test_file, test_name, source_name)
                )
            """))
            # Ensure confidence column exists on older installs (idempotent)
            await session.execute(text(
                "ALTER TABLE code_test_links "
                "ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 0.4"
            ))

            # ── Load test chunks ─────────────────────────────────────────────
            tc_rows = await session.execute(text("""
                SELECT id, file_path, name, body
                  FROM code_chunks
                 WHERE repo_name = :rn
                   AND (
                         file_path LIKE '%test%'
                      OR file_path LIKE '%spec%'
                      OR file_path LIKE '%__tests__%'
                   )
            """), {"rn": repo_name})
            test_chunks = tc_rows.fetchall()

            # ── Load source chunks (non-test) ────────────────────────────────
            sc_rows = await session.execute(text("""
                SELECT id, file_path, name, body
                  FROM code_chunks
                 WHERE repo_name = :rn
                   AND file_path NOT LIKE '%test%'
                   AND file_path NOT LIKE '%spec%'
                   AND file_path NOT LIKE '%__tests__%'
            """), {"rn": repo_name})
            source_chunks = sc_rows.fetchall()

            # ── Load import edges for import-based rule ──────────────────────
            imp_rows = await session.execute(text("""
                SELECT DISTINCT file_path, import_name
                  FROM code_imports
                 WHERE repo_name = :rn
            """), {"rn": repo_name})
            # test_file -> set of imported names (lowercase)
            file_imports: dict = {}
            for imp in imp_rows.fetchall():
                file_imports.setdefault(imp.file_path, set()).add(imp.import_name.lower())

            # ── Collect links with confidence ────────────────────────────────
            # key: (test_file, test_name, source_file, source_name) -> confidence
            best: dict = {}

            for tc in test_chunks:
                tc_file: str = tc.file_path
                tc_name: str = tc.name
                tc_body: str = tc.body or ""
                tc_imports: set = file_imports.get(tc_file, set())
                is_under_tests_dir = _is_under_tests(tc_file)

                stripped = _strip_test_prefix(tc_name).lower()

                for sc in source_chunks:
                    sc_name_lower = sc.name.lower()
                    link_key = (tc_file, tc_name, sc.file_path, sc.name)
                    conf = 0.0

                    # Rule A — name heuristic (score 0.4)
                    if stripped and stripped == sc_name_lower:
                        conf += 0.4

                    # Rule B — import-based (score 0.4)
                    if sc_name_lower in tc_imports:
                        conf += 0.4

                    # Rule C — body mention; word-boundary only, tests/ dir only (score 0.4)
                    if is_under_tests_dir and sc_name_lower in tc_body.lower():
                        if re.search(r'\b' + re.escape(sc.name) + r'\b', tc_body):
                            conf += 0.4

                    if conf > 0.0:
                        conf = min(1.0, round(conf, 2))
                        if best.get(link_key, 0.0) < conf:
                            best[link_key] = conf

            # ── Persist ──────────────────────────────────────────────────────
            for (tf, tn, sf, sn), conf in best.items():
                await session.execute(text("""
                    INSERT INTO code_test_links
                        (repo_name, test_file, source_file, test_name,
                         source_name, confidence, indexed_at)
                    VALUES (:rn, :tf, :sf, :tn, :sn, :conf, NOW())
                    ON CONFLICT (repo_name, test_file, test_name, source_name)
                    DO UPDATE SET
                        confidence = GREATEST(code_test_links.confidence, EXCLUDED.confidence),
                        indexed_at = NOW()
                """), {
                    "rn": repo_name, "tf": tf, "sf": sf,
                    "tn": tn, "sn": sn, "conf": conf,
                })
            await session.commit()

        logger.info(
            "CodeIndexer: linked %d test→source pairs for %s (structural rules)",
            len(best), repo_name,
        )

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
