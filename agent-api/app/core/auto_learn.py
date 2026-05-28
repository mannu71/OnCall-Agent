"""Closed learning loop — automatically learn from completed investigations.

When an investigation finishes the agent optionally triggers this module to:
  1. **Auto-approve gate** — if confidence ≥ threshold, skip HITL and learn
     immediately; otherwise record the result and wait for engineer approval.
  2. **KB upsert** — insert or update the ``knowledge_entries`` table so future
     investigations recall this resolution (pgvector similarity search).
  3. **Pattern upsert** — bump occurrence_count / confidence in log_patterns
     so high-frequency signatures surface first in recall.
  4. **Trajectory write** — append a structured JSONL record to the
     trajectory file for audit and quality tracking.
  5. **Skill distillation** — when ≥ MIN_TOOL_CALLS tool calls were made,
     delegate to ``SkillService.distill()`` to produce a structured, executable
     skill saved to the ``skills`` table.

Usage (call from the synthesis / learn node in the LangGraph graph)::

    from app.core.auto_learn import AutoLearnService, AutoLearnConfig

    svc = AutoLearnService(db_session, llm=aux_llm)
    result = await svc.learn(execution_id, investigation_state)

The service is designed to be called **outside** the main investigation
transaction — a failure here must never fail the investigation itself.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AutoLearnConfig:
    """Tunables for the closed learning loop.

    All values can be overridden via environment variables so the loop can
    be tuned without a code deploy.
    """

    # Confidence score at which the loop auto-approves without waiting for HITL.
    # Must be in [0.0, 1.0]. Set to 1.0 to disable auto-approve entirely.
    auto_approve_threshold: float = float(
        os.getenv("AUTO_LEARN_THRESHOLD", "0.85")
    )

    # Minimum number of tool calls in an investigation before skill distillation
    # is attempted (prevents generating trivial one-liner skills).
    min_tool_calls_for_skill: int = int(
        os.getenv("AUTO_LEARN_MIN_TOOL_CALLS", "5")
    )

    # File path for the JSONL trajectory log.
    trajectory_path: str = os.getenv(
        "TRAJECTORY_PATH",
        str(Path.home() / ".kyc_protect" / "trajectories" / "trajectory_samples.jsonl"),
    )

    # File path for failed trajectory log.
    failed_trajectory_path: str = os.getenv(
        "FAILED_TRAJECTORY_PATH",
        str(Path.home() / ".kyc_protect" / "trajectories" / "failed_trajectories.jsonl"),
    )

    # Whether to attempt skill distillation.
    distill_skills: bool = os.getenv("AUTO_LEARN_DISTILL_SKILLS", "true").lower() != "false"

    # Whether to attempt dynamic node code generation.
    compile_dynamic_nodes: bool = os.getenv("AUTO_LEARN_COMPILE_NODES", "true").lower() != "false"

    # Confidence delta added per occurrence.
    confidence_delta: float = 0.10

    # Maximum confidence ceiling.
    max_confidence: float = 1.0


# Singleton default config — callers may pass a custom one.
_DEFAULT_CONFIG = AutoLearnConfig()


# ─────────────────────────────────────────────────────────────────────────────
# Trajectory helpers
# ─────────────────────────────────────────────────────────────────────────────

def _trajectory_entry(
    execution_id: str,
    state: Dict[str, Any],
    *,
    success: bool,
    auto_approved: bool,
) -> Dict[str, Any]:
    """Build a structured trajectory dict for JSONL persistence."""
    tool_calls = state.get("tool_calls") or []
    return {
        "execution_id": execution_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "auto_approved": auto_approved,
        "inputs": {
            "user_query": state.get("user_query") or state.get("trigger") or "",
            "workflow_name": state.get("workflow_name", ""),
        },
        "tool_call_count": len(tool_calls),
        "tool_calls": tool_calls[:50],   # cap to avoid huge JSONL entries
        "synthesis": {
            "root_cause": state.get("root_cause") or state.get("final_answer") or "",
            "confidence_score": state.get("confidence_score"),
            "suggestions": state.get("suggestions") or [],
        },
        "outcome": {
            "engineer_approved": state.get("engineer_approved"),
            "engineer_notes": state.get("engineer_notes") or "",
        },
        "total_cost_usd": state.get("total_cost_usd"),
    }


def _append_trajectory(path: str, entry: Dict[str, Any]) -> None:
    """Append *entry* to a JSONL file, creating parent dirs as needed.

    Write-and-flush so crashes don't corrupt the file.  Never raises — logs
    errors instead.
    """
    try:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            fh.flush()
    except Exception as exc:
        logger.warning("auto_learn: trajectory write failed (%s)", exc)



# ─────────────────────────────────────────────────────────────────────────────
# AutoLearnResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AutoLearnResult:
    """Return value from ``AutoLearnService.learn()``."""

    execution_id: str
    auto_approved: bool
    kb_upserted: bool = False
    pattern_bumped: bool = False
    trajectory_saved: bool = False
    skill_distilled: bool = False
    dynamic_node_compiled: bool = False
    skipped_reason: Optional[str] = None   # set when learning was skipped
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# AutoLearnService
# ─────────────────────────────────────────────────────────────────────────────

class AutoLearnService:
    """Closed learning loop service.

    Args:
        db:     AsyncSession from SQLAlchemy.  When ``None`` all DB writes are
                skipped (useful for testing or environments without a DB).
        llm:    LangChain LLM used for skill distillation.  When ``None``,
                distillation is skipped.
        config: AutoLearnConfig instance.  Defaults to environment-driven config.
    """

    def __init__(
        self,
        db: Any = None,
        llm: Any = None,
        config: AutoLearnConfig = _DEFAULT_CONFIG,
    ) -> None:
        self._db = db
        self._llm = llm
        self._cfg = config

    # ── Public API ────────────────────────────────────────────────────────────

    def should_auto_approve(self, state: Dict[str, Any]) -> bool:
        """Return True when confidence is high enough to skip HITL.

        Trajectories above a quality threshold are accepted immediately
        without human review.
        """
        confidence = state.get("confidence_score")
        if confidence is None:
            return False
        try:
            return float(confidence) >= self._cfg.auto_approve_threshold
        except (TypeError, ValueError):
            return False

    async def learn(
        self,
        execution_id: str,
        state: Dict[str, Any],
        *,
        force: bool = False,
    ) -> AutoLearnResult:
        """Run the full closed learning loop for one completed investigation.

        Args:
            execution_id: Unique execution / investigation ID.
            state:        Final LangGraph state dict after synthesis.
            force:        If True, bypass the auto-approve check (use when
                          engineer has manually approved via HITL).

        Returns:
            AutoLearnResult with flags for each write that succeeded.
        """
        result = AutoLearnResult(
            execution_id=execution_id,
            auto_approved=False,
        )

        # ── Gate: only learn from approved or auto-approved investigations ────
        approved = state.get("engineer_approved") or force
        auto = self.should_auto_approve(state)

        if not approved and not auto:
            result.skipped_reason = (
                "not approved: confidence "
                f"{state.get('confidence_score')!r} < threshold "
                f"{self._cfg.auto_approve_threshold}"
            )
            logger.info(
                "auto_learn: skipping — %s (execution_id=%s)",
                result.skipped_reason, execution_id,
            )
            return result

        result.auto_approved = auto and not approved
        logger.info(
            "auto_learn: starting learn pass (execution_id=%s, auto_approved=%s)",
            execution_id, result.auto_approved,
        )

        # ── 1. KB upsert ──────────────────────────────────────────────────────
        result.kb_upserted = await self._upsert_known_issue(execution_id, state)

        # ── 2. Pattern bump ───────────────────────────────────────────────────
        result.pattern_bumped = await self._bump_pattern(state)

        # ── 3. Trajectory write ───────────────────────────────────────────────
        entry = _trajectory_entry(
            execution_id, state,
            success=True,
            auto_approved=result.auto_approved,
        )
        path = self._cfg.trajectory_path
        _append_trajectory(path, entry)
        result.trajectory_saved = True
        logger.info("auto_learn: trajectory appended → %s", path)

        # ── 4. Skill distillation ─────────────────────────────────────────────
        tool_calls = state.get("tool_calls") or []
        if (
            self._cfg.distill_skills
            and self._llm is not None
            and len(tool_calls) >= self._cfg.min_tool_calls_for_skill
        ):
            result.skill_distilled = await self._distill_skill(execution_id, state)

        # ── 5. Dynamic Node Compilation ───────────────────────────────────────
        if (
            self._cfg.compile_dynamic_nodes
            and self._llm is not None
            and len(tool_calls) >= self._cfg.min_tool_calls_for_skill
        ):
            user_query = state.get("user_query") or state.get("trigger") or ""
            if any(kw in user_query.lower() for kw in ("parse", "extract", "hex", "regex", "map", "resolve")):
                result.dynamic_node_compiled = await self._distill_dynamic_node(execution_id, state)

        return result

    async def learn_failed(
        self,
        execution_id: str,
        state: Dict[str, Any],
        error: str,
    ) -> None:
        """Append a failed-trajectory record (for data quality tracking).

        Never raises.
        """
        try:
            entry = _trajectory_entry(
                execution_id, state, success=False, auto_approved=False
            )
            entry["failure_reason"] = error
            _append_trajectory(self._cfg.failed_trajectory_path, entry)
        except Exception as exc:
            logger.warning("auto_learn: failed trajectory write error: %s", exc)

    # ── Private: KB upsert ────────────────────────────────────────────────────

    async def _upsert_known_issue(
        self,
        execution_id: str,
        state: Dict[str, Any],
    ) -> bool:
        """Insert or update a KnowledgeEntry row from the investigation result.

        Uses SQLAlchemy ``ON CONFLICT`` upsert on ``(title)`` to avoid
        duplicates for the same root cause.
        """
        if self._db is None:
            logger.debug("auto_learn: no DB session — skipping KB upsert")
            return False

        root_cause = state.get("root_cause") or state.get("final_answer") or ""
        if not root_cause.strip():
            logger.debug("auto_learn: no root_cause in state — skipping KB upsert")
            return False

        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from app.models.db_models import KnowledgeEntryModel

            title = _make_title(root_cause)
            symptoms = _extract_symptoms(state)
            solution = _build_solution(state)

            stmt = pg_insert(KnowledgeEntryModel).values(
                title=title,
                description=root_cause[:2000],
                symptoms=symptoms,
                solution=solution,
                category=state.get("workflow_name", "general"),
                source="agent",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=["title"],
                set_={
                    "description": root_cause[:2000],
                    "solution": solution,
                    "source": "agent",
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await self._db.execute(stmt)
            await self._db.commit()
            logger.info(
                "auto_learn: knowledge_entry upserted (execution_id=%s, title=%r)",
                execution_id, title,
            )
            return True

        except Exception as exc:
            logger.warning("auto_learn: KB upsert failed — %s", exc)
            try:
                await self._db.rollback()
            except Exception:
                pass
            return False

    # ── Private: pattern bump ─────────────────────────────────────────────────

    async def _bump_pattern(self, state: Dict[str, Any]) -> bool:
        """Increment severity for any log_pattern that was matched during
        this investigation (identified via state["matched_pattern_ids"]).
        """
        if self._db is None:
            return False

        pattern_ids: List[int] = state.get("matched_pattern_ids") or []
        if not pattern_ids:
            return False

        try:
            from sqlalchemy import text

            for pid in pattern_ids:
                await self._db.execute(
                    text(
                        """
                        UPDATE log_patterns
                        SET severity = LEAST(severity + 1, 10),
                            updated_at = NOW()
                        WHERE id = :pid
                        """
                    ),
                    {"pid": pid},
                )
            await self._db.commit()
            logger.info(
                "auto_learn: bumped %d log_pattern(s)", len(pattern_ids)
            )
            return True

        except Exception as exc:
            logger.warning("auto_learn: pattern bump failed — %s", exc)
            try:
                await self._db.rollback()
            except Exception:
                pass
            return False

    # ── Private: skill distillation ───────────────────────────────────────────

    async def _distill_skill(
        self,
        execution_id: str,
        state: Dict[str, Any],
    ) -> bool:
        """Delegate to SkillService to distil the investigation into an executable skill.

        SkillService uses the LLM to produce structured steps (not freeform
        markdown) and saves them to the ``skills`` table, not knowledge_entries.
        Uses the auxiliary (cheap) model, not the main investigation model.
        """
        try:
            from app.core.skills import SkillService
            svc = SkillService(llm=self._llm)
            result = await svc.distill(execution_id=execution_id, state=state)
            if result:
                logger.info(
                    "auto_learn: skill distilled → '%s' (id=%s, execution_id=%s)",
                    result.get("name"), result.get("id"), execution_id,
                )
                return True
            return False
        except Exception as exc:
            logger.warning("auto_learn: skill distillation failed — %s", exc)
            return False

    async def _distill_dynamic_node(
        self,
        execution_id: str,
        state: Dict[str, Any],
    ) -> bool:
        """Use LLM to distill incident logs into a dynamically compiled python GraphNode class."""
        if self._llm is None:
            return False

        try:
            from app.workflow.dynamic_loader import DynamicNodeCompiler
            from langchain_core.messages import SystemMessage, HumanMessage

            # 1. Ask the LLM to output a standalone Python GraphNode class following our exact structure
            _COMPILER_SYSTEM_PROMPT = """\
