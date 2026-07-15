"""Autonomous Curator — background job for pattern_memory and skills health maintenance.

Runs weekly (wired into app/core/scheduler.py).  Marks stale and low-confidence
patterns so they stop polluting similarity searches and RAG recall.  Also
curates the ``skills`` table — archiving unused skills and promoting high-value
ones to 'verified' status.

Pattern rules:
- Not seen in 30+ days AND curator_status == 'active'  → set 'stale'
- confidence_score < 0.3 AND occurrence_count < 3       → set 'archive'

Skill rules:
- status == 'active' AND recall_count == 0
  AND (last_used_at IS NULL OR last_used_at < 60 days ago) → set 'archived'
- status == 'active' AND success_count >= 5             → set 'verified'

A curator_runs row is written after each run.

The curator uses cheap queries — it never calls an LLM.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

from sqlalchemy import text

from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────────────────────────
_STALE_DAYS              = 30
_ARCHIVE_CONFIDENCE      = 0.3
_ARCHIVE_OCCURRENCES     = 3

# Skill curation thresholds
_SKILL_UNUSED_DAYS       = 60   # archive skills not recalled in this many days
_SKILL_VERIFY_SUCCESSES  = 5    # promote to 'verified' after this many successes

# Code-analyzer governance thresholds (Phase 1 — conservative)
_GRAPH_SNAPSHOTS_KEEP    = 10   # snapshots to keep per repo (oldest pruned beyond this)
_MEM_STALE_DAYS          = 90   # archive investigation_memory not accessed in N days
_MEM_LOW_WEIGHT          = 0.3  # archive if resolution_weight is also below this
_CLUSTER_DORMANT_DAYS    = 60   # mark incident_clusters 'monitoring' after N days quiet
_RCA_STALE_DAYS          = 180  # flag rca_history with no recurrence in N months


async def run_curator() -> Dict[str, Any]:
    """Main curator coroutine — call from the scheduler weekly.

    Returns:
        Dict with counts of patterns staled/archived, skills archived/verified,
        and the run timestamp.
    """
    logger.info("Curator: starting pattern_memory + skills + code-analyzer health pass")
    now = datetime.now(timezone.utc)
    stale_threshold    = now - timedelta(days=_STALE_DAYS)
    mem_threshold      = now - timedelta(days=_MEM_STALE_DAYS)
    cluster_threshold  = now - timedelta(days=_CLUSTER_DORMANT_DAYS)
    staled            = 0
    archived          = 0
    skills_archived   = 0
    skills_verified   = 0
    snapshots_pruned  = 0
    memory_archived   = 0
    clusters_dormant  = 0
    rca_flagged       = 0
    orphans_detected  = 0

    async with AsyncSessionLocal() as session:
        # ── Pattern: mark stale ───────────────────────────────────────────────
        stale_result = await session.execute(
            text("""
                UPDATE pattern_memory
                   SET curator_status = 'stale',
                       updated_at     = NOW()
                 WHERE curator_status = 'active'
                   AND last_seen < :threshold
            """),
            {"threshold": stale_threshold},
        )
        staled = stale_result.rowcount  # type: ignore[attr-defined]

        # ── Pattern: mark archive ─────────────────────────────────────────────
        archive_result = await session.execute(
            text("""
                UPDATE pattern_memory
                   SET curator_status = 'archive',
                       updated_at     = NOW()
                 WHERE curator_status IN ('active', 'stale')
                   AND confidence_score < :min_confidence
                   AND occurrence_count < :min_occurrences
            """),
            {
                "min_confidence":  _ARCHIVE_CONFIDENCE,
                "min_occurrences": _ARCHIVE_OCCURRENCES,
            },
        )
        archived = archive_result.rowcount  # type: ignore[attr-defined]

        # NOTE: skills are now file-based markdown (SKILL.md via SkillManager) —
        # there is no `skills` table to curate. The old DB-backed archive/verify
        # passes were removed with migration 031 (drop_skills). Markdown skills
        # are managed directly on disk (Skills page / files), not by the curator.

        # ── Code-analyzer: prune old graph snapshots (keep last N per repo) ──
        try:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS code_graph_snapshots (
                    id          SERIAL PRIMARY KEY,
                    repo_name   VARCHAR(255),
                    snapshot_at TIMESTAMPTZ DEFAULT NOW(),
                    is_full     BOOLEAN DEFAULT FALSE,
                    trigger     VARCHAR(50),
                    parent_id   INTEGER,
                    call_graph  JSONB,
                    chunk_summary JSONB,
                    added_edges   JSONB DEFAULT '[]',
                    removed_edges JSONB DEFAULT '[]',
                    modified_edges JSONB DEFAULT '[]',
                    commit_sha  VARCHAR(40),
                    tag         VARCHAR(255)
                )
            """))
            prune_result = await session.execute(text("""
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY repo_name
                               ORDER BY snapshot_at DESC
                           ) AS rn
                      FROM code_graph_snapshots
                )
                DELETE FROM code_graph_snapshots
                 WHERE id IN (SELECT id FROM ranked WHERE rn > :keep)
            """), {"keep": _GRAPH_SNAPSHOTS_KEEP})
            snapshots_pruned = prune_result.rowcount or 0
            if snapshots_pruned:
                logger.info("Curator: pruned %d old graph snapshot(s)", snapshots_pruned)
        except Exception as exc:
            logger.debug("Curator: snapshot prune skipped — %s", exc)

        # ── Code-analyzer: archive stale investigation_memory ─────────────
        try:
            await session.execute(text("""
                ALTER TABLE investigation_memory
                    ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'
            """))
            await session.execute(text("""
                ALTER TABLE investigation_memory
                    ADD COLUMN IF NOT EXISTS resolution_weight FLOAT DEFAULT 1.0
            """))
            mem_result = await session.execute(text("""
                UPDATE investigation_memory
                   SET status = 'archived',
                       updated_at = NOW()
                 WHERE status = 'active'
                   AND last_accessed < :threshold
                   AND resolution_weight < :weight
            """), {
                "threshold": mem_threshold,
                "weight":    _MEM_LOW_WEIGHT,
            })
            memory_archived = mem_result.rowcount or 0
            if memory_archived:
                logger.info("Curator: archived %d stale investigation_memory row(s)", memory_archived)
        except Exception as exc:
            logger.debug("Curator: investigation_memory archive skipped — %s", exc)

        # ── Code-analyzer: mark dormant incident_clusters ─────────────────
        try:
            cluster_result = await session.execute(text("""
                UPDATE incident_clusters
                   SET status = 'monitoring'
                 WHERE status = 'active'
                   AND last_seen < :threshold
            """), {"threshold": cluster_threshold})
            clusters_dormant = cluster_result.rowcount or 0
            if clusters_dormant:
                logger.info("Curator: marked %d incident cluster(s) as monitoring", clusters_dormant)
        except Exception as exc:
            logger.debug("Curator: incident_clusters dormancy pass skipped — %s", exc)

        # NOTE: the former RCA-integrity and orphaned-investigation_memory passes
        # were removed — they queried `code_chunks` (dropped in migration 007) plus
        # `rca_history`/`investigation_memory` (never created), so they failed every
        # run. rca_flagged/orphans_detected stay 0 to keep the run-record shape.

        # ── Semantic memory: promote frequently-recalled rows to pinned ───
        # "episode → pinned" consolidation: a finding recalled often enough
        # graduates into the always-injected pinned tier.
        try:
            from app.config import settings as _settings
            if getattr(_settings, "pinned_facts_enabled", True):
                from app.services.semantic_memory import semantic_memory
                pinned_promoted = await semantic_memory.promote_recurring_to_pinned(
                    recall_threshold=getattr(_settings, "pinned_promote_recall_threshold", 3),
                )
                if pinned_promoted:
                    logger.info("Curator: promoted %d memory row(s) to pinned", pinned_promoted)
        except Exception as exc:
            logger.debug("Curator: pinned promotion skipped — %s", exc)

        # ── Semantic memory: LLM audit / consolidation ──
        # Merge memories stating the same fact in different words. A SHA256
        # fingerprint short-circuits the LLM call when nothing changed, and a
        # hard guard refuses any pass that would delete >50% of memories.
        try:
            from app.config import settings as _settings
            if getattr(_settings, "memory_audit_enabled", False):
                from app.services.semantic_memory import semantic_memory
                audit_result = await semantic_memory.audit_and_consolidate()
                logger.info("Curator: semantic memory audit — %s", audit_result)
        except Exception as exc:
            logger.debug("Curator: semantic memory audit skipped — %s", exc)

        # ── Nullify orphaned cluster representative_rca_id ────────────────
        try:
            await session.execute(text("""
                UPDATE incident_clusters ic
                   SET representative_rca_id = NULL
                 WHERE representative_rca_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM rca_history r
                        WHERE r.id = ic.representative_rca_id
                   )
            """))
        except Exception as exc:
            logger.debug("Curator: cluster representative cleanup skipped — %s", exc)

        # ── Write curator run record ──────────────────────────────────────────
        await session.execute(
            text("""
                CREATE TABLE IF NOT EXISTS curator_runs (
                    id         SERIAL PRIMARY KEY,
                    ran_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    staled     INTEGER NOT NULL DEFAULT 0,
                    archived   INTEGER NOT NULL DEFAULT 0,
                    details    JSONB
                )
            """)
        )
        details = json.dumps({
            "stale_threshold_days":     _STALE_DAYS,
            "archive_confidence":       _ARCHIVE_CONFIDENCE,
            "skill_unused_days":        _SKILL_UNUSED_DAYS,
            "skill_verify_successes":   _SKILL_VERIFY_SUCCESSES,
            "skills_archived":          skills_archived,
            "skills_verified":          skills_verified,
            # Code-analyzer governance
            "snapshots_pruned":         snapshots_pruned,
            "memory_archived":          memory_archived,
            "clusters_dormant":         clusters_dormant,
            "rca_flagged":              rca_flagged,
            "orphans_detected":         orphans_detected,
        })
        await session.execute(
            text("""
                INSERT INTO curator_runs (ran_at, staled, archived, details)
                VALUES (:ran_at, :staled, :archived, :details::jsonb)
            """),
            {
                "ran_at":   now,
                "staled":   staled,
                "archived": archived,
                "details":  details,
            },
        )
        await session.commit()

    logger.info(
        "Curator: complete — staled=%d archived=%d skills_archived=%d skills_verified=%d "
        "snapshots_pruned=%d memory_archived=%d clusters_dormant=%d "
        "rca_flagged=%d orphans_detected=%d",
        staled, archived, skills_archived, skills_verified,
        snapshots_pruned, memory_archived, clusters_dormant,
        rca_flagged, orphans_detected,
    )
    return {
        "ran_at":           now.isoformat(),
        "staled":           staled,
        "archived":         archived,
        "skills_archived":  skills_archived,
        "skills_verified":  skills_verified,
        "snapshots_pruned": snapshots_pruned,
        "memory_archived":  memory_archived,
        "clusters_dormant": clusters_dormant,
        "rca_flagged":      rca_flagged,
        "orphans_detected": orphans_detected,
    }


async def get_last_run() -> Dict[str, Any] | None:
    """Return the most recent curator_runs row, or None if no run yet."""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                text("""
                    SELECT ran_at, staled, archived, details
                      FROM curator_runs
                     ORDER BY ran_at DESC
                     LIMIT 1
                """)
            )
            row = result.one_or_none()
        except Exception:
            return None

    if row is None:
        return None

    details = row.details or {}
    return {
        "ran_at":           row.ran_at.isoformat() if row.ran_at else None,
        "staled":           row.staled,
        "archived":         row.archived,
        "skills_archived":  details.get("skills_archived", 0),
        "skills_verified":  details.get("skills_verified", 0),
        # Code-analyzer governance (populated from Phase 1 onwards)
        "snapshots_pruned": details.get("snapshots_pruned", 0),
        "memory_archived":  details.get("memory_archived", 0),
        "clusters_dormant": details.get("clusters_dormant", 0),
        "rca_flagged":      details.get("rca_flagged", 0),
        "orphans_detected": details.get("orphans_detected", 0),
        "details":          details,
    }
