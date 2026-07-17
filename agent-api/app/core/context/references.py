"""Context reference parsing for @file, @folder, @url, @diff, @staged, @git patterns.

Provides parsing of context references from user messages to enable
injection of external content like files, URLs, and git state.
"""
from __future__ import annotations

import asyncio
import inspect
import mimetypes
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, List, Optional


@dataclass(frozen=True)
class ContextReference:
    """Parsed context reference from user message.
    
    Attributes:
        raw: Original reference text (e.g., "@file:path.py:10-20")
        kind: Type of reference ("file" | "folder" | "url" | "diff" | "staged" | "git")
        target: Path or URL being referenced
        start: Start position in message
        end: End position in message
        line_start: Starting line number for file references (1-indexed)
        line_end: Ending line number for file references (1-indexed)
    """
    raw: str
    kind: str
    target: str
    start: int
    end: int
    line_start: int | None = None
    line_end: int | None = None


@dataclass
class ContextReferenceResult:
    """Result of reference preprocessing.
    
    Attributes:
        message: Processed message with references expanded
        original_message: Original message before processing
        references: List of parsed references
        warnings: List of warning messages
        injected_tokens: Total tokens injected from references
        expanded: Whether any references were expanded
        blocked: Whether expansion was blocked due to hard limit
    """
    message: str
    original_message: str
    references: List[ContextReference] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    injected_tokens: int = 0
    expanded: bool = False
    blocked: bool = False


# Constants for security
TRAILING_PUNCTUATION = ",.;!?"
_SENSITIVE_HOME_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", ".config/gh")
_SENSITIVE_HOME_FILES = (
    Path(".ssh") / "authorized_keys",
    Path(".ssh") / "id_rsa",
    Path(".ssh") / "id_ed25519",
    Path(".ssh") / "config",
    Path(".bashrc"),
    Path(".zshrc"),
    Path(".profile"),
    Path(".bash_profile"),
    Path(".zprofile"),
    Path(".netrc"),
    Path(".pgpass"),
    Path(".npmrc"),
    Path(".pypirc"),
)
_SENSITIVE_APP_FILES = (".env", ".env.local", ".env.production")


# Regex patterns for different reference types
_QUOTED_REFERENCE_VALUE = r'(?:`[^`\n]+`|"[^"\n]+"|\'[^\'\n]+\')'
REFERENCE_PATTERN = re.compile(
    rf"(?<![\w/])@(?:(?P<simple>diff|staged)\b|(?P<kind>file|folder|git|url):(?P<value>{_QUOTED_REFERENCE_VALUE}(?::\d+(?:-\d+)?)?|\S+))"
)


def parse_context_references(message: str) -> List[ContextReference]:
    """Parse @ references from message.
    
    Supported patterns:
    - @file:path or @file:`path with spaces` - inject file contents
    - @file:path:10-20 - inject file lines 10-20
    - @file:path:10 - inject file from line 10 to end
    - @folder:path or @folder:`path with spaces` - inject directory listing
    - @url:https://... or @url:`https://...` - fetch and inject URL content
    - @diff - inject git diff output
    - @staged - inject staged changes
    - @git:N - inject last N git commits with patches
    
    Args:
        message: User message containing @ references
    
    Returns:
        List of ContextReference objects in order of appearance
    """
    refs: List[ContextReference] = []
    if not message:
        return refs

    for match in REFERENCE_PATTERN.finditer(message):
        simple = match.group("simple")
        if simple:
            refs.append(
                ContextReference(
                    raw=match.group(0),
                    kind=simple,
                    target="",
                    start=match.start(),
                    end=match.end(),
                )
            )
            continue

        kind = match.group("kind")
        value = _strip_trailing_punctuation(match.group("value") or "")
        line_start = None
        line_end = None
        target = _strip_reference_wrappers(value)

        if kind == "file":
            target, line_start, line_end = _parse_file_reference_value(value)

        refs.append(
            ContextReference(
                raw=match.group(0),
                kind=kind,
                target=target,
                start=match.start(),
                end=match.end(),
                line_start=line_start,
                line_end=line_end,
            )
        )

    return refs



