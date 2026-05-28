"""Per-turn tool catalog router (plan §2.2).

Problem: ``ToolRegistry.get_all_schemas`` sends every available tool on
every LLM turn. With MCP servers loaded the catalog is 20-30 K tokens —
paid on every request even when only 2-3 tools are relevant.

Solution: rank tools by relevance against the current task/message and
return the top-K. The ranker here is intentionally *embedding-free* —
it uses TF-IDF-style keyword overlap so it works in air-gapped /
no-bedrock environments. A future PR can swap in cosine similarity over
Titan embeddings; the public ``ToolRouter`` API will not change.

Accuracy guard: a configurable "pinned" set (final-answer, ask-user,
escalate, ...) is always included regardless of score. The router never
*excludes* a pinned tool — it only prunes optional ones.

Usage::

    router = ToolRouter(pinned={"final_answer", "ask_user"}, top_k=12)
    schemas = router.filter(all_schemas, query=task_description + last_user_msg)
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# Crude tokenizer good enough for keyword ranking — fast, no deps.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}")


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def schema_token_estimate(schema: Dict[str, Any]) -> int:
    """Rough JSON-character based token estimate for a single tool schema.

    Used for logging "we cut the catalog from X to Y tokens" so the
    saving is visible in metrics. Mirrors the char/4 heuristic so it
    agrees with what the model actually sees.
    """
    import json
    try:
        return max(1, len(json.dumps(schema)) // 4)
    except Exception:  # noqa: BLE001
        return 0


def _schema_text(schema: Dict[str, Any]) -> str:
    """Flatten the name + description fields of a tool schema for ranking."""
    parts: List[str] = []
    name = schema.get("name") or (schema.get("function") or {}).get("name")
    if name:
        parts.append(str(name))
    desc = schema.get("description") or (schema.get("function") or {}).get("description")
    if desc:
        parts.append(str(desc))
    # Parameter property names are signal too (e.g. "log_group", "customer_id").
    params = schema.get("parameters") or (schema.get("function") or {}).get("parameters") or {}
    props = params.get("properties") if isinstance(params, dict) else None
    if isinstance(props, dict):
        parts.extend(props.keys())
    return " ".join(parts)


@dataclass
class _Ranked:
    schema: Dict[str, Any]
    name: str
    score: float
    pinned: bool


def rank_tools(
    schemas: Sequence[Dict[str, Any]],
    query: str,
    *,
    pinned: Optional[Set[str]] = None,
) -> List[Tuple[Dict[str, Any], float]]:
    """Return [(schema, score), ...] sorted by descending relevance.

    Scoring: log-tf weighted keyword overlap between query tokens and the
    flattened schema text. Pinned tools always score ``+inf`` and sort
    first.
    """
    pinned = pinned or set()
    q_tokens = _tokens(query)
    if not q_tokens:
        # Without a query, preserve the registry's original order.
        return [(s, 0.0) for s in schemas]

    # Document frequency for IDF (within the catalog, cheap).
    df: Dict[str, int] = {}
    docs: List[List[str]] = []
    for s in schemas:
        toks = _tokens(_schema_text(s))
        docs.append(toks)
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n_docs = max(1, len(schemas))

    ranked: List[_Ranked] = []
    for s, toks in zip(schemas, docs):
        name = (
            s.get("name")
            or (s.get("function") or {}).get("name")
            or "<anonymous>"
        )
        if name in pinned:
            ranked.append(_Ranked(s, name, score=math.inf, pinned=True))
            continue
        if not toks:
            ranked.append(_Ranked(s, name, score=0.0, pinned=False))
            continue
        tf: Dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for qt in q_tokens:
            if qt in tf:
                idf = math.log(1 + n_docs / (1 + df.get(qt, 0)))
                score += (1 + math.log(tf[qt])) * idf
        ranked.append(_Ranked(s, name, score, pinned=False))

    ranked.sort(key=lambda r: (-r.score, r.name))
    return [(r.schema, r.score) for r in ranked]


@dataclass
class ToolRouter:
    """Stateful router that knows the pinned set and the top-K budget."""
    pinned: Set[str] = field(default_factory=set)
    top_k: int = 12

    def filter(
        self,
        schemas: Sequence[Dict[str, Any]],
        *,
        query: str,
    ) -> List[Dict[str, Any]]:
        """Return at most ``top_k + |pinned|`` tool schemas for this turn.

        - Pinned tools are always included.
        - Remaining slots are filled with the top-K-ranked non-pinned tools.
        - If ``query`` is empty / too short, the original catalog is
          returned unchanged (fail-open: never starve the model of tools
          on the basis of weak signal).
        """
        if not query or len(query.strip()) < 3:
            return list(schemas)

        ranked = rank_tools(schemas, query, pinned=self.pinned)
        out: List[Dict[str, Any]] = []
        non_pinned_used = 0
        for schema, score in ranked:
            name = (
                schema.get("name")
                or (schema.get("function") or {}).get("name")
                or ""
            )
            if name in self.pinned or math.isinf(score):
                out.append(schema)
                continue
            if non_pinned_used < self.top_k:
                out.append(schema)
                non_pinned_used += 1

        # Telemetry — visible in logs so the saving is auditable.
        try:
            before = sum(schema_token_estimate(s) for s in schemas)
            after = sum(schema_token_estimate(s) for s in out)
            logger.info(
                "tool_router: %d -> %d tools (~%d -> ~%d tokens, query_len=%d)",
                len(schemas), len(out), before, after, len(query),
            )
        except Exception:  # noqa: BLE001
            pass
        return out


# Convenience module-level instance.
# Pinned tool names are comma-separated in TOOL_ROUTER_PINNED_NAMES env var.
# These tools always score +inf and are never pruned regardless of query.
# Example: TOOL_ROUTER_PINNED_NAMES=final_answer,ask_user,escalate
_pinned_env = os.environ.get("TOOL_ROUTER_PINNED_NAMES", "")
_default_pinned: Set[str] = {n.strip() for n in _pinned_env.split(",") if n.strip()}

default_router = ToolRouter(
    top_k=int(os.environ.get("TOOL_ROUTER_TOP_K", "12")),
    pinned=_default_pinned,
)
