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

# BM25 parameters, at the standard Lucene defaults. k1 bounds term-frequency
# saturation; b controls how strongly length normalization applies.
#
# Both terms are load-bearing here, and the previous scorer had neither:
# `(1 + log(tf)) * idf` grows without bound in tf and ignores length entirely,
# so a verbose tool whose description merely REPEATS a query word outscored the
# short, precisely-named tool that word came from. Measured on a 4-tool probe,
# the old scorer ranked a padded `describe_alarms` above `get_log_events` for
# both "get log events" and "log group name"; with k1+b it ranks below for both,
# and b is what does that work (at b=0 the decoy still wins "log group name").
#
# Tuning note — evals/accuracy/tool_selection.py: lexical (13 cases) and synonym
# (2) sit at a perfect mean rank of 1.0, synonym having improved from 1.5. The 2
# TRUE-paraphrase cases move 5 -> 7, which drags the headline mean 1.53 -> 1.71
# at unchanged hit@3 (88.2%) and hit@8 (100%). Those two share NO tokens with
# their target by construction, so their rank is decided by incidental stopword
# matches, and the suite documents them as the gap that gates the deferred
# synonym/embedding work — not something ranker tuning is meant to close. A
# sweep found configurations that score them better (b=0, or pruning query terms
# above a 0.20 document-frequency ratio), but each helps only at a cliff and
# each gives back the verbose-decoy fix, so both were rejected as overfitting to
# two noisy cases.
_BM25_K1 = 1.2
_BM25_B = 0.75


def _tokens(text: str) -> List[str]:
    """Lowercased word tokens, with snake_case and camelCase names ALSO split.

    Tool names and parameter names are the highest-signal text in a schema and
    they are overwhelmingly compound: ``get_log_events``, ``logGroupName``.
    Matching them only as whole tokens meant a natural query ("get log events")
    scored zero against the very tool it names, because "log" never equals
    "get_log_events". Both forms are emitted — the compound token is kept so an
    exact-name query still gets its full weight, and the parts are added so
    prose queries can reach it.
    """
    return _tokens_with_length(text)[0]


def _tokens_with_length(text: str) -> Tuple[List[str], int]:
    """``(_tokens(text), original_word_count)``.

    The second value is the document length BM25 normalizes by. It counts words
    as written, NOT the expanded token list: splitting emits 2-4 tokens for
    every compound name, so a schema full of ``snake_case`` parameters would
    otherwise look several times longer than a prose-heavy one of the same real
    size and be penalized for it — an artifact of the tokenizer rather than
    anything about the document.
    """
    out: List[str] = []
    raws = _WORD_RE.findall(text or "")
    for raw in raws:
        token = raw.lower()
        out.append(token)
        parts = [p for p in token.split("_") if len(p) > 1]
        if len(parts) > 1:
            out.extend(parts)
            continue
        # camelCase / PascalCase — only worth splitting when there is a
        # lowercase→uppercase boundary in the original.
        camel = [p.lower() for p in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", raw)]
        if len(camel) > 1:
            out.extend(p for p in camel if len(p) > 1)
    return out, len(raws)


def schema_token_estimate(schema: Dict[str, Any]) -> int:
    """Rough JSON-character based token estimate for a single tool schema.

    Used for logging "we cut the catalog from X to Y tokens" so the
    saving is visible in metrics. Mirrors the char/4 heuristic so it
    agrees with what the model actually sees.
    """
    import json
    from app.core.llm.token_estimate import estimate_tokens
    try:
        return estimate_tokens(json.dumps(schema))
    except Exception:  # noqa: BLE001
        return 0


_TOKEN_MEMO: Dict[Tuple[str, int, int], Tuple[List[str], int]] = {}
_TOKEN_MEMO_MAX = 2048


def _schema_tokens(schema: Dict[str, Any]) -> Tuple[List[str], int]:
    """Tokenized schema text, memoized across calls.

    ``rank_tools`` re-tokenizes the ENTIRE catalog on every call in order to
    build the document-frequency table, and the catalog is the same object for
    the whole run — so each ``search_tools`` invocation was re-flattening and
    re-splitting every tool's full description from scratch. The catalog is
    static within a run; the tokens are not worth recomputing.

    Keyed by name plus the sizes of the two variable-length inputs, so an
    enriched or edited catalog entry gets a fresh key rather than a stale hit.
    """
    name = (
        schema.get("name")
        or (schema.get("function") or {}).get("name")
        or "<anonymous>"
    )
    desc = schema.get("description") or (schema.get("function") or {}).get("description") or ""
    params = schema.get("parameters") or (schema.get("function") or {}).get("parameters") or {}
    props = params.get("properties") if isinstance(params, dict) else None
    key = (str(name), len(str(desc)), len(props) if isinstance(props, dict) else 0)

    hit = _TOKEN_MEMO.get(key)
    if hit is not None:
        return hit
    entry = _tokens_with_length(_schema_text(schema))
    if len(_TOKEN_MEMO) >= _TOKEN_MEMO_MAX:
        _TOKEN_MEMO.clear()
    _TOKEN_MEMO[key] = entry
    return entry


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

    Scoring: Okapi BM25 over the flattened schema text. Pinned tools always
    score ``+inf`` and sort first.

    The length normalization matters more here than in general retrieval: tool
    schemas differ in size by an order of magnitude, and without the ``|d|/avgdl``
    term a verbose MCP tool that merely *mentions* a query word several times in
    a long description outranked the short, precisely-named tool the user
    actually meant.
    """
    pinned = pinned or set()
    q_tokens = _tokens(query)
    if not q_tokens:
        # Without a query, preserve the registry's original order.
        return [(s, 0.0) for s in schemas]

    # Document frequency for IDF (within the catalog, cheap).
    df: Dict[str, int] = {}
    docs: List[Tuple[List[str], int]] = []
    for s in schemas:
        entry = _schema_tokens(s)
        docs.append(entry)
        for t in set(entry[0]):
            df[t] = df.get(t, 0) + 1
    n_docs = max(1, len(schemas))
    avgdl = (sum(dl for _, dl in docs) / n_docs) or 1.0

    ranked: List[_Ranked] = []
    for s, (toks, dl) in zip(schemas, docs):
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
        norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * (dl / avgdl))
        score = 0.0
        for qt in q_tokens:
            f = tf.get(qt)
            if not f:
                continue
            idf = math.log(1 + (n_docs - df.get(qt, 0) + 0.5) / (df.get(qt, 0) + 0.5))
            score += idf * (f * (_BM25_K1 + 1)) / (f + norm)
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
