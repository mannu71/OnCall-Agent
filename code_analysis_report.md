# Agent-API Code Analysis Report

> Comprehensive analysis of `agent-api/app/` — unused code, dead code, and optimization opportunities.
> Generated: 2026-05-04

---

## Executive Summary

| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 **HIGH** | 12 | Unused files/code that should be removed |
| 🟡 **MEDIUM** | 14 | Optimizations, unused imports, dead patterns |
| 🔵 **LOW** | 8 | Style, minor improvements |

---

## 🔴 HIGH Severity — Unused Code to Remove

### H1. `app/core/demo_rate_limit_tracker.py` — Entire File Unused

**Status**: Never imported anywhere in the codebase  
**Lines**: 1-132 (entire file)  

This is a standalone demo script that imports from `rate_limit_tracker` using a **relative import** (`from rate_limit_tracker import ...`) which would fail if run as part of the app package anyway. No other file imports this module.

```python
# Line 8-12: Broken relative import (not app.core.rate_limit_tracker)
from rate_limit_tracker import (
    parse_rate_limit_headers,
    format_rate_limit_display,
    format_rate_limit_compact,
)
```

> **Action**: Delete this file entirely.

---

### H2. `app/core/streaming/example_usage.py` — Demo File in Production Code

**Status**: Never imported anywhere in the codebase  
**Lines**: 1-384 (entire file)  

Contains 4 example classes/functions (`MetricsCallback`, `WebSocketCallback`, `CompositeCallback`, example runners) that demonstrate streaming callback usage but are never imported or used in production code. The streaming module itself (`callbacks.py`) is used via `react.py`'s own `StreamCallback` Protocol — not these examples.

> **Action**: Move to `docs/examples/` or delete.

---

### H3. `app/core/memory/example_usage.py` — Demo File in Production Code

**Status**: Never imported anywhere in the codebase  
**Lines**: 1-82 (entire file)  

Demonstrates `MemoryManager` + `BuiltinMemoryProvider` usage. No production code imports it. Self-referential only.

> **Action**: Move to `docs/examples/` or delete.

---

### H4. `app/core/prompt_caching.py` — Entire Module Never Imported

**Status**: Zero imports across the entire codebase  
**Lines**: 1-232 (entire file)  

Despite being fully implemented with 5 public functions (`apply_anthropic_cache_control`, `count_cache_markers`, `has_cache_markers`, `remove_cache_markers`, `_build_cache_control`), this module is **never imported or used** by any file in the codebase. Not by `react.py`, not by any endpoint, not by any service.

The `app/config.py` does define a `prompt_cache_ttl` setting (line 86-89), but nothing reads it beyond the Settings class itself.

```python
# config.py line 86-89: Setting exists but is never consumed
prompt_cache_ttl: str = Field(
    default="5m",
    description="TTL for Anthropic prompt caching (5m or 1h)"
)
```

> **Action**: Either integrate into the LLM call chain in `react.py` or delete the module + config setting.

---

### H5. Test Files Inside `app/core/` — Should Be in `tests/`

**Status**: Test files mixed with production code  

The following test files exist inside the production `app/core/` directory:

| File | Lines | Size |
|------|-------|------|
| `app/core/test_context_compression.py` | ~400 | 14.7KB |
| `app/core/test_context_references.py` | ~700 | 24.9KB |
| `app/core/test_context_references_integration.py` | ~300 | 11KB |
| `app/core/test_rate_limit_tracker.py` | ~500 | 18.3KB |
| `app/core/memory/test_builtin.py` | ~300 | 11.5KB |
| `app/core/memory/test_manager.py` | ~500 | 18.7KB |
| `app/core/streaming/test_callbacks.py` | ~400 | 13.9KB |

These are co-located with production code instead of being in the proper `tests/` directory. This means:
- They get deployed to production Docker images unnecessarily
- They add ~113KB of test code to the production package
- They violate standard Python project layout conventions

> **Action**: Move all `test_*.py` files to `agent-api/tests/core/` (and corresponding subdirectories).

---

### H6. `app/core/__init__.py` — Empty `__all__` Export

**Lines**: 1-4  

