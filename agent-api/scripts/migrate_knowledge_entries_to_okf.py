"""One-off: migrate legacy ``knowledge_entries`` rows into the OKF bundle.

Reads every row from the (soon-to-be-dropped) ``knowledge_entries`` table and
writes it as an OKF ``known-issues/<slug>.md`` doc, then indexes it into the
``kb`` memory bank so it is recalled by the FTS path. Idempotent — re-running
overwrites the same slug rather than duplicating.

Run inside the agent-api container BEFORE migration 002 drops the table::

    docker exec -e PYTHONPATH=/app kyc-agent-api \
        python -m scripts.migrate_knowledge_entries_to_okf
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate_ke_to_okf")


def _symptoms_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return []


def _render_body(description: str, solution: str, symptoms: List[str]) -> str:
    parts: List[str] = []
    if symptoms:
        parts.append("# Symptoms\n" + "\n".join(f"- {s}" for s in symptoms))
    if description.strip():
        parts.append("# Root cause\n" + description.strip())
    if solution.strip():
        parts.append("# Resolution\n" + solution.strip())
    return "\n\n".join(parts)


async def main() -> int:
    from sqlalchemy import text
    from app.core.database import AsyncSessionLocal
    from app.core.knowledge import get_default_bundle, index_concept_into_kb
    from app.core.improvement.auto_learn import _derive_tags

    bundle = get_default_bundle()
    migrated = 0
    async with AsyncSessionLocal() as session:
        # Guard: table may already be gone (post-002). Treat as a no-op.
        exists = await session.execute(text(
            "SELECT to_regclass('public.knowledge_entries') IS NOT NULL"
        ))
        if not exists.scalar():
            logger.info("knowledge_entries table absent — nothing to migrate.")
            return 0
        rows = (await session.execute(text(
            "SELECT id, title, description, symptoms, solution, category, source "
            "FROM knowledge_entries ORDER BY id"
        ))).mappings().all()

    for r in rows:
        title = str(r["title"] or "").strip()
        if not title:
            continue
        description = str(r["description"] or "")
        solution = str(r["solution"] or "")
        category = str(r["category"] or "").strip()
        symptoms = _symptoms_list(r["symptoms"])
        body = _render_body(description, solution, symptoms)
        extra = [category] if category else []
        tags = _derive_tags(f"{description}\n{solution}", symptoms, extra=extra)
        res = bundle.write_concept(
            section="known-issues",
            title=title,
            body=body,
            description=description[:500],
            tags=tags,
            source=str(r["source"] or "manual"),
            confidence=0.9,
            extra_frontmatter={"migrated_from": f"knowledge_entries:{r['id']}"},
        )
        row_id = await index_concept_into_kb(bundle, Path(res["path"]), bank="kb")
        migrated += 1
        logger.info(
            "migrated ke:%s -> %s/%s (kb row=%s, tags=%s)",
            r["id"], res["section"], res["slug"], row_id, tags,
        )

    logger.info("done: migrated %d knowledge_entries into OKF bundle.", migrated)
    return migrated


if __name__ == "__main__":
    asyncio.run(main())