def preprocess_context_references(
    message: str,
    *,
    cwd: str | Path,
    context_length: int,
    url_fetcher: Callable[[str], str | Awaitable[str]] | None = None,
    allowed_root: str | Path | None = None,
) -> ContextReferenceResult:
    """Preprocess context references synchronously.
    
    This is a synchronous wrapper around preprocess_context_references_async.
    It handles both cases where an event loop is running and where it's not.
    
    Args:
        message: User message containing @ references
        cwd: Current working directory for resolving relative paths
        context_length: Model context length for token limit calculations
        url_fetcher: Optional custom URL fetcher function
        allowed_root: Optional root directory to restrict file access
    
    Returns:
        ContextReferenceResult with expanded content or warnings
    """
    coro = preprocess_context_references_async(
        message,
        cwd=cwd,
        context_length=context_length,
        url_fetcher=url_fetcher,
        allowed_root=allowed_root,
    )
    # Safe for both CLI (no loop) and gateway (loop already running).
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


async def preprocess_context_references_async(
    message: str,
    *,
    cwd: str | Path,
    context_length: int,
    url_fetcher: Callable[[str], str | Awaitable[str]] | None = None,
    allowed_root: str | Path | None = None,
) -> ContextReferenceResult:
    """Preprocess context references asynchronously.
    
    Expands @file, @folder, @url, @diff, @staged, @git references and injects
    their content into the message. Enforces token limits and security restrictions.
    
    Token limits:
    - Soft limit: 25% of context_length (warning)
    - Hard limit: 50% of context_length (refuse)
    
    Security:
    - Block sensitive credential files
    - Enforce allowed_root for file access
    
    Args:
        message: User message containing @ references
        cwd: Current working directory for resolving relative paths
        context_length: Model context length for token limit calculations
        url_fetcher: Optional custom URL fetcher function
        allowed_root: Optional root directory to restrict file access
    
    Returns:
        ContextReferenceResult with expanded content or warnings
    """
    from app.core.llm.model_metadata import estimate_tokens_rough
    
    refs = parse_context_references(message)
    if not refs:
        return ContextReferenceResult(message=message, original_message=message)

    cwd_path = Path(cwd).expanduser().resolve()
    # Default to the current working directory so @ references cannot escape
    # the active workspace unless a caller explicitly widens the root.
    allowed_root_path = (
        Path(allowed_root).expanduser().resolve() if allowed_root is not None else cwd_path
    )
    warnings: List[str] = []
    blocks: List[str] = []
    injected_tokens = 0

    for ref in refs:
        warning, block = await _expand_reference(
            ref,
            cwd_path,
            url_fetcher=url_fetcher,
            allowed_root=allowed_root_path,
        )
        if warning:
            warnings.append(warning)
        if block:
            blocks.append(block)
            injected_tokens += estimate_tokens_rough(block)

    hard_limit = max(1, int(context_length * 0.50))
    soft_limit = max(1, int(context_length * 0.25))
    if injected_tokens > hard_limit:
        warnings.append(
            f"@ context injection refused: {injected_tokens} tokens exceeds the 50% hard limit ({hard_limit})."
        )
        return ContextReferenceResult(
            message=message,
            original_message=message,
            references=refs,
            warnings=warnings,
            injected_tokens=injected_tokens,
            expanded=False,
            blocked=True,
        )

    if injected_tokens > soft_limit:
        warnings.append(
            f"@ context injection warning: {injected_tokens} tokens exceeds the 25% soft limit ({soft_limit})."
        )

    stripped = _remove_reference_tokens(message, refs)
    final = stripped
    if warnings:
        final = f"{final}\n\n--- Context Warnings ---\n" + "\n".join(f"- {warning}" for warning in warnings)
    if blocks:
        final = f"{final}\n\n--- Attached Context ---\n\n" + "\n\n".join(blocks)

    return ContextReferenceResult(
        message=final.strip(),
        original_message=message,
        references=refs,
        warnings=warnings,
        injected_tokens=injected_tokens,
        expanded=bool(blocks or warnings),
        blocked=False,
    )