```python
"""Core package - Business logic and services"""
__all__ = []
```

Exports nothing despite the package containing 20+ modules. Since all imports are done via full qualified paths (`from app.core.retry import ...`), the `__all__` is technically accurate but misleading. Either populate it or remove it.

> **Action**: Remove `__all__ = []` since it serves no purpose.

---

### H7. `app/core/streaming/` — `StreamCallback` Protocol Duplicated

The `app/core/streaming/callbacks.py` defines `StreamCallback` as a Protocol class. However, `app/workflow/strategies/react.py` (line 162) defines its **own** `StreamCallback` Protocol. The streaming module's exports (`StreamCallback`, `LoggingStreamCallback`, `build_tool_preview`) are **never imported** in `react.py` or any workflow code. The workflow redefines everything it needs.

This means `app/core/streaming/callbacks.py`'s `StreamCallback` and `LoggingStreamCallback` are effectively dead code — only used by example_usage.py (also dead) and test_callbacks.py.

> **Action**: Consolidate — either have `react.py` use `app.core.streaming.StreamCallback` or delete the streaming module's Protocol since react.py defines its own.

---

## 🟡 MEDIUM Severity — Unused Imports & Optimizations

### M1. `app/core/dependencies.py` — Unused `lru_cache` Import

**Line 2:**
```python
from functools import lru_cache
```

`lru_cache` is imported but never used anywhere in the file. No decorator or call references it.

> **Action**: Remove the import.

---

### M2. `app/core/executor.py` — Unused `subprocess` Import

**Line 3:**
```python
import subprocess
```

`subprocess` is imported but never used. The shell execution uses `asyncio.create_subprocess_shell` instead. `traceback` (line 6) and `io` (line 5) are used.

> **Action**: Remove `import subprocess`.

---

### M3. `app/core/error_classifier.py` — Unused `asyncio` Import

**Line 8:**
```python
import asyncio
```

`asyncio` is imported but never used anywhere in the 1073-line file. All functions are synchronous. There are no `asyncio.*` calls or `async def` functions.

> **Action**: Remove `import asyncio`.

---

### M4. `app/core/tool_registry.py` — Unused `Protocol` Import

**Line 8:**
```python
from typing import Any, Callable, Dict, List, Optional, Protocol
```

`Protocol` is imported but never used in the file. No Protocol class is defined.

> **Action**: Remove `Protocol` from the import.

---

### M5. `app/core/logging.py` — `sanitize_extra()` Never Called

**Lines 21-44:**

The function `sanitize_extra()` is defined but **never called** anywhere in the codebase. It's meant to sanitize extra dicts for LogRecord safety, but no caller uses it.

```python
def sanitize_extra(extra: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize extra dict to avoid conflicts with LogRecord attributes."""
    ...
```

> **Action**: Either integrate into the logging pipeline or remove.

---

### M6. `app/core/redact.py` — `redact_dict()` Never Imported

**Lines 43-47:**

`redact_dict()` is defined in `redact.py` but **never imported** by any file. Only `redact()` is used (via `logging_filter.py`). The `_redact_dict_inplace()` helper (line 57) is also only used by `redact_dict()`.

> **Action**: Remove if truly unused, or document as a public API for external callers.

---

### M7. `app/core/context_compression.py` — Empty `TYPE_CHECKING` Block

**Lines 29-30:**
```python
if TYPE_CHECKING:
    pass
```

The `TYPE_CHECKING` guard imports nothing. This is dead code.

> **Action**: Remove the `TYPE_CHECKING` import from line 20 and the empty block at lines 29-30.

---

### M8. `app/core/context_compression.py` — `update_from_response()` is a No-Op

**Lines 946-954:**
```python
def update_from_response(self, usage: Dict[str, Any]) -> None:
    """Update tracked token usage from API response."""
    # This can be used to track actual token usage from API responses
    # For now, we rely on estimates
    pass
```

This method does nothing — it's a stub with just `pass`. Search shows it's never called either.

> **Action**: Remove or implement.

---

### M9. `app/core/context_compression.py` — `_merge_summaries()` is a Pass-Through