You are an advanced self-programming compiler agent for an on-call response system.
Your job is to translate a completed incident investigation trajectory into a standalone, safe, and optimized Python class inheriting from GraphNode.

The class MUST:
1. Be named <SnakeCaseConverted>Node (e.g., if node name is 'log_parser', class is 'LogParserNode').
2. Inherit from `app.engine.crawler_engine.workflow_graph.GraphNode`.
3. Implement the `async def exec(self, prep_result: Any) -> Any` method.
4. Implement optional `async def prep(self, shared: Dict[str, Any]) -> Any` and `async def post(self, shared: Dict[str, Any], exec_result: Any) -> str`.
5. Strictly adhere to safety guidelines:
   - NO imports of `os`, `sys`, `subprocess`, `socket`, `shutil`, `importlib`, `pty` or `ctypes`.
   - NO dynamic code evaluation (`eval`, `exec`, `__import__`).
   - NO raw file system writes or network socket calls.
   - Use standard library safe imports like `re`, `json`, `datetime` or `urllib.parse`.

Output ONLY the exact Python code within ```python ``` blocks. Do not add markdown explanation, notes, or preamble outside the code fences."""

            user_query = state.get("user_query") or state.get("trigger") or ""
            root_cause = state.get("root_cause") or state.get("final_answer") or ""

            prompt_msg = f"""\