async def _expand_reference(
    ref: ContextReference,
    cwd: Path,
    *,
    url_fetcher: Callable[[str], str | Awaitable[str]] | None = None,
    allowed_root: Path | None = None,
) -> tuple[str | None, str | None]:
    """Expand a single context reference.
    
    Returns:
        Tuple of (warning_message, content_block)
    """
    try:
        if ref.kind == "file":
            return _expand_file_reference(ref, cwd, allowed_root=allowed_root)
        if ref.kind == "folder":
            return _expand_folder_reference(ref, cwd, allowed_root=allowed_root)
        if ref.kind == "diff":
            return _expand_git_reference(ref, cwd, ["diff"], "git diff")
        if ref.kind == "staged":
            return _expand_git_reference(ref, cwd, ["diff", "--staged"], "git diff --staged")
        if ref.kind == "git":
            count = max(1, min(int(ref.target or "1"), 10))
            return _expand_git_reference(ref, cwd, ["log", f"-{count}", "-p"], f"git log -{count} -p")
        if ref.kind == "url":
            content = await _fetch_url_content(ref.target, url_fetcher=url_fetcher)
            if not content:
                return f"{ref.raw}: no content extracted", None
            from app.core.llm.model_metadata import estimate_tokens_rough
            return None, f"🌐 {ref.raw} ({estimate_tokens_rough(content)} tokens)\n{content}"
    except Exception as exc:
        return f"{ref.raw}: {exc}", None

    return f"{ref.raw}: unsupported reference type", None


def _expand_file_reference(
    ref: ContextReference,
    cwd: Path,
    *,
    allowed_root: Path | None = None,
) -> tuple[str | None, str | None]:
    """Expand a @file reference."""
    from app.core.llm.model_metadata import estimate_tokens_rough
    
    path = _resolve_path(cwd, ref.target, allowed_root=allowed_root)
    _ensure_reference_path_allowed(path)
    if not path.exists():
        return f"{ref.raw}: file not found", None
    if not path.is_file():
        return f"{ref.raw}: path is not a file", None
    if _is_binary_file(path):
        return f"{ref.raw}: binary files are not supported", None

    text = path.read_text(encoding="utf-8")
    if ref.line_start is not None:
        lines = text.splitlines()
        start_idx = max(ref.line_start - 1, 0)
        # If line_end is None, read from line_start to end of file
        # If line_end is provided, read up to that line
        end_idx = min(ref.line_end, len(lines)) if ref.line_end is not None else len(lines)
        text = "\n".join(lines[start_idx:end_idx])

    lang = _code_fence_language(path)
    label = ref.raw
    return None, f"📄 {label} ({estimate_tokens_rough(text)} tokens)\n```{lang}\n{text}\n```"


def _expand_folder_reference(
    ref: ContextReference,
    cwd: Path,
    *,
    allowed_root: Path | None = None,
) -> tuple[str | None, str | None]:
    """Expand a @folder reference."""
    from app.core.llm.model_metadata import estimate_tokens_rough
    
    path = _resolve_path(cwd, ref.target, allowed_root=allowed_root)
    _ensure_reference_path_allowed(path)
    if not path.exists():
        return f"{ref.raw}: folder not found", None
    if not path.is_dir():
        return f"{ref.raw}: path is not a folder", None

    listing = _build_folder_listing(path, cwd)
    return None, f"📁 {ref.raw} ({estimate_tokens_rough(listing)} tokens)\n{listing}"


