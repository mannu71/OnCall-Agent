"""Shared code-analyzer node parsing — bridges the two workflow dialects.

A "code analyzer" node ships in two schema dialects, exactly like LLM nodes
(``llm`` vs ``language_model``):

  * **Legacy ReactFlow** ``codeAnalyzer`` — repos under ``node['data']['repos']``
    as a list of ``{name, path}`` dicts.
  * **New LangflowEditor** ``code_search_tool`` — repos under
    ``node['params']['repos']`` as a comma / newline separated string of names.

Both forms must be accepted everywhere a code-analyzer node is read (auto-index
on save, runtime tool building, the executor handler), so the parsing lives here
in one neutral, low-level module that those call sites import.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

#: Node ``type`` values that represent a code-analyzer / code-search node.
#: Both are accepted everywhere a code-analyzer node is consumed.
CODE_ANALYZER_NODE_TYPES = ("codeAnalyzer", "code_search_tool")

#: Code-intelligence backend a code-analyzer node targets. Only ``codegraph``
#: remains — the native C engine driven in-process over stdio (see
#: app.workflow.tools.codegraph_tools). The tuple/default are kept for callers
#: that still reference them, and the parser tolerates the retired
#: ``code_crawler`` value on old persisted nodes (resolving it to codegraph).
CODE_ANALYZER_BACKENDS = ("codegraph",)
DEFAULT_CODE_ANALYZER_BACKEND = "codegraph"

# Repo names in the LangflowEditor string form are separated by commas,
# semicolons, or newlines.
_REPO_SPLIT_RE = re.compile(r"[,\n;]+")


def read_code_analyzer_repos(node: Dict[str, Any]) -> List[Dict[str, str]]:
    """Return ``[{name, path}, ...]`` for a code-analyzer node in either dialect.

    Deduplicates by name, preserving first-seen order. ``path`` is ``""`` for
    name-only repos (the LangflowEditor form), which resolve under
    ``REPOS_BASE_PATH`` downstream.
    """
    out: List[Dict[str, str]] = []
    seen: set[str] = set()

    def _add(name: Any, path: Any = "") -> None:
        name = (str(name) if name is not None else "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        out.append({"name": name, "path": str(path or "").strip()})

    def _ingest(raw: Any) -> None:
        if isinstance(raw, str):
            for tok in _REPO_SPLIT_RE.split(raw):
                _add(tok)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, dict):
                    _add(item.get("name") or item.get("path"), item.get("path", ""))
                else:
                    _add(item)

    # Legacy ``data.repos`` (list of dicts) and new ``params.repos`` (string).
    _ingest((node.get("data") or {}).get("repos"))
    _ingest((node.get("params") or {}).get("repos"))

    return out


def read_code_analyzer_backend(node: Dict[str, Any]) -> str:
    """Return the code-intelligence backend for a code-analyzer node.

    codegraph is now the only backend. This always returns ``"codegraph"``,
    regardless of any ``backend`` value persisted on the node (including the
    retired ``code_crawler`` / "Code Crawler" values on older workflows). Kept as
    a function so existing call sites remain valid.
    """
    return DEFAULT_CODE_ANALYZER_BACKEND
