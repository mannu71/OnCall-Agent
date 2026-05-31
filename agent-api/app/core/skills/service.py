"""Skill system — reusable, executable resolution procedures.

Skills are the executable layer on top of the knowledge base.  Where a
knowledge entry records *what was learned*, a skill records *how to act*.

Lifecycle
---------
1. **Distillation** — after a high-quality investigation, the LLM converts the
   trajectory into a structured skill (name, trigger_patterns, ordered steps)
   and saves it to the ``skills`` table.
2. **Recall** — at investigation start, vector similarity against trigger_patterns
   surfaces relevant skills as context injected into the agent prompt.
3. **Execution** — the agent calls ``execute_skill(name, context)`` to run a
   skill's steps in order against the live MCP tool set.
4. **Promotion** — an engineer (or the curator) can promote a knowledge_entry
   with ``source="agent"`` into a first-class skill.
5. **Curation** — the weekly curator archives skills with zero recent usage and
   consolidates near-duplicate skills via LLM.

Skill step schema
-----------------
Each step is a dict::

    {
        "order":       1,
        "description": "Fetch recent error logs",
        "tool":        "cloudwatch_get_logs",
        "args_template": {"log_group": "{log_group}", "minutes": 30},
        "condition":   null,          # optional: only run if context key is truthy
        "on_failure":  "continue"     # continue | abort (default: continue)
    }

Values in ``args_template`` may contain ``{context_key}`` placeholders that
are substituted from the execution context dict before the tool is called.
"""
from __future__ import annotations

import json
import logging
import re
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM prompt for skill distillation
# ---------------------------------------------------------------------------

_DISTILL_SYSTEM = """\
You are converting a completed investigation into a reusable, executable skill.

Return ONLY a JSON object — no markdown, no explanation.

Schema:
{
  "name":             "<snake_case_slug, max 60 chars>",
  "title":            "<human-readable title, max 120 chars>",
  "description":      "<one paragraph describing when to use this skill>",
  "trigger_patterns": ["<3-5 short phrases that indicate this skill is relevant>"],
  "steps": [
    {
      "order":          1,
      "description":    "<what this step does>",
      "tool":           "<tool_name or null if it's a reasoning step>",
      "args_template":  {"<arg>": "<value or {context_placeholder}>"},
      "condition":      null,
      "on_failure":     "continue"
    }
  ]
}

Rules:
- steps must be ordered (order 1, 2, 3 …).
- args_template values may contain {placeholders} that will be filled from execution context.
- If a step requires human judgement and no tool, set tool to null.
- Keep steps concrete and specific — not "check the logs" but "call cloudwatch_get_logs with log_group={log_group}".
- Maximum 10 steps.
"""

_DISTILL_USER = """\
Investigation summary:
Query: {user_query}
Root cause: {root_cause}
Tool calls made ({tool_count}):
{tool_summary}
Resolution: {resolution}
"""

# Minimum tool calls before distillation is attempted
_MIN_TOOL_CALLS = settings.skill_min_tool_calls


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class SkillStepResult:
    """Outcome of executing one skill step."""
    order:       int
    description: str
    tool:        Optional[str]
    skipped:     bool  = False
    success:     bool  = True
    output:      str   = ""
    error:       Optional[str] = None


