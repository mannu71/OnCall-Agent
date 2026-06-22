"""Skill system — reusable, executable resolution procedures (FILE-backed).

Skills are the executable layer on top of the knowledge base.  Where a
knowledge entry records *what was learned*, a skill records *how to act*.

Storage
-------
Skills are stored as **one JSON file per skill** under ``settings.skills_store_dir``
(default ``data/skills_store/<name>.json``), fronted by a process-wide in-memory
cache. There is NO database table involved on the hot paths (recall / execute /
list / create / delete) — this removes per-turn DB round-trips. The only method
that still touches the DB is :meth:`promote`, which reads the source
``knowledge_entries`` row. A one-time, best-effort importer migrates any rows from
the legacy ``skills`` table into files on first use (no-op when the table is empty
or absent). ``name`` (a slug) is the identity / filename.

Lifecycle
---------
1. **Distillation** — after a high-quality investigation, the LLM converts the
   trajectory into a structured skill and saves it as a file.
2. **Recall** — at investigation start, keyword matching against trigger_patterns
   surfaces relevant skills as context injected into the agent prompt.
3. **Execution** — the agent calls ``execute_skill(name, context)`` to run a
   skill's steps in order against the live MCP tool set.
4. **Promotion** — an engineer (or the curator) can promote a knowledge_entry
   into a first-class skill.
5. **Curation** — the curator archives shaky drafts and consolidates duplicates.

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

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
  "confidence":       0.0,
  "pitfalls":         ["<optional failure modes / gotchas>"],
  "verification":     ["<optional ways to confirm success>"],
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
- confidence is 0.0–1.0: how reliably this generalises to future incidents. A
  one-off or shaky procedure scores low (<0.6); a clean, repeatable fix scores high.
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
# File store (module-level, shared by every SkillService instance)
# ---------------------------------------------------------------------------
# A process-wide cache keyed by skill name. Reads serve from the cache after an
# incremental, mtime-based rescan (cheap; keeps multiple workers roughly in sync
# since os.replace bumps the file mtime). Writes are atomic (temp + os.replace).

_CACHE: Dict[str, Dict[str, Any]] = {}
_MTIMES: Dict[str, float] = {}
_WRITE_LOCK = asyncio.Lock()
_MIGRATED = False


def _store_dir() -> Path:
    d = Path(getattr(settings, "skills_store_dir", "data/skills_store"))
    return d


def _skill_path(name: str) -> Path:
    return _store_dir() / f"{name}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scan_store() -> None:
    """Incrementally refresh the cache from disk (mtime-based; drops deletions)."""
    d = _store_dir()
    if not d.exists():
        # Nothing on disk yet — keep whatever's cached from writes this process.
        return
    seen: set[str] = set()
    for f in d.glob("*.json"):
        name = f.stem
        seen.add(name)
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        if _MTIMES.get(name) == mt and name in _CACHE:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            _CACHE[name] = _normalize_record(data, name=name)
            _MTIMES[name] = mt
        except Exception as exc:  # noqa: BLE001 — a bad file must not break the store
            logger.debug("skills store: failed to read %s: %s", f, exc)
    for name in list(_CACHE):
        if name not in seen:
            _CACHE.pop(name, None)
            _MTIMES.pop(name, None)


def _write_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Atomically persist a skill record and update the cache. Returns the record."""
    rec = _normalize_record(rec)
    name = rec["name"]
    d = _store_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _skill_path(name)
    tmp = d / f".{name}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    _CACHE[name] = rec
    try:
        _MTIMES[name] = path.stat().st_mtime
    except OSError:
        pass
    return rec


def _remove_record(name: str) -> bool:
    existed = name in _CACHE or _skill_path(name).exists()
    try:
        _skill_path(name).unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("skills store: failed to delete %s: %s", name, exc)
    _CACHE.pop(name, None)
    _MTIMES.pop(name, None)
    return existed


async def _migrate_db_skills_once() -> None:
    """One-time best-effort import of legacy DB skills into files (no-op if empty)."""
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    try:
        from sqlalchemy import select
        from app.models.db_models import SkillModel
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(SkillModel))).scalars().all()
        imported = 0
        for row in rows:
            name = row.name
            if not name or _skill_path(name).exists():
                continue
            _write_record(_orm_to_record(row))
            imported += 1
        if imported:
            logger.info("skills store: migrated %d legacy DB skill(s) to files", imported)
    except Exception as exc:  # noqa: BLE001 — DB missing/down must never break skills
        logger.debug("skills store: DB migration skipped (%s)", exc)


