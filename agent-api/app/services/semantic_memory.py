"""Bank-scoped semantic memory service.

Learned, operational memory captured while operating the system (confirmed root
causes, symptom→fix, human-authoritative facts) — complementary to the static,
code-derived ``repo_docs`` intelligence. Recall is **Postgres full-text search**
over the active repo bank ∪ the shared global bank (or a single named bank such
as ``'kb'`` for the OKF knowledge bundle). The ``search_vector`` is weighted —
title/tags at weight A, description at B, body at D — so ``ts_rank`` boosts
title/tag matches; document-side tags act as paraphrase synonyms. No embeddings:
the Bedrock Titan vector leg was removed in favour of FTS (zero embed calls, no
AWS dependency on the recall hot path).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.config import settings
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Rank-fusion constant — dampens the contribution of lower ranks. With a single
# FTS leg this simply preserves ts_rank order; retained so the scoring shape is
# stable if a second ranking signal is ever added.
_RRF_K = 60


def _sha256(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8", errors="replace")).hexdigest()


def _normalize_repos(repo: Any) -> Optional[List[str]]:
    """Coerce a str / list / None repo selector into a list (or None for global)."""
    if repo is None:
        return None
    if isinstance(repo, str):
        return [repo] if repo else None
    repos = [str(r) for r in repo if r]
    return repos or None


def _bank_filter(repos: Optional[List[str]], bank: Optional[str] = None) -> str:
    """SQL predicate selecting the memory bank(s) to recall from.

    Default: the repo bank(s) ∪ the shared global bank. When an explicit *bank*
    is given (e.g. ``'kb'`` for the OKF knowledge bundle index), recall is scoped
    to that single bank instead — a separate corpus from repo/global learned
    facts.
    """
    if bank:
        return "bank = :bank_name"
    if repos:
        return "(bank = 'global' OR (bank = 'repo' AND repo_name = ANY(:repos)))"
    return "bank = 'global'"


class SemanticMemoryService:
    """Store and recall bank-scoped semantic memories."""

    def __init__(self) -> None:
        pass

    # ── write ────────────────────────────────────────────────────────────────

    async def remember(
        self,
        content: str,
        *,
        repo: Optional[str] = None,
        source: str = "agent",
        importance: float = 0.5,
        veracity: float = 0.5,
        bank: Optional[str] = None,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Optional[int]:
        """Store one memory; dedupe on (bank, repo, content hash).

        Returns the row id, or None when *content* is empty. Best-effort: a DB or
        embedding error is logged and swallowed (memory must never break a run).

        When *title*, *tags*, or *description* are given (the OKF ``kb`` path),
        the FTS ``search_vector`` is built **weighted** — title+tags at weight A,
        description at B, body/``content`` at the default D — so ``ts_rank`` boosts
        title/tag matches ~10×. Tags act as document-side synonyms, the paraphrase
        mechanism that lets FTS-only recall match reworded queries. Calls without
        any structured field build a single unweighted vector, byte-identical to
        the prior behaviour.
        """
        content = (content or "").strip()
        if not content:
            return None
        bank = bank or ("repo" if repo else "global")

        # Weighted-vs-plain search_vector. weight_a = title + tags (synonyms),
        # weight_b = description. The sha folds in the structured fields so a
        # frontmatter-only edit (e.g. new tags) yields a new dedup identity and
        # deterministically re-indexes.
        tags_text = " ".join(str(t).strip() for t in (tags or []) if str(t).strip())
        weight_a = " ".join(p for p in ((title or "").strip(), tags_text) if p)
        weight_b = (description or "").strip()
        structured = bool(weight_a or weight_b)
        sha = _sha256(content if not structured else f"{content}\x00{weight_a}\x00{weight_b}")

        if structured:
            search_vector_sql = (
                "setweight(to_tsvector('english', :weight_a), 'A') || "
                "setweight(to_tsvector('english', :weight_b), 'B') || "
                "to_tsvector('english', :content)"
            )
        else:
            search_vector_sql = "to_tsvector('english', :content)"

        sql = text(
            f"""
            INSERT INTO semantic_memory
              (bank, repo_name, content, content_sha256, search_vector,
               source, importance, veracity, created_at)
            VALUES
              (:bank, :repo, :content, :sha,
               {search_vector_sql}, :source, :importance, :veracity, now())
            ON CONFLICT (bank, COALESCE(repo_name, ''), content_sha256)
            DO UPDATE SET
               importance    = GREATEST(semantic_memory.importance, EXCLUDED.importance),
               veracity      = EXCLUDED.veracity,
               search_vector = EXCLUDED.search_vector,
               source        = EXCLUDED.source
            RETURNING id
            """
        )
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(sql, {
                    "bank": bank,
                    "repo": repo,
                    "content": content,
                    "sha": sha,
                    "weight_a": weight_a,
                    "weight_b": weight_b,
                    "source": source,
                    "importance": float(importance),
                    "veracity": float(veracity),
                })
                row_id = result.scalar_one_or_none()
                await session.commit()
                return int(row_id) if row_id is not None else None
        except Exception as e:  # noqa: BLE001 — never propagate
            logger.warning("semantic_memory: remember failed (%s)", e)
            return None

    # ── read ─────────────────────────────────────────────────────────────────

    async def recall(
        self,
        query: str,
        *,
        repo: Optional[str] = None,
        k: Optional[int] = None,
        bank: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return up to *k* memories most relevant to *query* (Postgres FTS).

        Searches the repo bank ∪ global bank, or — when *bank* is given — that
        single bank (e.g. ``'kb'`` for the OKF knowledge bundle). Ranking is
        ``ts_rank`` over the weighted ``search_vector`` (title/tags weight A);
        document-side tags act as paraphrase synonyms. Results are token-bounded
        by the caller (the provider truncates each ``content``).
        """
        query = (query or "").strip()
        if not query:
            return []
        k = k or settings.memory_recall_k
        per_leg = max(k * 3, k)
        repos = _normalize_repos(repo)

        rows_by_id: Dict[int, Dict[str, Any]] = {}
        rrf: Dict[int, float] = {}

        try:
            async with AsyncSessionLocal() as session:
                await self._fts_leg(session, query, repos, per_leg, rows_by_id, rrf, bank)
        except Exception as e:  # noqa: BLE001 — recall must never break a run
            logger.warning("semantic_memory: recall failed (%s)", e)
            return []

        ranked = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:k]
        results: List[Dict[str, Any]] = []
        for row_id, score in ranked:
            item = rows_by_id[row_id]
            item["score"] = round(score, 6)
            results.append(item)

        # Best-effort recall bookkeeping (decay/importance inputs).
        if results:
            await self._bump_recall([r["id"] for r in results])
        return results

    async def _fts_leg(self, session, query, repos, limit, rows_by_id, rrf, bank=None) -> None:
        # OR-semantics: plainto_tsquery ANDs every term, so a query with any word
        # absent from a memory (e.g. an extra 'traffic') matches nothing. Recall
        # should surface on PARTIAL overlap, ranked by how many terms match — so
        # rewrite the '&'-joined query to '|'-joined. ts_rank still favours rows
        # that match more terms.
        ts = "replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery"
        sql = text(
            f"""
            SELECT id, content, source, importance, veracity,
                   ts_rank(search_vector, {ts}) AS score
            FROM semantic_memory
            WHERE {_bank_filter(repos, bank)}
              AND search_vector @@ {ts}
            ORDER BY score DESC
            LIMIT :limit
            """
        )
        params = {"q": query, "limit": limit}
        if bank:
            params["bank_name"] = bank
        elif repos:
            params["repos"] = repos
        result = await session.execute(sql, params)
        for rank, row in enumerate(result, start=1):
            rows_by_id.setdefault(row.id, _row_dict(row))
            rrf[row.id] = rrf.get(row.id, 0.0) + 1.0 / (_RRF_K + rank)

    async def _bump_recall(self, ids: List[int]) -> None:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(
                        "UPDATE semantic_memory "
                        "SET recall_count = recall_count + 1, last_recalled_at = now() "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
                await session.commit()
        except Exception as e:  # noqa: BLE001 — bookkeeping is best-effort
            logger.debug("semantic_memory: recall bump failed (%s)", e)

    # ── pinned facts (always-injected tier) ────────────────────────────────────

    async def pin_fact(
        self,
        content: str,
        *,
        repo: Optional[str] = None,
        source: str = "manual",
        importance: float = 0.9,
    ) -> Optional[int]:
        """Store a fact in the always-injected ``pinned`` bank.

        Pinned facts are recalled every turn regardless of query similarity
        (subject to the per-turn token budget), so they default to high
        importance. Repo-scoped when *repo* is given, else globally pinned.
        """
        return await self.remember(
            content,
            repo=repo,
            source=source,
            importance=importance,
            veracity=1.0,
            bank="pinned",
        )

    async def list_pinned(
        self,
        *,
        repo: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return pinned facts for the active repo(s) ∪ globally-pinned facts.

        NOT similarity-gated — every pinned fact is eligible. Ordered by
        importance·veracity so the token budget keeps the most important first.
        """
        repos = _normalize_repos(repo)
        where = (
            "(repo_name = ANY(:repos) OR repo_name IS NULL)" if repos else "repo_name IS NULL"
        )
        sql = text(
            f"""
            SELECT id, content, source, importance, veracity
            FROM semantic_memory
            WHERE bank = 'pinned' AND {where}
            ORDER BY (importance * veracity) DESC, created_at DESC
            LIMIT :limit
            """
        )
        params: Dict[str, Any] = {"limit": limit}
        if repos:
            params["repos"] = repos
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(sql, params)
                return [_row_dict(row) for row in result]
        except Exception as e:  # noqa: BLE001 — recall must never break a run
            logger.warning("semantic_memory: list_pinned failed (%s)", e)
            return []

    async def promote_recurring_to_pinned(self, *, recall_threshold: int) -> int:
        """Promote frequently-recalled repo/global rows into the pinned bank.

        The "episode → pinned" consolidation, run by the curator. A row
        recalled at least *recall_threshold* times is copied into ``bank='pinned'``
        (same repo scope) so it becomes always-injected. Idempotent via the dedup
        index. Returns the number promoted.
        """
        sql = text(
            """
            INSERT INTO semantic_memory
              (bank, repo_name, content, content_sha256, search_vector,
               source, importance, veracity, created_at)
            SELECT 'pinned', repo_name, content, content_sha256,
                   search_vector, source, GREATEST(importance, 0.8), veracity, now()
            FROM semantic_memory
            WHERE bank IN ('repo', 'global') AND recall_count >= :threshold
            ON CONFLICT (bank, COALESCE(repo_name, ''), content_sha256) DO NOTHING
            RETURNING id
            """
        )
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(sql, {"threshold": int(recall_threshold)})
                promoted = len(result.fetchall())
                await session.commit()
                return promoted
        except Exception as e:  # noqa: BLE001
            logger.warning("semantic_memory: promote_to_pinned failed (%s)", e)
            return 0

    # ── maintenance ────────────────────────────────────────────────────────────

    async def consolidate(self, *, max_rows_per_bank: int = 5000) -> int:
        """Decay/prune: drop the lowest-value rows when a bank grows unbounded.

        A ``sleep``-style decay. Keeps the highest importance·veracity, most
        recently/ frequently recalled rows. Returns the number deleted.
        """
        sql = text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                         PARTITION BY bank, COALESCE(repo_name, '')
                         ORDER BY (importance * veracity) DESC,
                                  recall_count DESC,
                                  COALESCE(last_recalled_at, created_at) DESC
                       ) AS rn
                FROM semantic_memory
            )
            DELETE FROM semantic_memory
            WHERE id IN (SELECT id FROM ranked WHERE rn > :keep)
            RETURNING id
            """
        )
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(sql, {"keep": max_rows_per_bank})
                deleted = len(result.fetchall())
                await session.commit()
                return deleted
        except Exception as e:  # noqa: BLE001
            logger.warning("semantic_memory: consolidate failed (%s)", e)
            return 0

    async def audit_and_consolidate(
        self,
        *,
        llm_fn: Optional[Any] = None,
        max_delete_fraction: Optional[float] = None,
    ) -> Dict[str, Any]:
        """LLM audit that merges memories stating the same fact.

        Cost & safety controls:
          * **Fingerprint short-circuit** — if the store is unchanged since the
            last audit, skip the LLM call entirely.
          * **Mass-deletion guard** — refuse to apply a plan that would delete
            more than ``max_delete_fraction`` of memories.

        Returns a small result dict (``skipped``/``reason``/``deleted``).
        """
        if max_delete_fraction is None:
            max_delete_fraction = settings.memory_audit_max_delete_fraction

        rows = await self._load_auditable_rows()
        if len(rows) < 2:
            return {"skipped": True, "reason": "too_few", "count": len(rows)}

        fingerprint = _audit_fingerprint(rows)
        if await self._get_audit_fingerprint() == fingerprint:
            return {"skipped": True, "reason": "unchanged", "count": len(rows)}

        groups = await self._llm_merge_groups(rows, llm_fn)
        to_delete, ok, reason = _plan_deletions(rows, groups, max_delete_fraction)

        if not ok:
            logger.warning(
                "semantic_memory: audit deletion guard tripped "
                "(would delete %d/%d) — skipping", len(to_delete), len(rows),
            )
            # Persist the fingerprint so we don't re-run the same unsafe plan.
            await self._set_audit_fingerprint(fingerprint)
            return {"skipped": True, "reason": reason, "would_delete": len(to_delete)}

        deleted = await self._delete_ids(to_delete) if to_delete else 0
        remaining = [r for r in rows if r["id"] not in set(to_delete)]
        await self._set_audit_fingerprint(_audit_fingerprint(remaining))
        return {"skipped": False, "merged": deleted, "deleted": deleted, "count": len(rows)}

    async def _load_auditable_rows(self) -> List[Dict[str, Any]]:
        """Load non-pinned memory rows (repo + global banks) for audit."""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(text(
                    "SELECT id, content, source FROM semantic_memory "
                    "WHERE bank IN ('repo', 'global') ORDER BY id"
                ))
                return [{"id": r.id, "content": r.content, "source": r.source} for r in result]
        except Exception as e:  # noqa: BLE001
            logger.warning("semantic_memory: audit load failed (%s)", e)
            return []

    async def _llm_merge_groups(
        self, rows: List[Dict[str, Any]], llm_fn: Optional[Any]
    ) -> List[List[int]]:
        listing = "\n".join(f"{r['id']}: {r['content']}" for r in rows)
        prompt = f"{_AUDIT_SYSTEM}\n\nMemories:\n{listing[:12000]}"
        try:
            if llm_fn is not None:
                raw = await llm_fn(prompt)
            else:
                from app.core.llm.call_llm import call_llm
                raw, _, _, _ = await call_llm(prompt, tier="search", use_cache=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("semantic_memory: audit LLM failed (%s)", e)
            return []
        return _parse_groups(raw)

    async def _delete_ids(self, ids: List[int]) -> int:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("DELETE FROM semantic_memory WHERE id = ANY(:ids) RETURNING id"),
                    {"ids": ids},
                )
                deleted = len(result.fetchall())
                await session.commit()
                return deleted
        except Exception as e:  # noqa: BLE001
            logger.warning("semantic_memory: audit delete failed (%s)", e)
            return 0

    async def _get_audit_fingerprint(self) -> Optional[str]:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("SELECT value FROM app_settings WHERE key = :k"),
                    {"k": _AUDIT_FINGERPRINT_KEY},
                )
                row = result.first()
                return row[0] if row else None
        except Exception:  # noqa: BLE001 — treat as no prior fingerprint
            return None

    async def _set_audit_fingerprint(self, fingerprint: str) -> None:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(
                        "INSERT INTO app_settings (key, value, updated_at) "
                        "VALUES (:k, :v, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
                    ),
                    {"k": _AUDIT_FINGERPRINT_KEY, "v": fingerprint},
                )
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("semantic_memory: audit fingerprint save failed (%s)", e)


