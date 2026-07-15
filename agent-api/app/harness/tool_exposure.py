"""Per-assembly tool exposure window — the "≤10-15 active tools" accuracy budget.

``tool_disclosure`` decides ONCE per assembly whether to defer an entire
open-ended tool set behind ``search_tools``/``call_tool`` (a count/token
threshold gate — either everything binds directly, or everything defers).
``ToolExposureManager`` replaces that binary choice with a budget: cap how
many of the non-core tools bind directly, always to the highest-ranked
subset for the current query, and always keep the rest reachable through the
same bridge tools.

Core/family tools (CloudWatch, codegraph, DB, playbook, delegate, edit,
planning, filesystem — see ``tool_disclosure._is_core_tool``) are exempt
from the cap, exactly as they already are in ``tool_disclosure`` and
``tool_router``: an agent's core investigation primitives must always be
directly callable every turn. The budget governs the long tail — mostly
MCP-server tools — which is also where tool-count-driven selection-accuracy
loss actually comes from.

Gated by ``settings.tool_exposure_mode == "window"`` (default ``"legacy"``,
which keeps today's ``apply_tool_disclosure`` behavior byte-for-byte); see
``app.harness.tool_assembler.assemble_base_tools``.
"""
from __future__ import annotations

import os
from typing import Any, List, Optional, Tuple


def _keep_prefixes() -> Tuple[str, ...]:
    from app.harness.tool_disclosure import _DEFAULT_KEEP_PREFIXES
    env = os.environ.get("TOOL_ROUTER_ALWAYS_KEEP_PREFIXES", _DEFAULT_KEEP_PREFIXES)
    return tuple(p.strip() for p in env.split(",") if p.strip())


class ToolExposureManager:
    """Scores and windows a tool catalog against a per-run rankable budget.

    Built once per run assembly over the base-tool catalog. ``window()``
    returns: all core tools + the search_tools/call_tool bridge (built once,
    closed over the FULL non-core catalog so a tool outside the window is
    still reachable via ``call_tool``) + up to ``max_direct`` of the
    highest-ranked remaining tools for the given query text.
    """

    def __init__(self, catalog: List[Any], *, max_direct: int = 12) -> None:
        from app.harness.tool_disclosure import _is_core_tool, build_disclosure_tools

        self.max_direct = max(0, max_direct)
        keep_prefixes = _keep_prefixes()
        self._core: List[Any] = [t for t in catalog if _is_core_tool(t, keep_prefixes)]
        self._rankable: List[Any] = [t for t in catalog if t not in self._core]
        self._bridge: List[Any] = (
            build_disclosure_tools(self._rankable) if self._rankable else []
        )

    def window(
        self, query_text: str, recent_errors: Optional[List[str]] = None,
    ) -> List[Any]:
        """Return this window's bound tool list: core + bridge + top-ranked tail."""
        if not self._rankable:
            return list(self._core)

        from app.core.tools.router import rank_tools

        catalog_schemas = []
        for t in self._rankable:
            name = getattr(t, "name", "")
            if not name:
                continue
            # Same underscore-split enrichment tool_disclosure uses — the
            # ranker tokenizes on word chars and keeps underscores intact, so
            # "wit_get_work_item" never matches a query of plain words
            # without this. `name` stays exact for lookup; only ranking text
            # is enriched.
            words = name.replace("__", " ").replace("_", " ")
            desc = getattr(t, "description", "") or ""
            catalog_schemas.append({"name": name, "description": f"{words} {desc}"})

        query = " ".join(p for p in [query_text, *(recent_errors or [])] if p)
        by_name = {getattr(t, "name", ""): t for t in self._rankable}
        ranked = rank_tools(catalog_schemas, query)
        top_names = [s["name"] for s, _score in ranked[: self.max_direct]]
        top_tools = [by_name[n] for n in top_names if n in by_name]

        return list(self._core) + list(self._bridge) + top_tools

    @property
    def bridge_names(self) -> Tuple[str, ...]:
        return tuple(getattr(t, "name", "") for t in self._bridge)

    @property
    def core_count(self) -> int:
        return len(self._core)

    @property
    def rankable_count(self) -> int:
        return len(self._rankable)