Generate a custom GraphNode subclass named 'log_parser' to automate the parsing of this incident query.
Incident Query: {user_query}
Root cause: {root_cause}
Logs gathered in tool calls: {json.dumps(state.get("tool_calls", []))}
"""

            response = await self._llm.ainvoke([
                SystemMessage(content=_COMPILER_SYSTEM_PROMPT),
                HumanMessage(content=prompt_msg),
            ])
            raw = response.content if hasattr(response, "content") else str(response)

            # Strip markdown fences
            raw = re.sub(r"^```(?:python)?\s*", "", raw.strip(), flags=re.MULTILINE)
            raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)

            # 2. Compile and Register via DynamicNodeCompiler
            compiler = DynamicNodeCompiler()
            compiler.compile_and_register(node_name="log_parser", code_content=raw)
            return True

        except Exception as exc:
            logger.warning("auto_learn: dynamic node compilation failed — %s", exc)
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_title(root_cause: str) -> str:
    """Create a short title from a root cause string."""
    words = root_cause.split()[:10]
    title = " ".join(words)
    return title[:200] if len(title) <= 200 else title[:197] + "..."


def _extract_symptoms(state: Dict[str, Any]) -> List[str]:
    """Extract symptom strings from state for the known_issue.symptoms column."""
    symptoms: List[str] = []
    query = state.get("user_query") or state.get("trigger") or ""
    if query:
        symptoms.append(query[:200])
    for finding in (state.get("log_findings") or [])[:3]:
        if isinstance(finding, dict) and finding.get("summary"):
            symptoms.append(str(finding["summary"])[:100])
        elif isinstance(finding, str):
            symptoms.append(finding[:100])
    return symptoms


def _build_solution(state: Dict[str, Any]) -> str:
    """Build a solution string from suggestions and root cause."""
    parts: List[str] = []
    root = state.get("root_cause") or state.get("final_answer") or ""
    if root:
        parts.append(root[:500])
    for s in (state.get("suggestions") or [])[:5]:
        if isinstance(s, str):
            parts.append(f"• {s}")
        elif isinstance(s, dict):
            text = s.get("text") or s.get("title") or str(s)
            parts.append(f"• {text}")
    return "\n".join(parts)[:2000]