# ── audit / consolidation ─────────────────────────────────────────────────────

_AUDIT_FINGERPRINT_KEY = "semantic_memory_audit_fingerprint"

_AUDIT_SYSTEM = """\
You are de-duplicating a memory store. Below is a numbered list of memories.
Identify groups of memories that state the SAME fact in different words.

Return ONLY a JSON array of groups; each group is an array of the integer ids
that should be merged, e.g. [[3, 7], [12, 19, 20]].

Rules:
- MERGE only entries that state the SAME fact in different words.
- When in doubt, KEEP entries separate (omit them).
- Never put an id in more than one group.
"""


def _audit_fingerprint(rows: List[Dict[str, Any]]) -> str:
    """Stable SHA256 over (id, content, source) — short-circuits unchanged audits."""
    parts = sorted(f"{r['id']}:{r.get('content', '')}:{r.get('source', '')}" for r in rows)
    return hashlib.sha256("\n".join(parts).encode("utf-8", "replace")).hexdigest()


def _parse_groups(raw: str) -> List[List[int]]:
    """Parse the audit LLM's JSON array-of-arrays, tolerating fences/prose."""
    if not raw:
        return []
    text_val = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    text_val = re.sub(r"\s*```$", "", text_val.strip(), flags=re.MULTILINE)
    match = re.search(r"\[.*\]", text_val, flags=re.DOTALL)
    if match:
        text_val = match.group(0)
    try:
        data = json.loads(text_val)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    groups: List[List[int]] = []
    for group in data:
        if isinstance(group, list):
            ids = [int(x) for x in group if isinstance(x, (int, float, str)) and str(x).strip().lstrip("-").isdigit()]
            if ids:
                groups.append(ids)
    return groups