**Lines 863-889:**
```python
def _merge_summaries(self, previous: str, new: str) -> str:
    """Merge a new summary with a previous summary iteratively."""
    # Simple merge strategy: ...
    # For now, return the new summary which should already be iterative
    return new
```

This method simply returns `new` without merging. The comment acknowledges it should be enhanced.

> **Action**: Either implement proper merging or simplify the caller to not call this at all.

---

### M10. `app/schemas/__init__.py` — Empty Package

**Lines 1-4:**
```python
"""Schemas package - API request/response models"""
__all__ = []
```

The entire `schemas/` package contains only this empty `__init__.py`. No schema modules exist. The Pydantic models are in `app/models/workflow.py` and `app/models/db_models.py` instead.

> **Action**: Delete the `app/schemas/` directory or move models there to match the naming.

---

### M11. `app/core/error_classifier.py` — Duplicate Pattern Entries

**Lines 163-168:**
```python
_CONTEXT_OVERFLOW_PATTERNS = [
    ...
    "truncating input",   # Line 158
    ...
    # Ollama patterns
    "truncating input",   # Line 164 — DUPLICATE
    # llama.cpp / llama-server patterns
    "slot context",       # Line 166 — DUPLICATE of line 159
    "n_ctx_slot",         # Line 167 — DUPLICATE of line 160
]
```

Three patterns appear twice in `_CONTEXT_OVERFLOW_PATTERNS`:
- `"truncating input"` (lines 158 and 164)
- `"slot context"` (lines 159 and 166)
- `"n_ctx_slot"` (lines 160 and 167)

> **Action**: Remove duplicate entries.

---

### M12. `app/core/error_classifier.py` — Massive Code Duplication

The `_classify_openai_error()` and `_classify_anthropic_error()` functions share ~80% identical code (billing disambiguation, rate limit detection, context overflow classification, 429/500/502/503 handling). Each is ~250 lines. This is a maintenance burden.

> **Action**: Extract shared classification logic into a common function, with provider-specific overrides.

---

### M13. `app/core/executor.py` — `shell=True` is Redundant

**Line 70:**
```python
process = await asyncio.create_subprocess_shell(
    task.command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    shell=True  # Redundant - create_subprocess_shell already implies shell=True
)
```

`create_subprocess_shell` already uses the shell. The explicit `shell=True` kwarg is unnecessary (and ignored for `create_subprocess_shell`).

> **Action**: Remove `shell=True`.

---

### M14. `app/models/__init__.py` — Empty Module

**Lines 1-2:**
```python
"""Pydantic models for data validation."""
```

The `__init__.py` exports nothing. Models are accessed via `app.models.workflow` and `app.models.db_models` directly.

> **Action**: Either add exports or leave as-is (acceptable for namespace packages).

---

## 🔵 LOW Severity — Style & Minor

### L1. `app/core/context_compression.py` — Unused `field` Import

**Line 19:**
```python
from dataclasses import dataclass, field
```

`field` is imported but only `dataclass` is used. The `CompressionResult` dataclass uses `Optional` with `None` defaults, not `field()`.

> **Action**: Remove `field` from import.

---

### L2. `app/core/context_references.py` — `json` Import Unused

**Line 10:**
```python
import json
```

`json` is imported but never used in the file. All serialization is handled by string formatting.

> **Action**: Remove `import json`.

---

### L3. `app/core/context_references.py` — `mimetypes` Import at Module Level

**Line 11:**
```python
import mimetypes
```

Used only in `_is_binary_file()` (line 555). Fine functionally, but lazy import would reduce startup cost.

> **Action**: Optional — move to function scope if startup time matters.

---

### L4. `app/core/model_metadata.py` — `Set` Not Used in Imports

**Line 21:**
```python
from typing import Any, Dict, List, Optional, TYPE_CHECKING
```

`TYPE_CHECKING` is used but only for `AsyncSession`. This is correct usage.

No issues here — false alarm during initial scan.

---

### L5. `app/core/scheduler.py` — Unused `List` Import

**Line 6:**
```python
from typing import Dict, Optional, Set, List
```

`List` is imported but the only list usage is the return from `get_scheduled_jobs()` which returns `list` (builtin), not `List` (typing).

> **Action**: Remove `List` from import.