@dataclass
class SkillExecutionResult:
    """Outcome of a full skill execution."""
    skill_name:   str
    success:      bool
    steps:        List[SkillStepResult] = field(default_factory=list)
    summary:      str = ""
    error:        Optional[str] = None

    def to_agent_text(self) -> str:
        """Format result as text for the agent."""
        lines = [f"Skill '{self.skill_name}' — {'✓ completed' if self.success else '✗ failed'}"]
        for s in self.steps:
            status = "⏭ skipped" if s.skipped else ("✓" if s.success else "✗")
            lines.append(f"  Step {s.order}: {status} {s.description}")
            if s.output:
                lines.append(f"    → {s.output[:400]}")
            if s.error:
                lines.append(f"    ✗ {s.error}")
        if self.summary:
            lines.append(f"\nSummary: {self.summary}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SkillService
# ---------------------------------------------------------------------------

class SkillService:
    """Manages skill distillation, recall, execution, and curation.

    Args:
        llm:  Optional LangChain LLM used for distillation and promotion.
              When None, distillation is skipped.
    """

    def __init__(self, llm: Any = None) -> None:
        self._llm = llm

    # ------------------------------------------------------------------
    # Distillation
    # ------------------------------------------------------------------

    async def distill(
        self,
        execution_id: str,
        state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Distil a completed investigation into a skill and persist it.

        Returns the saved skill dict on success, None if skipped or failed.
        """
        if self._llm is None:
            logger.debug("SkillService.distill: no LLM — skipping")
            return None

        tool_calls = state.get("tool_calls") or []
        if len(tool_calls) < _MIN_TOOL_CALLS:
            logger.debug(
                "SkillService.distill: only %d tool calls (min %d) — skipping",
                len(tool_calls), _MIN_TOOL_CALLS,
            )
            return None

        try:
            skill_dict = await self._call_llm_distill(state, tool_calls)
            if not skill_dict or not skill_dict.get("name"):
                return None

            return await self._upsert_skill(skill_dict, execution_id)

        except Exception as exc:
            logger.warning("SkillService.distill: failed — %s", exc)
            return None

    async def _call_llm_distill(
        self,
        state: Dict[str, Any],
        tool_calls: List[Any],
    ) -> Optional[Dict[str, Any]]:
        """Invoke the LLM and parse the returned JSON."""
        from langchain_core.messages import HumanMessage, SystemMessage

        user_query  = state.get("user_query") or state.get("trigger") or ""
        root_cause  = state.get("root_cause") or ""
        final_answer = state.get("final_answer") or ""
        resolution  = root_cause or final_answer

        tool_summary_lines = []
        for tc in tool_calls[:15]:
            if isinstance(tc, dict):
                name = tc.get("tool") or tc.get("name") or "unknown"
                ok = "✓" if not tc.get("failed") else "✗"
                tool_summary_lines.append(f"  {ok} {name}")

        user_msg = _DISTILL_USER.format(
            user_query   = user_query[:400],
            root_cause   = root_cause[:400],
            tool_count   = len(tool_calls),
            tool_summary = "\n".join(tool_summary_lines) or "  (none recorded)",
            resolution   = resolution[:400],
        )

        response = await self._llm.ainvoke([
            SystemMessage(content=_DISTILL_SYSTEM),
            HumanMessage(content=user_msg),
        ])
        raw = response.content if hasattr(response, "content") else str(response)

        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("SkillService.distill: JSON parse failed — %s\nRaw: %s", exc, raw[:300])
            return None

    async def _upsert_skill(
        self,
        skill_dict: Dict[str, Any],
        execution_id: str,
    ) -> Dict[str, Any]:
        """Insert or update the skill in the DB."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.models.db_models import SkillModel
        from app.core.database import AsyncSessionLocal

        name  = _slugify(skill_dict.get("name", ""))[:60]
        title = skill_dict.get("title", name)[:255]
        steps = skill_dict.get("steps") or []
        # Ensure steps are sorted by order
        steps = sorted(steps, key=lambda s: s.get("order", 99))

        async with AsyncSessionLocal() as session:
            stmt = pg_insert(SkillModel).values(
                name             = name,
                title            = title,
                description      = (skill_dict.get("description") or "")[:2000],
                trigger_patterns = skill_dict.get("trigger_patterns") or [],
                steps            = steps,
                workflow_name    = skill_dict.get("workflow_name"),
                source           = "distilled",
                status           = "active",
                success_count    = 0,
                recall_count     = 0,
                created_at       = datetime.now(timezone.utc),
                updated_at       = datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "title":            title,
                    "description":      (skill_dict.get("description") or "")[:2000],
                    "trigger_patterns": skill_dict.get("trigger_patterns") or [],
                    "steps":            steps,
                    "source":           "distilled",
                    "status":           "active",
                    "updated_at":       datetime.now(timezone.utc),
                },
            ).returning(SkillModel.id)

            result = await session.execute(stmt)
            skill_id = result.scalar_one()
            await session.commit()

        logger.info(
            "SkillService: skill '%s' upserted (id=%s, execution_id=%s)",
            name, skill_id, execution_id,
        )
        return {
            "id":    skill_id,
            "name":  name,
            "title": title,
            "steps": len(steps),
        }

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------

    async def recall(
        self,
        query: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Return up to *limit* active skills whose trigger_patterns match *query*.

        Uses both keyword matching (fast, no embedding needed) and optionally
        vector similarity if pgvector is available.

        Returns list of skill dicts suitable for injecting into the agent prompt.
        """
        try:
            return await self._keyword_recall(query, limit)
        except Exception as exc:
            logger.debug("SkillService.recall: failed — %s", exc)
            return []

    async def _keyword_recall(
        self,
        query: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        from sqlalchemy import select
        from app.models.db_models import SkillModel
        from app.core.database import AsyncSessionLocal

        query_lower = query.lower()

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SkillModel)
                .where(SkillModel.status == "active")
                .order_by(SkillModel.recall_count.desc())
                .limit(50)  # narrow in Python after loading
            )
            all_skills = result.scalars().all()

        matched: List[Tuple[SkillModel, int]] = []
        for skill in all_skills:
            patterns = skill.trigger_patterns or []
            score = 0
            for p in patterns:
                try:
                    if re.search(p, query, re.IGNORECASE):
                        score += 2
                except re.error:
                    if p.lower() in query_lower:
                        score += 1
            # Also check title/description keyword overlap
            if skill.title.lower() in query_lower:
                score += 1
            if score > 0:
                matched.append((skill, score))

        matched.sort(key=lambda x: x[1], reverse=True)

        return [
            {
                "id":    s.id,
                "name":  s.name,
                "title": s.title,
                "description": s.description,
                "trigger_patterns": s.trigger_patterns,
                "steps": s.steps,
                "score": score,
            }
            for s, score in matched[:limit]
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        skill_name: str,
        context: Dict[str, Any],
        mcp_manager: Any = None,
    ) -> SkillExecutionResult:
        """Execute a skill by name.

        Args:
            skill_name:  Skill ``name`` (slug).
            context:     Key-value dict used to fill ``{placeholder}`` slots
                         in step ``args_template`` values.
            mcp_manager: Live MCPClientManager instance.  When None, tool steps
                         are described but not executed.

        Returns:
            SkillExecutionResult
        """
        skill = await self._load_skill(skill_name)
        if skill is None:
            return SkillExecutionResult(
                skill_name=skill_name,
                success=False,
                error=f"Skill '{skill_name}' not found or archived.",
            )

        # If the skill delegates to a workflow, return guidance but don't execute
        if skill.get("workflow_name"):
            await self._increment_recall(skill["id"])
            return SkillExecutionResult(
                skill_name=skill_name,
                success=True,
                summary=(
                    f"Skill '{skill_name}' is implemented as workflow "
                    f"'{skill['workflow_name']}'. Trigger that workflow to execute it."
                ),
            )

        steps: List[Dict[str, Any]] = skill.get("steps") or []
        step_results: List[SkillStepResult] = []
        abort = False

        for step in sorted(steps, key=lambda s: s.get("order", 99)):
            if abort:
                step_results.append(SkillStepResult(
                    order=step.get("order", 0),
                    description=step.get("description", ""),
                    tool=step.get("tool"),
                    skipped=True,
                ))
                continue

            condition = step.get("condition")
            if condition and not context.get(condition):
                step_results.append(SkillStepResult(
                    order=step.get("order", 0),
                    description=step.get("description", ""),
                    tool=step.get("tool"),
                    skipped=True,
                    output=f"[skipped: condition '{condition}' not met]",
                ))
                continue

            sr = await self._execute_step(step, context, mcp_manager)
            step_results.append(sr)

            if not sr.success and step.get("on_failure", "continue") == "abort":
                abort = True

        overall_success = not abort and all(
            s.success or s.skipped for s in step_results
        )

        if overall_success:
            await self._increment_success(skill["id"])
        await self._increment_recall(skill["id"])

        return SkillExecutionResult(
            skill_name=skill_name,
            success=overall_success,
            steps=step_results,
            summary=self._build_summary(skill_name, step_results),
        )

    async def _execute_step(
        self,
        step: Dict[str, Any],
        context: Dict[str, Any],
        mcp_manager: Any,
    ) -> SkillStepResult:
        order       = step.get("order", 0)
        description = step.get("description", "")
        tool_name   = step.get("tool")
        args_tmpl   = step.get("args_template") or {}

        if not tool_name:
            return SkillStepResult(
                order=order,
                description=description,
                tool=None,
                output="[reasoning step — no tool required]",
            )

        # Resolve args placeholders
        args = _resolve_args(args_tmpl, context)

        if mcp_manager is None:
            return SkillStepResult(
                order=order,
                description=description,
                tool=tool_name,
                output=f"[dry-run] would call {tool_name}({args})",
            )

        # Find which server exposes this tool
        server_id = _find_server(tool_name, mcp_manager)
        if server_id is None:
            return SkillStepResult(
                order=order,
                description=description,
                tool=tool_name,
                success=False,
                error=f"No connected MCP server exposes tool '{tool_name}'",
            )

        try:
            result = await mcp_manager.execute_tool(server_id, tool_name, args)
            content = result.get("content") or result
            output = content if isinstance(content, str) else json.dumps(content, default=str)
            return SkillStepResult(
                order=order,
                description=description,
                tool=tool_name,
                success=not result.get("isError", False),
                output=output[:1000],
            )
        except Exception as exc:
            return SkillStepResult(
                order=order,
                description=description,
                tool=tool_name,
                success=False,
                error=str(exc)[:300],
            )

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    async def promote(
        self,
        knowledge_entry_id: int,
        steps: List[Dict[str, Any]],
        name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Promote a knowledge_entry to a formal executable skill.

        Args:
            knowledge_entry_id: ID of the KnowledgeEntryModel to promote.
            steps:              Explicit step list for the skill.
            name:               Optional slug override (auto-derived from title otherwise).

        Returns:
            Saved skill dict or None on failure.
        """
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.models.db_models import KnowledgeEntryModel, SkillModel
        from app.core.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(KnowledgeEntryModel).where(
                        KnowledgeEntryModel.id == knowledge_entry_id
                    )
                )
                entry = result.scalar_one_or_none()
                if entry is None:
                    logger.warning(
                        "SkillService.promote: knowledge_entry id=%d not found",
                        knowledge_entry_id,
                    )
                    return None

                skill_name = _slugify(name or entry.title)[:60]
                trigger_patterns = (
                    [str(s) for s in (entry.symptoms or [])][:5]
                    or [entry.title.lower()]
                )

                stmt = pg_insert(SkillModel).values(
                    name             = skill_name,
                    title            = entry.title[:255],
                    description      = entry.description[:2000] if entry.description else "",
                    trigger_patterns = trigger_patterns,
                    steps            = steps,
                    source           = "promoted",
                    status           = "active",
                    success_count    = 0,
                    recall_count     = 0,
                    promoted_from_id = knowledge_entry_id,
                    created_at       = datetime.now(timezone.utc),
                    updated_at       = datetime.now(timezone.utc),
                ).on_conflict_do_update(
                    index_elements=["name"],
                    set_={
                        "steps":      steps,
                        "status":     "active",
                        "source":     "promoted",
                        "updated_at": datetime.now(timezone.utc),
                    },
                ).returning(SkillModel.id)

                result2 = await session.execute(stmt)
                skill_id = result2.scalar_one()
                await session.commit()

            logger.info(
                "SkillService: promoted knowledge_entry %d → skill '%s' (id=%d)",
                knowledge_entry_id, skill_name, skill_id,
            )
            return {"id": skill_id, "name": skill_name, "title": entry.title}

        except Exception as exc:
            logger.warning("SkillService.promote: failed — %s", exc)
            return None

    # ------------------------------------------------------------------
    # Getters
    # ------------------------------------------------------------------

    async def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the skill dict for *name*, or None."""
        skill = await self._load_skill(name)
        return skill

    async def list_skills(
        self,
        status: str = "active",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List skills filtered by status."""
        from sqlalchemy import select
        from app.models.db_models import SkillModel
        from app.core.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as session:
                q = (
                    select(SkillModel)
                    .where(SkillModel.status == status)
                    .order_by(SkillModel.recall_count.desc())
                    .limit(limit)
                )
                result = await session.execute(q)
                skills = result.scalars().all()
            return [_skill_to_dict(s) for s in skills]
        except Exception as exc:
            logger.debug("SkillService.list_skills: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------

    async def _increment_success(self, skill_id: int) -> None:
        from sqlalchemy import update
        from app.models.db_models import SkillModel
        from app.core.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(SkillModel)
                    .where(SkillModel.id == skill_id)
                    .values(
                        success_count=SkillModel.success_count + 1,
                        last_used_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.debug("SkillService._increment_success: %s", exc)

    async def _increment_recall(self, skill_id: int) -> None:
        from sqlalchemy import update
        from app.models.db_models import SkillModel
        from app.core.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(SkillModel)
                    .where(SkillModel.id == skill_id)
                    .values(
                        recall_count=SkillModel.recall_count + 1,
                        last_used_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.debug("SkillService._increment_recall: %s", exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_skill(self, name: str) -> Optional[Dict[str, Any]]:
        from sqlalchemy import select
        from app.models.db_models import SkillModel
        from app.core.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(SkillModel).where(
                        SkillModel.name == name,
                        SkillModel.status != "archived",
                    )
                )
                skill = result.scalar_one_or_none()
            return _skill_to_dict(skill) if skill else None
        except Exception as exc:
            logger.debug("SkillService._load_skill(%s): %s", name, exc)
            return None

    @staticmethod
    def _build_summary(skill_name: str, steps: List[SkillStepResult]) -> str:
        done    = [s for s in steps if not s.skipped and s.success]
        failed  = [s for s in steps if not s.skipped and not s.success]
        skipped = [s for s in steps if s.skipped]
        parts = [f"Skill '{skill_name}': {len(done)} step(s) completed"]
        if failed:
            parts.append(f"{len(failed)} failed")
        if skipped:
            parts.append(f"{len(skipped)} skipped")
        return ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert *text* to a lowercase snake_case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s\-]+", "_", text)
    return text[:60] if text else "unnamed_skill"


def _resolve_args(
    args_template: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Substitute {placeholders} in args_template values from context."""
    resolved: Dict[str, Any] = {}
    for key, val in args_template.items():
        if isinstance(val, str) and "{" in val:
            try:
                resolved[key] = val.format_map(
                    _SafeDict(context)
                )
            except Exception:
                resolved[key] = val
        else:
            resolved[key] = val
    return resolved


class _SafeDict(dict):
    """dict subclass that returns the key in braces for missing keys."""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _find_server(tool_name: str, mcp_manager: Any) -> Optional[str]:
    """Return the server_id that exposes *tool_name*, or None."""
    for server_id, tool_names in (getattr(mcp_manager, "tools", {}) or {}).items():
        if tool_name in tool_names:
            return server_id
    return None


def _skill_to_dict(skill: Any) -> Dict[str, Any]:
    """Convert a SkillModel ORM row to a plain dict."""
    return {
        "id":               skill.id,
        "name":             skill.name,
        "title":            skill.title,
        "description":      skill.description,
        "trigger_patterns": skill.trigger_patterns or [],
        "steps":            skill.steps or [],
        "workflow_name":    skill.workflow_name,
        "source":           skill.source,
        "status":           skill.status,
        "success_count":    skill.success_count or 0,
        "recall_count":     skill.recall_count or 0,
        "last_used_at":     skill.last_used_at.isoformat() if skill.last_used_at else None,
        "promoted_from_id": skill.promoted_from_id,
        "created_at":       skill.created_at.isoformat() if skill.created_at else None,
        "updated_at":       skill.updated_at.isoformat() if skill.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

skill_service = SkillService()