def _expand_git_reference(
    ref: ContextReference,
    cwd: Path,
    args: List[str],
    label: str,
) -> tuple[str | None, str | None]:
    """Expand a git reference (@diff, @staged, @git:N)."""
    from app.core.llm.model_metadata import estimate_tokens_rough
    
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return f"{ref.raw}: git command timed out (30s)", None
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "git command failed"
        return f"{ref.raw}: {stderr}", None
    content = result.stdout.strip()
    if not content:
        content = "(no output)"
    return None, f"🧾 {label} ({estimate_tokens_rough(content)} tokens)\n```diff\n{content}\n```"


async def _fetch_url_content(
    url: str,
    *,
    url_fetcher: Callable[[str], str | Awaitable[str]] | None = None,
) -> str:
    """Fetch content from a URL."""
    fetcher = url_fetcher or _default_url_fetcher
    content = fetcher(url)
    if inspect.isawaitable(content):
        content = await content
    return str(content or "").strip()


async def _default_url_fetcher(url: str) -> str:
    """Default URL fetcher using web tools if available."""
    # For now, return a placeholder. This can be enhanced with actual web fetching.
    # In the full implementation, this would use the web_extract_tool or similar.
    return f"[URL content from {url} - fetching not yet implemented]"


def _resolve_path(cwd: Path, target: str, *, allowed_root: Path | None = None) -> Path:
    """Resolve a file path relative to cwd and validate it's within allowed_root."""
    path = Path(os.path.expanduser(target))
    if not path.is_absolute():
        path = cwd / path
    resolved = path.resolve()
    if allowed_root is not None:
        try:
            resolved.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError("path is outside the allowed workspace") from exc
    return resolved


def _ensure_reference_path_allowed(path: Path) -> None:
    """Ensure the path is not a sensitive credential file."""
    home = Path(os.path.expanduser("~")).resolve()

    blocked_exact = {home / rel for rel in _SENSITIVE_HOME_FILES}
    # Also block .env files in any directory
    if path.name in _SENSITIVE_APP_FILES:
        raise ValueError("path is a sensitive credential file and cannot be attached")
    
    blocked_dirs = [home / rel for rel in _SENSITIVE_HOME_DIRS]

    if path in blocked_exact:
        raise ValueError("path is a sensitive credential file and cannot be attached")

    for blocked_dir in blocked_dirs:
        try:
            path.relative_to(blocked_dir)
        except ValueError:
            continue
        raise ValueError("path is a sensitive credential or internal path and cannot be attached")


def _strip_trailing_punctuation(value: str) -> str:
    """Strip trailing punctuation from reference values.
    
    This handles cases where a reference appears at the end of a sentence,
    like "Check @file:readme.md." where the period is sentence punctuation,
    not part of the filename.
    
    Special handling:
    - URLs are not stripped (punctuation is significant)
    - Single character values are not stripped (e.g., '.')
    - Trailing '.' is only stripped if it looks like sentence punctuation
      (i.e., not part of a file extension or path component)
    """
    if not value or len(value) == 1:
        return value
    
    # For URLs, don't strip anything
    if value.startswith(('http://', 'https://', 'ftp://', 'file://')):
        return value
    
    # Strip trailing punctuation, but be conservative with '.'
    # Only strip '.' if it's clearly sentence punctuation (followed by nothing or whitespace in context)
    # For now, don't strip '.' at all since it's commonly part of paths/filenames
    stripped = value.rstrip(TRAILING_PUNCTUATION.replace('.', ''))
    
    # Handle unmatched closing brackets
    while stripped.endswith((")", "]", "}")):
        closer = stripped[-1]
        opener = {")": "(", "]": "[", "}": "{"}[closer]
        if stripped.count(closer) > stripped.count(opener):
            stripped = stripped[:-1]
            continue
        break
    
    return stripped if stripped else value


