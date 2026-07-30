"""Investigation Supervisor — quality gate for ReAct agent output.

The supervisor evaluates each completed investigation and routes it to one
of four outcomes:

  PASS      — answer is good enough; proceed to auto-learn.
  RETRY     — answer is low-quality; re-run the agent with corrective guidance
              injected into the query (up to ``max_retries`` times).
  HITL      — answer is borderline; surface to an engineer for review before
              persisting to the knowledge base.
  ESCALATE  — retries exhausted or answer is fundamentally unusable; return
              the best partial result and emit an alert.

Scoring model (weighted 0→1)
----------------------------
  confidence_score    40 %  — caller-supplied score (0.0–1.0)
  answer_richness     25 %  — length and resolution-keyword signal
  tool_health         20 %  — fraction of tool calls that succeeded
  certainty           15 %  — absence of "I don't know" hedging phrases

Thresholds are tunable via environment variables so the sensitivity can be
adjusted without a deploy.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Uncertainty hedge phrases that signal an incomplete answer.
# ---------------------------------------------------------------------------
_UNCERTAINTY_PHRASES = (
    "i cannot",
    "i can't",
    "i don't know",
    "i do not know",
    "unable to",
    "could not find",
    "no data available",
    "insufficient information",
    "no information",
    "not able to determine",
    "cannot determine",
    "no logs found",
    "no results",
    "no evidence",
)

# Resolution keywords — same as react.py _RESOLUTION_RE, kept in sync.
_RESOLUTION_RE = re.compile(
    r'\b(root cause|resolved|fix applied|solution|cause is|issue is|identified)\b',
    re.IGNORECASE,
)

# Minimum answer length (characters) to be considered substantive.
_MIN_ANSWER_CHARS = 80

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class SupervisorAction(str, Enum):
    PASS     = "pass"
    RETRY    = "retry"
    HITL     = "hitl"
    ESCALATE = "escalate"


@dataclass
class SupervisorVerdict:
    """Decision returned by :meth:`InvestigationSupervisor.evaluate`."""

    action:         SupervisorAction
    score:          float          # composite quality score [0.0, 1.0]
    reason:         str            # human-readable explanation
    retry_guidance: str = ""       # injected into the re-run query on RETRY
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def should_retry(self) -> bool:
        return self.action == SupervisorAction.RETRY

    @property
    def should_hitl(self) -> bool:
        return self.action == SupervisorAction.HITL

    @property
    def passed(self) -> bool:
        return self.action == SupervisorAction.PASS


@dataclass
class SupervisorConfig:
    """Thresholds and behaviour flags for the supervisor.

    All defaults can be overridden via environment variables.
    """

    # Score >= this → PASS immediately.
    pass_threshold: float = 0.60

    # Score >= this (but < pass_threshold) → HITL when hitl_enabled=True.
    hitl_threshold: float = 0.50

    # Maximum number of automatic retries before escalating.
    max_retries: int = 1

    # Whether HITL is a valid route (False → scores in hitl band go to RETRY).
    hitl_enabled: bool = True

    # Whether to run an LLM quality check in addition to heuristics.
    llm_scoring_enabled: bool = False

    # Total input+output tokens allowed across supervisor retry loops, counting
    # the whole agent tree (delegated subagents included — see
    # app.harness.usage_ledger). Keep in step with
    # settings.supervisor_token_budget, which documents the calibration.
    token_budget: int = 1_200_000

    @classmethod
    def from_settings(cls) -> "SupervisorConfig":
        """Build config from the canonical :class:`app.config.Settings` object."""
        return cls(
            pass_threshold=settings.supervisor_pass_threshold,
            hitl_threshold=settings.supervisor_hitl_threshold,
            max_retries=settings.supervisor_max_retries,
            hitl_enabled=settings.supervisor_hitl_enabled,
            llm_scoring_enabled=settings.supervisor_llm_scoring,
            token_budget=settings.supervisor_token_budget,
        )


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class InvestigationSupervisor:
    """Evaluates agent output and returns a routing verdict.

    Designed to be called *once* per completed investigation run, after
    ``_execute_agent`` returns and before ``_auto_learn`` is called.

    Usage::

        supervisor = InvestigationSupervisor(SupervisorConfig())
        verdict = supervisor.evaluate(
            final_answer  = result["final_answer"],
            tool_calls    = result["tool_calls"],
            confidence    = 0.75,
            retry_count   = 0,
        )
        if verdict.action == SupervisorAction.PASS:
            await self._auto_learn(...)
        elif verdict.action == SupervisorAction.RETRY:
            # re-run with verdict.retry_guidance injected into query
    """

    def __init__(self, config: Optional[SupervisorConfig] = None) -> None:
        self._cfg = config or SupervisorConfig()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        final_answer:  str,
        tool_calls:    List[Dict[str, Any]],
        confidence:    float,
        retry_count:   int = 0,
        messages:      Optional[List[Dict[str, Any]]] = None,
    ) -> SupervisorVerdict:
        """Score the investigation and return a routing verdict.

        Args:
            final_answer:  The agent's final response text.
            tool_calls:    List of tool call summaries from the agent run.
            confidence:    Caller-supplied confidence score in [0.0, 1.0].
            retry_count:   How many times this investigation has already been
                           retried (used to cap retry loops).
            messages:      Serialized conversation messages (used by the LLM
                           grader to extract tool-output evidence).

        Returns:
            :class:`SupervisorVerdict`
        """
        answer     = (final_answer or "").strip()
        score, breakdown = self._composite_score(answer, tool_calls, confidence)

        grader_result: Optional[Dict[str, Any]] = None
        if self._cfg.llm_scoring_enabled and answer:
            try:
                from app.core.quality.grader import build_evidence, grade_answer
                evidence = build_evidence(messages or [])
                grader_result = await grade_answer(
                    final_answer=answer,
                    evidence=evidence,
                )
                if grader_result is not None:
                    g_score = float(grader_result.get("score", 0.5))
                    score = score * 0.80 + g_score * 0.20
                    score = max(0.0, min(1.0, score))
                    breakdown["grader_score"] = round(g_score, 3)
                    breakdown["grader_verdict"] = grader_result.get("verdict", "unknown")
                    breakdown["composite"] = round(score, 3)
            except Exception as _ge:  # noqa: BLE001 — grader must never break the loop
                logger.warning(
                    "InvestigationSupervisor: grader skipped (%s)", _ge
                )

        logger.debug(
            "InvestigationSupervisor: score=%.3f breakdown=%s retry_count=%d",
            score, breakdown, retry_count,
        )

        verdict = self._route(score, breakdown, answer, retry_count)

        # Enrich retry guidance with the grader's reasoning when available.
        if (
            grader_result is not None
            and verdict.action == SupervisorAction.RETRY
            and grader_result.get("reasoning")
        ):
            verdict = SupervisorVerdict(
                action=verdict.action,
                score=verdict.score,
                reason=verdict.reason,
                retry_guidance=(
                    verdict.retry_guidance
                    + f"\n• Grader assessment: {grader_result['reasoning']}"
                ),
                score_breakdown=verdict.score_breakdown,
            )

        return verdict

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _composite_score(
        self,
        answer:     str,
        tool_calls: List[Dict[str, Any]],
        confidence: float,
    ) -> Tuple[float, Dict[str, float]]:
        """Compute a weighted quality score in [0.0, 1.0]."""

        # ── Component 1: caller confidence (40 %) ──────────────────────
        conf_score = max(0.0, min(1.0, float(confidence)))

        # ── Component 2: answer richness (25 %) ──────────────────────
        richness = self._score_answer_richness(answer)

        # ── Component 3: tool health (20 %) ────────────────────────────
        tool_health = self._score_tool_health(tool_calls)

        # ── Component 4: certainty — absence of hedges (15 %) ──────────
        certainty = self._score_certainty(answer)

        composite = (
            conf_score  * 0.40 +
            richness    * 0.25 +
            tool_health * 0.20 +
            certainty   * 0.15
        )

        breakdown = {
            "confidence":   round(conf_score,  3),
            "richness":     round(richness,     3),
            "tool_health":  round(tool_health,  3),
            "certainty":    round(certainty,    3),
            "composite":    round(composite,    3),
        }
        return composite, breakdown

    @staticmethod
    def _score_answer_richness(answer: str) -> float:
        """Score based on length + resolution keywords."""
        if not answer:
            return 0.0

        # Length component: saturates at 1 000 chars
        length_score = min(len(answer) / 1000.0, 1.0)

        # Resolution keyword bonus
        keyword_score = 1.0 if _RESOLUTION_RE.search(answer) else 0.0

        # Short penalty: below _MIN_ANSWER_CHARS is always low quality
        if len(answer) < _MIN_ANSWER_CHARS:
            return 0.10

        return (length_score * 0.6) + (keyword_score * 0.4)

    @staticmethod
    def _score_tool_health(tool_calls: List[Dict[str, Any]]) -> float:
        """Fraction of successful tool calls.

        Reads the actual per-call outcome (``ok`` / ``status``) populated by
        ``agent_runner._serialize_agent_result`` from the tool's real output via
        ``classify_tool_failure`` — NOT the tool name. A tool named
        ``cloudwatch_get_error_logs`` that succeeds must not be counted as failed.

        Call summaries that predate this status field (``ok``/``status`` both
        absent) are treated as unknown/neutral per-call, matching the prior
        no-signal behaviour rather than assuming success or failure.
        """
        if not tool_calls:
            # No tools used — ambiguous; give a neutral score
            return 0.60

        scored = 0
        successes = 0.0
        for tc in tool_calls:
            if "ok" in tc:
                scored += 1
                successes += 1.0 if tc.get("ok") else 0.0
            elif "status" in tc:
                scored += 1
                successes += 0.0 if str(tc.get("status")).lower() == "error" else 1.0
            else:
                # No status recorded for this call — neutral contribution.
                scored += 1
                successes += 0.60

        if scored == 0:
            return 0.60
        return max(0.0, min(1.0, successes / scored))

    @staticmethod
    def _score_certainty(answer: str) -> float:
        """1.0 if no hedging phrases found, decreasing with each hedge hit."""
        if not answer:
            return 0.0

        lower = answer.lower()
        hits = sum(1 for phrase in _UNCERTAINTY_PHRASES if phrase in lower)
        if hits == 0:
            return 1.0
        if hits == 1:
            return 0.60
        return max(0.0, 1.0 - (hits * 0.25))

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _route(
        self,
        score:      float,
        breakdown:  Dict[str, float],
        answer:     str,
        retry_count: int,
    ) -> SupervisorVerdict:
        """Map composite score + retry count to a SupervisorAction."""

        # ── Hard fail: empty answer ────────────────────────────────────
        if not answer:
            if retry_count < self._cfg.max_retries:
                return SupervisorVerdict(
                    action          = SupervisorAction.RETRY,
                    score           = score,
                    reason          = "Agent returned an empty answer.",
                    retry_guidance  = (
                        "Your previous attempt returned no answer. "
                        "Please re-investigate using the available tools and "
                        "provide a specific, evidence-based conclusion."
                    ),
                    score_breakdown = breakdown,
                )
            return SupervisorVerdict(
                action          = SupervisorAction.ESCALATE,
                score           = score,
                reason          = "Agent returned empty answer after retries — escalating.",
                score_breakdown = breakdown,
            )

        # ── PASS ────────────────────────────────────────────────────────
        if score >= self._cfg.pass_threshold:
            return SupervisorVerdict(
                action          = SupervisorAction.PASS,
                score           = score,
                reason          = f"Quality score {score:.2f} meets pass threshold "
                                  f"{self._cfg.pass_threshold:.2f}.",
                score_breakdown = breakdown,
            )

        # ── Retries still available ────────────────────────────────────
        if retry_count < self._cfg.max_retries:
            guidance = self._build_retry_guidance(breakdown, answer)
            return SupervisorVerdict(
                action          = SupervisorAction.RETRY,
                score           = score,
                reason          = f"Quality score {score:.2f} below pass threshold; "
                                  f"retrying ({retry_count + 1}/{self._cfg.max_retries}).",
                retry_guidance  = guidance,
                score_breakdown = breakdown,
            )

        # ── HITL band (retries exhausted, score borderline) ───────────
        if score >= self._cfg.hitl_threshold and self._cfg.hitl_enabled:
            return SupervisorVerdict(
                action          = SupervisorAction.HITL,
                score           = score,
                reason          = f"Quality score {score:.2f} in HITL band "
                                  f"[{self._cfg.hitl_threshold:.2f}, "
                                  f"{self._cfg.pass_threshold:.2f}); "
                                  f"requesting engineer review.",
                score_breakdown = breakdown,
            )

        # ── ESCALATE ────────────────────────────────────────────────────
        return SupervisorVerdict(
            action          = SupervisorAction.ESCALATE,
            score           = score,
            reason          = f"Quality score {score:.2f} below all thresholds after "
                              f"{retry_count} retry(ies) — escalating.",
            score_breakdown = breakdown,
        )

    @staticmethod
    def _build_retry_guidance(
        breakdown: Dict[str, float],
        answer:    str,
    ) -> str:
        """Construct targeted corrective guidance for the retry query."""
        hints: List[str] = []

        if breakdown.get("richness", 1.0) < 0.40:
            hints.append(
                "Your previous answer was too brief or lacked specific findings. "
                "Run more tool queries to gather concrete evidence before concluding."
            )
        if breakdown.get("certainty", 1.0) < 0.60:
            hints.append(
                "Your previous answer contained too many uncertain statements. "
                "If data is unavailable from one source, try an alternative tool "
                "or explain specifically what was found versus what is unknown."
            )
        if breakdown.get("tool_health", 1.0) < 0.50:
            hints.append(
                "Several tool calls failed in the previous run. "
                "Check tool arguments carefully and try alternative queries."
            )
        if not hints:
            hints.append(
                "The previous investigation answer did not meet quality standards. "
                "Please provide a more thorough analysis with specific evidence."
            )

        return (
            "[Supervisor Guidance — address these issues in your re-investigation]\n"
            + "\n".join(f"• {h}" for h in hints)
        )
