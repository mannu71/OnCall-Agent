"""Code Intelligence Indexer.

Walks a local repository, extracts function and class definitions using the
Python `ast` module (Python files) and regex patterns (TypeScript/TSX files),
embeds each chunk via AWS Bedrock Titan, and upserts into the code_chunks table.

Usage:
    from app.services.code_indexer import CodeIndexer

    indexer = CodeIndexer()
    chunks = await indexer.index_repo(
        repo_name="kyc-protect-api",
        local_path="/tmp/repos/kyc-protect-api",
        language="python",
    )
    print(f"Indexed {len(chunks)} chunks")
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.security import check_path, PathJailError

logger = logging.getLogger(__name__)

# Maximum file size to index (skip huge generated files)
_MAX_FILE_BYTES = 500_000
# Bedrock Titan embedding model
_EMBED_MODEL = "amazon.titan-embed-text-v2:0"


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
    embedding:  Optional[List[float]] = field(default=None, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# Python AST extraction
# ─────────────────────────────────────────────────────────────────────────────

def _get_source_lines(source: str, node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 0), getattr(node, "end_lineno", 0)


def _extract_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
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
            ))

    return chunks


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
            chunks.append(CodeChunk(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                chunk_type=ctype,
                body=body,
                language="typescript",
                line_start=line_start,
                line_end=line_end,
            ))

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Embedder — AWS Bedrock Titan
# ─────────────────────────────────────────────────────────────────────────────

async def _embed_text(text_to_embed: str, region: str = "us-east-1") -> Optional[List[float]]:
    """Call Bedrock Titan and return a 1536-dim embedding vector."""
    import json
    import boto3

    def _call() -> Optional[List[float]]:
        client  = boto3.client("bedrock-runtime", region_name=region)
        payload = json.dumps({"inputText": text_to_embed[:8191]})
        try:
            resp = client.invoke_model(
                modelId=_EMBED_MODEL,
                body=payload,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(resp["body"].read())
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
    """Walk a local repository, extract code chunks, embed, and upsert to DB."""

    def __init__(
        self,
        embed: bool = True,
        bedrock_region: str = "us-east-1",
        jail_path: Optional[str] = None,
    ):
        self.embed = embed
        self.bedrock_region = bedrock_region
        self.jail_path = jail_path  # If set, all file access is checked against this root

    async def index_repo(
        self,
        repo_name: str,
        local_path: str,
        language: str = "python",
    ) -> List[CodeChunk]:
        """Index all source files in *local_path* and upsert to DB.

        Args:
            repo_name: Unique name for this repository.
            local_path: Absolute path to the local checkout.
            language: 'python' | 'typescript' | 'mixed'

        Returns:
            List of CodeChunk objects that were indexed.
        """
        root = Path(local_path).resolve()
        if not root.exists():
            raise ValueError(f"Repository path does not exist: {root}")

        extensions: list[str] = []
        if language in {"python", "mixed"}:
            extensions += [".py"]
        if language in {"typescript", "mixed"}:
            extensions += [".ts", ".tsx"]

        all_chunks: List[CodeChunk] = []

        for ext in extensions:
            for file_path in root.rglob(f"*{ext}"):
                # Path jail check
                if self.jail_path:
                    try:
                        check_path(file_path, self.jail_path)
                    except PathJailError:
                        logger.warning("Skipping path outside jail: %s", file_path)
                        continue

                if file_path.stat().st_size > _MAX_FILE_BYTES:
                    logger.debug("Skipping oversized file: %s", file_path)
                    continue

                relative = str(file_path.relative_to(root))
                try:
                    source = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

                if ext == ".py":
                    chunks = extract_python_chunks(source, relative, repo_name)
                else:
                    chunks = extract_typescript_chunks(source, relative, repo_name)

                all_chunks.extend(chunks)

        logger.info(
            "CodeIndexer: extracted %d chunks from %s (%s)",
            len(all_chunks), repo_name, language,
        )

        if self.embed:
            await self._embed_chunks(all_chunks)

        await self._upsert_chunks(all_chunks)
        return all_chunks

    async def _embed_chunks(self, chunks: List[CodeChunk]) -> None:
        for chunk in chunks:
            text_to_embed = f"{chunk.signature}\n{chunk.docstring}\n{chunk.body}"[:4096]
            chunk.embedding = await _embed_text(text_to_embed, self.bedrock_region)

    async def _upsert_chunks(self, chunks: List[CodeChunk]) -> None:
        if not chunks:
            return

        async with AsyncSessionLocal() as session:
            for chunk in chunks:
                emb = f"[{','.join(str(v) for v in chunk.embedding)}]" if chunk.embedding else None
                await session.execute(
                    text("""
                        INSERT INTO code_chunks
                            (repo_name, file_path, name, chunk_type, signature, body,
                             docstring, language, line_start, line_end, embedding, indexed_at)
                        VALUES
                            (:repo_name, :file_path, :name, :chunk_type, :signature, :body,
                             :docstring, :language, :line_start, :line_end,
                             :embedding::vector, NOW())
                        ON CONFLICT (repo_name, file_path, name, chunk_type)
                        DO UPDATE SET
                            signature  = EXCLUDED.signature,
                            body       = EXCLUDED.body,
                            docstring  = EXCLUDED.docstring,
                            line_start = EXCLUDED.line_start,
                            line_end   = EXCLUDED.line_end,
                            embedding  = EXCLUDED.embedding,
                            indexed_at = NOW()
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
                    },
                )
            await session.commit()

        logger.info("CodeIndexer: upserted %d chunks", len(chunks))