def _strip_reference_wrappers(value: str) -> str:
    """Strip backticks or quotes from reference values."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "`\"'":
        return value[1:-1]
    return value


def _parse_file_reference_value(value: str) -> tuple[str, int | None, int | None]:
    """Parse file reference value to extract path and line range."""
    quoted_match = re.match(
        r'^(?P<quote>`|"|\')(?P<path>.+?)(?P=quote)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$',
        value,
    )
    if quoted_match:
        line_start = quoted_match.group("start")
        line_end = quoted_match.group("end")
        return (
            quoted_match.group("path"),
            int(line_start) if line_start is not None else None,
            int(line_end) if line_end is not None else None,  # Changed: don't default to line_start
        )

    range_match = re.match(r"^(?P<path>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?$", value)
    if range_match:
        line_start = int(range_match.group("start"))
        line_end_str = range_match.group("end")
        return (
            range_match.group("path"),
            line_start,
            int(line_end_str) if line_end_str is not None else None,  # Changed: don't default to line_start
        )

    return _strip_reference_wrappers(value), None, None


def _remove_reference_tokens(message: str, refs: List[ContextReference]) -> str:
    """Remove reference tokens from the message."""
    pieces: List[str] = []
    cursor = 0
    for ref in refs:
        pieces.append(message[cursor:ref.start])
        cursor = ref.end
    pieces.append(message[cursor:])
    text = "".join(pieces)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


def _is_binary_file(path: Path) -> bool:
    """Check if a file is binary."""
    mime, _ = mimetypes.guess_type(path.name)
    if mime and not mime.startswith("text/") and not any(
        path.name.endswith(ext) for ext in (".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".js", ".ts")
    ):
        return True
    chunk = path.read_bytes()[:4096]
    return b"\x00" in chunk


def _build_folder_listing(path: Path, cwd: Path, limit: int = 200) -> str:
    """Build a folder listing."""
    lines = [f"{path.relative_to(cwd)}/"]
    entries = _iter_visible_entries(path, cwd, limit=limit)
    for entry in entries:
        rel = entry.relative_to(cwd)
        indent = "  " * max(len(rel.parts) - len(path.relative_to(cwd).parts) - 1, 0)
        if entry.is_dir():
            lines.append(f"{indent}- {entry.name}/")
        else:
            meta = _file_metadata(entry)
            lines.append(f"{indent}- {entry.name} ({meta})")
    if len(entries) >= limit:
        lines.append("- ...")
    return "\n".join(lines)


def _iter_visible_entries(path: Path, cwd: Path, limit: int) -> List[Path]:
    """Iterate visible entries in a directory."""
    rg_entries = _rg_files(path, cwd, limit=limit)
    if rg_entries is not None:
        output: List[Path] = []
        seen_dirs: set[Path] = set()
        for rel in rg_entries:
            full = cwd / rel
            for parent in full.parents:
                if parent == cwd or parent in seen_dirs or path not in {parent, *parent.parents}:
                    continue
                seen_dirs.add(parent)
                output.append(parent)
            output.append(full)
        return sorted({p for p in output if p.exists()}, key=lambda p: (not p.is_dir(), str(p)))

    output = []
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
        files = sorted(f for f in files if not f.startswith("."))
        root_path = Path(root)
        for d in dirs:
            output.append(root_path / d)
            if len(output) >= limit:
                return output
        for f in files:
            output.append(root_path / f)
            if len(output) >= limit:
                return output
    return output


def _rg_files(path: Path, cwd: Path, limit: int) -> List[Path] | None:
    """Use ripgrep to list files if available."""
    try:
        result = subprocess.run(
            ["rg", "--files", str(path.relative_to(cwd))],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    files = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    return files[:limit]


def _file_metadata(path: Path) -> str:
    """Get file metadata for display."""
    if _is_binary_file(path):
        return f"{path.stat().st_size} bytes"
    try:
        line_count = path.read_text(encoding="utf-8").count("\n") + 1
    except Exception:
        return f"{path.stat().st_size} bytes"
    return f"{line_count} lines"


def _code_fence_language(path: Path) -> str:
    """Get code fence language for a file."""
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".md": "markdown",
        ".sh": "bash",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
    }
    return mapping.get(path.suffix.lower(), "")