---

### L6. `app/core/database.py` — `create_engine` (Sync) Import Unused

**Line 3:**
```python
from sqlalchemy import create_engine
```

Only the async engine (`create_async_engine`) is used. The sync `create_engine` is never called.

> **Action**: Remove `from sqlalchemy import create_engine`.

---

### L7. `app/core/database.py` — `text` Import Used Once

**Line 6:**
```python
from sqlalchemy import text
```

Used once in `init_db()` for `CREATE EXTENSION`. Fine, but could be a lazy import.

> **Action**: Optional — move to `init_db()` function scope.

---

### L8. `app/core/context_compression.py` — `Set` in TYPE_CHECKING

**Line 20:**
```python
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING
```

`Set` is imported but never used in the file. Only `List`, `Dict`, `Optional`, `Any` are used.

> **Action**: Remove `Set` from import.

---

## Cross-Reference Analysis

### Files Never Imported by Anything Else

| File | Status | Verdict |
|------|--------|---------|
| `app/core/demo_rate_limit_tracker.py` | ❌ Never imported | **DELETE** |
| `app/core/streaming/example_usage.py` | ❌ Never imported | **DELETE** |
| `app/core/memory/example_usage.py` | ❌ Never imported | **DELETE** |
| `app/core/prompt_caching.py` | ❌ Never imported | **DELETE or integrate** |
| `app/core/test_*.py` (all 4 files) | ❌ Only self-referential | **MOVE to tests/** |
| `app/core/memory/test_*.py` (2 files) | ❌ Only self-referential | **MOVE to tests/** |
| `app/core/streaming/test_callbacks.py` | ❌ Only self-referential | **MOVE to tests/** |
| `app/scripts/migrate_mcp_config.py` | ⚠️ CLI tool (run directly) | Keep (migration utility) |
| `app/scripts/migrate_schedules.py` | ⚠️ CLI tool (run directly) | Keep (migration utility) |

### `__init__.py` Export vs Usage Analysis

| Package | Exports | Actually Used Externally? |
|---------|---------|--------------------------|
| `app/core/__init__.py` | `[]` (nothing) | N/A — all imports are qualified |
| `app/core/memory/__init__.py` | `MemoryManager`, `MemoryProvider`, `BuiltinMemoryProvider` | ✅ Used in `react.py` (via direct `from app.core.memory.manager`), but also by `example_usage.py` (dead) |
| `app/core/streaming/__init__.py` | `StreamCallback`, `LoggingStreamCallback`, `build_tool_preview` | ❌ **Never imported** — `react.py` defines its own `StreamCallback` |
| `app/core/skills/__init__.py` | `Skill`, `SkillManager` | ✅ Used via `from app.core.skills.manager import SkillManager` |
| `app/repositories/__init__.py` | `BaseRepository`, `WorkflowRepository`, `ExecutionRepository`, `MCPConfigRepository`, `db_repository`, `DatabaseRepository` | ✅ All used |
| `app/schemas/__init__.py` | `[]` (nothing) | Empty package — no schemas exist |
| `app/models/__init__.py` | Nothing | Models accessed via `app.models.workflow` directly |

---

## Impact Estimate

If all HIGH findings are addressed:

| Metric | Before | After |
|--------|--------|-------|
| Files in `app/` | ~67 .py files | ~54 .py files |
| Dead code removed | 0 | ~1,800 lines |
| Test code in production | ~113KB | 0 |
| Orphan demo files | 3 | 0 |
| Unused module (prompt_caching) | 232 lines | 0 |

---

## Recommended Priority Order

1. **Delete** `demo_rate_limit_tracker.py` — completely broken and unused
2. **Delete/move** `example_usage.py` files (2 files) — demo code in production
3. **Move** all 7 `test_*.py` files from `app/core/` to `tests/`
4. **Integrate or delete** `prompt_caching.py` — 232 lines of dead code
5. **Remove** duplicate `StreamCallback` definitions — consolidate streaming
6. **Clean** unused imports across 6+ files
7. **Remove** duplicate patterns in `error_classifier.py`
8. **Refactor** error classifier to reduce code duplication (~500 duplicated lines)
