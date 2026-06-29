"""Vector Memory node handler — explicit semantic recall.

Wires the previously no-op "Vector Memory" palette node to the bank-scoped
semantic memory service. At run time it recalls the top-K memories most relevant
to the user's query and emits them as a cross-node result; the agent node picks
this up (``memory_recall_type``) and injects a "Recalled Memory" block into the
agent's initial context — the same pattern the CloudWatch / Code Analyzer nodes
use. The always-on builtin KB remains a separate, implicit background recall.
"""
import logging
from typing import Any, Dict

from . import register

logger = logging.getLogger(__name__)


def _format_memories(memories: list) -> str:
    lines = []
    for i, m in enumerate(memories, 1):
        content = (m.get("content") if isinstance(m, dict) else str(m)) or ""
        content = content.strip()
        if content:
            lines.append(f"{i}. {content}")
    return "\n".join(lines)


@register("vector_memory")
async def execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Recall semantic memories for the current query.

    node params: ``topK`` (int, default 5), ``collection`` (optional repo scope).
    """
    params = node.get("params", {}) or {}
    data = node.get("data", {}) or {}

    # Resolve the query the agent will be asked (same source the agent node uses).
    user_query = (
        (context.get("inputs", {}) or {}).get("user_query")
        or context.get("user_query")
        or ""
    )
    if not user_query:
        return {"status": "skipped", "output": "No query available for memory recall."}

    try:
        top_k = int(params.get("topK") or data.get("topK") or 5)
    except (TypeError, ValueError):
        top_k = 5
    collection = (params.get("collection") or data.get("collection") or "").strip() or None

    try:
        from app.services.semantic_memory import semantic_memory
        memories = await semantic_memory.recall(user_query, repo=collection, k=top_k)
    except Exception as exc:  # noqa: BLE001 — recall must never break a workflow
        logger.warning("Vector Memory node: recall failed (%s)", exc)
        return {"status": "failed", "error": f"memory recall failed: {exc}"}

    if not memories:
        return {
            "status": "success",
            "memory_recall_type": "semantic",
            "output": "",
            "count": 0,
        }

    formatted = _format_memories(memories)
    logger.info("Vector Memory node: recalled %d memory(ies)", len(memories))
    return {
        "status": "success",
        "memory_recall_type": "semantic",
        "output": formatted,
        "count": len(memories),
    }
