"""Loop 4 proposal applier — safe, reversible kinds only.

apply_proposals(report, *, dry_run) promotes low-risk draft proposals from
an ImprovementReport into live actions:

  skill        → create a confidence-gated draft skill (curator promotes later)
  reliability  → logged as a structured audit event (no silent state change)
  eval         → mark the source failure_ledger fingerprint 'converted'
                 (bookkeeping only — see app.core.improvement.analyzer.
                 convert_recurring_failures; never fabricates eval assertions)

'prompt' and 'policy' proposals are always left as human-reviewed drafts
because the system-prompt CACHE CONTRACT makes auto-editing off-limits and
policy changes require operator sign-off.

Every non-dry-run call is preceded by the eval guardrail (guard.py): if the
selftest fails, no proposals are applied and the reason is returned.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List

from app.core.improvement.analyzer import ImprovementReport

logger = logging.getLogger(__name__)

_SAFE_KINDS = frozenset({"skill", "reliability", "eval"})


def _eligible(proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [p for p in proposals if p.get("kind") in _SAFE_KINDS and p.get("status") == "draft"]


async def apply_proposals(
    report: ImprovementReport,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Apply safe draft proposals from *report*.

    Args:
        report:  ImprovementReport from :func:`analyze_recent`.
        dry_run: When ``True`` returns what would be applied without writing.

    Returns:
        Dict with keys: eligible, applied, skipped, dry_run, guard_passed, detail.
    """
    eligible = _eligible(report.proposals)
    base = {
        "eligible": len(eligible),
        "applied": 0,
        "skipped": 0,
        "dry_run": dry_run,
        "guard_passed": None,
        "detail": [],
    }

    if not eligible:
        return base

    if dry_run:
        base["detail"] = [
            {
                "kind": p.get("kind"),
                "target": p.get("target"),
                "suggestion": (p.get("suggestion") or "")[:120],
            }
            for p in eligible
        ]
        return base

    # Eval guardrail: only proceed when the selftest is green.
    from app.core.improvement.guard import run_selftest_guard

    guard = await run_selftest_guard()
    base["guard_passed"] = guard["passed"]
    if not guard["passed"]:
        logger.warning(
            "apply_proposals: eval guard failed — skipping all %d proposals. %s",
            len(eligible),
            guard.get("detail", "")[:200],
        )
        base["skipped"] = len(eligible)
        base["detail"] = [{"guard_blocked": True, "reason": guard.get("detail", "")[:200]}]
        return base

    applied = 0
    skipped = 0
    detail: List[Dict[str, Any]] = []

    for proposal in eligible:
        kind = proposal.get("kind")
        try:
            if kind == "skill":
                ok = await _apply_skill_proposal(proposal)
            elif kind == "reliability":
                ok = await _apply_reliability_proposal(proposal)
            elif kind == "eval":
                ok = await _apply_eval_proposal(proposal)
            else:
                ok = False

            if ok:
                applied += 1
                detail.append(
                    {
                        "kind": kind,
                        "target": proposal.get("target"),
                        "suggestion": (proposal.get("suggestion") or "")[:120],
                        "status": "applied",
                    }
                )
            else:
                skipped += 1
                detail.append(
                    {
                        "kind": kind,
                        "target": proposal.get("target"),
                        "status": "skipped",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("apply_proposals: failed for %s proposal (%s)", kind, exc)
            skipped += 1
            detail.append(
                {
                    "kind": kind,
                    "target": proposal.get("target"),
                    "status": "error",
                    "error": str(exc)[:200],
                }
            )

    base["applied"] = applied
    base["skipped"] = skipped
    base["detail"] = detail
    return base


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug or ("proposal_" + uuid.uuid4().hex[:8])


async def _apply_skill_proposal(proposal: Dict[str, Any]) -> bool:
    """Create a draft skill from a 'skill' proposal via the skill service."""
    try:
        from app.core.skills.service import SkillService

        svc = SkillService()
        suggestion = proposal.get("suggestion", "")
        rationale = proposal.get("rationale", "")
        slug = _slugify(suggestion)[:60]
        await svc.create_manual(
            name=slug,
            title=suggestion[:120],
            description=f"{suggestion}\n\nRationale: {rationale}",
            trigger_patterns=[],
            steps=[],
        )
        logger.info(
            "apply_proposals [skill]: drafted '%s' from proposal: %s",
            slug,
            suggestion[:80],
        )
        # Mark the drafted skill as 'draft' so the confidence-gate applies.
        # create_manual sets status='active'; patch it back to 'draft' since
        # this is an LLM-generated proposal, not a manually curated skill.
        try:
            from app.core.skills import service as _svc_mod

            rec = _svc_mod._CACHE.get(slug)
            if rec is not None:
                rec["status"] = "draft"
                rec["confidence"] = 0.0
                _svc_mod._write_record(rec)
        except Exception:  # noqa: BLE001 — best-effort downgrade
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_proposals [skill]: skipped (%s)", exc)
        return False


async def _apply_reliability_proposal(proposal: Dict[str, Any]) -> bool:
    """Record reliability proposals as structured audit events."""
    logger.info(
        "apply_proposals [reliability]: target=%s | %s",
        proposal.get("target"),
        (proposal.get("suggestion") or "")[:120],
    )
    return True


async def _apply_eval_proposal(proposal: Dict[str, Any]) -> bool:
    """Mark the source failure_ledger fingerprint 'converted' (bookkeeping
    only). See app.core.improvement.analyzer.convert_recurring_failures —
    an 'eval'-kind proposal's control_ref is always the fingerprint; the
    actual regression case/policy/selftest still needs a human to author,
    the suggestion text describes what to do. Marking it converted stops it
    from re-proposing on every analyze_recent() run.
    """
    fingerprint = proposal.get("control_ref")
    if not fingerprint:
        return False
    try:
        from app.infrastructure.persistence import failure_ledger_repository
        await failure_ledger_repository.mark_converted(
            fingerprint, control_ref=f"proposal:{proposal.get('target', '')}",
        )
        logger.info(
            "apply_proposals [eval]: marked failure_ledger fingerprint '%s' converted",
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_proposals [eval]: skipped (%s)", exc)
        return False
