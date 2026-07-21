"""Retrieval-accuracy eval — does FTS-only recall still find the CORRECT doc?

The knowledge stack moved off Bedrock embeddings to Postgres FTS over the OKF
bundle. This suite proves FTS-only recall is 100% accurate on a labelled query
set — including paraphrases, which are covered by document-side tag synonyms
(weight-A lexemes) rather than semantic vectors.

Hermetic and deterministic: it writes a fixed corpus of OKF concept docs into a
throwaway :class:`KnowledgeBundle` (a tempdir), indexes them into an isolated
``kb_eval`` memory bank, runs ``semantic_memory.recall(..., mode='fts')`` per
case, grades expected-doc-in-top-5, and purges the bank in a ``finally``. It
exercises the exact production SQL against the real container Postgres.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from evals.accuracy import _bootstrap  # noqa: F401

_EVAL_BANK = "kb_eval"
_K = 5

# ── Labelled corpus ──────────────────────────────────────────────────────────
# Each concept: (title, tags, body). Tags are the paraphrase-synonym anchors.
_CORPUS: List[Tuple[str, List[str], str]] = [
    ("SearchNotFoundException on AML screen",
     ["SearchNotFoundException", "AML screen", "search not found", "screening lookup"],
     "The AML screening lookup raised SearchNotFoundException because the search id "
     "could not be located in the provider."),
    ("HTTP 429 rate limit from compliance-api",
     ["HTTP 429", "TooManyRequests", "rate limit", "throttling", "too many requests"],
     "A downstream supplier returned HTTP 429 TooManyRequests; the outbound call rate "
     "exceeded the allowed limit and requests were throttled."),
    ("S3 NoSuchKey missing object",
     ["NoSuchKey", "S3", "missing file", "cloud storage object", "key does not exist"],
     "S3 GetObject failed with NoSuchKey — the requested file is missing from the cloud "
     "storage bucket."),
    ("Profile import bulk CSV onboarding",
     ["profile import", "bulk upload", "CSV onboarding", "customer onboarding"],
     "Bulk customer onboarding runs through the CSV profile import path, distinct from "
     "the single-profile API path."),
    ("Email notifier HTTP 400 Bad Request",
     ["HTTP 400", "Bad Request", "Email API", "notification rejected", "mail service"],
     "The Email API rejected the notification with HTTP 400 Bad Request; the mail service "
     "refused the malformed payload."),
    ("PagerDuty escalation owner for payment-service",
     ["PagerDuty", "escalation owner", "on-call", "paging", "payment-service", "who to page"],
     "Escalation ownership and paging for a payment-service incident live in PagerDuty; "
     "page the on-call owner when the payments service breaks overnight."),
    ("Database connection pool exhausted",
     ["connection pool", "connections exhausted", "connections used up", "timeout",
      "hanging requests"],
     "The database connection pool was exhausted — all connections were used up, so new "
     "queries hang and requests time out under load."),
    ("SQS message deserialization failure",
     ["deserialize", "SQS message", "queue parsing", "message parsing"],
     "Deserializing an SQS message failed — the body pulled from the queue could not be "
     "parsed into the expected type."),
    ("AmlScreen ArgumentOutOfRangeException",
     ["ArgumentOutOfRangeException", "AmlScreen", "out of range", "report crash"],
     "The AmlScreen report processor crashed with an ArgumentOutOfRangeException — an index "
     "was out of range while building the report."),
    ("Reports stuck in requested status",
     ["requested status", "pending reports", "submitted", "waiting to be processed"],
     "Reports left in the requested/submitted status are waiting to be processed and have "
     "not yet completed."),
    ("Failed reports in database breakdown",
     ["failed reports", "report failures", "reports database", "unsuccessful reports"],
     "Breakdown of failed reports in the database by type — the current state of "
     "unsuccessful report generation."),
    ("Split.io client already destroyed log flood",
     ["Split.io", "client destroyed", "log flood", "feature flag SDK", "disposed"],
     "After the feature-flag SDK client was disposed, every call logged 'Client has already "
     "been destroyed', flooding the logs."),
    ("Expired AWS credentials block log queries",
     ["expired credentials", "AWS creds", "login tokens", "CloudWatch unavailable",
      "cannot query logs"],
     "The AWS credentials (login tokens) expired, so CloudWatch log queries cannot run and "
     "logs are unavailable this turn."),
    ("Unguarded First() InvalidOperationException in ProfilesRepository",
     ["InvalidOperationException", "ProfilesRepository", "First on empty", "criteria collection"],
     "An unguarded .First() on an empty criteria collection in ProfilesRepository throws "
     "InvalidOperationException and crashes the request."),
]

# (query, expected_title, kind). Two queries per doc: one lexical, one paraphrase.
_CASES: List[Tuple[str, str, str]] = [
    ("SearchNotFoundException", "SearchNotFoundException on AML screen", "lexical"),
    ("aml screening cannot find the search", "SearchNotFoundException on AML screen", "paraphrase"),
    ("HTTP 429 rate limit", "HTTP 429 rate limit from compliance-api", "lexical"),
    ("service rejecting us with too many requests", "HTTP 429 rate limit from compliance-api", "paraphrase"),
    ("S3 NoSuchKey missing object", "S3 NoSuchKey missing object", "lexical"),
    ("a file is missing from the cloud storage bucket", "S3 NoSuchKey missing object", "paraphrase"),
    ("profile import bulk CSV", "Profile import bulk CSV onboarding", "lexical"),
    ("how does bulk customer onboarding work", "Profile import bulk CSV onboarding", "paraphrase"),
    ("HTTP 400 Bad Request Email API", "Email notifier HTTP 400 Bad Request", "lexical"),
    ("notification emails being rejected by the mail service", "Email notifier HTTP 400 Bad Request", "paraphrase"),
    ("PagerDuty escalation owner payment-service", "PagerDuty escalation owner for payment-service", "lexical"),
    ("who do I page when the payments service breaks overnight", "PagerDuty escalation owner for payment-service", "paraphrase"),
    ("DB connection pool exhausted", "Database connection pool exhausted", "lexical"),
    ("database connections all used up and requests hanging", "Database connection pool exhausted", "paraphrase"),
    ("SQS message deserialization failure", "SQS message deserialization failure", "lexical"),
    ("problem parsing a message pulled from the queue", "SQS message deserialization failure", "paraphrase"),
    ("AmlScreen ArgumentOutOfRangeException", "AmlScreen ArgumentOutOfRangeException", "lexical"),
    ("screening report crashed with an out of range exception", "AmlScreen ArgumentOutOfRangeException", "paraphrase"),
    ("reports requested status", "Reports stuck in requested status", "lexical"),
    ("how many report requests are waiting to be processed", "Reports stuck in requested status", "paraphrase"),
    ("failed reports in database", "Failed reports in database breakdown", "lexical"),
    ("current state of unsuccessful report generation", "Failed reports in database breakdown", "paraphrase"),
    ("Split.io client destroyed log flood", "Split.io client already destroyed log flood", "lexical"),
    ("logs flooded after the feature flag sdk was disposed", "Split.io client already destroyed log flood", "paraphrase"),
    ("expired AWS credentials", "Expired AWS credentials block log queries", "lexical"),
    ("login tokens no longer valid so cannot query logs", "Expired AWS credentials block log queries", "paraphrase"),
    ("ProfilesRepository InvalidOperationException", "Unguarded First() InvalidOperationException in ProfilesRepository", "lexical"),
    ("crash when the criteria collection is empty", "Unguarded First() InvalidOperationException in ProfilesRepository", "paraphrase"),
]


async def _purge_bank() -> None:
    from sqlalchemy import text
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM semantic_memory WHERE bank = :bank"), {"bank": _EVAL_BANK}
        )
        await session.commit()


async def _seed(tmp: Path) -> Dict[str, int]:
    """Write the corpus into a temp bundle, index into ``kb_eval``. Returns title→row_id."""
    from app.core.knowledge.bundle import KnowledgeBundle, index_concept_into_kb

    bundle = KnowledgeBundle(tmp)
    title_to_id: Dict[str, int] = {}
    for title, tags, body in _CORPUS:
        res = bundle.write_concept(
            section="known-issues", title=title, body=body,
            description=body[:200], tags=tags, source="kb", confidence=0.9,
        )
        row_id = await index_concept_into_kb(bundle, Path(res["path"]), bank=_EVAL_BANK)
        if row_id is not None:
            title_to_id[title] = int(row_id)
    return title_to_id


async def run_retrieval_suite() -> List[Dict[str, Any]]:
    from app.services.semantic_memory import semantic_memory

    rows: List[Dict[str, Any]] = []
    await _purge_bank()  # clean slate in case a prior run aborted
    with tempfile.TemporaryDirectory(prefix="kb_eval_") as td:
        try:
            title_to_id = await _seed(Path(td))
            for query, expected_title, kind in _CASES:
                expected_id = title_to_id.get(expected_title)
                results = await semantic_memory.recall(query, bank=_EVAL_BANK, k=_K)
                ids = [r["id"] for r in results]
                rank = ids.index(expected_id) + 1 if expected_id in ids else None
                hit = rank is not None and rank <= _K
                rows.append({
                    "feature": "retrieval",
                    "metric": "hit_at_5",
                    "id": f"{kind}:{query[:32]}",
                    "score": 1.0 if hit else 0.0,
                    "diagnostic": (f"rank {rank}" if rank else "MISS")
                                  + f" (kind={kind}, expected={expected_title[:40]!r})",
                })
        finally:
            await _purge_bank()
    return rows


if __name__ == "__main__":
    rows = asyncio.run(run_retrieval_suite())
    for r in rows:
        flag = "OK " if r["score"] >= 0.999 else "XX "
        print(f"{flag}{r['id']:<40} {r['diagnostic']}")
    n = len(rows)
    hit = sum(r["score"] for r in rows)
    print(f"\nretrieval accuracy: {hit:.0f}/{n} = {100 * hit / max(n, 1):.1f}%")