async def _ensure_store() -> None:
    """Run the one-time DB import, then refresh the cache from disk."""
    await _migrate_db_skills_once()
    _scan_store()


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
    """Manages skill distillation, recall, execution, and curation (file-backed).

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
        """Insert or update the skill file (distilled source)."""
        name  = _slugify(skill_dict.get("name", ""))[:60]
        title = (skill_dict.get("title") or name)[:255]
        steps = skill_dict.get("steps") or []
        steps = sorted(steps, key=lambda s: s.get("order", 99))

        # Confidence-gated activation: a low-confidence distillation lands as
        # 'draft' (excluded from recall) until proven.
        confidence = _coerce_confidence(skill_dict.get("confidence"))
        status = "active" if confidence >= settings.skill_confidence_min else "draft"
        description = _compose_description(skill_dict)

        async with _WRITE_LOCK:
            await _ensure_store()
            existing = _CACHE.get(name)
            now = _now_iso()
            rec = {
                "name":             name,
                "title":            title,
                "description":      description,
                "trigger_patterns": skill_dict.get("trigger_patterns") or [],
                "steps":            steps,
                "workflow_name":    skill_dict.get("workflow_name"),
                "source":           "distilled",
                "status":           status,
                "confidence":       confidence,
                # preserve counters/lineage/created_at across re-distillation
                "audit_verdict":    (existing or {}).get("audit_verdict"),
                "success_count":    int((existing or {}).get("success_count") or 0),
                "recall_count":     int((existing or {}).get("recall_count") or 0),
                "last_used_at":     (existing or {}).get("last_used_at"),
                "promoted_from_id": (existing or {}).get("promoted_from_id"),
                "created_at":       (existing or {}).get("created_at") or now,
                "updated_at":       now,
            }
            _write_record(rec)

        logger.info(
            "SkillService: skill '%s' upserted (file, execution_id=%s)", name, execution_id,
        )
        return {"id": name, "name": name, "title": title, "steps": len(steps)}

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------

    async def recall(
        self,
        query: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Return up to *limit* active skills whose trigger_patterns match *query*."""
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
        await _ensure_store()
        query_lower = query.lower()

        matched: List[Tuple[Dict[str, Any], int]] = []
        for skill in _CACHE.values():
            if skill.get("status") != "active":
                continue
            patterns = skill.get("trigger_patterns") or []
            score = 0
            for p in patterns:
                try:
                    if re.search(p, query, re.IGNORECASE):
                        score += 2
                except re.error:
                    if str(p).lower() in query_lower:
                        score += 1
            if (skill.get("title") or "").lower() in query_lower:
                score += 1
            if score > 0:
                matched.append((skill, score))

        matched.sort(key=lambda x: (x[1], x[0].get("recall_count") or 0), reverse=True)

        return [
            {
                "id":    s["name"],
                "name":  s["name"],
                "title": s.get("title"),
                "description": s.get("description"),
                "trigger_patterns": s.get("trigger_patterns"),
                "steps": s.get("steps"),
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
            await self._bump_counters(skill_name, success=False)
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

        await self._bump_counters(skill_name, success=overall_success)

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

        args = _resolve_args(args_tmpl, context)

        if mcp_manager is None:
            return SkillStepResult(
                order=order,
                description=description,
                tool=tool_name,
                output=f"[dry-run] would call {tool_name}({args})",
            )

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
    # Promotion (reads knowledge_entries from the DB; writes a skill file)
    # ------------------------------------------------------------------

    async def promote(
        self,
        knowledge_entry_id: int,
        steps: List[Dict[str, Any]],
        name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Promote a knowledge_entry to a formal executable skill (file-backed)."""
        from sqlalchemy import select
        from app.models.db_models import KnowledgeEntryModel
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
                    "SkillService.promote: knowledge_entry id=%d not found", knowledge_entry_id,
                )
                return None

            skill_name = _slugify(name or entry.title)[:60]
            trigger_patterns = (
                [str(s) for s in (entry.symptoms or [])][:5]
                or [entry.title.lower()]
            )

            async with _WRITE_LOCK:
                await _ensure_store()
                existing = _CACHE.get(skill_name)
                now = _now_iso()
                rec = {
                    "name":             skill_name,
                    "title":            entry.title[:255],
                    "description":      entry.description[:2000] if entry.description else "",
                    "trigger_patterns": trigger_patterns,
                    "steps":            steps,
                    "workflow_name":    (existing or {}).get("workflow_name"),
                    "source":           "promoted",
                    "status":           "active",
                    "confidence":       (existing or {}).get("confidence"),
                    "audit_verdict":    (existing or {}).get("audit_verdict"),
                    "success_count":    int((existing or {}).get("success_count") or 0),
                    "recall_count":     int((existing or {}).get("recall_count") or 0),
                    "last_used_at":     (existing or {}).get("last_used_at"),
                    "promoted_from_id": knowledge_entry_id,
                    "created_at":       (existing or {}).get("created_at") or now,
                    "updated_at":       now,
                }
                _write_record(rec)

            logger.info(
                "SkillService: promoted knowledge_entry %d → skill '%s' (file)",
                knowledge_entry_id, skill_name,
            )
            return {"id": skill_name, "name": skill_name, "title": entry.title}

        except Exception as exc:
            logger.warning("SkillService.promote: failed — %s", exc)
            return None

    # ------------------------------------------------------------------
    # Audit / curation
    # ------------------------------------------------------------------

    async def audit(self) -> Dict[str, int]:
        """Self-curate the skill files (draft→published lifecycle).

        Deterministic, LLM-free passes (idempotent verdicts):
          1. Promote proven drafts (success_count >= threshold) → active.
          2. Archive shaky, never-recalled, low-confidence drafts.
          3. Dedupe near-duplicate titles among active skills (keep most-used).
        """
        promoted = archived = deduped = 0
        promote_threshold = settings.skill_promote_success_count
        try:
            async with _WRITE_LOCK:
                await _ensure_store()
                now = _now_iso()

                # 1. Promote proven drafts.
                for rec in list(_CACHE.values()):
                    if rec.get("status") == "draft" and (rec.get("success_count") or 0) >= promote_threshold:
                        rec = {**rec, "status": "active", "audit_verdict": "promoted", "updated_at": now}
                        _write_record(rec)
                        promoted += 1

                # 2. Archive shaky, never-recalled drafts.
                for rec in list(_CACHE.values()):
                    if (
                        rec.get("status") == "draft"
                        and (rec.get("recall_count") or 0) == 0
                        and (rec.get("confidence") or 0.0) < settings.skill_confidence_min
                    ):
                        rec = {**rec, "status": "archived", "audit_verdict": "archived", "updated_at": now}
                        _write_record(rec)
                        archived += 1

                # 3. Dedupe near-duplicate titles among active skills.
                by_title: Dict[str, List[Dict[str, Any]]] = {}
                for rec in _CACHE.values():
                    if rec.get("status") == "active":
                        by_title.setdefault(_normalize_title(rec.get("title") or ""), []).append(rec)
                for dupes in by_title.values():
                    if len(dupes) < 2:
                        continue
                    dupes.sort(
                        key=lambda r: (r.get("recall_count") or 0) + (r.get("success_count") or 0),
                        reverse=True,
                    )
                    for victim in dupes[1:]:
                        victim = {**victim, "status": "archived", "audit_verdict": "deduped", "updated_at": now}
                        _write_record(victim)
                        deduped += 1
        except Exception as exc:  # noqa: BLE001 — audit is best-effort
            logger.warning("SkillService.audit: failed — %s", exc)

        if promoted or archived or deduped:
            logger.info(
                "SkillService.audit: promoted=%d archived=%d deduped=%d",
                promoted, archived, deduped,
            )
        return {"promoted": promoted, "archived": archived, "deduped": deduped}

    # ------------------------------------------------------------------
    # Getters
    # ------------------------------------------------------------------

    async def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the skill dict for *name*, or None."""
        return await self._load_skill(name)

    async def list_skills(
        self,
        status: str = "active",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List skills filtered by status ('all' lists every status)."""
        try:
            await _ensure_store()
            items = list(_CACHE.values())
            if status and status != "all":
                items = [s for s in items if s.get("status") == status]
            items.sort(key=lambda s: s.get("recall_count") or 0, reverse=True)
            return [_skill_to_dict(s) for s in items[:limit]]
        except Exception as exc:
            logger.debug("SkillService.list_skills: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Manual CRUD (UI-driven)
    # ------------------------------------------------------------------

    async def create_manual(
        self,
        *,
        name: str,
        title: str = "",
        description: str = "",
        trigger_patterns: Optional[List[str]] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        workflow_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create (or overwrite) an operator-authored skill (source='manual')."""
        slug = _slugify(name)[:60]
        async with _WRITE_LOCK:
            await _ensure_store()
            existing = _CACHE.get(slug)
            now = _now_iso()
            rec = {
                "name":             slug,
                "title":            title or name,
                "description":      description or "",
                "trigger_patterns": trigger_patterns or [],
                "steps":            steps or [],
                "workflow_name":    workflow_name,
                "source":           "manual",
                "status":           "active",
                "confidence":       1.0,
                "audit_verdict":    (existing or {}).get("audit_verdict"),
                "success_count":    int((existing or {}).get("success_count") or 0),
                "recall_count":     int((existing or {}).get("recall_count") or 0),
                "last_used_at":     (existing or {}).get("last_used_at"),
                "promoted_from_id": (existing or {}).get("promoted_from_id"),
                "created_at":       (existing or {}).get("created_at") or now,
                "updated_at":       now,
            }
            out = _write_record(rec)
        logger.info("SkillService.create_manual: saved '%s' (file)", slug)
        return _skill_to_dict(out)

    async def delete(self, name: str) -> bool:
        """Hard-delete a skill file by name. Returns True if it existed."""
        async with _WRITE_LOCK:
            await _ensure_store()
            removed = _remove_record(name)
        if removed:
            logger.info("SkillService.delete: removed '%s' (file)", name)
        return removed

    async def delete_all(self) -> int:
        """Hard-delete every skill file. Returns the count removed."""
        async with _WRITE_LOCK:
            await _ensure_store()
            names = list(_CACHE.keys())
            for name in names:
                _remove_record(name)
        logger.info("SkillService.delete_all: removed %d skill(s) (files)", len(names))
        return len(names)

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------

    async def _bump_counters(self, name: str, *, success: bool) -> None:
        """Increment recall (+ success) counters with a single atomic file write."""
        try:
            async with _WRITE_LOCK:
                await _ensure_store()
                rec = _CACHE.get(name)
                if rec is None:
                    return
                now = _now_iso()
                rec = {
                    **rec,
                    "recall_count": int(rec.get("recall_count") or 0) + 1,
                    "success_count": int(rec.get("success_count") or 0) + (1 if success else 0),
                    "last_used_at": now,
                    "updated_at": now,
                }
                _write_record(rec)
        except Exception as exc:
            logger.debug("SkillService._bump_counters(%s): %s", name, exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_skill(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            await _ensure_store()
            rec = _CACHE.get(name)
            if rec is None or rec.get("status") == "archived":
                return None
            return _skill_to_dict(rec)
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

def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation/extra space — for near-duplicate detection."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (title or "").lower())).strip()


def _coerce_confidence(value: Any) -> float:
    """Clamp a model-provided confidence to [0,1]; default 0.5 when missing/bad."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _compose_description(skill_dict: Dict[str, Any]) -> str:
    """Build the stored description, folding in pitfalls/verification sections."""
    parts = [(skill_dict.get("description") or "").strip()]
    pitfalls = [str(p).strip() for p in (skill_dict.get("pitfalls") or []) if str(p).strip()]
    verification = [str(v).strip() for v in (skill_dict.get("verification") or []) if str(v).strip()]
    if pitfalls:
        parts.append("Pitfalls: " + "; ".join(pitfalls))
    if verification:
        parts.append("Verify: " + "; ".join(verification))
    return "\n".join(p for p in parts if p)[:2000]


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
                resolved[key] = val.format_map(_SafeDict(context))
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


def _normalize_record(d: Dict[str, Any], name: Optional[str] = None) -> Dict[str, Any]:
    """Fill a stored/loaded skill dict with all expected keys; id == name."""
    nm = d.get("name") or name or "unnamed_skill"
    return {
        "id":               nm,
        "name":             nm,
        "title":            d.get("title") or nm,
        "description":      d.get("description") or "",
        "trigger_patterns": d.get("trigger_patterns") or [],
        "steps":            d.get("steps") or [],
        "workflow_name":    d.get("workflow_name"),
        "source":           d.get("source") or "manual",
        "status":           d.get("status") or "active",
        "confidence":       d.get("confidence"),
        "audit_verdict":    d.get("audit_verdict"),
        "success_count":    int(d.get("success_count") or 0),
        "recall_count":     int(d.get("recall_count") or 0),
        "last_used_at":     d.get("last_used_at"),
        "promoted_from_id": d.get("promoted_from_id"),
        "created_at":       d.get("created_at"),
        "updated_at":       d.get("updated_at"),
    }


def _orm_to_record(skill: Any) -> Dict[str, Any]:
    """Convert a legacy SkillModel ORM row to a file record (for migration)."""
    def _iso(v: Any) -> Optional[str]:
        return v.isoformat() if hasattr(v, "isoformat") and v else None
    return _normalize_record({
        "name":             skill.name,
        "title":            skill.title,
        "description":      skill.description,
        "trigger_patterns": skill.trigger_patterns or [],
        "steps":            skill.steps or [],
        "workflow_name":    skill.workflow_name,
        "source":           skill.source,
        "status":           skill.status,
        "confidence":       float(skill.confidence) if skill.confidence is not None else None,
        "audit_verdict":    getattr(skill, "audit_verdict", None),
        "success_count":    skill.success_count or 0,
        "recall_count":     skill.recall_count or 0,
        "last_used_at":     _iso(skill.last_used_at),
        "promoted_from_id": skill.promoted_from_id,
        "created_at":       _iso(skill.created_at),
        "updated_at":       _iso(skill.updated_at),
    })


def _skill_to_dict(skill: Any) -> Dict[str, Any]:
    """Normalize a stored dict (or legacy ORM row) to the canonical skill dict."""
    if isinstance(skill, dict):
        return _normalize_record(skill)
    return _orm_to_record(skill)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

skill_service = SkillService()