def _plan_deletions(
    rows: List[Dict[str, Any]],
    groups: List[List[int]],
    max_delete_fraction: float,
) -> Tuple[List[int], bool, str]:
    """From merge *groups*, decide which ids to delete (keep the lowest id each).

    Returns ``(to_delete, ok, reason)``. ``ok`` is False — and no deletion should
    be applied — when the plan would remove more than ``max_delete_fraction`` of
    rows (the mass-deletion safety guard).
    """
    valid_ids = {r["id"] for r in rows}
    to_delete: List[int] = []
    seen: set = set()
    for group in groups or []:
        ids = [int(i) for i in group if int(i) in valid_ids and int(i) not in seen]
        seen.update(ids)
        if len(ids) < 2:
            continue
        # Keep the lowest id (oldest), delete the rest.
        for victim in sorted(ids)[1:]:
            to_delete.append(victim)
    if not to_delete:
        return [], True, "noop"
    if len(to_delete) > max_delete_fraction * len(rows):
        return to_delete, False, "delete_guard"
    return to_delete, True, "ok"


def format_recall_block(results: List[Dict[str, Any]], max_chars: Optional[int] = None) -> str:
    """Render recall results as a compact, token-bounded context block.

    Each memory is truncated to ``max_chars`` (default ``settings.memory_max_chars``)
    so injected context stays small — the per-turn token guardrail.
    """
    if not results:
        return ""
    cap = settings.memory_max_chars if max_chars is None else max_chars
    lines = ["## Learned memory (from past investigations)"]
    for r in results:
        snippet = (r.get("content") or "").strip().replace("\n", " ")
        if cap and len(snippet) > cap:
            snippet = snippet[:cap].rstrip() + "…"
        src = r.get("source") or "agent"
        lines.append(f"- ({src}) {snippet}")
    return "\n".join(lines)


def format_pinned_block(results: List[Dict[str, Any]], max_tokens: Optional[int] = None) -> str:
    """Render pinned facts as an always-injected block, bounded by a token budget.

    Token estimate is the chars/4 heuristic used elsewhere. Facts are emitted in
    priority order (the caller sorts by importance) and dropped once the budget
    is reached, so the per-turn discipline holds.
    """
    if not results:
        return ""
    budget = settings.pinned_facts_max_tokens if max_tokens is None else max_tokens
    lines = ["## Pinned facts (always apply)"]
    used = len(lines[0]) // 4
    for r in results:
        fact = (r.get("content") or "").strip().replace("\n", " ")
        if not fact:
            continue
        line = f"- {fact}"
        cost = len(line) // 4 + 1
        if budget and used + cost > budget:
            break
        lines.append(line)
        used += cost
    return "\n".join(lines) if len(lines) > 1 else ""


def _row_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "content": row.content,
        "source": row.source,
        "importance": float(row.importance) if row.importance is not None else 0.5,
        "veracity": float(row.veracity) if row.veracity is not None else 0.5,
    }


# Module-level singleton (mirrors ``knowledge_base.knowledge_base``).
semantic_memory = SemanticMemoryService()
